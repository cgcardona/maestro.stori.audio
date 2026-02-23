"""Core tool execution: _execute_single_call and _execute_generator."""

from __future__ import annotations

import logging
from typing import Any

from app.core.expansion import ToolCall
from app.core.tools import get_tool_meta, ToolTier, ToolKind
from app.core.tracing import trace_span
from app.core.executor.models import ExecutionContext
from app.services.music_generator import get_music_generator

logger = logging.getLogger(__name__)


async def _execute_generator(
    name: str,
    params: dict[str, Any],
    ctx: ExecutionContext,
) -> None:
    """Execute a generator tool server-side."""
    mg = get_music_generator()

    gen_params = {
        "instrument": params.get("role", "drums"),
        "style": params.get("style", ""),
        "tempo": params.get("tempo", 120),
        "bars": params.get("bars", 4),
        "key": params.get("key"),
        "chords": params.get("chords"),
    }

    logger.info(f"🎵 Generating MIDI: {gen_params['instrument']} - {gen_params['style']}")

    try:
        result = await mg.generate(**gen_params, quality_preset="quality")

        if not result.success:
            logger.error(f"❌ Generation failed: {result.error}")
            ctx.add_result(name, False, {}, result.error)
            return

        logger.info(f"✅ Generated {len(result.notes)} notes via {result.backend_used.value}")

        track_name = params.get("trackName", gen_params["instrument"].capitalize())
        track_id = ctx.store.registry.resolve_track(track_name) or params.get("trackId")

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
            track_name = params["trackName"]
            track_id = ctx.store.registry.resolve_track(track_name)

            if track_id:
                params["trackId"] = track_id
                logger.debug(f"🔗 Resolved trackName '{track_name}' → {track_id[:8]}")
            elif call.name != "stori_add_midi_track":
                logger.warning(f"⚠️ Could not resolve trackName '{track_name}'")
                ctx.add_result(call.name, False, {}, f"Track '{track_name}' not found")
                return

        if "regionName" in params and "regionId" not in params:
            region_name = params["regionName"]
            parent_track = params.get("trackId") or params.get("trackName")
            region_id = ctx.store.registry.resolve_region(region_name, parent_track)
            if region_id:
                params["regionId"] = region_id
            else:
                logger.warning(f"⚠️ Could not resolve regionName '{region_name}'")

        # --- Step 2: Handle Entity-Creating Tools ---

        entity_created = None

        if call.name == "stori_add_midi_track":
            track_name = params.get("name", "Track")
            instrument = params.get("instrument")
            gm_program = params.get("gmProgram")

            existing = ctx.store.registry.resolve_track(track_name, exact=True)
            if existing:
                logger.debug(f"📋 Track '{track_name}' already exists: {existing[:8]}")
                params["trackId"] = existing
            else:
                track_id = ctx.store.create_track(
                    track_name,
                    track_id=params.get("trackId"),
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
            track_id = params.get("trackId")
            if not track_id:
                track_ref = params.get("name") or params.get("trackName")
                if track_ref:
                    track_id = ctx.store.registry.resolve_track(track_ref)
                    if track_id:
                        params["trackId"] = track_id
                    else:
                        ctx.add_result(call.name, False, {}, f"Track '{track_ref}' not found")
                        return
                else:
                    ctx.add_result(call.name, False, {}, "No track specified")
                    return

            region_name = params.get("name", "Region")
            _req_start = params.get("startBeat", 0)
            _req_dur = params.get("durationBeats", 16)

            _existing_rid = ctx.store.registry.find_overlapping_region(
                track_id, _req_start, _req_dur,
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
                region_id = ctx.store.create_region(
                    name=region_name,
                    parent_track_id=track_id,
                    region_id=params.get("regionId"),
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
            bus_name = params.get("name", "Bus")
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
            tempo = params.get("tempo")
            if tempo:
                ctx.store.set_tempo(tempo, transaction=ctx.transaction)

        elif call.name == "stori_set_key":
            key = params.get("key")
            if key:
                ctx.store.set_key(key, transaction=ctx.transaction)

        elif call.name == "stori_add_notes":
            region_id = params.get("regionId")
            notes = params.get("notes", [])
            if region_id and notes:
                ctx.store.add_notes(region_id, notes, transaction=ctx.transaction)

        elif call.name == "stori_add_insert_effect":
            track_id = params.get("trackId")
            effect_type = params.get("type")
            if track_id and effect_type:
                ctx.store.add_effect(track_id, effect_type, transaction=ctx.transaction)

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
