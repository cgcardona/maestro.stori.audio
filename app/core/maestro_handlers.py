"""Orchestration entry point for Maestro (Cursor-of-DAWs).

This module is the public API surface: it exports ``orchestrate`` and
``UsageTracker``. All handler logic lives in focused sub-modules:

- ``maestro_helpers``      — shared utilities, UsageTracker, StreamFinalResponse
- ``maestro_plan_tracker`` — _PlanTracker, _PlanStep, tool-name sets
- ``maestro_editing``      — _handle_editing, _apply_single_tool_call, routing helpers
- ``maestro_composing``    — _handle_composing, _handle_reasoning, _store_variation
- ``maestro_agent_teams``  — _run_instrument_agent, _handle_composition_agent_team
"""

from __future__ import annotations

import logging
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
)

from app.core.intent import Intent, IntentResult, SSEState, get_intent_result_with_llm
from app.core.llm_client import LLMClient
from app.core.prompt_parser import ParsedPrompt
from app.core.sse_utils import sse_event
from app.core.state_store import StateStore, get_or_create_store
from app.core.tracing import (
    clear_trace_context,
    create_trace_context,
    log_intent,
    trace_span,
)
from app.services.budget import get_model_or_default

# Public re-exports — external callers (routes, planner, pipeline) import these
# from maestro_handlers without needing to know the sub-module layout.
from app.core.maestro_helpers import UsageTracker, StreamFinalResponse  # noqa: F401
from app.core.maestro_editing import (
    _project_needs_structure,
    _is_additive_composition,
    _create_editing_composition_route,
    _handle_editing,
)
from app.core.maestro_composing import (
    _handle_reasoning,
    _handle_composing,
    _handle_composing_with_agent_teams,
)
from app.core.maestro_agent_teams import _handle_composition_agent_team
from app.core.maestro_helpers import _context_usage_fields

__all__ = ["orchestrate", "UsageTracker", "StreamFinalResponse"]

logger = logging.getLogger(__name__)


