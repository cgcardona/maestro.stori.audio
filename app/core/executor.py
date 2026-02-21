"""
Executor for Stori Maestro (Cursor-of-DAWs).

Provides two main execution paths:

1. **Variation mode** (``execute_plan_variation``):
   Simulates tool calls without mutating canonical state, captures
   base/proposed notes, and computes a Variation (musical diff) for
   human review.

2. **Phrase application** (``apply_variation_phrases``):
   Applies accepted variation phrases to canonical state after human
   approval.

Internal helpers (``_execute_single_call``, ``_execute_generator``)
handle entity creation, name resolution, and music generation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.core.state_store import StateStore, Transaction, get_or_create_store
from app.core.expansion import ToolCall, dedupe_tool_calls
from app.core.tools import get_tool_meta, ToolTier, ToolKind
from app.core.tracing import (
    TraceContext,
    get_trace_context,
    trace_span,
    log_tool_call,
)
from app.core.emotion_vector import EmotionVector, emotion_vector_from_stori_prompt
from app.services.music_generator import get_music_generator

logger = logging.getLogger(__name__)

# Per-generator-call timeout (seconds) to prevent one slow call from
# consuming the entire 90s variation budget.
_GENERATOR_TIMEOUT = 30

# ---------------------------------------------------------------------------
# Note normalization — MCP tool payloads may use camelCase field names.
# Canonical format is snake_case: start_beat, duration_beats.
# ---------------------------------------------------------------------------

_NOTE_KEY_MAP = {
    "startBeat": "start_beat",
    "durationBeats": "duration_beats",
}


def _normalize_note(note: dict) -> dict:
    """Return a copy of *note* with canonical snake_case field names."""
    out: dict[str, Any] = {}
    for k, v in note.items():
        out[_NOTE_KEY_MAP.get(k, k)] = v
    return out


@dataclass
class ExecutionResult:
    """Result of a single tool execution."""
    tool_name: str
    success: bool
    output: dict[str, Any]
    error: Optional[str] = None
    entity_created: Optional[str] = None  # ID of created entity


@dataclass
class ExecutionContext:
    """Context for plan execution with transaction support."""
    store: StateStore
    transaction: Transaction
    trace: TraceContext
    results: list[ExecutionResult] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    
    def add_result(
        self,
        tool_name: str,
        success: bool,
        output: dict[str, Any],
        error: Optional[str] = None,
        entity_created: Optional[str] = None,
    ) -> None:
        """Record a tool execution result."""
        self.results.append(ExecutionResult(
            tool_name=tool_name,
            success=success,
            output=output,
            error=error,
            entity_created=entity_created,
        ))
        
        # Log for tracing
        log_tool_call(self.trace.trace_id, tool_name, output, success, error)
    
    def add_event(self, event: dict[str, Any]) -> None:
        """Add an event to emit to the client."""
        self.events.append(event)
    
    @property
    def all_successful(self) -> bool:
        """Check if all executions were successful."""
        return all(r.success for r in self.results)
    
    @property
    def failed_tools(self) -> list[str]:
        """Get names of tools that failed."""
        return [r.tool_name for r in self.results if not r.success]
    
    @property
    def created_entities(self) -> dict[str, str]:
        """Get mapping of tool -> created entity ID."""
        return {
            r.tool_name: r.entity_created
            for r in self.results
            if r.entity_created
        }



async def _execute_single_call(call: ToolCall, ctx: ExecutionContext) -> None:
    """
    Execute a single tool call with entity management.
    
    Handles:
    - Entity creation (tracks, regions, buses) via StateStore
    - Name → ID resolution
    - Generator execution (server-side)
    - Event emission
    """
    meta = get_tool_meta(call.name)
    params = call.params.copy()
    
    with trace_span(ctx.trace, f"tool:{call.name}", {"params_keys": list(params.keys())}):
        
        # =====================================================================
        # Step 1: Resolve Name References
        # =====================================================================
        
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
        
        # =====================================================================
        # Step 2: Handle Entity-Creating Tools (via StateStore)
        # =====================================================================
        
        entity_created = None
        
        if call.name == "stori_add_midi_track":
            track_name = params.get("name", "Track")
            instrument = params.get("instrument")
            gm_program = params.get("gmProgram")
            
            # Check if track already exists - use exact matching to avoid
            # fuzzy matches causing duplicate ID issues (e.g., "Drums" matching "Phish Drums")
            existing = ctx.store.registry.resolve_track(track_name, exact=True)
            if existing:
                logger.debug(f"📋 Track '{track_name}' already exists: {existing[:8]}")
                params["trackId"] = existing
            else:
                # Create via StateStore (with transaction)
                track_id = ctx.store.create_track(
                    track_name,
                    track_id=params.get("trackId"),
                    transaction=ctx.transaction,
                )
                params["trackId"] = track_id
                entity_created = track_id
                logger.info(f"🎹 Created track: {track_name} → {track_id[:8]}")
            
            # Auto-infer GM program if not specified
            if gm_program is None:
                from app.core.gm_instruments import infer_gm_program_with_context
                inference = infer_gm_program_with_context(
                    track_name=track_name,
                    instrument=instrument,
                )
                # Always provide instrument metadata
                params["_gmInstrumentName"] = inference.instrument_name
                params["_isDrums"] = inference.is_drums
                
                logger.info(
                    f"🎵 [EXECUTOR] GM inference for '{track_name}': "
                    f"program={inference.program}, instrument={inference.instrument_name}, "
                    f"is_drums={inference.is_drums}, params_has_name={('_gmInstrumentName' in params)}"
                )
                
                # Add GM program if not drums
                if inference.needs_program_change:
                    gm_program = inference.program
                    params["gmProgram"] = gm_program
        
        elif call.name == "stori_add_midi_region":
            track_id = params.get("trackId")
            if not track_id:
                # Try to get from name parameter
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
            try:
                region_id = ctx.store.create_region(
                    name=region_name,
                    parent_track_id=track_id,
                    region_id=params.get("regionId"),
                    metadata={
                        "startBeat": params.get("startBeat", 0),
                        "durationBeats": params.get("durationBeats", 16),
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
        
        # =====================================================================
        # Step 3: Handle Generator Tools (Server-Side Execution)
        # =====================================================================
        
        if meta and meta.tier == ToolTier.TIER1 and meta.kind == ToolKind.GENERATOR:
            await _execute_generator(call.name, params, ctx)
            return
        
        # =====================================================================
        # Step 4: Record state changes for specific tools
        # =====================================================================
        
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
        
        # =====================================================================
        # Step 5: Emit Tier 2 Primitives to Client
        # =====================================================================
        
        # Ensure entity IDs are set for entity-creating tools
        if meta and meta.creates_entity and meta.id_fields:
            for id_field in meta.id_fields:
                if id_field not in params:
                    import uuid
                    generated_id = str(uuid.uuid4())
                    params[id_field] = generated_id
                    logger.debug(f"🔑 Generated {id_field}: {generated_id[:8]}")
        
        ctx.add_event({"tool": call.name, "params": params})
        ctx.add_result(call.name, True, {"params": params}, entity_created=entity_created)


async def _execute_generator(name: str, params: dict[str, Any], ctx: ExecutionContext) -> None:
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
        
        # Find target region
        track_name = params.get("trackName", gen_params["instrument"].capitalize())
        track_id = ctx.store.registry.resolve_track(track_name) or params.get("trackId")
        
        if not track_id:
            ctx.add_result(name, False, {}, f"Track '{track_name}' not found")
            return
        
        region_id = ctx.store.registry.get_latest_region_for_track(track_id)
        
        if not region_id:
            ctx.add_result(name, False, {}, "No region found for notes")
            return
        
        # Record notes added
        ctx.store.add_notes(region_id, result.notes, transaction=ctx.transaction)

        # Store CC and pitch bend data alongside notes
        if result.cc_events:
            ctx.store.add_cc(region_id, result.cc_events)
        if result.pitch_bends:
            ctx.store.add_pitch_bends(region_id, result.pitch_bends)
        if result.aftertouch:
            ctx.store.add_aftertouch(region_id, result.aftertouch)
        
        # Emit add_notes event
        ctx.add_event({
            "tool": "stori_add_notes",
            "params": {
                "notes": result.notes,
                "trackId": track_id,
                "regionId": region_id,
            }
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


# =============================================================================
# Three-phase grouping for parallel instrument execution
# =============================================================================

_PHASE1_TOOLS = {"stori_set_tempo", "stori_set_key"}
_PHASE3_TOOLS = {
    "stori_ensure_bus", "stori_add_send",
    "stori_set_track_volume", "stori_set_track_pan",
    "stori_mute_track", "stori_solo_track",
}


def _get_instrument_for_call(call: ToolCall) -> Optional[str]:
    """Extract the instrument/track name a tool call belongs to.

    Returns None for project-level (setup/mixing) calls.
    """
    if call.name == "stori_add_midi_track":
        return call.params.get("name")
    if call.name in _PHASE1_TOOLS | _PHASE3_TOOLS:
        return None
    return (
        call.params.get("trackName")
        or call.params.get("name")
        or (
            call.params.get("role", "").capitalize()
            if call.name.startswith("stori_generate") else None
        )
    )


def _group_into_phases(
    tool_calls: list[ToolCall],
) -> tuple[
    list[ToolCall],
    dict[str, list[ToolCall]],
    list[str],
    list[ToolCall],
]:
    """Split tool calls into three execution phases.

    Returns:
        (phase1_setup, instrument_groups, instrument_order, phase3_mixing)

    Phase 1 — project-level setup (tempo, key).
    Phase 2 — per-instrument groups keyed by lowercase track name.
              ``instrument_order`` preserves first-seen ordering.
    Phase 3 — shared buses, sends, volume/pan adjustments.
    """
    phase1: list[ToolCall] = []
    groups: dict[str, list[ToolCall]] = {}
    order: list[str] = []
    phase3: list[ToolCall] = []

    for call in tool_calls:
        if call.name in _PHASE1_TOOLS:
            phase1.append(call)
        elif call.name in _PHASE3_TOOLS:
            phase3.append(call)
        else:
            instrument = _get_instrument_for_call(call)
            if instrument:
                key = instrument.lower()
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append(call)
            else:
                phase3.append(call)

    return phase1, groups, order, phase3


# =============================================================================
# Variation Mode Execution
# =============================================================================

from app.models.variation import Variation
from app.services.variation import get_variation_service


@dataclass
class VariationContext:
    """Context for variation mode execution (no transaction)."""
    store: StateStore
    trace: TraceContext
    base_notes: dict[str, list[dict]]  # region_id -> notes before transformation
    proposed_notes: dict[str, list[dict]]  # region_id -> notes after transformation
    track_regions: dict[str, str]  # region_id -> track_id
    proposed_cc: dict[str, list[dict]] = field(default_factory=dict)
    proposed_pitch_bends: dict[str, list[dict]] = field(default_factory=dict)
    proposed_aftertouch: dict[str, list[dict]] = field(default_factory=dict)
    
    def capture_base_notes(self, region_id: str, track_id: str, notes: list[dict]) -> None:
        """Capture base notes for a region before transformation."""
        if region_id not in self.base_notes:
            self.base_notes[region_id] = [_normalize_note(n) for n in notes]
            self.track_regions[region_id] = track_id
    
    def record_proposed_notes(self, region_id: str, notes: list[dict]) -> None:
        """Record proposed notes for a region after transformation."""
        self.proposed_notes[region_id] = [_normalize_note(n) for n in notes]

    def record_proposed_cc(self, region_id: str, cc_events: list[dict]) -> None:
        """Record proposed MIDI CC events for a region."""
        if cc_events:
            self.proposed_cc.setdefault(region_id, []).extend(cc_events)

    def record_proposed_pitch_bends(self, region_id: str, pitch_bends: list[dict]) -> None:
        """Record proposed pitch bend events for a region."""
        if pitch_bends:
            self.proposed_pitch_bends.setdefault(region_id, []).extend(pitch_bends)

    def record_proposed_aftertouch(self, region_id: str, aftertouch: list[dict]) -> None:
        """Record proposed aftertouch events for a region."""
        if aftertouch:
            self.proposed_aftertouch.setdefault(region_id, []).extend(aftertouch)


async def execute_plan_variation(
    tool_calls: list[ToolCall],
    project_state: dict[str, Any],
    intent: str,
    conversation_id: Optional[str] = None,
    explanation: Optional[str] = None,
    progress_callback: Optional[Callable[..., Awaitable[None]]] = None,
    quality_preset: Optional[str] = None,
    tool_event_callback: Optional[Callable[..., Awaitable[None]]] = None,
    pre_tool_callback: Optional[Callable[..., Awaitable[None]]] = None,
    post_tool_callback: Optional[Callable[..., Awaitable[None]]] = None,
) -> Variation:
    """
    Execute a plan in variation mode - returns proposed changes without mutation.

    Instead of committing changes to StateStore, this function:
    1. Captures base state before each note-affecting tool
    2. Simulates Muse's transformations
    3. Computes variation between base and proposed states
    4. Returns a Variation object for frontend review

    Args:
        tool_calls: List of ToolCalls to execute
        project_state: Current project state from client
        intent: The user intent that triggered this execution
        conversation_id: Conversation ID for StateStore lookup
        explanation: Optional AI explanation of the variation
        progress_callback: Optional async callback(current_step, total_steps) after each step.
        tool_event_callback: Optional async callback(call_id, tool_name, params) called
            BEFORE each tool call is processed so the caller can emit toolStart/toolCall SSE.
            Deprecated in favour of pre_tool_callback/post_tool_callback.
        pre_tool_callback: Optional async callback(tool_name, params) fired BEFORE
            each tool call is processed (for toolStart / planStepUpdate:active).
        post_tool_callback: Optional async callback(tool_name, resolved_params) fired
            AFTER each tool call completes with resolved entity IDs
            (for toolCall with real UUIDs / planStepUpdate:completed).

    Returns:
        Variation object with phrases representing proposed changes
    """
    import uuid as uuid_module
    
    trace = get_trace_context()
    start_time = time.time()
    
    with trace_span(trace, "execute_plan_variation", {"tool_count": len(tool_calls)}):
        # Get StateStore (for context, not mutation)
        store = get_or_create_store(
            conversation_id=conversation_id or "default",
            project_id=project_state.get("id"),
        )
        store.sync_from_client(project_state)
        
        # Deduplicate tool calls
        tool_calls = dedupe_tool_calls(tool_calls)
        
        if not tool_calls:
            # Empty variation
            return Variation(
                variation_id=str(uuid_module.uuid4()),
                intent=intent,
                ai_explanation=explanation,
                affected_tracks=[],
                affected_regions=[],
                beat_range=(0.0, 0.0),
                phrases=[],
            )
        
        # Create variation context (no transaction)
        var_ctx = VariationContext(
            store=store,
            trace=trace,
            base_notes={},
            proposed_notes={},
            track_regions={},
        )
        
        logger.info(f"🎭 Variation mode: {len(tool_calls)} tool calls")
        
        # Extract existing notes from project state for base comparison
        _extract_notes_from_project(project_state, var_ctx)
        
        # Derive emotion vector from STORI PROMPT so Orpheus receives expressive context.
        emotion_vector: Optional[EmotionVector] = None
        if explanation:
            emotion_vector = emotion_vector_from_stori_prompt(explanation)
            logger.info(f"🎭 Emotion vector derived: {emotion_vector}")

        # -----------------------------------------------------------------------
        # Three-phase execution for maximum throughput
        #
        # Phase 1 — Setup (sequential): project-level primitives that must
        #   complete before instrument work begins (tempo, key, time sig).
        # Phase 2 — Instruments (parallel): each instrument gets its own
        #   concurrent sub-pipeline running sequentially within the group
        #   (create track → add region → generate MIDI → add effects → CC).
        #   Independent instruments run concurrently.
        # Phase 3 — Mixing (sequential): shared buses, sends, volume/pan
        #   adjustments that may reference multiple tracks.
        # -----------------------------------------------------------------------

        phase1, instrument_groups, instrument_order, phase3 = _group_into_phases(
            tool_calls
        )

        total = len(tool_calls)
        completed_count = [0]  # mutable counter, safe in single-threaded asyncio

        async def _dispatch(call: ToolCall) -> None:
            logger.info(f"🔧 Processing: {call.name}")
            if pre_tool_callback:
                await pre_tool_callback(call.name, call.params)
            elif tool_event_callback:
                await tool_event_callback(call.id, call.name, call.params)
            resolved_params = await _process_call_for_variation(
                call,
                var_ctx,
                quality_preset=quality_preset,
                emotion_vector=emotion_vector,
            )
            if post_tool_callback:
                await post_tool_callback(call.name, resolved_params)
            completed_count[0] += 1
            if progress_callback:
                await progress_callback(completed_count[0], total, call.name, call.params)

        # Phase 1: project-level setup (sequential)
        for call in phase1:
            await _dispatch(call)

        # Phase 2: instrument groups (parallel across groups, sequential
        # within each group).  Bounded concurrency prevents overloading
        # Orpheus when many instruments are requested.
        _MAX_PARALLEL_GROUPS = 5
        if instrument_groups:
            logger.info(
                f"🚀 Parallel instrument execution: {len(instrument_groups)} groups "
                f"({', '.join(instrument_order)}), "
                f"max {_MAX_PARALLEL_GROUPS} concurrent"
            )

            semaphore = asyncio.Semaphore(_MAX_PARALLEL_GROUPS)

            async def _run_instrument_group(calls: list[ToolCall]) -> None:
                async with semaphore:
                    for call in calls:
                        await _dispatch(call)

            await asyncio.gather(
                *[
                    _run_instrument_group(instrument_groups[name])
                    for name in instrument_order
                ]
            )

        # Phase 3: mixing / shared routing (sequential — may reference
        # multiple tracks that were created in Phase 2)
        for call in phase3:
            await _dispatch(call)

        # Log what we captured
        total_base = sum(len(n) for n in var_ctx.base_notes.values())
        total_proposed = sum(len(n) for n in var_ctx.proposed_notes.values())
        logger.info(
            f"📊 Variation context: {len(var_ctx.base_notes)} base regions ({total_base} notes), "
            f"{len(var_ctx.proposed_notes)} proposed regions ({total_proposed} notes)"
        )
        
        # Compute variation using variation service
        variation_service = get_variation_service()
        
        # Build region_id → startBeat mapping from registry metadata so
        # phrase start_beat/end_beat become absolute project positions.
        _region_start_beats: dict[str, float] = {}
        for rid in set(var_ctx.base_notes.keys()) | set(var_ctx.proposed_notes.keys()):
            entity = store.registry.get_region(rid)
            if entity and entity.metadata:
                _region_start_beats[rid] = float(
                    entity.metadata.get("startBeat", 0)
                )

        # If we have multiple regions, use multi-region variation.
        # Pass the full track_regions mapping so each phrase carries the
        # correct server-assigned trackId, not a single shared value.
        if len(var_ctx.proposed_notes) > 1:
            variation = variation_service.compute_multi_region_variation(
                base_regions=var_ctx.base_notes,
                proposed_regions=var_ctx.proposed_notes,
                track_regions=var_ctx.track_regions,
                intent=intent,
                explanation=explanation,
                region_start_beats=_region_start_beats,
                region_cc=var_ctx.proposed_cc,
                region_pitch_bends=var_ctx.proposed_pitch_bends,
                region_aftertouch=var_ctx.proposed_aftertouch,
            )
        elif var_ctx.proposed_notes:
            # Single region
            region_id = next(iter(var_ctx.proposed_notes.keys()))
            track_id = var_ctx.track_regions.get(region_id, "unknown")
            variation = variation_service.compute_variation(
                base_notes=var_ctx.base_notes.get(region_id, []),
                proposed_notes=var_ctx.proposed_notes.get(region_id, []),
                region_id=region_id,
                track_id=track_id,
                intent=intent,
                explanation=explanation,
                region_start_beat=_region_start_beats.get(region_id, 0.0),
                cc_events=var_ctx.proposed_cc.get(region_id),
                pitch_bends=var_ctx.proposed_pitch_bends.get(region_id),
                aftertouch=var_ctx.proposed_aftertouch.get(region_id),
            )
        else:
            # No notes affected - empty variation
            variation = Variation(
                variation_id=str(uuid_module.uuid4()),
                intent=intent,
                ai_explanation=explanation,
                affected_tracks=[],
                affected_regions=[],
                beat_range=(0.0, 0.0),
                phrases=[],
            )
        
        duration_ms = (time.time() - start_time) * 1000
        logger.info(
            f"✨ Variation computed: {variation.total_changes} changes in "
            f"{len(variation.phrases)} phrases ({duration_ms:.1f}ms)"
        )
        
        return variation


def _extract_notes_from_project(
    project_state: dict[str, Any],
    var_ctx: VariationContext,
) -> None:
    """Extract existing notes from project state into variation context.

    The frontend may omit the ``notes`` array (only ``note_count``).  When
    notes are absent, fall back to the StateStore so that notes populated by
    prior tool calls are still available for diffing.
    """
    tracks = project_state.get("tracks", [])

    for track in tracks:
        track_id = track.get("id", "")
        regions = track.get("regions", [])

        for region in regions:
            region_id = region.get("id", "")
            notes = region.get("notes", [])
            if not notes:
                notes = var_ctx.store.get_region_notes(region_id)
            if region_id and notes:
                var_ctx.capture_base_notes(region_id, track_id, notes)


async def _process_call_for_variation(
    call: ToolCall,
    var_ctx: VariationContext,
    quality_preset: Optional[str] = None,
    emotion_vector: Optional[EmotionVector] = None,
) -> dict[str, Any]:
    """
    Process a tool call to extract proposed notes for variation.

    Simulates entity creation (tracks, regions) in the state store so that
    subsequent generator calls can resolve track/region names. Does NOT
    mutate canonical state — uses the store's registry for name resolution.

    Returns resolved params dict with server-assigned entity IDs so the
    caller can emit real toolCall SSE events (proposal: false).

    emotion_vector is derived from the STORI PROMPT explanation and forwarded
    to Orpheus as expressive conditioning (tone, energy, intimacy, etc.).
    """
    params = call.params.copy()
    
    # -----------------------------------------------------------------
    # Name resolution: trackName → trackId, regionName → regionId
    # (mirrors the streaming executor's Step 1)
    # -----------------------------------------------------------------
    if "trackName" in params and "trackId" not in params:
        track_name = params["trackName"]
        track_id = var_ctx.store.registry.resolve_track(track_name)
        if track_id:
            params["trackId"] = track_id
    
    if "regionName" in params and "regionId" not in params:
        region_name = params["regionName"]
        parent_track = params.get("trackId") or params.get("trackName")
        region_id = var_ctx.store.registry.resolve_region(region_name, parent_track)
        if region_id:
            params["regionId"] = region_id
    
    # -----------------------------------------------------------------
    # Entity creation: register tracks and regions so generators can
    # resolve names like "Drums" → track_id → latest region.
    # Resolved entity IDs are stored in params for the caller.
    # -----------------------------------------------------------------
    if call.name == "stori_add_midi_track":
        track_name = params.get("name", "Track")
        existing = var_ctx.store.registry.resolve_track(track_name)
        if not existing:
            track_id = var_ctx.store.create_track(track_name, track_id=params.get("trackId"))
            params["trackId"] = track_id
            logger.info(f"🎹 [variation] Registered track: {track_name} → {track_id[:8]}")

            # Auto-infer GM program for the post-execution event
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

    elif call.name == "stori_add_midi_region":
        track_id = params.get("trackId", "")
        if not track_id:
            track_ref = params.get("trackName") or params.get("name")
            if track_ref:
                track_id = var_ctx.store.registry.resolve_track(track_ref) or ""
                if track_id:
                    params["trackId"] = track_id
        if track_id:
            region_name = params.get("name", "Region")
            region_id = var_ctx.store.create_region(
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
            logger.warning(f"⚠️ [variation] Cannot create region — no track resolved")

    # -----------------------------------------------------------------
    # Note-affecting tools
    # -----------------------------------------------------------------
    elif call.name == "stori_add_notes":
        region_id = params.get("regionId", "")
        track_id = params.get("trackId", "")
        notes = params.get("notes", [])

        if not region_id:
            logger.warning(
                f"⚠️ stori_add_notes: missing regionId, dropping {len(notes)} notes"
            )
        elif notes:
            # Validate the region exists in the registry. The planner never
            # provides regionIds in its plan (it uses trackName resolution), so
            # any regionId here should already be registered. If it's not, the
            # LLM may have hallucinated it — try to recover via the track's
            # latest region rather than silently drop the notes.
            registered = var_ctx.store.registry.get_region(region_id)
            if not registered:
                # Attempt fallback: resolve track → latest region
                resolved_track_id = (
                    var_ctx.store.registry.resolve_track(params.get("trackName", ""))
                    or track_id
                )
                fallback_region_id = (
                    var_ctx.store.registry.get_latest_region_for_track(resolved_track_id)
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
                # Derive track_id from registry if not provided or unregistered.
                if not track_id:
                    entity = var_ctx.store.registry.get_region(region_id)
                    track_id = entity.parent_id if entity else ""

                if region_id not in var_ctx.base_notes:
                    var_ctx.capture_base_notes(region_id, track_id, [])

                var_ctx.record_proposed_notes(region_id, notes)
                logger.info(
                    f"📝 stori_add_notes: {len(notes)} notes → "
                    f"region={region_id[:8]} track={track_id[:8]}"
                )
    
    # -----------------------------------------------------------------
    # MIDI CC and pitch bend tools
    # -----------------------------------------------------------------
    elif call.name == "stori_add_midi_cc":
        region_id = params.get("regionId", "")
        cc = params.get("cc")
        events = params.get("events", [])
        if region_id and cc is not None and events:
            cc_events = [{"cc": cc, "beat": e["beat"], "value": e["value"]} for e in events]
            var_ctx.record_proposed_cc(region_id, cc_events)
            logger.info(
                f"🎛️ stori_add_midi_cc: CC{cc} {len(events)} events → "
                f"region={region_id[:8]}"
            )

    elif call.name == "stori_add_pitch_bend":
        region_id = params.get("regionId", "")
        events = params.get("events", [])
        if region_id and events:
            var_ctx.record_proposed_pitch_bends(region_id, events)
            logger.info(
                f"🎛️ stori_add_pitch_bend: {len(events)} events → "
                f"region={region_id[:8]}"
            )

    elif call.name == "stori_add_aftertouch":
        region_id = params.get("regionId", "")
        events = params.get("events", [])
        if region_id and events:
            var_ctx.record_proposed_aftertouch(region_id, events)
            logger.info(
                f"🎛️ stori_add_aftertouch: {len(events)} events → "
                f"region={region_id[:8]}"
            )

    # Handle generators - they produce notes that go to a region
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
                    quality_preset=quality_preset or "quality",
                    emotion_vector=emotion_vector,
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
                # Find target region
                track_name = params.get("trackName", gen_params["instrument"].capitalize())
                track_id = var_ctx.store.registry.resolve_track(track_name) or params.get("trackId", "")
                
                if track_id:
                    region_id = var_ctx.store.registry.get_latest_region_for_track(track_id)
                    
                    if region_id:
                        if region_id not in var_ctx.base_notes:
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
                logger.warning(
                    f"⚠️ Generator {call.name} failed: {result.error}"
                )

        except asyncio.TimeoutError:
            logger.error(
                f"⏱ Generator {call.name} timed out after {_GENERATOR_TIMEOUT}s"
            )
        except Exception as e:
            logger.exception(
                f"⚠️ Generator simulation failed for {call.name}: {e}"
            )

    return params


@dataclass
class VariationApplyResult:
    """Result from applying variation phrases."""
    success: bool
    applied_phrase_ids: list[str]
    notes_added: int
    notes_removed: int
    notes_modified: int
    updated_regions: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


async def apply_variation_phrases(
    variation: Variation,
    accepted_phrase_ids: list[str],
    project_state: dict[str, Any],
    conversation_id: Optional[str] = None,
) -> VariationApplyResult:
    """
    Apply accepted phrases from a variation to the canonical state.
    
    This is the commit phase - only called after user accepts phrases.
    Changes are applied server-side via StateStore transaction.
    
    Args:
        variation: The Variation containing the phrases
        accepted_phrase_ids: List of phrase IDs to apply
        project_state: Current project state
        conversation_id: Conversation ID for StateStore lookup
        
    Returns:
        VariationApplyResult with success status and counts
    """
    trace = get_trace_context()
    
    with trace_span(trace, "apply_variation_phrases", {"phrase_count": len(accepted_phrase_ids)}):
        store = get_or_create_store(
            conversation_id=conversation_id or "default",
            project_id=project_state.get("id"),
        )
        # Only sync from client when a real project snapshot is provided.
        # Passing an empty dict would wipe server-side state built during the
        # compose phase, breaking note lookups after commit.
        if project_state:
            store.sync_from_client(project_state)
        
        try:
            notes_added = 0
            notes_removed = 0
            notes_modified = 0
            applied_phrases = []

            # Group adds and removals by region; track region→track mapping
            # from phrase data so we don't depend on a registry that may be empty.
            region_adds: dict[str, list[dict]] = {}
            region_removals: dict[str, list[dict]] = {}
            region_track_map: dict[str, str] = {}
            region_cc: dict[str, list[dict]] = {}
            region_pitch_bends: dict[str, list[dict]] = {}
            region_aftertouch: dict[str, list[dict]] = {}

            # Process phrases in sequence order (accepted_phrase_ids order)
            for phrase_id in accepted_phrase_ids:
                phrase = variation.get_phrase(phrase_id)
                if not phrase:
                    logger.warning(f"Phrase {phrase_id[:8]} not found in variation")
                    continue

                region_id = phrase.region_id
                region_track_map[region_id] = phrase.track_id

                if region_id not in region_adds:
                    region_adds[region_id] = []
                if region_id not in region_removals:
                    region_removals[region_id] = []

                for nc in phrase.note_changes:
                    if nc.change_type == "added":
                        notes_added += 1
                        if nc.after:
                            region_adds[region_id].append(nc.after.to_note_dict())

                    elif nc.change_type == "removed":
                        notes_removed += 1
                        if nc.before:
                            region_removals[region_id].append(nc.before.to_note_dict())

                    elif nc.change_type == "modified":
                        notes_modified += 1
                        if nc.before:
                            region_removals[region_id].append(nc.before.to_note_dict())
                        if nc.after:
                            region_adds[region_id].append(nc.after.to_note_dict())

                # Collect CC / pitch bend / aftertouch from controller_changes
                for cc_change in phrase.controller_changes:
                    kind = cc_change.get("kind", "cc")
                    if kind == "pitch_bend":
                        region_pitch_bends.setdefault(region_id, []).append(cc_change)
                    elif kind == "aftertouch":
                        region_aftertouch.setdefault(region_id, []).append(cc_change)
                    else:
                        region_cc.setdefault(region_id, []).append(cc_change)

                applied_phrases.append(phrase_id)

            # Apply changes in a single transaction: removals first, then adds
            tx = store.begin_transaction(f"Accept Variation: {len(accepted_phrase_ids)} phrases")

            for region_id, criteria in region_removals.items():
                if criteria:
                    store.remove_notes(region_id, criteria, transaction=tx)

            for region_id, notes in region_adds.items():
                if notes:
                    store.add_notes(region_id, notes, transaction=tx)

            for region_id, cc_events in region_cc.items():
                if cc_events:
                    store.add_cc(region_id, cc_events)

            for region_id, pb_events in region_pitch_bends.items():
                if pb_events:
                    store.add_pitch_bends(region_id, pb_events)

            for region_id, at_events in region_aftertouch.items():
                if at_events:
                    store.add_aftertouch(region_id, at_events)

            store.commit(tx)

            # Build updated_regions: full post-commit note state for every
            # affected region.  Keys are snake_case (Python-idiomatic); the
            # API layer converts to camelCase via UpdatedRegionPayload before
            # sending the response.
            affected_region_ids = set(region_adds.keys()) | set(region_removals.keys())
            updated_regions: list[dict[str, Any]] = []
            for rid in sorted(affected_region_ids):
                track_id = region_track_map.get(rid) or store.get_region_track_id(rid) or ""

                # Primary source: store (contains notes added during this commit)
                notes = store.get_region_notes(rid)
                # Fallback: use the adds directly if the store is empty (can
                # happen when the store instance differs from the compose phase)
                if not notes and rid in region_adds:
                    notes = region_adds[rid]

                # Attach region metadata so the API layer can populate
                # startBeat / durationBeats / name on UpdatedRegionPayload
                # for brand-new regions the frontend hasn't seen yet.
                region_entity = store.registry.get_region(rid)
                region_meta = region_entity.metadata if region_entity else {}

                updated_regions.append({
                    "region_id": rid,
                    "track_id": track_id,
                    "notes": notes,
                    "cc_events": store.get_region_cc(rid),
                    "pitch_bends": store.get_region_pitch_bends(rid),
                    "aftertouch": store.get_region_aftertouch(rid),
                    "start_beat": region_meta.get("startBeat"),
                    "duration_beats": region_meta.get("durationBeats"),
                    "name": region_entity.name if region_entity else None,
                })

            logger.info(
                "Applied variation phrases",
                extra={
                    "phrase_count": len(applied_phrases),
                    "notes_added": notes_added,
                    "notes_removed": notes_removed,
                    "notes_modified": notes_modified,
                    "updated_region_count": len(updated_regions),
                },
            )

            return VariationApplyResult(
                success=True,
                applied_phrase_ids=applied_phrases,
                notes_added=notes_added,
                notes_removed=notes_removed,
                notes_modified=notes_modified,
                updated_regions=updated_regions,
            )
            
        except Exception as e:
            logger.error(f"❌ Failed to apply variation phrases: {e}")
            
            return VariationApplyResult(
                success=False,
                applied_phrase_ids=[],
                notes_added=0,
                notes_removed=0,
                notes_modified=0,
                error=str(e),
            )
