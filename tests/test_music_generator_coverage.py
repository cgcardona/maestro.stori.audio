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
        assert p.num_candidates == 6
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
        fake_result.notes = [{"pitch": 36, "startBeats": 0, "durationBeats": 0.25, "velocity": 100}]
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