async def orchestrate(
    prompt: str,
    project_context: dict[str, Any] | None = None,
    model: str | None = None,
    usage_tracker: UsageTracker | None = None,
    conversation_id: str | None = None,
    user_id: str | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    quality_preset: str | None = None,
) -> AsyncIterator[str]:
    """Main orchestration using Cursor-of-DAWs architecture.

    Flow:
    1. Create trace context for request
    2. Intent Router classifies prompt → route + allowlist
    3. Backend determines execution_mode from intent:
       COMPOSING → variation (human review), EDITING → apply (immediate)
    4. Route to REASONING / EDITING / COMPOSING
    5. Execute with strict tool gating + entity validation
    """
    project_context = project_context or {}
    conversation_history = conversation_history or []
    selected_model = get_model_or_default(model)

    trace = create_trace_context(
        conversation_id=conversation_id,
        user_id=user_id,
    )

    llm = LLMClient(model=selected_model)

    _project_id = project_context.get("id") or ""
    store = get_or_create_store(
        conversation_id=_project_id or conversation_id or "default",
        project_id=_project_id,
    )
    store.sync_from_client(project_context)

    try:
        with trace_span(trace, "orchestrate", {"prompt_length": len(prompt)}):

            # =================================================================
            # Step 1: Intent Classification
            # =================================================================

            with trace_span(trace, "intent_classification"):
                route = await get_intent_result_with_llm(prompt, project_context, llm, conversation_history)

                _orch_slots = getattr(route, "slots", None)
                _orch_extras = getattr(_orch_slots, "extras", None) if _orch_slots is not None else None
                _orch_parsed: ParsedPrompt | None = (
                    _orch_extras.get("parsed_prompt") if isinstance(_orch_extras, dict) else None
                )

                # Backend-owned execution mode policy:
                # COMPOSING → variation (music generation requires human review)
                #   EXCEPT: empty project → override to EDITING (can't diff against nothing)
                #   EXCEPT: additive composition (new section / new tracks) → EDITING
                #   OVERRIDE: explicit `Mode: compose` in STORI PROMPT always stays COMPOSING
                # EDITING   → apply (structural ops execute directly)
                # REASONING → n/a (no tools)
                _explicit_compose = (
                    _orch_parsed is not None and _orch_parsed.mode == "compose"
                )
                if route.sse_state == SSEState.COMPOSING:
                    if _explicit_compose:
                        execution_mode = "variation"
                        logger.info(
                            f"🎵 Explicit Mode: compose in STORI PROMPT — "
                            f"staying COMPOSING (Orpheus generation)"
                        )
                    elif _project_needs_structure(project_context):
                        route = _create_editing_composition_route(route)
                        execution_mode = "apply"
                        logger.info(
                            f"🔄 Empty project: overriding {route.intent.value} → EDITING "
                            f"for structural creation with tool_call events"
                        )
                    elif _is_additive_composition(_orch_parsed, project_context):
                        route = _create_editing_composition_route(route)
                        execution_mode = "apply"
                        logger.info(
                            f"🔄 Additive composition (new section/tracks): "
                            f"overriding {route.intent.value} → EDITING "
                            f"for direct execution with tool_call events"
                        )
                    else:
                        execution_mode = "variation"
                        logger.info(f"Intent {route.intent.value} → COMPOSING, execution_mode='variation'")
                elif route.sse_state == SSEState.REASONING:
                    execution_mode = "reasoning"
                    logger.info(f"Intent {route.intent.value} → REASONING, execution_mode='reasoning'")
                else:
                    execution_mode = "apply"
                    logger.info(f"Intent {route.intent.value} → {route.sse_state.value}, execution_mode='apply'")

                log_intent(
                    trace.trace_id,
                    prompt,
                    route.intent.value,
                    route.confidence,
                    route.sse_state.value,
                    route.reasons,
                )

            # Agent teams (multi-instrument GENERATE_MUSIC, apply mode) surface as
            # "composing" so the frontend renders the composition UI, not the
            # editing UI, even though the route was overridden to SSEState.EDITING
            # for structural tooling reasons.
            _agent_team_path = (
                route.intent == Intent.GENERATE_MUSIC
                and execution_mode == "apply"
                and _orch_parsed is not None
                and bool(getattr(_orch_parsed, "roles", None))
                and len(getattr(_orch_parsed, "roles", [])) > 1
            )
            yield await sse_event({
                "type": "state",
                "state": SSEState.COMPOSING.value if _agent_team_path else route.sse_state.value,
                "intent": route.intent.value,
                "confidence": route.confidence,
                "traceId": trace.trace_id,
                "executionMode": execution_mode,
            })

            logger.info(f"[{trace.trace_id[:8]}] 🎯 {route.intent.value} → {route.sse_state.value}")

            # =================================================================
            # Step 2: Handle REASONING (questions - no tools)
            # =================================================================

            if route.sse_state == SSEState.REASONING:
                async for event in _handle_reasoning(
                    prompt, project_context, route, llm, trace,
                    usage_tracker, conversation_history
                ):
                    yield event
                return

            # =================================================================
            # Step 3: Handle COMPOSING (planner path or Agent Teams + Variation)
            # =================================================================

            if route.sse_state == SSEState.COMPOSING:
                _has_roles = (
                    _orch_parsed is not None
                    and bool(getattr(_orch_parsed, "roles", None))
                )
                if _explicit_compose and _has_roles:
                    assert _orch_parsed is not None  # narrowed by _has_roles
                    logger.info(
                        f"[{trace.trace_id[:8]}] 🎼 Mode: compose with "
                        f"{len(_orch_parsed.roles)} role(s) → "
                        f"Agent Teams + Variation capture"
                    )
                    async for event in _handle_composing_with_agent_teams(
                        prompt, project_context, _orch_parsed, route,
                        llm, store, trace, usage_tracker,
                        is_cancelled=is_cancelled,
                        quality_preset=quality_preset,
                    ):
                        yield event
                else:
                    async for event in _handle_composing(
                        prompt, project_context, route, llm, store, trace,
                        usage_tracker, conversation_id,
                        quality_preset=quality_preset,
                    ):
                        yield event
                return

            # =================================================================
            # Step 4: Handle EDITING (LLM tool calls with allowlist)
            # =================================================================

            # ── Agent Teams intercept ──
            # Multi-instrument STORI PROMPT compositions (2+ roles, apply mode)
            # spawn one independent LLM session per instrument running in
            # parallel. Single-instrument and non-STORI-PROMPT requests fall
            # through to the standard _handle_editing path unchanged.
            if (
                route.intent == Intent.GENERATE_MUSIC
                and execution_mode == "apply"
                and _orch_parsed is not None
                and getattr(_orch_parsed, "roles", None)
                and len(_orch_parsed.roles) > 1
            ):
                async for event in _handle_composition_agent_team(
                    prompt, project_context, _orch_parsed, route, llm, store,
                    trace, usage_tracker,
                    is_cancelled=is_cancelled,
                ):
                    yield event
            else:
                async for event in _handle_editing(
                    prompt, project_context, route, llm, store, trace,
                    usage_tracker, conversation_history, execution_mode,
                    is_cancelled=is_cancelled,
                    quality_preset=quality_preset,
                ):
                    yield event

    except Exception as e:
        logger.exception(f"[{trace.trace_id[:8]}] Orchestration error: {e}")
        yield await sse_event({
            "type": "error",
            "message": str(e),
            "traceId": trace.trace_id,
        })
        yield await sse_event({
            "type": "complete",
            "success": False,
            "error": str(e),
            "traceId": trace.trace_id,
            **_context_usage_fields(usage_tracker, selected_model),
        })

    finally:
        await llm.close()
        clear_trace_context()
