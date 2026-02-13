"""
Neural Melody Backend: Drop-in replacement for MelodySpecBackend.

Uses NeuralMelodyGenerator with emotion vector conditioning instead of
the rule-based melody_ir_renderer.

This is the MVP integration point for neural melody generation.
"""

import logging
from typing import Optional

from app.services.backends.base import (
    MusicGeneratorBackend,
    GenerationResult,
    GeneratorBackend,
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
    
    def __init__(self):
        self._generator: Optional[NeuralMelodyGenerator] = None
    
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
        key: Optional[str] = None,
        chords: Optional[list[str]] = None,
        **kwargs,
    ) -> GenerationResult:
        """
        Generate melody using neural model.
        
        Args:
            instrument: Must be lead/melody/synth/vocal
            style: Style string (used for emotion preset lookup)
            tempo: Tempo in BPM
            bars: Number of bars
            key: Musical key
            chords: Optional chord progression
            **kwargs: May include:
                - emotion_vector: EmotionVector or dict
                - section_type: str (verse/chorus/etc for preset lookup)
                - temperature: float
        """
        if instrument not in ("lead", "melody", "synth", "vocal"):
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error="MelodyNeuralBackend only handles lead/melody/synth/vocal",
            )
        
        try:
            # Get emotion vector from kwargs or derive from style/section
            emotion_vector = self._resolve_emotion_vector(kwargs, style)
            
            # Get chords from kwargs if not provided directly
            if chords is None and "music_spec" in kwargs:
                music_spec = kwargs["music_spec"]
                if hasattr(music_spec, "harmonic_spec") and music_spec.harmonic_spec:
                    harmonic = music_spec.harmonic_spec
                    if hasattr(harmonic, "chord_schedule") and harmonic.chord_schedule:
                        chords = [entry.chord for entry in harmonic.chord_schedule]
            
            # Generate
            result = await self.generator.generate(
                bars=bars,
                tempo=tempo,
                key=key or "C",
                chords=chords,
                emotion_vector=emotion_vector,
                temperature=kwargs.get("temperature", 1.0),
            )
            
            if not result.success:
                return GenerationResult(
                    success=False,
                    notes=[],
                    backend_used=self.backend_type,
                    metadata=result.metadata,
                    error=result.metadata.get("error", "Generation failed"),
                )
            
            # Format notes for output
            out = [
                {
                    "pitch": n["pitch"],
                    "startBeat": n["startBeat"],
                    "duration": n["duration"],
                    "velocity": n["velocity"],
                }
                for n in result.notes
            ]
            
            logger.info(f"MelodyNeuralBackend: {len(out)} notes, model={result.model_used}")
            
            return GenerationResult(
                success=True,
                notes=out,
                backend_used=self.backend_type,
                metadata={
                    "source": "melody_neural",
                    "model": result.model_used,
                    "emotion_vector": emotion_vector.to_dict(),
                    **result.metadata,
                },
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
    
    def _resolve_emotion_vector(self, kwargs: dict, style: str) -> EmotionVector:
        """
        Resolve emotion vector from kwargs or derive from context.
        
        Priority:
        1. Explicit emotion_vector in kwargs
        2. Section type preset (verse/chorus/etc)
        3. Style/genre preset
        4. Neutral default
        """
        # Check for explicit emotion vector
        if "emotion_vector" in kwargs:
            ev = kwargs["emotion_vector"]
            if isinstance(ev, EmotionVector):
                return ev
            if isinstance(ev, dict):
                return EmotionVector.from_dict(ev)
        
        # Check for section type
        if "section_type" in kwargs:
            section = kwargs["section_type"].lower()
            preset = get_emotion_preset(section)
            if preset:
                return preset
        
        # Try style as preset
        style_preset = get_emotion_preset(style.lower().replace(" ", "_"))
        if style_preset:
            return style_preset
        
        # Default neutral
        return EmotionVector()
