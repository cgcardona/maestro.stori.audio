"""Deep executor tests covering variation execution, apply_variation_phrases, and edge cases.

Supplements test_executor.py with:
- execute_plan_variation edge cases
- apply_variation_phrases with removals, modifications, partial accept
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.executor import (
    execute_plan_variation,
    apply_variation_phrases,
    ExecutionContext,
    VariationApplyResult,
)
from app.core.expansion import ToolCall
from app.core.state_store import StateStore
from app.core.tracing import TraceContext
from app.models.variation import (
    Variation,
    Phrase,
    NoteChange,
    MidiNoteSnapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_variation(
    phrases=None,
    variation_id="var-test-1",
    intent="test",
) -> Variation:
    if phrases is None:
        phrases = []
    return Variation(
        variation_id=variation_id,
        intent=intent,
        affected_tracks=["t1"],
        affected_regions=["r1"],
        beat_range=(0.0, 16.0),
        phrases=phrases,
    )


def _make_phrase(
    phrase_id="p1",
    note_changes=None,
    region_id="r1",
    track_id="t1",
) -> Phrase:
    return Phrase(
        phrase_id=phrase_id,
        track_id=track_id,
        region_id=region_id,
        start_beat=0.0,
        end_beat=4.0,
        label="Bar 1",
        note_changes=note_changes or [],
    )


def _note_add(pitch=60, start=0.0, dur=1.0, vel=100) -> NoteChange:
    return NoteChange(
        note_id=f"nc-add-{pitch}-{start}",
        change_type="added",
        after=MidiNoteSnapshot(pitch=pitch, start_beat=start, duration_beats=dur, velocity=vel),
    )


def _note_remove(pitch=60, start=0.0, dur=1.0, vel=100) -> NoteChange:
    return NoteChange(
        note_id=f"nc-rm-{pitch}-{start}",
        change_type="removed",
        before=MidiNoteSnapshot(pitch=pitch, start_beat=start, duration_beats=dur, velocity=vel),
    )


def _note_modify(old_pitch=60, new_pitch=63, start=0.0, dur=1.0) -> NoteChange:
    return NoteChange(
        note_id=f"nc-mod-{old_pitch}-{new_pitch}",
        change_type="modified",
        before=MidiNoteSnapshot(pitch=old_pitch, start_beat=start, duration_beats=dur, velocity=100),
        after=MidiNoteSnapshot(pitch=new_pitch, start_beat=start, duration_beats=dur, velocity=100),
    )


# ---------------------------------------------------------------------------
# execute_plan_variation
# ---------------------------------------------------------------------------


class TestExecutePlanVariation:

    @pytest.mark.anyio
    async def test_empty_tool_calls_returns_empty_variation(self):
        """No tool calls produces an empty variation."""
        variation = await execute_plan_variation(
            tool_calls=[],
            project_state={},
            intent="nothing",
        )
        assert variation.total_changes == 0
        assert variation.phrases == []
        assert variation.variation_id  # should still have an ID

    @pytest.mark.anyio
    async def test_add_notes_produces_variation(self):
        """stori_add_notes tool call produces a variation with phrases."""
        calls = [ToolCall("stori_add_notes", {
            "regionId": "r1",
            "trackId": "t1",
            "notes": [
                {"pitch": 60, "startBeat": 0.0, "durationBeats": 1.0, "velocity": 100},
                {"pitch": 62, "startBeat": 1.0, "durationBeats": 1.0, "velocity": 90},
            ],
        })]

        project_state = {
            "tracks": [{
                "id": "t1",
                "name": "Piano",
                "midiRegions": [{"id": "r1", "notes": []}],
            }]
        }

        variation = await execute_plan_variation(
            tool_calls=calls,
            project_state=project_state,
            intent="add notes",
        )

        assert variation.total_changes > 0
        assert len(variation.phrases) >= 1

    @pytest.mark.anyio
    async def test_no_mutation_of_canonical_state(self):
        """Variation execution must not modify the project_state dict."""
        project_state = {
            "tracks": [{
                "id": "t1",
                "name": "Piano",
                "midiRegions": [{"id": "r1", "notes": [{"pitch": 60, "startBeat": 0, "durationBeats": 1, "velocity": 100}]}],
            }]
        }
        import copy
        original = copy.deepcopy(project_state)

        calls = [ToolCall("stori_add_notes", {
            "regionId": "r1",
            "trackId": "t1",
            "notes": [{"pitch": 72, "startBeat": 2.0, "durationBeats": 1.0, "velocity": 100}],
        })]

        await execute_plan_variation(
            tool_calls=calls,
            project_state=project_state,
            intent="add note",
        )

        # Project state should be unchanged
        assert project_state == original


# ---------------------------------------------------------------------------
# apply_variation_phrases
# ---------------------------------------------------------------------------


class TestApplyVariationPhrases:

    @pytest.mark.anyio
    async def test_apply_added_notes(self):
        """Applying phrases with added notes creates notes in store."""
        phrase = _make_phrase(
            phrase_id="p1",
            note_changes=[_note_add(60, 0.0), _note_add(62, 1.0)],
        )
        variation = _make_variation(phrases=[phrase])

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=["p1"],
            project_state={},
        )

        assert result.success is True
        assert result.notes_added == 2
        assert result.notes_removed == 0
        assert result.applied_phrase_ids == ["p1"]

    @pytest.mark.anyio
    async def test_apply_removed_notes(self):
        """Applying phrases with removed notes triggers removals."""
        phrase = _make_phrase(
            phrase_id="p1",
            note_changes=[_note_remove(60, 0.0)],
        )
        variation = _make_variation(phrases=[phrase])

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=["p1"],
            project_state={},
        )

        assert result.success is True
        assert result.notes_removed == 1

    @pytest.mark.anyio
    async def test_apply_modified_notes(self):
        """Modified notes count as remove old + add new."""
        phrase = _make_phrase(
            phrase_id="p1",
            note_changes=[_note_modify(60, 63)],
        )
        variation = _make_variation(phrases=[phrase])

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=["p1"],
            project_state={},
        )

        assert result.success is True
        assert result.notes_modified == 1

    @pytest.mark.anyio
    async def test_partial_acceptance(self):
        """Only accepted phrase_ids are applied."""
        p1 = _make_phrase(phrase_id="p1", note_changes=[_note_add(60, 0.0)])
        p2 = _make_phrase(phrase_id="p2", note_changes=[_note_add(72, 4.0)])
        variation = _make_variation(phrases=[p1, p2])

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=["p1"],  # only accept p1
            project_state={},
        )

        assert result.success is True
        assert result.applied_phrase_ids == ["p1"]
        assert result.notes_added == 1  # only p1's note

    @pytest.mark.anyio
    async def test_unknown_phrase_id_skipped(self):
        """Unknown phrase IDs are silently skipped."""
        phrase = _make_phrase(phrase_id="p1", note_changes=[_note_add(60, 0.0)])
        variation = _make_variation(phrases=[phrase])

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=["p1", "p-nonexistent"],
            project_state={},
        )

        assert result.success is True
        assert result.applied_phrase_ids == ["p1"]

    @pytest.mark.anyio
    async def test_empty_accepted_ids(self):
        """Empty accepted list is a no-op."""
        phrase = _make_phrase(phrase_id="p1", note_changes=[_note_add(60)])
        variation = _make_variation(phrases=[phrase])

        result = await apply_variation_phrases(
            variation=variation,
            accepted_phrase_ids=[],
            project_state={},
        )

        assert result.success is True
        assert result.notes_added == 0


# ---------------------------------------------------------------------------
# ExecutionContext
# ---------------------------------------------------------------------------


class TestExecutionContextExtended:

    def test_all_successful_true(self):
        store = MagicMock(spec=StateStore)
        tx = MagicMock()
        trace = TraceContext(trace_id="test")
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_result("tool1", True, {})
        ctx.add_result("tool2", True, {})
        assert ctx.all_successful is True

    def test_all_successful_false(self):
        store = MagicMock(spec=StateStore)
        tx = MagicMock()
        trace = TraceContext(trace_id="test")
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_result("tool1", True, {})
        ctx.add_result("tool2", False, {}, error="failed")
        assert ctx.all_successful is False

    def test_failed_tools(self):
        store = MagicMock(spec=StateStore)
        tx = MagicMock()
        trace = TraceContext(trace_id="test")
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_result("tool1", True, {})
        ctx.add_result("tool2", False, {}, error="oops")
        assert ctx.failed_tools == ["tool2"]

    def test_created_entities(self):
        store = MagicMock(spec=StateStore)
        tx = MagicMock()
        trace = TraceContext(trace_id="test")
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_result("stori_add_midi_track", True, {}, entity_created="track-123")
        assert ctx.created_entities == {"stori_add_midi_track": "track-123"}

    def test_add_event(self):
        store = MagicMock(spec=StateStore)
        tx = MagicMock()
        trace = TraceContext(trace_id="test")
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_event({"type": "test", "data": 1})
        assert len(ctx.events) == 1
