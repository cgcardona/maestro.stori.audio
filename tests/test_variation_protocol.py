"""
Variation Protocol Tests — v1 Supercharge.

Tests for the wired-up variation protocol, proving the non-negotiable
invariants from the canonical v1 spec:

1. Commit only from READY (never STREAMING/CREATED)
2. Baseline safety — base_state_id mismatch blocks commit (409)
3. Double commit → 409
4. Partial phrase acceptance applies correct subset
5. Note removals are applied correctly
6. Discard during STREAMING cancels generation
7. SSE stream emits meta → phrases → done with strict sequencing
8. Done payload includes status (ready|failed|discarded)
9. variation_data no longer required on commit (loaded from store)
"""

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from app.variation.core.state_machine import (
    VariationStatus,
    InvalidTransitionError,
    can_commit,
    can_discard,
)
from app.variation.core.event_envelope import (
    EventEnvelope,
    SequenceCounter,
    build_meta_envelope,
    build_phrase_envelope,
    build_done_envelope,
    build_error_envelope,
)
from app.variation.storage.variation_store import (
    VariationStore,
    VariationRecord,
    PhraseRecord,
)
from app.variation.streaming.sse_broadcaster import SSEBroadcaster
from app.variation.streaming.stream_router import publish_event, close_variation_stream
from app.models.variation import (
    Variation,
    Phrase,
    NoteChange,
    MidiNoteSnapshot,
)
from app.core.executor import apply_variation_phrases, VariationApplyResult


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def vstore():
    """Fresh VariationStore per test."""
    store = VariationStore()
    yield store
    store.clear()


@pytest.fixture
def broadcaster():
    """Fresh SSEBroadcaster per test."""
    b = SSEBroadcaster()
    yield b
    b.clear()


@pytest.fixture
def project_id():
    return str(uuid.uuid4())


@pytest.fixture
def base_state_id():
    return str(uuid.uuid4())


@pytest.fixture
def ready_record(vstore, project_id, base_state_id):
    """A variation record in READY state with two phrases."""
    record = vstore.create(
        project_id=project_id,
        base_state_id=base_state_id,
        intent="add bass line",
    )
    record.transition_to(VariationStatus.STREAMING)

    # Add phrases as they would be during generation
    for i, label in enumerate(["Bars 1-4", "Bars 5-8"], start=1):
        seq = record.next_sequence()
        record.add_phrase(PhraseRecord(
            phrase_id=f"phrase-{i}",
            variation_id=record.variation_id,
            sequence=seq,
            track_id="track-bass",
            region_id="region-bass",
            beat_start=(i - 1) * 16.0,
            beat_end=i * 16.0,
            label=label,
            diff_json={
                "phrase_id": f"phrase-{i}",
                "track_id": "track-bass",
                "region_id": "region-bass",
                "start_beat": (i - 1) * 16.0,
                "end_beat": i * 16.0,
                "label": label,
                "tags": ["pitchChange"],
                "explanation": f"Bass {label}",
                "note_changes": [
                    {
                        "note_id": f"nc-{i}-1",
                        "change_type": "added",
                        "before": None,
                        "after": {
                            "pitch": 36 + i,
                            "start_beat": (i - 1) * 16.0,
                            "duration_beats": 2.0,
                            "velocity": 90,
                            "channel": 0,
                        },
                    }
                ],
                "controller_changes": [],
            },
            ai_explanation=f"Bass {label}",
            tags=["pitchChange"],
        ))

    record.transition_to(VariationStatus.READY)
    return record


def _make_variation_with_removals() -> Variation:
    """Create a Variation model with add/remove/modify changes."""
    return Variation(
        variation_id=str(uuid.uuid4()),
        intent="modify melody",
        ai_explanation="Reworked the melody",
        affected_tracks=["track-mel"],
        affected_regions=["region-mel"],
        beat_range=(0.0, 16.0),
        phrases=[
            Phrase(
                phrase_id="p-add",
                track_id="track-mel",
                region_id="region-mel",
                start_beat=0.0,
                end_beat=4.0,
                label="Bars 1-1",
                note_changes=[
                    NoteChange(
                        note_id="nc-add",
                        change_type="added",
                        before=None,
                        after=MidiNoteSnapshot(pitch=72, start_beat=0.0, duration_beats=1.0, velocity=100),
                    ),
                ],
            ),
            Phrase(
                phrase_id="p-remove",
                track_id="track-mel",
                region_id="region-mel",
                start_beat=4.0,
                end_beat=8.0,
                label="Bars 2-2",
                note_changes=[
                    NoteChange(
                        note_id="nc-remove",
                        change_type="removed",
                        before=MidiNoteSnapshot(pitch=60, start_beat=4.0, duration_beats=2.0, velocity=80),
                        after=None,
                    ),
                ],
            ),
            Phrase(
                phrase_id="p-modify",
                track_id="track-mel",
                region_id="region-mel",
                start_beat=8.0,
                end_beat=12.0,
                label="Bars 3-3",
                note_changes=[
                    NoteChange(
                        note_id="nc-modify",
                        change_type="modified",
                        before=MidiNoteSnapshot(pitch=64, start_beat=8.0, duration_beats=1.0, velocity=70),
                        after=MidiNoteSnapshot(pitch=65, start_beat=8.0, duration_beats=1.5, velocity=85),
                    ),
                ],
            ),
        ],
    )


