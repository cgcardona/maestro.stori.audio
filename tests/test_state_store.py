"""
Comprehensive tests for app.core.state_store.StateStore.

StateStore is the canonical in-memory DAW state — the engine everything
writes through. Zero test coverage previously. This file covers:

  1.  Initialization — defaults, IDs, properties
  2.  Entity creation — tracks, regions, buses; event log side-effects
  3.  Transaction lifecycle — begin, commit, rollback
  4.  Transaction atomicity — rollback restores registry + notes exactly
  5.  Nested / double transaction guards
  6.  State modification — set_tempo, set_key, add_notes, remove_notes, add_effect
  7.  Note materialization — add accumulates, remove filters, get returns copy
  8.  _notes_match helper — pitch/start_beat/duration_beats matching + tolerance
  9.  sync_from_client — clears stale state, populates registry + notes + metadata
 10.  Snapshot / restore — rollback restores entities, notes, tempo, key
 11.  Event log — version increments, get_events_since, get_entity_events
 12.  Serialization — to_dict shape
 13.  Optimistic concurrency — get_state_id, check_state_id
 14.  Store registry — get_or_create_store, clear_store, clear_all_stores
 15.  get_region_track_id, get_track_name, get_or_create_bus
"""

import pytest
from copy import deepcopy

from app.core.state_store import (
    StateStore,
    Transaction,
    EventType,
    StateEvent,
    _notes_match,
    get_or_create_store,
    clear_store,
    clear_all_stores,
)
from app.core.entity_registry import EntityType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh() -> StateStore:
    """Return a new StateStore with a unique conversation ID."""
    import uuid
    return StateStore(conversation_id=str(uuid.uuid4()), project_id=str(uuid.uuid4()))


def _note(pitch: int = 60, start: float = 0.0, dur: float = 1.0, vel: int = 100) -> dict:
    return {
        "pitch": pitch,
        "start_beat": start,
        "duration_beats": dur,
        "velocity": vel,
    }


# ===========================================================================
# 1. Initialization
# ===========================================================================

class TestInitialization:
    """StateStore initializes with correct defaults."""

    def test_default_tempo(self):
        assert _fresh().tempo == 120

    def test_default_key(self):
        assert _fresh().key == "C"

    def test_default_time_signature(self):
        assert _fresh().time_signature == (4, 4)

    def test_default_version_zero(self):
        assert _fresh().version == 0

    def test_registry_is_empty(self):
        store = _fresh()
        assert store.registry.list_tracks() == []
        assert store.registry.list_regions() == []
        assert store.registry.list_buses() == []

    def test_conversation_id_stored(self):
        store = StateStore(conversation_id="conv-abc")
        assert store.conversation_id == "conv-abc"

    def test_project_id_stored(self):
        store = StateStore(project_id="proj-xyz")
        assert store.project_id == "proj-xyz"

    def test_auto_generated_ids(self):
        store = StateStore()
        assert len(store.conversation_id) > 8
        assert len(store.project_id) > 8


# ===========================================================================
# 2. Entity creation — event log side-effects
# ===========================================================================

