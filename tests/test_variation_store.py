"""
Tests for the Variation Store.

Covers record creation, state transitions, phrase storage,
lifecycle management, and cleanup per the v1 canonical spec.
"""

import pytest
from datetime import datetime, timezone, timedelta

from app.variation.core.state_machine import (
    VariationStatus,
    InvalidTransitionError,
)
from app.variation.storage.variation_store import (
    VariationRecord,
    PhraseRecord,
    VariationStore,
    get_variation_store,
    reset_variation_store,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def store():
    """Fresh variation store for each test."""
    s = VariationStore()
    yield s
    s.clear()


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset singleton between tests."""
    yield
    reset_variation_store()


# =============================================================================
# Record Creation
# =============================================================================


class TestRecordCreation:
    """Test creating variation records."""

    def test_create_record(self, store):
        """Creating a record returns it in CREATED state."""
        record = store.create(
            project_id="proj-1",
            base_state_id="42",
            intent="make it minor",
        )

        assert record.project_id == "proj-1"
        assert record.base_state_id == "42"
        assert record.intent == "make it minor"
        assert record.status == VariationStatus.CREATED
        assert isinstance(record.variation_id, str)
        assert len(record.variation_id) > 0

    def test_create_with_explicit_id(self, store):
        """Can create with a pre-generated variation_id."""
        record = store.create(
            project_id="proj-1",
            base_state_id="0",
            intent="test",
            variation_id="my-custom-id",
        )

        assert record.variation_id == "my-custom-id"

    def test_create_duplicate_raises(self, store):
        """Cannot create two records with the same variation_id."""
        store.create(
            project_id="proj-1",
            base_state_id="0",
            intent="test",
            variation_id="dup-id",
        )

        with pytest.raises(ValueError, match="already exists"):
            store.create(
                project_id="proj-1",
                base_state_id="0",
                intent="test2",
                variation_id="dup-id",
            )

    def test_create_increments_count(self, store):
        """Store count increases with each creation."""
        assert store.count == 0
        store.create(project_id="p", base_state_id="0", intent="a")
        assert store.count == 1
        store.create(project_id="p", base_state_id="0", intent="b")
        assert store.count == 2


# =============================================================================
# Record Retrieval
# =============================================================================


class TestRecordRetrieval:
    """Test getting variation records."""

    def test_get_existing(self, store):
        """get() returns the record if it exists."""
        created = store.create(project_id="p", base_state_id="0", intent="test")
        found = store.get(created.variation_id)

        assert found is not None
        assert found.variation_id == created.variation_id

    def test_get_missing_returns_none(self, store):
        """get() returns None for missing records."""
        assert store.get("nonexistent") is None

    def test_get_or_raise_existing(self, store):
        """get_or_raise() returns the record if it exists."""
        created = store.create(project_id="p", base_state_id="0", intent="test")
        found = store.get_or_raise(created.variation_id)

        assert found.variation_id == created.variation_id

    def test_get_or_raise_missing_raises(self, store):
        """get_or_raise() raises KeyError for missing records."""
        with pytest.raises(KeyError, match="not found"):
            store.get_or_raise("nonexistent")


# =============================================================================
# State Transitions via Store
# =============================================================================


class TestStoreTransitions:
    """Test state transitions through the store."""

    def test_transition_happy_path(self, store):
        """CREATED → STREAMING → READY → COMMITTED."""
        record = store.create(project_id="p", base_state_id="0", intent="test")
        vid = record.variation_id

        store.transition(vid, VariationStatus.STREAMING)
        assert store.get(vid).status == VariationStatus.STREAMING

        store.transition(vid, VariationStatus.READY)
        assert store.get(vid).status == VariationStatus.READY

        store.transition(vid, VariationStatus.COMMITTED)
        assert store.get(vid).status == VariationStatus.COMMITTED

    def test_transition_invalid_raises(self, store):
        """Invalid transitions raise InvalidTransitionError."""
        record = store.create(project_id="p", base_state_id="0", intent="test")

        with pytest.raises(InvalidTransitionError):
            store.transition(record.variation_id, VariationStatus.COMMITTED)

    def test_transition_missing_raises(self, store):
        """Transitioning a missing record raises KeyError."""
        with pytest.raises(KeyError):
            store.transition("nonexistent", VariationStatus.STREAMING)

    def test_transition_updates_timestamp(self, store):
        """Transitions update the updated_at field."""
        record = store.create(project_id="p", base_state_id="0", intent="test")
        created_at = record.updated_at

        store.transition(record.variation_id, VariationStatus.STREAMING)

        assert record.updated_at >= created_at


# =============================================================================
# Phrase Management
# =============================================================================


class TestPhraseManagement:
    """Test phrase storage on VariationRecord."""

    def test_add_phrase(self):
        """Can add phrases to a record."""
        record = VariationRecord(
            variation_id="v-1",
            project_id="p-1",
            base_state_id="0",
            intent="test",
        )

        phrase = PhraseRecord(
            phrase_id="p-1",
            variation_id="v-1",
            sequence=2,
            track_id="t-1",
            region_id="r-1",
            beat_start=0.0,
            beat_end=16.0,
            label="Bars 1-4",
            diff_json={"note_changes": []},
        )

        record.add_phrase(phrase)
        assert len(record.phrases) == 1
        assert record.phrases[0].phrase_id == "p-1"

    def test_get_phrase_by_id(self):
        """Can find a phrase by ID."""
        record = VariationRecord(
            variation_id="v-1",
            project_id="p-1",
            base_state_id="0",
            intent="test",
        )

        record.add_phrase(PhraseRecord(
            phrase_id="p-1", variation_id="v-1", sequence=2,
            track_id="t-1", region_id="r-1",
            beat_start=0, beat_end=16, label="Bars 1-4",
            diff_json={},
        ))
        record.add_phrase(PhraseRecord(
            phrase_id="p-2", variation_id="v-1", sequence=3,
            track_id="t-1", region_id="r-1",
            beat_start=16, beat_end=32, label="Bars 5-8",
            diff_json={},
        ))

        found = record.get_phrase("p-2")
        assert found is not None
        assert found.phrase_id == "p-2"
        assert found.label == "Bars 5-8"

    def test_get_phrase_missing_returns_none(self):
        """get_phrase returns None for missing IDs."""
        record = VariationRecord(
            variation_id="v-1",
            project_id="p-1",
            base_state_id="0",
            intent="test",
        )

        assert record.get_phrase("nonexistent") is None

    def test_get_phrase_ids_ordered(self):
        """get_phrase_ids returns IDs sorted by sequence."""
        record = VariationRecord(
            variation_id="v-1",
            project_id="p-1",
            base_state_id="0",
            intent="test",
        )

        # Add out of order
        record.add_phrase(PhraseRecord(
            phrase_id="p-3", variation_id="v-1", sequence=4,
            track_id="t", region_id="r",
            beat_start=32, beat_end=48, label="Bars 9-12",
            diff_json={},
        ))
        record.add_phrase(PhraseRecord(
            phrase_id="p-1", variation_id="v-1", sequence=2,
            track_id="t", region_id="r",
            beat_start=0, beat_end=16, label="Bars 1-4",
            diff_json={},
        ))
        record.add_phrase(PhraseRecord(
            phrase_id="p-2", variation_id="v-1", sequence=3,
            track_id="t", region_id="r",
            beat_start=16, beat_end=32, label="Bars 5-8",
            diff_json={},
        ))

        ids = record.get_phrase_ids()
        assert ids == ["p-1", "p-2", "p-3"]


# =============================================================================
# Sequence Counter
# =============================================================================


class TestRecordSequence:
    """Test per-variation sequence counter."""

    def test_next_sequence_starts_at_one(self):
        """First next_sequence() returns 1."""
        record = VariationRecord(
            variation_id="v-1",
            project_id="p-1",
            base_state_id="0",
            intent="test",
        )

        assert record.next_sequence() == 1

    def test_next_sequence_increments(self):
        """Sequence increases monotonically."""
        record = VariationRecord(
            variation_id="v-1",
            project_id="p-1",
            base_state_id="0",
            intent="test",
        )

        assert record.next_sequence() == 1
        assert record.next_sequence() == 2
        assert record.next_sequence() == 3

    def test_last_sequence_tracks(self):
        """last_sequence returns the most recent value."""
        record = VariationRecord(
            variation_id="v-1",
            project_id="p-1",
            base_state_id="0",
            intent="test",
        )

        record.next_sequence()
        record.next_sequence()
        assert record.last_sequence == 2


# =============================================================================
# Project Listing
# =============================================================================


class TestProjectListing:
    """Test listing variations for a project."""

    def test_list_for_project(self, store):
        """list_for_project returns only matching project's variations."""
        store.create(project_id="proj-A", base_state_id="0", intent="a")
        store.create(project_id="proj-A", base_state_id="0", intent="b")
        store.create(project_id="proj-B", base_state_id="0", intent="c")

        results = store.list_for_project("proj-A")
        assert len(results) == 2
        assert all(r.project_id == "proj-A" for r in results)

    def test_list_with_status_filter(self, store):
        """list_for_project can filter by status."""
        r1 = store.create(project_id="p", base_state_id="0", intent="a")
        r2 = store.create(project_id="p", base_state_id="0", intent="b")

        store.transition(r1.variation_id, VariationStatus.STREAMING)

        streaming = store.list_for_project("p", status=VariationStatus.STREAMING)
        created = store.list_for_project("p", status=VariationStatus.CREATED)

        assert len(streaming) == 1
        assert len(created) == 1


# =============================================================================
# Deletion and Cleanup
# =============================================================================


class TestCleanup:
    """Test record deletion and TTL cleanup."""

    def test_delete_existing(self, store):
        """delete() removes the record and returns True."""
        record = store.create(project_id="p", base_state_id="0", intent="test")

        assert store.delete(record.variation_id) is True
        assert store.get(record.variation_id) is None
        assert store.count == 0

    def test_delete_missing(self, store):
        """delete() returns False for missing records."""
        assert store.delete("nonexistent") is False

    def test_clear(self, store):
        """clear() removes all records."""
        store.create(project_id="p", base_state_id="0", intent="a")
        store.create(project_id="p", base_state_id="0", intent="b")

        store.clear()
        assert store.count == 0

    def test_cleanup_expired(self, store):
        """cleanup_expired expires old non-terminal records."""
        record = store.create(project_id="p", base_state_id="0", intent="test")

        # Backdate the record
        record.created_at = datetime.now(timezone.utc) - timedelta(hours=2)

        expired_count = store.cleanup_expired(max_age_seconds=3600)

        assert expired_count == 1
        assert record.status == VariationStatus.EXPIRED

    def test_cleanup_skips_terminal(self, store):
        """cleanup_expired does not touch terminal records."""
        record = store.create(project_id="p", base_state_id="0", intent="test")
        store.transition(record.variation_id, VariationStatus.DISCARDED)

        # Backdate
        record.created_at = datetime.now(timezone.utc) - timedelta(hours=2)

        expired_count = store.cleanup_expired(max_age_seconds=3600)
        assert expired_count == 0
        assert record.status == VariationStatus.DISCARDED

    def test_cleanup_skips_recent(self, store):
        """cleanup_expired does not touch recent records."""
        store.create(project_id="p", base_state_id="0", intent="test")

        expired_count = store.cleanup_expired(max_age_seconds=3600)
        assert expired_count == 0


# =============================================================================
# Singleton
# =============================================================================


class TestSingleton:
    """Test singleton access."""

    def test_singleton_returns_same_instance(self):
        """get_variation_store returns the same instance."""
        store1 = get_variation_store()
        store2 = get_variation_store()
        assert store1 is store2

    def test_reset_clears_singleton(self):
        """reset_variation_store clears the singleton."""
        store1 = get_variation_store()
        store1.create(project_id="p", base_state_id="0", intent="test")

        reset_variation_store()

        store2 = get_variation_store()
        assert store2.count == 0
        assert store1 is not store2


# =============================================================================
# VariationRecord Direct Tests
# =============================================================================


class TestVariationRecord:
    """Test VariationRecord behavior directly."""

    def test_transition_to_valid(self):
        """transition_to works for valid transitions."""
        record = VariationRecord(
            variation_id="v-1",
            project_id="p-1",
            base_state_id="0",
            intent="test",
        )

        record.transition_to(VariationStatus.STREAMING)
        assert record.status == VariationStatus.STREAMING

    def test_transition_to_invalid_raises(self):
        """transition_to raises for invalid transitions."""
        record = VariationRecord(
            variation_id="v-1",
            project_id="p-1",
            base_state_id="0",
            intent="test",
        )

        with pytest.raises(InvalidTransitionError):
            record.transition_to(VariationStatus.COMMITTED)

    def test_base_state_id_preserved(self):
        """base_state_id is set at creation and doesn't change."""
        record = VariationRecord(
            variation_id="v-1",
            project_id="p-1",
            base_state_id="42",
            intent="test",
        )

        record.transition_to(VariationStatus.STREAMING)
        record.transition_to(VariationStatus.READY)

        assert record.base_state_id == "42"
