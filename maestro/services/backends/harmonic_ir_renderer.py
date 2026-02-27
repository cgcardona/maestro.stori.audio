"""
Harmonic Spec IR → MIDI chord notes renderer.

Renders HarmonicSpec + GlobalSpec to chord voicings (root, third, fifth, seventh)
at chord_schedule bar positions. See docs/MIDI_SPEC_IR_SCHEMA.md.
"""
from __future__ import annotations

import logging
import random

from maestro.contracts.json_types import NoteDict
from maestro.core.music_spec_ir import HarmonicSpec, GlobalSpec, ChordScheduleEntry
from maestro.core.chord_utils import chord_to_midi_voicing

logger = logging.getLogger(__name__)


def _voicing_octaves(voicing: str) -> int:
    """Number of octaves to span for voicing (piano)."""
    if voicing == "root_third_seventh":
        return 2  # root in one octave, third/fifth/7 in next
    return 2


def render_harmonic_spec(
    harmonic_spec: HarmonicSpec,
    global_spec: GlobalSpec,
    *,
    chord_rhythm_beats: float = 2.0,
    apply_humanize: bool = True,
) -> list[NoteDict]:
    """
    Render HarmonicSpec + GlobalSpec to MIDI note list (chord voicings).

    chord_schedule defines (bar, chord); each chord lasts until next schedule entry or end of bars.
    """
    notes: list[NoteDict] = []
    schedule = harmonic_spec.chord_schedule
    bars = global_spec.bars
    vel_lo, vel_hi = harmonic_spec.velocity_range
    rng = random.Random()

    num_voices = 4 if harmonic_spec.voicing == "root_third_seventh" else 3
    piano_octave = 4  # middle register for chords

    if harmonic_spec.chord_rhythm == "whole":
        chord_rhythm_beats = 4.0
    elif harmonic_spec.chord_rhythm == "half_note":
        chord_rhythm_beats = 2.0
    elif harmonic_spec.chord_rhythm == "quarter":
        chord_rhythm_beats = 1.0

    if not schedule:
        schedule = [ChordScheduleEntry(bar=0, chord="C")]

    for i, entry in enumerate(schedule):
        bar_start = entry.bar * 4.0
        next_bar = schedule[i + 1].bar * 4.0 if i + 1 < len(schedule) else bars * 4.0
        duration = min(chord_rhythm_beats * 2, next_bar - bar_start)  # hold until next chord or 2 units
        if duration <= 0:
            duration = 2.0

        midi_pitches = chord_to_midi_voicing(entry.chord, piano_octave, num_voices=num_voices)
        for p in midi_pitches:
            vel = rng.randint(vel_lo, vel_hi)
            notes.append({
                "pitch": p,
                "start_beat": bar_start,
                "duration_beats": duration,
                "velocity": vel,
            })

    if apply_humanize and global_spec.microtiming_jitter_ms:
        j_lo, j_hi = global_spec.microtiming_jitter_ms
        ms_per_beat = 60_000 / global_spec.tempo
        for n in notes:
            jitter_ms = rng.randint(j_lo, j_hi) if j_hi > j_lo else 0
            n["start_beat"] = round((n["start_beat"] + jitter_ms / ms_per_beat) * 4) / 4

    notes.sort(key=lambda x: (x["start_beat"], x["pitch"]))
    logger.info(f"Harmonic IR render: {len(notes)} chord notes")
    return notes
