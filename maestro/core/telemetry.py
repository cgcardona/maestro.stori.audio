"""Deterministic musical telemetry derived from generated MIDI data.

All computations are pure math — no LLM calls, no external dependencies
beyond stdlib.  Typical execution time is <1ms for realistic section sizes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SectionTelemetry:
    """Immutable telemetry snapshot for one generated section of one instrument.

    Computed once immediately after Orpheus generation completes, stored in
    ``SectionState``, and consumed by downstream agents (e.g. bass reads drum
    telemetry to lock its groove to the kick pattern).  All values are pure
    math — no LLM calls, no external I/O.

    Written via ``SectionState.store()``, read via ``SectionState.get()``.

    Attributes:
        section_name: Human-readable section label (e.g. ``"verse"``, ``"chorus"``).
        instrument: Instrument role that generated this section (e.g. ``"Drums"``).
        tempo: Project tempo in BPM at generation time (always a whole integer).
        energy_level: Normalised energy 0–1.  Derived from mean velocity × note
            density capped at 4 notes/beat.  High values → loud, dense sections.
        density_score: Notes per beat.  Typical ranges: drums 2–8, bass 1–4,
            pads 0.5–2.
        groove_vector: 16-element tuple of normalised onset counts per 16th-note
            bin within a beat.  Index 0 = downbeat, index 4 = second 16th, etc.
            Used by bass agents to lock their rhythm to the drum grid.
        kick_pattern_hash: MD5 fingerprint (first 8 hex chars) of the sorted kick
            drum onset positions (GM pitches 35/36).  Empty string when no kicks
            were generated.  Lets bass agents detect kick pattern changes between
            sections.
        rhythmic_complexity: Standard deviation of inter-onset intervals (in
            beats).  Low values → metronomic; high values → syncopated/polyrhythmic.
        velocity_mean: Mean MIDI velocity across all notes (0–127 scale).
        velocity_variance: Variance of MIDI velocity — high values indicate
            dynamic (humanised) playing; low values indicate mechanical patterns.
    """

    section_name: str
    instrument: str
    tempo: int
    energy_level: float
    density_score: float
    groove_vector: tuple[float, ...]
    kick_pattern_hash: str
    rhythmic_complexity: float
    velocity_mean: float
    velocity_variance: float


def _as_float(v: object, default: float = 0.0) -> float:
    """Coerce an object to float, falling back to *default*."""
    if isinstance(v, (int, float)):
        return float(v)
    return default


def _as_int(v: object, default: int = 0) -> int:
    """Coerce an object to int, falling back to *default*."""
    if isinstance(v, (int, float)):
        return int(v)
    return default


def _note_start(n: Mapping[str, object]) -> float:
    """Return the start beat of *n*, accepting both snake_case and camelCase keys."""
    return _as_float(n.get("start_beat") or n.get("startBeat"))


def _note_velocity(n: Mapping[str, object]) -> int:
    """Return the MIDI velocity of *n*, defaulting to 80 when absent."""
    return _as_int(n.get("velocity"), 80)


def _note_pitch(n: Mapping[str, object]) -> int:
    """Return the MIDI pitch of *n* (0–127), defaulting to 0 when absent."""
    return _as_int(n.get("pitch"))


def compute_section_telemetry(
    notes: Sequence[Mapping[str, object]],
    tempo: int,
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
