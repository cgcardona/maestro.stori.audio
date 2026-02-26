"""
Variation Commit Engine Tests — v1 Supercharge.

Proves correctness of apply_variation_phrases() including:
- Adds applied correctly
- Removals applied correctly (the previously missing case)
- Modified notes produce remove-old + add-new
- Partial acceptance only applies selected phrases
- Empty acceptance produces no-op
"""
from __future__ import annotations

import uuid
from typing import Literal
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from app.models.variation import (
    Variation,
    Phrase,
    NoteChange,
    MidiNoteSnapshot,
)
from app.core.executor import apply_variation_phrases, VariationApplyResult


def _make_test_variation(
    include_adds: bool = True,
    include_removes: bool = True,
    include_modifies: bool = True,
) -> Variation:
    """Build a test Variation with configurable change types."""
    phrases = []

    if include_adds:
        phrases.append(Phrase(
            phrase_id="p-adds",
            track_id="track-1",
            region_id="region-1",
            start_beat=0.0,
            end_beat=4.0,
            label="Bars 1-1",
            note_changes=[
                NoteChange(
                    note_id="nc-add-1",
                    change_type="added",
                    before=None,
                    after=MidiNoteSnapshot(pitch=60, start_beat=0.0, duration_beats=1.0, velocity=100),
                ),
                NoteChange(
                    note_id="nc-add-2",
                    change_type="added",
                    before=None,
                    after=MidiNoteSnapshot(pitch=64, start_beat=1.0, duration_beats=1.0, velocity=90),
                ),
            ],
        ))

    if include_removes:
        phrases.append(Phrase(
            phrase_id="p-removes",
            track_id="track-1",
            region_id="region-1",
            start_beat=4.0,
            end_beat=8.0,
            label="Bars 2-2",
            note_changes=[
                NoteChange(
                    note_id="nc-rm-1",
                    change_type="removed",
                    before=MidiNoteSnapshot(pitch=55, start_beat=4.0, duration_beats=2.0, velocity=80),
                    after=None,
                ),
            ],
        ))

    if include_modifies:
        phrases.append(Phrase(
            phrase_id="p-modifies",
            track_id="track-1",
            region_id="region-1",
            start_beat=8.0,
            end_beat=12.0,
            label="Bars 3-3",
            note_changes=[
                NoteChange(
                    note_id="nc-mod-1",
                    change_type="modified",
                    before=MidiNoteSnapshot(pitch=67, start_beat=8.0, duration_beats=1.0, velocity=70),
                    after=MidiNoteSnapshot(pitch=68, start_beat=8.0, duration_beats=1.5, velocity=85),
                ),
            ],
        ))

    beat_range = (0.0, 12.0)
    if phrases:
        beat_range = (
            min(p.start_beat for p in phrases),
            max(p.end_beat for p in phrases),
        )

    return Variation(
        variation_id=str(uuid.uuid4()),
        intent="test variation",
        ai_explanation="test",
        affected_tracks=["track-1"],
        affected_regions=["region-1"],
        beat_range=beat_range,
        phrases=phrases,
    )


def _mock_store() -> MagicMock:
    """Create a mock StateStore with add_notes, remove_notes, begin_transaction, commit."""
    store = MagicMock()
    store.begin_transaction.return_value = MagicMock()
    store.add_notes = MagicMock()
    store.remove_notes = MagicMock()
    store.commit = MagicMock()
    store.sync_from_client = MagicMock()
    # Region note queries used by updated_regions
    store.get_region_notes = MagicMock(return_value=[])
    store.get_region_track_id = MagicMock(return_value="track-1")
    return store


@pytest.fixture
def mock_store() -> MagicMock:
    return _mock_store()


