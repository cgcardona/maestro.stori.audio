"""Core tool execution — validate, enrich, persist, and emit for one tool call."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.core.gm_instruments import DRUM_ICON, icon_for_gm_program
from app.core.sse_utils import sse_event
from app.core.tool_validation import VALID_SF_SYMBOL_ICONS, validate_tool_call
from app.core.tracing import log_tool_call, log_validation_error, trace_span
from app.core.maestro_helpers import (
    _build_tool_result,
    _enrich_params_with_track_context,
    _human_label_for_tool,
)
from app.core.maestro_plan_tracker import _ToolCallOutcome, _GENERATOR_TOOL_NAMES

logger = logging.getLogger(__name__)


async def _execute_agent_generator(
    tc_id: str,
    tc_name: str,
    enriched_params: dict[str, Any],
    store: Any,
    trace: Any,
    composition_context: dict[str, Any],
    emit_sse: bool,
) -> Optional[_ToolCallOutcome]:
    """Route a generator tool call through MusicGenerator (Orpheus).

    Called from ``_apply_single_tool_call`` when the tool is a generator
    (``stori_generate_midi``, ``stori_generate_drums``, etc.) and
    ``composition_context`` is available.

    Returns a complete ``_ToolCallOutcome`` on success, or ``None`` to
    fall through to normal tool handling.
    """
    from app.services.music_generator import get_music_generator

    sse_events: list[dict[str, Any]] = []
    extra_tool_calls: list[dict[str, Any]] = []

    role = enriched_params.get("role", "")
    if not role:
        name_to_role = {
            "stori_generate_drums": "drums",
            "stori_generate_bass": "bass",
            "stori_generate_melody": "melody",
            "stori_generate_chords": "chords",
        }
        role = name_to_role.get(tc_name, "melody")

    style = enriched_params.get("style") or composition_context.get("style", "")
    tempo = int(enriched_params.get("tempo") or composition_context.get("tempo", 120))
    bars = int(enriched_params.get("bars") or composition_context.get("bars", 4))
    key = enriched_params.get("key") or composition_context.get("key")

    track_id = enriched_params.get("trackId", "")
    if not track_id:
        track_name = enriched_params.get("trackName", role.capitalize())
        track_id = store.registry.resolve_track(track_name) or ""

    region_id = ""
    if track_id:
        region_id = store.registry.get_latest_region_for_track(track_id) or ""

    if not region_id:
        error_msg = (
            f"Generator {tc_name}: no region found for track '{track_id}' "
            f"(role='{role}'). Ensure stori_add_midi_region is called before "
            f"the generator tool."
        )
        logger.error(f"[{trace.trace_id[:8]}] {error_msg}")
        error_result: dict[str, Any] = {"error": error_msg}
        if emit_sse:
            sse_events.append({"type": "toolError", "name": tc_name, "error": error_msg})
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

    if emit_sse:
        sse_events.append({
            "type": "toolStart",
            "name": tc_name,
            "label": f"Generating {role} via Orpheus",
        })

    gen_kwargs: dict[str, Any] = {
        "quality_preset": composition_context.get("quality_preset", "quality"),
    }
    emotion_vector = composition_context.get("emotion_vector")
    if emotion_vector is not None:
        gen_kwargs["emotion_vector"] = emotion_vector

    try:
        mg = get_music_generator()
        result = await mg.generate(
            instrument=role,
            style=style,
            tempo=tempo,
            bars=bars,
            key=key,
            **gen_kwargs,
        )
    except Exception as exc:
        logger.error(f"[{trace.trace_id[:8]}] Generator {tc_name} failed: {exc}")
        error_result: dict[str, Any] = {"error": str(exc)}
        if emit_sse:
            sse_events.append({"type": "toolError", "name": tc_name, "error": str(exc)})
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

    if not result.success:
        logger.warning(
            f"[{trace.trace_id[:8]}] Generator {tc_name} returned failure: {result.error}"
        )
        error_result = {"error": result.error or "Generation failed"}
        if emit_sse:
            sse_events.append({"type": "toolError", "name": tc_name, "error": result.error or ""})
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

    store.add_notes(region_id, result.notes)

    if result.cc_events:
        store.add_cc(region_id, result.cc_events)
    if result.pitch_bends:
        store.add_pitch_bends(region_id, result.pitch_bends)
    if result.aftertouch:
        store.add_aftertouch(region_id, result.aftertouch)

    logger.info(
        f"[{trace.trace_id[:8]}] Generator {tc_name} ({role}): "
        f"{len(result.notes)} notes, {len(result.cc_events)} CC, "
        f"{len(result.pitch_bends)} PB via {result.backend_used.value}"
    )

    tool_result: dict[str, Any] = {
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
        sse_events.append({
            "type": "toolCall",
            "id": tc_id,
            "name": "stori_add_notes",
            "params": {
                "trackId": track_id,
                "regionId": region_id,
                "notes": result.notes,
            },
        })
        if result.cc_events:
            extra_tool_calls.append({
                "tool": "stori_add_midi_cc",
                "params": {"regionId": region_id, "events": result.cc_events},
            })
        if result.pitch_bends:
            extra_tool_calls.append({
                "tool": "stori_add_pitch_bend",
                "params": {"regionId": region_id, "events": result.pitch_bends},
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


async def _apply_single_tool_call(
    tc_id: str,
    tc_name: str,
    resolved_args: dict[str, Any],
    allowed_tool_names: set[str],
    store: Any,
    trace: Any,
    add_notes_failures: dict[str, int],
    emit_sse: bool = True,
    composition_context: Optional[dict[str, Any]] = None,
) -> _ToolCallOutcome:
    """Validate, enrich, persist, and return results for one tool call.

    Handles entity creation (UUIDs), note persistence, generator routing
    (Orpheus), icon synthesis, and tool result building. Returns SSE events,
    LLM message objects, and enriched params without yielding — the caller
    decides whether to yield events directly (editing path) or put them
    into an asyncio.Queue (agent-team path).

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
    sse_events: list[dict[str, Any]] = []

    # ── Circuit breaker: stori_add_notes infinite-retry guard ──
    if tc_name == "stori_add_notes":
        cb_region_id = resolved_args.get("regionId", "__unknown__")
        cb_failures = add_notes_failures.get(cb_region_id, 0)
        if cb_failures >= 3:
            cb_error = (
                f"stori_add_notes: regionId '{cb_region_id}' has failed {cb_failures} times "
                f"without valid notes being added. Stop retrying with shorthand params. "
                f"Provide a real 'notes' array: "
                f"[{{\"pitch\": 60, \"startBeat\": 0, \"durationBeats\": 1, \"velocity\": 80}}, ...]"
            )
            logger.error(f"[{trace.trace_id[:8]}] 🔴 Circuit breaker: {cb_error}")
            if emit_sse:
                sse_events.append({"type": "toolError", "name": tc_name, "error": cb_error})
            msg_call: dict[str, Any] = {
                "role": "assistant",
                "tool_calls": [{"id": tc_id, "type": "function",
                                "function": {"name": tc_name, "arguments": json.dumps(resolved_args)}}],
            }
            msg_result: dict[str, Any] = {
                "role": "tool", "tool_call_id": tc_id,
                "content": json.dumps({"error": cb_error}),
            }
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
            cb_region_id = resolved_args.get("regionId", "__unknown__")
            add_notes_failures[cb_region_id] = add_notes_failures.get(cb_region_id, 0) + 1
        if emit_sse:
            sse_events.append({
                "type": "toolError",
                "name": tc_name,
                "error": validation.error_message,
                "errors": [str(e) for e in validation.errors],
            })
        error_result = {"error": validation.error_message}
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
        track_name = enriched_params.get("name", "Track")
        instrument = enriched_params.get("instrument")
        gm_program = enriched_params.get("gmProgram")
        drum_kit_id = enriched_params.get("drumKitId")
        if "trackId" in enriched_params:
            logger.warning(
                f"⚠️ LLM provided trackId '{enriched_params['trackId']}' for NEW track '{track_name}'. "
                f"Ignoring and generating fresh UUID to prevent duplicates."
            )
        track_id = store.create_track(track_name)
        enriched_params["trackId"] = track_id
        logger.debug(f"🔑 Generated trackId: {track_id[:8]} for '{track_name}'")
        if gm_program is None:
            from app.core.gm_instruments import infer_gm_program_with_context
            inference = infer_gm_program_with_context(track_name=track_name, instrument=instrument)
            enriched_params["_gmInstrumentName"] = inference.instrument_name
            enriched_params["_isDrums"] = inference.is_drums
            logger.info(
                f"🎵 GM inference for '{track_name}': "
                f"program={inference.program}, instrument={inference.instrument_name}, "
                f"is_drums={inference.is_drums}"
            )
            if inference.needs_program_change:
                enriched_params["gmProgram"] = inference.program

        if drum_kit_id and not enriched_params.get("_isDrums"):
            enriched_params["_isDrums"] = True
            logger.info(
                f"🥁 drumKitId='{drum_kit_id}' present — forcing _isDrums=True "
                f"for track '{track_name}'"
            )

        from app.core.track_styling import (
            normalize_color, color_for_role, is_valid_icon, infer_track_icon,
        )
        raw_color = enriched_params.get("color")
        valid_color = normalize_color(raw_color)
        if valid_color:
            enriched_params["color"] = valid_color
        else:
            track_count = len(store.registry.list_tracks())
            enriched_params["color"] = color_for_role(track_name, track_count)
            if raw_color:
                logger.debug(
                    f"🎨 Unrecognised color '{raw_color}' for '{track_name}' "
                    f"→ auto-assigned '{enriched_params['color']}'"
                )

        raw_icon = enriched_params.get("icon")
        _icon_from_llm = is_valid_icon(raw_icon)
        if not _icon_from_llm:
            enriched_params["icon"] = infer_track_icon(track_name)
            if raw_icon:
                logger.debug(
                    f"🏷️ Invalid icon '{raw_icon}' for '{track_name}' "
                    f"→ auto-assigned '{enriched_params['icon']}'"
                )

        # FE strict contract: exactly one of _isDrums or gmProgram must be set.
        is_drums = enriched_params.get("_isDrums", False)
        has_gm = enriched_params.get("gmProgram") is not None
        if is_drums and has_gm:
            enriched_params.pop("gmProgram", None)
            logger.debug(
                f"🔧 _isDrums=True — removed gmProgram for '{track_name}'"
            )
        elif not is_drums and not has_gm:
            enriched_params["gmProgram"] = 0
            logger.debug(
                f"🔧 Neither _isDrums nor gmProgram set for '{track_name}' "
                f"— defaulting gmProgram=0 (Acoustic Grand Piano)"
            )

    elif tc_name == "stori_add_midi_region":
        midi_region_track_id: Optional[str] = enriched_params.get("trackId")
        region_name: str = str(enriched_params.get("name", "Region"))
        if "regionId" in enriched_params:
            logger.warning(
                f"⚠️ LLM provided regionId '{enriched_params['regionId']}' for NEW region '{region_name}'. "
                f"Ignoring and generating fresh UUID to prevent duplicates."
            )
        if midi_region_track_id:
            try:
                region_id = store.create_region(
                    region_name, midi_region_track_id,
                    metadata={
                        "startBeat": enriched_params.get("startBeat", 0),
                        "durationBeats": enriched_params.get("durationBeats", 16),
                    }
                )
                enriched_params["regionId"] = region_id
                logger.debug(f"🔑 Generated regionId: {region_id[:8]} for '{region_name}'")
            except ValueError as e:
                logger.error(f"Failed to create region: {e}")
                error_result = {"success": False, "error": f"Failed to create region: {e}"}
                msg_call = {
                    "role": "assistant",
                    "tool_calls": [{"id": tc_id, "type": "function",
                                    "function": {"name": tc_name, "arguments": json.dumps(enriched_params)}}],
                }
                msg_result = {
                    "role": "tool", "tool_call_id": tc_id,
                    "content": json.dumps(error_result),
                }
                return _ToolCallOutcome(
                    enriched_params=enriched_params,
                    tool_result=error_result,
                    sse_events=sse_events,
                    msg_call=msg_call,
                    msg_result=msg_result,
                    skipped=True,
                )
        else:
            logger.error(
                f"⚠️ stori_add_midi_region called without trackId for region '{region_name}'"
            )
            error_result = {
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
                "content": json.dumps(error_result),
            }
            return _ToolCallOutcome(
                enriched_params=enriched_params,
                tool_result=error_result,
                sse_events=sse_events,
                msg_call=msg_call,
                msg_result=msg_result,
                skipped=True,
            )

    elif tc_name == "stori_duplicate_region":
        source_region_id: str = enriched_params.get("regionId", "")
        source_entity = store.registry.get_region(source_region_id)
        if source_entity:
            copy_name = f"{source_entity.name} (copy)"
            parent_track_id = source_entity.parent_id or ""
            try:
                new_region_id = store.create_region(
                    copy_name, parent_track_id,
                    metadata={"startBeat": enriched_params.get("startBeat", 0)},
                )
                enriched_params["newRegionId"] = new_region_id
                logger.debug(
                    f"🔑 Generated newRegionId: {new_region_id[:8]} "
                    f"for duplicate of '{source_entity.name}'"
                )
            except ValueError as e:
                logger.error(f"Failed to register duplicate region: {e}")

    elif tc_name == "stori_ensure_bus":
        bus_name = enriched_params.get("name", "Bus")
        if "busId" in enriched_params:
            logger.warning(
                f"⚠️ LLM provided busId '{enriched_params['busId']}' for bus '{bus_name}'. "
                f"Ignoring to prevent duplicates."
            )
        bus_id = store.get_or_create_bus(bus_name)
        enriched_params["busId"] = bus_id

    # ── Generator routing (Orpheus) ──
    if tc_name in _GENERATOR_TOOL_NAMES and composition_context:
        gen_outcome = await _execute_agent_generator(
            tc_id, tc_name, enriched_params, store, trace,
            composition_context, emit_sse,
        )
        if gen_outcome is not None:
            return gen_outcome

    # ── SSE events (toolStart + toolCall) ──
    extra_tool_calls: list[dict[str, Any]] = []
    if emit_sse:
        emit_params = _enrich_params_with_track_context(enriched_params, store)
        sse_events.append({
            "type": "toolStart",
            "name": tc_name,
            "label": _human_label_for_tool(tc_name, emit_params),
        })
        sse_events.append({
            "type": "toolCall",
            "id": tc_id,
            "name": tc_name,
            "params": emit_params,
        })

    log_tool_call(trace.trace_id, tc_name, enriched_params, True)

    # ── Refine icon via GM/drum inference and emit synthetic stori_set_track_icon ──
    if tc_name == "stori_add_midi_track" and emit_sse:
        _icon_track_id = enriched_params.get("trackId", "")
        _drum_kit = enriched_params.get("drumKitId")
        _is_drums = enriched_params.get("_isDrums", False)
        _gm_program = enriched_params.get("gmProgram")
        if _drum_kit or _is_drums:
            _track_icon: Optional[str] = DRUM_ICON
        elif _icon_from_llm:
            _track_icon = enriched_params.get("icon")
        elif _gm_program is not None:
            _track_icon = icon_for_gm_program(int(_gm_program))
        else:
            _track_icon = enriched_params.get("icon")
        if _track_icon and _track_icon not in VALID_SF_SYMBOL_ICONS:
            _track_icon = enriched_params.get("icon")
        if _track_icon:
            enriched_params["icon"] = _track_icon
        if _track_icon and _icon_track_id:
            _icon_params: dict[str, Any] = {"trackId": _icon_track_id, "icon": _track_icon}
            sse_events.append({
                "type": "toolStart",
                "name": "stori_set_track_icon",
                "label": f"Setting icon for {enriched_params.get('name', 'track')}",
            })
            sse_events.append({
                "type": "toolCall",
                "id": f"{tc_id}-icon",
                "name": "stori_set_track_icon",
                "params": _icon_params,
            })
            extra_tool_calls.append({"tool": "stori_set_track_icon", "params": _icon_params})
            logger.debug(
                f"🎨 Synthetic icon '{_track_icon}' → trackId {_icon_track_id[:8]} "
                f"({'drum kit' if (_drum_kit or _is_drums) else f'GM {_gm_program}'})"
            )

    # ── FE strict contract: backfill missing required fields ──
    if tc_name == "stori_add_notes":
        for note in enriched_params.get("notes", []):
            note.setdefault("pitch", 60)
            note.setdefault("velocity", 100)
            note.setdefault("startBeat", 0)
            note.setdefault("durationBeats", 1.0)
    elif tc_name == "stori_add_automation":
        for pt in enriched_params.get("points", []):
            pt.setdefault("beat", 0)
            pt.setdefault("value", 0.5)
    elif tc_name == "stori_add_midi_cc":
        for ev in enriched_params.get("events", []):
            ev.setdefault("beat", 0)
            ev.setdefault("value", 0)
    elif tc_name == "stori_add_pitch_bend":
        for ev in enriched_params.get("events", []):
            ev.setdefault("beat", 0)
            ev.setdefault("value", 0)

    # ── Note persistence (with post-processing when context is available) ──
    if tc_name == "stori_add_notes":
        _notes = enriched_params.get("notes", [])
        _rid = enriched_params.get("regionId", "")
        if _rid and _notes:
            if composition_context:
                from app.services.expressiveness import apply_expressiveness
                _role = composition_context.get("role", "melody")
                _style = composition_context.get("style", "")
                _bars = composition_context.get("bars", 4)
                if _style:
                    expr = apply_expressiveness(
                        _notes, _style, _bars, instrument_role=_role,
                    )
                    _notes = expr["notes"]
                    enriched_params["notes"] = _notes
                    if expr.get("cc_events"):
                        store.add_cc(_rid, expr["cc_events"])
                    if expr.get("pitch_bends"):
                        store.add_pitch_bends(_rid, expr["pitch_bends"])
                    logger.debug(
                        f"🎭 Post-processed {len(_notes)} notes for region "
                        f"{_rid[:8]} ({_role}/{_style}): "
                        f"+{len(expr.get('cc_events', []))} CC, "
                        f"+{len(expr.get('pitch_bends', []))} PB"
                    )
            store.add_notes(_rid, _notes)
            logger.debug(
                f"📝 Persisted {len(_notes)} notes for region {_rid[:8]} in StateStore"
            )
        add_notes_failures.pop(enriched_params.get("regionId", "__unknown__"), None)

    # ── Message objects for LLM conversation history ──
    if tc_name == "stori_add_notes":
        notes = enriched_params.get("notes", [])
        summary_params = {k: v for k, v in enriched_params.items() if k != "notes"}
        summary_params["_noteCount"] = len(notes)
        if notes:
            starts = [n["startBeat"] for n in notes]
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
    tool_result = _build_tool_result(tc_name, enriched_params, store)
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
