"""Tests for the StateStore with transaction support."""
import pytest

from app.core.state_store import (
    StateStore,
    StateEvent,
    EventType,
    Transaction,
    get_or_create_store,
    clear_store,
    clear_all_stores,
)
from app.core.entity_registry import EntityType


@pytest.fixture(autouse=True)
def cleanup_stores():
    """Clean up stores before and after each test."""
    clear_all_stores()
    yield
    clear_all_stores()


class TestStateStoreBasics:
    """Test basic StateStore operations."""

    def test_create_store(self):
        """Should create a new store with unique IDs."""
        store = StateStore()
        assert store.conversation_id is not None
        assert store.project_id is not None
        assert store.version == 0

    def test_create_track(self):
        """Should create a track and register it."""
        store = StateStore()
        track_id = store.create_track("Drums")
        
        assert track_id is not None
        assert store.registry.exists_track(track_id)
        assert store.registry.resolve_track("Drums") == track_id

    def test_create_region(self):
        """Should create a region linked to track."""
        store = StateStore()
        track_id = store.create_track("Drums")
        region_id = store.create_region("Pattern 1", track_id)
        
        assert region_id is not None
        assert store.registry.exists_region(region_id)
        regions = store.registry.get_track_regions(track_id)
        assert len(regions) == 1

    def test_create_region_without_track_fails(self):
        """Should fail to create region without valid track."""
        store = StateStore()
        
        with pytest.raises(ValueError):
            store.create_region("Pattern 1", "nonexistent-track")

    def test_create_bus(self):
        """Should create a bus."""
        store = StateStore()
        bus_id = store.create_bus("Reverb")
        
        assert bus_id is not None
        assert store.registry.exists_bus(bus_id)

    def test_get_or_create_bus(self):
        """Should return existing bus or create new one."""
        store = StateStore()
        
        bus_id_1 = store.get_or_create_bus("Reverb")
        bus_id_2 = store.get_or_create_bus("Reverb")
        bus_id_3 = store.get_or_create_bus("Delay")
        
        assert bus_id_1 == bus_id_2  # Same bus
        assert bus_id_1 != bus_id_3  # Different bus


class TestStateStoreVersioning:
    """Test state versioning."""

    def test_version_increments(self):
        """Version should increment on mutations."""
        store = StateStore()
        assert store.version == 0
        
        store.create_track("Drums")
        assert store.version == 1
        
        track_id = store.registry.resolve_track("Drums")
        store.create_region("Pattern", track_id)
        assert store.version == 2

    def test_events_recorded(self):
        """Events should be recorded for mutations."""
        store = StateStore()
        store.create_track("Drums")
        
        events = store.get_events_since(0)
        assert len(events) == 1
        assert events[0].event_type == EventType.TRACK_CREATED

    def test_entity_events(self):
        """Should get events for specific entity."""
        store = StateStore()
        track_id = store.create_track("Drums")
        store.create_track("Bass")
        
        events = store.get_entity_events(track_id)
        assert len(events) == 1
        assert events[0].entity_id == track_id


class TestTransactions:
    """Test transaction support."""

    def test_begin_transaction(self):
        """Should begin a transaction."""
        store = StateStore()
        tx = store.begin_transaction("Test transaction")
        
        assert tx.is_active
        assert not tx.committed
        assert not tx.rolled_back
        
        store.rollback(tx)

    def test_commit_transaction(self):
        """Committed changes should persist."""
        store = StateStore()
        tx = store.begin_transaction("Create track")
        
        track_id = store.create_track("Drums", transaction=tx)
        store.commit(tx)
        
        assert not tx.is_active
        assert tx.committed
        assert store.registry.exists_track(track_id)

    def test_rollback_transaction(self):
        """Rolled back changes should be reverted."""
        store = StateStore()
        
        # Create a track first (committed)
        initial_track = store.create_track("Initial")
        
        # Start a transaction
        tx = store.begin_transaction("Create more tracks")
        
        store.create_track("Drums", transaction=tx)
        store.create_track("Bass", transaction=tx)
        
        # Rollback
        store.rollback(tx)
        
        # Original track should still exist
        assert store.registry.exists_track(initial_track)
        
        # New tracks should NOT exist (rolled back)
        assert store.registry.resolve_track("Drums") is None
        assert store.registry.resolve_track("Bass") is None

    def test_nested_transaction_fails(self):
        """Should not allow nested transactions."""
        store = StateStore()
        tx = store.begin_transaction("First")
        
        with pytest.raises(RuntimeError):
            store.begin_transaction("Second")
        
        store.rollback(tx)


class TestStateStoreSerialization:
    """Test serialization and sync."""

    def test_to_dict(self):
        """Should serialize state."""
        store = StateStore()
        track_id = store.create_track("Drums")
        
        data = store.to_dict()
        
        assert data["conversation_id"] == store.conversation_id
        assert data["project_id"] == store.project_id
        assert data["version"] == store.version
        assert track_id in data["registry"]["tracks"]

    def test_sync_from_client(self):
        """Should sync with client state."""
        store = StateStore()
        
        client_state = {
            "tracks": [
                {"id": "track-1", "name": "Drums"},
                {"id": "track-2", "name": "Bass"},
            ],
            "buses": [
                {"id": "bus-1", "name": "Reverb"},
            ],
            "tempo": 90,
            "key": "Am",
        }
        
        store.sync_from_client(client_state)
        
        assert store.registry.exists_track("track-1")
        assert store.registry.exists_track("track-2")
        assert store.registry.exists_bus("bus-1")
        assert store.tempo == 90
        assert store.key == "Am"


