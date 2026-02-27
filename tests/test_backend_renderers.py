"""Tests for backend renderers (drum_ir, bass_ir, harmonic_ir, melody_ir).

Tests the IR-based MIDI generation backends end-to-end with real rendering.
"""
from __future__ import annotations

import pytest
from maestro.services.backends.drum_ir import DrumSpecBackend
from maestro.services.backends.bass_ir import BassSpecBackend
from maestro.services.backends.harmonic_ir import HarmonicSpecBackend
from maestro.services.backends.melody_ir import MelodySpecBackend
from maestro.contracts.json_types import NoteDict
from maestro.contracts.generation_types import GenerationContext
from maestro.services.backends.base import GeneratorBackend, GenerationResult


# ---------------------------------------------------------------------------
# DrumSpecBackend
# ---------------------------------------------------------------------------


class TestDrumSpecBackend:

    @pytest.fixture
    def backend(self) -> DrumSpecBackend:

        return DrumSpecBackend()

    def test_backend_type(self, backend: DrumSpecBackend) -> None:

        assert backend.backend_type == GeneratorBackend.DRUM_IR

    @pytest.mark.anyio
    async def test_is_available(self, backend: DrumSpecBackend) -> None:

        assert await backend.is_available() is True

    @pytest.mark.anyio
    async def test_generate_drums_trap(self, backend: DrumSpecBackend) -> None:

        result = await backend.generate(
            instrument="drums", style="trap", tempo=120, bars=4
        )
        assert result.success is True
        assert len(result.notes) > 0
        assert result.backend_used == GeneratorBackend.DRUM_IR
        assert "source" in result.metadata

    @pytest.mark.anyio
    async def test_generate_drums_boom_bap(self, backend: DrumSpecBackend) -> None:

        result = await backend.generate(
            instrument="drums", style="boom_bap", tempo=90, bars=4
        )
        assert result.success is True
        assert len(result.notes) > 0

    @pytest.mark.anyio
    async def test_generate_drums_house(self, backend: DrumSpecBackend) -> None:

        result = await backend.generate(
            instrument="drums", style="house", tempo=128, bars=4
        )
        assert result.success is True

    @pytest.mark.anyio
    async def test_generate_wrong_instrument(self, backend: DrumSpecBackend) -> None:

        result = await backend.generate(
            instrument="bass", style="trap", tempo=120, bars=4
        )
        assert result.success is False
        assert result.error is not None and "only handles drums" in result.error

    @pytest.mark.anyio
    async def test_generate_with_music_spec(self, backend: DrumSpecBackend) -> None:

        from maestro.core.music_spec_ir import build_full_music_spec
        spec = build_full_music_spec(style="trap", tempo=120, bars=4)
        result = await backend.generate(
            instrument="drums", style="trap", tempo=120, bars=4,
            context=GenerationContext(music_spec=spec),
        )
        assert result.success is True

    @pytest.mark.anyio
    async def test_note_format(self, backend: DrumSpecBackend) -> None:

        result = await backend.generate(
            instrument="drums", style="trap", tempo=120, bars=4
        )
        if result.notes:
            note = result.notes[0]
            assert "pitch" in note
            assert "start_beat" in note
            assert "duration_beats" in note
            assert "velocity" in note


# ---------------------------------------------------------------------------
# BassSpecBackend
# ---------------------------------------------------------------------------


class TestBassSpecBackend:

    @pytest.fixture
    def backend(self) -> BassSpecBackend:

        return BassSpecBackend()

    def test_backend_type(self, backend: BassSpecBackend) -> None:

        assert backend.backend_type == GeneratorBackend.BASS_IR

    @pytest.mark.anyio
    async def test_is_available(self, backend: BassSpecBackend) -> None:

        assert await backend.is_available() is True

    @pytest.mark.anyio
    async def test_generate_bass(self, backend: BassSpecBackend) -> None:

        result = await backend.generate(
            instrument="bass", style="trap", tempo=120, bars=4, key="Cm"
        )
        assert result.success is True
        assert len(result.notes) > 0
        assert result.backend_used == GeneratorBackend.BASS_IR

    @pytest.mark.anyio
    async def test_generate_wrong_instrument(self, backend: BassSpecBackend) -> None:

        result = await backend.generate(
            instrument="drums", style="trap", tempo=120, bars=4
        )
        assert result.success is False

    @pytest.mark.anyio
    async def test_generate_with_music_spec(self, backend: BassSpecBackend) -> None:

        from maestro.core.music_spec_ir import build_full_music_spec
        spec = build_full_music_spec(style="trap", tempo=120, bars=4, key="Cm")
        result = await backend.generate(
            instrument="bass", style="trap", tempo=120, bars=4, key="Cm",
            context=GenerationContext(music_spec=spec),
        )
        assert result.success is True


