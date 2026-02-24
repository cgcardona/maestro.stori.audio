"""
Tool argument validation package for Stori Maestro.

Validates tool calls before execution:
1. Allowlist check (intent-derived or MCP)
2. Entity resolution (trackName → trackId, etc.)
3. JSON schema validation (required params, types)
4. Value range validation
5. Tool-specific rules
6. Target scope advisory warnings

Public API:
    validate_tool_call(tool_name, params, allowed_tools, registry, target_scope)
    validate_tool_call_simple(tool_name, params, allowed_tools) -> (bool, str)
    validate_tool_calls_batch(tool_calls, allowed_tools, registry) -> list[ValidationResult]
"""

from app.core.tool_validation.models import ValidationError, ValidationResult
from app.core.tool_validation.constants import (
    VALID_SF_SYMBOL_ICONS,
    ENTITY_REF_FIELDS,
    VALUE_RANGES,
    NAME_LENGTH_LIMITS,
    TOOL_REQUIRED_FIELDS,
    AUTOMATION_CANONICAL_PARAMETERS,
)
from app.core.tool_validation.entities import (
    _find_closest_match,
    _resolve_and_validate_entities,
    resolve_tool_entities,
)
from app.core.tool_validation.schema import _validate_schema, _validate_type
from app.core.tool_validation.ranges import _validate_value_ranges
from app.core.tool_validation.specific import _validate_tool_specific
from app.core.tool_validation.scope import _check_target_scope
from app.core.tool_validation.validators import (
    validate_tool_call,
    validate_tool_call_simple,
    validate_tool_calls_batch,
    all_valid,
    collect_errors,
)

__all__ = [
    # Models
    "ValidationError",
    "ValidationResult",
    # Constants
    "VALID_SF_SYMBOL_ICONS",
    "ENTITY_REF_FIELDS",
    "VALUE_RANGES",
    "NAME_LENGTH_LIMITS",
    "TOOL_REQUIRED_FIELDS",
    "AUTOMATION_CANONICAL_PARAMETERS",
    # Internal helpers (used by tests)
    "_find_closest_match",
    "_resolve_and_validate_entities",
    "_validate_schema",
    "_validate_type",
    "_validate_value_ranges",
    "_validate_tool_specific",
    "_check_target_scope",
    "resolve_tool_entities",
    # Main entrypoints
    "validate_tool_call",
    "validate_tool_call_simple",
    "validate_tool_calls_batch",
    "all_valid",
    "collect_errors",
]
