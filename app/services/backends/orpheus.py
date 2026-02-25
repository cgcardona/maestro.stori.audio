"""Orpheus Music Transformer backend.

Transmits the full EmotionVector, RoleProfile summary, and
GenerationConstraints so Orpheus consumes structured intent without
re-derivation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any

from app.services.backends.base import (
    MusicGeneratorBackend,
    GenerationResult,
    GeneratorBackend,
)
from app.services.orpheus import get_orpheus_client, normalize_orpheus_tool_calls

if TYPE_CHECKING:
    from app.core.emotion_vector import EmotionVector

logger = logging.getLogger(__name__)

ENABLE_BEAT_RESCALING = os.environ.get("ENABLE_BEAT_RESCALING", "false").lower() in ("1", "true", "yes")

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
    """Rescale beat positions in-place when Orpheus output is compressed."""
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


def _build_intent_hash(
    emotion_vector: dict[str, float] | None,
    role_profile_summary: dict[str, float] | None,
    generation_constraints: dict[str, Any] | None,
    musical_goals: list[str],
) -> str:
    """Stable hash of the full intent payload for idempotency tracking."""
    blob = json.dumps(
        {
            "ev": emotion_vector,
            "rp": role_profile_summary,
            "gc": generation_constraints,
            "goals": sorted(musical_goals),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class OrpheusBackend(MusicGeneratorBackend):
    """Orpheus Music Transformer backend.

    Transmits structured EmotionVector, RoleProfile summary, and
    GenerationConstraints to Orpheus — zero information loss.
    """

    def __init__(self) -> None:
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
        key: str | None = None,
        chords: list[str] | None = None,
        **kwargs: Any,
    ) -> GenerationResult:
        emotion_vector: "EmotionVector" | None = kwargs.get("emotion_vector")
        quality_preset: str = kwargs.get("quality_preset", "quality")

        ev_dict: dict[str, float] | None = None
        rp_dict: dict[str, float] | None = None
        gc_dict: dict[str, Any] | None = None
        musical_goals: list[str] = []

        if emotion_vector is not None:
            ev_dict = emotion_vector.to_dict()

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

        from app.data.role_profiles import get_role_profile
        role_profile = get_role_profile(instrument)
        if role_profile is not None:
            rp_dict = role_profile.to_summary_dict()

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

        if emotion_vector is not None:
            from app.core.emotion_vector import emotion_to_constraints
            constraints = emotion_to_constraints(emotion_vector)
            gc_dict = {
                "drum_density": constraints.drum_density,
                "subdivision": constraints.subdivision,
                "swing_amount": constraints.swing_amount,
                "register_center": constraints.register_center,
                "register_spread": constraints.register_spread,
                "rest_density": constraints.rest_density,
                "leap_probability": constraints.leap_probability,
                "chord_extensions": constraints.chord_extensions,
                "borrowed_chord_probability": constraints.borrowed_chord_probability,
                "harmonic_rhythm_bars": constraints.harmonic_rhythm_bars,
                "velocity_floor": constraints.velocity_floor,
                "velocity_ceiling": constraints.velocity_ceiling,
            }

        intent_goals = [{"name": g, "weight": 1.0, "constraint_type": "soft"} for g in musical_goals]
        trace_id = kwargs.get("trace_id") or str(uuid.uuid4())
        intent_hash = _build_intent_hash(ev_dict, rp_dict, gc_dict, musical_goals)

        logger.info(
            f"⚠️ Orpheus SINGLE-instrument generate({instrument}): "
            f"This path uses seed MIDI + ignores prime_instruments. "
            f"ev={ev_dict is not None} rp={rp_dict is not None} "
            f"gc={gc_dict is not None} "
            f"goals={musical_goals} trace={trace_id[:8]}"
        )

        result = await self.client.generate(
            genre=style,
            tempo=tempo,
            instruments=[instrument],
            bars=bars,
            key=key,
            quality_preset=quality_preset,
            composition_id=kwargs.get("composition_id"),
            emotion_vector=ev_dict,
            role_profile_summary=rp_dict,
            generation_constraints=gc_dict,
            intent_goals=intent_goals,
            seed=kwargs.get("seed"),
            trace_id=trace_id,
            intent_hash=intent_hash,
        )

        if result.get("success"):
            meta = result.get("metadata", {})
            meta["trace_id"] = trace_id
            meta["intent_hash"] = intent_hash

            mvp_notes = result.get("notes")
            if mvp_notes:
                logger.info(f"✅ Orpheus: {len(mvp_notes)} notes (direct)")
                return GenerationResult(
                    success=True,
                    notes=[_normalize_note_keys(n) for n in mvp_notes],
                    backend_used=self.backend_type,
                    metadata=meta,
                )

            tool_calls = result.get("tool_calls", [])
            parsed = normalize_orpheus_tool_calls(tool_calls)
            notes: list[dict[str, Any]] = [_normalize_note_keys(n) for n in parsed["notes"]]
            cc_events: list[dict[str, Any]] = parsed["cc_events"]
            pitch_bends: list[dict[str, Any]] = parsed["pitch_bends"]
            aftertouch: list[dict[str, Any]] = parsed["aftertouch"]

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
                    metadata=meta,
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
                    metadata=meta,
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

    async def generate_unified(
        self,
        instruments: list[str],
        style: str,
        tempo: int,
        bars: int,
        key: str | None = None,
        **kwargs: Any,
    ) -> GenerationResult:
        """Generate all instruments together — coherent multi-instrument output.

        Sends every instrument in a single Orpheus call with unified_output=True.
        The response includes channel_notes keyed by instrument label (bass, keys,
        drums, melody, etc.) so the caller can distribute to tracks.
        """
        emotion_vector: "EmotionVector" | None = kwargs.get("emotion_vector")
        quality_preset: str = kwargs.get("quality_preset", "quality")

        ev_dict: dict[str, float] | None = None
        rp_dict: dict[str, float] | None = None
        gc_dict: dict[str, Any] | None = None
        musical_goals: list[str] = []

        if emotion_vector is not None:
            ev_dict = emotion_vector.to_dict()
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

        # Merge role profiles for all instruments
        from app.data.role_profiles import get_role_profile
        for inst in instruments:
            rp = get_role_profile(inst)
            if rp is not None and rp_dict is None:
                rp_dict = rp.to_summary_dict()

        if emotion_vector is not None:
            from app.core.emotion_vector import emotion_to_constraints
            constraints = emotion_to_constraints(emotion_vector)
            gc_dict = {
                "drum_density": constraints.drum_density,
                "subdivision": constraints.subdivision,
                "swing_amount": constraints.swing_amount,
                "register_center": constraints.register_center,
                "register_spread": constraints.register_spread,
                "rest_density": constraints.rest_density,
                "leap_probability": constraints.leap_probability,
                "chord_extensions": constraints.chord_extensions,
                "borrowed_chord_probability": constraints.borrowed_chord_probability,
                "harmonic_rhythm_bars": constraints.harmonic_rhythm_bars,
                "velocity_floor": constraints.velocity_floor,
                "velocity_ceiling": constraints.velocity_ceiling,
            }

        intent_goals = [
            {"name": g, "weight": 1.0, "constraint_type": "soft"}
            for g in musical_goals
        ]
        trace_id = kwargs.get("trace_id") or str(uuid.uuid4())
        intent_hash = _build_intent_hash(ev_dict, rp_dict, gc_dict, musical_goals)

        logger.info(
            f"🎼 Unified generation: {instruments} in {style} at {tempo} BPM "
            f"({bars} bars) trace={trace_id[:8]}"
        )

        result = await self.client.generate(
            genre=style,
            tempo=tempo,
            instruments=instruments,
            bars=bars,
            key=key,
            quality_preset=quality_preset,
            composition_id=kwargs.get("composition_id"),
            emotion_vector=ev_dict,
            role_profile_summary=rp_dict,
            generation_constraints=gc_dict,
            intent_goals=intent_goals,
            seed=kwargs.get("seed"),
            trace_id=trace_id,
            intent_hash=intent_hash,
            add_outro=kwargs.get("add_outro", False),
            unified_output=True,
        )

        if result.get("success"):
            meta = result.get("metadata", {})
            meta["trace_id"] = trace_id
            meta["intent_hash"] = intent_hash
            meta["unified_instruments"] = instruments

            mvp_notes = result.get("notes", [])
            channel_notes = result.get("channel_notes")

            if mvp_notes or channel_notes:
                logger.info(
                    f"✅ Unified: {len(mvp_notes or [])} flat notes, "
                    f"channels={list(channel_notes.keys()) if channel_notes else 'none'}"
                )
                return GenerationResult(
                    success=True,
                    notes=[_normalize_note_keys(n) for n in (mvp_notes or [])],
                    backend_used=self.backend_type,
                    metadata=meta,
                    channel_notes=channel_notes,
                )
            else:
                logger.warning("Unified generation returned no notes")
                return GenerationResult(
                    success=False,
                    notes=[],
                    backend_used=self.backend_type,
                    metadata=meta,
                    error="Unified generation produced no notes",
                )
        else:
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error=result.get("error", "Unified Orpheus generation failed"),
            )
