"""Core tool execution: _execute_single_call and _execute_generator."""

from __future__ import annotations

import logging

from app.core.expansion import ToolCall
from app.core.tools import get_tool_meta, ToolTier, ToolKind
from app.core.tracing import trace_span
from app.core.executor.models import ExecutionContext
from app.contracts.generation_types import GenerationContext
from app.services.music_generator import get_music_generator

logger = logging.getLogger(__name__)


def _sp(v: object, default: str = "") -> str:
    """Narrow an object param value to str."""
    return v if isinstance(v, str) else default


async def _execute_generator(
    name: str,
    params: dict[str, object],
    ctx: ExecutionContext,
) -> None:
    """Execute a generator tool server-side."""
    mg = get_music_generator()

    _tempo = params.get("tempo", 120)
    _bars = params.get("bars", 4)
    instrument = _sp(params.get("role"), "drums")
    style = _sp(params.get("style"))
    tempo = int(_tempo) if isinstance(_tempo, (int, float)) else 120
    bars = int(_bars) if isinstance(_bars, (int, float)) else 4
    _key = params.get("key")
    key = _key if isinstance(_key, str) else None
    _chords = params.get("chords")
    chords = _chords if isinstance(_chords, list) else None

    logger.info(f"🎵 Generating MIDI: {instrument} - {style}")

    try:
        result = await mg.generate(
            instrument=instrument,
            style=style,
            tempo=tempo,
            bars=bars,
            key=key,
            chords=chords,
            context=GenerationContext(quality_preset="quality"),
        )

        if not result.success:
            logger.error(f"❌ Generation failed: {result.error}")
            ctx.add_result(name, False, {}, result.error)
            return

        logger.info(f"✅ Generated {len(result.notes)} notes via {result.backend_used.value}")

        _track_name = params.get("trackName")
        track_name = _track_name if isinstance(_track_name, str) else instrument.capitalize()
        _tid = params.get("trackId")
        track_id = ctx.store.registry.resolve_track(track_name) or (_tid if isinstance(_tid, str) else None)

        if not track_id:
            ctx.add_result(name, False, {}, f"Track '{track_name}' not found")
            return

        region_id = ctx.store.registry.get_latest_region_for_track(track_id)

        if not region_id:
            ctx.add_result(name, False, {}, "No region found for notes")
            return

        ctx.store.add_notes(region_id, result.notes, transaction=ctx.transaction)

        if result.cc_events:
            ctx.store.add_cc(region_id, result.cc_events)
        if result.pitch_bends:
            ctx.store.add_pitch_bends(region_id, result.pitch_bends)
        if result.aftertouch:
            ctx.store.add_aftertouch(region_id, result.aftertouch)

        ctx.add_event({
            "tool": "stori_add_notes",
            "params": {
                "notes": result.notes,
                "trackId": track_id,
                "regionId": region_id,
            },
        })

        ctx.add_result(name, True, {
            "notes_count": len(result.notes),
            "cc_count": len(result.cc_events),
            "pitch_bend_count": len(result.pitch_bends),
            "aftertouch_count": len(result.aftertouch),
            "backend": result.backend_used.value,
        })

    except Exception as e:
        logger.exception(f"❌ Generator error: {e}")
        ctx.add_result(name, False, {}, str(e))


