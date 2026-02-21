"""COMPOSING handler — generate music via planner → executor → variation."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid as _uuid_mod
from typing import Any, AsyncIterator, Optional

from app.core.entity_context import format_project_context
from app.core.llm_client import LLMClient
from app.core.planner import build_execution_plan_stream, ExecutionPlan
from app.core.prompt_parser import ParsedPrompt
from app.core.sse_utils import sse_event
from app.core.state_store import StateStore
from app.core.tracing import trace_span
from app.core.maestro_helpers import (
    UsageTracker,
    _context_usage_fields,
    _enrich_params_with_track_context,
    _human_label_for_tool,
)
from app.core.maestro_plan_tracker import _PlanTracker
from app.core.maestro_composing.storage import _store_variation
from app.core.maestro_composing.fallback import _retry_composing_as_editing

logger = logging.getLogger(__name__)


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
