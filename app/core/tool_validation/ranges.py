"""Value range validation for numeric tool parameters."""

from __future__ import annotations

from app.contracts.json_types import JSONValue
from app.core.tool_validation.models import ValidationError
from app.core.tool_validation.constants import VALUE_RANGES


def _validate_value_ranges(params: dict[str, JSONValue]) -> list[ValidationError]:
    """Validate numeric values are within expected ranges."""
    errors: list[ValidationError] = []

    for field, (min_val, max_val) in VALUE_RANGES.items():
        if field in params:
            value = params[field]
            if isinstance(value, (int, float)):
                if value < min_val or value > max_val:
                    errors.append(ValidationError(
                        field=field,
                        message=f"Value {value} is out of range [{min_val}, {max_val}]",
                        code="VALUE_OUT_OF_RANGE",
                    ))

    if "notes" in params and isinstance(params["notes"], list):
        for i, note in enumerate(params["notes"]):
            if isinstance(note, dict):
                for field in ["pitch", "velocity"]:
                    if field in note:
                        value = note[field]
                        if field in VALUE_RANGES:
                            min_val, max_val = VALUE_RANGES[field]
                            if isinstance(value, (int, float)) and (
                                value < min_val or value > max_val
                            ):
                                errors.append(ValidationError(
                                    field=f"notes[{i}].{field}",
                                    message=f"Value {value} is out of range [{min_val}, {max_val}]",
                                    code="VALUE_OUT_OF_RANGE",
                                ))

    return errors
