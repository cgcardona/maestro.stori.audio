"""JSON schema validation for tool call parameters."""

from __future__ import annotations

from app.contracts.llm_types import ToolParametersDict, ToolSchemaDict
from app.core.tool_validation.models import ValidationError
from app.core.tool_validation.constants import TOOL_REQUIRED_FIELDS


def _validate_type(field: str, value: object, expected_type: str) -> ValidationError | None:
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
    params: dict[str, object],
    schema: ToolSchemaDict,
) -> list[ValidationError]:
    """Validate params against tool schema (required fields + types)."""
    errors: list[ValidationError] = []

    function_def = schema["function"]
    func_schema = function_def.get("parameters", ToolParametersDict(type="object"))

    required_val = func_schema.get("required")
    required = required_val if isinstance(required_val, list) else []
    properties_val = func_schema.get("properties")
    properties = properties_val if isinstance(properties_val, dict) else {}

    for field_val in required:
        if field_val not in params:
            errors.append(ValidationError(
                field=field_val,
                message=f"Required field '{field_val}' is missing",
                code="MISSING_REQUIRED",
            ))

    for field, value in params.items():
        if field not in properties:
            continue
        prop = properties.get(field)
        if not isinstance(prop, dict):
            continue
        expected_type_val = prop.get("type")
        if not isinstance(expected_type_val, str):
            continue
        type_error = _validate_type(field, value, expected_type_val)
        if type_error:
            errors.append(type_error)

    return errors