class TestApplyVariationPhrases:
    """Test the commit engine."""

    @pytest.mark.anyio
    async def test_adds_only(self, mock_store: MagicMock) -> None:

        """Added notes call store.add_notes."""
        variation = _make_test_variation(include_adds=True, include_removes=False, include_modifies=False)

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=["p-adds"],
            project_state={},
            store=mock_store,
        )

        assert result.success is True
        assert result.notes_added == 2
        assert result.notes_removed == 0
        assert result.notes_modified == 0
        assert "p-adds" in result.applied_phrase_ids

        mock_store.add_notes.assert_called_once()
        call_args = mock_store.add_notes.call_args
        region_id = call_args[0][0]
        notes = call_args[0][1]
        assert region_id == "region-1"
        assert len(notes) == 2
        assert notes[0]["pitch"] == 60
        assert notes[1]["pitch"] == 64

    @pytest.mark.anyio
    async def test_removals_applied(self, mock_store: MagicMock) -> None:

        """INVARIANT: Removed notes call store.remove_notes with before snapshot."""
        variation = _make_test_variation(include_adds=False, include_removes=True, include_modifies=False)

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=["p-removes"],
            project_state={},
            store=mock_store,
        )

        assert result.success is True
        assert result.notes_removed == 1
        assert result.notes_added == 0

        # Verify remove_notes was called
        mock_store.remove_notes.assert_called_once()
        call_args = mock_store.remove_notes.call_args
        region_id = call_args[0][0]
        criteria = call_args[0][1]
        assert region_id == "region-1"
        assert len(criteria) == 1
        assert criteria[0]["pitch"] == 55
        assert criteria[0]["start_beat"] == 4.0

    @pytest.mark.anyio
    async def test_modified_notes_remove_old_add_new(self, mock_store: MagicMock) -> None:

        """INVARIANT: Modified notes produce remove(before) + add(after)."""
        variation = _make_test_variation(include_adds=False, include_removes=False, include_modifies=True)

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=["p-modifies"],
            project_state={},
            store=mock_store,
        )

        assert result.success is True
        assert result.notes_modified == 1

        # remove_notes called with old note
        mock_store.remove_notes.assert_called_once()
        rm_criteria = mock_store.remove_notes.call_args[0][1]
        assert len(rm_criteria) == 1
        assert rm_criteria[0]["pitch"] == 67

        # add_notes called with new note
        mock_store.add_notes.assert_called_once()
        add_notes = mock_store.add_notes.call_args[0][1]
        assert len(add_notes) == 1
        assert add_notes[0]["pitch"] == 68
        assert add_notes[0]["duration_beats"] == 1.5

    @pytest.mark.anyio
    async def test_partial_acceptance_subset(self, mock_store: MagicMock) -> None:

        """INVARIANT: Only accepted phrases are applied."""
        variation = _make_test_variation(include_adds=True, include_removes=True, include_modifies=True)

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=["p-adds"],  # Only adds
            project_state={},
            store=mock_store,
        )

        assert result.success is True
        assert result.applied_phrase_ids == ["p-adds"]
        assert result.notes_added == 2
        assert result.notes_removed == 0
        assert result.notes_modified == 0

        # remove_notes should NOT be called since we didn't accept p-removes or p-modifies
        mock_store.remove_notes.assert_not_called()

    @pytest.mark.anyio
    async def test_all_change_types_in_one_commit(self, mock_store: MagicMock) -> None:

        """All three change types applied in one transaction."""
        variation = _make_test_variation()

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=["p-adds", "p-removes", "p-modifies"],
            project_state={},
            store=mock_store,
        )

        assert result.success is True
        assert result.notes_added == 2
        assert result.notes_removed == 1
        assert result.notes_modified == 1
        assert len(result.applied_phrase_ids) == 3

        # Removals happen before adds (remove_notes before add_notes)
        assert mock_store.remove_notes.call_count == 1
        assert mock_store.add_notes.call_count == 1

        rm_criteria = mock_store.remove_notes.call_args[0][1]
        # 1 removed + 1 modified-before = 2 removals
        assert len(rm_criteria) == 2

        add_notes = mock_store.add_notes.call_args[0][1]
        # 2 added + 1 modified-after = 3 adds
        assert len(add_notes) == 3

    @pytest.mark.anyio
    async def test_empty_acceptance_is_noop(self, mock_store: MagicMock) -> None:

        """Empty accepted_phrase_ids applies nothing."""
        variation = _make_test_variation()

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=[],
            project_state={},
            store=mock_store,
        )

        assert result.success is True
        assert result.applied_phrase_ids == []
        assert result.notes_added == 0
        assert result.notes_removed == 0
        assert result.notes_modified == 0

    @pytest.mark.anyio
    async def test_unknown_phrase_id_skipped(self, mock_store: MagicMock) -> None:

        """Unknown phrase IDs are skipped with warning."""
        variation = _make_test_variation(include_adds=True, include_removes=False, include_modifies=False)

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=["p-adds", "p-nonexistent"],
            project_state={},
            store=mock_store,
        )

        # p-adds applied, p-nonexistent skipped
        assert result.success is True
        assert result.applied_phrase_ids == ["p-adds"]
        assert result.notes_added == 2

    @pytest.mark.anyio
    async def test_multi_region_changes(self, mock_store: MagicMock) -> None:

        """Changes across multiple regions are handled correctly."""
        variation = Variation(
            variation_id=str(uuid.uuid4()),
            intent="multi-region",
            beat_range=(0.0, 8.0),
            phrases=[
                Phrase(
                    phrase_id="p-r1",
                    track_id="track-1",
                    region_id="region-A",
                    start_beat=0.0,
                    end_beat=4.0,
                    label="Region A",
                    note_changes=[
                        NoteChange(
                            note_id="nc-a1",
                            change_type="added",
                            before=None,
                            after=MidiNoteSnapshot(pitch=60, start_beat=0.0, duration_beats=1.0),
                        ),
                    ],
                ),
                Phrase(
                    phrase_id="p-r2",
                    track_id="track-2",
                    region_id="region-B",
                    start_beat=4.0,
                    end_beat=8.0,
                    label="Region B",
                    note_changes=[
                        NoteChange(
                            note_id="nc-b1",
                            change_type="removed",
                            before=MidiNoteSnapshot(pitch=72, start_beat=4.0, duration_beats=2.0),
                            after=None,
                        ),
                    ],
                ),
            ],
        )

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=["p-r1", "p-r2"],
            project_state={},
            store=mock_store,
        )

        assert result.success is True
        assert result.notes_added == 1
        assert result.notes_removed == 1

        # add_notes called for region-A
        add_call = mock_store.add_notes.call_args
        assert add_call[0][0] == "region-A"

        # remove_notes called for region-B
        rm_call = mock_store.remove_notes.call_args
        assert rm_call[0][0] == "region-B"


