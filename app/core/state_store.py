"""
Persistent StateStore for Stori Composer (Cursor-of-DAWs).

This is the **authoritative source of truth** for project state across requests.
Unlike the per-request EntityRegistry, StateStore persists across the session.

Key principles:
1. Project state is versioned - every mutation creates a new version
2. Rollback is first-class - failed plans can revert to previous version
3. Event sourcing - all changes are captured as events
4. Multi-client ready - state can be shared across connections

Architecture:
    StateStore (persistent, versioned)
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
from typing import Any, Optional
from copy import deepcopy

from app.core.entity_registry import EntityRegistry, EntityInfo, EntityType

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
    entity_type: Optional[EntityType]
    entity_id: Optional[str]
    data: dict[str, Any]
    timestamp: datetime
    version: int
    transaction_id: Optional[str] = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_type": self.event_type.value,
            "entity_type": self.entity_type.value if self.entity_type else None,
            "entity_id": self.entity_id,
            "data": self.data,
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


@dataclass
class StateSnapshot:
    """A full snapshot of state at a specific version."""
    version: int
    timestamp: datetime
    registry_data: dict[str, Any]
    project_metadata: dict[str, Any]


class StateStore:
    """
    Persistent, versioned state store for a project/conversation.
    
    Provides:
    1. Versioned state with rollback capability
    2. Transaction support for atomic multi-step operations
    3. Event log for audit trail
    4. Fast entity lookups via derived EntityRegistry
    
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
        conversation_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ):
        self.conversation_id = conversation_id or str(uuid.uuid4())
        self.project_id = project_id or str(uuid.uuid4())
        
        # Core state
        self._registry = EntityRegistry(project_id=self.project_id)
        self._version: int = 0
        self._events: list[StateEvent] = []
        self._snapshots: list[StateSnapshot] = []
        self._active_transaction: Optional[Transaction] = None
        
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
        return self._tempo
    
    @property
    def key(self) -> str:
        return self._key
    
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
            data={"description": description},
            transaction=tx,
        )
        
        logger.info(f"🔒 Transaction started: {tx.id[:8]}")
        return tx
    
    def commit(self, transaction: Transaction) -> None:
        """Commit a transaction, making all changes permanent."""
        if transaction.id != self._active_transaction.id:
            raise ValueError("Cannot commit a transaction that is not active")
        
        if not transaction.is_active:
            raise ValueError("Transaction is not active")
        
        # Record commit event
        self._append_event(
            event_type=EventType.TRANSACTION_COMMIT,
            entity_type=None,
            entity_id=None,
            data={"event_count": len(transaction.events)},
            transaction=transaction,
        )
        
        transaction.committed = True
        self._active_transaction = None
        
        logger.info(f"✅ Transaction committed: {transaction.id[:8]} ({len(transaction.events)} events)")
    
    def rollback(self, transaction: Transaction) -> None:
        """Rollback a transaction, reverting all changes."""
        if transaction.id != self._active_transaction.id:
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
            data={"rolled_back_events": len(transaction.events)},
            transaction=None,  # Rollback is outside the transaction
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
        track_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        transaction: Optional[Transaction] = None,
    ) -> str:
        """Create a new track and record the event."""
        track_id = self._registry.create_track(name, track_id, metadata)
        
        self._append_event(
            event_type=EventType.TRACK_CREATED,
            entity_type=EntityType.TRACK,
            entity_id=track_id,
            data={"name": name, "metadata": metadata or {}},
            transaction=transaction or self._active_transaction,
        )
        
        return track_id
    
    def create_region(
        self,
        name: str,
        parent_track_id: str,
        region_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        transaction: Optional[Transaction] = None,
    ) -> str:
        """Create a new region and record the event."""
        region_id = self._registry.create_region(name, parent_track_id, region_id, metadata)
        
        self._append_event(
            event_type=EventType.REGION_CREATED,
            entity_type=EntityType.REGION,
            entity_id=region_id,
            data={
                "name": name,
                "parent_track_id": parent_track_id,
                "metadata": metadata or {},
            },
            transaction=transaction or self._active_transaction,
        )
        
        return region_id
    
    def create_bus(
        self,
        name: str,
        bus_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        transaction: Optional[Transaction] = None,
    ) -> str:
        """Create a new bus and record the event."""
        bus_id = self._registry.create_bus(name, bus_id, metadata)
        
        self._append_event(
            event_type=EventType.BUS_CREATED,
            entity_type=EntityType.BUS,
            entity_id=bus_id,
            data={"name": name, "metadata": metadata or {}},
            transaction=transaction or self._active_transaction,
        )
        
        return bus_id
    
    def get_or_create_bus(
        self,
        name: str,
        transaction: Optional[Transaction] = None,
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
        transaction: Optional[Transaction] = None,
    ) -> None:
        """Set project tempo."""
        old_tempo = self._tempo
        self._tempo = tempo
        
        self._append_event(
            event_type=EventType.TEMPO_CHANGED,
            entity_type=None,
            entity_id=None,
            data={"old_tempo": old_tempo, "new_tempo": tempo},
            transaction=transaction or self._active_transaction,
        )
    
    def set_key(
        self,
        key: str,
        transaction: Optional[Transaction] = None,
    ) -> None:
        """Set project key."""
        old_key = self._key
        self._key = key
        
        self._append_event(
            event_type=EventType.KEY_CHANGED,
            entity_type=None,
            entity_id=None,
            data={"old_key": old_key, "new_key": key},
            transaction=transaction or self._active_transaction,
        )
    
    def add_notes(
        self,
        region_id: str,
        notes: list[dict[str, Any]],
        transaction: Optional[Transaction] = None,
    ) -> None:
        """Record notes added to a region."""
        self._append_event(
            event_type=EventType.NOTES_ADDED,
            entity_type=EntityType.REGION,
            entity_id=region_id,
            data={"notes_count": len(notes), "notes": notes},
            transaction=transaction or self._active_transaction,
        )
    
    def remove_notes(
        self,
        region_id: str,
        note_criteria: list[dict[str, Any]],
        transaction: Optional[Transaction] = None,
    ) -> None:
        """
        Record notes removed from a region.

        note_criteria is a list of dicts identifying notes to remove.
        Each dict should contain matching fields (pitch, start, duration, etc.)
        to identify the note in the region.
        """
        self._append_event(
            event_type=EventType.NOTES_REMOVED,
            entity_type=EntityType.REGION,
            entity_id=region_id,
            data={"notes_count": len(note_criteria), "notes": note_criteria},
            transaction=transaction or self._active_transaction,
        )

    def add_effect(
        self,
        track_id: str,
        effect_type: str,
        transaction: Optional[Transaction] = None,
    ) -> None:
        """Record effect added to a track."""
        self._append_event(
            event_type=EventType.EFFECT_ADDED,
            entity_type=EntityType.TRACK,
            entity_id=track_id,
            data={"effect_type": effect_type},
            transaction=transaction or self._active_transaction,
        )
    
    # =========================================================================
    # Synchronization
    # =========================================================================
    
    def sync_from_client(self, project_state: dict[str, Any]) -> None:
        """
        Sync with client-reported project state.
        
        This updates the registry without creating events (client is source of truth).
        """
        self._registry.sync_from_project_state(project_state)
        
        # Update project metadata
        if "tempo" in project_state:
            self._tempo = project_state["tempo"]
        if "key" in project_state:
            self._key = project_state["key"]
        if "timeSignature" in project_state:
            ts = project_state["timeSignature"]
            self._time_signature = (ts.get("numerator", 4), ts.get("denominator", 4))
    
    # =========================================================================
    # Event & Snapshot Management
    # =========================================================================
    
    def _append_event(
        self,
        event_type: EventType,
        entity_type: Optional[EntityType],
        entity_id: Optional[str],
        data: dict[str, Any],
        transaction: Optional[Transaction],
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
        """Take a snapshot of current state."""
        snapshot = StateSnapshot(
            version=self._version,
            timestamp=datetime.now(timezone.utc),
            registry_data=self._registry.to_dict(),
            project_metadata={
                "tempo": self._tempo,
                "key": self._key,
                "time_signature": self._time_signature,
            },
        )
        self._snapshots.append(snapshot)
        
        # Keep only last 10 snapshots
        if len(self._snapshots) > 10:
            self._snapshots = self._snapshots[-10:]
        
        return snapshot
    
    def _restore_snapshot(self, snapshot: StateSnapshot) -> None:
        """Restore state from a snapshot."""
        self._registry = EntityRegistry.from_dict(snapshot.registry_data)
        self._tempo = snapshot.project_metadata.get("tempo", 120)
        self._key = snapshot.project_metadata.get("key", "C")
        self._time_signature = tuple(snapshot.project_metadata.get("time_signature", (4, 4)))
        # Note: version is NOT restored - we continue incrementing
        
        logger.info(f"📸 Restored to snapshot v{snapshot.version}")
    
    # =========================================================================
    # Serialization
    # =========================================================================
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize entire store state."""
        return {
            "conversation_id": self.conversation_id,
            "project_id": self.project_id,
            "version": self._version,
            "registry": self._registry.to_dict(),
            "events": [e.to_dict() for e in self._events[-100:]],  # Last 100 events
            "project_metadata": {
                "tempo": self._tempo,
                "key": self._key,
                "time_signature": self._time_signature,
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
    project_id: Optional[str] = None,
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
