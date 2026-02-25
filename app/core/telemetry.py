"""Deterministic musical telemetry derived from generated MIDI data.

All computations are pure math — no LLM calls, no external dependencies
beyond stdlib.  Typical execution time is <1ms for realistic section sizes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SectionTelemetry:
    """Immutable telemetry snapshot for one section of one instrument.

    Computed once after generation completes, never mutated.
    """

    section_name: str
    instrument: str
    tempo: float
    energy_level: float
    density_score: float
    groove_vector: tuple[float, ...]
    kick_pattern_hash: str
    rhythmic_complexity: float
    velocity_mean: float
    velocity_variance: float


def _note_start(n: dict[str, Any]) -> float:
    return float(n.get("start_beat", n.get("startBeat", 0)))


def _note_velocity(n: dict[str, Any]) -> int:
    return int(n.get("velocity", 80))


def _note_pitch(n: dict[str, Any]) -> int:
    return int(n.get("pitch", 0))


def compute_section_telemetry(
    notes: list[dict[str, Any]],
    tempo: float,
    instrument: str,
    section_name: str,
    section_beats: float,
) -> SectionTelemetry:
    """Compute deterministic musical telemetry from generated MIDI notes.

    All values are derived from raw note data — no ML, no randomness.
    Designed to run in <2ms for typical section sizes (50-500 notes).
    """
    total_beats = max(section_beats, 1.0)
    n_notes = len(notes)

    # ── Density: notes per beat ──
    density = n_notes / total_beats

    # ── Velocity statistics ──
    if n_notes:
        velocities = [_note_velocity(n) for n in notes]
        vel_mean = sum(velocities) / n_notes
        vel_var = sum((v - vel_mean) ** 2 for v in velocities) / n_notes
    else:
        vel_mean = 0.0
        vel_var = 0.0

    # ── Energy: normalized product of velocity intensity and density ──
    # Velocity contributes 0-1 (divided by 127), density capped at 4 notes/beat
    energy = min(1.0, (vel_mean / 127.0) * min(density / 4.0, 1.0))

    # ── Groove vector: 16-bin histogram of note onset positions within beat ──
    # Bin 0 = downbeat, bin 4 = second 16th, etc.
    bins = [0.0] * 16
    for n in notes:
        offset = _note_start(n) % 1.0
        bin_idx = int(offset * 16) % 16
        bins[bin_idx] += 1
    bin_total = sum(bins) or 1.0
    groove_vector = tuple(b / bin_total for b in bins)

    # ── Kick pattern hash: fingerprint of kick drum positions ──
    # GM kick = pitch 35 (Acoustic Bass Drum) or 36 (Bass Drum 1)
    kick_positions = sorted(
        round(_note_start(n), 4)
        for n in notes
        if _note_pitch(n) in (35, 36)
    )
    if kick_positions:
        kick_hash = hashlib.md5(
            str(kick_positions).encode(), usedforsecurity=False
        ).hexdigest()[:8]
    else:
        kick_hash = ""

    # ── Rhythmic complexity: stddev of inter-onset intervals ──
    starts = sorted(_note_start(n) for n in notes)
    if len(starts) > 1:
        spacings = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
        spacing_mean = sum(spacings) / len(spacings)
        complexity = (
            sum((s - spacing_mean) ** 2 for s in spacings) / len(spacings)
        ) ** 0.5
    else:
        complexity = 0.0

    return SectionTelemetry(
        section_name=section_name,
        instrument=instrument,
        tempo=tempo,
        energy_level=round(energy, 4),
        density_score=round(density, 4),
        groove_vector=groove_vector,
        kick_pattern_hash=kick_hash,
        rhythmic_complexity=round(complexity, 4),
        velocity_mean=round(vel_mean, 2),
        velocity_variance=round(vel_var, 2),
    )
