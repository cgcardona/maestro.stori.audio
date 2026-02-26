"""EDITING handler — LLM tool calls with allowlist, validation, and continuation."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, AsyncIterator, Awaitable, Callable

if TYPE_CHECKING:
    from app.core.state_store import StateStore

from app.config import settings
from app.contracts.llm_types import ChatMessage
from app.contracts.project_types import ProjectContext
from app.core.entity_context import build_entity_context_for_llm, format_project_context

from app.contracts.json_types import ToolCallDict
from app.core.expansion import ToolCall
from app.core.intent import Intent, IntentResult, SSEState
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
from app.core.stream_utils import strip_tool_echoes
from app.core.tracing import TraceContext, log_llm_call, trace_span
from app.core.maestro_helpers import (
    UsageTracker,
    StreamFinalResponse,
    _context_usage_fields,
    _resolve_variable_refs,
    _stream_llm_response,
)
from app.protocol.emitter import emit
from app.protocol.events import (
    CompleteEvent,
    ContentEvent,
    DoneEvent,
    MetaEvent,
    NoteChangeSchema,
    PhraseEvent,
    StatusEvent,
    SummaryEvent,
)
from app.core.maestro_plan_tracker import _PlanTracker, _build_step_result
from app.core.maestro_editing.continuation import (
    _get_incomplete_tracks,
    _get_missing_expressive_steps,
)
from app.core.maestro_editing.tool_execution import _apply_single_tool_call


# ---------------------------------------------------------------------------
# Shared LLM tool dispatch loop
# ---------------------------------------------------------------------------


async def _run_llm_tool_loop(
    *,
    prompt: str,
    project_context: ProjectContext,
    route: IntentResult,
    llm: LLMClient,
    store: StateStore,
    trace: TraceContext,
    usage_tracker: UsageTracker | None,
    conversation_history: list[ChatMessage],
    is_cancelled: Callable[[], Awaitable[bool]] | None,
    quality_preset: str | None,
    emit_sse: bool,
    plan_tracker: _PlanTracker | None,
    collected: list[ToolCallDict],
) -> AsyncIterator[str]:
    """Shared LLM iteration loop — dispatches tool calls and accumulates results.

    Yields SSE event strings.  Appends executed tool call dicts to ``collected``
    so the caller can inspect what was dispatched.
    """
    from app.core.tools import ALL_TOOLS

    if route.intent == Intent.GENERATE_MUSIC:
        sys_prompt = system_prompt_base() + "\n" + editing_composition_prompt()
    else:
        required_single = bool(route.force_stop_after and route.tool_choice == "required")
        sys_prompt = system_prompt_base() + "\n" + editing_prompt(required_single)

    _slots = getattr(route, "slots", None)
    _extras = getattr(_slots, "extras", None) if _slots is not None else None
    parsed: ParsedPrompt | None = _extras.get("parsed_prompt") if isinstance(_extras, dict) else None
    if parsed is not None:
        sys_prompt += structured_prompt_context(parsed)
        if parsed.position is not None:
            start_beat = resolve_position(parsed.position, project_context or {})
            sys_prompt += sequential_context(start_beat, parsed.section, pos=parsed.position)

    allowed_tools = [t for t in ALL_TOOLS if t["function"]["name"] in route.allowed_tool_names]

    messages: list[ChatMessage] = [{"role": "system", "content": sys_prompt}]

    if project_context:
        messages.append({"role": "system", "content": format_project_context(project_context)})
    else:
        messages.append({"role": "system", "content": build_entity_context_for_llm(store)})

    if conversation_history:
        messages.extend(conversation_history)

    messages.append({"role": "user", "content": wrap_user_request(prompt)})

    is_composition = route.intent == Intent.GENERATE_MUSIC
    llm_max_tokens: int | None = settings.composition_max_tokens if is_composition else None
    reasoning_fraction: float | None = settings.composition_reasoning_fraction if is_composition else None

    iteration = 0
    _add_notes_failures: dict[str, int] = {}
    max_iterations = (
        settings.composition_max_iterations if is_composition
        else settings.orchestration_max_iterations
    )

    while iteration < max_iterations:
        iteration += 1

        if is_cancelled:
            try:
                if await is_cancelled():
                    break
            except Exception:
                pass

        with trace_span(trace, f"llm_iteration_{iteration}"):
            start_time = time.time()

            if llm.supports_reasoning():
                response = None
                async for item in _stream_llm_response(
                    llm, messages, allowed_tools, route.tool_choice,
                    trace,
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

        if route.force_stop_after:
            response = enforce_single_tool(response)

        if response.content:
            clean_content = strip_tool_echoes(response.content)
            if clean_content:
                yield emit(ContentEvent(content=clean_content))

        if not response.has_tool_calls:
            if not is_composition:
                break

        iter_tool_results: list[dict[str, object]] = []

        if (
            plan_tracker is None
            and response is not None
            and response.has_tool_calls
            and emit_sse
        ):
            _candidate = _PlanTracker()
            _candidate.build(
                response.tool_calls, prompt, project_context,
                is_composition, store,
            )
            if len(_candidate.steps) >= 2:
                plan_tracker = _candidate
                yield emit(plan_tracker.to_plan_event())
        elif (
            plan_tracker is not None
            and response is not None
            and response.has_tool_calls
            and emit_sse
        ):
            for tc in response.tool_calls:
                resolved = _resolve_variable_refs(tc.params, iter_tool_results)
                step = plan_tracker.find_step_for_tool(tc.name, resolved, store)
                if step and step.status == "pending":
                    yield emit(plan_tracker.activate_step(step.step_id))

        for tc_idx, tc in enumerate(response.tool_calls):
            resolved_args = _resolve_variable_refs(tc.params, iter_tool_results)

            if plan_tracker and emit_sse:
                step = plan_tracker.step_for_tool_index(tc_idx)
                if step is None:
                    step = plan_tracker.find_step_for_tool(
                        tc.name, resolved_args, store,
                    )
                if step and step.step_id != plan_tracker._active_step_id:
                    if plan_tracker._active_step_id:
                        evt = plan_tracker.complete_active_step()
                        if evt:
                            yield emit(evt)
                    yield emit(plan_tracker.activate_step(step.step_id))

            outcome = await _apply_single_tool_call(
                tc_id=tc.id,
                tc_name=tc.name,
                resolved_args=resolved_args,
                allowed_tool_names=route.allowed_tool_names,
                store=store,
                trace=trace,
                add_notes_failures=_add_notes_failures,
                emit_sse=emit_sse,
            )

            for sse_evt in outcome.sse_events:
                yield emit(sse_evt)

            if not outcome.skipped:
                _tc_dict: ToolCallDict = {"tool": tc.name, "params": outcome.enriched_params}
                collected.append(_tc_dict)
                collected.extend(outcome.extra_tool_calls)

                if plan_tracker and emit_sse:
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

        if plan_tracker and plan_tracker._active_step_id and emit_sse:
            evt = plan_tracker.complete_active_step()
            if evt:
                yield emit(evt)

        if response is not None and response.has_tool_calls:
            manifest = store.registry.agent_manifest()
            messages.append({
                "role": "system",
                "content": (
                    f"{manifest}\n"
                    "Use the IDs above for subsequent tool calls. "
                    "Do NOT re-add notes to regions that already have notes. "
                    "Do NOT call stori_clear_notes unless explicitly replacing content. "
                    "A successful stori_add_notes response means the notes were stored — "
                    "do not redo the call."
                ),
            })

        if route.force_stop_after and collected:
            break

        if is_composition and iteration < max_iterations:
            all_tracks = store.registry.list_tracks()
            incomplete = _get_incomplete_tracks(store, collected)

            if not all_tracks:
                continuation = (
                    "You haven't created any tracks yet. "
                    "Use stori_add_midi_track to create the instruments, "
                    "then stori_add_midi_region and stori_add_notes for each."
                )
                messages.append({"role": "user", "content": continuation})
                continue
            elif incomplete:
                if plan_tracker and emit_sse:
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
                            yield emit(plan_tracker.complete_step_by_id(
                                _step.step_id,
                                f"Created {_step.track_name}",
                            ))
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
                continue
            else:
                missing_expressive = _get_missing_expressive_steps(
                    parsed, collected
                )
                if missing_expressive:
                    manifest = store.registry.agent_manifest()
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
                        "EXPRESSIVE PHASE — call ALL of these in ONE batch, then stop:\n"
                        + "\n".join(f"  {i+1}. {m}" for i, m in enumerate(missing_expressive))
                        + f"\n\n{manifest}"
                        + "\n\nBatch ALL tool calls in a single response. No text. Just the tool calls."
                    )
                    messages.append({"role": "user", "content": expressive_msg})
                    continue
                break

        if not is_composition:
            break


# ---------------------------------------------------------------------------
# Mode-specific handlers
# ---------------------------------------------------------------------------


async def _handle_editing_apply(
    prompt: str,
    project_context: ProjectContext,
    route: IntentResult,
    llm: LLMClient,
    store: StateStore,
    trace: TraceContext,
    usage_tracker: UsageTracker | None,
    conversation_history: list[ChatMessage],
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    quality_preset: str | None = None,
) -> AsyncIterator[str]:
    """Handle EDITING in apply mode — immediate mutation with plan tracking."""
    yield emit(StatusEvent(message="Processing..."))

    is_composition = route.intent == Intent.GENERATE_MUSIC
    _slots = getattr(route, "slots", None)
    _extras = getattr(_slots, "extras", None) if _slots is not None else None
    parsed: ParsedPrompt | None = _extras.get("parsed_prompt") if isinstance(_extras, dict) else None

    plan_tracker: _PlanTracker | None = None
    if is_composition and parsed is not None:
        plan_tracker = _PlanTracker()
        plan_tracker.build_from_prompt(parsed, prompt, project_context or {})
        yield emit(plan_tracker.to_plan_event())

    tool_calls_collected: list[ToolCallDict] = []

    async for evt in _run_llm_tool_loop(
        prompt=prompt,
        project_context=project_context,
        route=route,
        llm=llm,
        store=store,
        trace=trace,
        usage_tracker=usage_tracker,
        conversation_history=conversation_history,
        is_cancelled=is_cancelled,
        quality_preset=quality_preset,
        emit_sse=True,
        plan_tracker=plan_tracker,
        collected=tool_calls_collected,
    ):
        yield evt

    if plan_tracker:
        for skip_evt in plan_tracker.finalize_pending_as_skipped():
            yield emit(skip_evt)

    if tool_calls_collected:
        _summary_tracks: list[str] = []
        _summary_regions = 0
        _summary_notes = 0
        _summary_effects = 0
        for _tc in tool_calls_collected:
            _tc_name = _tc.get("tool", "")
            _tc_params = _tc.get("params", {})
            if _tc_name == "stori_add_midi_track":
                _name_val = _tc_params.get("name", "")
                _summary_tracks.append(_name_val if isinstance(_name_val, str) else "")
            elif _tc_name == "stori_add_midi_region":
                _summary_regions += 1
            elif _tc_name == "stori_add_notes":
                _notes_val = _tc_params.get("notes", [])
                _summary_notes += len(_notes_val) if isinstance(_notes_val, list) else 0
            elif _tc_name == "stori_add_insert_effect":
                _summary_effects += 1
        yield emit(SummaryEvent(
            tracks=_summary_tracks, regions=_summary_regions,
            notes=_summary_notes, effects=_summary_effects,
        ))

    yield emit(CompleteEvent(
        success=True,
        tool_calls=tool_calls_collected,
        state_version=store.version,
        trace_id=trace.trace_id,
        **_context_usage_fields(usage_tracker, llm.model),
    ))


async def _handle_editing_variation(
    prompt: str,
    project_context: ProjectContext,
    route: IntentResult,
    llm: LLMClient,
    store: StateStore,
    trace: TraceContext,
    usage_tracker: UsageTracker | None,
    conversation_history: list[ChatMessage],
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    quality_preset: str | None = None,
) -> AsyncIterator[str]:
    """Handle EDITING in variation mode — compute + emit variation proposal."""
    yield emit(StatusEvent(message="Generating variation..."))

    tool_calls_collected: list[ToolCallDict] = []

    async for evt in _run_llm_tool_loop(
        prompt=prompt,
        project_context=project_context,
        route=route,
        llm=llm,
        store=store,
        trace=trace,
        usage_tracker=usage_tracker,
        conversation_history=conversation_history,
        is_cancelled=is_cancelled,
        quality_preset=quality_preset,
        emit_sse=False,
        plan_tracker=None,
        collected=tool_calls_collected,
    ):
        yield evt

    if not tool_calls_collected:
        yield emit(CompleteEvent(
            success=True,
            trace_id=trace.trace_id,
            **_context_usage_fields(usage_tracker, llm.model),
        ))
        return

    from app.core.executor import execute_plan_variation

    tool_call_objs = [
        ToolCall(name=str(tc["tool"]), params=dict(tc.get("params", {})))
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
    from app.contracts.json_types import RegionMetadataWire
    _edit_region_metadata: dict[str, RegionMetadataWire] = {}
    for _re in store.registry.list_regions():
        _rmeta: RegionMetadataWire = {
            "startBeat": _re.metadata.start_beat,
            "durationBeats": _re.metadata.duration_beats,
            "name": _re.name,
        }
        _edit_region_metadata[_re.id] = _rmeta
    await _store_variation(
        variation, project_context,
        base_state_id=store.get_state_id(),
        conversation_id=store.conversation_id,
        region_metadata=_edit_region_metadata,
    )

    note_counts = variation.note_counts
    yield emit(MetaEvent(
        variation_id=variation.variation_id,
        base_state_id=store.get_state_id(),
        intent=variation.intent,
        ai_explanation=variation.ai_explanation,
        affected_tracks=variation.affected_tracks,
        affected_regions=variation.affected_regions,
        note_counts=note_counts,
    ))

    for phrase in variation.phrases:
        yield emit(PhraseEvent(
            phrase_id=phrase.phrase_id,
            track_id=phrase.track_id,
            region_id=phrase.region_id,
            start_beat=phrase.start_beat,
            end_beat=phrase.end_beat,
            label=phrase.label,
            tags=phrase.tags,
            explanation=phrase.explanation,
            note_changes=[
                NoteChangeSchema.model_validate(nc.model_dump(by_alias=True))
                for nc in phrase.note_changes
            ],
            cc_events=phrase.cc_events,
            pitch_bends=phrase.pitch_bends,
            aftertouch=phrase.aftertouch,
        ))

    yield emit(DoneEvent(
        variation_id=variation.variation_id,
        phrase_count=len(variation.phrases),
    ))

    yield emit(CompleteEvent(
        success=True,
        variation_id=variation.variation_id,
        total_changes=variation.total_changes,
        phrase_count=len(variation.phrases),
        trace_id=trace.trace_id,
        **_context_usage_fields(usage_tracker, llm.model),
    ))


# ---------------------------------------------------------------------------
# Public dispatcher — backward-compatible entry point
# ---------------------------------------------------------------------------


async def _handle_editing(
    prompt: str,
    project_context: ProjectContext,
    route: IntentResult,
    llm: LLMClient,
    store: StateStore,
    trace: TraceContext,
    usage_tracker: UsageTracker | None,
    conversation_history: list[ChatMessage],
    execution_mode: str = "apply",
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    quality_preset: str | None = None,
) -> AsyncIterator[str]:
    """Dispatch to mode-specific handler — no branching inside."""
    if execution_mode == "variation":
        async for evt in _handle_editing_variation(
            prompt, project_context, route, llm, store, trace,
            usage_tracker, conversation_history,
            is_cancelled=is_cancelled, quality_preset=quality_preset,
        ):
            yield evt
    else:
        async for evt in _handle_editing_apply(
            prompt, project_context, route, llm, store, trace,
            usage_tracker, conversation_history,
            is_cancelled=is_cancelled, quality_preset=quality_preset,
        ):
            yield evt
