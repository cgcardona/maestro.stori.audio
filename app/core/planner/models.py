"""ExecutionPlan dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.expansion import ToolCall
from app.core.plan_schemas import PlanValidationResult


@dataclass
class ExecutionPlan:
    """Validated execution plan ready for the executor."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    safety_validated: bool = False
    llm_response_text: str | None = None
    validation_result: PlanValidationResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "notes": self.notes,
            "safety_validated": self.safety_validated,
            "validation_errors": self.validation_result.errors if self.validation_result else [],
        }

    @property
    def is_valid(self) -> bool:
        return self.safety_validated and len(self.tool_calls) > 0

    @property
    def generation_count(self) -> int:
        return sum(1 for tc in self.tool_calls if tc.name.startswith("stori_generate"))

    @property
    def edit_count(self) -> int:
        return sum(
            1 for tc in self.tool_calls
            if tc.name in ("stori_add_midi_track", "stori_add_midi_region")
        )