class TestEntityCreation:
    """create_track / create_region / create_bus record events and update registry."""

    def test_create_track_returns_id(self):
        store = _fresh()
        tid = store.create_track("Drums")
        assert isinstance(tid, str) and len(tid) > 8

    def test_create_track_registers_entity(self):
        store = _fresh()
        tid = store.create_track("Drums")
        assert store.registry.exists_track(tid)

    def test_create_track_appends_event(self):
        store = _fresh()
        tid = store.create_track("Drums")
        events = store.get_entity_events(tid)
        assert len(events) == 1
        assert events[0].event_type == EventType.TRACK_CREATED

    def test_create_track_increments_version(self):
        store = _fresh()
        assert store.version == 0
        store.create_track("Drums")
        assert store.version == 1

    def test_create_region_returns_id(self):
        store = _fresh()
        tid = store.create_track("Drums")
        rid = store.create_region("Pattern", parent_track_id=tid)
        assert isinstance(rid, str)

    def test_create_region_registers_entity(self):
        store = _fresh()
        tid = store.create_track("Drums")
        rid = store.create_region("Pattern", parent_track_id=tid)
        assert store.registry.exists_region(rid)

    def test_create_region_appends_event(self):
        store = _fresh()
        tid = store.create_track("Drums")
        rid = store.create_region("Pattern", parent_track_id=tid)
        events = store.get_entity_events(rid)
        assert any(e.event_type == EventType.REGION_CREATED for e in events)

    def test_create_bus_returns_id(self):
        store = _fresh()
        bid = store.create_bus("Reverb")
        assert isinstance(bid, str)

    def test_create_bus_registers_entity(self):
        store = _fresh()
        bid = store.create_bus("Reverb")
        assert store.registry.exists_bus(bid)

    def test_create_bus_appends_event(self):
        store = _fresh()
        bid = store.create_bus("Reverb")
        events = store.get_entity_events(bid)
        assert any(e.event_type == EventType.BUS_CREATED for e in events)

    def test_get_or_create_bus_idempotent(self):
        store = _fresh()
        bid1 = store.get_or_create_bus("Delay")
        bid2 = store.get_or_create_bus("Delay")
        assert bid1 == bid2
        # Only one bus should exist
        assert len(store.registry.list_buses()) == 1

    def test_get_region_track_id(self):
        store = _fresh()
        tid = store.create_track("Bass")
        rid = store.create_region("Intro", parent_track_id=tid)
        assert store.get_region_track_id(rid) == tid

    def test_get_region_track_id_unknown_returns_none(self):
        assert _fresh().get_region_track_id("nonexistent") is None

    def test_get_track_name_returns_name(self):
        store = _fresh()
        tid = store.create_track("Guitar Lead")
        assert store.get_track_name(tid) == "Guitar Lead"

    def test_get_track_name_unknown_returns_none(self):
        assert _fresh().get_track_name("nonexistent") is None

    def test_multiple_tracks_all_registered(self):
        store = _fresh()
        ids = [store.create_track(f"Track{i}") for i in range(5)]
        assert len(store.registry.list_tracks()) == 5
        for tid in ids:
            assert store.registry.exists_track(tid)


# ===========================================================================
# 3. Transaction lifecycle
# ===========================================================================

class TestTransactionLifecycle:
    """begin_transaction / commit / rollback happy paths."""

    def test_begin_returns_transaction(self):
        store = _fresh()
        tx = store.begin_transaction("test")
        assert isinstance(tx, Transaction)
        assert tx.is_active

    def test_commit_marks_transaction_committed(self):
        store = _fresh()
        tx = store.begin_transaction()
        store.commit(tx)
        assert tx.committed
        assert not tx.is_active

    def test_commit_clears_active_transaction(self):
        store = _fresh()
        tx = store.begin_transaction()
        store.commit(tx)
        # After commit, begin a new one without error
        tx2 = store.begin_transaction()
        assert tx2.is_active
        store.rollback(tx2)

    def test_rollback_marks_transaction_rolled_back(self):
        store = _fresh()
        tx = store.begin_transaction()
        store.rollback(tx)
        assert tx.rolled_back
        assert not tx.is_active

    def test_transaction_start_event_recorded(self):
        store = _fresh()
        v_before = store.version
        tx = store.begin_transaction("my tx")
        # TRANSACTION_START event should have been appended
        start_events = [
            e for e in store._events
            if e.event_type == EventType.TRANSACTION_START
        ]
        assert len(start_events) == 1
        store.rollback(tx)

    def test_commit_event_recorded(self):
        store = _fresh()
        tx = store.begin_transaction()
        store.commit(tx)
        commit_events = [
            e for e in store._events
            if e.event_type == EventType.TRANSACTION_COMMIT
        ]
        assert len(commit_events) == 1

    def test_rollback_event_recorded(self):
        store = _fresh()
        tx = store.begin_transaction()
        store.rollback(tx)
        rb_events = [
            e for e in store._events
            if e.event_type == EventType.TRANSACTION_ROLLBACK
        ]
        assert len(rb_events) == 1

    def test_mutations_within_transaction_linked_to_tx_id(self):
        store = _fresh()
        tx = store.begin_transaction()
        tid = store.create_track("Drums", transaction=tx)
        events = store.get_entity_events(tid)
        assert all(e.transaction_id == tx.id for e in events)
        store.commit(tx)

    def test_commit_without_transaction_raises(self):
        store = _fresh()
        tx = store.begin_transaction()
        store.commit(tx)
        with pytest.raises(ValueError):
            store.commit(tx)  # already committed

    def test_rollback_without_transaction_raises(self):
        store = _fresh()
        with pytest.raises(ValueError):
            # No active transaction
            dummy_tx = Transaction(
                id="fake",
                started_at=__import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                )
            )
            store.rollback(dummy_tx)


