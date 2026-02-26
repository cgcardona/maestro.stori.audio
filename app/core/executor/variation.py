"""Variation mode execution — two-phase pipeline.

Phase 1 (Maestro orchestration):
    ``execute_tools_for_variation`` dispatches tool calls against a StateStore,
    collects base/proposed note data in a ``VariationContext``, and returns it.
    The store is held in a ``VariationExecutionContext`` that does NOT leak
    to the caller.

Phase 2 (Muse computation):
    ``compute_variation_from_context`` takes *only* the collected musical data
    (no StateStore access) and produces a ``Variation`` diff via
    ``VariationService``.

``execute_plan_variation`` is a thin convenience wrapper that runs both phases
in sequence and translates the boundary (extracting region metadata from the
store before handing off to Muse).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid as uuid_module
from typing import Any, Awaitable, Callable

from app.contracts.project_types import ProjectContext
from app.contracts.json_types import (
    AftertouchDict,
    CCEventDict,
    NoteDict,
    PitchBendDict,
)
from app.core.expansion import ToolCall, dedupe_tool_calls
from app.core.tool_names import ToolName
from app.core.tools import get_tool_meta, ToolTier, ToolKind
from app.core.tracing import get_trace_context, trace_span
from app.core.emotion_vector import EmotionVector, emotion_vector_from_stori_prompt
from app.core.state_store import get_or_create_store
from app.core.executor.models import VariationContext, VariationExecutionContext
from app.core.executor.phases import _group_into_phases
from app.models.variation import Variation
from app.services.variation import get_variation_service
from app.contracts.generation_types import GenerationContext
from app.services.music_generator import get_music_generator

logger = logging.getLogger(__name__)

_GENERATOR_TIMEOUT: float = 180
_MAX_PARALLEL_GROUPS = 5


def _extract_notes_from_project(
    project_state: ProjectContext,
    var_ctx: VariationContext,
    exec_ctx: VariationExecutionContext,
) -> None:
    """Extract existing notes from project state into variation context.

    Falls back to StateStore (via exec_ctx) when the frontend omits the notes array.
    """
    tracks = project_state.get("tracks", [])
    for track in tracks:
        track_id = track.get("id", "")
        for region in track.get("regions", []):
            region_id = region.get("id", "")
            notes = region.get("notes", [])
            if not notes:
                notes = exec_ctx.store.get_region_notes(region_id)
            if region_id and notes:
                var_ctx.capture_base_notes(region_id, track_id, notes)


async def _process_call_for_variation(
    call: ToolCall,
    var_ctx: VariationContext,
    exec_ctx: VariationExecutionContext,
    quality_preset: str | None = None,
    emotion_vector: EmotionVector | None = None,
) -> dict[str, Any]:
    """
    Process a tool call to extract proposed notes for variation.

    Uses ``exec_ctx.store`` for entity resolution and creation (Maestro concern).
    Records musical data into ``var_ctx`` (data accumulation for Muse).
    """
    params = call.params.copy()
    store = exec_ctx.store

    if "trackName" in params and "trackId" not in params:
        track_name = params["trackName"]
        track_id = store.registry.resolve_track(track_name)
        if track_id:
            params["trackId"] = track_id

    if "regionName" in params and "regionId" not in params:
        region_name = params["regionName"]
        parent_track = params.get("trackId") or params.get("trackName")
        region_id = store.registry.resolve_region(region_name, parent_track)
        if region_id:
            params["regionId"] = region_id

    if call.name == ToolName.ADD_MIDI_TRACK:
        track_name = params.get("name", "Track")
        existing = store.registry.resolve_track(track_name)
        if not existing:
            track_id = store.create_track(track_name, track_id=params.get("trackId"))
            params["trackId"] = track_id
            logger.info(f"🎹 [variation] Registered track: {track_name} → {track_id[:8]}")

            from app.core.gm_instruments import infer_gm_program_with_context
            inference = infer_gm_program_with_context(
                track_name=track_name,
                instrument=params.get("instrument"),
            )
            params["_gmInstrumentName"] = inference.instrument_name
            params["_isDrums"] = inference.is_drums
            if inference.needs_program_change:
                params["gmProgram"] = inference.program
        else:
            params["trackId"] = existing
            logger.debug(f"🎹 [variation] Track already exists: {track_name} → {existing[:8]}")

    elif call.name == ToolName.ADD_MIDI_REGION:
        track_id = params.get("trackId", "")
        if not track_id:
            track_ref = params.get("trackName") or params.get("name")
            if track_ref:
                track_id = store.registry.resolve_track(track_ref) or ""
                if track_id:
                    params["trackId"] = track_id
        if track_id:
            region_name = params.get("name", "Region")
            region_id = store.create_region(
                name=region_name,
                parent_track_id=track_id,
                region_id=None,
                metadata={
                    "startBeat": params.get("startBeat", 0),
                    "durationBeats": params.get("durationBeats", 16),
                },
            )
            params["regionId"] = region_id
            logger.info(
                f"📎 [variation] Registered region: {region_name} → {region_id[:8]} "
                f"(track={track_id[:8]})"
            )
        else:
            logger.warning("⚠️ [variation] Cannot create region — no track resolved")

    elif call.name == ToolName.ADD_NOTES:
        region_id = params.get("regionId", "")
        track_id = params.get("trackId", "")
        notes = params.get("notes", [])

        if not region_id:
            logger.warning(
                f"⚠️ stori_add_notes: missing regionId, dropping {len(notes)} notes"
            )
        elif notes:
            registered = store.registry.get_region(region_id)
            if not registered:
                resolved_track_id = (
                    store.registry.resolve_track(params.get("trackName", ""))
                    or track_id
                )
                fallback_region_id = (
                    store.registry.get_latest_region_for_track(resolved_track_id)
                    if resolved_track_id else None
                )
                if fallback_region_id:
                    logger.warning(
                        f"⚠️ stori_add_notes: regionId={region_id[:8]} not in registry; "
                        f"falling back to latest region={fallback_region_id[:8]} "
                        f"for track={resolved_track_id[:8]}"
                    )
                    region_id = fallback_region_id
                    track_id = resolved_track_id
                else:
                    logger.warning(
                        f"⚠️ stori_add_notes: regionId={region_id[:8]} not in registry "
                        f"and no fallback found; dropping {len(notes)} notes"
                    )
                    region_id = ""

            if region_id:
                if not track_id:
                    entity = store.registry.get_region(region_id)
                    track_id = entity.parent_id if entity else ""

                if region_id not in var_ctx.base.notes:
                    var_ctx.capture_base_notes(region_id, track_id or "", [])

                var_ctx.record_proposed_notes(region_id, notes)
                logger.info(
                    f"📝 stori_add_notes: {len(notes)} notes → "
                    f"region={region_id[:8]} track={(track_id or '')[:8]}"
                )

    elif call.name == ToolName.ADD_MIDI_CC:
        region_id = params.get("regionId", "")
        cc = params.get("cc")
        events = params.get("events", [])
        if region_id and cc is not None and events:
            cc_events: list[CCEventDict] = [{"cc": cc, "beat": e["beat"], "value": e["value"]} for e in events]
            var_ctx.record_proposed_cc(region_id, cc_events)
            logger.info(
                f"🎛️ stori_add_midi_cc: CC{cc} {len(events)} events → region={region_id[:8]}"
            )

    elif call.name == ToolName.ADD_PITCH_BEND:
        region_id = params.get("regionId", "")
        events = params.get("events", [])
        if region_id and events:
            var_ctx.record_proposed_pitch_bends(region_id, events)
            logger.info(
                f"🎛️ stori_add_pitch_bend: {len(events)} events → region={region_id[:8]}"
            )

    elif call.name == ToolName.ADD_AFTERTOUCH:
        region_id = params.get("regionId", "")
        events = params.get("events", [])
        if region_id and events:
            var_ctx.record_proposed_aftertouch(region_id, events)
            logger.info(
                f"🎛️ stori_add_aftertouch: {len(events)} events → region={region_id[:8]}"
            )

    meta = get_tool_meta(call.name)
    if meta and meta.tier == ToolTier.TIER1 and meta.kind == ToolKind.GENERATOR:
        mg = get_music_generator()

        gen_params = {
            "instrument": params.get("role", "drums"),
            "style": params.get("style", ""),
            "tempo": params.get("tempo", 120),
            "bars": params.get("bars", 4),
            "key": params.get("key"),
            "chords": params.get("chords"),
        }

        try:
            gen_start = time.time()
            logger.info(f"🎵 Starting generator {call.name} with params: {gen_params}")

            result = await asyncio.wait_for(
                mg.generate(
                    **gen_params,
                    context=GenerationContext(
                        quality_preset=quality_preset or "quality",
                        emotion_vector=emotion_vector,
                    ),
                ),
                timeout=_GENERATOR_TIMEOUT,
            )

            gen_duration = time.time() - gen_start
            logger.info(
                f"🎵 Generator {call.name} completed in {gen_duration:.1f}s: "
                f"success={result.success}, "
                f"notes={len(result.notes) if result.notes else 0}"
            )

            if result.success and result.notes:
                track_name = params.get("trackName", gen_params["instrument"].capitalize())
                track_id = (
                    store.registry.resolve_track(track_name)
                    or params.get("trackId", "")
                )

                if track_id:
                    region_id = store.registry.get_latest_region_for_track(track_id)

                    if region_id:
                        if region_id not in var_ctx.base.notes:
                            var_ctx.capture_base_notes(region_id, track_id, [])

                        var_ctx.record_proposed_notes(region_id, result.notes)
                        var_ctx.record_proposed_cc(region_id, result.cc_events)
                        var_ctx.record_proposed_pitch_bends(region_id, result.pitch_bends)
                        var_ctx.record_proposed_aftertouch(region_id, result.aftertouch)
                        params["regionId"] = region_id
                        params["trackId"] = track_id
                        logger.info(
                            f"📝 Recorded {len(result.notes)} notes, "
                            f"{len(result.cc_events)} CC, "
                            f"{len(result.pitch_bends)} PB, "
                            f"{len(result.aftertouch)} AT for "
                            f"region={region_id[:8]} track={track_id[:8]}"
                        )
                    else:
                        logger.warning(
                            f"⚠️ No region found for track={track_id[:8]}, "
                            f"dropping {len(result.notes)} generated notes"
                        )
                else:
                    logger.warning(
                        f"⚠️ Could not resolve track '{track_name}', "
                        f"dropping {len(result.notes)} generated notes"
                    )
            elif not result.success:
                logger.warning(f"⚠️ Generator {call.name} failed: {result.error}")

        except asyncio.TimeoutError:
            logger.error(
                f"⏱ Generator {call.name} timed out after {_GENERATOR_TIMEOUT}s"
            )
        except Exception as e:
            logger.exception(f"⚠️ Generator simulation failed for {call.name}: {e}")

    return params


def compute_variation_from_context(
    *,
    base_notes: dict[str, list[NoteDict]],
    proposed_notes: dict[str, list[NoteDict]],
    track_regions: dict[str, str],
    proposed_cc: dict[str, list[CCEventDict]],
    proposed_pitch_bends: dict[str, list[PitchBendDict]],
    proposed_aftertouch: dict[str, list[AftertouchDict]],
    region_start_beats: dict[str, float],
    intent: str,
    explanation: str | None = None,
) -> Variation:
    """Muse computation — produce a Variation diff from collected musical data.

    This function has NO access to StateStore or EntityRegistry.  All inputs
    are plain data extracted at the Maestro->Muse boundary.
    """
    variation_service = get_variation_service()

    if len(proposed_notes) > 1:
        return variation_service.compute_multi_region_variation(
            base_regions=base_notes,
            proposed_regions=proposed_notes,
            track_regions=track_regions,
            intent=intent,
            explanation=explanation,
            region_start_beats=region_start_beats,
            region_cc=proposed_cc,
            region_pitch_bends=proposed_pitch_bends,
            region_aftertouch=proposed_aftertouch,
        )

    if proposed_notes:
        region_id = next(iter(proposed_notes.keys()))
        track_id = track_regions.get(region_id, "unknown")
        return variation_service.compute_variation(
            base_notes=base_notes.get(region_id, []),
            proposed_notes=proposed_notes.get(region_id, []),
            region_id=region_id,
            track_id=track_id,
            intent=intent,
            explanation=explanation,
            region_start_beat=region_start_beats.get(region_id, 0.0),
            cc_events=proposed_cc.get(region_id),
            pitch_bends=proposed_pitch_bends.get(region_id),
            aftertouch=proposed_aftertouch.get(region_id),
        )

    return Variation(
        variation_id=str(uuid_module.uuid4()),
        intent=intent,
        ai_explanation=explanation,
        affected_tracks=[],
        affected_regions=[],
        beat_range=(0.0, 0.0),
        phrases=[],
    )


async def execute_tools_for_variation(
    tool_calls: list[ToolCall],
    project_state: ProjectContext,
    conversation_id: str | None = None,
    explanation: str | None = None,
    quality_preset: str | None = None,
    tool_event_callback: Callable[..., Awaitable[None]] | None = None,
    pre_tool_callback: Callable[..., Awaitable[None]] | None = None,
    post_tool_callback: Callable[..., Awaitable[None]] | None = None,
) -> VariationContext:
    """Maestro orchestration — dispatch tool calls, collect base/proposed state.

    Creates a ``VariationExecutionContext`` internally for store access.
    Returns a data-only ``VariationContext`` — the store does not leak.
    """
    trace = get_trace_context()

    store = get_or_create_store(
        conversation_id=conversation_id or "default",
        project_id=project_state.get("id"),
    )
    store.sync_from_client(project_state)

    tool_calls = dedupe_tool_calls(tool_calls)

    exec_ctx = VariationExecutionContext(store=store, trace=trace)
    var_ctx = VariationContext(trace=trace)

    if not tool_calls:
        return var_ctx

    logger.info(f"🎭 Variation mode: {len(tool_calls)} tool calls")
    _extract_notes_from_project(project_state, var_ctx, exec_ctx)

    emotion_vector: EmotionVector | None = None
    if explanation:
        emotion_vector = emotion_vector_from_stori_prompt(explanation)
        logger.info(f"🎭 Emotion vector derived: {emotion_vector}")

    phase1, instrument_groups, instrument_order, phase3 = _group_into_phases(tool_calls)

    completed_count = [0]

    async def _dispatch(call: ToolCall) -> None:
        logger.info(f"🔧 Processing: {call.name}")
        if pre_tool_callback:
            await pre_tool_callback(call.name, call.params)
        elif tool_event_callback:
            await tool_event_callback(call.id, call.name, call.params)
        resolved_params = await _process_call_for_variation(
            call,
            var_ctx,
            exec_ctx,
            quality_preset=quality_preset,
            emotion_vector=emotion_vector,
        )
        if post_tool_callback:
            await post_tool_callback(call.name, resolved_params)
        completed_count[0] += 1

    for call in phase1:
        await _dispatch(call)

    if instrument_groups:
        logger.info(
            f"🚀 Parallel instrument execution: {len(instrument_groups)} groups "
            f"({', '.join(instrument_order)}), max {_MAX_PARALLEL_GROUPS} concurrent"
        )
        semaphore = asyncio.Semaphore(_MAX_PARALLEL_GROUPS)

        async def _run_instrument_group(calls: list[ToolCall]) -> None:
            async with semaphore:
                for call in calls:
                    await _dispatch(call)

        await asyncio.gather(
            *[_run_instrument_group(instrument_groups[name]) for name in instrument_order]
        )

    for call in phase3:
        await _dispatch(call)

    total_base = sum(len(n) for n in var_ctx.base.notes.values())
    total_proposed = sum(len(n) for n in var_ctx.proposed.notes.values())
    logger.info(
        f"📊 Variation context: {len(var_ctx.base.notes)} base regions ({total_base} notes), "
        f"{len(var_ctx.proposed.notes)} proposed regions ({total_proposed} notes)"
    )

    # Collect region start beats at the Maestro→Muse boundary
    for rid in set(var_ctx.base.notes.keys()) | set(var_ctx.proposed.notes.keys()):
        entity = store.registry.get_region(rid)
        if entity:
            var_ctx.proposed.region_start_beats[rid] = float(entity.metadata.start_beat)

    return var_ctx


async def execute_plan_variation(
    tool_calls: list[ToolCall],
    project_state: ProjectContext,
    intent: str,
    conversation_id: str | None = None,
    explanation: str | None = None,
    quality_preset: str | None = None,
    tool_event_callback: Callable[..., Awaitable[None]] | None = None,
    pre_tool_callback: Callable[..., Awaitable[None]] | None = None,
    post_tool_callback: Callable[..., Awaitable[None]] | None = None,
) -> Variation:
    """Convenience wrapper — runs Maestro orchestration then Muse computation.

    Equivalent to calling ``execute_tools_for_variation`` followed by
    ``compute_variation_from_context`` with the boundary translation in between.
    """
    start_time = time.time()
    trace = get_trace_context()

    with trace_span(trace, "execute_plan_variation", {"tool_count": len(tool_calls)}):
        var_ctx = await execute_tools_for_variation(
            tool_calls=tool_calls,
            project_state=project_state,
            conversation_id=conversation_id,
            explanation=explanation,
            quality_preset=quality_preset,
            tool_event_callback=tool_event_callback,
            pre_tool_callback=pre_tool_callback,
            post_tool_callback=post_tool_callback,
        )

        variation = compute_variation_from_context(
            base_notes=var_ctx.base.notes,
            proposed_notes=var_ctx.proposed.notes,
            track_regions=var_ctx.proposed.track_regions,
            proposed_cc=var_ctx.proposed.cc,
            proposed_pitch_bends=var_ctx.proposed.pitch_bends,
            proposed_aftertouch=var_ctx.proposed.aftertouch,
            region_start_beats=var_ctx.proposed.region_start_beats,
            intent=intent,
            explanation=explanation,
        )

        duration_ms = (time.time() - start_time) * 1000
        logger.info(
            f"✨ Variation computed: {variation.total_changes} changes in "
            f"{len(variation.phrases)} phrases ({duration_ms:.1f}ms)"
        )

        return variation
