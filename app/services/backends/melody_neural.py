"""
Neural Melody Backend: Drop-in replacement for MelodySpecBackend.

Uses NeuralMelodyGenerator with emotion vector conditioning instead of
the rule-based melody_ir_renderer.

This is the MVP integration point for neural melody generation.
"""
from __future__ import annotations

import logging

from app.contracts.generation_types import GenerationContext
from app.contracts.json_types import JSONValue, NoteDict
from app.services.backends.base import (
    GenerationMetadata,
    GenerationResult,
    GeneratorBackend,
    MusicGeneratorBackend,
)
from app.services.neural.melody_generator import NeuralMelodyGenerator
from app.core.emotion_vector import EmotionVector, get_emotion_preset

logger = logging.getLogger(__name__)


class MelodyNeuralBackend(MusicGeneratorBackend):
    """
    Neural melody generation backend.
    
    Drop-in replacement for MelodySpecBackend that uses emotion-conditioned
    neural generation instead of rule-based rendering.
    
    Handles: lead, melody, synth, vocal instruments.
    """
    
    def __init__(self) -> None:
        self._generator: NeuralMelodyGenerator | None = None
    
    @property
    def generator(self) -> NeuralMelodyGenerator:
        """Lazy initialization of generator."""
        if self._generator is None:
            self._generator = NeuralMelodyGenerator()
        return self._generator
    
    @property
    def backend_type(self) -> GeneratorBackend:
        # Use a new backend type for neural
        # For now, return MELODY_IR and add a flag in metadata
        return GeneratorBackend.MELODY_IR
    
    async def is_available(self) -> bool:
        """Check if neural backend is available."""
        return await self.generator.is_available()
    
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
                error="MelodyNeuralBackend only handles lead/melody/synth/vocal",
            )
        
        try:
            ctx = context or {}
            emotion_vector = self._resolve_emotion_vector(ctx, style)
            
            if chords is None and "music_spec" in ctx:
                music_spec = ctx["music_spec"]
                if music_spec and hasattr(music_spec, "harmonic_spec") and music_spec.harmonic_spec:
                    harmonic = music_spec.harmonic_spec
                    if hasattr(harmonic, "chord_schedule") and harmonic.chord_schedule:
                        chords = [entry.chord for entry in harmonic.chord_schedule]
            
            result = await self.generator.generate(
                bars=bars,
                tempo=tempo,
                key=key or "C",
                chords=chords,
                emotion_vector=emotion_vector,
                temperature=ctx.get("temperature", 1.0),
            )
            
            if not result.success:
                return GenerationResult(
                    success=False,
                    notes=[],
                    backend_used=self.backend_type,
                    metadata=result.metadata,
                    error=result.metadata.get("error", "Generation failed"),
                )
            
            out: list[NoteDict] = [
                {
                    "pitch": n["pitch"],
                    "start_beat": n["start_beat"],
                    "duration_beats": n["duration_beats"],
                    "velocity": n["velocity"],
                }
                for n in result.notes
            ]
            
            logger.info(f"MelodyNeuralBackend: {len(out)} notes, model={result.model_used}")
            
            meta: GenerationMetadata = {
                "source": "melody_neural",
                "model": result.model_used,
                "emotion_vector": emotion_vector.to_dict(),
                **result.metadata,
            }
            return GenerationResult(
                success=True,
                notes=out,
                backend_used=self.backend_type,
                metadata=meta,
            )
            
        except Exception as e:
            logger.exception(f"MelodyNeuralBackend failed: {e}")
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error=str(e),
            )
    
    def _resolve_emotion_vector(self, ctx: GenerationContext, style: str) -> EmotionVector:
        """Resolve emotion vector from context or derive from style/section.

        Priority: explicit emotion_vector > section_type preset > style preset > neutral.
        """
        if "emotion_vector" in ctx:
            ev = ctx["emotion_vector"]
            if isinstance(ev, EmotionVector):
                return ev
        
        section_type = ctx.get("section_type")
        if section_type:
            preset = get_emotion_preset(section_type.lower())
            if preset:
                return preset
        
        # Try style as preset
        style_preset = get_emotion_preset(style.lower().replace(" ", "_"))
        if style_preset:
            return style_preset
        
        # Default neutral
        return EmotionVector()
