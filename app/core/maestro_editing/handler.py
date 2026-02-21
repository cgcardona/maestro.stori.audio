"""EDITING handler — LLM tool calls with allowlist, validation, and continuation."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, cast

from app.config import settings
from app.core.entity_context import build_entity_context_for_llm, format_project_context
from app.core.expansion import ToolCall
from app.core.intent import Intent, SSEState
from app.core.llm_client import LLMClient, enforce_single_tool
from app.core.prompt_parser import ParsedPrompt
from app.core.prompts import (
    editing_composition_prompt,
    editing_prompt,
    resolve_position,
    sequential_context,
    structured_prompt_context,
    system_prompt_base,
    wrap_user_request,
)
from app.core.sse_utils import sse_event, strip_tool_echoes
from app.core.tracing import log_llm_call, trace_span
from app.core.maestro_helpers import (
    UsageTracker,
    StreamFinalResponse,
    _context_usage_fields,
    _entity_manifest,
    _resolve_variable_refs,
    _stream_llm_response,
)
from app.core.maestro_plan_tracker import _PlanTracker, _build_step_result
from app.core.maestro_editing.continuation import (
    _get_incomplete_tracks,
    _get_missing_expressive_steps,
)
from app.core.maestro_editing.tool_execution import _apply_single_tool_call

logger = logging.getLogger(__name__)


async def _handle_editing(
    prompt: str,
    project_context: dict[str, Any],
    route: Any,
    llm: LLMClient,
    store: Any,
    trace: Any,
    usage_tracker: Optional[UsageTracker],
    conversation_history: list[dict[str, Any]],
    execution_mode: str = "apply",
    is_cancelled: Optional[Callable[[], Awaitable[bool]]] = None,
    quality_preset: Optional[str] = None,
) -> AsyncIterator[str]:
    """Handle EDITING state - LLM tool calls with allowlist + validation.

    Args:
        execution_mode: "apply" for immediate mutation, "variation" for proposal mode
        is_cancelled: async callback returning True if the client disconnected
    """
    from app.core.tools import ALL_TOOLS

    status_msg = "Processing..." if execution_mode == "apply" else "Generating variation..."
    yield await sse_event({"type": "status", "message": status_msg})

    if route.intent == Intent.GENERATE_MUSIC:
        sys_prompt = system_prompt_base() + "\n" + editing_composition_prompt()
    else:
        required_single = bool(route.force_stop_after and route.tool_choice == "required")
        sys_prompt = system_prompt_base() + "\n" + editing_prompt(required_single)

    _slots = getattr(route, "slots", None)
    _extras = getattr(_slots, "extras", None) if _slots is not None else None
    parsed: Optional[ParsedPrompt] = _extras.get("parsed_prompt") if isinstance(_extras, dict) else None
    if parsed is not None:
        sys_prompt += structured_prompt_context(parsed)
        if parsed.position is not None:
            start_beat = resolve_position(parsed.position, project_context or {})
            sys_prompt += sequential_context(start_beat, parsed.section, pos=parsed.position)

    allowed_tools = [t for t in ALL_TOOLS if t["function"]["name"] in route.allowed_tool_names]

    messages: list[dict[str, Any]] = [{"role": "system", "content": sys_prompt}]

    if project_context:
        messages.append({"role": "system", "content": format_project_context(project_context)})
    else:
        messages.append({"role": "system", "content": build_entity_context_for_llm(store)})

    if conversation_history:
        messages.extend(conversation_history)

    messages.append({"role": "user", "content": wrap_user_request(prompt)})

    is_composition = route.intent == Intent.GENERATE_MUSIC
    llm_max_tokens: Optional[int] = settings.composition_max_tokens if is_composition else None
    reasoning_fraction: Optional[float] = settings.composition_reasoning_fraction if is_composition else None

    tool_calls_collected: list[dict[str, Any]] = []
    plan_tracker: Optional[_PlanTracker] = None
    iteration = 0
    _add_notes_failures: dict[str, int] = {}
    max_iterations = (
        settings.composition_max_iterations if is_composition
        else settings.orchestration_max_iterations
    )

    if is_composition and parsed is not None and execution_mode == "apply":
        plan_tracker = _PlanTracker()
        plan_tracker.build_from_prompt(parsed, prompt, project_context or {})
        yield await sse_event(plan_tracker.to_plan_event())

    while iteration < max_iterations:
        iteration += 1

        if is_cancelled:
            try:
                if await is_cancelled():
                    logger.info(
                        f"[{trace.trace_id[:8]}] 🛑 Client disconnected, "
                        f"stopping at iteration {iteration}"
                    )
                    break
            except Exception:
                pass

        logger.info(
            f"[{trace.trace_id[:8]}] 🔄 Editing iteration {iteration}/{max_iterations} "
            f"(composition={is_composition})"
        )

        with trace_span(trace, f"llm_iteration_{iteration}"):
            start_time = time.time()

            if llm.supports_reasoning():
                response = None
                async for item in _stream_llm_response(
                    llm, messages, allowed_tools, route.tool_choice,
                    trace, lambda data: sse_event(data),
                    max_tokens=llm_max_tokens,
                    reasoning_fraction=reasoning_fraction,
                    suppress_content=True,
                ):
                    if isinstance(item, StreamFinalResponse):
                        response = item.response
                    else:
                        yield item
            else:
                response = await llm.chat_completion(
                    messages=messages,
                    tools=allowed_tools,
                    tool_choice=route.tool_choice,
                    temperature=settings.orchestration_temperature,
                    max_tokens=llm_max_tokens,
                )

            duration_ms = (time.time() - start_time) * 1000

            if response is None:
                break
            if response.usage:
                log_llm_call(
                    trace.trace_id,
                    llm.model,
                    response.usage.get("prompt_tokens", 0),
                    response.usage.get("completion_tokens", 0),
                    duration_ms,
                    response.has_tool_calls,
                )
                if usage_tracker:
                    usage_tracker.add(
                        response.usage.get("prompt_tokens", 0),
                        response.usage.get("completion_tokens", 0),
                    )

        if response is None:
            break
        if route.force_stop_after:
            response = enforce_single_tool(response)

        if response.content:
            clean_content = strip_tool_echoes(response.content)
            if clean_content:
                yield await sse_event({"type": "content", "content": clean_content})

        if not response.has_tool_calls:
            if not is_composition:
                break

        iter_tool_results: list[dict[str, Any]] = []

        if (
            plan_tracker is None
            and response is not None
            and response.has_tool_calls
            and execution_mode == "apply"
        ):
            _candidate = _PlanTracker()
            _candidate.build(
                response.tool_calls, prompt, project_context,
                is_composition, store,
            )
            if len(_candidate.steps) >= 2:
                plan_tracker = _candidate
                yield await sse_event(plan_tracker.to_plan_event())
        elif (
            plan_tracker is not None
            and response is not None
            and response.has_tool_calls
            and execution_mode == "apply"
        ):
            for tc in response.tool_calls:
                resolved = _resolve_variable_refs(tc.params, iter_tool_results)
                step = plan_tracker.find_step_for_tool(tc.name, resolved, store)
                if step and step.status == "pending":
                    yield await sse_event(plan_tracker.activate_step(step.step_id))

        for tc_idx, tc in enumerate(response.tool_calls):
            resolved_args = _resolve_variable_refs(tc.params, iter_tool_results)

            if plan_tracker and execution_mode == "apply":
                step = plan_tracker.step_for_tool_index(tc_idx)
                if step is None:
                    step = plan_tracker.find_step_for_tool(
                        tc.name, resolved_args, store,
                    )
                if step and step.step_id != plan_tracker._active_step_id:
                    if plan_tracker._active_step_id:
                        evt = plan_tracker.complete_active_step()
                        if evt:
                            yield await sse_event(evt)
                    yield await sse_event(
                        plan_tracker.activate_step(step.step_id)
                    )

            outcome = await _apply_single_tool_call(
                tc_id=tc.id,
                tc_name=tc.name,
                resolved_args=resolved_args,
                allowed_tool_names=route.allowed_tool_names,
                store=store,
                trace=trace,
                add_notes_failures=_add_notes_failures,
                emit_sse=(execution_mode == "apply"),
            )

            for evt in outcome.sse_events:
                yield await sse_event(evt)

            if not outcome.skipped:
                tool_calls_collected.append({"tool": tc.name, "params": outcome.enriched_params})
                tool_calls_collected.extend(outcome.extra_tool_calls)

                if plan_tracker and execution_mode == "apply":
                    _step = (
                        plan_tracker.step_for_tool_index(tc_idx)
                        or plan_tracker.find_step_for_tool(
                            tc.name, outcome.enriched_params, store
                        )
                    )
                    if _step:
                        _step.result = _build_step_result(
                            tc.name, outcome.enriched_params, _step.result,
                        )

            messages.append(outcome.msg_call)
            iter_tool_results.append(outcome.tool_result)
            messages.append(outcome.msg_result)

        if plan_tracker and plan_tracker._active_step_id and execution_mode == "apply":
            evt = plan_tracker.complete_active_step()
            if evt:
                yield await sse_event(evt)

        if response is not None and response.has_tool_calls:
            snapshot = _entity_manifest(store)
            messages.append({
                "role": "system",
                "content": (
                    "ENTITY STATE AFTER TOOL CALLS (authoritative — use these IDs):\n"
                    + json.dumps(snapshot, indent=None)
                    + "\nUse the IDs above for subsequent tool calls. "
                    "Do NOT re-add notes to regions that already have notes (check noteCount). "
                    "Do NOT call stori_clear_notes unless explicitly replacing content. "
                    "A successful stori_add_notes response means the notes were stored — "
                    "do not redo the call."
                ),
            })

        if route.force_stop_after and tool_calls_collected:
            logger.info(f"[{trace.trace_id[:8]}] ✅ Force stop after {len(tool_calls_collected)} tool(s)")
            break

        if is_composition and iteration < max_iterations:
            all_tracks = store.registry.list_tracks()
            incomplete = _get_incomplete_tracks(store, tool_calls_collected)

            if not all_tracks:
                continuation = (
                    "You haven't created any tracks yet. "
                    "Use stori_add_midi_track to create the instruments, "
                    "then stori_add_midi_region and stori_add_notes for each."
                )
                messages.append({"role": "user", "content": continuation})
                logger.info(
                    f"[{trace.trace_id[:8]}] 🔄 Continuation: no tracks yet "
                    f"(iteration {iteration})"
                )
                continue
            elif incomplete:
                if plan_tracker and execution_mode == "apply":
                    incomplete_set = set(incomplete)
                    existing_track_names = {
                        t.name for t in store.registry.list_tracks()
                    }
                    for _step in plan_tracker.steps:
                        if (
                            _step.track_name
                            and _step.status in ("active", "pending")
                            and _step.track_name in existing_track_names
                            and _step.track_name not in incomplete_set
                        ):
                            yield await sse_event(
                                plan_tracker.complete_step_by_id(
                                    _step.step_id,
                                    f"Created {_step.track_name}",
                                )
                            )
                    messages.append({
                        "role": "system",
                        "content": plan_tracker.progress_context(),
                    })

                continuation = (
                    f"Continue — these tracks still need regions and notes: "
                    f"{', '.join(incomplete)}. "
                    f"Call stori_add_midi_region AND stori_add_notes together for each track. "
                    f"Use multiple tool calls in one response."
                )
                messages.append({"role": "user", "content": continuation})
                logger.info(
                    f"[{trace.trace_id[:8]}] 🔄 Continuation: {len(incomplete)} tracks still need content "
                    f"(iteration {iteration})"
                )
                continue
            else:
                missing_expressive = _get_missing_expressive_steps(
                    parsed, tool_calls_collected
                )
                if missing_expressive:
                    entity_snapshot = _entity_manifest(store)
                    messages.append({
                        "role": "system",
                        "content": (
                            "EXPRESSIVE PHASE LOCK: All tracks have been created and have notes. "
                            "You MUST NOT call stori_add_midi_track, stori_add_midi_region, "
                            "stori_add_notes, or any track/region creation tool. "
                            "Only call: stori_add_insert_effect, stori_add_midi_cc, "
                            "stori_add_pitch_bend, stori_add_automation, stori_ensure_bus, stori_add_send."
                        ),
                    })
                    expressive_msg = (
                        "⚠️ EXPRESSIVE PHASE — call ALL of these in ONE batch, then stop:\n"
                        + "\n".join(f"  {i+1}. {m}" for i, m in enumerate(missing_expressive))
                        + f"\n\nEntity IDs for your calls:\n{entity_snapshot}"
                        + "\n\nBatch ALL tool calls in a single response. No text. Just the tool calls."
                    )
                    messages.append({"role": "user", "content": expressive_msg})
                    logger.info(
                        f"[{trace.trace_id[:8]}] 🔄 Continuation: {len(missing_expressive)} "
                        f"expressive step(s) pending (iteration {iteration})"
                    )
                    continue
                logger.info(
                    f"[{trace.trace_id[:8]}] ✅ All tracks and expressive steps done "
                    f"after iteration {iteration}"
                )
                break

        if not is_composition:
            if response is not None and response.has_tool_calls:
                logger.info(
                    f"[{trace.trace_id[:8]}] ✅ Non-composition: executed "
                    f"{len(response.tool_calls)} tool(s), stopping after iteration {iteration}"
                )
                break
            break

    # =========================================================================
    # Variation Mode: Compute and emit variation per spec (meta/phrase/done)
    # =========================================================================
    if execution_mode == "variation" and tool_calls_collected:
        from app.core.executor import execute_plan_variation

        tool_call_objs = [
            ToolCall(name=cast(str, tc["tool"]), params=cast(dict[str, Any], tc["params"]))
            for tc in tool_calls_collected
        ]

        variation = await execute_plan_variation(
            tool_calls=tool_call_objs,
            project_state=project_context,
            intent=prompt,
            conversation_id=store.conversation_id,
            explanation=None,
            quality_preset=quality_preset,
        )

        from app.core.maestro_composing import _store_variation
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

        for phrase in variation.phrases:
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
            f"[{trace.trace_id[:8]}] EDITING variation streamed: "
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
        return

    # =========================================================================
    # Apply Mode: Standard completion
    # =========================================================================

    if plan_tracker:
        for skip_evt in plan_tracker.finalize_pending_as_skipped():
            yield await sse_event(skip_evt)

    yield await sse_event({
        "type": "complete",
        "success": True,
        "toolCalls": tool_calls_collected,
        "stateVersion": store.version,
        "traceId": trace.trace_id,
        **_context_usage_fields(usage_tracker, llm.model),
    })
