"""
Quality Metrics for Music Generation

Objective measures to track generation quality over time.
Essential for A/B testing policies without relying on subjective "sounds good to us."

Metrics tracked:
- Note density distribution
- Rhythmic stability
- Tonal coherence
- Repetition vs novelty
- Velocity distribution
"""
from __future__ import annotations

import statistics

from orpheus_types import GenerationComparison, OrpheusNoteDict


def analyze_quality(notes: list[OrpheusNoteDict], bars: int, tempo: int) -> dict[str, float]:
    """
    Analyze generation quality with objective metrics.
    
    Args:
        notes: list of note dicts with pitch, startBeat, duration, velocity
        bars: Number of bars generated
        tempo: Tempo in BPM
        
    Returns:
        dict of quality metrics (0-1 normalized where applicable)
    """
    if not notes:
        return {
            "note_count": 0.0,
            "error": -1.0,
        }
    
    metrics: dict[str, float] = {}
    
    # 1. Note density
    total_beats = bars * 4
    metrics["note_count"] = float(len(notes))
    metrics["notes_per_bar"] = len(notes) / max(bars, 1)
    metrics["notes_per_beat"] = len(notes) / max(total_beats, 1)
    
    # 2. Pitch range and distribution
    pitches = [n["pitch"] for n in notes if "pitch" in n]
    if pitches:
        metrics["pitch_min"] = min(pitches)
        metrics["pitch_max"] = max(pitches)
        metrics["pitch_range"] = max(pitches) - min(pitches)
        metrics["pitch_mean"] = statistics.mean(pitches)
        metrics["pitch_stdev"] = statistics.stdev(pitches) if len(pitches) > 1 else 0.0
    
    # 3. Velocity distribution (expressiveness)
    velocities = [n["velocity"] for n in notes if "velocity" in n]
    if velocities:
        metrics["velocity_mean"] = statistics.mean(velocities)
        metrics["velocity_stdev"] = statistics.stdev(velocities) if len(velocities) > 1 else 0.0
        metrics["velocity_variation"] = metrics["velocity_stdev"] / max(metrics["velocity_mean"], 1)
    
    # 4. Timing distribution (rhythmic stability)
    start_beats = [n["start_beat"] for n in notes]
    if start_beats:
        # Check for quantization (how "on-grid" are the notes?)
        fractional_parts = [b % 0.25 for b in start_beats]  # 16th note grid
        metrics["timing_quantization"] = 1.0 - (statistics.mean(fractional_parts) / 0.25)
        
        # Note spacing (are notes evenly distributed or clustered?)
        if len(start_beats) > 1:
            sorted_beats = sorted(start_beats)
            gaps = [sorted_beats[i+1] - sorted_beats[i] for i in range(len(sorted_beats)-1)]
            metrics["timing_gap_stdev"] = statistics.stdev(gaps) if len(gaps) > 1 else 0.0
    
    # 5. Duration distribution
    durations = [n["duration_beats"] for n in notes]
    if durations:
        metrics["duration_mean"] = statistics.mean(durations)
        metrics["duration_stdev"] = statistics.stdev(durations) if len(durations) > 1 else 0.0
    
    # 6. Repetition analysis (novelty vs predictability)
    # Simple heuristic: count repeated 2-note patterns
    if len(notes) >= 4:
        pattern_set = set()
        repeated_patterns = 0
        for i in range(len(notes) - 1):
            pattern = (notes[i]["pitch"], notes[i+1]["pitch"])
            if pattern in pattern_set:
                repeated_patterns += 1
            pattern_set.add(pattern)
        metrics["pattern_repetition_rate"] = repeated_patterns / max(len(notes) - 1, 1)
    
    # 7. Overall quality score (weighted composite)
    # This is a heuristic - tune weights based on listening tests
    quality_score = 0.0
    weights_sum = 0.0
    
    # Prefer reasonable note density (not too sparse, not too dense)
    if "notes_per_bar" in metrics:
        ideal_density = 8.0  # ~8 notes per bar is "good"
        density_score = 1.0 - min(abs(metrics["notes_per_bar"] - ideal_density) / ideal_density, 1.0)
        quality_score += density_score * 0.3
        weights_sum += 0.3
    
    # Prefer good velocity variation (not flat, not chaotic)
    if "velocity_variation" in metrics:
        ideal_variation = 0.15  # 15% variation
        variation_score = 1.0 - min(abs(metrics["velocity_variation"] - ideal_variation) / ideal_variation, 1.0)
        quality_score += variation_score * 0.2
        weights_sum += 0.2
    
    # Prefer reasonable pitch range (not too narrow, not impossible)
    if "pitch_range" in metrics:
        range_val = metrics["pitch_range"]
        if 12 <= range_val <= 36:  # 1-3 octaves is good
            range_score = 1.0
        else:
            range_score = 0.5
        quality_score += range_score * 0.2
        weights_sum += 0.2
    
    # Prefer some repetition (musically coherent) but not total repetition
    if "pattern_repetition_rate" in metrics:
        rep_rate = metrics["pattern_repetition_rate"]
        if 0.2 <= rep_rate <= 0.5:  # Sweet spot
            rep_score = 1.0
        elif rep_rate < 0.2:
            rep_score = 0.7  # Too random
        else:
            rep_score = 0.6  # Too repetitive
        quality_score += rep_score * 0.3
        weights_sum += 0.3
    
    if weights_sum > 0:
        metrics["quality_score"] = quality_score / weights_sum
    
    return metrics