# ===========================================================================
# 4. Transaction atomicity — rollback restores state
# ===========================================================================

class TestTransactionAtomicity:
    """Rollback must restore registry entities and note state precisely."""

    def test_rollback_removes_created_track(self):
        store = _fresh()
        tx = store.begin_transaction()
        tid = store.create_track("Drums", transaction=tx)
        assert store.registry.exists_track(tid)
        store.rollback(tx)
        assert not store.registry.exists_track(tid)

    def test_rollback_removes_created_region(self):
        store = _fresh()
        # Create track outside transaction (committed)
        tid = store.create_track("Drums")
        tx = store.begin_transaction()
        rid = store.create_region("Pattern", parent_track_id=tid, transaction=tx)
        assert store.registry.exists_region(rid)
        store.rollback(tx)
        assert not store.registry.exists_region(rid)

    def test_rollback_restores_notes(self):
        store = _fresh()
        tid = store.create_track("Bass")
        rid = store.create_region("Verse", parent_track_id=tid)
        # Add initial notes (committed)
        store.add_notes(rid, [_note(60)])
        assert len(store.get_region_notes(rid)) == 1

        tx = store.begin_transaction()
        store.add_notes(rid, [_note(62), _note(64)], transaction=tx)
        assert len(store.get_region_notes(rid)) == 3  # 1 + 2
        store.rollback(tx)
        # Notes must revert to 1
        assert len(store.get_region_notes(rid)) == 1

    def test_rollback_restores_tempo(self):
        store = _fresh()
        store.set_tempo(120)  # committed
        tx = store.begin_transaction()
        store.set_tempo(140, transaction=tx)
        assert store.tempo == 140
        store.rollback(tx)
        assert store.tempo == 120

    def test_rollback_restores_key(self):
        store = _fresh()
        store.set_key("Am")
        tx = store.begin_transaction()
        store.set_key("F#m", transaction=tx)
        assert store.key == "F#m"
        store.rollback(tx)
        assert store.key == "Am"

    def test_committed_entities_survive_next_rollback(self):
        """Entities committed in tx1 must survive a rollback in tx2."""
        store = _fresh()
        tx1 = store.begin_transaction()
        tid = store.create_track("Drums", transaction=tx1)
        store.commit(tx1)

        tx2 = store.begin_transaction()
        store.create_track("Bass", transaction=tx2)
        store.rollback(tx2)

        # Drums from tx1 must still exist
        assert store.registry.exists_track(tid)
        # Bass from tx2 must not
        assert store.registry.resolve_track("Bass") is None


# ===========================================================================
# 5. Nested / double transaction guards
# ===========================================================================

class TestTransactionGuards:
    """Double begin raises; wrong transaction errors are raised."""

    def test_double_begin_raises(self):
        store = _fresh()
        tx = store.begin_transaction()
        with pytest.raises(RuntimeError, match="already active"):
            store.begin_transaction()
        store.rollback(tx)

    def test_commit_wrong_transaction_raises(self):
        store = _fresh()
        tx1 = store.begin_transaction()
        # Build a fake transaction object
        fake_tx = Transaction(
            id="totally-different-id",
            started_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
        )
        with pytest.raises(ValueError):
            store.commit(fake_tx)
        store.rollback(tx1)

    def test_commit_already_committed_tx_raises(self):
        store = _fresh()
        tx = store.begin_transaction()
        store.commit(tx)
        # Begin new transaction to make commit fail for old one
        tx2 = store.begin_transaction()
        with pytest.raises(ValueError):
            store.commit(tx)  # old tx, wrong id
        store.rollback(tx2)

    def test_new_transaction_after_commit(self):
        store = _fresh()
        tx1 = store.begin_transaction()
        store.commit(tx1)
        tx2 = store.begin_transaction()  # must not raise
        assert tx2.is_active
        store.commit(tx2)

    def test_new_transaction_after_rollback(self):
        store = _fresh()
        tx1 = store.begin_transaction()
        store.rollback(tx1)
        tx2 = store.begin_transaction()  # must not raise
        assert tx2.is_active
        store.rollback(tx2)


