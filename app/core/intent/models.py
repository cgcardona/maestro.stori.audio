"""Dataclass models for intent routing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.contracts.llm_types import OpenAIToolChoice, ToolSchemaDict
from app.core.intent_config import Intent, SSEState, IdiomMatch


@dataclass(frozen=True)
class Slots:
    """Extracted slots from user prompt."""
    action: str | None = None
    target_type: str | None = None
    target_name: str | None = None
    amount: float | None = None
    amount_unit: str | None = None
    direction: str | None = None
    value_str: str | None = None
    idiom_match: IdiomMatch | None = None
    extras: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class IntentResult:
    """Result of intent classification."""
    intent: Intent
    sse_state: SSEState
    confidence: float
    slots: Slots
    tools: list[ToolSchemaDict]
    allowed_tool_names: set[str]
    tool_choice: OpenAIToolChoice | None
    force_stop_after: bool
    requires_planner: bool = False
    reasons: tuple[str, ...] = ()

    @property
    def needs_llm_fallback(self) -> bool:
        """True when the result is UNKNOWN with low confidence."""
        return self.intent == Intent.UNKNOWN and self.confidence < 0.5


@dataclass(frozen=True)
class Rule:
    """A pattern-based intent rule."""
    name: str
    intent: Intent
    pattern: re.Pattern[str]
    confidence: float
    slot_extractor: str | None = None