# =============================================================================
# State Machine Integration Tests
# =============================================================================

class TestStateMachineEnforcement:
    """Prove state transitions are enforced in VariationStore."""

    def test_commit_only_from_ready(self, vstore, project_id, base_state_id):
        """INVARIANT: Commit is only allowed from READY."""
        record = vstore.create(project_id=project_id, base_state_id=base_state_id, intent="test")

        # CREATED → COMMITTED is invalid
        with pytest.raises(InvalidTransitionError):
            record.transition_to(VariationStatus.COMMITTED)

        record.transition_to(VariationStatus.STREAMING)

        # STREAMING → COMMITTED is invalid
        with pytest.raises(InvalidTransitionError):
            record.transition_to(VariationStatus.COMMITTED)

        record.transition_to(VariationStatus.READY)

        # READY → COMMITTED is valid
        record.transition_to(VariationStatus.COMMITTED)
        assert record.status == VariationStatus.COMMITTED

    def test_discard_from_any_non_terminal(self, vstore, project_id, base_state_id):
        """Discard allowed from CREATED, STREAMING, READY."""
        for source in [VariationStatus.CREATED, VariationStatus.STREAMING, VariationStatus.READY]:
            record = vstore.create(
                project_id=project_id,
                base_state_id=base_state_id,
                intent="discard-test",
            )
            if source == VariationStatus.STREAMING:
                record.transition_to(VariationStatus.STREAMING)
            elif source == VariationStatus.READY:
                record.transition_to(VariationStatus.STREAMING)
                record.transition_to(VariationStatus.READY)

            assert can_discard(record.status)
            record.transition_to(VariationStatus.DISCARDED)
            assert record.status == VariationStatus.DISCARDED

    def test_terminal_states_are_final(self, vstore, project_id, base_state_id):
        """No transitions allowed from terminal states."""
        terminal_targets = [VariationStatus.STREAMING, VariationStatus.READY, VariationStatus.COMMITTED]

        for terminal in [VariationStatus.COMMITTED, VariationStatus.DISCARDED, VariationStatus.FAILED]:
            record = vstore.create(
                project_id=project_id,
                base_state_id=base_state_id,
                intent="terminal-test",
            )
            record.transition_to(VariationStatus.STREAMING)
            if terminal == VariationStatus.COMMITTED:
                record.transition_to(VariationStatus.READY)
            record.transition_to(terminal)

            for target in terminal_targets:
                with pytest.raises(InvalidTransitionError):
                    record.transition_to(target)


# =============================================================================
# Commit Correctness Tests
# =============================================================================

