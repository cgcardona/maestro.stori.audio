"""COMPOSING handler — generate music via planner → executor → variation."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid as _uuid_mod
from typing import AsyncIterator, Awaitable, Callable

from app.contracts.json_types import JSONValue, NoteDict, RegionMetadataWire
from app.contracts.pydantic_types import wrap_dict
from app.contracts.project_types import ProjectContext

from app.core.entity_context import format_project_context
from app.core.intent import IntentResult
from app.core.llm_client import LLMClient
from app.core.planner import build_execution_plan_stream, ExecutionPlan
from app.core.prompt_parser import ParsedPrompt
from app.protocol.emitter import emit
from app.protocol.events import (
    CompleteEvent,
    ContentEvent,
    DoneEvent,
    ErrorEvent,
    MetaEvent,
    NoteChangeSchema,
    PhraseEvent,
    StatusEvent,
    MaestroEvent,
    ToolCallEvent,
    ToolStartEvent,
)
from app.core.state_store import StateStore
from app.core.tracing import TraceContext, trace_span
from app.core.maestro_editing.tool_execution import phase_for_tool
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
    project_context: ProjectContext,
    route: IntentResult,
    llm: LLMClient,
    store: StateStore,
    trace: TraceContext,
    usage_tracker: UsageTracker | None,
    conversation_id: str | None,
    quality_preset: str | None = None,
) -> AsyncIterator[str]:
    """Handle COMPOSING state - generate music via planner.

    All COMPOSING intents produce a Variation for human review.
    The planner generates a tool-call plan, the executor simulates it
    in variation mode, and the result is streamed as meta/phrase/done events.

    Phase 1 (Unified SSE UX): reasoning events are streamed during the
    planner's LLM call so the user sees the agent thinking — same UX as
    EDITING mode.
    """
    yield emit(StatusEvent(message="Thinking..."))

    _slots = getattr(route, "slots", None)
    _extras = getattr(_slots, "extras", None) if _slots is not None else None
    parsed: ParsedPrompt | None = (
        _extras.get("parsed_prompt") if isinstance(_extras, dict) else None
    )

    # ── Streaming planner: yields reasoning SSE events, then the plan ──
    plan: ExecutionPlan | None = None
    with trace_span(trace, "planner"):
        from app.contracts.json_types import JSONObject
        from app.protocol.emitter import parse_event as _parse

        async def _emit_sse(data: JSONObject) -> str:
            return emit(_parse(data))

        async for item in build_execution_plan_stream(
            user_prompt=prompt,
            project_state=project_context,
            route=route,
            llm=llm,
            parsed=parsed,
            usage_tracker=usage_tracker,
            emit_sse=_emit_sse,
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
            yield emit(composing_plan_tracker.to_plan_event())

        # PROPOSAL PHASE
        for tc in plan.tool_calls:
            yield emit(ToolCallEvent(
                id="",
                name=tc.name,
                label=_human_label_for_tool(tc.name, tc.params),
                phase=phase_for_tool(tc.name),
                params=wrap_dict(tc.params),
                proposal=True,
            ))

        # EXECUTION PHASE
        try:
            with trace_span(trace, "variation_generation", {"steps": len(plan.tool_calls)}):
                from app.core.executor import execute_plan_variation

                _event_queue: asyncio.Queue[MaestroEvent] = asyncio.Queue()
                _active_step_by_track: dict[str, str] = {}

                async def _on_pre_tool(
                    tool_name: str, params: dict[str, JSONValue],
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
                    await _event_queue.put(ToolStartEvent(
                        name=tool_name,
                        label=label,
                        phase=phase_for_tool(tool_name),
                    ))

                async def _on_post_tool(
                    tool_name: str, resolved_params: dict[str, JSONValue],
                ) -> None:
                    """Execution phase: toolCall with real UUID after success.

                    Generator tools (stori_generate_midi etc.) are backend-internal;
                    their output reaches the frontend via phrase events, not toolCall.
                    """
                    from app.core.maestro_plan_tracker.constants import _GENERATOR_TOOL_NAMES
                    if tool_name in _GENERATOR_TOOL_NAMES:
                        return
                    call_id = str(_uuid_mod.uuid4())
                    emit_params = _enrich_params_with_track_context(resolved_params, store)
                    label = _human_label_for_tool(tool_name, emit_params)
                    await _event_queue.put(ToolCallEvent(
                        id=call_id,
                        name=tool_name,
                        label=label,
                        phase=phase_for_tool(tool_name),
                        params=wrap_dict(emit_params),
                        proposal=False,
                    ))

                _VARIATION_TIMEOUT = 300
                task = asyncio.create_task(
                    execute_plan_variation(
                        tool_calls=plan.tool_calls,
                        project_state=project_context,
                        intent=prompt,
                        conversation_id=conversation_id,
                        explanation=plan.llm_response_text,
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
                            yield emit(event_data)
                        except asyncio.TimeoutError:
                            if task.done():
                                break
                        await asyncio.sleep(0)

                    while not _event_queue.empty():
                        evt = await _event_queue.get()
                        yield emit(evt)

                    variation = await task

                    for final_evt in composing_plan_tracker.complete_all_active_steps():
                        yield emit(final_evt)

                    for skip_evt in composing_plan_tracker.finalize_pending_as_skipped():
                        yield emit(skip_evt)

                except asyncio.TimeoutError:
                    logger.error(
                        f"[{trace.trace_id[:8]}] Variation generation timed out "
                        f"after {_VARIATION_TIMEOUT}s"
                    )
                    _fail_variation_id = str(_uuid_mod.uuid4())
                    yield emit(ErrorEvent(
                        message=f"Generation timed out after {_VARIATION_TIMEOUT}s",
                        trace_id=trace.trace_id,
                    ))
                    yield emit(DoneEvent(
                        variation_id=_fail_variation_id,
                        phrase_count=0,
                        status="failed",
                    ))
                    _usage = _context_usage_fields(usage_tracker, llm.model)
                    yield emit(CompleteEvent(
                        success=False,
                        error="timeout",
                        trace_id=trace.trace_id,
                        input_tokens=_usage["input_tokens"],
                        context_window_tokens=_usage["context_window_tokens"],
                    ))
                    return

                if len(variation.phrases) == 0:
                    logger.error(
                        f"[{trace.trace_id[:8]}] COMPOSING produced 0 phrases "
                        f"despite {len(plan.tool_calls)} tool calls — "
                        f"this indicates a generation or entity resolution failure. "
                        f"Proposed notes captured: {sum(len(n) for n in getattr(variation, '_proposed_notes', {}).values()) if hasattr(variation, '_proposed_notes') else 'N/A'}"
                    )

                _region_metadata: dict[str, RegionMetadataWire] = {}
                for _re in store.registry.list_regions():
                    _rmeta: RegionMetadataWire = {
                        "startBeat": _re.metadata.start_beat,
                        "durationBeats": _re.metadata.duration_beats,
                        "name": _re.name,
                    }
                    _region_metadata[_re.id] = _rmeta

                await _store_variation(
                    variation, project_context,
                    base_state_id=store.get_state_id(),
                    conversation_id=store.conversation_id,
                    region_metadata=_region_metadata,
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

                _usage = _context_usage_fields(usage_tracker, llm.model)
                yield emit(CompleteEvent(
                    success=True,
                    variation_id=variation.variation_id,
                    total_changes=variation.total_changes,
                    phrase_count=len(variation.phrases),
                    trace_id=trace.trace_id,
                    input_tokens=_usage["input_tokens"],
                    context_window_tokens=_usage["context_window_tokens"],
                ))

        except BaseException as e:
            logger.exception(
                f"[{trace.trace_id[:8]}] Variation generation failed: {e}"
            )
            _fail_variation_id = str(_uuid_mod.uuid4())
            yield emit(ErrorEvent(
                message=f"Generation failed: {e}",
                trace_id=trace.trace_id,
            ))
            yield emit(DoneEvent(
                variation_id=_fail_variation_id,
                phrase_count=0,
                status="failed",
            ))
            _usage = _context_usage_fields(usage_tracker, llm.model)
            yield emit(CompleteEvent(
                success=False,
                error=str(e),
                trace_id=trace.trace_id,
                input_tokens=_usage["input_tokens"],
                context_window_tokens=_usage["context_window_tokens"],
            ))
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
            yield emit(ContentEvent(
                content=(
                    "I understand you want to generate music. To help me create exactly what you're looking for, "
                    "could you tell me:\n"
                    "- What style or genre? (e.g., 'lofi', 'jazz', 'electronic')\n"
                    "- What tempo? (e.g., 90 BPM)\n"
                    "- How many bars? (e.g., 8 bars)\n\n"
                    "Example: 'Create an exotic melody at 100 BPM for 8 bars in C minor'"
                ),
            ))
        else:
            yield emit(ContentEvent(
                content=(
                    "I need more information to generate music. Please specify:\n"
                    "- Style/genre (e.g., 'boom bap', 'lofi', 'trap')\n"
                    "- Tempo (e.g., 90 BPM)\n"
                    "- Number of bars (e.g., 8 bars)\n\n"
                    "Example: 'Make a boom bap beat at 90 BPM with drums and bass for 8 bars'"
                ),
            ))

        _usage = _context_usage_fields(usage_tracker, llm.model)
        yield emit(CompleteEvent(
            success=True,
            tool_calls=[],
            trace_id=trace.trace_id,
            input_tokens=_usage["input_tokens"],
            context_window_tokens=_usage["context_window_tokens"],
        ))


# ---------------------------------------------------------------------------
# Agent Teams + Variation capture
# ---------------------------------------------------------------------------


async def _handle_composing_with_agent_teams(
    prompt: str,
    project_context: ProjectContext,
    parsed: ParsedPrompt,
    route: IntentResult,
    llm: LLMClient,
    store: StateStore,
    trace: TraceContext,
    usage_tracker: UsageTracker | None,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    quality_preset: str | None = None,
) -> AsyncIterator[str]:
    """Agent Teams composition with Variation capture.

    Combines streaming per-agent reasoning from Agent Teams with
    Muse's Variation system for human-reviewable commit/discard.

    ``Mode: compose`` is the directive — any compose request with parsed
    roles (1+) uses this path.  The instrument count determines
    parallelism (1 agent vs N parallel agents), not the execution path.

    Flow:
      1. Snapshot base notes from project state
      2. Run Agent Teams (streams reasoning, tool events, heartbeats)
      3. Collect generated notes from StateStore
      4. Compute Variation via VariationService
      5. Store variation for commit/discard
      6. Emit meta/phrase/done/complete events
    """
    from app.core.maestro_agent_teams import _handle_composition_agent_team
    from app.core.maestro_editing import _create_editing_composition_route
    from app.core.executor.snapshots import capture_base_snapshot, capture_proposed_snapshot
    from app.models.variation import Variation
    from app.services.variation import get_variation_service

    # ── 1. Snapshot base notes before Agent Teams runs ──
    _base_snapshot = capture_base_snapshot(store)
    _base_notes: dict[str, list[NoteDict]] = {}
    _track_regions: dict[str, str] = {}
    for track in project_context.get("tracks", []):
        track_id = track.get("id", "")
        for region in track.get("regions", []):
            rid = region.get("id", "")
            notes = region.get("notes", []) or _base_snapshot.notes.get(rid, [])
            if rid and notes:
                _base_notes[rid] = notes
                _track_regions[rid] = track_id

    # Agent Teams needs an editing-level tool allowlist (tempo, key, tracks,
    # regions, generators, effects).  The COMPOSING route may have a narrower
    # set, so we create an editing composition route for the coordinator.
    _at_route = _create_editing_composition_route(route)

    # ── 2. Run Agent Teams — intercept the ``complete`` event ──
    _agent_complete: dict[str, JSONValue] | None = None  # parse boundary: SSE complete event
    _SSE_PREFIX = "data: "

    async for event_str in _handle_composition_agent_team(
        prompt, project_context, parsed, _at_route, llm, store,
        trace, usage_tracker, is_cancelled=is_cancelled,
    ):
        if event_str.startswith(_SSE_PREFIX):
            try:
                _payload = json.loads(event_str[len(_SSE_PREFIX):].rstrip())
                if _payload.get("type") == "complete":
                    _agent_complete = _payload
                    continue
            except (json.JSONDecodeError, ValueError):
                pass
        yield event_str

    # ── 3. Collect proposed notes via snapshot (never read live StateStore) ──
    _proposed_snapshot = capture_proposed_snapshot(store)
    _proposed_notes: dict[str, list[NoteDict]] = {}
    _region_start_beats: dict[str, float] = {}

    for region_entity in store.registry.list_regions():
        rid = region_entity.id
        notes = _proposed_snapshot.notes.get(rid, [])
        if notes:
            _proposed_notes[rid] = notes
            _track_regions[rid] = region_entity.parent_id or ""
            if rid not in _base_notes:
                _base_notes[rid] = []
            _region_start_beats[rid] = float(region_entity.metadata.start_beat)

    # ── 4. Compute Variation ──
    _vs = get_variation_service()

    if _proposed_notes:
        if len(_proposed_notes) > 1:
            variation = _vs.compute_multi_region_variation(
                base_regions=_base_notes,
                proposed_regions=_proposed_notes,
                track_regions=_track_regions,
                intent=prompt,
                explanation=(
                    f"Agent Teams composition: "
                    f"{len(parsed.roles)} instrument(s)"
                ),
                region_start_beats=_region_start_beats,
            )
        else:
            _rid = next(iter(_proposed_notes))
            _tid = _track_regions.get(_rid, "")
            variation = _vs.compute_variation(
                base_notes=_base_notes.get(_rid, []),
                proposed_notes=_proposed_notes[_rid],
                region_id=_rid,
                track_id=_tid,
                intent=prompt,
                explanation=(
                    f"Agent Teams composition: "
                    f"{len(parsed.roles)} instrument(s)"
                ),
                region_start_beat=_region_start_beats.get(_rid, 0.0),
            )
    else:
        variation = Variation(
            variation_id=str(_uuid_mod.uuid4()),
            intent=prompt,
            ai_explanation="No notes generated",
            affected_tracks=[],
            affected_regions=[],
            beat_range=(0.0, 0.0),
            phrases=[],
        )

    logger.info(
        f"[{trace.trace_id[:8]}] 🎼 Variation computed from Agent Teams: "
        f"{variation.total_changes} changes in "
        f"{len(variation.phrases)} phrases"
    )

    # ── 5. Store variation for commit/discard ──
    _at_region_metadata: dict[str, RegionMetadataWire] = {}
    for _re in store.registry.list_regions():
        _rmeta_at: RegionMetadataWire = {
            "startBeat": _re.metadata.start_beat,
            "durationBeats": _re.metadata.duration_beats,
            "name": _re.name,
        }
        _at_region_metadata[_re.id] = _rmeta_at

    await _store_variation(
        variation, project_context,
        base_state_id=store.get_state_id(),
        conversation_id=store.conversation_id,
        region_metadata=_at_region_metadata,
    )

    # ── 6. Emit variation events ──
    yield emit(MetaEvent(
        variation_id=variation.variation_id,
        base_state_id=store.get_state_id(),
        intent=variation.intent,
        ai_explanation=variation.ai_explanation,
        affected_tracks=variation.affected_tracks,
        affected_regions=variation.affected_regions,
        note_counts=variation.note_counts,
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

    # Merge Agent Teams success/warnings with variation metadata
    _success_raw = (
        _agent_complete.get("success", True)
        if _agent_complete else True
    )
    _success = bool(_success_raw)
    _warnings_raw = _agent_complete.get("warnings") if _agent_complete else None
    _warnings: list[str] | None = (
        [str(w) for w in _warnings_raw if w is not None]
        if isinstance(_warnings_raw, list) else None
    )
    _sv_raw = _agent_complete.get("stateVersion") if _agent_complete else None
    _state_version: int | None = _sv_raw if isinstance(_sv_raw, int) else None

    _usage = _context_usage_fields(usage_tracker, llm.model)
    yield emit(CompleteEvent(
        success=_success,
        variation_id=variation.variation_id,
        total_changes=variation.total_changes,
        phrase_count=len(variation.phrases),
        trace_id=trace.trace_id,
        warnings=_warnings,
        state_version=_state_version,
        **_usage,
    ))
