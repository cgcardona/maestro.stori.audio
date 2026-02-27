"""
Tests for the Event Envelope system.

Covers envelope construction, serialization, SSE formatting,
sequence counters, builder helpers, and the Phrase→wire serialization
helpers (build_phrase_payload, note_change_to_wire, _snapshot_to_note_dict).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from app.models.variation import Phrase

from app.variation.core.event_envelope import (
    AnyEnvelope,
    DonePayload,
    ErrorPayload,
    EventEnvelope,
    MetaPayload,
    PhrasePayload,
    SequenceCounter,
    build_done_envelope,
    build_envelope,
    build_error_envelope,
    build_meta_envelope,
    build_phrase_envelope,
    build_phrase_payload,
    note_change_to_wire,
    _snapshot_to_note_dict,
)


# =============================================================================
# Envelope Construction
# =============================================================================


class TestEnvelopeConstruction:
    """Test EventEnvelope creation and fields."""

    def test_build_envelope_basic(self) -> None:

        """build_envelope creates a valid envelope with all fields."""
        payload: MetaPayload = {"intent": "make it darker"}
        envelope = build_envelope(
            event_type="meta",
            payload=payload,
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

    def test_envelope_is_immutable(self) -> None:

        """EventEnvelope is frozen (immutable)."""
        empty_meta: MetaPayload = {}
        envelope = build_envelope(
            event_type="meta",
            payload=empty_meta,
            sequence=1,
            variation_id="v",
        )

        with pytest.raises(AttributeError):
            setattr(envelope, "sequence", 2)

    def test_envelope_to_dict(self) -> None:

        """to_dict returns all required fields."""
        phrase_payload: PhrasePayload = {"phraseId": "p-1"}
        envelope = build_envelope(
            event_type="phrase",
            payload=phrase_payload,
            sequence=3,
            variation_id="var-1",
            project_id="proj-1",
            base_state_id="10",
        )

        d = envelope.to_dict()

        assert d["type"] == "phrase"
        assert d["sequence"] == 3
        assert d["variationId"] == "var-1"
        assert d["projectId"] == "proj-1"
        assert d["baseStateId"] == "10"
        assert d["payload"] == {"phraseId": "p-1"}
        assert "timestampMs" in d

    def test_envelope_to_json(self) -> None:

        """to_json returns valid JSON string."""
        done_payload: DonePayload = {"status": "ready"}
        envelope = build_envelope(
            event_type="done",
            payload=done_payload,
            sequence=5,
            variation_id="var-1",
        )

        json_str = envelope.to_json()
        parsed = json.loads(json_str)

        assert parsed["type"] == "done"
        assert parsed["sequence"] == 5
        assert parsed["payload"]["status"] == "ready"

    def test_envelope_to_sse(self) -> None:

        """to_sse formats as valid SSE event string."""
        meta_payload: MetaPayload = {"intent": "test"}
        envelope = build_envelope(
            event_type="meta",
            payload=meta_payload,
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

    def test_starts_at_zero(self) -> None:

        """Counter starts at 0 (first next() returns 1)."""
        counter = SequenceCounter()
        assert counter.current == 0

    def test_next_increments(self) -> None:

        """next() returns strictly increasing values."""
        counter = SequenceCounter()

        assert counter.next() == 1
        assert counter.next() == 2
        assert counter.next() == 3

    def test_current_tracks_last_value(self) -> None:

        """current property returns the last emitted value."""
        counter = SequenceCounter()

        counter.next()
        counter.next()
        assert counter.current == 2

        counter.next()
        assert counter.current == 3

    def test_reset(self) -> None:

        """reset() brings counter back to 0."""
        counter = SequenceCounter()
        counter.next()
        counter.next()

        counter.reset()
        assert counter.current == 0
        assert counter.next() == 1

    def test_sequence_never_repeats(self) -> None:

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

    def test_build_meta_envelope(self) -> None:

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
        # build_meta_envelope returns EventEnvelope[MetaPayload] — no cast needed
        assert envelope.payload["intent"] == "make it minor"
        assert envelope.payload["aiExplanation"] == "Lowered thirds"
        assert envelope.payload["affectedTracks"] == ["track-1"]
        note_counts = envelope.payload["noteCounts"]
        assert isinstance(note_counts, dict)
        assert note_counts["modified"] == 4

    def test_build_phrase_envelope(self) -> None:

        """Phrase envelope has correct structure."""
        phrase_data: PhrasePayload = {
            "phraseId": "p-1",
            "trackId": "t-1",
            "regionId": "r-1",
            "startBeat": 0.0,
            "endBeat": 4.0,
            "label": "Bars 1-4",
            "noteChanges": [],
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
        # build_phrase_envelope returns EventEnvelope[PhrasePayload] — no cast needed
        assert envelope.payload["phraseId"] == "p-1"
        assert envelope.payload["startBeat"] == 0.0

    def test_build_done_envelope(self) -> None:

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
        # build_done_envelope returns EventEnvelope[DonePayload] — no cast needed
        assert envelope.payload["status"] == "ready"
        assert envelope.payload["phraseCount"] == 3

    def test_build_done_envelope_failed(self) -> None:

        """Done envelope with failed status."""
        envelope = build_done_envelope(
            variation_id="var-1",
            project_id="proj-1",
            base_state_id="10",
            sequence=3,
            status="failed",
            phrase_count=0,
        )

        # build_done_envelope returns EventEnvelope[DonePayload] — no cast needed
        assert envelope.payload["status"] == "failed"

    def test_build_error_envelope(self) -> None:

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
        # build_error_envelope returns EventEnvelope[ErrorPayload] — no cast needed
        assert envelope.payload["message"] == "Generation failed"
        assert envelope.payload["code"] == "GENERATION_ERROR"


# =============================================================================
# Ordering Invariants
# =============================================================================


class TestOrderingInvariants:
    """Test that event ordering follows the v1 spec."""

    def test_meta_is_sequence_one(self) -> None:

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

    def test_phrases_follow_meta(self) -> None:

        """Phrase events should have sequence > 1."""
        counter = SequenceCounter()
        meta_seq = counter.next()  # 1
        phrase_seq = counter.next()  # 2

        assert meta_seq == 1
        assert phrase_seq == 2
        assert phrase_seq > meta_seq

    def test_done_is_last(self) -> None:

        """Done event should have the highest sequence number."""
        counter = SequenceCounter()
        counter.next()  # meta = 1
        counter.next()  # phrase = 2
        counter.next()  # phrase = 3
        done_seq = counter.next()  # done = 4

        assert done_seq == 4
        assert done_seq == counter.current

    def test_error_then_done_sequence(self) -> None:

        """Error should be followed by done, in strict sequence order."""
        counter = SequenceCounter()
        counter.next()  # meta = 1
        error_seq = counter.next()  # error = 2
        done_seq = counter.next()  # done = 3

        assert done_seq == error_seq + 1

    def test_full_event_stream_ordering(self) -> None:

        """Simulate a full event stream and verify ordering."""
        counter = SequenceCounter()
        vid = "var-test"
        pid = "proj-test"
        bsid = "0"

        events: list[AnyEnvelope] = []

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
                phrase_data={"phraseId": f"p-{i}"},
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


# =============================================================================
# Phrase serialization helpers
# =============================================================================


class TestSnapshotToNoteDict:
    """_snapshot_to_note_dict converts a MidiNoteSnapshot to NoteChangeDict."""

    def test_all_fields_mapped(self) -> None:
        """All MidiNoteSnapshot fields reach the output NoteChangeDict."""
        from app.models.variation import MidiNoteSnapshot
        snap = MidiNoteSnapshot(pitch=64, start_beat=2.0, duration_beats=0.5, velocity=90, channel=3)
        result = _snapshot_to_note_dict(snap)
        assert result["pitch"] == 64
        assert result["startBeat"] == 2.0
        assert result["durationBeats"] == 0.5
        assert result["velocity"] == 90
        assert result["channel"] == 3

    def test_snake_case_mapped_to_camel_case(self) -> None:
        """start_beat → startBeat, duration_beats → durationBeats."""
        from app.models.variation import MidiNoteSnapshot
        snap = MidiNoteSnapshot(pitch=60, start_beat=1.5, duration_beats=2.0)
        result = _snapshot_to_note_dict(snap)
        assert "startBeat" in result
        assert "durationBeats" in result
        assert "start_beat" not in result
        assert "duration_beats" not in result


class TestNoteChangeToWire:
    """note_change_to_wire serialises a NoteChange to NoteChangeEntryDict."""

    def test_added_change_type(self) -> None:
        """added: before=None, after is set."""
        from app.models.variation import NoteChange, MidiNoteSnapshot
        nc = NoteChange(
            note_id="nc-1",
            change_type="added",
            before=None,
            after=MidiNoteSnapshot(pitch=60, start_beat=0.0, duration_beats=1.0),
        )
        wire = note_change_to_wire(nc)
        assert wire["noteId"] == "nc-1"
        assert wire["changeType"] == "added"
        assert wire["before"] is None
        assert wire["after"] is not None
        assert wire["after"]["pitch"] == 60

    def test_removed_change_type(self) -> None:
        """removed: before is set, after=None."""
        from app.models.variation import NoteChange, MidiNoteSnapshot
        nc = NoteChange(
            note_id="nc-2",
            change_type="removed",
            before=MidiNoteSnapshot(pitch=72, start_beat=1.0, duration_beats=0.5),
            after=None,
        )
        wire = note_change_to_wire(nc)
        assert wire["changeType"] == "removed"
        assert wire["before"] is not None
        assert wire["before"]["pitch"] == 72
        assert wire["after"] is None

    def test_modified_change_type(self) -> None:
        """modified: both before and after are set."""
        from app.models.variation import NoteChange, MidiNoteSnapshot
        nc = NoteChange(
            note_id="nc-3",
            change_type="modified",
            before=MidiNoteSnapshot(pitch=60, start_beat=0.0, duration_beats=1.0, velocity=80),
            after=MidiNoteSnapshot(pitch=62, start_beat=0.0, duration_beats=1.0, velocity=90),
        )
        wire = note_change_to_wire(nc)
        assert wire["changeType"] == "modified"
        assert wire["before"] is not None
        assert wire["after"] is not None
        assert wire["before"]["pitch"] == 60
        assert wire["after"]["pitch"] == 62
        assert wire["after"]["velocity"] == 90

    def test_required_keys_always_present(self) -> None:
        """noteId and changeType are Required in NoteChangeEntryDict."""
        from app.models.variation import NoteChange, MidiNoteSnapshot
        nc = NoteChange(
            note_id="nc-x",
            change_type="added",
            before=None,
            after=MidiNoteSnapshot(pitch=55, start_beat=0.0, duration_beats=1.0),
        )
        wire = note_change_to_wire(nc)
        assert "noteId" in wire
        assert "changeType" in wire


class TestBuildPhrasePayload:
    """build_phrase_payload produces a complete, typed PhrasePayload."""

    def _make_phrase(self) -> "Phrase":
        from app.models.variation import Phrase, NoteChange, MidiNoteSnapshot
        from app.contracts.json_types import CCEventDict, PitchBendDict, AftertouchDict
        return Phrase(
            phrase_id="p-1",
            track_id="track-1",
            region_id="region-1",
            start_beat=0.0,
            end_beat=4.0,
            label="Bars 1-2",
            note_changes=[
                NoteChange(
                    note_id="nc-1",
                    change_type="added",
                    before=None,
                    after=MidiNoteSnapshot(pitch=60, start_beat=0.0, duration_beats=1.0),
                )
            ],
            cc_events=[CCEventDict(cc=64, beat=0.0, value=127)],
            pitch_bends=[PitchBendDict(beat=1.0, value=200)],
            aftertouch=[AftertouchDict(beat=0.5, value=80)],
            explanation="test phrase",
            tags=["rhythmChange"],
        )

    def test_scalar_fields(self) -> None:
        """All scalar Phrase fields are present in the payload."""
        from app.models.variation import Phrase
        phrase = self._make_phrase()
        assert isinstance(phrase, Phrase)
        payload = build_phrase_payload(phrase)
        assert payload["phraseId"] == "p-1"
        assert payload["trackId"] == "track-1"
        assert payload["regionId"] == "region-1"
        assert payload["startBeat"] == 0.0
        assert payload["endBeat"] == 4.0
        assert payload["label"] == "Bars 1-2"
        assert payload["explanation"] == "test phrase"
        assert payload["tags"] == ["rhythmChange"]

    def test_note_changes_serialised(self) -> None:
        """noteChanges contains typed NoteChangeEntryDict entries."""
        from app.models.variation import Phrase
        phrase = self._make_phrase()
        assert isinstance(phrase, Phrase)
        payload = build_phrase_payload(phrase)
        ncs = payload["noteChanges"]
        assert len(ncs) == 1
        assert ncs[0]["noteId"] == "nc-1"
        assert ncs[0]["changeType"] == "added"
        assert ncs[0]["before"] is None
        after = ncs[0]["after"]
        assert after is not None
        assert after["pitch"] == 60

    def test_cc_pitch_aftertouch_passthrough(self) -> None:
        """CC, pitch-bend, and aftertouch lists are passed through unchanged."""
        from app.models.variation import Phrase
        phrase = self._make_phrase()
        assert isinstance(phrase, Phrase)
        payload = build_phrase_payload(phrase)
        assert payload["ccEvents"][0]["cc"] == 64
        assert payload["ccEvents"][0]["value"] == 127
        assert payload["pitchBends"][0]["value"] == 200
        assert payload["aftertouch"][0]["beat"] == 0.5
        assert payload["aftertouch"][0]["value"] == 80

    def test_round_trip_via_envelope(self) -> None:
        """build_phrase_payload output can be passed directly to build_phrase_envelope."""
        from app.models.variation import Phrase
        phrase = self._make_phrase()
        assert isinstance(phrase, Phrase)
        payload = build_phrase_payload(phrase)
        env = build_phrase_envelope(
            variation_id="v-1",
            project_id="proj-1",
            base_state_id="base",
            sequence=2,
            phrase_data=payload,
        )
        assert env.type == "phrase"
        # build_phrase_envelope returns EventEnvelope[PhrasePayload] — no cast needed
        assert env.payload["phraseId"] == "p-1"

    def test_no_model_dump_any_leakage(self) -> None:
        """noteChanges entries have the exact NoteChangeEntryDict keys, not arbitrary model_dump keys."""
        from app.models.variation import Phrase
        phrase = self._make_phrase()
        assert isinstance(phrase, Phrase)
        payload = build_phrase_payload(phrase)
        nc = payload["noteChanges"][0]
        # Required keys always present
        assert "noteId" in nc
        assert "changeType" in nc
        # snake_case keys from model_dump must NOT appear
        assert "note_id" not in nc
        assert "change_type" not in nc