# ---------------------------------------------------------------------------
# HarmonicSpecBackend
# ---------------------------------------------------------------------------


class TestHarmonicSpecBackend:

    @pytest.fixture
    def backend(self) -> HarmonicSpecBackend:

        return HarmonicSpecBackend()

    def test_backend_type(self, backend: HarmonicSpecBackend) -> None:

        assert backend.backend_type == GeneratorBackend.HARMONIC_IR

    @pytest.mark.anyio
    async def test_is_available(self, backend: HarmonicSpecBackend) -> None:

        assert await backend.is_available() is True

    @pytest.mark.anyio
    async def test_generate_chords(self, backend: HarmonicSpecBackend) -> None:

        result = await backend.generate(
            instrument="piano", style="jazz", tempo=120, bars=4, key="Cm"
        )
        assert result.success is True
        assert len(result.notes) > 0

    @pytest.mark.anyio
    async def test_generate_wrong_instrument(self, backend: HarmonicSpecBackend) -> None:

        result = await backend.generate(
            instrument="drums", style="trap", tempo=120, bars=4
        )
        assert result.success is False

    @pytest.mark.anyio
    async def test_generate_with_music_spec(self, backend: HarmonicSpecBackend) -> None:

        from maestro.core.music_spec_ir import build_full_music_spec
        spec = build_full_music_spec(style="jazz", tempo=120, bars=8, key="Cm")
        result = await backend.generate(
            instrument="piano", style="jazz", tempo=120, bars=8, key="Cm",
            context=GenerationContext(music_spec=spec),
        )
        assert result.success is True


# ---------------------------------------------------------------------------
# MelodySpecBackend
# ---------------------------------------------------------------------------


class TestMelodySpecBackend:

    @pytest.fixture
    def backend(self) -> MelodySpecBackend:

        return MelodySpecBackend()

    def test_backend_type(self, backend: MelodySpecBackend) -> None:

        assert backend.backend_type == GeneratorBackend.MELODY_IR

    @pytest.mark.anyio
    async def test_is_available(self, backend: MelodySpecBackend) -> None:

        assert await backend.is_available() is True

    @pytest.mark.anyio
    async def test_generate_melody(self, backend: MelodySpecBackend) -> None:

        result = await backend.generate(
            instrument="lead", style="jazz", tempo=120, bars=4, key="Cm"
        )
        assert result.success is True
        assert len(result.notes) > 0

    @pytest.mark.anyio
    async def test_generate_wrong_instrument(self, backend: MelodySpecBackend) -> None:

        result = await backend.generate(
            instrument="drums", style="trap", tempo=120, bars=4
        )
        assert result.success is False

    @pytest.mark.anyio
    async def test_generate_with_music_spec(self, backend: MelodySpecBackend) -> None:

        from maestro.core.music_spec_ir import build_full_music_spec
        spec = build_full_music_spec(style="jazz", tempo=120, bars=8, key="Cm")
        result = await backend.generate(
            instrument="lead", style="jazz", tempo=120, bars=8, key="Cm",
            context=GenerationContext(music_spec=spec),
        )
        assert result.success is True


# ---------------------------------------------------------------------------
# GenerationResult
# ---------------------------------------------------------------------------


class TestGenerationResult:

    def test_success_result(self) -> None:

        notes: list[NoteDict] = [{"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 100}]
        result = GenerationResult(
            success=True,
            notes=notes,
            backend_used=GeneratorBackend.DRUM_IR,
            metadata={"source": "test"},
        )
        assert result.error is None

    def test_failure_result(self) -> None:

        result = GenerationResult(
            success=False,
            notes=[],
            backend_used=GeneratorBackend.DRUM_IR,
            metadata={},
            error="Something went wrong",
        )
        assert result.error == "Something went wrong"