class TestUpdatedRegions:
    """Test that updated_regions returns full note state after commit."""

    @pytest.mark.anyio
    async def test_updated_regions_returned_with_notes(self) -> None:

        """Commit should return updated_regions with full note data for affected regions."""
        from app.core.state_store import StateStore, clear_all_stores

        clear_all_stores()
        store = StateStore(conversation_id="test-updated-regions")

        # set up a track + region with pre-existing notes
        track_id = store.create_track("Bass", track_id="track-bass")
        region_id = store.create_region("Line", track_id, region_id="region-bass")
        store.add_notes(region_id, [
            {"pitch": 40, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 80, "channel": 0},
            {"pitch": 43, "start_beat": 1.0, "duration_beats": 1.0, "velocity": 80, "channel": 0},
        ])

        # Variation that adds one note
        variation = Variation(
            variation_id=str(uuid.uuid4()),
            intent="add a note",
            beat_range=(2.0, 3.0),
            phrases=[
                Phrase(
                    phrase_id="p-add",
                    track_id="track-bass",
                    region_id="region-bass",
                    start_beat=2.0,
                    end_beat=3.0,
                    label="Bar 1",
                    note_changes=[
                        NoteChange(
                            note_id="nc-1",
                            change_type="added",
                            after=MidiNoteSnapshot(pitch=45, start_beat=2.0, duration_beats=1.0, velocity=90),
                        ),
                    ],
                ),
            ],
        )

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=["p-add"],
            project_state={},
            store=store,
        )

        assert result.success is True
        assert len(result.updated_regions) == 1

        region_data = result.updated_regions[0]
        assert region_data["region_id"] == "region-bass"
        assert region_data["track_id"] == "track-bass"
        assert len(region_data["notes"]) == 3  # 2 original + 1 added
        pitches = {n["pitch"] for n in region_data["notes"]}
        assert pitches == {40, 43, 45}

        clear_all_stores()

    @pytest.mark.anyio
    async def test_updated_regions_after_removal(self) -> None:

        """Commit with removals should return updated_regions minus removed notes."""
        from app.core.state_store import StateStore, clear_all_stores

        clear_all_stores()
        store = StateStore(conversation_id="test-removal-regions")
        track_id = store.create_track("Keys", track_id="track-keys")
        region_id = store.create_region("Chords", track_id, region_id="region-keys")
        store.add_notes(region_id, [
            {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 100, "channel": 0},
            {"pitch": 64, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 100, "channel": 0},
            {"pitch": 67, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 100, "channel": 0},
        ])

        # Variation that removes one note
        variation = Variation(
            variation_id=str(uuid.uuid4()),
            intent="remove E",
            beat_range=(0.0, 1.0),
            phrases=[
                Phrase(
                    phrase_id="p-rm",
                    track_id="track-keys",
                    region_id="region-keys",
                    start_beat=0.0,
                    end_beat=1.0,
                    label="Beat 1",
                    note_changes=[
                        NoteChange(
                            note_id="nc-rm",
                            change_type="removed",
                            before=MidiNoteSnapshot(pitch=64, start_beat=0.0, duration_beats=1.0, velocity=100),
                        ),
                    ],
                ),
            ],
        )

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=["p-rm"],
            project_state={},
            store=store,
        )

        assert result.success is True
        assert len(result.updated_regions) == 1
        region_data = result.updated_regions[0]
        assert region_data["region_id"] == "region-keys"
        assert region_data["track_id"] == "track-keys"
        notes = region_data["notes"]
        assert len(notes) == 2
        pitches = {n["pitch"] for n in notes}
        assert pitches == {60, 67}
        # Notes are stored snake_case internally; the API layer converts to
        # camelCase via UpdatedRegionPayload before sending the JSON response.
        for note in notes:
            assert "start_beat" in note
            assert "duration_beats" in note

        clear_all_stores()


