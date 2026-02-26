"""Multi-dimensional candidate scoring for rejection sampling.

Orpheus generates 10 parallel batches per call.  This module scores each
candidate against the requested intent and returns the best one.

Scoring dimensions:
    - Key compliance (pitch class distribution vs target key)
    - Density match (notes per bar vs requested density)
    - Register compliance (pitch centroid vs register_center/spread)
    - Velocity compliance (range vs floor/ceiling constraints)
    - Pattern diversity (entropy-based, penalizes monotonous output)
    - Instrument coverage (did we get notes on expected channels?)

Each dimension returns a 0..1 score.  The final score is a weighted sum.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from orpheus_types import OrpheusNoteDict, ScoringParams
from key_detection import (
    MAJOR_PROFILE,
    MINOR_PROFILE,
    detect_key_from_pitches,
    parse_key_string,
    key_to_semitones,
)

logger = logging.getLogger(__name__)

# Default weights — tunable via environment or A/B tests
_DEFAULT_WEIGHTS: dict[str, float] = {
    "key_compliance": 0.25,
    "density": 0.15,
    "register": 0.15,
    "velocity": 0.10,
    "diversity": 0.15,
    "coverage": 0.10,
    "silence": 0.10,
}


@dataclass
class CandidateScore:
    """Detailed scoring breakdown for a single generated candidate."""
    batch_index: int
    total_score: float = 0.0
    note_count: int = 0
    dimensions: dict[str, float] = field(default_factory=dict)
    detected_key: str | None = None


def _key_compliance(
    pitches: list[int],
    target_key: str | None,
) -> float:
    """Score how well the pitch distribution matches the target key.

    Returns 1.0 for perfect match, 0.0 for worst mismatch.
    Falls back to 0.5 if no target key or too few pitches.
    """
    if not target_key or len(pitches) < 8:
        return 0.5

    parsed = parse_key_string(target_key)
    if parsed is None:
        return 0.5

    target_tonic, target_mode = parsed
    target_root = key_to_semitones(target_tonic, target_mode)
    profile = MAJOR_PROFILE if target_mode == "major" else MINOR_PROFILE

    pc_counts = [0.0] * 12
    for p in pitches:
        pc_counts[p % 12] += 1.0

    rotated = pc_counts[-target_root:] + pc_counts[:-target_root] if target_root > 0 else pc_counts

    n = 12
    mean_x = sum(rotated) / n
    mean_y = sum(profile) / n
    dx = [xi - mean_x for xi in rotated]
    dy = [yi - mean_y for yi in profile]
    num = sum(a * b for a, b in zip(dx, dy))
    den_x = math.sqrt(sum(a * a for a in dx))
    den_y = math.sqrt(sum(b * b for b in dy))
    if den_x == 0 or den_y == 0:
        return 0.5

    corr = num / (den_x * den_y)
    return max(0.0, (corr + 1.0) / 2.0)


_ORPHEUS_TYPICAL_NOTES_PER_BAR = 50.0


def _density_match(
    notes: list[OrpheusNoteDict],
    bars: int,
    target_density: float | None,
) -> float:
    """Score how well note density matches the target.

    target_density is notes_per_bar.  Orpheus generates polyphonic MIDI
    with many simultaneous voices, so typical output is 30-150 notes/bar.
    The default (when no target is provided) is 50 notes/bar — the
    empirical midpoint of healthy Orpheus output.
    """
    if bars <= 0:
        return 0.5

    actual_npb = len(notes) / max(bars, 1)

    if target_density is None or target_density <= 0:
        ideal = _ORPHEUS_TYPICAL_NOTES_PER_BAR
    else:
        ideal = target_density

    ratio = actual_npb / ideal
    if 0.5 <= ratio <= 2.0:
        return 1.0 - abs(ratio - 1.0) * 0.5
    return max(0.0, 1.0 - abs(ratio - 1.0) * 0.3)


def _register_compliance(
    pitches: list[int],
    register_center: int | None,
    register_spread: int | None,
) -> float:
    """Score pitch distribution against register constraints."""
    if not pitches:
        return 0.0

    center = register_center or 60
    spread = register_spread or 24

    mean_pitch = sum(pitches) / len(pitches)
    center_error = abs(mean_pitch - center) / max(spread, 1)

    low = center - spread
    high = center + spread
    out_of_range = sum(1 for p in pitches if p < low or p > high)
    range_score = 1.0 - (out_of_range / len(pitches))

    center_score = max(0.0, 1.0 - center_error * 0.5)
    return center_score * 0.5 + range_score * 0.5


def _velocity_compliance(
    notes: list[OrpheusNoteDict],
    velocity_floor: int | None,
    velocity_ceiling: int | None,
) -> float:
    """Score velocity range against constraints."""
    if not notes:
        return 0.0

    velocities = [n.get("velocity", 80) for n in notes]
    floor_v = velocity_floor or 0
    ceil_v = velocity_ceiling or 127

    in_range = sum(1 for v in velocities if floor_v <= v <= ceil_v)
    return in_range / len(velocities)


def _pattern_diversity(notes: list[OrpheusNoteDict]) -> float:
    """Score melodic diversity using 2-note pattern entropy.

    Higher diversity → higher score.  Penalizes both total randomness
    (too many unique patterns) and total repetition (single pattern).
    """
    if len(notes) < 4:
        return 0.5

    patterns: dict[tuple[int, int], int] = {}
    for i in range(len(notes) - 1):
        p = (notes[i].get("pitch", 0), notes[i + 1].get("pitch", 0))
        patterns[p] = patterns.get(p, 0) + 1

    total = sum(patterns.values())
    if total == 0:
        return 0.5

    entropy = 0.0
    for count in patterns.values():
        prob = count / total
        if prob > 0:
            entropy -= prob * math.log2(prob)

    max_entropy = math.log2(max(len(patterns), 1)) if patterns else 1.0
    if max_entropy == 0:
        return 0.5

    normalised = entropy / max_entropy
    # Sweet spot: 0.4-0.8 normalized entropy
    if 0.4 <= normalised <= 0.8:
        return 1.0
    elif normalised < 0.4:
        return 0.5 + normalised
    else:
        return max(0.5, 1.0 - (normalised - 0.8))


def _coverage_score(
    channel_notes: dict[int, list[OrpheusNoteDict]],
    expected_channels: int,
) -> float:
    """Score whether output has notes on expected number of channels."""
    if expected_channels <= 0:
        return 1.0

    active = sum(1 for ch_notes in channel_notes.values() if len(ch_notes) > 0)
    return min(1.0, active / expected_channels)


def _silence_score(notes: list[OrpheusNoteDict], bars: int) -> float:
    """Score based on fraction of bars that contain at least one note."""
    if bars <= 0 or not notes:
        return 0.0

    bar_counts = [0] * bars
    for n in notes:
        b = int(n.get("start_beat", 0.0) / 4)
        if 0 <= b < bars:
            bar_counts[b] += 1

    active = sum(1 for c in bar_counts if c > 0)
    return active / bars


def score_candidate(
    notes: list[OrpheusNoteDict],
    channel_notes: dict[int, list[OrpheusNoteDict]],
    batch_index: int,
    params: ScoringParams,
    weights: dict[str, float] | None = None,
) -> CandidateScore:
    """Score a single generation candidate across all dimensions.

    Returns a ``CandidateScore`` with per-dimension breakdowns and a
    weighted total score in [0, 1].
    """
    w = weights or _DEFAULT_WEIGHTS
    result = CandidateScore(batch_index=batch_index, note_count=len(notes))

    pitches = [n["pitch"] for n in notes]

    dims: dict[str, float] = {}
    dims["key_compliance"] = _key_compliance(pitches, params.target_key)
    dims["density"] = _density_match(notes, params.bars, params.target_density)
    dims["register"] = _register_compliance(pitches, params.register_center, params.register_spread)
    dims["velocity"] = _velocity_compliance(notes, params.velocity_floor, params.velocity_ceiling)
    dims["diversity"] = _pattern_diversity(notes)
    dims["coverage"] = _coverage_score(channel_notes, params.expected_channels)
    dims["silence"] = _silence_score(notes, params.bars)

    total = 0.0
    weight_sum = 0.0
    for dim_name, dim_score in dims.items():
        w_val = w.get(dim_name, 0.0)
        total += dim_score * w_val
        weight_sum += w_val

    result.total_score = round(total / max(weight_sum, 0.01), 4)
    result.dimensions = {k: round(v, 4) for k, v in dims.items()}

    # Detect key for observability
    if pitches:
        key_result = detect_key_from_pitches(pitches)
        if key_result:
            result.detected_key = f"{key_result[0]} {key_result[1]}"

    return result


def select_best_candidate(
    candidates: list[CandidateScore],
) -> CandidateScore:
    """Pick the highest-scoring candidate from a list.

    Returns the best ``CandidateScore``.  In case of ties, prefers
    the candidate with more notes (less likely to be degenerate).
    """
    if not candidates:
        raise ValueError("No candidates to select from")

    return max(
        candidates,
        key=lambda c: (c.total_score, c.note_count),
    )
