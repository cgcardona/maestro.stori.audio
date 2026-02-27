"""Core tool execution — validate, enrich, persist, and emit for one tool call."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from maestro.contracts.generation_types import CompositionContext, RoleResult, UnifiedGenerationOutput
from maestro.contracts.llm_types import AssistantMessage, ToolResultMessage
from maestro.contracts.pydantic_types import wrap_dict
from maestro.core.gm_instruments import DRUM_ICON, icon_for_gm_program
from maestro.core.tool_validation import VALID_SF_SYMBOL_ICONS, validate_tool_call
from maestro.core.tracing import TraceContext, log_tool_call, log_validation_error, trace_span
from maestro.protocol.events import (
    GeneratorCompleteEvent,
    GeneratorStartEvent,
    MaestroEvent,
    ToolCallEvent,
    ToolErrorEvent,
    ToolStartEvent,
)

if TYPE_CHECKING:
    from maestro.core.state_store import StateStore

from maestro.contracts.json_types import JSONValue, NoteDict, ToolCallDict, json_list, is_note_dict
from maestro.core.maestro_helpers import (
    _build_tool_result,
    _enrich_params_with_track_context,
    _human_label_for_tool,
)
from maestro.core.maestro_plan_tracker import _ToolCallOutcome, _GENERATOR_TOOL_NAMES
from maestro.core.maestro_plan_tracker.constants import (
    _ARRANGEMENT_TOOL_NAMES,
    _SETUP_TOOL_NAMES,
    _EFFECT_TOOL_NAMES,
    _MIXING_TOOL_NAMES,
    _TRACK_CREATION_NAMES,
    _CONTENT_TOOL_NAMES,
    _EXPRESSION_TOOL_NAMES,
    _EXPRESSIVE_TOOL_NAMES,
)
from maestro.contracts.generation_types import GenerationContext
from maestro.services.music_generator import get_music_generator

logger = logging.getLogger(__name__)


def _sp(params: dict[str, JSONValue], key: str, default: str = "") -> str:
    """Extract a string value from a params dict, falling back to *default*."""
    v = params.get(key, default)
    return v if isinstance(v, str) else default


def _sp_opt(params: dict[str, JSONValue], key: str) -> str | None:
    """Extract an optional string value from a params dict."""
    v = params.get(key)
    return v if isinstance(v, str) else None


def _ip(params: dict[str, JSONValue], key: str, default: int = 0) -> int:
    """Extract an int value from a params dict."""
    v = params.get(key, default)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    return default


def _fp(params: dict[str, JSONValue], key: str, default: float = 0.0) -> float:
    """Extract a float value from a params dict."""
    v = params.get(key, default)
    if isinstance(v, (int, float)):
        return float(v)
    return default


def _list_dicts(params: dict[str, JSONValue], key: str) -> list[dict[str, JSONValue]]:
    """Extract a list of dicts from a params dict, filtering out non-dict items."""
    v = params.get(key, [])
    if not isinstance(v, list):
        return []
    return [item for item in v if isinstance(item, dict)]


def phase_for_tool(tool_name: str) -> str:
    """Map a tool name to its DAW workflow phase.

    Phases (mirrors a professional DAW session, in order):
      setup       — project scaffolding: tempo, key, track/region creation,
                    instrument selection, cosmetics, transport, UI
      composition — creative content: notes, MIDI generators
      arrangement — structural editing: move, transpose, quantize, swing, clear
      soundDesign — tone shaping: insert effects (EQ, compression, reverb…)
      expression  — performance data: MIDI CC, pitch bend, aftertouch
      mixing      — balance & routing: volume, pan, mute/solo, buses, sends,
                    automation
    """
    if tool_name in _SETUP_TOOL_NAMES:
        return "setup"
    if tool_name in _ARRANGEMENT_TOOL_NAMES:
        return "arrangement"
    if tool_name in _EXPRESSION_TOOL_NAMES:
        return "expression"
    if tool_name in _EFFECT_TOOL_NAMES:
        return "soundDesign"
    if tool_name in _MIXING_TOOL_NAMES:
        return "mixing"
    return "composition"


async def _execute_agent_generator(
    tc_id: str,
    tc_name: str,
    enriched_params: dict[str, JSONValue],
    store: StateStore,
    trace: TraceContext,
    composition_context: CompositionContext,
    emit_sse: bool,
    pre_emit_callback: (
        Callable[[list[MaestroEvent]], Awaitable[None]] | None
    ) = None,
) -> _ToolCallOutcome | None:
    """Route a generator tool call through MusicGenerator (Orpheus).

    Called from ``_apply_single_tool_call`` when the tool is a generator
    (``stori_generate_midi``) and ``composition_context`` is available.

    Returns a complete ``_ToolCallOutcome`` on success, or ``None`` to
    fall through to normal tool handling.
    """
    sse_events: list[MaestroEvent] = []
    extra_tool_calls: list[ToolCallDict] = []

    _role = enriched_params.get("role", "")
    role = str(_role) if _role else "melody"

    _style = enriched_params.get("style") or composition_context.get("style", "")
    style = str(_style) if _style else ""
    _tempo = enriched_params.get("tempo") or composition_context.get("tempo", 120)
    tempo = int(_tempo) if isinstance(_tempo, (int, float)) else 120
    _bars = enriched_params.get("bars") or composition_context.get("bars", 4)
    bars = int(_bars) if isinstance(_bars, (int, float)) else 4
    _key = enriched_params.get("key") or composition_context.get("key")
    key = str(_key) if isinstance(_key, str) else None
    _start_beat = enriched_params.get("start_beat", 0)
    start_beat = float(_start_beat) if isinstance(_start_beat, (int, float)) else 0.0
    _prompt = enriched_params.get("prompt", "")
    instrument_prompt = str(_prompt) if _prompt else ""

    # Prefer explicit trackId/regionId passed by the agent; fall back to registry lookup.
    _track_id = enriched_params.get("trackId", "")
    track_id = str(_track_id) if _track_id else ""
    _region_id = enriched_params.get("regionId", "")
    region_id = str(_region_id) if _region_id else ""

    if not track_id:
        _track_name = enriched_params.get("trackName", role.capitalize())
        track_name = str(_track_name) if _track_name else role.capitalize()
        track_id = store.registry.resolve_track(track_name) or ""

    if not region_id and track_id:
        region_id = store.registry.get_latest_region_for_track(track_id) or ""

    if not region_id:
        error_msg = (
            f"Generator {tc_name}: no region found for track '{track_id or role}' "
            f"(role='{role}'). stori_add_midi_region must be called for this track "
            f"before stori_generate_midi. Pass regionId from stori_add_midi_region."
        )
        logger.error(f"❌ [{trace.trace_id[:8]}] {error_msg}")
        error_result: dict[str, JSONValue] = {"error": error_msg}
        if emit_sse:
            sse_events.append(ToolErrorEvent(name=tc_name, error=error_msg))
        return _ToolCallOutcome(
            enriched_params=enriched_params,
            tool_result=error_result,
            sse_events=sse_events,
            msg_call={
                "role": "assistant",
                "tool_calls": [{"id": tc_id, "type": "function",
                                "function": {"name": tc_name, "arguments": json.dumps(enriched_params)}}],
            },
            msg_result={
                "role": "tool", "tool_call_id": tc_id,
                "content": json.dumps(error_result),
            },
            skipped=True,
        )

    _gen_label = f"Generating {role} via Orpheus"
    _gen_phase = phase_for_tool(tc_name)
    if emit_sse:
        _pre_events: list[MaestroEvent] = [
            ToolStartEvent(name=tc_name, label=_gen_label, phase=_gen_phase),
            GeneratorStartEvent(
                role=role, agent_id=role, style=style,
                bars=bars, start_beat=start_beat, label=role.capitalize(),
            ),
        ]
        if pre_emit_callback is not None:
            await pre_emit_callback(_pre_events)
        else:
            sse_events.extend(_pre_events)

    gen_ctx: GenerationContext = {
        "quality_preset": composition_context.get("quality_preset", "quality"),
    }
    emotion_vector = composition_context.get("emotion_vector")
    if emotion_vector is not None:
        gen_ctx["emotion_vector"] = emotion_vector
    if trace and hasattr(trace, "trace_id"):
        gen_ctx["composition_id"] = trace.trace_id

    import time as _time
    _gen_start = _time.monotonic()

    _prompt_preview = repr(instrument_prompt[:80]) if instrument_prompt else "none"
    logger.info(
        f"stori_generate_midi | role={role} track={track_id[:8] if track_id else 'none'} "
        f"region={region_id[:8] if region_id else 'none'} start_beat={start_beat} "
        f"bars={bars} style={style} key={key} prompt={_prompt_preview}"
    )

    try:
        mg = get_music_generator()
        _section_key = composition_context.get("section_key") if composition_context else None
        _all_instruments = composition_context.get("all_instruments") if composition_context else None

        if _section_key and _all_instruments:
            logger.info(
                f"🎼 [{trace.trace_id[:8]}] UNIFIED path: "
                f"section={_section_key} role={role} "
                f"all_instruments={_all_instruments}"
            )
            result = await mg.generate_for_section(
                section_key=_section_key,
                instrument=role,
                all_instruments=_all_instruments,
                style=style,
                tempo=tempo,
                bars=bars,
                key=key,
                context=gen_ctx,
            )
        else:
            logger.warning(
                f"⚠️ [{trace.trace_id[:8]}] PER-INSTRUMENT path (no unified): "
                f"role={role} section_key={_section_key} "
                f"all_instruments={_all_instruments} "
                f"context_keys={list(composition_context.keys()) if composition_context else 'None'}"
            )
            result = await mg.generate(
                instrument=role,
                style=style,
                tempo=tempo,
                bars=bars,
                key=key,
                context=gen_ctx,
            )
    except Exception as exc:
        logger.error(f"❌ [{trace.trace_id[:8]}] Generator {tc_name} failed: {exc}")
        error_result = {"error": str(exc)}
        if emit_sse:
            sse_events.append(ToolErrorEvent(name=tc_name, error=str(exc)))
        return _ToolCallOutcome(
            enriched_params=enriched_params,
            tool_result=error_result,
            sse_events=sse_events,
            msg_call={
                "role": "assistant",
                "tool_calls": [{"id": tc_id, "type": "function",
                                "function": {"name": tc_name, "arguments": json.dumps(enriched_params)}}],
            },
            msg_result={
                "role": "tool", "tool_call_id": tc_id,
                "content": json.dumps(error_result),
            },
            skipped=True,
        )

    _gen_duration_ms = int((_time.monotonic() - _gen_start) * 1000)

    if not result.success:
        _is_gpu_error = "gpu_unavailable" in (result.error or "") or "GPU" in (result.error or "")
        logger.warning(
            f"⚠️ [{trace.trace_id[:8]}] Generator {tc_name} returned failure "
            f"(role={role} duration={_gen_duration_ms}ms gpu_error={_is_gpu_error}): {result.error}"
        )
        error_msg = result.error or "Generation failed"
        if _is_gpu_error:
            error_msg = result.error or "gpu_unavailable"
        error_result = {"error": error_msg}
        if emit_sse:
            sse_events.append(ToolErrorEvent(name=tc_name, error=error_msg))
        return _ToolCallOutcome(
            enriched_params=enriched_params,
            tool_result=error_result,
            sse_events=sse_events,
            msg_call={
                "role": "assistant",
                "tool_calls": [{"id": tc_id, "type": "function",
                                "function": {"name": tc_name, "arguments": json.dumps(enriched_params)}}],
            },
            msg_result={
                "role": "tool", "tool_call_id": tc_id,
                "content": json.dumps(error_result),
            },
            skipped=True,
        )

    _MIN_NOTES_THRESHOLD = 4
    if len(result.notes) < _MIN_NOTES_THRESHOLD:
        logger.warning(
            f"⚠️ [{trace.trace_id[:8]}] stori_generate_midi returned only "
            f"{len(result.notes)} note(s) (< {_MIN_NOTES_THRESHOLD}) — likely a generation failure. "
            f"Full params: role={role}, style={style}, tempo={tempo}, bars={bars}, "
            f"key={key}, start_beat={start_beat}, instrument_prompt={instrument_prompt!r}"
        )
    else:
        logger.info(
            f"✅ stori_generate_midi | role={role} notes={len(result.notes)} "
            f"cc={len(result.cc_events or [])} pb={len(result.pitch_bends or [])} "
            f"duration_ms={_gen_duration_ms} retry_count={result.metadata.get('retry_count', 0)}"
        )

    if emit_sse:
        sse_events.append(GeneratorCompleteEvent(
            role=role, agent_id=role,
            note_count=len(result.notes), duration_ms=_gen_duration_ms,
        ))

    store.add_notes(region_id, result.notes)

    if result.cc_events:
        store.add_cc(region_id, result.cc_events)
    if result.pitch_bends:
        store.add_pitch_bends(region_id, result.pitch_bends)
    if result.aftertouch:
        store.add_aftertouch(region_id, result.aftertouch)

    tool_result: dict[str, JSONValue] = {
        "regionId": region_id,
        "trackId": track_id,
        "notesAdded": len(result.notes),
        "totalNotes": len(result.notes),
        "ccEvents": len(result.cc_events),
        "pitchBends": len(result.pitch_bends),
        "backend": result.backend_used.value,
    }

    enriched_params["regionId"] = region_id
    enriched_params["trackId"] = track_id
    enriched_params["_notesGenerated"] = len(result.notes)

    if emit_sse:
        emit_params = _enrich_params_with_track_context(enriched_params, store)
        sse_events.append(ToolCallEvent(
            id=tc_id, name="stori_add_notes",
            label=_gen_label, phase=_gen_phase,
            params=wrap_dict({"trackId": track_id, "regionId": region_id, "notes": json_list(result.notes)}),
        ))
        if result.cc_events:
            extra_tool_calls.append({
                "tool": "stori_add_midi_cc",
                "params": {"regionId": region_id, "events": json_list(result.cc_events)},
            })
        if result.pitch_bends:
            extra_tool_calls.append({
                "tool": "stori_add_pitch_bend",
                "params": {"regionId": region_id, "events": json_list(result.pitch_bends)},
            })

    return _ToolCallOutcome(
        enriched_params=enriched_params,
        tool_result=tool_result,
        sse_events=sse_events,
        msg_call={
            "role": "assistant",
            "tool_calls": [{"id": tc_id, "type": "function",
                            "function": {"name": tc_name, "arguments": json.dumps(enriched_params)}}],
        },
        msg_result={
            "role": "tool", "tool_call_id": tc_id,
            "content": json.dumps(tool_result),
        },
        skipped=False,
        extra_tool_calls=extra_tool_calls,
    )


async def execute_unified_generation(
    instruments: list[str],
    style: str,
    tempo: int,
    bars: int,
    key: str | None,
    region_map: dict[str, tuple[str, str]],
    store: StateStore,
    trace: TraceContext,
    composition_context: CompositionContext | None = None,
) -> UnifiedGenerationOutput:
    """Generate all instruments together in one Orpheus call, distribute to regions.

    Args:
        instruments: All instrument roles for this section (e.g. ["drums", "bass", "keys"]).
        style: Musical style / genre.
        tempo: BPM.
        bars: Number of bars.
        key: Musical key.
        region_map: {role: (track_id, region_id)} — pre-created regions for each instrument.
        store: StateStore for persisting notes.
        trace: Trace context.
        composition_context: Emotion vector, quality preset, etc.

    Returns:
        dict with per-role results: {role: {"notes_added": int, "success": bool}}.
    """
    import time as _time
    _gen_start = _time.monotonic()

    gen_ctx: GenerationContext = {}
    if composition_context:
        gen_ctx["quality_preset"] = composition_context.get("quality_preset", "quality")
        ev = composition_context.get("emotion_vector")
        if ev is not None:
            gen_ctx["emotion_vector"] = ev
    if trace and hasattr(trace, "trace_id"):
        gen_ctx["composition_id"] = trace.trace_id

    mg = get_music_generator()
    result = await mg.generate_unified(
        instruments=instruments,
        style=style,
        tempo=tempo,
        bars=bars,
        key=key,
        context=gen_ctx,
    )

    _gen_duration_ms = int((_time.monotonic() - _gen_start) * 1000)
    per_role: dict[str, RoleResult] = {}

    if not result.success:
        logger.error(
            f"❌ Unified generation failed after {_gen_duration_ms}ms: {result.error}"
        )
        for role in instruments:
            per_role[role] = RoleResult(notes_added=0, success=False, error=result.error)
        return UnifiedGenerationOutput(per_role=per_role, _duration_ms=_gen_duration_ms)

    channel_notes = result.channel_notes or {}

    # Distribute per-channel notes to their respective regions
    for role in instruments:
        track_id, region_id = region_map.get(role, ("", ""))
        if not region_id:
            per_role[role] = RoleResult(notes_added=0, success=False, error="no region")
            continue

        role_notes = channel_notes.get(role, [])
        if not role_notes:
            role_key = role.lower()
            for ch_label, ch_notes in channel_notes.items():
                if role_key in ch_label.lower() or ch_label.lower() in role_key:
                    role_notes = ch_notes
                    break

        if not role_notes and result.notes:
            role_notes = result.notes

        store.add_notes(region_id, role_notes)
        if result.cc_events:
            store.add_cc(region_id, result.cc_events)
        if result.pitch_bends:
            store.add_pitch_bends(region_id, result.pitch_bends)

        per_role[role] = RoleResult(
            notes_added=len(role_notes),
            success=True,
            track_id=track_id,
            region_id=region_id,
        )
        logger.info(
            f"✅ Unified → {role}: {len(role_notes)} notes → region {region_id[:8]}"
        )

    _total_notes = sum(r.get("notes_added", 0) for r in per_role.values())
    logger.info(
        f"✅ Unified generation complete: {_gen_duration_ms}ms, {_total_notes} total notes"
    )
    return UnifiedGenerationOutput(
        per_role=per_role,
        _metadata=result.metadata or {},
        _duration_ms=_gen_duration_ms,
    )


async def _apply_single_tool_call(
    tc_id: str,
    tc_name: str,
    resolved_args: dict[str, JSONValue],
    allowed_tool_names: set[str] | frozenset[str],
    store: StateStore,
    trace: TraceContext,
    add_notes_failures: dict[str, int],
    emit_sse: bool = True,
    composition_context: CompositionContext | None = None,
    pre_emit_callback: (
        Callable[[list[MaestroEvent]], Awaitable[None]] | None
    ) = None,
) -> _ToolCallOutcome:
    """Validate, enrich, persist, and return results for one tool call.

    Handles entity creation (UUIDs), note persistence, generator routing
    (Orpheus), icon synthesis, and tool result building.

    **SSE contract:** This function never emits SSE events directly.
    When ``emit_sse=True`` it *builds* typed MaestroEvent instances and
    returns them in ``_ToolCallOutcome.sse_events``.  The caller decides
    whether to yield them to the client (editing path) or queue them
    (agent-team path).  When ``emit_sse=False`` the list is empty.

    Args:
        tc_id: Tool call ID (from LLM response or synthetic UUID).
        tc_name: Tool name (e.g. ``"stori_add_midi_track"``).
        resolved_args: Params after ``$N.field`` variable ref resolution.
        allowed_tool_names: Tool allowlist for validation.
        store: StateStore for entity creation and result building.
        trace: Trace context for logging and spans.
        add_notes_failures: Mutable circuit-breaker counter (modified in-place).
        emit_sse: When ``False``, sse_events is empty (variation/proposal mode).
        composition_context: Optional dict with style, tempo, bars, key,
            emotion_vector, quality_preset for generator tool routing.
    """
    sse_events: list[MaestroEvent] = []
    msg_call: AssistantMessage = {"role": "assistant"}
    msg_result: ToolResultMessage = {"role": "tool", "tool_call_id": "", "content": ""}

    # ── Circuit breaker: stori_add_notes infinite-retry guard ──
    if tc_name == "stori_add_notes":
        _cb_raw = resolved_args.get("regionId", "__unknown__")
        cb_region_id = _cb_raw if isinstance(_cb_raw, str) else "__unknown__"
        cb_failures = add_notes_failures.get(cb_region_id, 0)
        if cb_failures >= 3:
            cb_error = (
                f"stori_add_notes: regionId '{cb_region_id}' has failed {cb_failures} times "
                f"without valid notes being added. Stop retrying with shorthand params. "
                f"Provide a real 'notes' array: "
                f"[{{\"pitch\": 60, \"startBeat\": 0, \"durationBeats\": 1, \"velocity\": 80}}, ...]"
            )
            logger.error(f"[{trace.trace_id[:8]}] Circuit breaker: {cb_error}")
            if emit_sse:
                sse_events.append(ToolErrorEvent(name=tc_name, error=cb_error))
            msg_call = AssistantMessage(
                role="assistant",
                tool_calls=[{"id": tc_id, "type": "function",
                             "function": {"name": tc_name, "arguments": json.dumps(resolved_args)}}],
            )
            msg_result = ToolResultMessage(
                role="tool", tool_call_id=tc_id,
                content=json.dumps({"error": cb_error}),
            )
            return _ToolCallOutcome(
                enriched_params=resolved_args,
                tool_result={"error": cb_error},
                sse_events=sse_events,
                msg_call=msg_call,
                msg_result=msg_result,
                skipped=True,
            )

    # ── Validation ──
    with trace_span(trace, f"validate:{tc_name}"):
        validation = validate_tool_call(tc_name, resolved_args, allowed_tool_names, store.registry)

    if not validation.valid:
        log_validation_error(trace.trace_id, tc_name, [str(e) for e in validation.errors])
        if tc_name == "stori_add_notes":
            _fail_raw = resolved_args.get("regionId", "__unknown__")
            cb_region_id = _fail_raw if isinstance(_fail_raw, str) else "__unknown__"
            add_notes_failures[cb_region_id] = add_notes_failures.get(cb_region_id, 0) + 1
        if emit_sse:
            sse_events.append(ToolErrorEvent(
                name=tc_name,
                error=validation.error_message,
                errors=[str(e) for e in validation.errors],
            ))
        error_result: dict[str, JSONValue] = {"error": validation.error_message}
        msg_call = {
            "role": "assistant",
            "tool_calls": [{"id": tc_id, "type": "function",
                            "function": {"name": tc_name, "arguments": json.dumps(resolved_args)}}],
        }
        msg_result = {
            "role": "tool", "tool_call_id": tc_id,
            "content": json.dumps(error_result),
        }
        return _ToolCallOutcome(
            enriched_params=resolved_args,
            tool_result=error_result,
            sse_events=sse_events,
            msg_call=msg_call,
            msg_result=msg_result,
            skipped=True,
        )

    enriched_params = validation.resolved_params
    _icon_from_llm = False

    # ── Entity creation ──
    if tc_name == "stori_add_midi_track":
        track_name = _sp(enriched_params, "name", "Track")
        instrument = _sp_opt(enriched_params, "instrument")
        gm_program = enriched_params.get("gmProgram")
        drum_kit_id = enriched_params.get("drumKitId")
        track_id = store.create_track(track_name)
        enriched_params["trackId"] = track_id
        if gm_program is None:
            from maestro.core.gm_instruments import infer_gm_program_with_context
            inference = infer_gm_program_with_context(track_name=track_name, instrument=instrument)
            enriched_params["_gmInstrumentName"] = inference.instrument_name
            enriched_params["_isDrums"] = inference.is_drums
            if inference.needs_program_change:
                enriched_params["gmProgram"] = inference.program

        if drum_kit_id and not enriched_params.get("_isDrums"):
            enriched_params["_isDrums"] = True

        from maestro.core.track_styling import (
            normalize_color, color_for_role, is_valid_icon, infer_track_icon,
        )
        raw_color = _sp_opt(enriched_params, "color")
        valid_color = normalize_color(raw_color)
        if valid_color:
            enriched_params["color"] = valid_color
        else:
            track_count = len(store.registry.list_tracks())
            enriched_params["color"] = color_for_role(track_name, track_count)

        raw_icon = _sp_opt(enriched_params, "icon")
        _icon_from_llm = is_valid_icon(raw_icon)
        if not _icon_from_llm:
            enriched_params["icon"] = infer_track_icon(track_name)

        # FE strict contract: exactly one of _isDrums or gmProgram must be set.
        is_drums = enriched_params.get("_isDrums", False)
        has_gm = enriched_params.get("gmProgram") is not None
        if is_drums and has_gm:
            enriched_params.pop("gmProgram", None)
        elif not is_drums and not has_gm:
            enriched_params["gmProgram"] = 0

    elif tc_name == "stori_add_midi_region":
        midi_region_track_id: str | None = _sp_opt(enriched_params, "trackId")
        region_name: str = _sp(enriched_params, "name", "Region")
        if midi_region_track_id:
            _req_start = _fp(enriched_params, "startBeat", 0.0)
            _req_dur = _fp(enriched_params, "durationBeats", 16.0)

            _existing_rid = store.registry.find_overlapping_region(
                midi_region_track_id, _req_start, _req_dur,
            )
            if _existing_rid:
                enriched_params["regionId"] = _existing_rid
                logger.info(
                    f"📍 Idempotent region hit: beat {_req_start}-{_req_start + _req_dur} "
                    f"on track {midi_region_track_id[:8]} → returning {_existing_rid[:8]}"
                )
                _existing_entity = store.registry.get_region(_existing_rid)
                _existing_name = _existing_entity.name if _existing_entity else region_name
                idempotent_result: dict[str, JSONValue] = {
                    "success": True,
                    "regionId": _existing_rid,
                    "existingRegionId": _existing_rid,
                    "skipped": True,
                    "startBeat": _req_start,
                    "durationBeats": _req_dur,
                    "name": _existing_name,
                }
                msg_call = {
                    "role": "assistant",
                    "tool_calls": [{"id": tc_id, "type": "function",
                                    "function": {"name": tc_name, "arguments": json.dumps(enriched_params)}}],
                }
                msg_result = {
                    "role": "tool", "tool_call_id": tc_id,
                    "content": json.dumps(idempotent_result),
                }
                return _ToolCallOutcome(
                    enriched_params=enriched_params,
                    tool_result=idempotent_result,
                    sse_events=[],
                    msg_call=msg_call,
                    msg_result=msg_result,
                    skipped=False,
                )

            try:
                region_id = store.create_region(
                    region_name, midi_region_track_id,
                    metadata={
                        "startBeat": _req_start,
                        "durationBeats": _req_dur,
                    }
                )
                enriched_params["regionId"] = region_id
            except ValueError as e:
                logger.error(f"❌ Failed to create region: {e}")
                region_err: dict[str, JSONValue] = {
                    "success": False,
                    "error": f"Failed to create region: {e}",
                }
                msg_call = {
                    "role": "assistant",
                    "tool_calls": [{"id": tc_id, "type": "function",
                                    "function": {"name": tc_name, "arguments": json.dumps(enriched_params)}}],
                }
                msg_result = {
                    "role": "tool", "tool_call_id": tc_id,
                    "content": json.dumps(region_err),
                }
                return _ToolCallOutcome(
                    enriched_params=enriched_params,
                    tool_result=region_err,
                    sse_events=sse_events,
                    msg_call=msg_call,
                    msg_result=msg_result,
                    skipped=True,
                )
        else:
            logger.error(
                f"stori_add_midi_region called without trackId for region '{region_name}'"
            )
            no_track_err: dict[str, JSONValue] = {
                "success": False,
                "error": (
                    f"Cannot create region '{region_name}' — no trackId provided. "
                    "Use $N.trackId to reference a track created in a prior tool call, "
                    "or use trackName for name-based resolution."
                ),
            }
            msg_call = {
                "role": "assistant",
                "tool_calls": [{"id": tc_id, "type": "function",
                                "function": {"name": tc_name, "arguments": json.dumps(enriched_params)}}],
            }
            msg_result = {
                "role": "tool", "tool_call_id": tc_id,
                "content": json.dumps(no_track_err),
            }
            return _ToolCallOutcome(
                enriched_params=enriched_params,
                tool_result=no_track_err,
                sse_events=sse_events,
                msg_call=msg_call,
                msg_result=msg_result,
                skipped=True,
            )

    elif tc_name == "stori_duplicate_region":
        source_region_id: str = _sp(enriched_params, "regionId")
        source_entity = store.registry.get_region(source_region_id)
        if source_entity:
            copy_name = f"{source_entity.name} (copy)"
            parent_track_id = source_entity.parent_id or ""
            try:
                new_region_id = store.create_region(
                    copy_name, parent_track_id,
                    metadata={"startBeat": _fp(enriched_params, "startBeat")},
                )
                enriched_params["newRegionId"] = new_region_id
            except ValueError as e:
                logger.error(f"Failed to register duplicate region: {e}")

    elif tc_name == "stori_ensure_bus":
        bus_name = _sp(enriched_params, "name", "Bus")
        bus_id = store.get_or_create_bus(bus_name)
        enriched_params["busId"] = bus_id

    # ── Generator routing (Orpheus) ──
    if tc_name in _GENERATOR_TOOL_NAMES and composition_context:
        gen_outcome = await _execute_agent_generator(
            tc_id, tc_name, enriched_params, store, trace,
            composition_context, emit_sse,
            pre_emit_callback=pre_emit_callback,
        )
        if gen_outcome is not None:
            return gen_outcome

    # ── SSE events (toolStart + toolCall) ──
    extra_tool_calls: list[ToolCallDict] = []
    _tc_phase = phase_for_tool(tc_name)
    if emit_sse:
        emit_params = _enrich_params_with_track_context(enriched_params, store)
        _tc_label = _human_label_for_tool(tc_name, emit_params)
        sse_events.append(ToolStartEvent(
            name=tc_name, label=_tc_label, phase=_tc_phase,
        ))
        sse_events.append(ToolCallEvent(
            id=tc_id, name=tc_name, label=_tc_label,
            phase=_tc_phase, params=wrap_dict(emit_params),
        ))

    log_tool_call(trace.trace_id, tc_name, enriched_params, True)

    # ── Refine icon via GM/drum inference and emit synthetic stori_set_track_icon ──
    if tc_name == "stori_add_midi_track" and emit_sse:
        _icon_track_id = _sp(enriched_params, "trackId")
        _drum_kit = enriched_params.get("drumKitId")
        _is_drums = enriched_params.get("_isDrums", False)
        _gm_program = enriched_params.get("gmProgram")
        if _drum_kit or _is_drums:
            _track_icon: str | None = DRUM_ICON
        elif _icon_from_llm:
            _track_icon = _sp_opt(enriched_params, "icon")
        elif _gm_program is not None:
            _track_icon = icon_for_gm_program(int(_gm_program) if isinstance(_gm_program, (int, float)) else 0)
        else:
            _track_icon = _sp_opt(enriched_params, "icon")
        if _track_icon and _track_icon not in VALID_SF_SYMBOL_ICONS:
            _track_icon = _sp_opt(enriched_params, "icon")
        if _track_icon:
            enriched_params["icon"] = _track_icon
        if _track_icon and _icon_track_id:
            _icon_params: dict[str, JSONValue] = {"trackId": _icon_track_id, "icon": _track_icon}
            _icon_label = f"Setting icon for {_sp(enriched_params, 'name', 'track')}"
            _icon_phase = phase_for_tool("stori_set_track_icon")
            sse_events.append(ToolStartEvent(
                name="stori_set_track_icon", label=_icon_label, phase=_icon_phase,
            ))
            sse_events.append(ToolCallEvent(
                id=f"{tc_id}-icon", name="stori_set_track_icon",
                label=_icon_label, phase=_icon_phase, params=wrap_dict(_icon_params),
            ))
            extra_tool_calls.append({"tool": "stori_set_track_icon", "params": _icon_params})

    # ── FE strict contract: backfill missing required fields ──
    if tc_name == "stori_add_notes":
        for note in _list_dicts(enriched_params, "notes"):
            note.setdefault("pitch", 60)
            note.setdefault("velocity", 100)
            note.setdefault("startBeat", 0)
            note.setdefault("durationBeats", 1.0)
    elif tc_name == "stori_add_automation":
        for pt in _list_dicts(enriched_params, "points"):
            pt.setdefault("beat", 0)
            pt.setdefault("value", 0.5)
    elif tc_name == "stori_add_midi_cc":
        for ev in _list_dicts(enriched_params, "events"):
            ev.setdefault("beat", 0)
            ev.setdefault("value", 0)
    elif tc_name == "stori_add_pitch_bend":
        for ev in _list_dicts(enriched_params, "events"):
            ev.setdefault("beat", 0)
            ev.setdefault("value", 0)

    # ── Note persistence (with post-processing when context is available) ──
    if tc_name == "stori_add_notes":
        _notes_raw = enriched_params.get("notes", [])
        _notes: list[NoteDict] = (
            [n for n in _notes_raw if is_note_dict(n)]
            if isinstance(_notes_raw, list) else []
        )
        _rid = _sp(enriched_params, "regionId")
        if _rid and _notes:
            if composition_context:
                from maestro.services.expressiveness import apply_expressiveness
                _role = str(composition_context.get("role") or "melody")
                _style = str(composition_context.get("style") or "")
                _bars_raw = composition_context.get("bars")
                _bars = int(_bars_raw) if isinstance(_bars_raw, int) else 4
                if _style:
                    expr = apply_expressiveness(
                        _notes, _style, _bars, instrument_role=_role,
                    )
                    _notes = expr["notes"]
                    enriched_params["notes"] = json_list(_notes)
                    if expr.get("cc_events"):
                        store.add_cc(_rid, expr["cc_events"])
                    if expr.get("pitch_bends"):
                        store.add_pitch_bends(_rid, expr["pitch_bends"])
            store.add_notes(_rid, _notes)
        add_notes_failures.pop(_sp(enriched_params, "regionId", "__unknown__"), None)

    # ── Effect persistence ──
    if tc_name == "stori_add_insert_effect":
        _fx_track = _sp(enriched_params, "trackId")
        _fx_type = _sp(enriched_params, "type")
        if _fx_track and _fx_type:
            try:
                store.add_effect(_fx_track, _fx_type)
            except Exception as _fx_exc:
                logger.error(f"Effect persistence failed: {_fx_exc}")

    # ── Message objects for LLM conversation history ──
    if tc_name == "stori_add_notes":
        _notes_raw2 = enriched_params.get("notes", [])
        notes: list[NoteDict] = (
            [n for n in _notes_raw2 if is_note_dict(n)]
            if isinstance(_notes_raw2, list) else []
        )
        summary_params = {k: v for k, v in enriched_params.items() if k != "notes"}
        summary_params["_noteCount"] = len(notes)
        if notes:
            starts = [n.get("startBeat", n.get("start_beat", 0)) for n in notes]
            summary_params["_beatRange"] = [min(starts), max(starts)]
        msg_arguments = json.dumps(summary_params)
    else:
        msg_arguments = json.dumps(enriched_params)

    msg_call = {
        "role": "assistant",
        "tool_calls": [{"id": tc_id, "type": "function",
                        "function": {"name": tc_name, "arguments": msg_arguments}}],
    }

    # ── Tool result ──
    tool_result: dict[str, JSONValue] = _build_tool_result(tc_name, enriched_params, store)
    msg_result = {
        "role": "tool", "tool_call_id": tc_id,
        "content": json.dumps(tool_result),
    }

    return _ToolCallOutcome(
        enriched_params=enriched_params,
        tool_result=tool_result,
        sse_events=sse_events,
        msg_call=msg_call,
        msg_result=msg_result,
        skipped=False,
        extra_tool_calls=extra_tool_calls,
    )
