"""Build IntentResult from classified intent + centralized config."""

from __future__ import annotations

from typing import Any

from app.core.intent_config import Intent, get_intent_config
from app.core.intent.models import IntentResult, Slots
from app.core.tools import ALL_TOOLS


def _build_result(
    intent: Intent,
    confidence: float,
    slots: Slots,
    reasons: tuple[str, ...],
) -> IntentResult:
    """Build IntentResult from intent using centralized config."""
    config = get_intent_config(intent)

    return IntentResult(
        intent=intent,
        sse_state=config.sse_state,
        confidence=confidence,
        slots=slots,
        tools=ALL_TOOLS,
        allowed_tool_names=set(config.allowed_tools),
        tool_choice=config.tool_choice,
        force_stop_after=config.force_stop_after,
        requires_planner=config.requires_planner,
        reasons=reasons,
    )


def _clarify(raw: str, reason: str) -> IntentResult:
    """Return a clarification-needed result."""
    return _build_result(
        Intent.NEEDS_CLARIFICATION,
        confidence=0.6,
        slots=Slots(value_str=raw),
        reasons=(f"clarify:{reason}",),
    )
