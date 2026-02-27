"""
Melody Spec IR → MIDI melody notes renderer.

Renders MelodySpec + GlobalSpec + HarmonicSpec; uses chord_schedule for
resolution on chord boundaries and tensions near change points.
See docs/MIDI_SPEC_IR_SCHEMA.md.
"""
from __future__ import annotations

import logging
import random
from maestro.contracts.json_types import NoteDict
from maestro.core.music_spec_ir import MelodySpec, GlobalSpec, HarmonicSpec, ChordScheduleEntry
from maestro.core.chord_utils import chord_root_pitch_class, chord_to_scale_degrees

logger = logging.getLogger(__name__)

# Natural minor scale degrees (semitones above root): 0,2,3,5,7,8,10
MINOR_SCALE = [0, 2, 3, 5, 7, 8, 10]
MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]


def _chord_at_bar(schedule: list[ChordScheduleEntry], bar: int) -> str:
    if not schedule:
        return "C"
    best = schedule[0]
    for e in schedule:
        if e.bar <= bar:
            best = e
        else:
            break
    return best.chord


def _scale_tones(key: str, scale: str) -> list[int]:
    """Scale degrees in semitones (0-11) for key/scale."""
    is_minor = "m" in key.lower() or scale != "major"
    return MINOR_SCALE if is_minor else MAJOR_SCALE


def _contour_offsets(contour: str, num_notes: int) -> list[float]:
    """Return per-note pitch offset (0..1) for contour. arc = low-high-low."""
    if contour == "arc":
        half = num_notes / 2
        return [2 * (i / num_notes) * (1 - i / num_notes) for i in range(num_notes)]
    if contour == "ascending":
        return [i / max(1, num_notes - 1) for i in range(num_notes)]
    if contour == "descending":
        return [1 - i / max(1, num_notes - 1) for i in range(num_notes)]
    if contour == "wave":
        import math
        return [0.5 + 0.4 * math.sin(i * 0.5) for i in range(num_notes)]
    return [0.5] * num_notes


def render_melody_spec(
    melody_spec: MelodySpec,
    global_spec: GlobalSpec,
    harmonic_spec: HarmonicSpec,
    *,
    apply_humanize: bool = True,
) -> list[NoteDict]:
    """
    Render MelodySpec + GlobalSpec + HarmonicSpec to MIDI melody notes.

    Uses chord_schedule: resolve to chord tones on bar boundaries; scale_lock for other notes.
    """
    notes: list[NoteDict] = []
    schedule = harmonic_spec.chord_schedule
    bars = global_spec.bars
    key = global_spec.key
    scale = global_spec.scale
    rng = random.Random()

    scale_degrees = _scale_tones(key, scale)
    # Melody register: mid_high = octave 5-6
    base_octave = 5 if melody_spec.register in ("mid_high", "high") else 4
    pc = chord_root_pitch_class(key.replace("m", "").replace("M", "") if key else "C")
    base_midi = base_octave * 12 + pc

    num_notes_target = int(bars * 4 * (1 - melody_spec.rest_density))  # 4 beats per bar, rest_density = rest fraction
    num_notes_target = max(4, num_notes_target)
    contour_off = _contour_offsets(melody_spec.contour, num_notes_target)

    beat = 0.0
    for i in range(num_notes_target):
        bar_index = int(beat // 4)
        if bar_index >= bars:
            break
        chord = _chord_at_bar(schedule, bar_index)
        chord_pc = chord_root_pitch_class(chord)
        chord_degrees = chord_to_scale_degrees(chord, 3)  # root, third, fifth

        # On chord boundaries (bar start), resolve to chord tone
        bar_start_beat = bar_index * 4.0
        is_boundary = abs(beat - bar_start_beat) < 0.25
        if melody_spec.scale_lock and is_boundary:
            degree = rng.choice(chord_degrees)
            pitch = base_octave * 12 + chord_pc + degree
        else:
            degree = rng.choice(scale_degrees)
            contour_idx = min(i, len(contour_off) - 1)
            octave_shift = int(contour_off[contour_idx] * 2) - 1  # -1, 0, or +1 octave
            pitch = base_midi + degree + octave_shift * 12

        pitch = max(48, min(84, pitch))  # clamp to melody range
        vel = rng.randint(80, 110)
        dur = 0.25 + rng.random() * 0.5  # 8th to quarter
        notes.append({
            "pitch": int(pitch),
            "start_beat": round(beat * 4) / 4,
            "duration_beats": round(dur * 4) / 4,
            "velocity": vel,
        })
        beat += 0.5 + rng.random() * 0.5  # advance 0.5–1 beat

    if apply_humanize and global_spec.microtiming_jitter_ms:
        j_lo, j_hi = global_spec.microtiming_jitter_ms
        ms_per_beat = 60_000 / global_spec.tempo
        for n in notes:
            jitter_ms = rng.randint(j_lo, j_hi) if j_hi > j_lo else 0
            n["start_beat"] = round((n["start_beat"] + jitter_ms / ms_per_beat) * 4) / 4

    notes.sort(key=lambda x: (x["start_beat"], x["pitch"]))
    logger.info(f"Melody IR render: {len(notes)} notes")
    return notes
