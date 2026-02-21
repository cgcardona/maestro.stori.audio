"""Dataclass models for tool validation results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
    """Result of tool call validation."""

    valid: bool
    tool_name: str
    original_params: dict[str, Any]
    resolved_params: dict[str, Any]
    errors: list[ValidationError]
    warnings: list[str]

    @property
    def error_message(self) -> str:
        if not self.errors:
            return ""
        return "; ".join(str(e) for e in self.errors)
