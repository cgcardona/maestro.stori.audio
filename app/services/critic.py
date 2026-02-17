"""
Critic: Layer-aware scoring for Music Spec IR outputs.

Scores candidate outputs (drums, bass, melody, chords) with metrics:
- Drums: groove pocket, hat articulation, fill localization, ghost plausibility
- Bass: kick-bass alignment with anticipation awareness
- Melody: phrase structure, motif reuse
- Chords: voicing quality
"""
import logging
import math
from typing import Any, Optional
from collections import Counter
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Thresholds and Weights
# -----------------------------------------------------------------------------

# Drum rubric weights (sum to 1.0) - Updated for groove focus
DRUM_WEIGHTS = {
    "groove_pocket": 0.20,        # NEW: timing consistency per role
    "hat_articulation": 0.18,     # NEW: closed/open variety, velocity arcs
    "fill_localization": 0.15,    # UPGRADED: fills in correct bars only
    "ghost_plausibility": 0.12,   # NEW: ghosts near backbeats, low velocity
    "layer_balance": 0.12,        # Uses actual layer labels now
    "repetition_structure": 0.10, # UPGRADED: A/A' patterns ok, not identical
    "velocity_dynamics": 0.08,    # Accent curves, not just entropy
    "syncopation": 0.05,
}

ACCEPT_THRESHOLD_DRUM = 0.65
ACCEPT_THRESHOLD_DRUM_QUALITY = 0.75  # For quality preset
ACCEPT_THRESHOLD_BASS = 0.55
ACCEPT_THRESHOLD_BASS_QUALITY = 0.70
ACCEPT_THRESHOLD_MELODY = 0.5
ACCEPT_THRESHOLD_CHORDS = 0.5


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

def _distinct_pitches(notes: list[dict]) -> int:
    return len(set(n.get("pitch", 0) for n in notes))


