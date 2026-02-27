"""Persistent StateStore for Maestro — Maestro's in-memory working tree.

Boundary rules (enforced by convention, verified in tests):

StateStore MAY:
    - Maintain the mutable working tree (tracks, regions, notes, buses).
    - Accept mutations from tool-call execution (create_track, add_notes, …).
    - Resolve entity names to IDs via EntityRegistry.
    - Provide versioned state via ``get_state_id()``.
    - Support transactions with rollback for plan execution.
    - Sync from the DAW via ``sync_from_client()``.
    - Provide immutable snapshots via ``get_region_notes()`` (returns deepcopy).

StateStore MUST NOT:
    - Be accessed directly by Muse commit logic.  Muse receives snapshots
      via ``capture_base_snapshot`` / ``capture_proposed_snapshot`` from
      ``app.core.executor.snapshots``, never live store references.
    - Be treated as an immutable base state.  It is mutable — callers must
      snapshot before mutation.
    - Store variation or phrase data (that belongs to VariationStore).
    - Be shared across requests via conversation_id for Muse's benefit.

StateStore IS:
    A per-request working tree, a mutable scratchpad for tool-call execution,
    a derived view of the DAW's state (via sync), and a source of snapshots
    that Muse consumes (via explicit capture).

StateStore IS NOT:
    A persistent store, an authority on musical history, a replacement for a
    Muse repository, or a shared state bus between Maestro and Muse.

Architecture:
    StateStore (persistent per session, versioned)
        └── EntityRegistry (derived view, fast lookups)
        └── EventLog (append-only mutation history)
        └── Snapshots (periodic full state captures)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from copy import deepcopy
from typing_extensions import TypedDict

from maestro.contracts.project_types import ProjectContext
from maestro.contracts.json_types import (
    AftertouchDict,
    CCEventDict,
    InternalNoteDict,
    JSONValue,
    NoteDict,
    PitchBendDict,
    RegionAftertouchMap,
    RegionCCMap,
    RegionNotesMap,
    RegionPitchBendMap,
    StateEventData,
)
from maestro.core.entity_registry import EntityMetadata, EntityRegistry, EntityInfo, EntityType

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Types of state mutation events."""
    # Entity creation
    TRACK_CREATED = "track.created"
    REGION_CREATED = "region.created"
    BUS_CREATED = "bus.created"
    
    # Entity modification
    TRACK_MODIFIED = "track.modified"
    REGION_MODIFIED = "region.modified"
    NOTES_ADDED = "notes.added"
    NOTES_REMOVED = "notes.removed"
    EFFECT_ADDED = "effect.added"
    
    # Entity deletion
    TRACK_DELETED = "track.deleted"
    REGION_DELETED = "region.deleted"
    
    # Project-level
    TEMPO_CHANGED = "project.tempo_changed"
    KEY_CHANGED = "project.key_changed"
    
    # Transaction markers
    TRANSACTION_START = "transaction.start"
    TRANSACTION_COMMIT = "transaction.commit"
    TRANSACTION_ROLLBACK = "transaction.rollback"


@dataclass
class StateEvent:
    """A single state mutation event."""
    id: str
    event_type: EventType
    entity_type: EntityType | None
    entity_id: str | None
    data: StateEventData
    timestamp: datetime
    version: int
    transaction_id: str | None = None
    
    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "event_type": self.event_type.value,
            "entity_type": self.entity_type.value if self.entity_type else None,
            "entity_id": self.entity_id,
            "data": dict(self.data),
            "timestamp": self.timestamp.isoformat(),
            "version": self.version,
            "transaction_id": self.transaction_id,
        }


@dataclass
class Transaction:
    """A group of events that should succeed or fail together."""
    id: str
    started_at: datetime
    events: list[StateEvent] = field(default_factory=list)
    committed: bool = False
    rolled_back: bool = False
    
    @property
    def is_active(self) -> bool:
        return not self.committed and not self.rolled_back


