"""
Harmonic Spec IR backend: IR-based chord voicings.

Renders HarmonicSpec + GlobalSpec → chord notes. Used for instrument in ("piano", "chords", "harmony").
See docs/MIDI_SPEC_IR_SCHEMA.md.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.backends.base import (
    MusicGeneratorBackend,
    GenerationResult,
    GeneratorBackend,
)
from app.core.music_spec_ir import MusicSpec, default_harmonic_spec, GlobalSpec
from app.services.backends.harmonic_ir_renderer import render_harmonic_spec

logger = logging.getLogger(__name__)


class HarmonicSpecBackend(MusicGeneratorBackend):
    """IR-based chords: chord_schedule → voicings (root, third, fifth, seventh)."""

    @property
    def backend_type(self) -> GeneratorBackend:
        return GeneratorBackend.HARMONIC_IR

    async def is_available(self) -> bool:
        return True

    async def generate(
        self,
        instrument: str,
        style: str,
        tempo: int,
        bars: int,
        key: str | None = None,
        chords: list[str] | None = None,
        **kwargs: Any,
    ) -> GenerationResult:
        if instrument not in ("piano", "chords", "harmony", "keys"):
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error="HarmonicSpecBackend only handles piano/chords/harmony",
            )
        try:
            music_spec = kwargs.get("music_spec")
            if music_spec and music_spec.harmonic_spec and music_spec.global_spec:
                global_spec = music_spec.global_spec
                harmonic_spec = music_spec.harmonic_spec
            else:
                global_spec = GlobalSpec(tempo=tempo, bars=bars, key=(key or "C").strip())
                harmonic_spec = default_harmonic_spec(key=key or "C", bars=bars, chords=chords)
            notes = render_harmonic_spec(harmonic_spec, global_spec)
            out = [
                {"pitch": n["pitch"], "start_beat": n["start_beat"], "duration_beats": n["duration_beats"], "velocity": n["velocity"]}
                for n in notes
            ]
            logger.info(f"HarmonicSpecBackend: {len(out)} chord notes")
            return GenerationResult(
                success=True,
                notes=out,
                backend_used=self.backend_type,
                metadata={"source": "harmonic_ir"},
            )
        except Exception as e:
            logger.exception(f"HarmonicSpecBackend failed: {e}")
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error=str(e),
            )
