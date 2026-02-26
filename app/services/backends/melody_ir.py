"""
Melody Spec IR backend: IR-based melody (phrase, contour, chord resolution).

Renders MelodySpec + GlobalSpec + HarmonicSpec → melody notes. Used for instrument in ("lead", "melody", "synth").
See docs/MIDI_SPEC_IR_SCHEMA.md.
"""
from __future__ import annotations

import logging

from app.contracts.generation_types import GenerationContext
from app.contracts.json_types import NoteDict
from app.services.backends.base import (
    MusicGeneratorBackend,
    GenerationResult,
    GeneratorBackend,
)
from app.core.music_spec_ir import MusicSpec, default_melody_spec, default_harmonic_spec, GlobalSpec
from app.services.backends.melody_ir_renderer import render_melody_spec

logger = logging.getLogger(__name__)


class MelodySpecBackend(MusicGeneratorBackend):
    """IR-based melody: phrase boundaries, contour, chord_schedule resolution."""

    @property
    def backend_type(self) -> GeneratorBackend:
        return GeneratorBackend.MELODY_IR

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
        context: GenerationContext | None = None,
    ) -> GenerationResult:
        if instrument not in ("lead", "melody", "synth", "vocal"):
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error="MelodySpecBackend only handles lead/melody/synth",
            )
        try:
            ctx = context or {}
            music_spec = ctx.get("music_spec")
            if music_spec and music_spec.melody_spec and music_spec.harmonic_spec and music_spec.global_spec:
                global_spec = music_spec.global_spec
                harmonic_spec = music_spec.harmonic_spec
                melody_spec = music_spec.melody_spec
            else:
                global_spec = GlobalSpec(tempo=tempo, bars=bars, key=(key or "C").strip())
                harmonic_spec = default_harmonic_spec(key=key or "C", bars=bars, chords=chords)
                melody_spec = default_melody_spec(bars=bars)
            notes = render_melody_spec(melody_spec, global_spec, harmonic_spec)
            out: list[NoteDict] = [
                {"pitch": n["pitch"], "start_beat": n["start_beat"], "duration_beats": n["duration_beats"], "velocity": n["velocity"]}
                for n in notes
            ]
            logger.info(f"MelodySpecBackend: {len(out)} notes")
            return GenerationResult(
                success=True,
                notes=out,
                backend_used=self.backend_type,
                metadata={"source": "melody_ir"},
            )
        except Exception as e:
            logger.exception(f"MelodySpecBackend failed: {e}")
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error=str(e),
            )
