"""Tests for the MusicGenerator service (app/services/music_generator.py).

Covers backend priority, availability, coupled generation, and quality presets.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from app.services.music_generator import (
    MusicGenerator,
    QualityPresetConfig,
    GenerationContext,
    QUALITY_PRESETS,
    get_music_generator,
    reset_music_generator,
)
from app.services.backends.base import GenerationResult


# ---------------------------------------------------------------------------
# Quality Presets
# ---------------------------------------------------------------------------


class TestQualityPresets:

    def test_fast_preset(self):
        p = QUALITY_PRESETS["fast"]
        assert p.num_candidates == 1
        assert p.use_critic is False

    def test_balanced_preset(self):
        p = QUALITY_PRESETS["balanced"]
        assert p.num_candidates == 2
        assert p.use_critic is True
        assert p.use_coupled_generation is True

    def test_quality_preset(self):
        p = QUALITY_PRESETS["quality"]
        assert p.num_candidates == 4
        assert p.use_critic is True


# ---------------------------------------------------------------------------
# GenerationContext
# ---------------------------------------------------------------------------


class TestGenerationContext:

    def test_defaults(self):
        ctx = GenerationContext()
        assert ctx.rhythm_spine is None
        assert ctx.drum_notes is None
        assert ctx.style == "trap"

    def test_custom_values(self):
        ctx = GenerationContext(style="jazz", tempo=100, bars=8)
        assert ctx.style == "jazz"
        assert ctx.tempo == 100


# ---------------------------------------------------------------------------
# MusicGenerator
# ---------------------------------------------------------------------------


class TestMusicGenerator:

    def test_singleton_pattern(self):
        reset_music_generator()
        g1 = get_music_generator()
        g2 = get_music_generator()
        assert g1 is g2
        reset_music_generator()

    @pytest.mark.anyio
    async def test_generate_returns_result(self):
        """Generate returns a GenerationResult."""
        mg = MusicGenerator()

        fake_result = MagicMock()
        fake_result.success = True
        fake_result.notes = [{"pitch": 36, "start_beat": 0, "duration_beats": 0.25, "velocity": 100}]
        fake_result.backend_used = MagicMock()
        fake_result.metadata = {}

        # Mock all backends to fail except one
        with patch.object(mg, "generate", new_callable=AsyncMock, return_value=fake_result):
            result = await mg.generate(instrument="drums", style="boom_bap", tempo=90, bars=4)
            assert result.success is True

    @pytest.mark.anyio
    async def test_backend_map_has_entries(self):
        """MusicGenerator initializes with backend_map."""
        mg = MusicGenerator()
        assert isinstance(mg.backend_map, dict)
        assert len(mg.backend_map) > 0

    def test_generation_context_management(self):
        mg = MusicGenerator()
        ctx = GenerationContext(style="lofi", tempo=80, bars=16)
        mg.set_generation_context(ctx)
        assert mg._generation_context is ctx
        mg.clear_generation_context()
        assert mg._generation_context is None

    @pytest.mark.anyio
    async def test_get_available_backends(self):
        """get_available_backends returns a list."""
        mg = MusicGenerator()
        backends = await mg.get_available_backends()
        assert isinstance(backends, list)


# ---------------------------------------------------------------------------
# _scorer_for_instrument — instrument-role-based scoring
# ---------------------------------------------------------------------------


class TestScorerForInstrument:

    def _mg(self) -> MusicGenerator:
        from app.services.backends.base import GeneratorBackend
        mg = MusicGenerator()
        mg._generation_context = None
        return mg

    def test_drums_returns_scorer(self):
        """Drums role always gets a scorer (enables rejection sampling for Orpheus)."""
        from app.services.backends.base import GeneratorBackend
        mg = self._mg()
        scorer = mg._scorer_for_instrument("drums", GeneratorBackend.ORPHEUS, bars=4, style="trap")
        assert scorer is not None

    def test_bass_returns_scorer(self):
        from app.services.backends.base import GeneratorBackend
        mg = self._mg()
        scorer = mg._scorer_for_instrument("bass", GeneratorBackend.ORPHEUS, bars=4, style="trap")
        assert scorer is not None

    def test_organ_returns_scorer(self):
        """Melodic instruments (organ) also get a scorer — chord scoring."""
        from app.services.backends.base import GeneratorBackend
        mg = self._mg()
        scorer = mg._scorer_for_instrument("organ", GeneratorBackend.ORPHEUS, bars=4, style="ska")
        assert scorer is not None

    def test_unknown_role_returns_none(self):
        """Unknown instrument roles return None (no scoring, single generation)."""
        from app.services.backends.base import GeneratorBackend
        mg = self._mg()
        scorer = mg._scorer_for_instrument("theremin", GeneratorBackend.ORPHEUS, bars=4, style="lofi")
        assert scorer is None

    def test_drum_ir_backend_overrides_role(self):
        """DRUM_IR backend always uses drum scoring regardless of role name."""
        from app.services.backends.base import GeneratorBackend
        mg = self._mg()
        scorer = mg._scorer_for_instrument("mystery", GeneratorBackend.DRUM_IR, bars=4, style="jazz")
        assert scorer is not None


# ---------------------------------------------------------------------------
# _candidates_for_role — smarter candidate counts
# ---------------------------------------------------------------------------


class TestCandidatesForRole:

    def _mg(self) -> MusicGenerator:
        return MusicGenerator()

    def test_drums_keeps_full_candidates(self):
        """Drums keeps the full quality-preset candidate count."""
        from app.services.backends.base import GeneratorBackend
        mg = self._mg()
        config = QUALITY_PRESETS["quality"]  # num_candidates=4
        assert mg._candidates_for_role("drums", config, GeneratorBackend.ORPHEUS) == 4

    def test_bass_keeps_full_candidates(self):
        from app.services.backends.base import GeneratorBackend
        mg = self._mg()
        config = QUALITY_PRESETS["quality"]
        assert mg._candidates_for_role("bass", config, GeneratorBackend.ORPHEUS) == 4

    def test_organ_capped_at_two_for_quality(self):
        """Melodic instruments are capped at 2 candidates for quality preset."""
        from app.services.backends.base import GeneratorBackend
        mg = self._mg()
        config = QUALITY_PRESETS["quality"]  # num_candidates=6
        assert mg._candidates_for_role("organ", config, GeneratorBackend.ORPHEUS) == 2

    def test_guitar_capped_at_two_for_quality(self):
        from app.services.backends.base import GeneratorBackend
        mg = self._mg()
        config = QUALITY_PRESETS["quality"]
        assert mg._candidates_for_role("guitar", config, GeneratorBackend.ORPHEUS) == 2

    def test_non_orpheus_backend_untouched(self):
        """IR backends are not modified — they have their own scoring logic."""
        from app.services.backends.base import GeneratorBackend
        mg = self._mg()
        config = QUALITY_PRESETS["quality"]
        assert mg._candidates_for_role("organ", config, GeneratorBackend.HARMONIC_IR) == 4

    def test_balanced_preset_melodic_unchanged(self):
        """Balanced preset (2 candidates) is not further reduced for melodic tracks."""
        from app.services.backends.base import GeneratorBackend
        mg = self._mg()
        config = QUALITY_PRESETS["balanced"]  # num_candidates=2
        assert mg._candidates_for_role("organ", config, GeneratorBackend.ORPHEUS) == 2


# ---------------------------------------------------------------------------
# Parallel candidate generation via asyncio.gather
# ---------------------------------------------------------------------------


class TestParallelCandidateGeneration:

    @pytest.mark.anyio
    async def test_parallel_candidates_dispatched(self):
        """Quality preset dispatches all candidates concurrently (asyncio.gather)."""
        import asyncio
        from app.services.backends.base import GeneratorBackend, GenerationResult

        mg = MusicGenerator()
        config = QUALITY_PRESETS["quality"]  # 6 candidates

        call_count = [0]
        call_times: list[float] = []

        async def fake_generate(*args, **kwargs):
            call_count[0] += 1
            call_times.append(asyncio.get_event_loop().time())
            return GenerationResult(
                success=True,
                notes=[{"pitch": 36, "start_beat": float(call_count[0] - 1), "duration_beats": 0.25, "velocity": 100}],
                backend_used=GeneratorBackend.ORPHEUS,
                metadata={},
            )

        mock_backend = MagicMock()
        mock_backend.generate = fake_generate
        mock_backend.backend_type = GeneratorBackend.ORPHEUS

        result = await mg._generate_with_coupling(
            backend=mock_backend,
            backend_type=GeneratorBackend.ORPHEUS,
            instrument="drums",
            style="trap",
            tempo=120,
            bars=4,
            key=None,
            chords=None,
            preset_config=config,
            num_candidates=config.num_candidates,
        )

        # All 4 candidates dispatched in parallel (drums keeps full count)
        assert call_count[0] == 4
        assert result.success
        assert "critic_score" in result.metadata
        assert "parallel_candidates" in result.metadata

    @pytest.mark.anyio
    async def test_best_candidate_selected(self):
        """The candidate with the highest critic score is returned."""
        import asyncio
        from app.services.backends.base import GeneratorBackend, GenerationResult

        mg = MusicGenerator()
        config = QUALITY_PRESETS["balanced"]  # 2 candidates

        scores_assigned = [0.3, 0.8]  # second candidate is better
        call_idx = [0]

        async def fake_generate(*args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            # Return notes with a recognisable marker so we can identify which candidate won
            return GenerationResult(
                success=True,
                notes=[{"pitch": 36 + idx, "start_beat": 0.0, "duration_beats": 0.25, "velocity": 100}],
                backend_used=GeneratorBackend.ORPHEUS,
                metadata={"candidate_idx": idx},
            )

        mock_backend = MagicMock()
        mock_backend.generate = fake_generate
        mock_backend.backend_type = GeneratorBackend.ORPHEUS

        with patch(
            "app.services.music_generator.MusicGenerator._scorer_for_instrument",
            return_value=lambda notes: (scores_assigned.pop(0) if scores_assigned else 0.0, None),
        ):
            result = await mg._generate_with_coupling(
                backend=mock_backend,
                backend_type=GeneratorBackend.ORPHEUS,
                instrument="drums",
                style="trap",
                tempo=120,
                bars=4,
                key=None,
                chords=None,
                preset_config=config,
                num_candidates=2,
            )

        assert result.success
        assert result.metadata["critic_score"] == pytest.approx(0.8, abs=0.01)

    @pytest.mark.anyio
    async def test_all_candidates_fail_falls_through(self):
        """If all parallel candidates fail, falls through to single generation."""
        from app.services.backends.base import GeneratorBackend, GenerationResult

        mg = MusicGenerator()
        config = QUALITY_PRESETS["balanced"]

        call_idx = [0]

        async def fake_generate(*args, **kwargs):
            call_idx[0] += 1
            if call_idx[0] <= 2:
                # First two calls (parallel candidates) fail
                return GenerationResult(success=False, notes=[], backend_used=GeneratorBackend.ORPHEUS, metadata={}, error="fail")
            # Third call (single fallback) succeeds
            return GenerationResult(success=True, notes=[{"pitch": 36, "start_beat": 0.0, "duration_beats": 0.25, "velocity": 100}], backend_used=GeneratorBackend.ORPHEUS, metadata={})

        mock_backend = MagicMock()
        mock_backend.generate = fake_generate
        mock_backend.backend_type = GeneratorBackend.ORPHEUS

        result = await mg._generate_with_coupling(
            backend=mock_backend,
            backend_type=GeneratorBackend.ORPHEUS,
            instrument="drums",
            style="trap",
            tempo=120,
            bars=4,
            key=None,
            chords=None,
            preset_config=config,
            num_candidates=2,
        )

        # Falls through to single generation (third call)
        assert result.success
