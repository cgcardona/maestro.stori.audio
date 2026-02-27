"""Dataclass models for tool validation results."""

from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import TypedDict

from maestro.contracts.json_types import JSONValue


@dataclass
class ValidationError:
    """A single validation error."""

    field: str
    message: str
    code: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


@dataclass
class ValidationResult:
    """Result of tool call validation.

    ``original_params`` is the raw input before entity resolution.
    ``resolved_params`` has entity names replaced with IDs and is safe
    for further processing — both are precisely typed as ``dict[str, JSONValue]``
    since tool params are always JSON-decoded before validation.
    """

    valid: bool
    tool_name: str
    original_params: dict[str, JSONValue]
    resolved_params: dict[str, JSONValue]
    errors: list[ValidationError]
    warnings: list[str]

    @property
    def error_message(self) -> str:
        if not self.errors:
            return ""
        return "; ".join(str(e) for e in self.errors)


class EntityResolutionResult(TypedDict):
    """Internal return type of ``_resolve_and_validate_entities``."""

    params: dict[str, JSONValue]
    errors: list[ValidationError]
    warnings: list[str]