class _ProjectMetadataSnapshot(TypedDict, total=False):
    """Versioned musical-metadata slice stored inside ``StateSnapshot.project_metadata``.

    All fields are optional (``total=False``) — a snapshot only captures
    what has been set on the ``StateStore`` at that version.  Absent fields
    should be treated as "unchanged from the previous snapshot".

    Attributes:
        tempo: Project tempo in BPM (whole integer — see Tempo Convention in
            ``docs/reference/type_contracts.md``).
        key: Root key string, e.g. ``"Am"`` or ``"C#"``.
        time_signature: ``(numerator, denominator)`` tuple, e.g. ``(4, 4)``.
        _region_notes: Snapshot of all MIDI notes per region ID at this version.
        _region_cc: Snapshot of all MIDI CC events per region ID.
        _region_pitch_bends: Snapshot of all pitch bend events per region ID.
        _region_aftertouch: Snapshot of all aftertouch events per region ID.
    """

    tempo: int
    key: str
    time_signature: tuple[int, int]
    _region_notes: RegionNotesMap
    _region_cc: RegionCCMap
    _region_pitch_bends: RegionPitchBendMap
    _region_aftertouch: RegionAftertouchMap


@dataclass
class StateSnapshot:
    """A full snapshot of state at a specific version."""
    version: int
    timestamp: datetime
    registry_data: dict[str, object]
    project_metadata: _ProjectMetadataSnapshot


_CAMEL_TO_SNAKE: dict[str, str] = {
    "startBeat": "start_beat",
    "durationBeats": "duration_beats",
}


def _normalize_note(note: NoteDict | InternalNoteDict) -> InternalNoteDict:
    """Normalize a note dict to internal snake_case keys.

    Tool calls from the LLM use camelCase (startBeat, durationBeats).
    Internal storage always uses snake_case.  Explicit per-field extraction
    keeps mypy satisfied without a cast or type: ignore.
    """
    result: InternalNoteDict = {}

    pitch = note.get("pitch")
    if pitch is not None:
        result["pitch"] = pitch

    velocity = note.get("velocity")
    if velocity is not None:
        result["velocity"] = velocity

    channel = note.get("channel")
    if channel is not None:
        result["channel"] = channel

    layer = note.get("layer")
    if layer is not None:
        result["layer"] = layer

    note_id = note.get("noteId")
    if note_id is not None:
        result["noteId"] = note_id
    note_id_snake = note.get("note_id")
    if note_id_snake is not None:
        result["note_id"] = note_id_snake

    track_id = note.get("trackId")
    if track_id is not None:
        result["trackId"] = track_id
    track_id_snake = note.get("track_id")
    if track_id_snake is not None:
        result["track_id"] = track_id_snake

    region_id = note.get("regionId")
    if region_id is not None:
        result["regionId"] = region_id
    region_id_snake = note.get("region_id")
    if region_id_snake is not None:
        result["region_id"] = region_id_snake

    # Timing: prefer snake_case; fall back to camelCase alias
    start_beat = note.get("start_beat")
    if start_beat is None:
        start_beat = note.get("startBeat")
    if start_beat is not None:
        result["start_beat"] = start_beat

    duration_beats = note.get("duration_beats")
    if duration_beats is None:
        duration_beats = note.get("durationBeats")
    if duration_beats is not None:
        result["duration_beats"] = duration_beats

    return result


def _notes_match(existing: InternalNoteDict, criteria: InternalNoteDict) -> bool:
    """Check if an existing note matches removal criteria.

    Matching on pitch + start_beat + duration_beats (snake_case only).
    """
    _TOL = 1e-6
    if existing.get("pitch") != criteria.get("pitch"):
        return False
    if abs(existing.get("start_beat", 0) - criteria.get("start_beat", 0)) > _TOL:
        return False
    if abs(existing.get("duration_beats", 0) - criteria.get("duration_beats", 0)) > _TOL:
        return False
    return True


@dataclass
class CompositionState:
    """Tracks evolving Orpheus composition state across sections and instruments.

    Stored per-composition in StateStore so the Maestro memory layer can
    pass session continuity information to the Orpheus music service.
    This is the architectural hook for future direct token-state persistence.
    """
    composition_id: str
    session_id: str
    accumulated_midi_path: str | None = None
    last_token_estimate: int = 0
    created_at: float = 0.0
    call_count: int = 0


