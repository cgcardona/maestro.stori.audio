"""
Tests for the Event Envelope system.

Covers envelope construction, serialization, SSE formatting,
sequence counters, and builder helpers per the v1 canonical spec.
"""

import json
import pytest

from app.variation.core.event_envelope import (
    EventEnvelope,
    SequenceCounter,
    build_envelope,
    build_meta_envelope,
    build_phrase_envelope,
    build_done_envelope,
    build_error_envelope,
)


# =============================================================================
# Envelope Construction
# =============================================================================


class TestEnvelopeConstruction:
    """Test EventEnvelope creation and fields."""

    def test_build_envelope_basic(self):
        """build_envelope creates a valid envelope with all fields."""
        envelope = build_envelope(
            event_type="meta",
            payload={"intent": "make it darker"},
            sequence=1,
            variation_id="var-123",
            project_id="proj-456",
            base_state_id="42",
        )

        assert envelope.type == "meta"
        assert envelope.sequence == 1
        assert envelope.variation_id == "var-123"
        assert envelope.project_id == "proj-456"
        assert envelope.base_state_id == "42"
        assert envelope.payload == {"intent": "make it darker"}
        assert isinstance(envelope.timestamp_ms, int)
        assert envelope.timestamp_ms > 0

    def test_envelope_is_immutable(self):
        """EventEnvelope is frozen (immutable)."""
        envelope = build_envelope(
            event_type="meta",
            payload={},
            sequence=1,
            variation_id="v",
        )

        with pytest.raises(AttributeError):
            envelope.sequence = 2  # type: ignore[misc]

    def test_envelope_to_dict(self):
        """to_dict returns all required fields."""
        envelope = build_envelope(
            event_type="phrase",
            payload={"phrase_id": "p-1"},
            sequence=3,
            variation_id="var-1",
            project_id="proj-1",
            base_state_id="10",
        )

        d = envelope.to_dict()

        assert d["type"] == "phrase"
        assert d["sequence"] == 3
        assert d["variation_id"] == "var-1"
        assert d["project_id"] == "proj-1"
        assert d["base_state_id"] == "10"
        assert d["payload"] == {"phrase_id": "p-1"}
        assert "timestamp_ms" in d

    def test_envelope_to_json(self):
        """to_json returns valid JSON string."""
        envelope = build_envelope(
            event_type="done",
            payload={"status": "ready"},
            sequence=5,
            variation_id="var-1",
        )

        json_str = envelope.to_json()
        parsed = json.loads(json_str)

        assert parsed["type"] == "done"
        assert parsed["sequence"] == 5
        assert parsed["payload"]["status"] == "ready"

    def test_envelope_to_sse(self):
        """to_sse formats as valid SSE event string."""
        envelope = build_envelope(
            event_type="meta",
            payload={"intent": "test"},
            sequence=1,
            variation_id="var-1",
        )

        sse = envelope.to_sse()

        assert sse.startswith("event: meta\n")
        assert "data: " in sse
        assert sse.endswith("\n\n")

        # Parse the data portion
        lines = sse.strip().split("\n")
        assert lines[0] == "event: meta"
        data_line = lines[1]
        assert data_line.startswith("data: ")
        data_json = json.loads(data_line[6:])
        assert data_json["type"] == "meta"


# =============================================================================
# Sequence Counter
# =============================================================================


class TestSequenceCounter:
    """Test monotonic sequence counter."""

    def test_starts_at_zero(self):
        """Counter starts at 0 (first next() returns 1)."""
        counter = SequenceCounter()
        assert counter.current == 0

    def test_next_increments(self):
        """next() returns strictly increasing values."""
        counter = SequenceCounter()

        assert counter.next() == 1
        assert counter.next() == 2
        assert counter.next() == 3

    def test_current_tracks_last_value(self):
        """current property returns the last emitted value."""
        counter = SequenceCounter()

        counter.next()
        counter.next()
        assert counter.current == 2

        counter.next()
        assert counter.current == 3

    def test_reset(self):
        """reset() brings counter back to 0."""
        counter = SequenceCounter()
        counter.next()
        counter.next()

        counter.reset()
        assert counter.current == 0
        assert counter.next() == 1

    def test_sequence_never_repeats(self):
        """Sequence values are unique and strictly increasing."""
        counter = SequenceCounter()
        seen = set()

        for _ in range(100):
            val = counter.next()
            assert val not in seen
            seen.add(val)

        assert len(seen) == 100


# =============================================================================
# Builder Helpers
# =============================================================================


