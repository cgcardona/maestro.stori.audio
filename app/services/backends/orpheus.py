"""Orpheus Music Transformer backend."""
import logging
import os
from typing import TYPE_CHECKING, Any, Optional

from app.services.backends.base import (
    MusicGeneratorBackend,
    GenerationResult,
    GeneratorBackend,
)
from app.services.orpheus import get_orpheus_client

if TYPE_CHECKING:
    from app.core.emotion_vector import EmotionVector

logger = logging.getLogger(__name__)

ENABLE_BEAT_RESCALING = os.environ.get("ENABLE_BEAT_RESCALING", "false").lower() in ("1", "true", "yes")

# Orpheus may return note fields in snake_case; the DAW client expects camelCase.
_SNAKE_TO_CAMEL: dict[str, str] = {
    "start_beat": "startBeat",
    "duration_beats": "durationBeats",
}


def _normalize_note_keys(note: dict[str, Any]) -> dict[str, Any]:
    """Ensure note dict uses camelCase field names (startBeat, durationBeats)."""
    return {_SNAKE_TO_CAMEL.get(k, k): v for k, v in note.items()}


def _rescale_beats(
    notes: list[dict[str, Any]],
    cc_events: list[dict[str, Any]],
    pitch_bends: list[dict[str, Any]],
    aftertouch: list[dict[str, Any]],
    target_beats: int,
    bars: int = 0,
) -> None:
    """Rescale beat positions in-place when Orpheus output is compressed.

    Orpheus may generate the correct *number* of notes for N bars but place
    them all in a short beat window (e.g. 0-8 beats instead of 0-96).
    Detect this by comparing the max note end position to the target and
    apply a linear scale factor when the content spans less than half the
    requested duration AND the note density suggests compression (at least
    2 notes per bar).
    """
    if not notes or target_beats <= 0:
        return

    min_notes_for_rescale = max(bars * 2, 8)
    if len(notes) < min_notes_for_rescale:
        return

    max_end = max(
        n.get("startBeat", 0) + n.get("durationBeats", 0) for n in notes
    )
    if max_end <= 0 or max_end >= target_beats * 0.5:
        return

    scale = target_beats / max_end
    logger.info(
        f"Rescaling Orpheus output: max_end={max_end:.2f} → "
        f"target={target_beats} (scale={scale:.2f}x)"
    )

    for n in notes:
        n["startBeat"] = round(n.get("startBeat", 0) * scale, 4)
        n["durationBeats"] = round(n.get("durationBeats", 0) * scale, 4)

    for ev in cc_events:
        ev["beat"] = round(ev.get("beat", 0) * scale, 4)

    for ev in pitch_bends:
        ev["beat"] = round(ev.get("beat", 0) * scale, 4)

    for ev in aftertouch:
        ev["beat"] = round(ev.get("beat", 0) * scale, 4)


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
        tone_warmth: float = 0.0
        energy_intensity: float = 0.0
        energy_excitement: float = 0.0
        complexity: float = 0.5
        musical_goals: list[str] = []

        if emotion_vector is not None:
            tone_brightness = float(emotion_vector.valence)
            energy_intensity = float(emotion_vector.energy * 2.0 - 1.0)
            tone_warmth = float(emotion_vector.intimacy * 2.0 - 1.0)
            energy_excitement = float(emotion_vector.motion * 2.0 - 1.0)

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

        # Enrich with heuristic-derived role profile data.
        from app.data.role_profiles import get_role_profile
        role_profile = get_role_profile(instrument)
        if role_profile is not None:
            complexity = role_profile.orpheus_complexity
            if role_profile.rest_ratio > 0.4:
                musical_goals.append("breathing")
            if role_profile.pct_monophonic > 0.8:
                musical_goals.append("monophonic")
            if role_profile.motif_pitch_trigram_repeat > 0.85:
                musical_goals.append("repetitive")
            if role_profile.sustained_ratio > 0.03:
                musical_goals.append("sustained")
            if role_profile.syncopation_ratio > 0.5:
                musical_goals.append("syncopated")

        logger.debug(
            f"Orpheus conditioning ({instrument}): brightness={tone_brightness:.2f} "
            f"warmth={tone_warmth:.2f} intensity={energy_intensity:.2f} "
            f"excitement={energy_excitement:.2f} complexity={complexity:.2f} "
            f"goals={musical_goals}"
        )

        result = await self.client.generate(
            genre=style,
            tempo=tempo,
            instruments=[instrument],
            bars=bars,
            key=key,
            tone_brightness=tone_brightness,
            tone_warmth=tone_warmth,
            energy_intensity=energy_intensity,
            energy_excitement=energy_excitement,
            complexity=complexity,
            musical_goals=musical_goals or None,
            quality_preset=quality_preset,
            composition_id=kwargs.get("composition_id"),
            previous_notes=kwargs.get("previous_notes"),
        )
        
        if result.get("success"):
            # Extract notes, CC events, and pitch bends from Orpheus tool_calls
            # response. We request one instrument per call (instruments=[instrument]);
            # Orpheus filters to that instrument's channel.
            notes: list[dict] = []
            cc_events: list[dict] = []
            pitch_bends: list[dict] = []
            aftertouch: list[dict] = []
            tool_calls = result.get("tool_calls", [])
            
            for tool_call in tool_calls:
                tool_name = tool_call.get("tool", "")
                params = tool_call.get("params", {})

                if tool_name == "addNotes":
                    notes.extend(params.get("notes", []))

                elif tool_name == "addMidiCC":
                    cc_num = params.get("cc")
                    for ev in params.get("events", []):
                        cc_events.append({
                            "cc": cc_num,
                            "beat": ev.get("beat", 0),
                            "value": ev.get("value", 0),
                        })

                elif tool_name == "addPitchBend":
                    for ev in params.get("events", []):
                        pitch_bends.append({
                            "beat": ev.get("beat", 0),
                            "value": ev.get("value", 0),
                        })

                elif tool_name == "addAftertouch":
                    for ev in params.get("events", []):
                        entry: dict = {
                            "beat": ev.get("beat", 0),
                            "value": ev.get("value", 0),
                        }
                        if "pitch" in ev:
                            entry["pitch"] = ev["pitch"]
                        aftertouch.append(entry)
            
            notes = [_normalize_note_keys(n) for n in notes]

            target_beats = bars * 4
            if ENABLE_BEAT_RESCALING:
                _rescale_beats(notes, cc_events, pitch_bends, aftertouch, target_beats, bars=bars)
            else:
                logger.debug("Beat rescaling disabled (ENABLE_BEAT_RESCALING=false)")

            if notes:
                logger.info(
                    f"Orpheus generated {len(notes)} notes, "
                    f"{len(cc_events)} CC, "
                    f"{len(pitch_bends)} PB, "
                    f"{len(aftertouch)} AT"
                )
                return GenerationResult(
                    success=True,
                    notes=notes,
                    backend_used=self.backend_type,
                    metadata={"source": "orpheus", "tool_calls_count": len(tool_calls)},
                    cc_events=cc_events,
                    pitch_bends=pitch_bends,
                    aftertouch=aftertouch,
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
