"""Storpheus Music Transformer backend.

Transmits the full EmotionVector, RoleProfile summary, and
GenerationConstraints so Storpheus consumes structured intent without
re-derivation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from typing import TYPE_CHECKING

from maestro.contracts.generation_types import GenerationContext
from maestro.contracts.json_types import (
    AftertouchDict,
    CCEventDict,
    GenerationConstraintsDict,
    IntentGoalDict,
    JSONValue,
    NoteDict,
    PitchBendDict,
)
from maestro.services.backends.base import (
    GenerationMetadata,
    GenerationResult,
    GeneratorBackend,
    MusicGeneratorBackend,
)
from maestro.services.storpheus import get_storpheus_client, normalize_storpheus_tool_calls

if TYPE_CHECKING:
    from maestro.core.emotion_vector import EmotionVector

logger = logging.getLogger(__name__)

ENABLE_BEAT_RESCALING = os.environ.get("ENABLE_BEAT_RESCALING", "false").lower() in ("1", "true", "yes")

_SNAKE_TO_CAMEL: dict[str, str] = {
    "start_beat": "startBeat",
    "duration_beats": "durationBeats",
}


def _normalize_note_keys(note: NoteDict) -> NoteDict:
    """Ensure note dict uses camelCase timing keys (startBeat, durationBeats) for the DAW.

    Explicit per-field extraction keeps mypy clean without a type: ignore.
    Only the two timing keys are translated; all other keys pass through.
    """
    out: NoteDict = {}
    if "pitch" in note:
        out["pitch"] = note["pitch"]
    if "velocity" in note:
        out["velocity"] = note["velocity"]
    if "channel" in note:
        out["channel"] = note["channel"]
    if "layer" in note:
        out["layer"] = note["layer"]
    if "noteId" in note:
        out["noteId"] = note["noteId"]
    if "note_id" in note:
        out["note_id"] = note["note_id"]
    if "trackId" in note:
        out["trackId"] = note["trackId"]
    if "track_id" in note:
        out["track_id"] = note["track_id"]
    if "regionId" in note:
        out["regionId"] = note["regionId"]
    if "region_id" in note:
        out["region_id"] = note["region_id"]
    # Timing: prefer camelCase for wire format; fall back to snake_case source
    start_beat = note.get("startBeat") if "startBeat" in note else note.get("start_beat")
    if start_beat is not None:
        out["startBeat"] = start_beat
    duration_beats = note.get("durationBeats") if "durationBeats" in note else note.get("duration_beats")
    if duration_beats is not None:
        out["durationBeats"] = duration_beats
    return out


def _rescale_beats(
    notes: list[NoteDict],
    cc_events: list[CCEventDict],
    pitch_bends: list[PitchBendDict],
    aftertouch: list[AftertouchDict],
    target_beats: int,
    bars: int = 0,
) -> None:
    """Rescale beat positions in-place when Storpheus output is compressed."""
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
        f"Rescaling Storpheus output: max_end={max_end:.2f} → "
        f"target={target_beats} (scale={scale:.2f}x)"
    )

    for n in notes:
        n["startBeat"] = round(n.get("startBeat", 0) * scale, 4)
        n["durationBeats"] = round(n.get("durationBeats", 0) * scale, 4)

    for cc_ev in cc_events:
        cc_ev["beat"] = round(cc_ev.get("beat", 0) * scale, 4)

    for pb_ev in pitch_bends:
        pb_ev["beat"] = round(pb_ev.get("beat", 0) * scale, 4)

    for at_ev in aftertouch:
        at_ev["beat"] = round(at_ev.get("beat", 0) * scale, 4)


def _build_intent_hash(
    emotion_vector: dict[str, float] | None,
    role_profile_summary: dict[str, float] | None,
    generation_constraints: GenerationConstraintsDict | None,
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


class StorpheusBackend(MusicGeneratorBackend):
    """Orpheus Music Transformer backend.

    Transmits structured EmotionVector, RoleProfile summary, and
    GenerationConstraints to Orpheus — zero information loss.
    """

    def __init__(self) -> None:
        self.client = get_storpheus_client()

    @property
    def backend_type(self) -> GeneratorBackend:
        return GeneratorBackend.STORPHEUS

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
        context: GenerationContext | None = None,
    ) -> GenerationResult:
        ctx = context or {}
        emotion_vector: "EmotionVector" | None = ctx.get("emotion_vector")
        quality_preset: str = ctx.get("quality_preset", "quality")

        ev_dict: dict[str, float] | None = None
        rp_dict: dict[str, float] | None = None
        gc_dict: GenerationConstraintsDict | None = None
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

        from maestro.data.role_profiles import get_role_profile
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
            from maestro.core.emotion_vector import emotion_to_constraints
            constraints = emotion_to_constraints(emotion_vector)
            gc_dict = GenerationConstraintsDict(
                drum_density=constraints.drum_density,
                subdivision=constraints.subdivision,
                swing_amount=constraints.swing_amount,
                register_center=constraints.register_center,
                register_spread=constraints.register_spread,
                rest_density=constraints.rest_density,
                leap_probability=constraints.leap_probability,
                chord_extensions=constraints.chord_extensions,
                borrowed_chord_probability=constraints.borrowed_chord_probability,
                harmonic_rhythm_bars=constraints.harmonic_rhythm_bars,
                velocity_floor=constraints.velocity_floor,
                velocity_ceiling=constraints.velocity_ceiling,
            )

        intent_goals: list[IntentGoalDict] = [
            IntentGoalDict(name=g, weight=1.0, constraint_type="soft")
            for g in musical_goals
        ]
        trace_id: str = ctx.get("trace_id") or str(uuid.uuid4())
        intent_hash = _build_intent_hash(ev_dict, rp_dict, gc_dict, musical_goals)

        logger.info(
            f"⚠️ Storpheus SINGLE-instrument generate({instrument}): "
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
            composition_id=ctx.get("composition_id"),
            emotion_vector=ev_dict,
            role_profile_summary=rp_dict,
            generation_constraints=gc_dict if gc_dict else None,
            intent_goals=intent_goals if intent_goals else None,
            seed=ctx.get("seed"),
            trace_id=trace_id,
            intent_hash=intent_hash,
        )

        if result.get("success"):
            meta: GenerationMetadata = {}
            _meta_raw = result.get("metadata")
            if isinstance(_meta_raw, dict):
                meta["storpheus_metadata"] = _meta_raw
            meta["trace_id"] = trace_id
            meta["intent_hash"] = intent_hash

            mvp_notes = result.get("notes")
            if mvp_notes:
                logger.info(f"✅ Storpheus: {len(mvp_notes)} notes (direct)")
                return GenerationResult(
                    success=True,
                    notes=[_normalize_note_keys(n) for n in mvp_notes],
                    backend_used=self.backend_type,
                    metadata=meta,
                )

            tool_calls = result.get("tool_calls", [])
            parsed = normalize_storpheus_tool_calls(tool_calls)
            notes: list[NoteDict] = [_normalize_note_keys(n) for n in parsed["notes"]]
            cc_events: list[CCEventDict] = parsed["cc_events"]
            pitch_bends: list[PitchBendDict] = parsed["pitch_bends"]
            aftertouch: list[AftertouchDict] = parsed["aftertouch"]

            target_beats = bars * 4
            if ENABLE_BEAT_RESCALING:
                _rescale_beats(notes, cc_events, pitch_bends, aftertouch, target_beats, bars=bars)
            else:
                logger.debug("Beat rescaling disabled (ENABLE_BEAT_RESCALING=false)")

            if notes:
                logger.info(
                    f"Storpheus generated {len(notes)} notes, "
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
                logger.warning("Storpheus returned success but no notes found in tool_calls")
                return GenerationResult(
                    success=False,
                    notes=[],
                    backend_used=self.backend_type,
                    metadata=meta,
                    error="No notes found in Storpheus response",
                )
        else:
            return GenerationResult(
                success=False,
                notes=[],
                backend_used=self.backend_type,
                metadata={},
                error=result.get("error", "Storpheus generation failed"),
            )

    async def generate_unified(
        self,
        instruments: list[str],
        style: str,
        tempo: int,
        bars: int,
        key: str | None = None,
        context: GenerationContext | None = None,
    ) -> GenerationResult:
        """Generate all instruments together — coherent multi-instrument output.

        Sends every instrument in a single Storpheus call with unified_output=True.
        The response includes channel_notes keyed by instrument label (bass, keys,
        drums, melody, etc.) so the caller can distribute to tracks.
        """
        ctx = context or {}
        emotion_vector: "EmotionVector" | None = ctx.get("emotion_vector")
        quality_preset: str = ctx.get("quality_preset", "quality")

        ev_dict: dict[str, float] | None = None
        rp_dict: dict[str, float] | None = None
        gc_dict: GenerationConstraintsDict | None = None
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

        from maestro.data.role_profiles import get_role_profile
        for inst in instruments:
            rp = get_role_profile(inst)
            if rp is not None and rp_dict is None:
                rp_dict = rp.to_summary_dict()

        if emotion_vector is not None:
            from maestro.core.emotion_vector import emotion_to_constraints
            constraints = emotion_to_constraints(emotion_vector)
            gc_dict = GenerationConstraintsDict(
                drum_density=constraints.drum_density,
                subdivision=constraints.subdivision,
                swing_amount=constraints.swing_amount,
                register_center=constraints.register_center,
                register_spread=constraints.register_spread,
                rest_density=constraints.rest_density,
                leap_probability=constraints.leap_probability,
                chord_extensions=constraints.chord_extensions,
                borrowed_chord_probability=constraints.borrowed_chord_probability,
                harmonic_rhythm_bars=constraints.harmonic_rhythm_bars,
                velocity_floor=constraints.velocity_floor,
                velocity_ceiling=constraints.velocity_ceiling,
            )

        intent_goals: list[IntentGoalDict] = [
            IntentGoalDict(name=g, weight=1.0, constraint_type="soft")
            for g in musical_goals
        ]
        trace_id: str = ctx.get("trace_id") or str(uuid.uuid4())
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
            composition_id=ctx.get("composition_id"),
            emotion_vector=ev_dict,
            role_profile_summary=rp_dict,
            generation_constraints=gc_dict if gc_dict else None,
            intent_goals=intent_goals if intent_goals else None,
            seed=ctx.get("seed"),
            trace_id=trace_id,
            intent_hash=intent_hash,
            add_outro=ctx.get("add_outro", False),
            unified_output=True,
        )

        if result.get("success"):
            meta: GenerationMetadata = {}
            _meta_raw = result.get("metadata")
            if isinstance(_meta_raw, dict):
                meta["storpheus_metadata"] = _meta_raw
            meta["trace_id"] = trace_id
            meta["intent_hash"] = intent_hash
            meta["unified_instruments"] = instruments

            mvp_notes = result.get("notes", [])
            channel_notes = result.get("channel_notes")

            if mvp_notes or channel_notes:
                _str_channel_notes: dict[str, list[NoteDict]] | None = None
                if isinstance(channel_notes, dict):
                    _str_channel_notes = {
                        str(ch): [_normalize_note_keys(n) for n in notes]
                        for ch, notes in channel_notes.items()
                    }
                logger.info(
                    f"✅ Unified: {len(mvp_notes or [])} flat notes, "
                    f"channels={list(_str_channel_notes.keys()) if _str_channel_notes else 'none'}"
                )
                return GenerationResult(
                    success=True,
                    notes=[_normalize_note_keys(n) for n in (mvp_notes or [])],
                    backend_used=self.backend_type,
                    metadata=meta,
                    channel_notes=_str_channel_notes,
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
                error=result.get("error", "Unified Storpheus generation failed"),
            )
