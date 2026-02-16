"""
IR-aware repair engine for drum (and later bass/melody) outputs.

Applies repair_instructions from the critic while respecting IR:
salience caps, density targets, groove template. See MIDI_SPEC_IR_SCHEMA.md §7.2.
"""
import logging
import random
from typing import Any, Optional

from app.core.music_spec_ir import (
    DrumSpec,
    DEFAULT_SALIENCE_WEIGHT,
)

logger = logging.getLogger(__name__)

# GM drum pitch -> layer name (for salience when we don't have layer_of)
PITCH_TO_LAYER = {
    36: "core", 38: "core", 39: "core",
    42: "timekeepers", 44: "timekeepers", 46: "timekeepers",
    49: "cymbal_punctuation", 51: "cymbal_punctuation", 52: "cymbal_punctuation",
    37: "ghost_layer", 40: "ghost_layer", 41: "ghost_layer", 43: "ghost_layer",
    45: "ghost_layer", 47: "ghost_layer",
    48: "fills", 50: "fills",
    54: "ear_candy", 56: "ear_candy", 69: "ear_candy", 70: "ear_candy", 75: "ear_candy",
}


def _layer_for_note(note: dict) -> str:
    return PITCH_TO_LAYER.get(note.get("pitch", 0), "timekeepers")


def _salience_at_beat(notes: list[dict], beat: float, salience_weight: dict) -> float:
    total = 0.0
    for n in notes:
        start = n["start_beat"]
        dur = n.get("duration_beats", 0.25)
        if start <= beat < start + dur:
            total += salience_weight.get(_layer_for_note(n), 0.5)
    return total


def _can_add_at_beat(notes: list[dict], beat: float, layer: str, max_salience: float, salience_weight: dict) -> bool:
    current = _salience_at_beat(notes, beat, salience_weight)
    add = salience_weight.get(layer, 0.5)
    return current + add <= max_salience


def _apply_salience_cap(notes: list[dict], drum_spec: DrumSpec) -> list[dict]:
    """Drop lowest-salience notes at beats over max_salience_per_beat."""
    if not notes:
        return notes
    salience_weight = drum_spec.salience_weight
    max_sal = drum_spec.constraints.max_salience_per_beat
    beat_res = 0.25
    beats_to_check = set()
    for n in notes:
        start = n["start_beat"]
        dur = n.get("duration_beats", 0.25)
        b = start
        while b < start + dur:
            beats_to_check.add(round(b / beat_res) * beat_res)
            b += beat_res
    out = list(notes)
    while True:
        worst_beat = None
        worst_excess = 0.0
        for beat in beats_to_check:
            s = _salience_at_beat(out, beat, salience_weight)
            if s > max_sal and s - max_sal > worst_excess:
                worst_excess = s - max_sal
                worst_beat = beat
        if worst_beat is None:
            break
        candidates = [
            n for n in out
            if n["start_beat"] <= worst_beat < n["start_beat"] + n.get("duration_beats", 0.25)
        ]
        if not candidates:
            break
        candidates.sort(key=lambda n: salience_weight.get(_layer_for_note(n), 0.5))
        out.remove(candidates[0])
    return out


def apply_drum_repair(
    notes: list[dict],
    drum_spec: DrumSpec,
    repair_instructions: list[str],
    *,
    rng: Optional[random.Random] = None,
) -> list[dict]:
    """
    Apply repair_instructions to drum notes in an IR-aware way.
    Respects max_salience_per_beat, fill_bars, groove. Returns repaired list (new list).
    """
    rng = rng or random.Random()
    out = [dict(n) for n in notes]
    salience_weight = drum_spec.salience_weight
    max_sal = drum_spec.constraints.max_salience_per_beat
    fill_bars = drum_spec.constraints.fill_bars

    for instr in repair_instructions:
        instr_lower = instr.lower()
        # instrument_coverage_low: add ghost_layer hits in bars 2 and 4
        if "instrument_coverage_low" in instr_lower and "ghost" in instr_lower:
            ghost_pitches = [37, 40, 41]  # rim, e.snare, low tom
            for bar in [2, 4]:
                bar_start = bar * 4.0
                for _ in range(rng.randint(2, 4)):
                    beat = bar_start + rng.choice([0.25, 0.5, 0.75, 1.25, 1.5, 1.75, 2.25, 2.5, 2.75, 3.25, 3.5, 3.75])
                    if _can_add_at_beat(out, beat, "ghost_layer", max_sal, salience_weight):
                        out.append({
                            "pitch": rng.choice(ghost_pitches),
                            "start_beat": beat,
                            "duration_beats": 0.25,
                            "velocity": rng.randint(40, 70),
                        })
            continue
        # no_fill: add fill in fill_bars using toms
        if "no_fill" in instr_lower:
            fill_pitches = [41, 43, 45, 47]
            for bar in fill_bars:
                if bar >= 16:
                    continue
                bar_start = bar * 4.0
                for i in range(rng.randint(4, 8)):
                    beat = bar_start + 0.25 * (i + rng.randint(0, 2))
                    if beat >= bar_start + 4:
                        break
                    if _can_add_at_beat(out, beat, "fills", max_sal, salience_weight):
                        out.append({
                            "pitch": rng.choice(fill_pitches),
                            "start_beat": round(beat * 4) / 4,
                            "duration_beats": 0.25,
                            "velocity": rng.randint(70, 95),
                        })
            continue
        # hats_repetitive: add open hat (46) on beat 4 of bar 2 and 4
        if "hats_repetitive" in instr_lower or "open hat" in instr_lower:
            for bar in [2, 4]:
                beat = bar * 4.0 + 3.0  # beat 4 of bar
                if _can_add_at_beat(out, beat, "timekeepers", max_sal, salience_weight):
                    out.append({
                        "pitch": 46,
                        "start_beat": beat,
                        "duration_beats": 0.5,
                        "velocity": rng.randint(70, 90),
                    })
            continue
        # velocity_flat: accent beat 1 and 3, reduce on upbeats
        if "velocity_flat" in instr_lower:
            for n in out:
                b = n["start_beat"]
                beat_in_bar = (b % 4)
                if abs(beat_in_bar - 0.0) < 0.2 or abs(beat_in_bar - 2.0) < 0.2:
                    n["velocity"] = min(127, int(n.get("velocity", 80) * 1.15))
                elif 0.2 < beat_in_bar < 1.8 and (beat_in_bar * 4) % 4 != 0:
                    n["velocity"] = max(1, int(n.get("velocity", 80) * 0.85))
            continue

    out = _apply_salience_cap(out, drum_spec)
    out.sort(key=lambda n: (n["start_beat"], n["pitch"]))
    logger.info(f"Drum repair: {len(notes)} -> {len(out)} notes, applied {len(repair_instructions)} instructions")
    return out


def repair_drum_if_needed(
    notes: list[dict],
    drum_spec: DrumSpec,
    score: float,
    repair_instructions: list[str],
    accept_threshold: float = 0.6,
) -> tuple[list[dict], bool]:
    """
    If score < accept_threshold and we have repair_instructions, apply repair and return (repaired_notes, True).
    Else return (original_notes, False).
    """
    if score >= accept_threshold or not repair_instructions:
        return notes, False
    repaired = apply_drum_repair(notes, drum_spec, repair_instructions)
    return repaired, True