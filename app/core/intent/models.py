"""Dataclass models for intent routing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.intent_config import Intent, SSEState, IdiomMatch


@dataclass(frozen=True)
class Slots:
    """Extracted slots from user prompt."""
    action: Optional[str] = None
    target_type: Optional[str] = None
    target_name: Optional[str] = None
    amount: Optional[float] = None
    amount_unit: Optional[str] = None
    direction: Optional[str] = None
    value_str: Optional[str] = None
    idiom_match: Optional[IdiomMatch] = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntentResult:
    """Result of intent classification."""
    intent: Intent
    sse_state: SSEState
    confidence: float
    slots: Slots
    tools: list[dict[str, Any]]
    allowed_tool_names: set[str]
    tool_choice: str | dict | None
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
    pattern: re.Pattern
    confidence: float
    slot_extractor: Optional[str] = None