# ===========================================================================
# 6. State modification — set_tempo, set_key, add_effect
# ===========================================================================

class TestStateModification:
    """State setters update the store and record events."""

    def test_set_tempo_updates_property(self):
        store = _fresh()
        store.set_tempo(140)
        assert store.tempo == 140

    def test_set_tempo_records_old_and_new(self):
        store = _fresh()
        store.set_tempo(140)
        events = [e for e in store._events if e.event_type == EventType.TEMPO_CHANGED]
        assert len(events) == 1
        assert events[0].data["old_tempo"] == 120
        assert events[0].data["new_tempo"] == 140

    def test_set_key_updates_property(self):
        store = _fresh()
        store.set_key("Dm")
        assert store.key == "Dm"

    def test_set_key_records_event(self):
        store = _fresh()
        store.set_key("Am")
        events = [e for e in store._events if e.event_type == EventType.KEY_CHANGED]
        assert len(events) == 1
        assert events[0].data["new_key"] == "Am"

    def test_add_effect_records_event(self):
        store = _fresh()
        tid = store.create_track("Drums")
        store.add_effect(tid, "reverb")
        events = [
            e for e in store._events
            if e.event_type == EventType.EFFECT_ADDED and e.entity_id == tid
        ]
        assert len(events) == 1
        assert events[0].data["effect_type"] == "reverb"


# ===========================================================================
# 7. Note materialization
# ===========================================================================

class TestNoteMaterialization:
    """add_notes / remove_notes / get_region_notes work correctly."""

    def _setup(self) -> tuple[StateStore, str, str]:
        store = _fresh()
        tid = store.create_track("Bass")
        rid = store.create_region("Verse", parent_track_id=tid)
        return store, tid, rid

    def test_add_notes_materializes(self):
        store, _, rid = self._setup()
        store.add_notes(rid, [_note(60), _note(62)])
        notes = store.get_region_notes(rid)
        assert len(notes) == 2

    def test_add_notes_accumulates(self):
        store, _, rid = self._setup()
        store.add_notes(rid, [_note(60)])
        store.add_notes(rid, [_note(62)])
        assert len(store.get_region_notes(rid)) == 2

    def test_add_notes_records_event(self):
        store, _, rid = self._setup()
        store.add_notes(rid, [_note(60)])
        events = [
            e for e in store._events
            if e.event_type == EventType.NOTES_ADDED and e.entity_id == rid
        ]
        assert len(events) == 1
        assert events[0].data["notes_count"] == 1

    def test_get_region_notes_returns_deep_copy(self):
        """Mutating the returned list must not affect the store."""
        store, _, rid = self._setup()
        store.add_notes(rid, [_note(60)])
        notes = store.get_region_notes(rid)
        notes.clear()
        assert len(store.get_region_notes(rid)) == 1

    def test_get_region_notes_empty_region_returns_empty(self):
        store, _, rid = self._setup()
        assert store.get_region_notes(rid) == []

    def test_get_region_notes_unknown_region_returns_empty(self):
        assert _fresh().get_region_notes("nonexistent") == []

    def test_remove_notes_by_pitch_and_beat(self):
        store, _, rid = self._setup()
        store.add_notes(rid, [_note(60, 0.0, 1.0), _note(62, 1.0, 1.0)])
        store.remove_notes(rid, [_note(60, 0.0, 1.0)])
        remaining = store.get_region_notes(rid)
        assert len(remaining) == 1
        assert remaining[0]["pitch"] == 62

    def test_remove_notes_non_matching_criteria_leaves_all(self):
        store, _, rid = self._setup()
        store.add_notes(rid, [_note(60), _note(62)])
        store.remove_notes(rid, [_note(99, 99.0, 99.0)])  # no match
        assert len(store.get_region_notes(rid)) == 2

    def test_remove_notes_records_event(self):
        store, _, rid = self._setup()
        store.add_notes(rid, [_note(60)])
        store.remove_notes(rid, [_note(60)])
        events = [
            e for e in store._events
            if e.event_type == EventType.NOTES_REMOVED and e.entity_id == rid
        ]
        assert len(events) == 1

    def test_remove_all_notes(self):
        store, _, rid = self._setup()
        notes = [_note(p) for p in [60, 62, 64, 65, 67]]
        store.add_notes(rid, notes)
        store.remove_notes(rid, notes)
        assert store.get_region_notes(rid) == []

    def test_add_notes_to_unknown_region_does_not_raise(self):
        """add_notes to an unregistered region ID works (creates entry)."""
        store = _fresh()
        store.add_notes("phantom-region-id", [_note(60)])
        assert len(store.get_region_notes("phantom-region-id")) == 1