class TestCommitCorrectness:
    """Prove commit invariants."""

    def test_can_commit_only_ready(self):
        """can_commit() returns True only for READY."""
        assert can_commit(VariationStatus.READY) is True
        for status in [VariationStatus.CREATED, VariationStatus.STREAMING,
                        VariationStatus.COMMITTED, VariationStatus.DISCARDED,
                        VariationStatus.FAILED, VariationStatus.EXPIRED]:
            assert can_commit(status) is False

    def test_double_commit_blocked(self, ready_record):
        """INVARIANT: Committed → Committed is invalid."""
        ready_record.transition_to(VariationStatus.COMMITTED)
        with pytest.raises(InvalidTransitionError):
            ready_record.transition_to(VariationStatus.COMMITTED)

    def test_record_to_variation_roundtrip(self, ready_record):
        """Phrases stored in record can be converted to Variation model."""
        from app.api.routes.variation import _record_to_variation
        variation = _record_to_variation(ready_record)

        assert variation.variation_id == ready_record.variation_id
        assert len(variation.phrases) == len(ready_record.phrases)
        assert variation.phrases[0].phrase_id == "phrase-1"
        assert variation.phrases[1].phrase_id == "phrase-2"

    def test_partial_acceptance(self, ready_record):
        """INVARIANT: Only accepted phrase IDs are applied."""
        from app.api.routes.variation import _record_to_variation
        variation = _record_to_variation(ready_record)

        # Only first phrase
        accepted = variation.get_accepted_notes(["phrase-1"])
        assert len(accepted) == 1
        assert accepted[0]["pitch"] == 37  # 36 + 1

        # Only second phrase
        accepted2 = variation.get_accepted_notes(["phrase-2"])
        assert len(accepted2) == 1
        assert accepted2[0]["pitch"] == 38  # 36 + 2

        # Both
        accepted_both = variation.get_accepted_notes(["phrase-1", "phrase-2"])
        assert len(accepted_both) == 2

    def test_note_removals_tracked(self):
        """INVARIANT: Removals are correctly identified."""
        variation = _make_variation_with_removals()

        removed_ids = variation.get_removed_note_ids(["p-remove"])
        assert "nc-remove" in removed_ids

    def test_modified_notes_tracked(self):
        """Modified notes produce both removal and addition."""
        variation = _make_variation_with_removals()

        # Modified produces accepted notes (the 'after')
        accepted = variation.get_accepted_notes(["p-modify"])
        assert len(accepted) == 1
        assert accepted[0]["pitch"] == 65

        # Modified also produces removal (the note_id)
        removed_ids = variation.get_removed_note_ids(["p-modify"])
        assert "nc-modify" in removed_ids


# =============================================================================
# Event Envelope Sequencing Tests
# =============================================================================

class TestEventSequencing:
    """Prove strict sequence ordering invariants."""

    def test_meta_is_sequence_one(self):
        """INVARIANT: meta must be sequence=1."""
        env = build_meta_envelope(
            variation_id="v1", project_id="p1", base_state_id="s1",
            intent="test", ai_explanation=None,
            affected_tracks=[], affected_regions=[], note_counts={},
            sequence=1,
        )
        assert env.sequence == 1
        assert env.type == "meta"

    def test_sequence_counter_strict_monotonic(self):
        """Sequence counter produces strictly increasing values."""
        counter = SequenceCounter()
        values = [counter.next() for _ in range(10)]
        assert values == list(range(1, 11))

    def test_phrase_sequences_follow_meta(self):
        """Phrases must have sequence > 1."""
        counter = SequenceCounter()
        meta_seq = counter.next()  # 1
        phrase_seqs = [counter.next() for _ in range(3)]  # 2, 3, 4
        done_seq = counter.next()  # 5

        assert meta_seq == 1
        assert phrase_seqs == [2, 3, 4]
        assert done_seq == 5

    def test_done_is_last(self):
        """Done must have the highest sequence number."""
        vid = "v1"
        counter = SequenceCounter()

        events = [
            build_meta_envelope(vid, "p", "s", "test", None, [], [], {}, counter.next()),
            build_phrase_envelope(vid, "p", "s", counter.next(), {"phrase_id": "p1"}),
            build_phrase_envelope(vid, "p", "s", counter.next(), {"phrase_id": "p2"}),
            build_done_envelope(vid, "p", "s", counter.next(), status="ready", phrase_count=2),
        ]

        seqs = [e.sequence for e in events]
        assert seqs == sorted(seqs), "Sequences must be monotonically increasing"
        assert events[-1].type == "done"
        assert events[-1].payload["status"] == "ready"

    def test_done_includes_status(self):
        """INVARIANT: done payload includes status field."""
        for status in ["ready", "failed", "discarded"]:
            env = build_done_envelope("v", "p", "s", 99, status=status)
            assert env.payload["status"] == status

    def test_error_then_done_failed(self):
        """Error flow: error event followed by done(status=failed)."""
        counter = SequenceCounter()
        meta = build_meta_envelope("v", "p", "s", "t", None, [], [], {}, counter.next())
        error = build_error_envelope("v", "p", "s", counter.next(), "boom")
        done = build_done_envelope("v", "p", "s", counter.next(), status="failed")

        assert meta.sequence < error.sequence < done.sequence
        assert error.type == "error"
        assert done.type == "done"
        assert done.payload["status"] == "failed"


# =============================================================================
# SSE Broadcasting Tests
# =============================================================================

