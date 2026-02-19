"""Orpheus Music Transformer backend."""
import logging
from typing import TYPE_CHECKING, Optional

from app.services.backends.base import (
    MusicGeneratorBackend,
    GenerationResult,
    GeneratorBackend,
)
from app.services.orpheus import get_orpheus_client

if TYPE_CHECKING:
    from app.core.emotion_vector import EmotionVector

logger = logging.getLogger(__name__)


class OrpheusBackend(MusicGeneratorBackend):
    """
    Orpheus Music Transformer backend.

    Best quality but requires GPU server running Orpheus.
    Accepts an optional EmotionVector (derived from the STORI PROMPT) and
    maps its 5 axes to Orpheus intent fields so every generation call is
    conditioned on the user's creative brief.
    """

    def __init__(self):
        self.client = get_orpheus_client()

    @property
    def backend_type(self) -> GeneratorBackend:
        return GeneratorBackend.ORPHEUS

    async def is_available(self) -> bool:
        return await self.client.health_check()

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
        # Extract emotion vector and map to Orpheus intent fields.
        emotion_vector: Optional["EmotionVector"] = kwargs.get("emotion_vector")
        quality_preset: str = kwargs.get("quality_preset", "quality")

        tone_brightness: float = 0.0
        energy_intensity: float = 0.0
        musical_goals: list[str] = []

        if emotion_vector is not None:
            # valence [-1, 1] → tone_brightness [-1, 1]
            tone_brightness = float(emotion_vector.valence)
            # energy [0, 1] → energy_intensity scaled to [-1, 1]
            energy_intensity = float(emotion_vector.energy * 2.0 - 1.0)
            # Build a concise goal list from the most salient axes.
            if emotion_vector.energy > 0.7:
                musical_goals.append("energetic")
            elif emotion_vector.energy < 0.3:
                musical_goals.append("sparse")
            if emotion_vector.valence < -0.3:
                musical_goals.append("dark")
            elif emotion_vector.valence > 0.3:
                musical_goals.append("bright")
            if emotion_vector.tension > 0.6:
                musical_goals.append("tense")
            if emotion_vector.intimacy > 0.7:
                musical_goals.append("intimate")
            if emotion_vector.motion > 0.7:
                musical_goals.append("driving")
            elif emotion_vector.motion < 0.25:
                musical_goals.append("sustained")
            logger.debug(
                f"Orpheus emotion conditioning: brightness={tone_brightness:.2f} "
                f"intensity={energy_intensity:.2f} goals={musical_goals}"
            )

        result = await self.client.generate(
            genre=style,
            tempo=tempo,
            instruments=[instrument],
            bars=bars,
            key=key,
            tone_brightness=tone_brightness,
            energy_intensity=energy_intensity,
            musical_goals=musical_goals or None,
            quality_preset=quality_preset,
        )
        
        if result.get("success"):
            # Extract notes from Orpheus tool_calls response. We request one instrument per call
            # (instruments=[instrument]); Orpheus filters to that instrument's channel so we get
            # only that role's notes, not the full mix repeated on every track.
            notes = []
            tool_calls = result.get("tool_calls", [])
            
            for tool_call in tool_calls:
                if tool_call.get("tool") == "addNotes":
                    params = tool_call.get("params", {})
                    call_notes = params.get("notes", [])
                    notes.extend(call_notes)
            
            if notes:
                logger.info(f"Orpheus generated {len(notes)} notes")
                return GenerationResult(
                    success=True,
                    notes=notes,
                    backend_used=self.backend_type,
                    metadata={"source": "orpheus", "tool_calls_count": len(tool_calls)},
                )
            else:
                logger.warning("Orpheus returned success but no notes found in tool_calls")
                return GenerationResult(
                    success=False,
                    notes=[],
                    backend_used=self.backend_type,
                    metadata={},
                    error="No notes found in Orpheus response",
                )
        else:
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error=result.get("error", "Orpheus generation failed"),
            )
