"""
Text2MIDI Generator Backend.

Wraps the Text2MidiBackend to implement the MusicGeneratorBackend interface,
making neural generation available through the standard MusicGenerator pipeline.

This is the primary generation backend for Stori - uses the amaai-lab/text2midi
model via HuggingFace Spaces to generate high-quality MIDI from natural language.
"""
from __future__ import annotations

import logging

from app.contracts.generation_types import GenerationContext
from app.contracts.json_types import JSONValue
from app.services.backends.base import (
    GenerationMetadata,
    GenerationResult,
    GeneratorBackend,
    MusicGeneratorBackend,
)
from app.services.neural.text2midi_backend import Text2MidiBackend
from app.core.emotion_vector import EmotionVector, get_emotion_preset

logger = logging.getLogger(__name__)


# Map instrument types to text2midi-friendly names
INSTRUMENT_MAP = {
    "drums": "drums",
    "bass": "bass",
    "piano": "piano",
    "keys": "piano",
    "chords": "piano",
    "harmony": "piano",
    "lead": "synthesizer",
    "melody": "piano",
    "synth": "synthesizer",
    "vocal": "voice",
    "guitar": "guitar",
    "strings": "strings",
    "brass": "brass",
    "organ": "organ",
}

# Map styles to emotion presets
STYLE_EMOTION_MAP = {
    # High energy styles
    "trap": EmotionVector(energy=0.8, valence=0.2, tension=0.5, intimacy=0.3, motion=0.8),
    "edm": EmotionVector(energy=0.9, valence=0.6, tension=0.4, intimacy=0.2, motion=0.9),
    "house": EmotionVector(energy=0.7, valence=0.5, tension=0.3, intimacy=0.4, motion=0.8),
    "rock": EmotionVector(energy=0.8, valence=0.4, tension=0.5, intimacy=0.3, motion=0.7),
    "metal": EmotionVector(energy=0.95, valence=-0.2, tension=0.8, intimacy=0.1, motion=0.9),
    
    # Mid energy styles
    "pop": EmotionVector(energy=0.6, valence=0.6, tension=0.3, intimacy=0.5, motion=0.6),
    "funk": EmotionVector(energy=0.7, valence=0.5, tension=0.3, intimacy=0.4, motion=0.8),
    "soul": EmotionVector(energy=0.5, valence=0.4, tension=0.3, intimacy=0.7, motion=0.5),
    "r&b": EmotionVector(energy=0.5, valence=0.3, tension=0.3, intimacy=0.8, motion=0.5),
    "disco": EmotionVector(energy=0.75, valence=0.7, tension=0.2, intimacy=0.4, motion=0.8),
    
    # Low energy styles
    "jazz": EmotionVector(energy=0.4, valence=0.3, tension=0.4, intimacy=0.7, motion=0.5),
    "blues": EmotionVector(energy=0.4, valence=-0.3, tension=0.4, intimacy=0.7, motion=0.4),
    "ambient": EmotionVector(energy=0.2, valence=0.2, tension=0.2, intimacy=0.6, motion=0.2),
    "classical": EmotionVector(energy=0.4, valence=0.3, tension=0.4, intimacy=0.5, motion=0.4),
    "lofi": EmotionVector(energy=0.3, valence=0.2, tension=0.2, intimacy=0.8, motion=0.3),
    
    # Groovy styles
    "boom_bap": EmotionVector(energy=0.5, valence=0.3, tension=0.3, intimacy=0.5, motion=0.6),
    "hip_hop": EmotionVector(energy=0.6, valence=0.3, tension=0.4, intimacy=0.4, motion=0.7),
    "reggae": EmotionVector(energy=0.5, valence=0.5, tension=0.2, intimacy=0.6, motion=0.5),
    
    # Specialty styles
    "phish": EmotionVector(energy=0.6, valence=0.5, tension=0.4, intimacy=0.5, motion=0.7),
    "grateful_dead": EmotionVector(energy=0.5, valence=0.4, tension=0.3, intimacy=0.6, motion=0.6),
    "cinematic": EmotionVector(energy=0.5, valence=0.2, tension=0.6, intimacy=0.4, motion=0.5),
    "epic": EmotionVector(energy=0.8, valence=0.3, tension=0.7, intimacy=0.2, motion=0.6),
}


class Text2MidiGeneratorBackend(MusicGeneratorBackend):
    """
    Neural MIDI generation via text2midi HuggingFace Space.
    
    This backend:
    1. Maps style to emotion vector
    2. Converts instrument + style + emotion to text description
    3. Calls text2midi to generate MIDI
    4. Returns notes in standard format
    """
    
    def __init__(self) -> None:
        self._backend: Text2MidiBackend | None = None
    
    @property
    def backend(self) -> Text2MidiBackend:
        """Lazy-initialize the text2midi backend."""
        if self._backend is None:
            self._backend = Text2MidiBackend()
        return self._backend
    
    @property
    def backend_type(self) -> GeneratorBackend:
        return GeneratorBackend.TEXT2MIDI
    
    async def is_available(self) -> bool:
        """Check if text2midi Space is reachable."""
        try:
            return await self.backend.is_available()
        except Exception as e:
            logger.debug(f"text2midi not available: {e}")
            return False
    
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
        try:
            ctx = context or {}
            emotion_vector = ctx.get("emotion_vector")
            if emotion_vector is None:
                # Try to get from style
                emotion_vector = STYLE_EMOTION_MAP.get(
                    style.lower().replace("-", "_").replace(" ", "_"),
                    EmotionVector()  # Default neutral
                )
            
            # Map instrument name
            mapped_instrument = INSTRUMENT_MAP.get(instrument.lower(), "piano")
            
            # Generate via text2midi
            logger.info(
                f"[text2midi] Generating {bars} bars of {style} {instrument} "
                f"at {tempo} BPM (emotion: e={emotion_vector.energy:.1f}, "
                f"v={emotion_vector.valence:.1f})"
            )
            
            result = await self.backend.generate(
                bars=bars,
                tempo=tempo,
                key=key or "C",
                emotion_vector=emotion_vector,
                style=style,
                instrument=mapped_instrument,
                temperature=ctx.get("temperature", 1.0),
            )
            
            if result.success:
                logger.info(f"[text2midi] Generated {len(result.notes)} notes")
                meta: GenerationMetadata = {
                    "source": "text2midi",
                    "model": result.model_used,
                    "emotion_vector": emotion_vector.to_dict(),
                    "style": style,
                    "instrument": mapped_instrument,
                    **(result.metadata or {}),
                }
                return GenerationResult(
                    success=True,
                    notes=result.notes,
                    backend_used=self.backend_type,
                    metadata=meta,
                )
            else:
                logger.warning(f"[text2midi] Generation failed: {result.error}")
                return GenerationResult(
                    success=False,
                    notes=[],
                    backend_used=self.backend_type,
                    metadata={},
                    error=result.error or "text2midi generation failed",
                )
                
        except Exception as e:
            logger.exception(f"[text2midi] Exception: {e}")
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error=str(e),
            )