# ===========================================================================
# 8. _notes_match helper
# ===========================================================================

class TestNotesMatch:
    """_notes_match uses pitch + start_beat + duration_beats with float tolerance."""

    def test_exact_match(self):
        n = {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0}
        assert _notes_match(n, n.copy())

    def test_pitch_mismatch(self):
        n = {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0}
        c = {"pitch": 62, "start_beat": 0.0, "duration_beats": 1.0}
        assert not _notes_match(n, c)

    def test_start_beat_mismatch(self):
        n = {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0}
        c = {"pitch": 60, "start_beat": 0.5, "duration_beats": 1.0}
        assert not _notes_match(n, c)

    def test_duration_mismatch(self):
        n = {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0}
        c = {"pitch": 60, "start_beat": 0.0, "duration_beats": 2.0}
        assert not _notes_match(n, c)

    def test_float_tolerance_passes(self):
        """Differences within 1e-6 are treated as equal."""
        n = {"pitch": 60, "start_beat": 0.0,       "duration_beats": 1.0}
        c = {"pitch": 60, "start_beat": 1e-7,      "duration_beats": 1.0 + 1e-7}
        assert _notes_match(n, c)

    def test_float_outside_tolerance_fails(self):
        n = {"pitch": 60, "start_beat": 0.0,  "duration_beats": 1.0}
        c = {"pitch": 60, "start_beat": 1e-5, "duration_beats": 1.0}
        assert not _notes_match(n, c)

    def test_velocity_not_matched(self):
        """velocity difference must NOT prevent a match."""
        n = {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 100}
        c = {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 50}
        assert _notes_match(n, c)

    def test_missing_fields_default_to_zero(self):
        n = {"pitch": 60}
        c = {"pitch": 60}
        assert _notes_match(n, c)


# ===========================================================================
# 9. sync_from_client
# ===========================================================================

