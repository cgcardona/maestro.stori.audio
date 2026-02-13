"""Tests for music generation service (Orpheus required; no pattern fallback)."""
import pytest
from app.services.music_generator import MusicGenerator
from app.services.backends.base import GeneratorBackend, GenerationResult


class TestMusicGenerator:
    """Tests for the main generator (Orpheus-first)."""

    @pytest.mark.anyio
    async def test_can_force_specific_backend(self):
        """Should be able to force a specific backend (e.g. drum IR)."""
        generator = MusicGenerator()
        result = await generator.generate(
            instrument="drums",
            style="trap",
            tempo=90,
            bars=4,
            preferred_backend=GeneratorBackend.DRUM_IR,
        )
        assert result.success
        assert result.backend_used == GeneratorBackend.DRUM_IR

    @pytest.mark.anyio
    async def test_drums_use_drum_ir_when_available(self):
        """Drums should use DrumSpecBackend (IR layers, groove, salience) when available."""
        generator = MusicGenerator()
        result = await generator.generate(
            instrument="drums",
            style="trap",
            tempo=120,
            bars=4,
            preferred_backend=GeneratorBackend.DRUM_IR,
        )
        assert result.success
        assert result.backend_used == GeneratorBackend.DRUM_IR
        assert len(result.notes) > 0
        for note in result.notes:
            assert "pitch" in note and "startBeat" in note and "duration" in note and "velocity" in note
        distinct = len(set(n["pitch"] for n in result.notes))
        assert distinct >= 8, f"Expected diverse kit, got {distinct} distinct pitches"


class TestGenerationResult:
    """Tests for GenerationResult dataclass."""

    def test_success_result(self):
        """Test successful result."""
        result = GenerationResult(
            success=True,
            notes=[{"pitch": 60, "startBeat": 0, "duration": 1, "velocity": 100}],
            backend_used=GeneratorBackend.ORPHEUS,
            metadata={"source": "orpheus"},
        )
        assert result.success
        assert len(result.notes) == 1
        assert result.error is None

    def test_error_result(self):
        """Test error result."""
        result = GenerationResult(
            success=False,
            notes=[],
            backend_used=GeneratorBackend.ORPHEUS,
            metadata={},
            error="Connection failed",
        )
        assert not result.success
        assert result.error == "Connection failed"
