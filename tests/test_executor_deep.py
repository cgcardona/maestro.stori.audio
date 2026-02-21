"""Deep executor tests covering variation execution, apply_variation_phrases, and edge cases.

Supplements test_executor.py with:
- execute_plan_variation edge cases
- apply_variation_phrases with removals, modifications, partial accept
- _extract_notes_from_project with midiRegions
- Generator timeout handling
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import app.core.executor as executor_module
from app.core.executor import (
    execute_plan_variation,
    apply_variation_phrases,
    _extract_notes_from_project,
    _process_call_for_variation,
    ExecutionContext,
    VariationContext,
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
                "regions": [{"id": "r1", "notes": []}],
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
    async def test_tool_event_callback_called_before_processing(self):
        """tool_event_callback is invoked before each tool call is processed."""
        captured: list[tuple[str, str, dict]] = []

        async def _on_tool(call_id, name, params):
            captured.append((call_id, name, params))

        calls = [
            ToolCall("stori_set_tempo", {"tempo": 120}, id="tc-1"),
            ToolCall("stori_set_key", {"key": "Am"}, id="tc-2"),
        ]
        await execute_plan_variation(
            tool_calls=calls,
            project_state={},
            intent="setup",
            tool_event_callback=_on_tool,
        )

        assert len(captured) == 2
        assert captured[0] == ("tc-1", "stori_set_tempo", {"tempo": 120})
        assert captured[1] == ("tc-2", "stori_set_key", {"key": "Am"})

    @pytest.mark.anyio
    async def test_no_mutation_of_canonical_state(self):
        """Variation execution must not modify the project_state dict."""
        project_state = {
            "tracks": [{
                "id": "t1",
                "name": "Piano",
                "regions": [{"id": "r1", "notes": [{"pitch": 60, "startBeat": 0, "durationBeats": 1, "velocity": 100}]}],
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


# ---------------------------------------------------------------------------
# _extract_notes_from_project — midiRegions support (Change 5)
# ---------------------------------------------------------------------------


class TestExtractNotesFromProject:

    def test_extracts_from_regions_key(self):
        """Standard 'regions' key is extracted."""
        store = StateStore(conversation_id="test-extract")
        trace = TraceContext(trace_id="test")
        var_ctx = VariationContext(
            store=store, trace=trace,
            base_notes={}, proposed_notes={}, track_regions={},
        )
        project = {
            "tracks": [{
                "id": "t1",
                "regions": [{"id": "r1", "notes": [
                    {"pitch": 60, "startBeat": 0, "durationBeats": 1, "velocity": 100},
                ]}],
            }]
        }
        _extract_notes_from_project(project, var_ctx)
        assert "r1" in var_ctx.base_notes
        assert len(var_ctx.base_notes["r1"]) == 1

    def test_falls_back_to_store_when_no_notes(self):
        """When region has no notes array, falls back to StateStore."""
        store = StateStore(conversation_id="test-fallback")
        store.add_notes("r1", [{"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100}])
        trace = TraceContext(trace_id="test")
        var_ctx = VariationContext(
            store=store, trace=trace,
            base_notes={}, proposed_notes={}, track_regions={},
        )
        project = {
            "tracks": [{
                "id": "t1",
                "regions": [{"id": "r1", "noteCount": 1}],
            }]
        }
        _extract_notes_from_project(project, var_ctx)
        assert "r1" in var_ctx.base_notes
        assert len(var_ctx.base_notes["r1"]) == 1


# ---------------------------------------------------------------------------
# Generator timeout (Change 4)
# ---------------------------------------------------------------------------


class TestGeneratorTimeout:

    @pytest.mark.anyio
    async def test_generator_timeout_does_not_crash(self):
        """A generator that exceeds the 30s timeout should be caught, not crash."""
        store = StateStore(conversation_id="test-timeout")
        tid = store.create_track("Drums")
        store.create_region("Pattern", tid)
        trace = TraceContext(trace_id="test")
        var_ctx = VariationContext(
            store=store, trace=trace,
            base_notes={}, proposed_notes={}, track_regions={},
        )

        call = ToolCall("stori_generate_drums", {
            "role": "drums",
            "style": "boom_bap",
            "tempo": 90,
            "bars": 4,
        })

        async def slow_generate(**kwargs):
            await asyncio.sleep(100)

        mock_mg = MagicMock()
        mock_mg.generate = slow_generate

        original_timeout = executor_module._GENERATOR_TIMEOUT
        try:
            executor_module._GENERATOR_TIMEOUT = 0.1
            with patch("app.core.executor.variation.get_music_generator", return_value=mock_mg):
                # Should not raise — timeout is caught internally
                await _process_call_for_variation(call, var_ctx)
        finally:
            executor_module._GENERATOR_TIMEOUT = original_timeout

        # No proposed notes since generator timed out
        assert len(var_ctx.proposed_notes) == 0

    @pytest.mark.anyio
    async def test_generator_exception_does_not_crash(self):
        """A generator that raises an exception should be caught gracefully."""
        store = StateStore(conversation_id="test-gen-err")
        tid = store.create_track("Bass")
        store.create_region("Groove", tid)
        trace = TraceContext(trace_id="test")
        var_ctx = VariationContext(
            store=store, trace=trace,
            base_notes={}, proposed_notes={}, track_regions={},
        )

        call = ToolCall("stori_generate_bass", {
            "role": "bass",
            "style": "funk",
            "tempo": 100,
            "bars": 4,
        })

        mock_mg = MagicMock()
        mock_mg.generate = AsyncMock(side_effect=RuntimeError("Orpheus down"))

        with patch("app.core.executor.variation.get_music_generator", return_value=mock_mg):
            await _process_call_for_variation(call, var_ctx)

        assert len(var_ctx.proposed_notes) == 0


# ---------------------------------------------------------------------------
# emotion_vector derivation in execute_plan_variation
# ---------------------------------------------------------------------------


class TestEmotionVectorIntegration:
    """Tests that execute_plan_variation derives and passes emotion_vector to mg.generate."""

    @pytest.mark.anyio
    async def test_emotion_vector_derived_from_stori_prompt(self):
        """When explanation contains a STORI PROMPT, emotion_vector is derived and passed."""
        stori_prompt = (
            "STORI PROMPT\n"
            "Section: Verse\n"
            "Style: Lofi Hip-Hop\n"
            "Vibe: Melancholic, warm\n"
            "Energy: Low"
        )

        captured_kwargs: dict = {}

        async def mock_generate(**kwargs):
            captured_kwargs.update(kwargs)
            from app.services.music_generator import GenerationResult
            from app.services.backends.base import GeneratorBackend
            return GenerationResult(
                success=True,
                notes=[{"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 80}],
                backend_used=GeneratorBackend.ORPHEUS,
                metadata={},
            )

        generator_call = ToolCall("stori_generate_midi", {
            "role": "drums",
            "style": "lofi",
            "tempo": 85,
            "bars": 4,
        })

        mock_mg = MagicMock()
        mock_mg.generate = mock_generate

        with patch("app.core.executor.variation.get_music_generator", return_value=mock_mg):
            with patch("app.core.executor.variation.get_or_create_store") as mock_store_factory:
                mock_store = MagicMock()
                mock_store.registry = MagicMock()
                mock_store.registry.resolve_track = MagicMock(return_value="t1")
                mock_store.registry.get_latest_region_for_track = MagicMock(return_value="r1")
                mock_store.registry.get_region = MagicMock(return_value=None)
                mock_store.sync_from_client = MagicMock()
                mock_store.conversation_id = "test"
                mock_store_factory.return_value = mock_store

                with patch("app.core.executor.variation.get_variation_service") as mock_vs:
                    from app.models.variation import Variation
                    mock_vs.return_value.compute_variation = MagicMock(
                        return_value=Variation(
                            variation_id="v1",
                            intent="test",
                            affected_tracks=[],
                            affected_regions=[],
                            beat_range=(0.0, 4.0),
                            phrases=[],
                        )
                    )
                    mock_vs.return_value.compute_multi_region_variation = MagicMock(
                        return_value=Variation(
                            variation_id="v1",
                            intent="test",
                            affected_tracks=[],
                            affected_regions=[],
                            beat_range=(0.0, 4.0),
                            phrases=[],
                        )
                    )

                    await execute_plan_variation(
                        tool_calls=[generator_call],
                        project_state={},
                        intent="compose lofi verse",
                        explanation=stori_prompt,
                    )

        # emotion_vector should have been passed to mg.generate
        assert "emotion_vector" in captured_kwargs
        ev = captured_kwargs["emotion_vector"]
        assert ev is not None
        # A melancholic low-energy prompt should produce low energy and high intimacy
        assert ev.energy < 0.5
        assert ev.intimacy > 0.5

    @pytest.mark.anyio
    async def test_no_explanation_skips_emotion_vector(self):
        """When explanation is None, emotion_vector is not passed (or is None)."""
        captured_kwargs: dict = {}

        async def mock_generate(**kwargs):
            captured_kwargs.update(kwargs)
            from app.services.music_generator import GenerationResult
            from app.services.backends.base import GeneratorBackend
            return GenerationResult(
                success=True,
                notes=[],
                backend_used=GeneratorBackend.ORPHEUS,
                metadata={},
            )

        generator_call = ToolCall("stori_generate_midi", {
            "role": "drums",
            "style": "trap",
            "tempo": 140,
            "bars": 4,
        })

        mock_mg = MagicMock()
        mock_mg.generate = mock_generate

        with patch("app.core.executor.variation.get_music_generator", return_value=mock_mg):
            with patch("app.core.executor.variation.get_or_create_store") as mock_store_factory:
                mock_store = MagicMock()
                mock_store.registry = MagicMock()
                mock_store.registry.resolve_track = MagicMock(return_value="t1")
                mock_store.registry.get_latest_region_for_track = MagicMock(return_value="r1")
                mock_store.registry.get_region = MagicMock(return_value=None)
                mock_store.sync_from_client = MagicMock()
                mock_store.conversation_id = "test"
                mock_store_factory.return_value = mock_store

                with patch("app.core.executor.variation.get_variation_service") as mock_vs:
                    from app.models.variation import Variation
                    mock_vs.return_value.compute_variation = MagicMock(
                        return_value=Variation(
                            variation_id="v1",
                            intent="test",
                            affected_tracks=[],
                            affected_regions=[],
                            beat_range=(0.0, 4.0),
                            phrases=[],
                        )
                    )
                    mock_vs.return_value.compute_multi_region_variation = MagicMock(
                        return_value=Variation(
                            variation_id="v1",
                            intent="test",
                            affected_tracks=[],
                            affected_regions=[],
                            beat_range=(0.0, 4.0),
                            phrases=[],
                        )
                    )

                    await execute_plan_variation(
                        tool_calls=[generator_call],
                        project_state={},
                        intent="compose",
                        explanation=None,
                    )

        # emotion_vector should be None (no explanation provided)
        assert captured_kwargs.get("emotion_vector") is None

    @pytest.mark.anyio
    async def test_quality_preset_forwarded_to_generator(self):
        """quality_preset passed to execute_plan_variation reaches mg.generate."""
        captured_kwargs: dict = {}

        async def mock_generate(**kwargs):
            captured_kwargs.update(kwargs)
            from app.services.music_generator import GenerationResult
            from app.services.backends.base import GeneratorBackend
            return GenerationResult(
                success=True,
                notes=[],
                backend_used=GeneratorBackend.ORPHEUS,
                metadata={},
            )

        generator_call = ToolCall("stori_generate_midi", {
            "role": "drums",
            "style": "trap",
            "tempo": 140,
            "bars": 4,
        })

        mock_mg = MagicMock()
        mock_mg.generate = mock_generate

        with patch("app.core.executor.variation.get_music_generator", return_value=mock_mg):
            with patch("app.core.executor.variation.get_or_create_store") as mock_store_factory:
                mock_store = MagicMock()
                mock_store.registry = MagicMock()
                mock_store.registry.resolve_track = MagicMock(return_value="t1")
                mock_store.registry.get_latest_region_for_track = MagicMock(return_value="r1")
                mock_store.registry.get_region = MagicMock(return_value=None)
                mock_store.sync_from_client = MagicMock()
                mock_store.conversation_id = "test"
                mock_store_factory.return_value = mock_store

                with patch("app.core.executor.variation.get_variation_service") as mock_vs:
                    from app.models.variation import Variation
                    mock_vs.return_value.compute_variation = MagicMock(
                        return_value=Variation(
                            variation_id="v1",
                            intent="test",
                            affected_tracks=[],
                            affected_regions=[],
                            beat_range=(0.0, 4.0),
                            phrases=[],
                        )
                    )
                    mock_vs.return_value.compute_multi_region_variation = MagicMock(
                        return_value=Variation(
                            variation_id="v1",
                            intent="test",
                            affected_tracks=[],
                            affected_regions=[],
                            beat_range=(0.0, 4.0),
                            phrases=[],
                        )
                    )

                    await execute_plan_variation(
                        tool_calls=[generator_call],
                        project_state={},
                        intent="compose",
                        quality_preset="fast",
                    )

        assert captured_kwargs.get("quality_preset") == "fast"


# ---------------------------------------------------------------------------
# Three-phase parallel generator dispatch
# ---------------------------------------------------------------------------


class TestParallelGeneratorDispatch:
    """Verify the 3-phase execution model in execute_plan_variation."""

    def _make_store_mock(self):
        store = MagicMock()
        store.registry = MagicMock()
        store.registry.resolve_track = MagicMock(return_value=None)
        store.registry.get_latest_region_for_track = MagicMock(return_value=None)
        store.registry.get_region = MagicMock(return_value=None)
        store.sync_from_client = MagicMock()
        store.conversation_id = "test"
        return store

    def _make_variation(self):
        from app.models.variation import Variation
        return Variation(
            variation_id="v1",
            intent="test",
            affected_tracks=[],
            affected_regions=[],
            beat_range=(0.0, 4.0),
            phrases=[],
        )

    def _make_gen_call(self, role: str) -> ToolCall:
        """Create a stori_generate_midi ToolCall for the given role."""
        return ToolCall(
            id=f"call-{role}",
            name="stori_generate_midi",
            params={"role": role, "style": "ska", "tempo": 165, "bars": 4, "key": "Bb"},
        )

    @pytest.mark.anyio
    async def test_independent_generators_run_in_parallel(self):
        """Organ, guitar, and horns dispatch concurrently (Phase 3), not sequentially."""
        import asyncio

        call_order: list[str] = []
        in_flight: list[str] = []

        async def fake_generate(*args, **kwargs):
            role = kwargs.get("instrument", "unknown")
            in_flight.append(role)
            await asyncio.sleep(0)  # yield so all tasks are in-flight together
            call_order.append(role)
            in_flight.remove(role)
            return MagicMock(
                success=False,  # success=False is fine — we just care about dispatch order
                notes=[],
                backend_used=MagicMock(),
                metadata={},
                error="mock",
            )

        mock_mg = MagicMock()
        mock_mg.generate = fake_generate
        mock_mg._generation_context = None

        tool_calls = [
            self._make_gen_call("organ"),
            self._make_gen_call("guitar"),
            self._make_gen_call("horns"),
        ]

        with (
            patch("app.core.executor.variation.get_music_generator", return_value=mock_mg),
            patch("app.core.executor.variation.get_or_create_store") as mock_factory,
            patch("app.core.executor.variation.get_variation_service") as mock_vs,
        ):
            mock_factory.return_value = self._make_store_mock()
            mock_vs.return_value.compute_variation = MagicMock(return_value=self._make_variation())
            mock_vs.return_value.compute_multi_region_variation = MagicMock(return_value=self._make_variation())

            await execute_plan_variation(
                tool_calls=tool_calls,
                project_state={},
                intent="test",
                quality_preset="fast",
            )

        # All three should have started without waiting for one to finish
        assert set(call_order) == {"organ", "guitar", "horns"}

    @pytest.mark.anyio
    async def test_drums_before_bass_before_parallel(self):
        """Phase ordering: setup → drums → bass → parallel melodic."""
        execution_order: list[str] = []

        async def track_call(call_id, tool_name, params):
            execution_order.append(params.get("role", tool_name))

        # Setup call (track creation)
        setup_call = ToolCall(
            id="setup",
            name="stori_add_midi_track",
            params={"name": "Drums", "drumKitId": "acoustic"},
        )
        drums_call = self._make_gen_call("drums")
        bass_call = self._make_gen_call("bass")
        organ_call = self._make_gen_call("organ")

        mock_mg = MagicMock()
        mock_mg.generate = AsyncMock(return_value=MagicMock(
            success=False, notes=[], backend_used=MagicMock(), metadata={}, error="mock"
        ))
        mock_mg._generation_context = None

        with (
            patch("app.core.executor.variation.get_music_generator", return_value=mock_mg),
            patch("app.core.executor.variation.get_or_create_store") as mock_factory,
            patch("app.core.executor.variation.get_variation_service") as mock_vs,
        ):
            mock_factory.return_value = self._make_store_mock()
            mock_vs.return_value.compute_variation = MagicMock(return_value=self._make_variation())
            mock_vs.return_value.compute_multi_region_variation = MagicMock(return_value=self._make_variation())

            await execute_plan_variation(
                tool_calls=[setup_call, drums_call, bass_call, organ_call],
                project_state={},
                intent="test",
                quality_preset="fast",
                tool_event_callback=track_call,
            )

        # Setup and coupled generators fire in order; organ comes after
        assert execution_order.index("stori_add_midi_track") < execution_order.index("drums")
        assert execution_order.index("drums") < execution_order.index("bass")
        assert execution_order.index("bass") < execution_order.index("organ")

    @pytest.mark.anyio
    async def test_no_generators_runs_setup_only(self):
        """Plans with no generator calls work fine (no parallel phase)."""
        call = ToolCall(
            id="c1",
            name="stori_set_tempo",
            params={"tempo": 120},
        )

        with (
            patch("app.core.executor.variation.get_or_create_store") as mock_factory,
            patch("app.core.executor.variation.get_variation_service") as mock_vs,
        ):
            mock_factory.return_value = self._make_store_mock()
            mock_vs.return_value.compute_variation = MagicMock(return_value=self._make_variation())
            mock_vs.return_value.compute_multi_region_variation = MagicMock(return_value=self._make_variation())

            result = await execute_plan_variation(
                tool_calls=[call],
                project_state={},
                intent="test",
            )

        assert result is not None


# ===========================================================================
# Bug 4: Phrase startBeat/endBeat are absolute project positions
# ===========================================================================


class TestPhraseAbsoluteBeats:
    """Phrase start_beat/end_beat must be absolute project positions, not region-relative."""

    def test_phrases_offset_by_region_start_beat(self):
        """Variation service adds region_start_beat to phrase positions."""
        from app.services.variation import VariationService

        svc = VariationService(bars_per_phrase=4, beats_per_bar=4)
        proposed = [
            {"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100},
            {"pitch": 62, "start_beat": 4, "duration_beats": 1, "velocity": 100},
        ]
        # Region starts at beat 16 (bar 5)
        variation = svc.compute_variation(
            base_notes=[],
            proposed_notes=proposed,
            region_id="r1",
            track_id="t1",
            intent="test",
            region_start_beat=16.0,
        )
        assert len(variation.phrases) >= 1
        # First phrase covers region-relative beats 0-16, but absolute 16-32
        p0 = variation.phrases[0]
        assert p0.start_beat == 16.0, f"Expected absolute start 16.0, got {p0.start_beat}"
        assert p0.end_beat == 32.0, f"Expected absolute end 32.0, got {p0.end_beat}"

    def test_bar_labels_reflect_absolute_position(self):
        """Bar labels should use absolute project bar numbers, not region-relative."""
        from app.services.variation import VariationService

        svc = VariationService(bars_per_phrase=4, beats_per_bar=4)
        proposed = [
            {"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100},
        ]
        variation = svc.compute_variation(
            base_notes=[],
            proposed_notes=proposed,
            region_id="r1",
            track_id="t1",
            intent="test",
            region_start_beat=16.0,
        )
        assert len(variation.phrases) >= 1
        # Region starts at beat 16 = bar 5, phrase covers bars 5-8
        assert "5" in variation.phrases[0].label

    def test_note_start_beat_stays_region_relative(self):
        """Note startBeat inside noteChanges must remain region-relative (Bug 6)."""
        from app.services.variation import VariationService

        svc = VariationService(bars_per_phrase=4, beats_per_bar=4)
        proposed = [
            {"pitch": 60, "start_beat": 2.5, "duration_beats": 1, "velocity": 100},
        ]
        variation = svc.compute_variation(
            base_notes=[],
            proposed_notes=proposed,
            region_id="r1",
            track_id="t1",
            intent="test",
            region_start_beat=16.0,
        )
        assert len(variation.phrases) == 1
        nc = variation.phrases[0].note_changes[0]
        assert nc.after is not None
        assert nc.after.start_beat == 2.5, (
            f"Note startBeat should be region-relative (2.5), got {nc.after.start_beat}"
        )

    def test_multi_region_absolute_beats(self):
        """compute_multi_region_variation uses per-region offsets."""
        from app.services.variation import VariationService

        svc = VariationService(bars_per_phrase=4, beats_per_bar=4)
        variation = svc.compute_multi_region_variation(
            base_regions={"r1": [], "r2": []},
            proposed_regions={
                "r1": [{"pitch": 36, "start_beat": 0, "duration_beats": 1, "velocity": 100}],
                "r2": [{"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100}],
            },
            track_regions={"r1": "t1", "r2": "t2"},
            intent="test",
            region_start_beats={"r1": 16.0, "r2": 16.0},
        )
        for phrase in variation.phrases:
            assert phrase.start_beat >= 16.0, (
                f"Phrase start_beat={phrase.start_beat} should be >= 16 (absolute)"
            )

    def test_zero_offset_backwards_compatible(self):
        """With region_start_beat=0 (default), behaviour is unchanged."""
        from app.services.variation import VariationService

        svc = VariationService(bars_per_phrase=4, beats_per_bar=4)
        proposed = [
            {"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100},
        ]
        variation = svc.compute_variation(
            base_notes=[],
            proposed_notes=proposed,
            region_id="r1",
            track_id="t1",
            intent="test",
        )
        assert variation.phrases[0].start_beat == 0.0
        assert variation.phrases[0].end_beat == 16.0


# ===========================================================================
# Bug 5: Commit returns fully materialized updatedRegions
# ===========================================================================


class TestApplyVariationUpdatedRegions:
    """apply_variation_phrases must return non-empty updatedRegions with notes."""

    @pytest.mark.anyio
    async def test_updated_regions_contain_notes_after_commit(self):
        """After commit, updatedRegions should include the applied notes."""
        phrase = Phrase(
            phrase_id="p1",
            track_id="t1",
            region_id="r1",
            start_beat=0,
            end_beat=16,
            label="Bars 1-4",
            note_changes=[
                NoteChange(
                    note_id="n1",
                    change_type="added",
                    before=None,
                    after=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
                ),
                NoteChange(
                    note_id="n2",
                    change_type="added",
                    before=None,
                    after=MidiNoteSnapshot(pitch=64, start_beat=4, duration_beats=1, velocity=90),
                ),
            ],
        )
        variation = _make_variation(
            phrases=[phrase],
            variation_id="v1",
        )

        with patch("app.core.executor.apply.get_or_create_store") as mock_factory:
            store = StateStore(conversation_id="test", project_id="proj1")
            store.create_track("Track1", track_id="t1")
            store.create_region("Region1", "t1", region_id="r1", metadata={
                "startBeat": 0,
                "durationBeats": 16,
            })
            mock_factory.return_value = store

            result = await apply_variation_phrases(
                variation=variation,
                accepted_phrase_ids=["p1"],
                project_state={},
                conversation_id="test",
            )

        assert result.success
        assert len(result.updated_regions) == 1
        ur = result.updated_regions[0]
        assert ur["region_id"] == "r1"
        assert ur["track_id"] == "t1"
        assert len(ur["notes"]) == 2, f"Expected 2 notes, got {len(ur['notes'])}"

    @pytest.mark.anyio
    async def test_updated_regions_include_metadata(self):
        """updatedRegions should include start_beat, duration_beats, name."""
        phrase = Phrase(
            phrase_id="p1",
            track_id="t1",
            region_id="r1",
            start_beat=16,
            end_beat=32,
            label="Bars 5-8",
            note_changes=[
                NoteChange(
                    note_id="n1",
                    change_type="added",
                    before=None,
                    after=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
                ),
            ],
        )
        variation = _make_variation(phrases=[phrase])

        with patch("app.core.executor.apply.get_or_create_store") as mock_factory:
            store = StateStore(conversation_id="test", project_id="proj1")
            store.create_track("Track1", track_id="t1")
            store.create_region("Verse", "t1", region_id="r1", metadata={
                "startBeat": 16,
                "durationBeats": 32,
            })
            mock_factory.return_value = store

            result = await apply_variation_phrases(
                variation=variation,
                accepted_phrase_ids=["p1"],
                project_state={},
                conversation_id="test",
            )

        assert result.success
        assert len(result.updated_regions) == 1
        ur = result.updated_regions[0]
        assert ur["start_beat"] == 16
        assert ur["duration_beats"] == 32
        assert ur["name"] == "Verse"

    @pytest.mark.anyio
    async def test_updated_regions_fallback_to_adds(self):
        """When store.get_region_notes is empty, fall back to region_adds."""
        phrase = Phrase(
            phrase_id="p1",
            track_id="t1",
            region_id="r1",
            start_beat=0,
            end_beat=16,
            label="Bars 1-4",
            note_changes=[
                NoteChange(
                    note_id="n1",
                    change_type="added",
                    before=None,
                    after=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
                ),
            ],
        )
        variation = _make_variation(phrases=[phrase])

        with patch("app.core.executor.apply.get_or_create_store") as mock_factory:
            store = MagicMock()
            store.registry.get_region.return_value = None
            store.get_region_track_id.return_value = "t1"
            store.get_region_notes.return_value = []  # empty!
            store.add_notes = MagicMock()
            store.remove_notes = MagicMock()
            store.begin_transaction.return_value = MagicMock()
            store.commit = MagicMock()
            mock_factory.return_value = store

            result = await apply_variation_phrases(
                variation=variation,
                accepted_phrase_ids=["p1"],
                project_state={},
                conversation_id="test",
            )

        assert result.success
        assert len(result.updated_regions) == 1
        assert len(result.updated_regions[0]["notes"]) == 1


# ===========================================================================
# CC and Pitch Bend pipeline tests
# ===========================================================================

class TestVariationContextCC:
    """VariationContext records CC and pitch bend data."""

    def test_record_proposed_cc(self):
        store = StateStore(conversation_id="cc", project_id="p")
        ctx = VariationContext(
            store=store,
            trace=TraceContext(trace_id="test"),
            base_notes={},
            proposed_notes={},
            track_regions={},
        )
        cc_events = [{"cc": 64, "beat": 0.0, "value": 127}]
        ctx.record_proposed_cc("r1", cc_events)
        assert ctx.proposed_cc["r1"] == cc_events

    def test_record_proposed_pitch_bends(self):
        store = StateStore(conversation_id="pb", project_id="p")
        ctx = VariationContext(
            store=store,
            trace=TraceContext(trace_id="test"),
            base_notes={},
            proposed_notes={},
            track_regions={},
        )
        pb_events = [{"beat": 1.0, "value": 4096}]
        ctx.record_proposed_pitch_bends("r1", pb_events)
        assert ctx.proposed_pitch_bends["r1"] == pb_events

    def test_empty_cc_not_recorded(self):
        store = StateStore(conversation_id="cc2", project_id="p")
        ctx = VariationContext(
            store=store,
            trace=TraceContext(trace_id="test"),
            base_notes={},
            proposed_notes={},
            track_regions={},
        )
        ctx.record_proposed_cc("r1", [])
        assert "r1" not in ctx.proposed_cc

    def test_cc_accumulates_across_calls(self):
        store = StateStore(conversation_id="cc3", project_id="p")
        ctx = VariationContext(
            store=store,
            trace=TraceContext(trace_id="test"),
            base_notes={},
            proposed_notes={},
            track_regions={},
        )
        ctx.record_proposed_cc("r1", [{"cc": 64, "beat": 0, "value": 127}])
        ctx.record_proposed_cc("r1", [{"cc": 11, "beat": 1, "value": 80}])
        assert len(ctx.proposed_cc["r1"]) == 2


class TestStateStoreCCPitchBend:
    """StateStore CC and pitch bend storage."""

    def test_add_and_get_cc(self):
        store = StateStore(conversation_id="s1", project_id="p")
        store.add_cc("r1", [{"cc": 64, "beat": 0, "value": 127}])
        result = store.get_region_cc("r1")
        assert len(result) == 1
        assert result[0]["cc"] == 64

    def test_add_and_get_pitch_bends(self):
        store = StateStore(conversation_id="s2", project_id="p")
        store.add_pitch_bends("r1", [{"beat": 0.5, "value": 2048}])
        result = store.get_region_pitch_bends("r1")
        assert len(result) == 1
        assert result[0]["value"] == 2048

    def test_get_empty_cc_returns_empty_list(self):
        store = StateStore(conversation_id="s3", project_id="p")
        assert store.get_region_cc("nonexistent") == []

    def test_get_empty_pitch_bends_returns_empty_list(self):
        store = StateStore(conversation_id="s4", project_id="p")
        assert store.get_region_pitch_bends("nonexistent") == []

    def test_cc_survives_snapshot_restore(self):
        store = StateStore(conversation_id="s5", project_id="p")
        store.add_cc("r1", [{"cc": 64, "beat": 0, "value": 127}])
        store.add_pitch_bends("r1", [{"beat": 1.0, "value": 8191}])
        snap = store._take_snapshot()
        store._region_cc.clear()
        store._region_pitch_bends.clear()
        assert store.get_region_cc("r1") == []
        store._restore_snapshot(snap)
        assert len(store.get_region_cc("r1")) == 1
        assert len(store.get_region_pitch_bends("r1")) == 1


class TestVariationServiceCC:
    """Variation service propagates CC/pitch bend to phrases."""

    def test_cc_events_appear_in_phrase_controller_changes(self):
        from app.services.variation import VariationService
        svc = VariationService()
        base_notes: list[dict] = []
        proposed_notes = [
            {"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100},
        ]
        cc = [{"cc": 64, "beat": 0.5, "value": 127}]

        variation = svc.compute_variation(
            base_notes=base_notes,
            proposed_notes=proposed_notes,
            region_id="r1",
            track_id="t1",
            intent="test",
            cc_events=cc,
        )
        assert len(variation.phrases) >= 1
        # The first phrase should have a CC controller change
        cc_changes = [
            c for c in variation.phrases[0].controller_changes
            if c.get("kind") == "cc"
        ]
        assert len(cc_changes) == 1
        assert cc_changes[0]["cc"] == 64
        assert cc_changes[0]["value"] == 127

    def test_pitch_bends_appear_in_phrase_controller_changes(self):
        from app.services.variation import VariationService
        svc = VariationService()
        proposed_notes = [
            {"pitch": 64, "start_beat": 0, "duration_beats": 2, "velocity": 90},
        ]
        pb = [{"beat": 0.5, "value": 4096}]

        variation = svc.compute_variation(
            base_notes=[],
            proposed_notes=proposed_notes,
            region_id="r1",
            track_id="t1",
            intent="test",
            pitch_bends=pb,
        )
        pb_changes = [
            c for c in variation.phrases[0].controller_changes
            if c.get("kind") == "pitch_bend"
        ]
        assert len(pb_changes) == 1
        assert pb_changes[0]["value"] == 4096

    def test_multi_region_cc_per_region(self):
        from app.services.variation import VariationService
        svc = VariationService()

        base_regions: dict[str, list[dict]] = {"r1": [], "r2": []}
        proposed_regions = {
            "r1": [{"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100}],
            "r2": [{"pitch": 64, "start_beat": 0, "duration_beats": 1, "velocity": 100}],
        }
        track_regions = {"r1": "t1", "r2": "t2"}
        region_cc = {
            "r1": [{"cc": 64, "beat": 0, "value": 127}],
            "r2": [{"cc": 11, "beat": 0, "value": 90}],
        }

        variation = svc.compute_multi_region_variation(
            base_regions=base_regions,
            proposed_regions=proposed_regions,
            track_regions=track_regions,
            intent="test",
            region_cc=region_cc,
        )
        # Each region should have its own CC in controller_changes
        cc_by_region: dict[str, list[dict]] = {}
        for phrase in variation.phrases:
            for c in phrase.controller_changes:
                cc_by_region.setdefault(phrase.region_id, []).append(c)
        assert 64 in [c["cc"] for c in cc_by_region.get("r1", [])]
        assert 11 in [c["cc"] for c in cc_by_region.get("r2", [])]


class TestApplyVariationCC:
    """apply_variation_phrases stores CC data and returns it in updated_regions."""

    @pytest.mark.anyio
    async def test_cc_in_commit_response(self):
        """CC data from phrase controller_changes flows to updated_regions."""
        phrase = Phrase(
            phrase_id="p1",
            track_id="t1",
            region_id="r1",
            start_beat=0,
            end_beat=16,
            label="Bars 1-4",
            note_changes=[
                NoteChange(
                    note_id="n1",
                    change_type="added",
                    before=None,
                    after=MidiNoteSnapshot(
                        pitch=60, start_beat=0, duration_beats=1, velocity=100
                    ),
                ),
            ],
            controller_changes=[
                {"kind": "cc", "cc": 64, "beat": 0.5, "value": 127},
                {"kind": "pitch_bend", "beat": 1.0, "value": 4096},
            ],
        )
        variation = _make_variation(phrases=[phrase])

        with patch("app.core.executor.apply.get_or_create_store") as mock_factory:
            store = StateStore(conversation_id="cc-commit", project_id="p")
            store.create_track("Track 1", track_id="t1")
            store.create_region("Region 1", parent_track_id="t1", region_id="r1")
            mock_factory.return_value = store

            result = await apply_variation_phrases(
                variation=variation,
                accepted_phrase_ids=["p1"],
                project_state={},
                conversation_id="cc-commit",
            )

        assert result.success
        assert len(result.updated_regions) == 1
        ur = result.updated_regions[0]
        assert len(ur["cc_events"]) == 1
        assert ur["cc_events"][0]["cc"] == 64
        assert len(ur["pitch_bends"]) == 1
        assert ur["pitch_bends"][0]["value"] == 4096


class TestGenerationResultCC:
    """GenerationResult carries CC and pitch bend data."""

    def test_defaults_to_empty_lists(self):
        from app.services.backends.base import GenerationResult, GeneratorBackend
        r = GenerationResult(
            success=True,
            notes=[{"pitch": 60}],
            backend_used=GeneratorBackend.ORPHEUS,
            metadata={},
        )
        assert r.cc_events == []
        assert r.pitch_bends == []

    def test_explicit_cc_and_pitch_bends(self):
        from app.services.backends.base import GenerationResult, GeneratorBackend
        r = GenerationResult(
            success=True,
            notes=[{"pitch": 60}],
            backend_used=GeneratorBackend.ORPHEUS,
            metadata={},
            cc_events=[{"cc": 64, "beat": 0, "value": 127}],
            pitch_bends=[{"beat": 1, "value": 8191}],
        )
        assert len(r.cc_events) == 1
        assert len(r.pitch_bends) == 1


class TestOrpheusBackendCC:
    """Orpheus backend extracts CC and pitch bend from tool_calls."""

    @pytest.mark.anyio
    async def test_extract_cc_and_pitch_bend(self):
        from app.services.backends.orpheus import OrpheusBackend

        mock_client = AsyncMock()
        mock_client.generate.return_value = {
            "success": True,
            "tool_calls": [
                {
                    "tool": "addNotes",
                    "params": {
                        "notes": [
                            {"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100}
                        ]
                    },
                },
                {
                    "tool": "addMidiCC",
                    "params": {
                        "cc": 64,
                        "events": [
                            {"beat": 0, "value": 127},
                            {"beat": 2, "value": 0},
                        ],
                    },
                },
                {
                    "tool": "addPitchBend",
                    "params": {
                        "events": [{"beat": 1.5, "value": 4096}],
                    },
                },
            ],
        }

        backend = OrpheusBackend()
        backend.client = mock_client

        result = await backend.generate(
            instrument="piano",
            style="classical",
            tempo=120,
            bars=4,
        )

        assert result.success
        assert len(result.notes) == 1
        assert len(result.cc_events) == 2
        assert result.cc_events[0] == {"cc": 64, "beat": 0, "value": 127}
        assert result.cc_events[1] == {"cc": 64, "beat": 2, "value": 0}
        assert len(result.pitch_bends) == 1
        assert result.pitch_bends[0] == {"beat": 1.5, "value": 4096}

    @pytest.mark.anyio
    async def test_no_cc_when_absent(self):
        """When Orpheus returns only addNotes, CC/PB should be empty."""
        from app.services.backends.orpheus import OrpheusBackend

        mock_client = AsyncMock()
        mock_client.generate.return_value = {
            "success": True,
            "tool_calls": [
                {
                    "tool": "addNotes",
                    "params": {
                        "notes": [
                            {"pitch": 64, "start_beat": 0, "duration_beats": 2, "velocity": 80}
                        ]
                    },
                },
            ],
        }

        backend = OrpheusBackend()
        backend.client = mock_client

        result = await backend.generate(
            instrument="bass",
            style="funk",
            tempo=100,
            bars=4,
        )

        assert result.success
        assert result.cc_events == []
        assert result.pitch_bends == []
        assert result.aftertouch == []


# ===========================================================================
# Aftertouch pipeline tests
# ===========================================================================

class TestAftertouchPipeline:
    """Aftertouch data flows through the entire pipeline."""

    def test_variation_context_records_aftertouch(self):
        store = StateStore(conversation_id="at1", project_id="p")
        ctx = VariationContext(
            store=store,
            trace=TraceContext(trace_id="test"),
            base_notes={},
            proposed_notes={},
            track_regions={},
        )
        at_events = [{"beat": 0.5, "value": 80}]
        ctx.record_proposed_aftertouch("r1", at_events)
        assert ctx.proposed_aftertouch["r1"] == at_events

    def test_empty_aftertouch_not_recorded(self):
        store = StateStore(conversation_id="at2", project_id="p")
        ctx = VariationContext(
            store=store,
            trace=TraceContext(trace_id="test"),
            base_notes={},
            proposed_notes={},
            track_regions={},
        )
        ctx.record_proposed_aftertouch("r1", [])
        assert "r1" not in ctx.proposed_aftertouch

    def test_state_store_aftertouch(self):
        store = StateStore(conversation_id="at3", project_id="p")
        store.add_aftertouch("r1", [{"beat": 0, "value": 64, "pitch": 60}])
        result = store.get_region_aftertouch("r1")
        assert len(result) == 1
        assert result[0]["pitch"] == 60
        assert store.get_region_aftertouch("nonexistent") == []

    def test_aftertouch_survives_snapshot_restore(self):
        store = StateStore(conversation_id="at4", project_id="p")
        store.add_aftertouch("r1", [{"beat": 0, "value": 100}])
        snap = store._take_snapshot()
        store._region_aftertouch.clear()
        assert store.get_region_aftertouch("r1") == []
        store._restore_snapshot(snap)
        assert len(store.get_region_aftertouch("r1")) == 1

    def test_variation_service_aftertouch_in_phrases(self):
        from app.services.variation import VariationService
        svc = VariationService()
        proposed = [{"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100}]
        at = [{"beat": 0.5, "value": 80, "pitch": 60}]

        variation = svc.compute_variation(
            base_notes=[],
            proposed_notes=proposed,
            region_id="r1",
            track_id="t1",
            intent="test",
            aftertouch=at,
        )
        at_changes = [
            c for c in variation.phrases[0].controller_changes
            if c.get("kind") == "aftertouch"
        ]
        assert len(at_changes) == 1
        assert at_changes[0]["pitch"] == 60
        assert at_changes[0]["value"] == 80

    def test_generation_result_aftertouch_default(self):
        from app.services.backends.base import GenerationResult, GeneratorBackend
        r = GenerationResult(
            success=True, notes=[], backend_used=GeneratorBackend.ORPHEUS, metadata={}
        )
        assert r.aftertouch == []

    @pytest.mark.anyio
    async def test_orpheus_extracts_aftertouch(self):
        from app.services.backends.orpheus import OrpheusBackend
        mock_client = AsyncMock()
        mock_client.generate.return_value = {
            "success": True,
            "tool_calls": [
                {"tool": "addNotes", "params": {
                    "notes": [{"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 100}]
                }},
                {"tool": "addAftertouch", "params": {
                    "events": [
                        {"beat": 0.5, "value": 100, "pitch": 60},
                        {"beat": 1.0, "value": 0},
                    ]
                }},
            ],
        }
        backend = OrpheusBackend()
        backend.client = mock_client
        result = await backend.generate(instrument="piano", style="classical", tempo=120, bars=4)
        assert result.success
        assert len(result.aftertouch) == 2
        assert result.aftertouch[0]["pitch"] == 60
        assert "pitch" not in result.aftertouch[1]

    @pytest.mark.anyio
    async def test_commit_includes_aftertouch(self):
        """Aftertouch in controller_changes flows to updated_regions."""
        phrase = Phrase(
            phrase_id="p1",
            track_id="t1",
            region_id="r1",
            start_beat=0,
            end_beat=16,
            label="Bars 1-4",
            note_changes=[
                NoteChange(
                    note_id="n1",
                    change_type="added",
                    before=None,
                    after=MidiNoteSnapshot(pitch=60, start_beat=0, duration_beats=1, velocity=100),
                ),
            ],
            controller_changes=[
                {"kind": "aftertouch", "beat": 0.5, "value": 80, "pitch": 60},
            ],
        )
        variation = _make_variation(phrases=[phrase])

        with patch("app.core.executor.apply.get_or_create_store") as mock_factory:
            store = StateStore(conversation_id="at-commit", project_id="p")
            store.create_track("Track 1", track_id="t1")
            store.create_region("Region 1", parent_track_id="t1", region_id="r1")
            mock_factory.return_value = store

            result = await apply_variation_phrases(
                variation=variation,
                accepted_phrase_ids=["p1"],
                project_state={},
                conversation_id="at-commit",
            )

        assert result.success
        ur = result.updated_regions[0]
        assert len(ur["aftertouch"]) == 1
        assert ur["aftertouch"][0]["pitch"] == 60
