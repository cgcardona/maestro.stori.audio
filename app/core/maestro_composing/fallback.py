"""Composing fallback helpers — EDITING route when planner fails."""

from __future__ import annotations

import logging
from typing import AsyncIterator

from app.contracts.project_types import ProjectContext
from app.core.intent import Intent, IntentResult, SSEState
from app.core.intent_config import _PRIMITIVES_REGION, _PRIMITIVES_TRACK
from app.core.llm_client import LLMClient
from app.core.state_store import StateStore
from app.core.tracing import TraceContext
from app.core.tools import ALL_TOOLS
from app.core.maestro_helpers import UsageTracker
from app.protocol.emitter import emit
from app.protocol.events import StatusEvent

logger = logging.getLogger(__name__)


def _create_editing_fallback_route(route: IntentResult) -> IntentResult:
    """Build an IntentResult for EDITING when the COMPOSING planner fails.

    The planner is supposed to return JSON; sometimes the LLM returns tool-call
    syntax instead. This creates a one-off EDITING route with primitives so we
    can still produce tool calls. See docs/reference/architecture.md.
    """
    return IntentResult(
        intent=Intent.NOTES_ADD,
        sse_state=SSEState.EDITING,
        confidence=0.7,
        slots=route.slots,
        tools=ALL_TOOLS,
        allowed_tool_names=set(_PRIMITIVES_REGION) | set(_PRIMITIVES_TRACK),
        tool_choice="auto",
        force_stop_after=False,
        requires_planner=False,
        reasons=("Fallback from planner failure",),
    )


async def _retry_composing_as_editing(
    prompt: str,
    project_context: ProjectContext,
    route: IntentResult,
    llm: LLMClient,
    store: StateStore,
    trace: TraceContext,
    usage_tracker: UsageTracker | None,
    quality_preset: str | None = None,
) -> AsyncIterator[str]:
    """When planner output looks like function calls instead of JSON, retry as EDITING."""
    logger.warning(
        f"[{trace.trace_id[:8]}] Planner output looks like function calls, "
        "falling back to EDITING mode with tools"
    )
    yield emit(StatusEvent(message="Retrying with different approach..."))
    from app.core.maestro_editing import _handle_editing
    editing_route = _create_editing_fallback_route(route)
    async for event in _handle_editing(
        prompt, project_context, editing_route, llm, store,
        trace, usage_tracker, [], "variation",
        quality_preset=quality_preset,
    ):
        yield event
