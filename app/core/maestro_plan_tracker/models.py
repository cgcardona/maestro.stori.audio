"""Dataclasses for plan step and tool-call outcome."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.contracts.json_types import ToolCallDict
from app.contracts.llm_types import AssistantMessage, ToolResultMessage
from app.core.sse_utils import SSEEventInput


@dataclass
class _PlanStep:
    """Internal state for one plan step."""
    step_id: str
    label: str
    detail: str | None = None
    status: str = "pending"
    result: str | None = None
    track_name: str | None = None
    tool_name: str | None = None  # canonical tool name for frontend icon/color rendering
    tool_indices: list[int] = field(default_factory=list)
    parallel_group: str | None = None  # steps sharing a group run concurrently
    phase: str = "composition"  # setup | composition | arrangement | soundDesign | expression | mixing


@dataclass
class _ToolCallOutcome:
    """Outcome of executing one tool call in editing/agent mode.

    The caller decides what to do with SSE events and message objects —
    either yield them directly (editing path) or put them into a queue
    (agent-team path).
    """
    enriched_params: dict[str, Any]
    tool_result: dict[str, Any]
    sse_events: list[SSEEventInput]
    msg_call: AssistantMessage          # assistant message containing the tool call
    msg_result: ToolResultMessage       # tool response message
    skipped: bool = False               # True when rejected by circuit-breaker or validation
    extra_tool_calls: list[ToolCallDict] = field(default_factory=list)  # synthetic calls (icon)