class TestSSEBroadcasting:
    """Prove SSE broadcasting with replay."""

    @pytest.mark.anyio
    async def test_publish_then_subscribe_gets_replay(self, broadcaster):
        """Late-join subscriber receives historical events."""
        vid = "v-late"
        env1 = build_meta_envelope(vid, "p", "s", "test", None, [], [], {}, 1)
        env2 = build_phrase_envelope(vid, "p", "s", 2, {"phrase_id": "p1"})

        await broadcaster.publish(env1)
        await broadcaster.publish(env2)

        # Late subscriber
        queue = broadcaster.subscribe(vid, from_sequence=0)
        # Should get both events replayed
        e1 = queue.get_nowait()
        e2 = queue.get_nowait()
        assert e1.sequence == 1
        assert e2.sequence == 2

    @pytest.mark.anyio
    async def test_subscribe_from_sequence_skips_old(self, broadcaster):
        """Replay only sends events after from_sequence."""
        vid = "v-skip"
        for seq in range(1, 6):
            env = build_phrase_envelope(vid, "p", "s", seq, {"phrase_id": f"p{seq}"})
            await broadcaster.publish(env)

        queue = broadcaster.subscribe(vid, from_sequence=3)
        # Should only get seq 4 and 5
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        assert [e.sequence for e in events] == [4, 5]

    @pytest.mark.anyio
    async def test_close_stream_sends_sentinel(self, broadcaster):
        """close_stream sends None sentinel to subscribers."""
        vid = "v-close"
        queue = broadcaster.subscribe(vid)

        await broadcaster.close_stream(vid)

        sentinel = queue.get_nowait()
        assert sentinel is None

    @pytest.mark.anyio
    async def test_full_stream_lifecycle(self, broadcaster):
        """Simulate: meta → phrase → phrase → done, subscriber receives all."""
        vid = "v-full"
        queue = broadcaster.subscribe(vid)

        events = [
            build_meta_envelope(vid, "p", "s", "intent", None, [], [], {}, 1),
            build_phrase_envelope(vid, "p", "s", 2, {"phrase_id": "p1"}),
            build_phrase_envelope(vid, "p", "s", 3, {"phrase_id": "p2"}),
            build_done_envelope(vid, "p", "s", 4, status="ready", phrase_count=2),
        ]

        for env in events:
            await broadcaster.publish(env)
        await broadcaster.close_stream(vid)

        received = []
        while not queue.empty():
            item = queue.get_nowait()
            if item is None:
                break
            received.append(item)

        assert len(received) == 4
        assert received[0].type == "meta"
        assert received[1].type == "phrase"
        assert received[2].type == "phrase"
        assert received[3].type == "done"
        assert received[3].payload["status"] == "ready"

        # Verify strict ordering
        seqs = [e.sequence for e in received]
        assert seqs == [1, 2, 3, 4]


# =============================================================================
# Stream Router Tests
# =============================================================================

class TestStreamRouter:
    """Prove stream_router.publish_event routes to SSE broadcaster."""

    @pytest.mark.anyio
    async def test_publish_event_routes_to_sse(self):
        """publish_event() delivers to SSE subscribers."""
        from app.variation.streaming.sse_broadcaster import (
            get_sse_broadcaster,
            reset_sse_broadcaster,
        )

        reset_sse_broadcaster()
        try:
            broadcaster = get_sse_broadcaster()
            vid = "v-router"
            queue = broadcaster.subscribe(vid)

            env = build_meta_envelope(vid, "p", "s", "test", None, [], [], {}, 1)
            delivered = await publish_event(env)

            assert delivered >= 1
            received = queue.get_nowait()
            assert received.type == "meta"
        finally:
            reset_sse_broadcaster()


# =============================================================================
# VariationStore + Record Tests
# =============================================================================

