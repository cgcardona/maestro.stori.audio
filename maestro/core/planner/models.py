"""ExecutionPlan dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

from maestro.contracts.json_types import JSONValue, ToolCallPreviewDict
from maestro.core.expansion import ToolCall
from maestro.core.plan_schemas import PlanValidationResult


class ExecutionPlanSummary(TypedDict):
    """Wire shape of ``ExecutionPlan.to_dict()``.

    Emitted in the ``plan`` SSE event so the frontend can render a step-by-step
    checklist before execution begins.  ``tool_calls`` mirrors the ``ToolCall``
    representation (name + params); ``validation_errors`` is empty on a valid plan.
    """

    tool_calls: list[ToolCallPreviewDict]
    notes: list[str]
    safety_validated: bool
    validation_errors: list[str]


@dataclass
class ExecutionPlan:
    """Validated execution plan ready for the executor.

    Produced by ``build_execution_plan`` and consumed by ``run_executor``.
    A plan is only executed when ``is_valid`` is ``True`` — i.e. it passed
    safety validation AND contains at least one tool call.

    Attributes:
        tool_calls: Ordered list of ``ToolCall``s to execute.  May include both
            generator calls (``stori_generate_midi``) and primitive DAW calls.
        notes: Human-readable annotations from the planner LLM (not executed).
        safety_validated: Set to ``True`` by the validator after checking the
            plan against schema and safety rules.
        llm_response_text: Raw LLM explanation text, kept for debugging and
            plan-preview SSE events.
        validation_result: Full ``PlanValidationResult`` from Pydantic parsing,
            or ``None`` when validation was skipped (e.g. empty plan).
    """

    tool_calls: list[ToolCall] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    safety_validated: bool = False
    llm_response_text: str | None = None
    validation_result: PlanValidationResult | None = None

    def to_dict(self) -> ExecutionPlanSummary:
        """Serialise to a summary dict suitable for SSE plan-preview events."""
        return {
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "notes": self.notes,
            "safety_validated": self.safety_validated,
            "validation_errors": self.validation_result.errors if self.validation_result else [],
        }

    @property
    def is_valid(self) -> bool:
        """``True`` when the plan passed safety validation and has at least one tool call."""
        return self.safety_validated and len(self.tool_calls) > 0

    @property
    def generation_count(self) -> int:
        """Number of ``stori_generate_*`` calls in the plan (Orpheus invocations)."""
        return sum(1 for tc in self.tool_calls if tc.name.startswith("stori_generate"))

    @property
    def edit_count(self) -> int:
        """Number of structural edit calls (track / region creation) in the plan."""
        return sum(
            1 for tc in self.tool_calls
            if tc.name in ("stori_add_midi_track", "stori_add_midi_region")
        )