def _velocity_entropy_normalized(notes: list[dict]) -> float:
    """Normalized velocity entropy: 0 = all same, 1 = well spread."""
    if not notes:
        return 0.0
    vels = [n.get("velocity", 80) for n in notes]
    bins = Counter(min(16, v // 8) for v in vels)
    n = len(vels)
    if n <= 1:
        return 0.0
    ent = -sum((c / n) * math.log2(c / n) for c in bins.values())
    return min(1.0, ent / 4.0)  # 4 bits max for 16 bins


def _offbeat_ratio(notes: list[dict], beat_resolution: float = 0.25) -> float:
    """Fraction of onsets that are offbeat (not on quarter)."""
    if not notes:
        return 0.0
    off = sum(1 for n in notes if (n.get("start_beat", 0) * 4) % 4 != 0)
    return off / len(notes)


def _get_notes_by_layer(notes: list[dict], layer_map: Optional[dict] = None) -> dict:
    """Group notes by layer name."""
    by_layer: dict[str, list[dict]] = {}
    for i, n in enumerate(notes):
        layer = n.get("layer")
        if layer is None and layer_map:
            layer = layer_map.get(i, "unknown")
        if layer is None:
            layer = "unknown"
        if layer not in by_layer:
            by_layer[layer] = []
        by_layer[layer].append(n)
    return by_layer


# -----------------------------------------------------------------------------
# Groove Pocket Scoring
# -----------------------------------------------------------------------------

def _score_groove_pocket(
    notes: list[dict],
    layer_map: Optional[dict] = None,
    style: str = "trap",
) -> tuple[float, list[str]]:
    """
    Score timing consistency per instrument role.
    
    Good pocket means:
    - Kicks are relatively early/on-beat
    - Snares are relatively late (behind the beat)
    - Hats have consistent timing pattern
    - Ghosts are late
    
    Returns (score 0-1, repair messages)
    """
    repair = []
    by_layer = _get_notes_by_layer(notes, layer_map)
    
    if not notes:
        return 0.0, ["empty_drums"]
    
    scores = []
    
    # Core layer: check kick vs snare timing relationship
    core_notes = by_layer.get("core", [])
    if core_notes:
        kicks = [n for n in core_notes if n.get("pitch") in (35, 36)]
        snares = [n for n in core_notes if n.get("pitch") in (38, 39, 40)]
        
        if kicks and snares:
            # Calculate average timing offset from grid for each
            kick_offsets = [(n["start_beat"] % 0.25) for n in kicks]
            snare_offsets = [(n["start_beat"] % 0.25) for n in snares]
            
            avg_kick = sum(kick_offsets) / len(kick_offsets) if kick_offsets else 0
            avg_snare = sum(snare_offsets) / len(snare_offsets) if snare_offsets else 0
            
            # Snares should be later than kicks (positive offset difference)
            if avg_snare >= avg_kick:
                scores.append(1.0)
            else:
                scores.append(0.6)
                repair.append("pocket_inverted: snare should be later than kick")
        else:
            scores.append(0.7)  # Partial core
    
    # Timekeepers: hats should have consistent timing pattern
    hat_notes = by_layer.get("timekeepers", [])
    if hat_notes:
        offsets = [(n["start_beat"] % 0.5) for n in hat_notes]
        if offsets:
            variance = sum((o - sum(offsets)/len(offsets))**2 for o in offsets) / len(offsets)
            # Low variance = consistent timing = good
            hat_score = max(0.5, 1.0 - variance * 10)
            scores.append(hat_score)
    
    # Ghost layer: should be late (positive offset)
    ghost_notes = by_layer.get("ghost_layer", [])
    if ghost_notes:
        ghost_offsets = [(n["start_beat"] % 0.25) for n in ghost_notes]
        avg_ghost = sum(ghost_offsets) / len(ghost_offsets) if ghost_offsets else 0
        # Ghosts should be late (offset > 0.05)
        if avg_ghost > 0.03:
            scores.append(1.0)
        else:
            scores.append(0.7)
    
    return sum(scores) / max(1, len(scores)) if scores else 0.7, repair


# -----------------------------------------------------------------------------
# Hat Articulation Scoring
# -----------------------------------------------------------------------------

def _score_hat_articulation(
    notes: list[dict],
    layer_map: Optional[dict] = None,
    bars: int = 16,
) -> tuple[float, list[str]]:
    """
    Score hi-hat articulation variety.
    
    Good hat articulation includes:
    - Mix of closed (42) and open (46) hats
    - Velocity arcs within bars (louder at ends)
    - Some variation bar to bar (not identical)
    
    Returns (score 0-1, repair messages)
    """
    repair = []
    by_layer = _get_notes_by_layer(notes, layer_map)
    hat_notes = by_layer.get("timekeepers", [])
    
    if not hat_notes:
        return 0.5, ["no_hats: add hi-hat layer"]
    
    scores = []
    
    # 1. Closed/open ratio: should have some open hats but mostly closed
    closed = sum(1 for n in hat_notes if n.get("pitch") == 42)
    open_hat = sum(1 for n in hat_notes if n.get("pitch") == 46)
    pedal = sum(1 for n in hat_notes if n.get("pitch") == 44)
    
    total_hats = len(hat_notes)
    open_ratio = (open_hat + pedal * 0.5) / total_hats if total_hats > 0 else 0
    
    # Ideal: 5-20% open/pedal
    if 0.03 <= open_ratio <= 0.25:
        scores.append(1.0)
    elif open_ratio < 0.03:
        scores.append(0.6)
        repair.append("hats_monotone: add occasional open hat on beat 4")
    else:
        scores.append(0.7)  # Too many open hats
    
    # 2. Velocity variance within bars (should have dynamic range)
    bar_vel_ranges = []
    for b in range(bars):
        bar_start = b * 4.0
        bar_end = bar_start + 4.0
        bar_hats = [n for n in hat_notes if bar_start <= n.get("start_beat", 0) < bar_end]
        if len(bar_hats) >= 4:
            vels = [n.get("velocity", 80) for n in bar_hats]
            bar_vel_ranges.append(max(vels) - min(vels))
    
    if bar_vel_ranges:
        avg_range = sum(bar_vel_ranges) / len(bar_vel_ranges)
        # Good range is 15-40 velocity units
        if avg_range >= 12:
            scores.append(1.0)
        elif avg_range >= 6:
            scores.append(0.7)
        else:
            scores.append(0.4)
            repair.append("hats_flat_velocity: add velocity arc within bars")
    
    # 3. Bar-to-bar variation (not identical patterns)
    bar_patterns = []
    for b in range(bars):
        bar_start = b * 4.0
        bar_end = bar_start + 4.0
        bar_hats = [n for n in hat_notes if bar_start <= n.get("start_beat", 0) < bar_end]
        pattern = tuple(sorted(round((n["start_beat"] - bar_start) * 4) / 4 for n in bar_hats))
        bar_patterns.append(pattern)
    
    if len(bar_patterns) >= 2:
        unique_patterns = len(set(bar_patterns))
        variation_ratio = unique_patterns / len(bar_patterns)
        # Some repetition is good (music), but not 100% identical
        if 0.25 <= variation_ratio <= 0.8:
            scores.append(1.0)
        elif variation_ratio < 0.25:
            scores.append(0.5)
            repair.append("hats_repetitive: add variation every 2-4 bars")
        else:
            scores.append(0.7)  # Too random
    
    return sum(scores) / max(1, len(scores)) if scores else 0.6, repair


# -----------------------------------------------------------------------------
# Fill Localization Scoring
# -----------------------------------------------------------------------------

def _score_fill_localization(
    notes: list[dict],
    layer_map: Optional[dict] = None,
    fill_bars: Optional[list[int]] = None,
    bars: int = 16,
) -> tuple[float, list[str]]:
    """
    Score whether fills occur in the correct bars.
    
    Good fill localization:
    - Most fill-layer notes are in fill_bars
    - Fill bars have higher density than non-fill bars
    - Some variation between fill phrases
    
    Returns (score 0-1, repair messages)
    """
    repair = []
    fill_bars = fill_bars or [b for b in range(3, bars, 4)]  # Default: bar 3, 7, 11, 15
    
    by_layer = _get_notes_by_layer(notes, layer_map)
    fill_notes = by_layer.get("fills", [])
    
    if not fill_notes:
        # No fills might be ok for some styles
        return 0.6, ["no_fills: consider adding fill in turnaround bars"]
    
    scores = []
    
    # 1. What percentage of fill notes are in fill bars?
    in_fill_bars = 0
    for n in fill_notes:
        bar_idx = int(n.get("start_beat", 0) // 4)
        if bar_idx in fill_bars:
            in_fill_bars += 1
    
    localization_ratio = in_fill_bars / len(fill_notes) if fill_notes else 0
    
    if localization_ratio >= 0.7:
        scores.append(1.0)
    elif localization_ratio >= 0.4:
        scores.append(0.6)
        repair.append("fills_scattered: concentrate fills in phrase-end bars")
    else:
        scores.append(0.3)
        repair.append("fills_misplaced: fills should be in bars " + str(fill_bars))
    
    # 2. Fill bars should have more activity than regular bars
    fill_bar_hits = sum(1 for n in notes if int(n.get("start_beat", 0) // 4) in fill_bars)
    non_fill_bars = [b for b in range(bars) if b not in fill_bars]
    non_fill_hits = sum(1 for n in notes if int(n.get("start_beat", 0) // 4) in non_fill_bars)
    
    if len(fill_bars) > 0 and len(non_fill_bars) > 0:
        fill_density = fill_bar_hits / len(fill_bars)
        non_fill_density = non_fill_hits / len(non_fill_bars)
        
        if fill_density > non_fill_density:
            scores.append(1.0)
        else:
            scores.append(0.6)
    
    return sum(scores) / max(1, len(scores)) if scores else 0.6, repair


# -----------------------------------------------------------------------------
# Ghost Plausibility Scoring
# -----------------------------------------------------------------------------

def _score_ghost_plausibility(
    notes: list[dict],
    layer_map: Optional[dict] = None,
) -> tuple[float, list[str]]:
    """
    Score ghost note placement and velocity.
    
    Good ghost notes:
    - Near backbeats (before/after beats 2 and 4)
    - Low velocity (< 70)
    - Don't conflict with main snare hits
    
    Returns (score 0-1, repair messages)
    """
    repair = []
    by_layer = _get_notes_by_layer(notes, layer_map)
    ghost_notes = by_layer.get("ghost_layer", [])
    
    if not ghost_notes:
        return 0.7, []  # Ghosts are optional
    
    scores = []
    
    # 1. Velocity check: ghosts should be quiet
    quiet_ghosts = sum(1 for n in ghost_notes if n.get("velocity", 80) < 75)
    quiet_ratio = quiet_ghosts / len(ghost_notes) if ghost_notes else 0
    
    if quiet_ratio >= 0.7:
        scores.append(1.0)
    elif quiet_ratio >= 0.4:
        scores.append(0.7)
    else:
        scores.append(0.4)
        repair.append("ghosts_too_loud: ghost velocity should be < 70")
    
    # 2. Position check: near backbeats (beats 1, 2, 3, 4 ± 0.5)
    near_backbeat = 0
    for n in ghost_notes:
        beat_in_bar = n.get("start_beat", 0) % 4
        # Check if within 0.5 beats of backbeat (1 or 3)
        if abs(beat_in_bar - 1.0) < 0.6 or abs(beat_in_bar - 3.0) < 0.6:
            near_backbeat += 1
        # Also ok if anticipating downbeat
        elif beat_in_bar > 3.5 or beat_in_bar < 0.5:
            near_backbeat += 1
    
    backbeat_ratio = near_backbeat / len(ghost_notes) if ghost_notes else 0
    if backbeat_ratio >= 0.5:
        scores.append(1.0)
    elif backbeat_ratio >= 0.3:
        scores.append(0.7)
    else:
        scores.append(0.5)
    
    return sum(scores) / max(1, len(scores)) if scores else 0.7, repair


# -----------------------------------------------------------------------------
# Layer Balance Scoring (now with actual layer data)
# -----------------------------------------------------------------------------

def _score_layer_balance(
    notes: list[dict],
    layer_map: Optional[dict] = None,
) -> tuple[float, list[str]]:
    """
    Score layer balance using actual layer labels.
    
    Good balance:
    - Core (kick/snare) present
    - Timekeepers (hats) present
    - At least one accent layer (fills, ghosts, cymbals)
    
    Returns (score 0-1, repair messages)
    """
    repair = []
    by_layer = _get_notes_by_layer(notes, layer_map)
    
    present_layers = set(by_layer.keys()) - {"unknown"}
    
    scores = []
    
    # Core is essential
    if "core" in present_layers:
        scores.append(1.0)
    else:
        scores.append(0.0)
        repair.append("no_core: add kick and snare")
    
    # Timekeepers are essential
    if "timekeepers" in present_layers:
        scores.append(1.0)
    else:
        scores.append(0.3)
        repair.append("no_hats: add hi-hat layer")
    
    # At least one accent layer
    accent_layers = {"fills", "ghost_layer", "cymbal_punctuation", "ear_candy"}
    if present_layers & accent_layers:
        scores.append(1.0)
    else:
        scores.append(0.5)
        repair.append("no_accents: add fills or ghost notes")
    
    # Bonus for variety
    layer_count = len(present_layers)
    if layer_count >= 4:
        scores.append(1.0)
    elif layer_count >= 3:
        scores.append(0.8)
    else:
        scores.append(0.6)
    
    return sum(scores) / max(1, len(scores)) if scores else 0.5, repair


# -----------------------------------------------------------------------------
# Repetition Structure Scoring
# -----------------------------------------------------------------------------

def _score_repetition_structure(
    notes: list[dict],
    bars: int = 16,
) -> tuple[float, list[str]]:
    """
    Score repetition structure (A/A' patterns ok, identical bars not ok).
    
    Good structure:
    - Some repetition for musicality (not random)
    - Variations between repeats (A → A')
    - Not every bar identical
    
    Returns (score 0-1, repair messages)
    """
    repair = []
    
    if not notes or bars < 2:
        return 0.5, []
    
    # Build bar rhythms (pitch-agnostic)
    bar_rhythms = []
    for b in range(bars):
        bar_start = b * 4.0
        bar_end = bar_start + 4.0
        onsets = tuple(sorted(
            round((n["start_beat"] - bar_start) * 4) / 4
            for n in notes
            if bar_start <= n.get("start_beat", 0) < bar_end
        ))
        bar_rhythms.append(onsets)
    
    # Count exact repeats
    exact_repeats = sum(1 for i in range(1, len(bar_rhythms)) if bar_rhythms[i] == bar_rhythms[i - 1])
    exact_repeat_ratio = exact_repeats / max(1, len(bar_rhythms) - 1)
    
    # Count similar patterns (edit distance <= 2)
    def pattern_distance(p1, p2):
        return len(set(p1) ^ set(p2))
    
    similar_pairs = 0
    for i in range(1, len(bar_rhythms)):
        if pattern_distance(bar_rhythms[i], bar_rhythms[i - 1]) <= 2:
            similar_pairs += 1
    similar_ratio = similar_pairs / max(1, len(bar_rhythms) - 1)
    
    # Scoring: some similarity is good, too much exact repetition is bad
    if exact_repeat_ratio < 0.3 and similar_ratio > 0.2:
        score = 1.0  # Good: varied but cohesive
    elif exact_repeat_ratio < 0.5:
        score = 0.8  # Acceptable
    elif exact_repeat_ratio < 0.7:
        score = 0.5
        repair.append("too_repetitive: add bar-to-bar variation")
    else:
        score = 0.3
        repair.append("monotonous: patterns too identical, add fills/variations")
    
    return score, repair


# -----------------------------------------------------------------------------
# Velocity Dynamics Scoring
# -----------------------------------------------------------------------------

def _score_velocity_dynamics(
    notes: list[dict],
    bars: int = 16,
) -> tuple[float, list[str]]:
    """
    Score velocity dynamics (accent curves, not just entropy).
    
    Good dynamics:
    - Backbeats (2, 4) accented
    - Downbeats (1, 3) present
    - Dynamic range over bars
    
    Returns (score 0-1, repair messages)
    """
    repair = []
    
    if not notes:
        return 0.0, ["no_notes"]
    
    scores = []
    
    # 1. Beat-position velocity correlation (backbeats should be louder)
    beat_vels: dict[int, list[int]] = {0: [], 1: [], 2: [], 3: []}
    for n in notes:
        beat_in_bar = int(n.get("start_beat", 0) % 4)
        beat_vels[beat_in_bar].append(n.get("velocity", 80))
    
    avg_vels = {b: sum(v) / len(v) if v else 80 for b, v in beat_vels.items()}
    
    # Backbeats (1, 3) should be >= other beats
    if avg_vels.get(1, 80) >= avg_vels.get(0, 80) * 0.95 and \
       avg_vels.get(3, 80) >= avg_vels.get(2, 80) * 0.95:
        scores.append(1.0)
    else:
        scores.append(0.7)
    
    # 2. Overall velocity spread
    all_vels = [n.get("velocity", 80) for n in notes]
    if all_vels:
        vel_range = max(all_vels) - min(all_vels)
        if vel_range >= 30:
            scores.append(1.0)
        elif vel_range >= 15:
            scores.append(0.7)
        else:
            scores.append(0.4)
            repair.append("velocity_flat: increase dynamic range")
    
    return sum(scores) / max(1, len(scores)) if scores else 0.5, repair


# -----------------------------------------------------------------------------
# Main Scoring Functions
# -----------------------------------------------------------------------------

def score_drum_notes(
    notes: list[dict],
    *,
    layer_map: Optional[dict] = None,
    fill_bars: Optional[list[int]] = None,
    bars: int = 16,
    style: str = "trap",
    max_salience_per_beat: float = 2.5,
    min_distinct: int = 8,
) -> tuple[float, list[str]]:
    """
    Score drum notes with Critic v2 (layer-aware, groove-aware).
    
    Returns (score 0–1, repair_instructions).
    
    Uses actual layer labels when available for better scoring of:
    - Groove pocket (timing per role)
    - Hat articulation (closed/open, velocity arcs)
    - Fill localization (fills in correct bars)
    - Ghost plausibility (near backbeats, low velocity)
    """
    fill_bars = fill_bars or [b for b in range(3, bars, 4)]
    all_repair: list[str] = []
    scores = {}
    
    # 1. Groove pocket
    pocket_score, pocket_repair = _score_groove_pocket(notes, layer_map, style)
    scores["groove_pocket"] = pocket_score
    all_repair.extend(pocket_repair)
    
    # 2. Hat articulation
    hat_score, hat_repair = _score_hat_articulation(notes, layer_map, bars)
    scores["hat_articulation"] = hat_score
    all_repair.extend(hat_repair)
    
    # 3. Fill localization
    fill_score, fill_repair = _score_fill_localization(notes, layer_map, fill_bars, bars)
    scores["fill_localization"] = fill_score
    all_repair.extend(fill_repair)
    
    # 4. Ghost plausibility
    ghost_score, ghost_repair = _score_ghost_plausibility(notes, layer_map)
    scores["ghost_plausibility"] = ghost_score
    all_repair.extend(ghost_repair)
    
    # 5. Layer balance
    balance_score, balance_repair = _score_layer_balance(notes, layer_map)
    scores["layer_balance"] = balance_score
    all_repair.extend(balance_repair)
    
    # 6. Repetition structure
    rep_score, rep_repair = _score_repetition_structure(notes, bars)
    scores["repetition_structure"] = rep_score
    all_repair.extend(rep_repair)
    
    # 7. Velocity dynamics
    vel_score, vel_repair = _score_velocity_dynamics(notes, bars)
    scores["velocity_dynamics"] = vel_score
    all_repair.extend(vel_repair)
    
    # 8. Syncopation
    offbeat = _offbeat_ratio(notes)
    scores["syncopation"] = min(1.0, offbeat * 2.0)
    
    # Calculate weighted total
    total = sum(DRUM_WEIGHTS.get(k, 0) * scores.get(k, 0.5) for k in DRUM_WEIGHTS)
    
    logger.debug(f"Critic scores: {scores}, total: {total:.3f}")
    return total, all_repair


def score_bass_notes(
    notes: list[dict],
    kick_beats: Optional[list[float]] = None,
    *,
    window_beats: float = 0.25,
    anticipation_allowed: bool = True,
) -> tuple[float, list[str]]:
    """
    Score bass notes with improved kick-bass alignment.
    
    Now considers:
    - Direct kick alignment
    - Anticipation (slightly before kick)
    - Note density appropriate for style
    """
    repair: list[str] = []
    if not notes:
        return 0.0, ["bass_empty: add bass notes"]

    scores = []
    
    if kick_beats:
        kick_set = set(kick_beats)
        aligned = 0
        anticipated = 0
        
        for n in notes:
            start = n.get("start_beat", 0)
            
            # Check direct alignment
            for k in kick_set:
                if abs(start - k) <= window_beats:
                    aligned += 1
                    break
            else:
                # Check anticipation (1/16 to 1/8 before kick)
                if anticipation_allowed:
                    for k in kick_set:
                        if 0.0625 <= k - start <= 0.25:
                            anticipated += 1
                            break
        
        alignment = aligned / len(notes)
        anticipation_ratio = anticipated / len(notes)
        
        # Combined score: alignment is primary, anticipation is bonus
        combined = alignment + anticipation_ratio * 0.3
        scores.append(min(1.0, combined))
        
        if alignment < 0.4:
            repair.append("kick_bass_alignment_low: align more bass onsets with kick")
    else:
        scores.append(0.7)  # no kick info: assume ok
    
    # Note density check
    bars = max(1, int(max(n.get("start_beat", 0) for n in notes) / 4) + 1)
    notes_per_bar = len(notes) / bars
    if 2 <= notes_per_bar <= 8:
        scores.append(1.0)
    elif notes_per_bar < 2:
        scores.append(0.5)
        repair.append("bass_sparse: add more bass notes")
    else:
        scores.append(0.6)  # Slightly too dense
    
    score = sum(scores) / len(scores) if scores else 0.5
    return score, repair


def score_melody_notes(notes: list[dict], *, min_notes: int = 8) -> tuple[float, list[str]]:
    """Score melody: phrase length, rest density proxy (note count), register."""
    repair: list[str] = []
    if len(notes) < min_notes:
        return 0.4, ["melody_sparse: add more melody notes or check rest_density"]
    score = min(1.0, len(notes) / 32.0)  # 32 notes in 8 bars = good
    return score, repair


def score_chord_notes(notes: list[dict], *, min_notes: int = 4) -> tuple[float, list[str]]:
    """Score chord voicings: completeness (multiple pitches per chord), rhythm."""
    repair: list[str] = []
    if len(notes) < min_notes:
        return 0.5, ["chords_sparse: add chord voicings"]
    score = min(1.0, len(notes) / 24.0)
    return score, repair


def accept_drum(score: float, quality_preset: str = "balanced") -> bool:
    """Check if drum score passes threshold for given quality preset."""
    threshold = ACCEPT_THRESHOLD_DRUM_QUALITY if quality_preset == "quality" else ACCEPT_THRESHOLD_DRUM
    return score >= threshold


def accept_bass(score: float, quality_preset: str = "balanced") -> bool:
    """Check if bass score passes threshold for given quality preset."""
    threshold = ACCEPT_THRESHOLD_BASS_QUALITY if quality_preset == "quality" else ACCEPT_THRESHOLD_BASS
    return score >= threshold


# -----------------------------------------------------------------------------
# Rejection Sampling Helper
# -----------------------------------------------------------------------------

@dataclass
class RejectionSamplingResult:
    """Result of rejection sampling loop."""
    best_result: Any
    best_score: float
    attempts: int
    accepted: bool
    all_scores: list[float]


def rejection_sample(
    generate_fn,
    scorer_fn,
    *,
    max_attempts: int = 6,
    accept_threshold: float = 0.75,
    early_stop_threshold: float = 0.85,
) -> RejectionSamplingResult:
    """
    Rejection sampling loop with early stopping.
    
    Args:
        generate_fn: Callable that returns (result, notes) tuple
        scorer_fn: Callable that takes notes and returns (score, repair_msgs)
        max_attempts: Maximum number of generation attempts
        accept_threshold: Minimum score to accept
        early_stop_threshold: Score above which we stop immediately
    
    Returns:
        RejectionSamplingResult with best result and metrics
    """
    best_result = None
    best_score = -1.0
    all_scores = []
    
    for attempt in range(max_attempts):
        result, notes = generate_fn()
        if not result or not notes:
            continue
        
        score, _ = scorer_fn(notes)
        all_scores.append(score)
        
        if score > best_score:
            best_score = score
            best_result = result
        
        # Early stop if we hit excellent score
        if score >= early_stop_threshold:
            logger.info(f"Rejection sampling: early stop at attempt {attempt + 1}, score {score:.3f}")
            return RejectionSamplingResult(
                best_result=best_result,
                best_score=best_score,
                attempts=attempt + 1,
                accepted=True,
                all_scores=all_scores,
            )
    
    accepted = best_score >= accept_threshold
    logger.info(f"Rejection sampling: {len(all_scores)} attempts, best score {best_score:.3f}, accepted={accepted}")
    
    return RejectionSamplingResult(
        best_result=best_result,
        best_score=best_score,
        attempts=len(all_scores),
        accepted=accepted,
        all_scores=all_scores,
    )
