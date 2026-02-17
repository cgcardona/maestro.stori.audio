"""
Executor for Stori Composer (Cursor-of-DAWs).

Executes validated tool call plans against the DAW state with:
- Transaction semantics (atomic execution with rollback)
- StateStore integration (persistent, versioned state)
- Event sourcing (all mutations captured)
- Streaming execution (incremental feedback)

Execution Flow:
1. Begin transaction on StateStore
2. Execute each tool call in sequence
3. On success: commit transaction, return events
4. On failure: rollback transaction, return partial events with error
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from app.core.state_store import StateStore, Transaction, get_or_create_store
from app.core.expansion import ToolCall, dedupe_tool_calls
from app.core.tools import get_tool_meta, ToolTier, ToolKind
from app.core.tracing import (
    TraceContext,
    get_trace_context,
    trace_span,
    log_tool_call,
    log_plan_execution,
)
from app.services.music_generator import get_music_generator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Note normalization — MCP tool payloads may use camelCase field names.
# Canonical format is snake_case: start_beat, duration_beats.
# ---------------------------------------------------------------------------

_NOTE_KEY_MAP = {
    "startBeat": "start_beat",
    "startBeats": "start_beat",
    "start": "start_beat",
    "durationBeats": "duration_beats",
    "duration": "duration_beats",
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


async def execute_plan(
    tool_calls: list[ToolCall],
    project_state: dict[str, Any],
    conversation_id: Optional[str] = None,
    store: Optional[StateStore] = None,
) -> list[dict[str, Any]]:
    """
    Execute a plan (list of tool calls) with transaction semantics.
    
    All calls succeed together or fail together with rollback.
    
    Args:
        tool_calls: List of ToolCalls to execute
        project_state: Current project state from client
        conversation_id: Conversation ID for StateStore lookup
        store: Optional pre-initialized StateStore
        
    Returns:
        List of events to send to the client
    """
    trace = get_trace_context()
    start_time = time.time()
    
    with trace_span(trace, "execute_plan", {"tool_count": len(tool_calls)}):
        # Get or create StateStore
        if store is None:
            store = get_or_create_store(
                conversation_id=conversation_id or "default",
                project_id=project_state.get("projectId"),
            )
        
        # Sync with client state
        store.sync_from_client(project_state)
        
        # Deduplicate tool calls
        tool_calls = dedupe_tool_calls(tool_calls)
        
        if not tool_calls:
            return []
        
        # Begin transaction
        tx = store.begin_transaction(f"Execute {len(tool_calls)} tools")
        
        # Create execution context
        ctx = ExecutionContext(
            store=store,
            transaction=tx,
            trace=trace,
        )
        
        try:
            logger.info(f"🎬 Executing plan: {len(tool_calls)} tool calls")
            
            # Execute each tool call
            for call in tool_calls:
                await _execute_single_call(call, ctx)
                
                # Check for critical failures that should abort
                if ctx.results and not ctx.results[-1].success:
                    meta = get_tool_meta(call.name)
                    if meta and meta.creates_entity:
                        # Entity creation failure is critical - abort
                        logger.error(f"❌ Critical failure in {call.name}, aborting")
                        raise ExecutionError(f"Failed to create entity: {ctx.results[-1].error}")
            
            # All succeeded - commit
            store.commit(tx)
            
            duration_ms = (time.time() - start_time) * 1000
            log_plan_execution(
                trace.trace_id,
                len(tool_calls),
                len([r for r in ctx.results if r.success]),
                len([r for r in ctx.results if not r.success]),
                duration_ms,
            )
            
            logger.info(f"✅ Plan executed: {len(ctx.results)} tools, {len(ctx.events)} events")
            return ctx.events
            
        except Exception as e:
            # Rollback on any failure
            logger.error(f"❌ Plan execution failed: {e}")
            store.rollback(tx)
            
            duration_ms = (time.time() - start_time) * 1000
            log_plan_execution(
                trace.trace_id,
                len(tool_calls),
                len([r for r in ctx.results if r.success]),
                len([r for r in ctx.results if not r.success]) + 1,
                duration_ms,
            )
            
            # Return partial events with error marker
            ctx.events.append({
                "type": "error",
                "tool": ctx.failed_tools[-1] if ctx.failed_tools else "unknown",
                "error": str(e),
            })
            return ctx.events


class ExecutionError(Exception):
    """Raised when execution fails critically."""
    pass


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
        
        elif call.name == "stori_set_key_signature":
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
        result = await mg.generate(**gen_params)
        
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
            "backend": result.backend_used.value,
        })
        
    except Exception as e:
        logger.exception(f"❌ Generator error: {e}")
        ctx.add_result(name, False, {}, str(e))


# =============================================================================
# Streaming Execution
# =============================================================================

async def execute_plan_streaming(
    tool_calls: list[ToolCall],
    project_state: dict[str, Any],
    conversation_id: Optional[str] = None,
    store: Optional[StateStore] = None,
) -> AsyncIterator[dict[str, Any]]:
    """
    Execute a plan and yield events as they're generated.
    
    Provides incremental feedback for SSE streaming.
    
    Yields:
        dict: Events to send to client
    """
    trace = get_trace_context()
    start_time = time.time()
    
    # Get or create StateStore
    if store is None:
        store = get_or_create_store(
            conversation_id=conversation_id or "default",
            project_id=project_state.get("projectId"),
        )
    
    store.sync_from_client(project_state)
    tool_calls = dedupe_tool_calls(tool_calls)
    
    if not tool_calls:
        yield {"type": "plan_complete", "success": True, "total_events": 0}
        return
    
    # Begin transaction
    tx = store.begin_transaction(f"Stream {len(tool_calls)} tools")
    
    ctx = ExecutionContext(
        store=store,
        transaction=tx,
        trace=trace,
    )
    
    logger.info(f"🎬 Streaming execution: {len(tool_calls)} tool calls")
    
    try:
        for i, call in enumerate(tool_calls):
            # Yield progress
            yield {
                "type": "plan_progress",
                "step": i + 1,
                "total": len(tool_calls),
                "tool": call.name,
            }
            
            # Execute and collect events
            events_before = len(ctx.events)
            await _execute_single_call(call, ctx)
            
            # Yield new events
            for event in ctx.events[events_before:]:
                yield {
                    "type": "tool_call",
                    "name": event.get("tool"),
                    "params": event.get("params", {}),
                }
            
            # Check for failure
            if ctx.results and not ctx.results[-1].success:
                error = ctx.results[-1].error
                yield {
                    "type": "tool_error",
                    "name": call.name,
                    "error": error,
                }
                
                # For entity creation failures, abort
                meta = get_tool_meta(call.name)
                if meta and meta.creates_entity:
                    raise ExecutionError(error)
        
        # Commit transaction
        store.commit(tx)
        
        duration_ms = (time.time() - start_time) * 1000
        log_plan_execution(
            trace.trace_id,
            len(tool_calls),
            len([r for r in ctx.results if r.success]),
            len(ctx.failed_tools),
            duration_ms,
        )
        
        yield {
            "type": "plan_complete",
            "success": ctx.all_successful,
            "total_events": len(ctx.events),
            "failed_tools": ctx.failed_tools,
            "state_version": store.version,
        }
        
    except Exception as e:
        logger.error(f"❌ Streaming execution failed: {e}")
        store.rollback(tx)
        
        yield {
            "type": "plan_complete",
            "success": False,
            "total_events": len(ctx.events),
            "failed_tools": ctx.failed_tools,
            "error": str(e),
        }


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
    
    def capture_base_notes(self, region_id: str, track_id: str, notes: list[dict]) -> None:
        """Capture base notes for a region before transformation."""
        if region_id not in self.base_notes:
            self.base_notes[region_id] = [_normalize_note(n) for n in notes]
            self.track_regions[region_id] = track_id
    
    def record_proposed_notes(self, region_id: str, notes: list[dict]) -> None:
        """Record proposed notes for a region after transformation."""
        self.proposed_notes[region_id] = [_normalize_note(n) for n in notes]


async def execute_plan_variation(
    tool_calls: list[ToolCall],
    project_state: dict[str, Any],
    intent: str,
    conversation_id: Optional[str] = None,
    explanation: Optional[str] = None,
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
            project_id=project_state.get("projectId"),
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
        
        # Process tool calls to collect proposed notes
        for i, call in enumerate(tool_calls):
            logger.info(f"🔧 Processing call {i + 1}/{len(tool_calls)}: {call.name}")
            await _process_call_for_variation(call, var_ctx)
        
        # Log what we captured
        total_base = sum(len(n) for n in var_ctx.base_notes.values())
        total_proposed = sum(len(n) for n in var_ctx.proposed_notes.values())
        logger.info(
            f"📊 Variation context: {len(var_ctx.base_notes)} base regions ({total_base} notes), "
            f"{len(var_ctx.proposed_notes)} proposed regions ({total_proposed} notes)"
        )
        
        # Compute variation using variation service
        variation_service = get_variation_service()
        
        # If we have multiple regions, use multi-region variation
        if len(var_ctx.proposed_notes) > 1:
            # Group by track
            track_id = next(iter(var_ctx.track_regions.values()), "unknown")
            variation = variation_service.compute_multi_region_variation(
                base_regions=var_ctx.base_notes,
                proposed_regions=var_ctx.proposed_notes,
                track_id=track_id,
                intent=intent,
                explanation=explanation,
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
    """Extract existing notes from project state into variation context."""
    tracks = project_state.get("tracks", [])
    
    for track in tracks:
        track_id = track.get("id", "")
        regions = track.get("midiRegions", [])
        
        for region in regions:
            region_id = region.get("id", "")
            notes = region.get("notes", [])
            
            if region_id and notes:
                var_ctx.capture_base_notes(region_id, track_id, notes)


async def _process_call_for_variation(
    call: ToolCall,
    var_ctx: VariationContext,
) -> None:
    """
    Process a tool call to extract proposed notes for variation.
    
    Simulates entity creation (tracks, regions) in the state store so that
    subsequent generator calls can resolve track/region names. Does NOT
    mutate canonical state — uses the store's registry for name resolution.
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
    # -----------------------------------------------------------------
    if call.name in ("stori_add_midi_track", "stori_add_track"):
        track_name = params.get("name", "Track")
        existing = var_ctx.store.registry.resolve_track(track_name)
        if not existing:
            track_id = var_ctx.store.create_track(track_name, track_id=params.get("trackId"))
            logger.info(f"🎹 [variation] Registered track: {track_name} → {track_id[:8]}")
        else:
            logger.debug(f"🎹 [variation] Track already exists: {track_name} → {existing[:8]}")

    elif call.name in ("stori_add_midi_region", "stori_add_region"):
        track_id = params.get("trackId", "")
        if not track_id:
            track_ref = params.get("trackName") or params.get("name")
            if track_ref:
                track_id = var_ctx.store.registry.resolve_track(track_ref) or ""
        if track_id:
            region_name = params.get("name", "Region")
            region_id = var_ctx.store.create_region(
                name=region_name,
                parent_track_id=track_id,
                region_id=params.get("regionId"),
                metadata={
                    "startBeat": params.get("startBeat", 0),
                    "durationBeats": params.get("durationBeats", 16),
                },
            )
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
        
        if region_id and notes:
            if region_id not in var_ctx.base_notes:
                var_ctx.capture_base_notes(region_id, track_id, [])
            
            var_ctx.record_proposed_notes(region_id, notes)
            logger.info(
                f"📝 stori_add_notes: {len(notes)} notes for region={region_id[:8]}"
            )
        elif not region_id:
            logger.warning(
                f"⚠️ stori_add_notes: missing regionId, dropping {len(notes)} notes"
            )
    
    # Handle generators - they produce notes that go to a region
    meta = get_tool_meta(call.name)
    if meta and meta.tier == ToolTier.TIER1 and meta.kind == ToolKind.GENERATOR:
        # Simulate generator execution to get proposed notes
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
            result = await mg.generate(**gen_params)
            
            if result.success and result.notes:
                logger.info(
                    f"🎵 Generator {call.name} produced {len(result.notes)} notes"
                )
                # Find target region
                track_name = params.get("trackName", gen_params["instrument"].capitalize())
                track_id = var_ctx.store.registry.resolve_track(track_name) or params.get("trackId", "")
                
                if track_id:
                    region_id = var_ctx.store.registry.get_latest_region_for_track(track_id)
                    
                    if region_id:
                        if region_id not in var_ctx.base_notes:
                            var_ctx.capture_base_notes(region_id, track_id, [])
                        
                        var_ctx.record_proposed_notes(region_id, result.notes)
                        logger.info(
                            f"📝 Recorded {len(result.notes)} proposed notes for "
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
                        
        except Exception as e:
            logger.exception(
                f"⚠️ Generator simulation failed for {call.name}: {e}"
            )


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
            project_id=project_state.get("projectId"),
        )
        store.sync_from_client(project_state)
        
        try:
            notes_added = 0
            notes_removed = 0
            notes_modified = 0
            applied_phrases = []

            # Group adds and removals by region
            region_adds: dict[str, list[dict]] = {}
            region_removals: dict[str, list[dict]] = {}

            # Process phrases in sequence order (accepted_phrase_ids order)
            for phrase_id in accepted_phrase_ids:
                phrase = variation.get_phrase(phrase_id)
                if not phrase:
                    logger.warning(f"Phrase {phrase_id[:8]} not found in variation")
                    continue

                region_id = phrase.region_id

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
                        # Modified = remove old note + add new note
                        if nc.before:
                            region_removals[region_id].append(nc.before.to_note_dict())
                        if nc.after:
                            region_adds[region_id].append(nc.after.to_note_dict())

                applied_phrases.append(phrase_id)

            # Apply changes in a single transaction: removals first, then adds
            tx = store.begin_transaction(f"Accept Variation: {len(accepted_phrase_ids)} phrases")

            for region_id, criteria in region_removals.items():
                if criteria:
                    store.remove_notes(region_id, criteria, transaction=tx)

            for region_id, notes in region_adds.items():
                if notes:
                    store.add_notes(region_id, notes, transaction=tx)

            store.commit(tx)

            # Build updated_regions: full note state for every affected region
            affected_region_ids = set(region_adds.keys()) | set(region_removals.keys())
            updated_regions: list[dict[str, Any]] = []
            for rid in sorted(affected_region_ids):
                track_id = store.get_region_track_id(rid)
                updated_regions.append({
                    "region_id": rid,
                    "track_id": track_id or "",
                    "notes": store.get_region_notes(rid),
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