class TestSyncFromClient:
    """sync_from_client replaces registry and note store with client snapshot."""

    def _project(self) -> dict:
        return {
            "tempo": 140,
            "key": "Dm",
            "timeSignature": "3/4",
            "tracks": [
                {
                    "id": "t1", "name": "Drums",
                    "regions": [
                        {
                            "id": "r1", "name": "Intro",
                            "notes": [
                                {"pitch": 36, "start_beat": 0.0,
                                 "duration_beats": 0.25, "velocity": 100}
                            ],
                        }
                    ],
                },
                {
                    "id": "t2", "name": "Bass",
                    "regions": [],
                },
            ],
            "buses": [{"id": "b1", "name": "Reverb"}],
        }

    def test_sync_populates_tracks(self):
        store = _fresh()
        store.sync_from_client(self._project())
        assert store.registry.exists_track("t1")
        assert store.registry.exists_track("t2")

    def test_sync_populates_regions(self):
        store = _fresh()
        store.sync_from_client(self._project())
        assert store.registry.exists_region("r1")

    def test_sync_populates_notes(self):
        store = _fresh()
        store.sync_from_client(self._project())
        notes = store.get_region_notes("r1")
        assert len(notes) == 1
        assert notes[0]["pitch"] == 36

    def test_sync_sets_tempo(self):
        store = _fresh()
        store.sync_from_client(self._project())
        assert store.tempo == 140

    def test_sync_sets_key(self):
        store = _fresh()
        store.sync_from_client(self._project())
        assert store.key == "Dm"

    def test_sync_sets_time_signature_string(self):
        store = _fresh()
        store.sync_from_client(self._project())
        assert store.time_signature == (3, 4)

    def test_sync_sets_time_signature_dict(self):
        store = _fresh()
        store.sync_from_client({
            "timeSignature": {"numerator": 6, "denominator": 8}
        })
        assert store.time_signature == (6, 8)

    def test_sync_clears_stale_tracks(self):
        """Stale tracks from a previous sync must not survive."""
        store = _fresh()
        # First sync: old project
        store.sync_from_client({
            "tracks": [{"id": "old-t", "name": "Old", "regions": []}]
        })
        assert store.registry.exists_track("old-t")

        # Second sync: new project, old track gone
        store.sync_from_client({
            "tracks": [{"id": "new-t", "name": "New", "regions": []}]
        })
        assert not store.registry.exists_track("old-t")
        assert store.registry.exists_track("new-t")

    def test_sync_clears_stale_notes(self):
        """Notes from a previous sync must not survive into the new sync."""
        store = _fresh()
        store.sync_from_client({
            "tracks": [{"id": "t1", "name": "Bass",
                        "regions": [{"id": "r1", "name": "P",
                                     "notes": [_note(60)]}]}]
        })
        assert len(store.get_region_notes("r1")) == 1

        # Re-sync with empty notes
        store.sync_from_client({
            "tracks": [{"id": "t1", "name": "Bass",
                        "regions": [{"id": "r1", "name": "P", "notes": []}]}]
        })
        assert store.get_region_notes("r1") == []

    def test_sync_preserves_notes_when_key_absent(self):
        """When the client reports a region without a 'notes' key, keep prior notes."""
        store = _fresh()
        store.sync_from_client({
            "tracks": [{"id": "t1", "name": "Bass",
                        "regions": [{"id": "r1", "name": "P",
                                     "notes": [_note(60)]}]}]
        })
        assert len(store.get_region_notes("r1")) == 1

        # Re-sync: region present but no "notes" key (frontend sends only noteCount)
        store.sync_from_client({
            "tracks": [{"id": "t1", "name": "Bass",
                        "regions": [{"id": "r1", "name": "P", "noteCount": 1}]}]
        })
        assert len(store.get_region_notes("r1")) == 1
        assert store.get_region_notes("r1")[0]["pitch"] == 60

    def test_sync_is_idempotent(self):
        project = self._project()
        store = _fresh()
        store.sync_from_client(project)
        store.sync_from_client(project)
        assert len(store.registry.list_tracks()) == 2

    def test_sync_empty_project_no_error(self):
        store = _fresh()
        store.sync_from_client({})
        assert store.registry.list_tracks() == []


# ===========================================================================
# 10. Event log — version, get_events_since, get_entity_events
# ===========================================================================

class TestEventLog:
    """Event log records every mutation in order."""

    def test_version_increments_per_event(self):
        store = _fresh()
        v0 = store.version
        store.create_track("T1")
        v1 = store.version
        store.create_track("T2")
        v2 = store.version
        assert v1 == v0 + 1
        assert v2 == v0 + 2

    def test_get_events_since_returns_only_newer(self):
        store = _fresh()
        store.create_track("T1")
        v1 = store.version
        store.create_track("T2")
        store.create_track("T3")
        newer = store.get_events_since(v1)
        assert all(e.version > v1 for e in newer)

    def test_get_events_since_zero_returns_all(self):
        store = _fresh()
        store.create_track("T")
        store.set_tempo(100)
        events = store.get_events_since(0)
        assert len(events) >= 2

    def test_get_entity_events_filters_by_entity(self):
        store = _fresh()
        tid1 = store.create_track("Drums")
        tid2 = store.create_track("Bass")
        e1 = store.get_entity_events(tid1)
        e2 = store.get_entity_events(tid2)
        assert all(e.entity_id == tid1 for e in e1)
        assert all(e.entity_id == tid2 for e in e2)

    def test_events_have_correct_type(self):
        store = _fresh()
        tid = store.create_track("Drums")
        rid = store.create_region("P", parent_track_id=tid)
        store.set_tempo(130)
        types = {e.event_type for e in store._events}
        assert EventType.TRACK_CREATED in types
        assert EventType.REGION_CREATED in types
        assert EventType.TEMPO_CHANGED in types

    def test_events_have_timestamps(self):
        store = _fresh()
        store.create_track("T")
        for e in store._events:
            assert e.timestamp is not None

    def test_transaction_events_have_tx_id(self):
        store = _fresh()
        tx = store.begin_transaction()
        tid = store.create_track("T", transaction=tx)
        events = store.get_entity_events(tid)
        assert all(e.transaction_id == tx.id for e in events)
        store.commit(tx)


