"""
Bass Spec IR backend: IR-based bass (kick follow, chord follow).

Renders BassSpec + GlobalSpec + HarmonicSpec → MIDI notes. Used for instrument="bass".
Supports coupled generation with RhythmSpine from drum output.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.backends.base import (
    MusicGeneratorBackend,
    GenerationResult,
    GeneratorBackend,
)
from app.core.music_spec_ir import MusicSpec, default_bass_spec, default_drum_spec, default_harmonic_spec, GlobalSpec
from app.services.backends.bass_ir_renderer import BassRenderResult, render_bass_spec
from app.services.groove_engine import RhythmSpine

logger = logging.getLogger(__name__)


class BassSpecBackend(MusicGeneratorBackend):
    """
    IR-based bass: plan (BassSpec + GlobalSpec + HarmonicSpec) → notes.
    
    Kick follow, chord follow. Now supports coupled generation:
    - Pass rhythm_spine from drum generation for kick/snare locking
    - Or pass drum_kick_beats directly for explicit kick alignment
    """

    @property
    def backend_type(self) -> GeneratorBackend:
        return GeneratorBackend.BASS_IR

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
        if instrument != "bass":
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error="BassSpecBackend only handles bass",
            )
        try:
            # Extract coupling parameters
            rhythm_spine: RhythmSpine | None = kwargs.pop("rhythm_spine", None)
            drum_kick_beats: list[float] | None = kwargs.pop("drum_kick_beats", None)
            
            music_spec = kwargs.get("music_spec")
            if music_spec and music_spec.bass_spec and music_spec.harmonic_spec and music_spec.global_spec:
                bass_spec = music_spec.bass_spec
                global_spec = music_spec.global_spec
                harmonic_spec = music_spec.harmonic_spec
                drum_spec = music_spec.drum_spec or default_drum_spec(style=style, bars=bars)
            else:
                bass_spec = default_bass_spec(style=style, bars=bars)
                global_spec = GlobalSpec(tempo=tempo, bars=bars, key=(key or "C").strip())
                harmonic_spec = default_harmonic_spec(key=key or "C", bars=bars, chords=chords)
                drum_spec = default_drum_spec(style=style, bars=bars)
            
            # Log coupling info
            coupling_info = "uncoupled"
            if rhythm_spine:
                coupling_info = f"coupled (spine: {len(rhythm_spine.kick_onsets)} kicks)"
            elif drum_kick_beats:
                coupling_info = f"coupled (explicit: {len(drum_kick_beats)} kicks)"
            
            # Render bass with coupling
            raw_notes = render_bass_spec(
                bass_spec,
                global_spec,
                harmonic_spec,
                drum_groove_template=drum_spec.groove_template,
                drum_kick_beats=drum_kick_beats,
                rhythm_spine=rhythm_spine,
            )
            notes_list: list[dict[str, Any]] = raw_notes.notes if isinstance(raw_notes, BassRenderResult) else raw_notes
            out = [
                {"pitch": n["pitch"], "start_beat": n["start_beat"], "duration_beats": n["duration_beats"], "velocity": n["velocity"]}
                for n in notes_list
            ]
            
            logger.info(f"BassSpecBackend: {len(out)} notes, {coupling_info}")
            
            return GenerationResult(
                success=True,
                notes=out,
                backend_used=self.backend_type,
                metadata={
                    "source": "bass_ir",
                    "coupling": coupling_info,
                    "kick_count": len(rhythm_spine.kick_onsets) if rhythm_spine else (len(drum_kick_beats) if drum_kick_beats else 0),
                },
            )
        except Exception as e:
            logger.exception(f"BassSpecBackend failed: {e}")
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error=str(e),
            )