class TestBuilderHelpers:
    """Test convenience builder functions."""

    def test_build_meta_envelope(self):
        """Meta envelope has correct structure."""
        envelope = build_meta_envelope(
            variation_id="var-1",
            project_id="proj-1",
            base_state_id="10",
            intent="make it minor",
            ai_explanation="Lowered thirds",
            affected_tracks=["track-1"],
            affected_regions=["region-1"],
            note_counts={"added": 0, "removed": 0, "modified": 4},
        )

        assert envelope.type == "meta"
        assert envelope.sequence == 1
        assert envelope.payload["intent"] == "make it minor"
        assert envelope.payload["ai_explanation"] == "Lowered thirds"
        assert envelope.payload["affected_tracks"] == ["track-1"]
        assert envelope.payload["note_counts"]["modified"] == 4

    def test_build_phrase_envelope(self):
        """Phrase envelope has correct structure."""
        phrase_data = {
            "phrase_id": "p-1",
            "track_id": "t-1",
            "region_id": "r-1",
            "start_beat": 0.0,
            "end_beat": 4.0,
            "label": "Bars 1-4",
            "note_changes": [],
        }

        envelope = build_phrase_envelope(
            variation_id="var-1",
            project_id="proj-1",
            base_state_id="10",
            sequence=2,
            phrase_data=phrase_data,
        )

        assert envelope.type == "phrase"
        assert envelope.sequence == 2
        assert envelope.payload["phrase_id"] == "p-1"
        assert envelope.payload["start_beat"] == 0.0

    def test_build_done_envelope(self):
        """Done envelope has correct structure."""
        envelope = build_done_envelope(
            variation_id="var-1",
            project_id="proj-1",
            base_state_id="10",
            sequence=5,
            status="ready",
            phrase_count=3,
        )

        assert envelope.type == "done"
        assert envelope.sequence == 5
        assert envelope.payload["status"] == "ready"
        assert envelope.payload["phrase_count"] == 3

    def test_build_done_envelope_failed(self):
        """Done envelope with failed status."""
        envelope = build_done_envelope(
            variation_id="var-1",
            project_id="proj-1",
            base_state_id="10",
            sequence=3,
            status="failed",
            phrase_count=0,
        )

        assert envelope.payload["status"] == "failed"

    def test_build_error_envelope(self):
        """Error envelope has correct structure."""
        envelope = build_error_envelope(
            variation_id="var-1",
            project_id="proj-1",
            base_state_id="10",
            sequence=4,
            error_message="Generation failed",
            error_code="GENERATION_ERROR",
        )

        assert envelope.type == "error"
        assert envelope.payload["message"] == "Generation failed"
        assert envelope.payload["code"] == "GENERATION_ERROR"


# =============================================================================
# Ordering Invariants
# =============================================================================


class TestOrderingInvariants:
    """Test that event ordering follows the v1 spec."""

    def test_meta_is_sequence_one(self):
        """Meta event should be sequence 1."""
        meta = build_meta_envelope(
            variation_id="v-1",
            project_id="p-1",
            base_state_id="0",
            intent="test",
            ai_explanation=None,
            affected_tracks=[],
            affected_regions=[],
            note_counts={"added": 0, "removed": 0, "modified": 0},
        )

        assert meta.sequence == 1

    def test_phrases_follow_meta(self):
        """Phrase events should have sequence > 1."""
        counter = SequenceCounter()
        meta_seq = counter.next()  # 1
        phrase_seq = counter.next()  # 2

        assert meta_seq == 1
        assert phrase_seq == 2
        assert phrase_seq > meta_seq

    def test_done_is_last(self):
        """Done event should have the highest sequence number."""
        counter = SequenceCounter()
        counter.next()  # meta = 1
        counter.next()  # phrase = 2
        counter.next()  # phrase = 3
        done_seq = counter.next()  # done = 4

        assert done_seq == 4
        assert done_seq == counter.current

    def test_error_then_done_sequence(self):
        """Error should be followed by done, in strict sequence order."""
        counter = SequenceCounter()
        counter.next()  # meta = 1
        error_seq = counter.next()  # error = 2
        done_seq = counter.next()  # done = 3

        assert done_seq == error_seq + 1

    def test_full_event_stream_ordering(self):
        """Simulate a full event stream and verify ordering."""
        counter = SequenceCounter()
        vid = "var-test"
        pid = "proj-test"
        bsid = "0"

        events = []

        # meta (seq 1)
        events.append(build_meta_envelope(
            variation_id=vid, project_id=pid, base_state_id=bsid,
            intent="test", ai_explanation=None,
            affected_tracks=[], affected_regions=[],
            note_counts={"added": 0, "removed": 0, "modified": 0},
            sequence=counter.next(),
        ))

        # phrases (seq 2, 3, 4)
        for i in range(3):
            events.append(build_phrase_envelope(
                variation_id=vid, project_id=pid, base_state_id=bsid,
                sequence=counter.next(),
                phrase_data={"phrase_id": f"p-{i}"},
            ))

        # done (seq 5)
        events.append(build_done_envelope(
            variation_id=vid, project_id=pid, base_state_id=bsid,
            sequence=counter.next(),
            status="ready", phrase_count=3,
        ))

        # Verify ordering
        assert events[0].type == "meta"
        assert events[0].sequence == 1
        assert all(e.type == "phrase" for e in events[1:4])
        assert events[-1].type == "done"
        assert events[-1].sequence == 5

        # Verify strictly increasing sequence
        sequences = [e.sequence for e in events]
        assert sequences == sorted(sequences)
        assert len(set(sequences)) == len(sequences)  # No duplicates

        # Verify all events share the same variation context
        for event in events:
            assert event.variation_id == vid
            assert event.project_id == pid
            assert event.base_state_id == bsid