# =============================================================================
# NoteChangeEntryDict round-trip and AftertouchDict contract
# =============================================================================


class TestNoteChangeEntryDictRoundTrip:
    """Verify that _record_to_variation correctly reads back NoteChangeEntryDict data.

    Exercises the full path:
      Phrase domain model
        → build_phrase_payload (NoteChangeEntryDict construction)
        → PhraseRecord.diff_json storage
        → _record_to_variation (NoteChange reconstruction)
    """

    def _make_phrase_record(
        self,
        change_type: Literal["added", "removed", "modified"] = "added",
    ) -> object:
        """Build a PhraseRecord whose diff_json was produced by build_phrase_payload."""
        from app.models.variation import Phrase, NoteChange, MidiNoteSnapshot
        from app.contracts.json_types import CCEventDict, PitchBendDict, AftertouchDict
        from app.variation.core.event_envelope import build_phrase_payload
        from app.variation.storage.variation_store import PhraseRecord

        before = MidiNoteSnapshot(pitch=60, start_beat=0.0, duration_beats=1.0) if change_type != "added" else None
        after = MidiNoteSnapshot(pitch=62, start_beat=0.0, duration_beats=1.0) if change_type != "removed" else None
        phrase = Phrase(
            phrase_id="p-rt",
            track_id="t-1",
            region_id="r-1",
            start_beat=0.0,
            end_beat=4.0,
            label="test",
            note_changes=[NoteChange(note_id="nc-rt", change_type=change_type, before=before, after=after)],
            cc_events=[CCEventDict(cc=11, beat=0.5, value=80)],
            pitch_bends=[PitchBendDict(beat=1.0, value=100)],
            aftertouch=[AftertouchDict(beat=2.0, value=64)],
        )
        return PhraseRecord(
            phrase_id="p-rt",
            variation_id="v-1",
            sequence=1,
            track_id="t-1",
            region_id="r-1",
            beat_start=0.0,
            beat_end=4.0,
            label="test",
            diff_json=build_phrase_payload(phrase),
        )

    def _to_variation(self, pr: object) -> Variation:
        """Wrap a PhraseRecord in a VariationRecord and round-trip through _record_to_variation."""
        from app.variation.storage.variation_store import VariationRecord, PhraseRecord
        from app.api.routes.variation.commit import _record_to_variation

        assert isinstance(pr, PhraseRecord)
        record = VariationRecord(
            variation_id="v-1",
            project_id="proj-1",
            base_state_id="base",
            intent="test",
        )
        record.phrases.append(pr)
        return _record_to_variation(record)

    def test_added_note_round_trips(self) -> None:
        """added NoteChange serialises and deserialises without data loss."""
        pr = self._make_phrase_record("added")
        variation = self._to_variation(pr)
        nc = variation.phrases[0].note_changes[0]
        assert nc.note_id == "nc-rt"
        assert nc.change_type == "added"
        assert nc.before is None
        assert nc.after is not None
        assert nc.after.pitch == 62

    def test_removed_note_round_trips(self) -> None:
        """removed NoteChange serialises and deserialises without data loss."""
        pr = self._make_phrase_record("removed")
        variation = self._to_variation(pr)
        nc = variation.phrases[0].note_changes[0]
        assert nc.change_type == "removed"
        assert nc.before is not None
        assert nc.before.pitch == 60
        assert nc.after is None

    def test_modified_note_round_trips(self) -> None:
        """modified NoteChange carries both before and after through the round-trip."""
        pr = self._make_phrase_record("modified")
        variation = self._to_variation(pr)
        nc = variation.phrases[0].note_changes[0]
        assert nc.change_type == "modified"
        assert nc.before is not None
        assert nc.after is not None
        assert nc.before.pitch == 60
        assert nc.after.pitch == 62

    def test_cc_events_preserved(self) -> None:
        """CCEventDict entries survive the round-trip intact."""
        pr = self._make_phrase_record()
        variation = self._to_variation(pr)
        cc = variation.phrases[0].cc_events
        assert len(cc) == 1
        assert cc[0]["cc"] == 11
        assert cc[0]["value"] == 80

    def test_aftertouch_preserved(self) -> None:
        """AftertouchDict (with Required beat/value) survives the round-trip."""
        pr = self._make_phrase_record()
        variation = self._to_variation(pr)
        at = variation.phrases[0].aftertouch
        assert len(at) == 1
        assert at[0]["beat"] == 2.0
        assert at[0]["value"] == 64