async def _execute_single_call(call: ToolCall, ctx: ExecutionContext) -> None:
    """
    Execute a single tool call with entity management.

    Handles entity creation (tracks, regions, buses) via StateStore,
    name → ID resolution, generator execution, and event emission.
    """
    meta = get_tool_meta(call.name)
    params = call.params.copy()

    with trace_span(ctx.trace, f"tool:{call.name}", {"params_keys": list(params.keys())}):

        # --- Step 1: Resolve Name References ---

        if "trackName" in params and "trackId" not in params:
            track_name = _sp(params["trackName"])
            track_id = ctx.store.registry.resolve_track(track_name)

            if track_id:
                params["trackId"] = track_id
                logger.debug(f"🔗 Resolved trackName '{track_name}' → {track_id[:8]}")
            elif call.name != "stori_add_midi_track":
                logger.warning(f"⚠️ Could not resolve trackName '{track_name}'")
                ctx.add_result(call.name, False, {}, f"Track '{track_name}' not found")
                return

        if "regionName" in params and "regionId" not in params:
            region_name = _sp(params["regionName"])
            _ptid = params.get("trackId")
            _ptn = params.get("trackName")
            parent_track = (_ptid if isinstance(_ptid, str) else None) or (_ptn if isinstance(_ptn, str) else None)
            region_id = ctx.store.registry.resolve_region(region_name, parent_track)
            if region_id:
                params["regionId"] = region_id
            else:
                logger.warning(f"⚠️ Could not resolve regionName '{region_name}'")

        # --- Step 2: Handle Entity-Creating Tools ---

        entity_created = None

        if call.name == "stori_add_midi_track":
            track_name = _sp(params.get("name"), "Track")
            _instr = params.get("instrument")
            instrument = _instr if isinstance(_instr, str) else None
            gm_program = params.get("gmProgram")

            existing = ctx.store.registry.resolve_track(track_name, exact=True)
            if existing:
                logger.debug(f"📋 Track '{track_name}' already exists: {existing[:8]}")
                params["trackId"] = existing
            else:
                _etid = params.get("trackId")
                track_id = ctx.store.create_track(
                    track_name,
                    track_id=_etid if isinstance(_etid, str) else None,
                    transaction=ctx.transaction,
                )
                params["trackId"] = track_id
                entity_created = track_id
                logger.info(f"🎹 Created track: {track_name} → {track_id[:8]}")

            if gm_program is None:
                from app.core.gm_instruments import infer_gm_program_with_context
                inference = infer_gm_program_with_context(
                    track_name=track_name,
                    instrument=instrument,
                )
                params["_gmInstrumentName"] = inference.instrument_name
                params["_isDrums"] = inference.is_drums
                logger.info(
                    f"🎵 [EXECUTOR] GM inference for '{track_name}': "
                    f"program={inference.program}, instrument={inference.instrument_name}, "
                    f"is_drums={inference.is_drums}"
                )
                if inference.needs_program_change:
                    params["gmProgram"] = inference.program

        elif call.name == "stori_add_midi_region":
            _region_track_id_raw = params.get("trackId")
            region_track_id = _region_track_id_raw if isinstance(_region_track_id_raw, str) else None
            if not region_track_id:
                _tr = params.get("name") or params.get("trackName")
                track_ref = _tr if isinstance(_tr, str) else None
                if track_ref:
                    region_track_id = ctx.store.registry.resolve_track(track_ref)
                    if region_track_id:
                        params["trackId"] = region_track_id
                    else:
                        ctx.add_result(call.name, False, {}, f"Track '{track_ref}' not found")
                        return
                else:
                    ctx.add_result(call.name, False, {}, "No track specified")
                    return

            region_name = _sp(params.get("name"), "Region")
            _req_start_raw = params.get("startBeat", 0)
            _req_dur_raw = params.get("durationBeats", 16)
            _req_start = _req_start_raw if isinstance(_req_start_raw, (int, float)) else 0
            _req_dur = _req_dur_raw if isinstance(_req_dur_raw, (int, float)) else 16

            _existing_rid = ctx.store.registry.find_overlapping_region(
                region_track_id, _req_start, _req_dur,
            )
            if _existing_rid:
                params["regionId"] = _existing_rid
                logger.info(
                    f"📍 Idempotent region hit: {region_name} → {_existing_rid[:8]}"
                )
                ctx.add_result(call.name, True, {
                    "params": params,
                    "existingRegionId": _existing_rid,
                    "skipped": True,
                }, entity_created=_existing_rid)
                return

            try:
                _rid_raw = params.get("regionId")
                region_id = ctx.store.create_region(
                    name=region_name,
                    parent_track_id=region_track_id,
                    region_id=_rid_raw if isinstance(_rid_raw, str) else None,
                    metadata={
                        "startBeat": _req_start,
                        "durationBeats": _req_dur,
                    },
                    transaction=ctx.transaction,
                )
                params["regionId"] = region_id
                entity_created = region_id
                logger.info(f"📍 Created region: {region_name} → {region_id[:8]}")
            except ValueError as e:
                ctx.add_result(call.name, False, {}, str(e))
                return

        elif call.name == "stori_ensure_bus":
            bus_name = _sp(params.get("name"), "Bus")
            bus_id = ctx.store.get_or_create_bus(bus_name, transaction=ctx.transaction)
            params["busId"] = bus_id
            entity_created = bus_id
            logger.debug(f"🔊 Ensured bus: {bus_name} → {bus_id[:8]}")

        # --- Step 3: Handle Generator Tools ---

        if meta and meta.tier == ToolTier.TIER1 and meta.kind == ToolKind.GENERATOR:
            await _execute_generator(call.name, params, ctx)
            return

        # --- Step 4: Record State Changes ---

        if call.name == "stori_set_tempo":
            _tempo_raw = params.get("tempo")
            if isinstance(_tempo_raw, (int, float)):
                ctx.store.set_tempo(int(_tempo_raw), transaction=ctx.transaction)

        elif call.name == "stori_set_key":
            _key = params.get("key")
            if isinstance(_key, str):
                ctx.store.set_key(_key, transaction=ctx.transaction)

        elif call.name == "stori_add_notes":
            _rid = params.get("regionId")
            _notes = params.get("notes")
            if isinstance(_rid, str) and isinstance(_notes, list):
                ctx.store.add_notes(_rid, _notes, transaction=ctx.transaction)

        elif call.name == "stori_add_insert_effect":
            _track = params.get("trackId")
            _etype = params.get("type")
            if isinstance(_track, str) and isinstance(_etype, str):
                ctx.store.add_effect(_track, _etype, transaction=ctx.transaction)

        # --- Step 5: Emit Tier 2 Primitives to Client ---

        if meta and meta.creates_entity and meta.id_fields:
            for id_field in meta.id_fields:
                if id_field not in params:
                    import uuid
                    generated_id = str(uuid.uuid4())
                    params[id_field] = generated_id
                    logger.debug(f"🔑 Generated {id_field}: {generated_id[:8]}")

        ctx.add_event({"tool": call.name, "params": params})
        ctx.add_result(call.name, True, {"params": params}, entity_created=entity_created)
