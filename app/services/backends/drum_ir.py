"""
Drum Spec IR backend: IR-based drum generation (layers, groove, salience).

Renders DrumSpec + GlobalSpec → MIDI notes. Used only for instrument="drums".
Uses Groove Engine for style-specific microtiming.
Accepts optional music_spec from orchestrator. Post: critic + IR-aware repair.
"""
from __future__ import annotations

import logging

from app.contracts.generation_types import GenerationContext
from app.contracts.json_types import JSONValue, NoteDict
from app.services.backends.base import (
    MusicGeneratorBackend,
    GenerationResult,
    GeneratorBackend,
)
from app.core.music_spec_ir import MusicSpec, default_drum_spec, GlobalSpec
from app.services.backends.drum_ir_renderer import DrumRenderResult, render_drum_spec
from app.services.critic import score_drum_notes, ACCEPT_THRESHOLD_DRUM
from app.services.repair import repair_drum_if_needed

logger = logging.getLogger(__name__)


class DrumSpecBackend(MusicGeneratorBackend):
    """
    IR-based drum renderer: plan (DrumSpec + GlobalSpec) → notes.

    Uses groove templates, layered kit (core, timekeepers, ghost, fills, ear candy),
    salience cap, fill bars, and Groove Engine humanization.
    
    Notes include layer labels for critic scoring.
    Only handles instrument="drums".
    """

    @property
    def backend_type(self) -> GeneratorBackend:
        return GeneratorBackend.DRUM_IR

    async def is_available(self) -> bool:
        return True  # Always available (pure Python renderer)

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
        if instrument != "drums":
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error="DrumSpecBackend only handles drums",
            )
        try:
            ctx = context or {}
            music_spec: MusicSpec | None = ctx.get("music_spec")
            if music_spec and music_spec.drum_spec and music_spec.global_spec:
                drum_spec = music_spec.drum_spec
                global_spec = music_spec.global_spec
            else:
                drum_spec = default_drum_spec(style=style, bars=bars)
                global_spec = GlobalSpec(
                    tempo=tempo,
                    bars=bars,
                    key=(key or "C").strip(),
                )
            
            # Render drums with Groove Engine (apply_groove=True)
            # This handles layer labels, salience cap, and style-specific microtiming
            raw_notes = render_drum_spec(
                drum_spec,
                global_spec,
                apply_salience_cap=True,
                apply_groove=True,
            )
            notes = raw_notes.notes if isinstance(raw_notes, DrumRenderResult) else raw_notes
            
            layer_map: dict[int, str] = {
                i: str(n.get("layer", "unknown")) for i, n in enumerate(notes)
            }

            score, repair_instructions = score_drum_notes(
                notes,
                layer_map=layer_map,
                fill_bars=drum_spec.constraints.fill_bars,
                bars=global_spec.bars,
                style=drum_spec.style,
                max_salience_per_beat=drum_spec.constraints.max_salience_per_beat,
            )
            
            repaired_notes, repaired = repair_drum_if_needed(
                notes, drum_spec, score, repair_instructions, accept_threshold=ACCEPT_THRESHOLD_DRUM
            )
            notes = repaired_notes
            
            # Output format: start_beat, duration_beats, velocity, pitch, layer
            # Layer is included for downstream use (e.g., coupled generation scoring)
            out_notes: list[NoteDict] = [
                {
                    "pitch": n["pitch"],
                    "start_beat": n["start_beat"],
                    "duration_beats": n["duration_beats"],
                    "velocity": n["velocity"],
                }
                for n in notes
            ]
            
            distinct = len(set(n["pitch"] for n in out_notes))
            meta: dict[str, object] = {
                "source": "drum_ir",
                "groove_template": drum_spec.groove_template,
                "humanize_profile": global_spec.humanize_profile,
                "distinct_pitches": distinct,
                "critic_score": round(score, 2),
            }
            if repaired:
                meta["repaired"] = True
            
            logger.info(f"DrumSpecBackend: {len(out_notes)} notes, {distinct} distinct pitches, score={score:.2f}")
            
            return GenerationResult(
                success=True,
                notes=out_notes,
                backend_used=self.backend_type,
                metadata=meta,
            )
        except Exception as e:
            logger.exception(f"DrumSpecBackend failed: {e}")
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error=str(e),
            )