def rejection_score(notes: list[OrpheusNoteDict], bars: int) -> float:
    """Fast rejection sampling score for candidate ranking.

    Combines four signals into a single 0–1 score:
    - Note density variance (prefer moderate, penalize extremes)
    - Pitch range sanity (1–3 octaves ideal)
    - Repetition penalty (penalize >60% repeated 2-note patterns)
    - Silence penalty (penalize bars with no notes)

    Higher is better. Used by the generation loop to pick the best of
    N candidates without human intervention.
    """
    if not notes:
        return 0.0

    total_beats = max(bars * 4, 1)

    # ── Density variance: how evenly distributed are notes across bars? ──
    bar_counts = [0] * max(bars, 1)
    for n in notes:
        b = int(n.get("start_beat", 0.0) / 4)
        if 0 <= b < bars:
            bar_counts[b] += 1
    mean_density = len(notes) / max(bars, 1)
    if mean_density > 0 and len(bar_counts) > 1:
        variance = sum((c - mean_density) ** 2 for c in bar_counts) / len(bar_counts)
        cv = (variance ** 0.5) / mean_density
        density_score = max(0.0, 1.0 - cv)
    else:
        density_score = 0.5

    # ── Pitch range sanity ──
    pitches = [n.get("pitch", 60) for n in notes]
    pitch_range = max(pitches) - min(pitches) if pitches else 0
    if 12 <= pitch_range <= 36:
        range_score = 1.0
    elif pitch_range < 12:
        range_score = pitch_range / 12.0
    else:
        range_score = max(0.0, 1.0 - (pitch_range - 36) / 48.0)

    # ── Repetition penalty ──
    if len(notes) >= 4:
        pattern_set: set[tuple[int, int]] = set()
        repeated = 0
        for i in range(len(notes) - 1):
            p = (notes[i].get("pitch", 0), notes[i + 1].get("pitch", 0))
            if p in pattern_set:
                repeated += 1
            pattern_set.add(p)
        rep_rate = repeated / max(len(notes) - 1, 1)
        rep_score = max(0.0, 1.0 - max(0.0, rep_rate - 0.4) / 0.6)
    else:
        rep_score = 0.5

    # ── Silence penalty: fraction of bars with at least one note ──
    active_bars = sum(1 for c in bar_counts if c > 0)
    silence_score = active_bars / max(bars, 1)

    return float(round(
        density_score * 0.3 + range_score * 0.2 + rep_score * 0.25 + silence_score * 0.25,
        4,
    ))


def compare_generations(notes_a: list[OrpheusNoteDict], notes_b: list[OrpheusNoteDict], bars: int, tempo: int) -> GenerationComparison:
    """
    Compare two generations to determine which is better.
    
    Useful for A/B testing policies or picking best from parallel candidates.
    
    Returns:
        Comparison metrics and a "winner" recommendation.
    """
    metrics_a = analyze_quality(notes_a, bars, tempo)
    metrics_b = analyze_quality(notes_b, bars, tempo)
    
    score_a = metrics_a.get("quality_score", 0.0)
    score_b = metrics_b.get("quality_score", 0.0)
    
    return {
        "generation_a": metrics_a,
        "generation_b": metrics_b,
        "winner": "a" if score_a > score_b else "b" if score_b > score_a else "tie",
        "confidence": abs(score_a - score_b),
    }