# ===========================================================================
# 11. Serialization
# ===========================================================================

class TestSerialization:
    """to_dict produces a complete snapshot."""

    def test_to_dict_has_required_keys(self):
        store = _fresh()
        d = store.to_dict()
        assert "conversation_id" in d
        assert "project_id" in d
        assert "version" in d
        assert "registry" in d
        assert "events" in d
        assert "project_metadata" in d

    def test_to_dict_version_matches(self):
        store = _fresh()
        store.create_track("Drums")
        d = store.to_dict()
        assert d["version"] == store.version

    def test_to_dict_registry_has_tracks(self):
        store = _fresh()
        store.create_track("Drums")
        d = store.to_dict()
        assert "tracks" in d["registry"]
        assert len(d["registry"]["tracks"]) == 1

    def test_to_dict_project_metadata_has_tempo_and_key(self):
        store = _fresh()
        store.set_tempo(140)
        store.set_key("Am")
        d = store.to_dict()
        assert d["project_metadata"]["tempo"] == 140
        assert d["project_metadata"]["key"] == "Am"

    def test_state_event_to_dict(self):
        store = _fresh()
        tid = store.create_track("T")
        events = store.get_entity_events(tid)
        d = events[0].to_dict()
        assert "id" in d
        assert "event_type" in d
        assert "version" in d
        assert "timestamp" in d


# ===========================================================================
# 12. Optimistic concurrency
# ===========================================================================

class TestOptimisticConcurrency:
    """get_state_id / check_state_id for variation commit safety."""

    def test_initial_state_id_is_zero(self):
        store = _fresh()
        assert store.get_state_id() == "0"

    def test_state_id_increments_with_mutations(self):
        store = _fresh()
        store.create_track("T")
        assert store.get_state_id() == str(store.version)
        assert store.get_state_id() != "0"

    def test_check_state_id_matches_current(self):
        store = _fresh()
        sid = store.get_state_id()
        assert store.check_state_id(sid)

    def test_check_state_id_fails_after_mutation(self):
        store = _fresh()
        sid = store.get_state_id()  # "0"
        store.create_track("T")
        assert not store.check_state_id(sid)

    def test_check_state_id_invalid_string_returns_false(self):
        assert not _fresh().check_state_id("not-a-number")

    def test_check_state_id_empty_string_returns_false(self):
        assert not _fresh().check_state_id("")


# ===========================================================================
# 13. Store registry — get_or_create_store, clear_store, clear_all_stores
# ===========================================================================

class TestStoreRegistry:
    """Module-level store registry is correct."""

    def setup_method(self):
        clear_all_stores()

    def teardown_method(self):
        clear_all_stores()

    def test_get_or_create_returns_store(self):
        store = get_or_create_store("conv-1")
        assert isinstance(store, StateStore)

    def test_get_or_create_same_conv_returns_same_store(self):
        s1 = get_or_create_store("conv-1")
        s2 = get_or_create_store("conv-1")
        assert s1 is s2

    def test_different_conv_different_stores(self):
        s1 = get_or_create_store("conv-1")
        s2 = get_or_create_store("conv-2")
        assert s1 is not s2

    def test_clear_store_removes_it(self):
        get_or_create_store("conv-1")
        clear_store("conv-1")
        # Creating again gives a fresh store
        s_new = get_or_create_store("conv-1")
        assert s_new.version == 0

    def test_clear_all_stores_removes_all(self):
        get_or_create_store("conv-a")
        get_or_create_store("conv-b")
        clear_all_stores()
        s = get_or_create_store("conv-a")
        assert s.version == 0

    def test_store_retains_state_across_calls(self):
        store = get_or_create_store("conv-persist")
        tid = store.create_track("Drums")
        same_store = get_or_create_store("conv-persist")
        assert same_store.registry.exists_track(tid)

    def test_project_id_passed_to_new_store(self):
        store = get_or_create_store("conv-new", project_id="my-project")
        assert store.project_id == "my-project"
