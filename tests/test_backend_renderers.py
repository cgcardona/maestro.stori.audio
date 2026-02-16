"""Tests for backend renderers (drum_ir, bass_ir, harmonic_ir, melody_ir).

Tests the IR-based MIDI generation backends end-to-end with real rendering.
"""
import pytest
from app.services.backends.drum_ir import DrumSpecBackend
from app.services.backends.bass_ir import BassSpecBackend
from app.services.backends.harmonic_ir import HarmonicSpecBackend
from app.services.backends.melody_ir import MelodySpecBackend
from app.services.backends.base import GeneratorBackend, GenerationResult


# ---------------------------------------------------------------------------
# DrumSpecBackend
# ---------------------------------------------------------------------------


class TestDrumSpecBackend:

    @pytest.fixture
    def backend(self):
        return DrumSpecBackend()

    def test_backend_type(self, backend):
        assert backend.backend_type == GeneratorBackend.DRUM_IR

    @pytest.mark.anyio
    async def test_is_available(self, backend):
        assert await backend.is_available() is True

    @pytest.mark.anyio
    async def test_generate_drums_trap(self, backend):
        result = await backend.generate(
            instrument="drums", style="trap", tempo=120, bars=4
        )
        assert result.success is True
        assert len(result.notes) > 0
        assert result.backend_used == GeneratorBackend.DRUM_IR
        assert "source" in result.metadata

    @pytest.mark.anyio
    async def test_generate_drums_boom_bap(self, backend):
        result = await backend.generate(
            instrument="drums", style="boom_bap", tempo=90, bars=4
        )
        assert result.success is True
        assert len(result.notes) > 0

    @pytest.mark.anyio
    async def test_generate_drums_house(self, backend):
        result = await backend.generate(
            instrument="drums", style="house", tempo=128, bars=4
        )
        assert result.success is True

    @pytest.mark.anyio
    async def test_generate_wrong_instrument(self, backend):
        result = await backend.generate(
            instrument="bass", style="trap", tempo=120, bars=4
        )
        assert result.success is False
        assert "only handles drums" in result.error

    @pytest.mark.anyio
    async def test_generate_with_music_spec(self, backend):
        from app.core.music_spec_ir import build_full_music_spec
        spec = build_full_music_spec(style="trap", tempo=120, bars=4)
        result = await backend.generate(
            instrument="drums", style="trap", tempo=120, bars=4,
            music_spec=spec,
        )
        assert result.success is True

    @pytest.mark.anyio
    async def test_note_format(self, backend):
        result = await backend.generate(
            instrument="drums", style="trap", tempo=120, bars=4
        )
        if result.notes:
            note = result.notes[0]
            assert "pitch" in note
            assert "startBeat" in note
            assert "duration" in note
            assert "velocity" in note


# ---------------------------------------------------------------------------
# BassSpecBackend
# ---------------------------------------------------------------------------


class TestBassSpecBackend:

    @pytest.fixture
    def backend(self):
        return BassSpecBackend()

    def test_backend_type(self, backend):
        assert backend.backend_type == GeneratorBackend.BASS_IR

    @pytest.mark.anyio
    async def test_is_available(self, backend):
        assert await backend.is_available() is True

    @pytest.mark.anyio
    async def test_generate_bass(self, backend):
        result = await backend.generate(
            instrument="bass", style="trap", tempo=120, bars=4, key="Cm"
        )
        assert result.success is True
        assert len(result.notes) > 0
        assert result.backend_used == GeneratorBackend.BASS_IR

    @pytest.mark.anyio
    async def test_generate_wrong_instrument(self, backend):
        result = await backend.generate(
            instrument="drums", style="trap", tempo=120, bars=4
        )
        assert result.success is False

    @pytest.mark.anyio
    async def test_generate_with_music_spec(self, backend):
        from app.core.music_spec_ir import build_full_music_spec
        spec = build_full_music_spec(style="trap", tempo=120, bars=4, key="Cm")
        result = await backend.generate(
            instrument="bass", style="trap", tempo=120, bars=4, key="Cm",
            music_spec=spec,
        )
        assert result.success is True


# ---------------------------------------------------------------------------
# HarmonicSpecBackend
# ---------------------------------------------------------------------------


class TestHarmonicSpecBackend:

    @pytest.fixture
    def backend(self):
        return HarmonicSpecBackend()

    def test_backend_type(self, backend):
        assert backend.backend_type == GeneratorBackend.HARMONIC_IR

    @pytest.mark.anyio
    async def test_is_available(self, backend):
        assert await backend.is_available() is True

    @pytest.mark.anyio
    async def test_generate_chords(self, backend):
        result = await backend.generate(
            instrument="piano", style="jazz", tempo=120, bars=4, key="Cm"
        )
        assert result.success is True
        assert len(result.notes) > 0

    @pytest.mark.anyio
    async def test_generate_wrong_instrument(self, backend):
        result = await backend.generate(
            instrument="drums", style="trap", tempo=120, bars=4
        )
        assert result.success is False

    @pytest.mark.anyio
    async def test_generate_with_music_spec(self, backend):
        from app.core.music_spec_ir import build_full_music_spec
        spec = build_full_music_spec(style="jazz", tempo=120, bars=8, key="Cm")
        result = await backend.generate(
            instrument="piano", style="jazz", tempo=120, bars=8, key="Cm",
            music_spec=spec,
        )
        assert result.success is True


# ---------------------------------------------------------------------------
# MelodySpecBackend
# ---------------------------------------------------------------------------


class TestMelodySpecBackend:

    @pytest.fixture
    def backend(self):
        return MelodySpecBackend()

    def test_backend_type(self, backend):
        assert backend.backend_type == GeneratorBackend.MELODY_IR

    @pytest.mark.anyio
    async def test_is_available(self, backend):
        assert await backend.is_available() is True

    @pytest.mark.anyio
    async def test_generate_melody(self, backend):
        result = await backend.generate(
            instrument="lead", style="jazz", tempo=120, bars=4, key="Cm"
        )
        assert result.success is True
        assert len(result.notes) > 0

    @pytest.mark.anyio
    async def test_generate_wrong_instrument(self, backend):
        result = await backend.generate(
            instrument="drums", style="trap", tempo=120, bars=4
        )
        assert result.success is False

    @pytest.mark.anyio
    async def test_generate_with_music_spec(self, backend):
        from app.core.music_spec_ir import build_full_music_spec
        spec = build_full_music_spec(style="jazz", tempo=120, bars=8, key="Cm")
        result = await backend.generate(
            instrument="lead", style="jazz", tempo=120, bars=8, key="Cm",
            music_spec=spec,
        )
        assert result.success is True


# ---------------------------------------------------------------------------
# GenerationResult
# ---------------------------------------------------------------------------


class TestGenerationResult:

    def test_success_result(self):
        result = GenerationResult(
            success=True,
            notes=[{"pitch": 60, "startBeat": 0, "duration": 1, "velocity": 100}],
            backend_used=GeneratorBackend.DRUM_IR,
            metadata={"source": "test"},
        )
        assert result.error is None

    def test_failure_result(self):
        result = GenerationResult(
            success=False,
            notes=[],
            backend_used=GeneratorBackend.DRUM_IR,
            metadata={},
            error="Something went wrong",
        )
        assert result.error == "Something went wrong"
