"""JSON schema validation for tool call parameters."""

from __future__ import annotations

from typing import Any, Optional

from app.core.tool_validation.models import ValidationError
from app.core.tool_validation.constants import TOOL_REQUIRED_FIELDS


def _validate_type(field: str, value: Any, expected_type: str) -> Optional[ValidationError]:
    """Validate a value against an expected JSON Schema type."""
    type_map: dict[str, type | tuple[type, ...]] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    expected = type_map.get(expected_type)
    if expected is not None and not isinstance(value, expected):
        return ValidationError(
            field=field,
            message=f"Expected {expected_type}, got {type(value).__name__}",
            code="TYPE_MISMATCH",
        )
    return None


def _validate_schema(
    tool_name: str,
    params: dict[str, Any],
    schema: dict[str, Any],
) -> list[ValidationError]:
    """Validate params against tool schema (required fields + types)."""
    errors: list[ValidationError] = []

    func_schema = schema.get("function", {}).get("parameters", {})
    required = func_schema.get("required", [])
    properties = func_schema.get("properties", {})

    for field in required:
        if field not in params:
            errors.append(ValidationError(
                field=field,
                message=f"Required field '{field}' is missing",
                code="MISSING_REQUIRED",
            ))

    for field, value in params.items():
        if field not in properties:
            continue
        expected_type = properties[field].get("type")
        if expected_type:
            type_error = _validate_type(field, value, expected_type)
            if type_error:
                errors.append(type_error)

    return errors
