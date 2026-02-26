"""Main validation entrypoints: validate_tool_call and batch helpers."""

from __future__ import annotations

import logging
from app.contracts.json_types import JSONValue
from app.core.entity_registry import EntityRegistry
from app.core.tools import tool_schema_by_name
from app.core.tool_validation.models import ValidationError, ValidationResult
from app.core.tool_validation.entities import _resolve_and_validate_entities
from app.core.tool_validation.schema import _validate_schema
from app.core.tool_validation.ranges import _validate_value_ranges
from app.core.tool_validation.specific import _validate_tool_specific
from app.core.tool_validation.scope import _check_target_scope

logger = logging.getLogger(__name__)


def validate_tool_call(
    tool_name: str,
    params: dict[str, JSONValue],
    allowed_tools: set[str] | frozenset[str],
    registry: EntityRegistry | None = None,
    target_scope: tuple[str, str | None] | None = None,
) -> ValidationResult:
    """
    Validate a tool call.

    Allowlist is the single source of truth: only tools in allowed_tools may be called.
    Maestro passes intent-derived allowlists; MCP passes all MCP tool names.

    Steps:
    1. Allowlist check
    2. Entity resolution (when registry provided)
    3. Schema validation (required params, types)
    4. Value range validation
    5. Tool-specific validation
    6. Target scope advisory check (structured prompt, never blocks)
    """
    errors: list[ValidationError] = []
    warnings: list[str] = []
    resolved_params = params.copy()

    # 1. Allowlist check
    if tool_name not in allowed_tools:
        errors.append(ValidationError(
            field="tool_name",
            message=f"Tool '{tool_name}' is not allowed for this request",
            code="TOOL_NOT_ALLOWED",
        ))
        return ValidationResult(
            valid=False,
            tool_name=tool_name,
            original_params=params,
            resolved_params=resolved_params,
            errors=errors,
            warnings=warnings,
        )

    # 2. Entity resolution
    if registry:
        resolution = _resolve_and_validate_entities(tool_name, resolved_params, registry)
        resolved_params = resolution["params"]
        errors.extend(resolution["errors"])
        warnings.extend(resolution["warnings"])

    # 3. Schema validation
    schema = tool_schema_by_name(tool_name)
    if schema:
        errors.extend(_validate_schema(tool_name, resolved_params, schema))

    # 4. Value range validation
    errors.extend(_validate_value_ranges(resolved_params))

    # 5. Tool-specific validation
    errors.extend(_validate_tool_specific(tool_name, resolved_params))

    # 6. Target scope advisory check
    if target_scope is not None:
        warnings.extend(_check_target_scope(tool_name, resolved_params, target_scope, registry))

    return ValidationResult(
        valid=len(errors) == 0,
        tool_name=tool_name,
        original_params=params,
        resolved_params=resolved_params,
        errors=errors,
        warnings=warnings,
    )


def validate_tool_call_simple(
    tool_name: str,
    params: dict[str, JSONValue],
    allowed_tools: set[str],
) -> tuple[bool, str]:
    """Simple validation returning (valid, error_message). Backward-compatible."""
    result = validate_tool_call(tool_name, params, allowed_tools, registry=None)
    return result.valid, result.error_message


def validate_tool_calls_batch(
    tool_calls: list[tuple[str, dict[str, JSONValue]]],
    allowed_tools: set[str],
    registry: EntityRegistry | None = None,
) -> list[ValidationResult]:
    """Validate a batch of (tool_name, params) tuples."""
    return [
        validate_tool_call(tool_name, params, allowed_tools, registry)
        for tool_name, params in tool_calls
    ]


def all_valid(results: list[ValidationResult]) -> bool:
    """True if every result in the batch is valid."""
    return all(r.valid for r in results)


def collect_errors(results: list[ValidationResult]) -> list[str]:
    """Collect error messages from invalid results."""
    return [
        f"{r.tool_name}: {r.error_message}"
        for r in results
        if not r.valid
    ]
