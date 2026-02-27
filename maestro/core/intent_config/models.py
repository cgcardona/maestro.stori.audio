"""IntentConfig and IdiomMatch dataclasses."""

from __future__ import annotations

from dataclasses import dataclass

from maestro.core.intent_config.enums import Intent, SSEState


@dataclass(frozen=True)
class IntentConfig:
    """Configuration for an intent."""
    intent: Intent
    sse_state: SSEState
    allowed_tools: frozenset[str]
    force_stop_after: bool = True  # Stop after first tool call
    tool_choice: str = "required"  # "required", "auto", or "none"
    requires_planner: bool = False  # Route through planner instead of direct LLM
    description: str = ""


@dataclass(frozen=True)
class IdiomMatch:
    """A matched producer idiom with direction and optional weight."""
    intent: Intent
    phrase: str
    direction: str  # "increase", "decrease", "add", "remove"
    target: str | None = None  # e.g., "highs", "lows", "width"
    suggested_tools: frozenset[str] = frozenset()
    weight: int = 1  # 1-5 scale from structured prompt Vibe weights
