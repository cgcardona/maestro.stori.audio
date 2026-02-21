"""COMPOSING and REASONING handlers for Maestro.

Handles the COMPOSING SSE state (planner → executor → variation) and the
REASONING state (question answering without tools). Also owns variation
storage and the composing-to-editing fallback path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid as _uuid_mod
from typing import Any, AsyncIterator, Optional

from app.core.entity_context import format_project_context
from app.core.intent import Intent, IntentResult, SSEState
from app.core.intent_config import _PRIMITIVES_REGION, _PRIMITIVES_TRACK
from app.core.llm_client import LLMClient, LLMResponse
from app.core.planner import build_execution_plan_stream, ExecutionPlan
from app.core.prompt_parser import ParsedPrompt
from app.core.prompts import system_prompt_base, wrap_user_request
from app.core.sse_utils import sanitize_reasoning, sse_event
from app.core.state_store import StateStore
from app.core.tools import ALL_TOOLS
from app.core.tracing import log_llm_call, trace_span
from app.core.maestro_helpers import (
    UsageTracker,
    _context_usage_fields,
    _enrich_params_with_track_context,
    _human_label_for_tool,
)
from app.core.maestro_plan_tracker import _PlanTracker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Variation storage
# ---------------------------------------------------------------------------

def _store_variation(
    variation: Any,
    project_context: dict[str, Any],
    store: "StateStore",
) -> None:
    """Persist a Variation to the VariationStore so commit/discard can find it.

    Called from the maestro/stream path after ``execute_plan_variation`` returns.
    Mirrors the storage logic in the ``/variation/propose`` background task.
    """
    from app.variation.storage.variation_store import (
        get_variation_store,
        PhraseRecord,
    )
    from app.variation.core.state_machine import VariationStatus

    project_id = project_context.get("id", "")
    base_state_id = store.get_state_id()

    vstore = get_variation_store()
    record = vstore.create(
        project_id=project_id,
        base_state_id=base_state_id,
        intent=variation.intent,
        variation_id=variation.variation_id,
        conversation_id=store.conversation_id,
    )

    record.transition_to(VariationStatus.STREAMING)
    record.ai_explanation = variation.ai_explanation
    record.affected_tracks = variation.affected_tracks
    record.affected_regions = variation.affected_regions

    for phrase in variation.phrases:
        seq = record.next_sequence()

        region_entity = store.registry.get_region(phrase.region_id)
        region_meta = region_entity.metadata if region_entity else {}
        region_start_beat = region_meta.get("startBeat")
        region_duration_beats = region_meta.get("durationBeats")
        region_name = region_entity.name if region_entity else None

        record.add_phrase(PhraseRecord(
            phrase_id=phrase.phrase_id,
            variation_id=variation.variation_id,
            sequence=seq,
            track_id=phrase.track_id,
            region_id=phrase.region_id,
            beat_start=phrase.start_beat,
            beat_end=phrase.end_beat,
            label=phrase.label,
            diff_json={
                "phraseId": phrase.phrase_id,
                "trackId": phrase.track_id,
                "regionId": phrase.region_id,
                "startBeat": phrase.start_beat,
                "endBeat": phrase.end_beat,
                "label": phrase.label,
                "tags": phrase.tags,
                "explanation": phrase.explanation,
                "noteChanges": [nc.model_dump(by_alias=True) for nc in phrase.note_changes],
                "controllerChanges": phrase.controller_changes,
            },
            ai_explanation=phrase.explanation,
            tags=phrase.tags,
            region_start_beat=region_start_beat,
            region_duration_beats=region_duration_beats,
            region_name=region_name,
        ))

    record.transition_to(VariationStatus.READY)
    logger.info(
        f"Variation stored: {variation.variation_id[:8]} "
        f"({len(variation.phrases)} phrases, status=READY)"
    )


# ---------------------------------------------------------------------------
# Composing fallback helpers
# ---------------------------------------------------------------------------

def _create_editing_fallback_route(route: Any) -> IntentResult:
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
    project_context: dict[str, Any],
    route: Any,
    llm: LLMClient,
    store: StateStore,
    trace: Any,
    usage_tracker: Optional[UsageTracker],
    quality_preset: Optional[str] = None,
) -> AsyncIterator[str]:
    """When planner output looks like function calls instead of JSON, retry as EDITING."""
    logger.warning(
        f"[{trace.trace_id[:8]}] Planner output looks like function calls, "
        "falling back to EDITING mode with tools"
    )
    yield await sse_event({"type": "status", "message": "Retrying with different approach..."})
    from app.core.maestro_editing import _handle_editing
    editing_route = _create_editing_fallback_route(route)
    async for event in _handle_editing(
        prompt, project_context, editing_route, llm, store,
        trace, usage_tracker, [], "variation",
        quality_preset=quality_preset,
    ):
        yield event


# ---------------------------------------------------------------------------
# REASONING handler
# ---------------------------------------------------------------------------

async def _handle_reasoning(
    prompt: str,
    project_context: dict[str, Any],
    route: Any,
    llm: LLMClient,
    trace: Any,
    usage_tracker: Optional[UsageTracker],
    conversation_history: list[dict[str, Any]],
) -> AsyncIterator[str]:
    """Handle REASONING state - answer questions without tools."""
    yield await sse_event({"type": "status", "message": "Reasoning..."})

    if route.intent == Intent.ASK_STORI_DOCS:
        try:
            from app.services.rag import get_rag_service
            rag = get_rag_service(llm_client=llm)

            if rag.collection_exists():
                async for chunk in rag.answer(prompt, model=llm.model):
                    yield await sse_event({"type": "content", "content": chunk})

                yield await sse_event({
                    "type": "complete",
                    "success": True,
                    "toolCalls": [],
                    "traceId": trace.trace_id,
                    **_context_usage_fields(usage_tracker, llm.model),
                })
                return
        except Exception as e:
            logger.warning(f"[{trace.trace_id[:8]}] RAG failed: {e}")

    with trace_span(trace, "llm_thinking"):
        messages = [{"role": "system", "content": system_prompt_base()}]

        if project_context:
            messages.append({"role": "system", "content": format_project_context(project_context)})

        if conversation_history:
            messages.extend(conversation_history)

        messages.append({"role": "user", "content": wrap_user_request(prompt)})

        start_time = time.time()
        response = None

        logger.info(f"🎯 REASONING handler: supports_reasoning={llm.supports_reasoning()}, model={llm.model}")
        if llm.supports_reasoning():
            logger.info("🌊 Using streaming path for reasoning model")
            response_text = ""
            async for raw in llm.chat_completion_stream(
                messages=messages,
                tools=[],
                tool_choice="none",
            ):
                event = raw
                if event.get("type") == "reasoning_delta":
                    reasoning_text = event.get("text", "")
                    if reasoning_text:
                        sanitized = sanitize_reasoning(reasoning_text)
                        if sanitized:
                            yield await sse_event({
                                "type": "reasoning",
                                "content": sanitized,
                            })
                elif event.get("type") == "content_delta":
                    content_text = event.get("text", "")
                    if content_text:
                        response_text += content_text
                        yield await sse_event({"type": "content", "content": content_text})
                elif event.get("type") == "done":
                    response = LLMResponse(
                        content=response_text or event.get("content"),
                        usage=event.get("usage", {})
                    )
            duration_ms = (time.time() - start_time) * 1000
        else:
            response = await llm.chat_completion(
                messages=messages,
                tools=[],
                tool_choice="none",
            )
            duration_ms = (time.time() - start_time) * 1000

            if response.content:
                yield await sse_event({"type": "content", "content": response.content})

        if response and response.usage:
            log_llm_call(
                trace.trace_id,
                llm.model,
                response.usage.get("prompt_tokens", 0),
                response.usage.get("completion_tokens", 0),
                duration_ms,
                False,
            )
            if usage_tracker:
                usage_tracker.add(
                    response.usage.get("prompt_tokens", 0),
                    response.usage.get("completion_tokens", 0),
                )

    yield await sse_event({
        "type": "complete",
        "success": True,
        "toolCalls": [],
        "traceId": trace.trace_id,
        **_context_usage_fields(usage_tracker, llm.model),
    })


# ---------------------------------------------------------------------------
# COMPOSING handler
# ---------------------------------------------------------------------------

async def _handle_composing(
    prompt: str,
    project_context: dict[str, Any],
    route: Any,
    llm: LLMClient,
    store: StateStore,
    trace: Any,
    usage_tracker: Optional[UsageTracker],
    conversation_id: Optional[str],
    quality_preset: Optional[str] = None,
) -> AsyncIterator[str]:
    """Handle COMPOSING state - generate music via planner.

    All COMPOSING intents produce a Variation for human review.
    The planner generates a tool-call plan, the executor simulates it
    in variation mode, and the result is streamed as meta/phrase/done events.

    Phase 1 (Unified SSE UX): reasoning events are streamed during the
    planner's LLM call so the user sees the agent thinking — same UX as
    EDITING mode.
    """
    yield await sse_event({"type": "status", "message": "Thinking..."})

    _slots = getattr(route, "slots", None)
    _extras = getattr(_slots, "extras", None) if _slots is not None else None
    parsed: Optional[ParsedPrompt] = (
        _extras.get("parsed_prompt") if isinstance(_extras, dict) else None
    )

    # ── Streaming planner: yields reasoning SSE events, then the plan ──
    plan: Optional[ExecutionPlan] = None
    with trace_span(trace, "planner"):
        async for item in build_execution_plan_stream(
            user_prompt=prompt,
            project_state=project_context,
            route=route,
            llm=llm,
            parsed=parsed,
            usage_tracker=usage_tracker,
            emit_sse=lambda data: sse_event(data),
        ):
            if isinstance(item, ExecutionPlan):
                plan = item
            else:
                yield item

    if plan and plan.tool_calls:
        composing_plan_tracker = _PlanTracker()
        composing_plan_tracker.build(
            plan.tool_calls, prompt, project_context,
            is_composition=True, store=store,
        )
        if len(composing_plan_tracker.steps) >= 1:
            yield await sse_event(composing_plan_tracker.to_plan_event())

        yield await sse_event({
            "type": "planSummary",
            "totalSteps": len(composing_plan_tracker.steps),
            "generations": plan.generation_count,
            "edits": plan.edit_count,
        })

        # PROPOSAL PHASE
        for tc in plan.tool_calls:
            yield await sse_event({
                "type": "toolCall",
                "id": "",
                "name": tc.name,
                "params": tc.params,
                "proposal": True,
            })

        # EXECUTION PHASE
        try:
            with trace_span(trace, "variation_generation", {"steps": len(plan.tool_calls)}):
                from app.core.executor import execute_plan_variation

                logger.info(
                    f"[{trace.trace_id[:8]}] Starting variation execution: "
                    f"{len(plan.tool_calls)} tool calls"
                )

                _event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
                _active_step_by_track: dict[str, str] = {}

                async def _on_pre_tool(
                    tool_name: str, params: dict[str, Any],
                ) -> None:
                    """Execution phase: planStepUpdate:active + toolStart."""
                    step = composing_plan_tracker.find_step_for_tool(
                        tool_name, params, store,
                    )
                    if step and step.status != "active":
                        track_key = (step.track_name or "").lower()
                        prev_step_id = _active_step_by_track.get(track_key)
                        if prev_step_id and prev_step_id != step.step_id:
                            await _event_queue.put(
                                composing_plan_tracker.complete_step_by_id(prev_step_id)
                            )
                        await _event_queue.put(
                            composing_plan_tracker.activate_step(step.step_id)
                        )
                        if track_key:
                            _active_step_by_track[track_key] = step.step_id

                    label = _human_label_for_tool(tool_name, params)
                    await _event_queue.put({
                        "type": "toolStart",
                        "name": tool_name,
                        "label": label,
                    })

                async def _on_post_tool(
                    tool_name: str, resolved_params: dict[str, Any],
                ) -> None:
                    """Execution phase: toolCall with real UUID after success."""
                    call_id = str(_uuid_mod.uuid4())
                    emit_params = _enrich_params_with_track_context(resolved_params, store)
                    await _event_queue.put({
                        "type": "toolCall",
                        "id": call_id,
                        "name": tool_name,
                        "params": emit_params,
                        "proposal": False,
                    })

                async def _on_progress(
                    current: int, total: int,
                    tool_name: str = "", tool_args: dict | None = None,
                ) -> None:
                    label = _human_label_for_tool(tool_name, tool_args or {}) if tool_name else f"Step {current}"
                    await _event_queue.put({
                        "type": "progress",
                        "currentStep": current,
                        "totalSteps": total,
                        "message": label,
                        "toolName": tool_name,
                    })

                _VARIATION_TIMEOUT = 300
                task = asyncio.create_task(
                    execute_plan_variation(
                        tool_calls=plan.tool_calls,
                        project_state=project_context,
                        intent=prompt,
                        conversation_id=conversation_id,
                        explanation=plan.llm_response_text,
                        progress_callback=_on_progress,
                        pre_tool_callback=_on_pre_tool,
                        post_tool_callback=_on_post_tool,
                        quality_preset=quality_preset,
                    )
                )

                variation = None
                start_wall = time.time()
                try:
                    while True:
                        if time.time() - start_wall > _VARIATION_TIMEOUT:
                            task.cancel()
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass
                            raise asyncio.TimeoutError()
                        try:
                            event_data = await asyncio.wait_for(
                                _event_queue.get(), timeout=0.05,
                            )
                            yield await sse_event(event_data)
                        except asyncio.TimeoutError:
                            if task.done():
                                break
                        await asyncio.sleep(0)

                    while not _event_queue.empty():
                        yield await sse_event(await _event_queue.get())

                    variation = await task

                    for final_evt in composing_plan_tracker.complete_all_active_steps():
                        yield await sse_event(final_evt)

                    for skip_evt in composing_plan_tracker.finalize_pending_as_skipped():
                        yield await sse_event(skip_evt)

                except asyncio.TimeoutError:
                    logger.error(
                        f"[{trace.trace_id[:8]}] Variation generation timed out "
                        f"after {_VARIATION_TIMEOUT}s"
                    )
                    yield await sse_event({
                        "type": "error",
                        "message": f"Generation timed out after {_VARIATION_TIMEOUT}s",
                        "traceId": trace.trace_id,
                    })
                    yield await sse_event({
                        "type": "done",
                        "variationId": "",
                        "phraseCount": 0,
                        "status": "failed",
                    })
                    yield await sse_event({
                        "type": "complete",
                        "success": False,
                        "error": "timeout",
                        "traceId": trace.trace_id,
                        **_context_usage_fields(usage_tracker, llm.model),
                    })
                    return

                logger.info(
                    f"[{trace.trace_id[:8]}] Variation computed: "
                    f"{variation.total_changes} changes, {len(variation.phrases)} phrases"
                )

                if len(variation.phrases) == 0:
                    logger.error(
                        f"[{trace.trace_id[:8]}] COMPOSING produced 0 phrases "
                        f"despite {len(plan.tool_calls)} tool calls — "
                        f"this indicates a generation or entity resolution failure. "
                        f"Proposed notes captured: {sum(len(n) for n in getattr(variation, '_proposed_notes', {}).values()) if hasattr(variation, '_proposed_notes') else 'N/A'}"
                    )

                _store_variation(variation, project_context, store)

                note_counts = variation.note_counts
                yield await sse_event({
                    "type": "meta",
                    "variationId": variation.variation_id,
                    "baseStateId": store.get_state_id(),
                    "intent": variation.intent,
                    "aiExplanation": variation.ai_explanation,
                    "affectedTracks": variation.affected_tracks,
                    "affectedRegions": variation.affected_regions,
                    "noteCounts": note_counts,
                })

                for i, phrase in enumerate(variation.phrases):
                    logger.debug(
                        f"[{trace.trace_id[:8]}] Emitting phrase {i + 1}/{len(variation.phrases)}: "
                        f"{len(phrase.note_changes)} note changes"
                    )
                    yield await sse_event({
                        "type": "phrase",
                        "phraseId": phrase.phrase_id,
                        "trackId": phrase.track_id,
                        "regionId": phrase.region_id,
                        "startBeat": phrase.start_beat,
                        "endBeat": phrase.end_beat,
                        "label": phrase.label,
                        "tags": phrase.tags,
                        "explanation": phrase.explanation,
                        "noteChanges": [nc.model_dump(by_alias=True) for nc in phrase.note_changes],
                        "controllerChanges": phrase.controller_changes,
                    })

                yield await sse_event({
                    "type": "done",
                    "variationId": variation.variation_id,
                    "phraseCount": len(variation.phrases),
                })

                logger.info(
                    f"[{trace.trace_id[:8]}] Variation streamed: "
                    f"{variation.total_changes} changes in {len(variation.phrases)} phrases"
                )

                yield await sse_event({
                    "type": "complete",
                    "success": True,
                    "variationId": variation.variation_id,
                    "totalChanges": variation.total_changes,
                    "phraseCount": len(variation.phrases),
                    "traceId": trace.trace_id,
                    **_context_usage_fields(usage_tracker, llm.model),
                })

        except BaseException as e:
            logger.exception(
                f"[{trace.trace_id[:8]}] Variation generation failed: {e}"
            )
            yield await sse_event({
                "type": "error",
                "message": f"Generation failed: {e}",
                "traceId": trace.trace_id,
            })
            yield await sse_event({
                "type": "done",
                "variationId": "",
                "phraseCount": 0,
                "status": "failed",
            })
            yield await sse_event({
                "type": "complete",
                "success": False,
                "error": str(e),
                "traceId": trace.trace_id,
                **_context_usage_fields(usage_tracker, llm.model),
            })
        return
    else:
        response_text = plan.llm_response_text if plan else None

        looks_like_function_calls = (
            response_text and
            ("stori_" in response_text or
             "add_midi_track(" in response_text or
             "add_notes(" in response_text or
             "add_region(" in response_text)
        )

        if looks_like_function_calls:
            async for event in _retry_composing_as_editing(
                prompt, project_context, route, llm, store,
                trace, usage_tracker,
                quality_preset=quality_preset,
            ):
                yield event
            return

        if response_text:
            yield await sse_event({
                "type": "content",
                "content": "I understand you want to generate music. To help me create exactly what you're looking for, "
                           "could you tell me:\n"
                           "- What style or genre? (e.g., 'lofi', 'jazz', 'electronic')\n"
                           "- What tempo? (e.g., 90 BPM)\n"
                           "- How many bars? (e.g., 8 bars)\n\n"
                           "Example: 'Create an exotic melody at 100 BPM for 8 bars in C minor'",
            })
        else:
            yield await sse_event({
                "type": "content",
                "content": "I need more information to generate music. Please specify:\n"
                           "- Style/genre (e.g., 'boom bap', 'lofi', 'trap')\n"
                           "- Tempo (e.g., 90 BPM)\n"
                           "- Number of bars (e.g., 8 bars)\n\n"
                           "Example: 'Make a boom bap beat at 90 BPM with drums and bass for 8 bars'",
            })

        yield await sse_event({
            "type": "complete",
            "success": True,
            "toolCalls": [],
            "traceId": trace.trace_id,
            **_context_usage_fields(usage_tracker, llm.model),
        })