class TestAftertouchDictContract:
    """AftertouchDict Required-field contracts."""

    def test_beat_and_value_are_required(self) -> None:
        """Both beat and value must be provided — pitch is optional."""
        from app.contracts.json_types import AftertouchDict
        at: AftertouchDict = {"beat": 1.5, "value": 64}
        assert at["beat"] == 1.5
        assert at["value"] == 64
        assert "pitch" not in at

    def test_polyphonic_aftertouch_includes_pitch(self) -> None:
        """Polyphonic aftertouch adds the optional pitch key."""
        from app.contracts.json_types import AftertouchDict
        at: AftertouchDict = {"beat": 0.5, "value": 80, "pitch": 60}
        assert at["pitch"] == 60

    def test_beat_type_is_float(self) -> None:
        """beat is Required[float] — integer literal is also float-compatible."""
        from app.contracts.json_types import AftertouchDict
        at: AftertouchDict = {"beat": 0, "value": 127}  # 0 coerces to float at runtime
        assert isinstance(at["beat"], (int, float))

    def test_value_range(self) -> None:
        """value is Required[int] in range 0-127."""
        from app.contracts.json_types import AftertouchDict
        for v in (0, 64, 127):
            at: AftertouchDict = {"beat": 0.0, "value": v}
            assert at["value"] == v
