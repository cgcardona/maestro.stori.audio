"""
Tests for app.services.variation and app.models.variation.

These cover every public class and function with zero prior test coverage:
  1.  NoteMatch properties (is_added, is_removed, is_modified, is_unchanged, _has_changes)
  2.  _notes_match (pitch tolerance, timing tolerance)
  3.  match_notes  (all-add, all-remove, matched pairs, mixed, empty inputs)
  4.  _beat_to_bar / _generate_bar_label helpers
  5.  _detect_change_tags (pitch, rhythm, velocity, articulation, harmony, scale, register, density)
  6.  VariationService.compute_variation (no changes, adds, removes, modifications, phrase grouping)
  7.  VariationService.compute_multi_region_variation
  8.  get_variation_service singleton
  9.  Variation model properties (total_changes, note_counts, is_empty, get_phrase, get_accepted_notes)
 10.  Phrase model properties (added_count, removed_count, modified_count, is_empty)
 11.  MidiNoteSnapshot.from_note_dict / to_note_dict
 12.  NoteChange model_post_init validation constraints
"""

import pytest

from app.services.variation import (
    TIMING_TOLERANCE_BEATS,
    NoteMatch,
    VariationService,
    _beat_to_bar,
    _detect_change_tags,
    _generate_bar_label,
    _notes_match,
    get_variation_service,
    match_notes,
)
from app.models.variation import (
    MidiNoteSnapshot,
    NoteChange,
    Phrase,
    Variation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _note(pitch: int = 60, start: float = 0.0, dur: float = 0.5, vel: int = 100) -> dict:
    return {"pitch": pitch, "start_beat": start, "duration_beats": dur, "velocity": vel}


def _snapshot(pitch: int = 60, start: float = 0.0, dur: float = 0.5, vel: int = 100) -> MidiNoteSnapshot:
    return MidiNoteSnapshot(pitch=pitch, start_beat=start, duration_beats=dur, velocity=vel)


def _service() -> VariationService:
    return VariationService(bars_per_phrase=4, beats_per_bar=4)


# ===========================================================================
# 1. NoteMatch properties
# ===========================================================================

class TestNoteMatchProperties:
    def test_added_when_base_is_none(self):
        m = NoteMatch(base_note=None, proposed_note=_note(), base_index=None, proposed_index=0)
        assert m.is_added
        assert not m.is_removed
        assert not m.is_modified
        assert not m.is_unchanged

    def test_removed_when_proposed_is_none(self):
        m = NoteMatch(base_note=_note(), proposed_note=None, base_index=0, proposed_index=None)
        assert m.is_removed
        assert not m.is_added
        assert not m.is_modified
        assert not m.is_unchanged

    def test_modified_when_pitch_differs(self):
        m = NoteMatch(
            base_note=_note(pitch=60),
            proposed_note=_note(pitch=62),
            base_index=0, proposed_index=0,
        )
        assert m.is_modified
        assert not m.is_unchanged

    def test_unchanged_when_identical(self):
        n = _note()
        m = NoteMatch(base_note=n, proposed_note=n, base_index=0, proposed_index=0)
        assert m.is_unchanged
        assert not m.is_modified

    def test_modified_when_velocity_differs(self):
        m = NoteMatch(
            base_note=_note(vel=100),
            proposed_note=_note(vel=80),
            base_index=0, proposed_index=0,
        )
        assert m.is_modified

    def test_modified_when_timing_beyond_tolerance(self):
        m = NoteMatch(
            base_note=_note(start=0.0),
            proposed_note=_note(start=TIMING_TOLERANCE_BEATS + 0.01),
            base_index=0, proposed_index=0,
        )
        assert m.is_modified

    def test_unchanged_within_timing_tolerance(self):
        m = NoteMatch(
            base_note=_note(start=0.0),
            proposed_note=_note(start=TIMING_TOLERANCE_BEATS / 2),
            base_index=0, proposed_index=0,
        )
        assert m.is_unchanged

    def test_is_modified_false_when_both_none(self):
        m = NoteMatch(base_note=None, proposed_note=None, base_index=None, proposed_index=None)
        assert not m.is_modified

    def test_is_unchanged_false_when_both_none(self):
        m = NoteMatch(base_note=None, proposed_note=None, base_index=None, proposed_index=None)
        assert not m.is_unchanged


# ===========================================================================
# 2. _notes_match
# ===========================================================================

class TestNotesMatch:
    def test_identical_notes_match(self):
        n = _note()
        assert _notes_match(n, n)

    def test_different_pitch_no_match(self):
        assert not _notes_match(_note(pitch=60), _note(pitch=61))

    def test_timing_within_tolerance_matches(self):
        n1 = _note(start=0.0)
        n2 = _note(start=TIMING_TOLERANCE_BEATS - 0.001)
        assert _notes_match(n1, n2)

    def test_timing_exceeds_tolerance_no_match(self):
        n1 = _note(start=0.0)
        n2 = _note(start=TIMING_TOLERANCE_BEATS + 0.001)
        assert not _notes_match(n1, n2)

    def test_missing_pitch_returns_false(self):
        assert not _notes_match({}, _note())

    def test_none_pitch_returns_false(self):
        assert not _notes_match({"pitch": None, "start_beat": 0}, _note())


# ===========================================================================
# 3. match_notes
# ===========================================================================

class TestMatchNotes:
    def test_empty_both(self):
        assert match_notes([], []) == []

    def test_all_added(self):
        proposed = [_note(60), _note(62), _note(64)]
        matches = match_notes([], proposed)
        assert all(m.is_added for m in matches)
        assert len(matches) == 3

    def test_all_removed(self):
        base = [_note(60), _note(62)]
        matches = match_notes(base, [])
        assert all(m.is_removed for m in matches)
        assert len(matches) == 2

    def test_identical_notes_are_unchanged(self):
        notes = [_note(60, 0.0), _note(62, 1.0)]
        matches = match_notes(notes, notes)
        assert all(m.is_unchanged for m in matches)

    def test_modified_note(self):
        base = [_note(60)]
        proposed = [_note(62)]  # different pitch → add + remove
        matches = match_notes(base, proposed)
        has_removed = any(m.is_removed for m in matches)
        has_added = any(m.is_added for m in matches)
        assert has_removed and has_added

    def test_mixed_unchanged_and_changed(self):
        base = [_note(60, 0.0), _note(64, 2.0)]
        proposed = [_note(60, 0.0), _note(67, 2.0)]  # first unchanged, second changed
        matches = match_notes(base, proposed)
        unchanged = [m for m in matches if m.is_unchanged]
        assert len(unchanged) >= 1

    def test_each_note_matched_at_most_once(self):
        """A note should not be matched to multiple proposed notes."""
        base = [_note(60)]
        proposed = [_note(60), _note(60)]  # two identical proposed
        matches = match_notes(base, proposed)
        # base has 1, proposed has 2 → one unchanged + one added
        added = [m for m in matches if m.is_added]
        unchanged = [m for m in matches if m.is_unchanged]
        assert len(added) == 1
        assert len(unchanged) == 1


# ===========================================================================
# 4. _beat_to_bar and _generate_bar_label
# ===========================================================================

class TestBeatToBar:
    def test_beat_0_is_bar_1(self):
        assert _beat_to_bar(0) == 1

    def test_beat_4_is_bar_2(self):
        assert _beat_to_bar(4) == 2

    def test_beat_8_is_bar_3(self):
        assert _beat_to_bar(8) == 3

    def test_fractional_beat(self):
        assert _beat_to_bar(0.5) == 1

    def test_custom_beats_per_bar(self):
        assert _beat_to_bar(3, beats_per_bar=3) == 2


class TestGenerateBarLabel:
    def test_single_bar(self):
        assert _generate_bar_label(1, 1) == "Bar 1"

    def test_range(self):
        assert _generate_bar_label(1, 4) == "Bars 1-4"

    def test_adjacent_bars(self):
        assert _generate_bar_label(3, 4) == "Bars 3-4"


# ===========================================================================
# 5. _detect_change_tags
# ===========================================================================

class TestDetectChangeTags:
    def _added_change(self, pitch: int = 60) -> NoteChange:
        return NoteChange(
            note_id="n1",
            change_type="added",
            after=_snapshot(pitch=pitch),
        )

    def _removed_change(self, pitch: int = 60) -> NoteChange:
        return NoteChange(
            note_id="n1",
            change_type="removed",
            before=_snapshot(pitch=pitch),
        )

    def _modified_change(
        self,
        before: MidiNoteSnapshot,
        after: MidiNoteSnapshot,
    ) -> NoteChange:
        return NoteChange(note_id="n1", change_type="modified", before=before, after=after)

    def test_added_note_density_tag(self):
        tags = _detect_change_tags([self._added_change()])
        assert "densityChange" in tags

    def test_removed_note_density_tag(self):
        tags = _detect_change_tags([self._removed_change()])
        assert "densityChange" in tags

    def test_pitch_change_tag(self):
        nc = self._modified_change(_snapshot(pitch=60), _snapshot(pitch=65))
        tags = _detect_change_tags([nc])
        assert "pitchChange" in tags

    def test_semitone_pitch_change_scale_tag(self):
        nc = self._modified_change(_snapshot(pitch=60), _snapshot(pitch=61))  # 1 semitone
        tags = _detect_change_tags([nc])
        assert "scaleChange" in tags

    def test_third_interval_harmony_tag(self):
        nc = self._modified_change(_snapshot(pitch=60), _snapshot(pitch=63))  # minor third
        tags = _detect_change_tags([nc])
        assert "harmonyChange" in tags

    def test_rhythm_change_tag(self):
        nc = self._modified_change(
            _snapshot(start=0.0),
            _snapshot(start=TIMING_TOLERANCE_BEATS + 0.1),
        )
        tags = _detect_change_tags([nc])
        assert "rhythmChange" in tags

    def test_velocity_change_tag(self):
        nc = self._modified_change(_snapshot(vel=100), _snapshot(vel=60))
        tags = _detect_change_tags([nc])
        assert "velocityChange" in tags

    def test_articulation_change_tag(self):
        nc = self._modified_change(
            _snapshot(dur=0.5),
            _snapshot(dur=1.5),
        )
        tags = _detect_change_tags([nc])
        assert "articulationChange" in tags

    def test_register_change_tag(self):
        nc = self._modified_change(_snapshot(pitch=60), _snapshot(pitch=73))  # 13 semitones
        tags = _detect_change_tags([nc])
        assert "registerChange" in tags

    def test_no_changes_returns_empty(self):
        assert _detect_change_tags([]) == []

    def test_tags_are_sorted(self):
        nc = self._modified_change(_snapshot(pitch=60, vel=100), _snapshot(pitch=61, vel=80))
        tags = _detect_change_tags([nc])
        assert tags == sorted(tags)


# ===========================================================================
# 6. VariationService.compute_variation
# ===========================================================================

class TestComputeVariation:
    def test_no_changes_returns_empty_variation(self):
        svc = _service()
        notes = [_note(60), _note(64)]
        variation = svc.compute_variation(
            base_notes=notes,
            proposed_notes=notes,
            region_id="r1", track_id="t1",
            intent="make brighter",
        )
        assert variation.phrases == []
        assert variation.total_changes == 0
        assert variation.is_empty

    def test_variation_id_is_assigned(self):
        svc = _service()
        variation = svc.compute_variation([], [_note()], "r1", "t1", "test")
        assert len(variation.variation_id) > 0

    def test_custom_variation_id_preserved(self):
        svc = _service()
        variation = svc.compute_variation([], [_note()], "r1", "t1", "test",
                                          variation_id="my-id-123")
        assert variation.variation_id == "my-id-123"

    def test_intent_stored(self):
        svc = _service()
        variation = svc.compute_variation([], [_note()], "r1", "t1", "make it louder")
        assert variation.intent == "make it louder"

    def test_explanation_stored(self):
        svc = _service()
        variation = svc.compute_variation(
            [], [_note()], "r1", "t1", "test",
            explanation="I added a note for brightness"
        )
        assert variation.ai_explanation == "I added a note for brightness"

    def test_added_notes_appear_in_phrases(self):
        svc = _service()
        variation = svc.compute_variation([], [_note(60), _note(64)], "r1", "t1", "add notes")
        assert variation.total_changes == 2
        assert any(p.added_count > 0 for p in variation.phrases)

    def test_removed_notes_appear_in_phrases(self):
        svc = _service()
        variation = svc.compute_variation([_note(60)], [], "r1", "t1", "remove note")
        assert variation.total_changes == 1
        assert any(p.removed_count > 0 for p in variation.phrases)

    def test_beat_range_computed(self):
        svc = _service()
        variation = svc.compute_variation(
            [],
            [_note(60, start=4.0, dur=2.0)],
            "r1", "t1", "test",
        )
        assert variation.beat_range[0] == 4.0
        assert variation.beat_range[1] == 6.0

    def test_affected_tracks_and_regions(self):
        svc = _service()
        variation = svc.compute_variation([], [_note()], "r-abc", "t-xyz", "test")
        assert "t-xyz" in variation.affected_tracks
        assert "r-abc" in variation.affected_regions

    def test_notes_grouped_into_phrases_by_bar(self):
        svc = _service()
        # Notes in two different 4-bar phrase windows
        notes_phrase_1 = [_note(60, start=0.0), _note(64, start=2.0)]   # beats 0-2 → phrase 0
        notes_phrase_2 = [_note(67, start=16.0), _note(69, start=18.0)] # beats 16-18 → phrase 1
        variation = svc.compute_variation(
            [],
            notes_phrase_1 + notes_phrase_2,
            "r1", "t1", "test",
        )
        assert len(variation.phrases) == 2

    def test_phrase_label_format(self):
        svc = _service()
        variation = svc.compute_variation([], [_note(start=0.0)], "r1", "t1", "test")
        label = variation.phrases[0].label
        assert "Bar" in label

    def test_empty_base_and_proposed_is_empty_variation(self):
        svc = _service()
        variation = svc.compute_variation([], [], "r1", "t1", "test")
        assert variation.is_empty

    def test_phrase_region_and_track_ids_set(self):
        svc = _service()
        variation = svc.compute_variation([], [_note()], "r-test", "t-test", "x")
        for phrase in variation.phrases:
            assert phrase.region_id == "r-test"
            assert phrase.track_id == "t-test"


# ===========================================================================
# 7. VariationService.compute_multi_region_variation
# ===========================================================================

class TestComputeMultiRegionVariation:
    def test_no_changes_anywhere(self):
        svc = _service()
        notes = {"r1": [_note(60)], "r2": [_note(64)]}
        variation = svc.compute_multi_region_variation(
            base_regions=notes,
            proposed_regions=notes,
            track_regions={"r1": "t1", "r2": "t1"},
            intent="test",
        )
        assert variation.is_empty
        assert variation.affected_regions == []

    def test_changes_in_one_region(self):
        svc = _service()
        variation = svc.compute_multi_region_variation(
            base_regions={"r1": [_note(60)], "r2": [_note(64)]},
            proposed_regions={"r1": [_note(60)], "r2": [_note(67)]},  # r2 changed
            track_regions={"r1": "t1", "r2": "t1"},
            intent="raise r2 note",
        )
        assert "r2" in variation.affected_regions
        assert "r1" not in variation.affected_regions

    def test_changes_in_both_regions(self):
        svc = _service()
        variation = svc.compute_multi_region_variation(
            base_regions={"r1": [_note(60)], "r2": [_note(64)]},
            proposed_regions={"r1": [_note(62)], "r2": [_note(67)]},
            track_regions={"r1": "t1", "r2": "t1"},
            intent="change both",
        )
        assert len(variation.affected_regions) == 2

    def test_per_region_track_id(self):
        """Each phrase must carry its own region's server-assigned trackId."""
        svc = _service()
        variation = svc.compute_multi_region_variation(
            base_regions={},
            proposed_regions={
                "r-drums": [_note(36)],
                "r-bass": [_note(40)],
            },
            track_regions={"r-drums": "t-drums", "r-bass": "t-bass"},
            intent="verse section",
        )
        phrase_track_ids = {p.track_id for p in variation.phrases}
        assert "t-drums" in phrase_track_ids
        assert "t-bass" in phrase_track_ids
        assert len(variation.affected_tracks) == 2

    def test_new_region_in_proposed(self):
        """A region only in proposed_regions (all notes added)."""
        svc = _service()
        variation = svc.compute_multi_region_variation(
            base_regions={},
            proposed_regions={"r-new": [_note(60), _note(62)]},
            track_regions={"r-new": "t1"},
            intent="add region",
        )
        assert "r-new" in variation.affected_regions
        assert variation.total_changes == 2

    def test_removed_region(self):
        """A region only in base_regions (all notes removed)."""
        svc = _service()
        variation = svc.compute_multi_region_variation(
            base_regions={"r-del": [_note(60)]},
            proposed_regions={},
            track_regions={"r-del": "t1"},
            intent="remove region",
        )
        assert "r-del" in variation.affected_regions

    def test_beat_range_spans_all_regions(self):
        svc = _service()
        variation = svc.compute_multi_region_variation(
            base_regions={},
            proposed_regions={
                "r1": [_note(start=0.0, dur=1.0)],
                "r2": [_note(start=8.0, dur=2.0)],
            },
            track_regions={"r1": "t1", "r2": "t1"},
            intent="test",
        )
        assert variation.beat_range[0] == 0.0
        assert variation.beat_range[1] == 10.0

    def test_intent_stored(self):
        svc = _service()
        variation = svc.compute_multi_region_variation(
            base_regions={}, proposed_regions={"r1": [_note()]},
            track_regions={"r1": "t1"}, intent="the intent"
        )
        assert variation.intent == "the intent"


# ===========================================================================
# 8. get_variation_service singleton
# ===========================================================================

class TestGetVariationService:
    def test_returns_variation_service(self):
        svc = get_variation_service()
        assert isinstance(svc, VariationService)

    def test_same_instance_returned(self):
        svc1 = get_variation_service()
        svc2 = get_variation_service()
        assert svc1 is svc2


# ===========================================================================
# 9. Variation model properties
# ===========================================================================

class TestVariationModel:
    def _phrase_with(self, n_added=0, n_removed=0, n_modified=0) -> Phrase:
        changes = []
        for i in range(n_added):
            changes.append(NoteChange(note_id=f"a{i}", change_type="added",
                                      after=_snapshot()))
        for i in range(n_removed):
            changes.append(NoteChange(note_id=f"r{i}", change_type="removed",
                                      before=_snapshot()))
        for i in range(n_modified):
            changes.append(NoteChange(note_id=f"m{i}", change_type="modified",
                                      before=_snapshot(pitch=60),
                                      after=_snapshot(pitch=62)))
        return Phrase(
            phrase_id=f"p-{n_added}-{n_removed}-{n_modified}",
            track_id="t1", region_id="r1",
            start_beat=0, end_beat=4, label="Bar 1",
            note_changes=changes,
        )

    def _variation(self, phrases=None) -> Variation:
        return Variation(
            variation_id="v1",
            intent="test",
            affected_tracks=["t1"],
            affected_regions=["r1"],
            beat_range=(0.0, 4.0),
            phrases=phrases or [],
        )

    def test_total_changes_empty(self):
        assert self._variation().total_changes == 0

    def test_total_changes_sums_phrases(self):
        v = self._variation([
            self._phrase_with(n_added=2),
            self._phrase_with(n_removed=3),
        ])
        assert v.total_changes == 5

    def test_note_counts(self):
        v = self._variation([
            self._phrase_with(n_added=1, n_removed=2, n_modified=3),
        ])
        counts = v.note_counts
        assert counts["added"] == 1
        assert counts["removed"] == 2
        assert counts["modified"] == 3

    def test_is_empty_true(self):
        assert self._variation().is_empty

    def test_is_empty_false_when_has_changes(self):
        v = self._variation([self._phrase_with(n_added=1)])
        assert not v.is_empty

    def test_get_phrase_found(self):
        p = self._phrase_with(n_added=1)
        v = self._variation([p])
        assert v.get_phrase(p.phrase_id) is p

    def test_get_phrase_not_found(self):
        v = self._variation()
        assert v.get_phrase("nonexistent") is None

    def test_get_accepted_notes_for_added(self):
        phrase = self._phrase_with(n_added=2)
        v = self._variation([phrase])
        notes = v.get_accepted_notes([phrase.phrase_id])
        assert len(notes) == 2
        assert all("pitch" in n for n in notes)

    def test_get_accepted_notes_skips_rejected_phrases(self):
        p1 = self._phrase_with(n_added=2)
        p2 = self._phrase_with(n_added=1)
        v = self._variation([p1, p2])
        notes = v.get_accepted_notes([p1.phrase_id])  # only accept p1
        assert len(notes) == 2

    def test_get_removed_note_ids_for_removed_changes(self):
        phrase = self._phrase_with(n_removed=2)
        v = self._variation([phrase])
        ids = v.get_removed_note_ids([phrase.phrase_id])
        assert len(ids) == 2

    def test_get_removed_note_ids_includes_modified(self):
        phrase = self._phrase_with(n_modified=1)
        v = self._variation([phrase])
        ids = v.get_removed_note_ids([phrase.phrase_id])
        assert len(ids) == 1  # modified counts as a "before" removal


# ===========================================================================
# 10. Phrase model properties
# ===========================================================================

class TestPhraseModel:
    def _phrase(self, n_added=0, n_removed=0, n_modified=0) -> Phrase:
        changes = []
        for i in range(n_added):
            changes.append(NoteChange(note_id=f"a{i}", change_type="added",
                                      after=_snapshot()))
        for i in range(n_removed):
            changes.append(NoteChange(note_id=f"r{i}", change_type="removed",
                                      before=_snapshot()))
        for i in range(n_modified):
            changes.append(NoteChange(note_id=f"m{i}", change_type="modified",
                                      before=_snapshot(pitch=60), after=_snapshot(pitch=62)))
        return Phrase(
            phrase_id="p1", track_id="t1", region_id="r1",
            start_beat=0, end_beat=4, label="Bar 1",
            note_changes=changes,
        )

    def test_added_count(self):
        assert self._phrase(n_added=3).added_count == 3

    def test_removed_count(self):
        assert self._phrase(n_removed=2).removed_count == 2

    def test_modified_count(self):
        assert self._phrase(n_modified=4).modified_count == 4

    def test_is_empty_true(self):
        assert self._phrase().is_empty

    def test_is_empty_false(self):
        assert not self._phrase(n_added=1).is_empty


# ===========================================================================
# 11. MidiNoteSnapshot.from_note_dict / to_note_dict
# ===========================================================================

class TestMidiNoteSnapshot:
    def test_from_note_dict_defaults(self):
        snap = MidiNoteSnapshot.from_note_dict({})
        assert snap.pitch == 60
        assert snap.start_beat == 0
        assert snap.duration_beats == 0.5
        assert snap.velocity == 100

    def test_from_note_dict_values(self):
        snap = MidiNoteSnapshot.from_note_dict({
            "pitch": 72, "start_beat": 4.0, "duration_beats": 1.0, "velocity": 80
        })
        assert snap.pitch == 72
        assert snap.start_beat == 4.0

    def test_to_note_dict_round_trip(self):
        snap = _snapshot(pitch=64, start=2.0, dur=1.5, vel=90)
        d = snap.to_note_dict()
        snap2 = MidiNoteSnapshot.from_note_dict(d)
        assert snap2.pitch == snap.pitch
        assert snap2.start_beat == snap.start_beat
        assert snap2.duration_beats == snap.duration_beats
        assert snap2.velocity == snap.velocity

    def test_to_note_dict_contains_expected_keys(self):
        d = _snapshot().to_note_dict()
        for key in ("pitch", "start_beat", "duration_beats", "velocity", "channel"):
            assert key in d


# ===========================================================================
# 12. NoteChange model_post_init validation
# ===========================================================================

class TestNoteChangeValidation:
    def test_added_with_before_raises(self):
        with pytest.raises(Exception):  # ValueError via Pydantic
            NoteChange(
                note_id="n",
                change_type="added",
                before=_snapshot(),  # must be None
                after=_snapshot(),
            )

    def test_removed_with_after_raises(self):
        with pytest.raises(Exception):
            NoteChange(
                note_id="n",
                change_type="removed",
                before=_snapshot(),
                after=_snapshot(),  # must be None
            )

    def test_modified_with_missing_before_raises(self):
        with pytest.raises(Exception):
            NoteChange(
                note_id="n",
                change_type="modified",
                before=None,
                after=_snapshot(),
            )

    def test_modified_with_missing_after_raises(self):
        with pytest.raises(Exception):
            NoteChange(
                note_id="n",
                change_type="modified",
                before=_snapshot(),
                after=None,
            )

    def test_valid_added(self):
        nc = NoteChange(note_id="n", change_type="added", after=_snapshot())
        assert nc.change_type == "added"

    def test_valid_removed(self):
        nc = NoteChange(note_id="n", change_type="removed", before=_snapshot())
        assert nc.change_type == "removed"

    def test_valid_modified(self):
        nc = NoteChange(note_id="n", change_type="modified",
                        before=_snapshot(pitch=60), after=_snapshot(pitch=62))
        assert nc.change_type == "modified"