class StateStore:
    """
    Persistent, versioned state store for a project/conversation.
    
    Provides:
    1. Versioned state with rollback capability
    2. Transaction support for atomic multi-step operations
    3. Event log for audit trail
    4. Fast entity lookups via derived EntityRegistry
    5. Composition state tracking for Orpheus session continuity
    
    Usage:
        store = StateStore(conversation_id="abc-123")
        
        # Start a transaction
        tx = store.begin_transaction()
        
        try:
            track_id = store.create_track("Drums", transaction=tx)
            region_id = store.create_region("Pattern 1", track_id, transaction=tx)
            store.commit(tx)
        except Exception:
            store.rollback(tx)
        
        # Lookup entities
        track = store.registry.resolve_track("drums")
    """
    
    def __init__(
        self,
        conversation_id: str | None = None,
        project_id: str | None = None,
    ):
        self.conversation_id = conversation_id or str(uuid.uuid4())
        self.project_id = project_id or str(uuid.uuid4())
        
        # Core state
        self._registry = EntityRegistry(project_id=self.project_id)
        self._version: int = 0
        self._events: list[StateEvent] = []
        self._snapshots: list[StateSnapshot] = []
        self._active_transaction: Transaction | None = None
        
        # Materialized note store: region_id -> list of note dicts
        # Maintained by add_notes/remove_notes; queryable after commit
        self._region_notes: RegionNotesMap = {}

        # MIDI CC, pitch bend, and aftertouch stores: region_id -> list of event dicts
        self._region_cc: RegionCCMap = {}
        self._region_pitch_bends: RegionPitchBendMap = {}
        self._region_aftertouch: RegionAftertouchMap = {}

        # Orpheus composition state: composition_id -> CompositionState
        self._composition_states: dict[str, CompositionState] = {}
        
        # Project metadata
        self._tempo: int = 120
        self._key: str = "C"
        self._time_signature: tuple[int, int] = (4, 4)
        
        logger.debug(f"🏗️ StateStore initialized: conv={self.conversation_id[:8]}, proj={self.project_id[:8]}")
    
    @property
    def registry(self) -> EntityRegistry:
        """Get the derived EntityRegistry for fast lookups."""
        return self._registry
    
    @property
    def version(self) -> int:
        """Current state version."""
        return self._version
    
    @property
    def tempo(self) -> int:
        """Current project tempo in BPM (whole integer).

        Starts at 120. Updated via :meth:`set_tempo`. Coerced to ``int``
        at the DAW→Maestro boundary — see the Tempo Convention in
        ``docs/reference/type_contracts.md``.
        """
        return self._tempo

    @property
    def key(self) -> str:
        """Current project key signature (e.g. ``"Am"``, ``"C#"``, ``"Bb"``).

        Starts at ``"C"``. Updated via :meth:`set_key`.
        """
        return self._key

    @property
    def time_signature(self) -> tuple[int, int]:
        """Current project time signature as ``(numerator, denominator)``.

        Starts at ``(4, 4)``.  The denominator is always a power of two
        (2, 4, 8, 16).  Updated only when the DAW explicitly sends a
        ``timeSignature`` in the project context snapshot.
        """
        return self._time_signature

    # =========================================================================
    # Transaction Management
    # =========================================================================
    
    def begin_transaction(self, description: str = "") -> Transaction:
        """
        Begin a new transaction.
        
        All mutations within a transaction are atomic - they either
        all succeed or all fail.
        """
        if self._active_transaction and self._active_transaction.is_active:
            raise RuntimeError("Transaction already active. Commit or rollback first.")
        
        # Take snapshot FIRST, before any transaction events
        # This captures the state we'll restore to on rollback
        self._take_snapshot()
        
        tx = Transaction(
            id=str(uuid.uuid4()),
            started_at=datetime.now(timezone.utc),
        )
        self._active_transaction = tx
        
        # Record transaction start AFTER snapshot
        self._append_event(
            event_type=EventType.TRANSACTION_START,
            entity_type=None,
            entity_id=None,
            data=StateEventData(description=description),
            transaction=tx,
        )
        
        logger.info(f"🔒 Transaction started: {tx.id[:8]}")
        return tx
    
    def commit(self, transaction: Transaction) -> None:
        """Commit a transaction, making all changes permanent."""
        active = self._active_transaction
        if active is None:
            raise ValueError("No active transaction")
        if transaction.id != active.id:
            raise ValueError("Cannot commit a transaction that is not active")
        
        if not transaction.is_active:
            raise ValueError("Transaction is not active")
        
        # Record commit event
        self._append_event(
            event_type=EventType.TRANSACTION_COMMIT,
            entity_type=None,
            entity_id=None,
            data=StateEventData(event_count=len(transaction.events)),
            transaction=transaction,
        )
        
        transaction.committed = True
        self._active_transaction = None
        
        logger.info(f"✅ Transaction committed: {transaction.id[:8]} ({len(transaction.events)} events)")
    
    def rollback(self, transaction: Transaction) -> None:
        """Rollback a transaction, reverting all changes."""
        active = self._active_transaction
        if active is None:
            raise ValueError("No active transaction")
        if transaction.id != active.id:
            raise ValueError("Cannot rollback a transaction that is not active")
        
        if not transaction.is_active:
            raise ValueError("Transaction is not active")
        
        # Find the snapshot before this transaction
        rollback_snapshot = None
        for snapshot in reversed(self._snapshots):
            if snapshot.version < transaction.events[0].version if transaction.events else self._version:
                rollback_snapshot = snapshot
                break
        
        if rollback_snapshot:
            self._restore_snapshot(rollback_snapshot)
        
        # Remove transaction events
        self._events = [e for e in self._events if e.transaction_id != transaction.id]
        
        # Record rollback event
        self._append_event(
            event_type=EventType.TRANSACTION_ROLLBACK,
            entity_type=None,
            entity_id=None,
            data=StateEventData(rolled_back_events=len(transaction.events)),
            transaction=None,
        )
        
        transaction.rolled_back = True
        self._active_transaction = None
        
        logger.warning(f"⏪ Transaction rolled back: {transaction.id[:8]} ({len(transaction.events)} events reverted)")
    
    # =========================================================================
    # Entity Creation (with event sourcing)
    # =========================================================================
    
    def create_track(
        self,
        name: str,
        track_id: str | None = None,
        metadata: EntityMetadata | dict[str, JSONValue] | None = None,
        transaction: Transaction | None = None,
    ) -> str:
        """Create a new track and record the event."""
        track_id = self._registry.create_track(name, track_id, metadata)
        
        _raw_meta = metadata.to_dict() if isinstance(metadata, EntityMetadata) else (metadata or {})
        meta_dict: dict[str, JSONValue] = dict(_raw_meta)
        self._append_event(
            event_type=EventType.TRACK_CREATED,
            entity_type=EntityType.TRACK,
            entity_id=track_id,
            data=StateEventData(name=name, metadata=meta_dict),
            transaction=transaction or self._active_transaction,
        )
        
        return track_id
    
    def create_region(
        self,
        name: str,
        parent_track_id: str,
        region_id: str | None = None,
        metadata: EntityMetadata | dict[str, JSONValue] | None = None,
        transaction: Transaction | None = None,
    ) -> str:
        """Create a new region and record the event."""
        region_id = self._registry.create_region(name, parent_track_id, region_id, metadata)
        
        _raw_region_meta = metadata.to_dict() if isinstance(metadata, EntityMetadata) else (metadata or {})
        region_meta: dict[str, JSONValue] = dict(_raw_region_meta)
        self._append_event(
            event_type=EventType.REGION_CREATED,
            entity_type=EntityType.REGION,
            entity_id=region_id,
            data=StateEventData(
                name=name,
                parent_track_id=parent_track_id,
                metadata=region_meta,
            ),
            transaction=transaction or self._active_transaction,
        )
        
        return region_id
    
    def create_bus(
        self,
        name: str,
        bus_id: str | None = None,
        metadata: EntityMetadata | dict[str, JSONValue] | None = None,
        transaction: Transaction | None = None,
    ) -> str:
        """Create a new bus and record the event."""
        bus_id = self._registry.create_bus(name, bus_id, metadata)
        
        _raw_bus_meta = metadata.to_dict() if isinstance(metadata, EntityMetadata) else (metadata or {})
        bus_meta: dict[str, JSONValue] = dict(_raw_bus_meta)
        self._append_event(
            event_type=EventType.BUS_CREATED,
            entity_type=EntityType.BUS,
            entity_id=bus_id,
            data=StateEventData(name=name, metadata=bus_meta),
            transaction=transaction or self._active_transaction,
        )
        
        return bus_id
    
    def get_or_create_bus(
        self,
        name: str,
        transaction: Transaction | None = None,
    ) -> str:
        """Get existing bus by name or create a new one."""
        existing = self._registry.resolve_bus(name)
        if existing:
            return existing
        return self.create_bus(name, transaction=transaction)
    
    # =========================================================================
    # State Modification
    # =========================================================================
    
    def set_tempo(
        self,
        tempo: int,
        transaction: Transaction | None = None,
    ) -> None:
        """Set the project tempo and append a ``TEMPO_CHANGED`` event to the log.

        Args:
            tempo: New tempo in BPM (whole integer; see Tempo Convention).
            transaction: Optional active transaction.  When provided, the event
                is tagged with the transaction so it can be rolled back atomically.
        """
        old_tempo = self._tempo
        self._tempo = tempo
        
        self._append_event(
            event_type=EventType.TEMPO_CHANGED,
            entity_type=None,
            entity_id=None,
            data=StateEventData(old_tempo=old_tempo, new_tempo=tempo),
            transaction=transaction or self._active_transaction,
        )
    
    def set_key(
        self,
        key: str,
        transaction: Transaction | None = None,
    ) -> None:
        """Set the project key signature and append a ``KEY_CHANGED`` event to the log.

        Args:
            key: New key string (e.g. ``"Am"``, ``"F#"``).
            transaction: Optional active transaction for atomic rollback.
        """
        old_key = self._key
        self._key = key
        
        self._append_event(
            event_type=EventType.KEY_CHANGED,
            entity_type=None,
            entity_id=None,
            data=StateEventData(old_key=old_key, new_key=key),
            transaction=transaction or self._active_transaction,
        )
    
    def add_notes(
        self,
        region_id: str,
        notes: list[NoteDict],
        transaction: Transaction | None = None,
    ) -> None:
        """Add notes to a region (event + materialized view).

        Notes are normalized to snake_case keys on ingress so internal
        storage is always consistent regardless of wire format.
        """
        normalized = [_normalize_note(n) for n in notes]
        if region_id not in self._region_notes:
            self._region_notes[region_id] = []
        self._region_notes[region_id].extend(deepcopy(normalized))
        
        self._append_event(
            event_type=EventType.NOTES_ADDED,
            entity_type=EntityType.REGION,
            entity_id=region_id,
            data=StateEventData(notes_count=len(notes), notes=notes),
            transaction=transaction or self._active_transaction,
        )
    
    def remove_notes(
        self,
        region_id: str,
        note_criteria: list[InternalNoteDict],
        transaction: Transaction | None = None,
    ) -> None:
        """
        Remove notes from a region (event + materialized view).

        note_criteria is a list of dicts identifying notes to remove.
        Matching uses pitch + start_beat + duration_beats.
        """
        if region_id in self._region_notes:
            for criteria in note_criteria:
                self._region_notes[region_id] = [
                    n for n in self._region_notes[region_id]
                    if not _notes_match(n, criteria)
                ]
        
        self._append_event(
            event_type=EventType.NOTES_REMOVED,
            entity_type=EntityType.REGION,
            entity_id=region_id,
            data=StateEventData(notes_count=len(note_criteria)),
            transaction=transaction or self._active_transaction,
        )

    def add_effect(
        self,
        track_id: str,
        effect_type: str,
        transaction: Transaction | None = None,
    ) -> None:
        """Record effect added to a track."""
        self._append_event(
            event_type=EventType.EFFECT_ADDED,
            entity_type=EntityType.TRACK,
            entity_id=track_id,
            data=StateEventData(effect_type=effect_type),
            transaction=transaction or self._active_transaction,
        )
    
    # =========================================================================
    # Region Note Queries
    # =========================================================================
    
    def get_region_notes(self, region_id: str) -> list[InternalNoteDict]:
        """Return the current materialized note list for a region."""
        return deepcopy(self._region_notes.get(region_id, []))
    
    def get_region_track_id(self, region_id: str) -> str | None:
        """Return the parent track ID for a region (from registry)."""
        entity = self._registry.get_region(region_id)
        return entity.parent_id if entity else None

    def get_track_name(self, track_id: str) -> str | None:
        """Return the name of a track by ID, or None if not found."""
        entity = self._registry.get_track(track_id)
        return entity.name if entity else None

    # =========================================================================
    # MIDI CC and Pitch Bend
    # =========================================================================

    def add_cc(
        self,
        region_id: str,
        cc_events: list[CCEventDict],
    ) -> None:
        """Append MIDI CC events to a region."""
        if region_id not in self._region_cc:
            self._region_cc[region_id] = []
        self._region_cc[region_id].extend(deepcopy(cc_events))

    def get_region_cc(self, region_id: str) -> list[CCEventDict]:
        """Return CC events for a region."""
        return deepcopy(self._region_cc.get(region_id, []))

    def add_pitch_bends(
        self,
        region_id: str,
        pitch_bends: list[PitchBendDict],
    ) -> None:
        """Append pitch bend events to a region."""
        if region_id not in self._region_pitch_bends:
            self._region_pitch_bends[region_id] = []
        self._region_pitch_bends[region_id].extend(deepcopy(pitch_bends))

    def get_region_pitch_bends(self, region_id: str) -> list[PitchBendDict]:
        """Return pitch bend events for a region."""
        return deepcopy(self._region_pitch_bends.get(region_id, []))

    def add_aftertouch(
        self,
        region_id: str,
        aftertouch: list[AftertouchDict],
    ) -> None:
        """Append aftertouch events (channel or poly) to a region."""
        if region_id not in self._region_aftertouch:
            self._region_aftertouch[region_id] = []
        self._region_aftertouch[region_id].extend(deepcopy(aftertouch))

    def get_region_aftertouch(self, region_id: str) -> list[AftertouchDict]:
        """Return aftertouch events for a region."""
        return deepcopy(self._region_aftertouch.get(region_id, []))

    # =========================================================================
    # Composition State (Orpheus session continuity)
    # =========================================================================

    def get_composition_state(self, composition_id: str) -> CompositionState | None:
        """Return the composition state for a given composition, if any."""
        return self._composition_states.get(composition_id)

    def update_composition_state(
        self,
        composition_id: str,
        session_id: str,
        token_estimate: int = 0,
        midi_path: str | None = None,
    ) -> CompositionState:
        """Create or update the composition state for session continuity."""
        import time as _time
        state = self._composition_states.get(composition_id)
        if state is None:
            state = CompositionState(
                composition_id=composition_id,
                session_id=session_id,
                created_at=_time.time(),
            )
            self._composition_states[composition_id] = state
        state.session_id = session_id
        state.last_token_estimate = token_estimate
        state.call_count += 1
        if midi_path:
            state.accumulated_midi_path = midi_path
        return state

    # =========================================================================
    # Synchronization
    # =========================================================================
    
    def sync_from_client(self, project_state: ProjectContext) -> None:
        """
        Sync with client-reported project state.

        REPLACES the registry and note store with the client's snapshot so
        that no stale entities survive across project switches or deletions.
        The client ``project`` field is the sole source of truth.
        """
        # Clear stale state before rebuilding
        self._registry.clear()

        # Preserve existing region notes — the client may report regions
        # without a notes array (only note_count), so we keep what we had
        # from prior tool calls or syncs.
        previous_notes = dict(self._region_notes)
        self._region_notes.clear()

        self._registry.sync_from_project_state(project_state)

        # Initialize region notes from client-reported state
        for track in project_state.get("tracks", []):
            for region in track.get("regions", []):
                region_id = region.get("id")
                if region_id:
                    if "notes" in region:
                        # Client explicitly sent notes (even if empty) — use them
                        self._region_notes[region_id] = deepcopy(region["notes"])
                    elif region_id in previous_notes:
                        # Client reported region but omitted notes — keep prior data
                        self._region_notes[region_id] = previous_notes[region_id]

        # Update project metadata
        if "tempo" in project_state:
            self._tempo = int(project_state["tempo"])
        if "key" in project_state:
            self._key = project_state["key"]
        ts_raw = project_state.get("timeSignature")
        if isinstance(ts_raw, str):
            parts = ts_raw.split("/")
            if len(parts) == 2:
                self._time_signature = (int(parts[0]), int(parts[1]))
        elif isinstance(ts_raw, dict):
            self._time_signature = (ts_raw["numerator"], ts_raw["denominator"])
    
    # =========================================================================
    # Event & Snapshot Management
    # =========================================================================
    
    def _append_event(
        self,
        event_type: EventType,
        entity_type: EntityType | None,
        entity_id: str | None,
        data: StateEventData,
        transaction: Transaction | None,
    ) -> StateEvent:
        """Append an event to the log."""
        self._version += 1
        
        event = StateEvent(
            id=str(uuid.uuid4()),
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            data=data,
            timestamp=datetime.now(timezone.utc),
            version=self._version,
            transaction_id=transaction.id if transaction else None,
        )
        
        self._events.append(event)
        
        if transaction and transaction.is_active:
            transaction.events.append(event)
        
        return event
    
    def _take_snapshot(self) -> StateSnapshot:
        """Take a snapshot of current state (including region notes)."""
        snapshot = StateSnapshot(
            version=self._version,
            timestamp=datetime.now(timezone.utc),
            registry_data=self._registry.to_dict(),
            project_metadata=_ProjectMetadataSnapshot(
                tempo=self._tempo,
                key=self._key,
                time_signature=self._time_signature,
                _region_notes=deepcopy(self._region_notes),
                _region_cc=deepcopy(self._region_cc),
                _region_pitch_bends=deepcopy(self._region_pitch_bends),
                _region_aftertouch=deepcopy(self._region_aftertouch),
            ),
        )
        self._snapshots.append(snapshot)
        
        # Keep only last 10 snapshots
        if len(self._snapshots) > 10:
            self._snapshots = self._snapshots[-10:]
        
        return snapshot
    
    def _restore_snapshot(self, snapshot: StateSnapshot) -> None:
        """Restore state from a snapshot (including region notes)."""
        self._registry = EntityRegistry.from_dict(snapshot.registry_data)
        meta = snapshot.project_metadata
        self._tempo = meta.get("tempo", 120)
        self._key = meta.get("key", "C")
        self._time_signature = meta.get("time_signature", (4, 4))
        self._region_notes = deepcopy(meta.get("_region_notes", {}))
        self._region_cc = deepcopy(meta.get("_region_cc", {}))
        self._region_pitch_bends = deepcopy(meta.get("_region_pitch_bends", {}))
        self._region_aftertouch = deepcopy(meta.get("_region_aftertouch", {}))
        
        logger.info(f"📸 Restored to snapshot v{snapshot.version}")
    
    # =========================================================================
    # Serialization
    # =========================================================================
    
    def to_dict(self) -> dict[str, object]:
        """Serialize entire store state."""
        return {
            "conversation_id": self.conversation_id,
            "project_id": self.project_id,
            "version": self._version,
            "registry": self._registry.to_dict(),
            "events": [e.to_dict() for e in self._events[-100:]],
            "project_metadata": {
                "tempo": self._tempo,
                "key": self._key,
                "time_signature": list(self._time_signature),
            },
        }
    
    def get_events_since(self, version: int) -> list[StateEvent]:
        """Get all events since a specific version (for sync)."""
        return [e for e in self._events if e.version > version]
    
    def get_entity_events(self, entity_id: str) -> list[StateEvent]:
        """Get all events for a specific entity (for audit)."""
        return [e for e in self._events if e.entity_id == entity_id]
    
    def get_state_id(self) -> str:
        """
        Get the current state ID for optimistic concurrency.
        
        Returns the version as a string to match the spec's base_state_id format.
        """
        return str(self._version)
    
    def check_state_id(self, expected_state_id: str) -> bool:
        """
        Check if the current state matches the expected state ID.
        
        Used for optimistic concurrency control in variation commits.
        
        Args:
            expected_state_id: The expected state version
            
        Returns:
            True if state matches, False otherwise
        """
        try:
            expected_version = int(expected_state_id)
            return self._version == expected_version
        except ValueError:
            logger.warning(f"Invalid state_id format: {expected_state_id}")
            return False


# =============================================================================
# Store Registry (conversation_id -> StateStore)
# =============================================================================

_stores: dict[str, StateStore] = {}


def get_or_create_store(
    conversation_id: str,
    project_id: str | None = None,
) -> StateStore:
    """
    Get existing store for conversation or create new one.
    
    This is the primary way to get a StateStore.
    """
    if conversation_id in _stores:
        return _stores[conversation_id]
    
    store = StateStore(conversation_id=conversation_id, project_id=project_id)
    _stores[conversation_id] = store
    
    return store


def clear_store(conversation_id: str) -> None:
    """Remove a store from the registry."""
    if conversation_id in _stores:
        del _stores[conversation_id]


def clear_all_stores() -> None:
    """Clear all stores (for testing)."""
    _stores.clear()