class TestRegionNotes:
    """Test the materialized region note store."""

    def test_add_notes_creates_materialized_view(self):
        """Adding notes should populate the materialized note list."""
        store = StateStore()
        track_id = store.create_track("Drums")
        region_id = store.create_region("Pattern", track_id)

        notes = [
            {"pitch": 36, "start_beat": 0.0, "duration_beats": 0.5, "velocity": 100, "channel": 9},
            {"pitch": 38, "start_beat": 1.0, "duration_beats": 0.5, "velocity": 90, "channel": 9},
        ]
        store.add_notes(region_id, notes)

        result = store.get_region_notes(region_id)
        assert len(result) == 2
        assert result[0]["pitch"] == 36
        assert result[1]["pitch"] == 38

    def test_get_region_notes_returns_copy(self):
        """get_region_notes should return a deep copy (no mutation leaks)."""
        store = StateStore()
        track_id = store.create_track("Bass")
        region_id = store.create_region("Line", track_id)

        store.add_notes(region_id, [{"pitch": 40, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 80}])

        copy = store.get_region_notes(region_id)
        copy.append({"pitch": 99})

        assert len(store.get_region_notes(region_id)) == 1

    def test_remove_notes_by_pitch_and_start(self):
        """Removing notes should match on pitch + start_beat + duration_beats."""
        store = StateStore()
        track_id = store.create_track("Keys")
        region_id = store.create_region("Chords", track_id)

        store.add_notes(region_id, [
            {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 100},
            {"pitch": 64, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 100},
            {"pitch": 67, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 100},
        ])

        # Remove middle note
        store.remove_notes(region_id, [
            {"pitch": 64, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 100},
        ])

        result = store.get_region_notes(region_id)
        assert len(result) == 2
        pitches = [n["pitch"] for n in result]
        assert 64 not in pitches
        assert 60 in pitches
        assert 67 in pitches

    def test_remove_then_add_modified_note(self):
        """Simulates a 'modified' note: remove old, add new."""
        store = StateStore()
        track_id = store.create_track("Lead")
        region_id = store.create_region("Melody", track_id)

        store.add_notes(region_id, [
            {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 80},
        ])

        store.remove_notes(region_id, [
            {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 80},
        ])
        store.add_notes(region_id, [
            {"pitch": 63, "start_beat": 0.0, "duration_beats": 1.5, "velocity": 90},
        ])

        result = store.get_region_notes(region_id)
        assert len(result) == 1
        assert result[0]["pitch"] == 63
        assert result[0]["duration_beats"] == 1.5

    def test_sync_initializes_region_notes(self):
        """sync_from_client should populate region notes from project state."""
        store = StateStore()

        state = {
            "tracks": [{
                "id": "t1",
                "name": "Piano",
                "regions": [{
                    "id": "r1",
                    "name": "Intro",
                    "notes": [
                        {"pitch": 60, "start_beat": 0.0, "duration_beats": 2.0, "velocity": 100},
                        {"pitch": 64, "start_beat": 2.0, "duration_beats": 2.0, "velocity": 90},
                    ],
                }],
            }],
        }
        store.sync_from_client(state)

        result = store.get_region_notes("r1")
        assert len(result) == 2
        assert result[0]["pitch"] == 60

    def test_empty_region_returns_empty_list(self):
        """Querying an unknown region returns []."""
        store = StateStore()
        assert store.get_region_notes("nonexistent") == []

    def test_get_region_track_id(self):
        """get_region_track_id should return the parent track."""
        store = StateStore()
        track_id = store.create_track("Drums")
        region_id = store.create_region("Pattern", track_id)

        assert store.get_region_track_id(region_id) == track_id
        assert store.get_region_track_id("nonexistent") is None

    def test_rollback_restores_region_notes(self):
        """Rolling back a transaction should restore region notes to pre-tx state."""
        store = StateStore()
        track_id = store.create_track("Bass")
        region_id = store.create_region("Line", track_id)

        # Pre-transaction: add a note
        store.add_notes(region_id, [
            {"pitch": 40, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 80},
        ])

        # Start transaction, add more notes
        tx = store.begin_transaction("test add")
        store.add_notes(region_id, [
            {"pitch": 42, "start_beat": 1.0, "duration_beats": 1.0, "velocity": 80},
        ], transaction=tx)

        assert len(store.get_region_notes(region_id)) == 2

        # Rollback
        store.rollback(tx)

        # Should be back to 1 note
        assert len(store.get_region_notes(region_id)) == 1
        assert store.get_region_notes(region_id)[0]["pitch"] == 40


class TestStoreRegistry:
    """Test store registry (conversation -> store mapping)."""

    def test_get_or_create_store(self):
        """Should get or create store by conversation ID."""
        store1 = get_or_create_store("conv-1")
        store2 = get_or_create_store("conv-1")
        store3 = get_or_create_store("conv-2")
        
        assert store1 is store2  # Same conversation
        assert store1 is not store3  # Different conversation

    def test_clear_store(self):
        """Should clear specific store."""
        store = get_or_create_store("conv-to-clear")
        store.create_track("Test")
        
        clear_store("conv-to-clear")
        
        new_store = get_or_create_store("conv-to-clear")
        assert new_store is not store
        assert store.registry.resolve_track("Test") is not None  # Old store still has it
        assert new_store.registry.resolve_track("Test") is None  # New store doesn't

    def test_clear_all_stores(self):
        """Should clear all stores."""
        get_or_create_store("conv-a")
        get_or_create_store("conv-b")
        
        clear_all_stores()
        
        # New stores should be created
        new_a = get_or_create_store("conv-a")
        new_b = get_or_create_store("conv-b")
        
        assert new_a.version == 0
        assert new_b.version == 0
