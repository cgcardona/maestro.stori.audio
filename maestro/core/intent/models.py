"""Dataclass models for intent routing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypedDict

from maestro.contracts.llm_types import OpenAIToolChoice, ToolSchemaDict
from maestro.core.intent_config import Intent, SSEState, IdiomMatch

if TYPE_CHECKING:
    from maestro.prompts import MaestroPrompt


class SlotsExtrasDict(TypedDict, total=False):
    """Typed extras bag on ``Slots``.

    All fields are optional (``total=False``).  Each field is populated by a
    specific part of the intent routing / parsing pipeline:

    parsed_prompt
        The fully parsed structured MAESTRO PROMPT, present only when the request
        was a structured prompt (not natural language).
    visible
        UI panel visibility flag from ``stori_show_panel`` / hide patterns.
    target
        Idiom match target entity (e.g. instrument name from a producer idiom).
    matched_phrase
        The raw phrase that triggered an idiom match.
    tempo
        Project tempo override extracted during intent parsing (BPM as int).
    style
        Musical style tag extracted during intent parsing.
    """

    parsed_prompt: MaestroPrompt
    visible: bool
    target: str
    matched_phrase: str
    tempo: int
    style: str


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
    extras: SlotsExtrasDict = field(default_factory=SlotsExtrasDict)


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