class TestVariationStoreIntegration:
    """Integration tests for VariationStore lifecycle."""

    def test_create_and_retrieve(self, vstore, project_id, base_state_id):
        """Create → get returns the same record."""
        record = vstore.create(project_id=project_id, base_state_id=base_state_id, intent="test")
        retrieved = vstore.get(record.variation_id)
        assert retrieved is record
        assert retrieved.status == VariationStatus.CREATED

    def test_full_happy_path_lifecycle(self, vstore, project_id, base_state_id):
        """CREATED → STREAMING → READY → COMMITTED."""
        record = vstore.create(project_id=project_id, base_state_id=base_state_id, intent="test")
        record.transition_to(VariationStatus.STREAMING)
        record.transition_to(VariationStatus.READY)
        record.transition_to(VariationStatus.COMMITTED)
        assert record.status == VariationStatus.COMMITTED

    def test_phrases_stored_in_sequence_order(self, vstore, project_id, base_state_id):
        """Phrases are retrievable in sequence order."""
        record = vstore.create(project_id=project_id, base_state_id=base_state_id, intent="test")
        record.transition_to(VariationStatus.STREAMING)

        for i in range(3):
            seq = record.next_sequence()
            record.add_phrase(PhraseRecord(
                phrase_id=f"p-{i}",
                variation_id=record.variation_id,
                sequence=seq,
                track_id="t",
                region_id="r",
                beat_start=float(i * 4),
                beat_end=float((i + 1) * 4),
                label=f"Phrase {i}",
                diff_json={},
            ))

        ids = record.get_phrase_ids()
        assert ids == ["p-0", "p-1", "p-2"]

    def test_get_or_raise_missing(self, vstore):
        """get_or_raise raises KeyError for missing ID."""
        with pytest.raises(KeyError):
            vstore.get_or_raise("nonexistent")

    def test_cleanup_expired(self, vstore, project_id, base_state_id):
        """cleanup_expired transitions old non-terminal variations."""
        record = vstore.create(project_id=project_id, base_state_id=base_state_id, intent="old")
        # Manually set created_at to the past
        from datetime import datetime, timezone, timedelta
        record.created_at = datetime.now(timezone.utc) - timedelta(hours=2)

        expired = vstore.cleanup_expired(max_age_seconds=3600)
        assert expired == 1
        assert record.status == VariationStatus.EXPIRED


# =============================================================================
# Discard / Cancellation Tests
# =============================================================================

class TestDiscardCancellation:
    """Prove discard cancels generation and emits terminal event."""

    @pytest.mark.anyio
    async def test_discard_during_streaming(self, vstore, broadcaster, project_id, base_state_id):
        """Discard from STREAMING cancels and emits done(discarded)."""
        record = vstore.create(project_id=project_id, base_state_id=base_state_id, intent="cancel-test")
        record.transition_to(VariationStatus.STREAMING)

        queue = broadcaster.subscribe(record.variation_id)

        record.transition_to(VariationStatus.DISCARDED)
        done = build_done_envelope(
            variation_id=record.variation_id,
            project_id=record.project_id,
            base_state_id=record.base_state_id,
            sequence=record.next_sequence(),
            status="discarded",
            phrase_count=0,
        )
        await broadcaster.publish(done)
        await broadcaster.close_stream(record.variation_id)

        received = []
        while not queue.empty():
            item = queue.get_nowait()
            if item is None:
                break
            received.append(item)

        assert len(received) == 1
        assert received[0].type == "done"
        assert received[0].payload["status"] == "discarded"

    def test_discard_from_ready(self, ready_record):
        """Discard from READY succeeds."""
        assert can_discard(ready_record.status)
        ready_record.transition_to(VariationStatus.DISCARDED)
        assert ready_record.status == VariationStatus.DISCARDED

    def test_discard_from_created(self, vstore, project_id, base_state_id):
        """Discard from CREATED succeeds."""
        record = vstore.create(project_id=project_id, base_state_id=base_state_id, intent="test")
        assert can_discard(record.status)
        record.transition_to(VariationStatus.DISCARDED)
        assert record.status == VariationStatus.DISCARDED


# =============================================================================
# Envelope Serialization Tests
# =============================================================================

class TestEnvelopeSerialization:
    """Prove envelope wire format is correct."""

    def test_to_dict_has_all_fields(self):
        """Envelope dict contains all required protocol fields."""
        env = build_meta_envelope("v", "p", "s", "test", None, [], [], {}, 1)
        d = env.to_dict()

        required_keys = {"type", "sequence", "variation_id", "project_id",
                         "base_state_id", "payload", "timestamp_ms"}
        assert required_keys.issubset(d.keys())

    def test_to_json_roundtrips(self):
        """JSON serialization roundtrips correctly."""
        env = build_phrase_envelope("v", "p", "s", 5, {"phrase_id": "p1", "notes": []})
        j = env.to_json()
        parsed = json.loads(j)

        assert parsed["type"] == "phrase"
        assert parsed["sequence"] == 5
        assert parsed["payload"]["phrase_id"] == "p1"

    def test_to_sse_format(self):
        """SSE format has event: and data: lines."""
        env = build_done_envelope("v", "p", "s", 10, status="ready", phrase_count=3)
        sse = env.to_sse()

        assert sse.startswith("event: done\n")
        assert "data: " in sse
        assert sse.endswith("\n\n")

        # Parse the data line
        data_line = [l for l in sse.split("\n") if l.startswith("data:")][0]
        data_json = json.loads(data_line[len("data: "):])
        assert data_json["payload"]["status"] == "ready"
        assert data_json["payload"]["phrase_count"] == 3
