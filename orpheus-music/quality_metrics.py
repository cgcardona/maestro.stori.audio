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

from typing import List, Dict, Any
import statistics


def analyze_quality(notes: List[Dict[str, Any]], bars: int, tempo: int) -> Dict[str, float]:
    """
    Analyze generation quality with objective metrics.
    
    Args:
        notes: List of note dicts with pitch, startBeat, duration, velocity
        bars: Number of bars generated
        tempo: Tempo in BPM
        
    Returns:
        Dict of quality metrics (0-1 normalized where applicable)
    """
    if not notes:
        return {
            "note_count": 0,
            "error": "No notes generated"
        }
    
    metrics = {}
    
    # 1. Note density
    total_beats = bars * 4
    metrics["note_count"] = len(notes)
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
    start_beats = [n["startBeat"] for n in notes if "startBeat" in n]
    if start_beats:
        # Check for quantization (how "on-grid" are the notes?)
        fractional_parts = [b % 0.25 for b in start_beats]  # 16th note grid
        metrics["timing_quantization"] = 1.0 - (statistics.mean(fractional_parts) / 0.25)
        
        # Note spacing (are notes evenly distributed or clustered?)
        if len(start_beats) > 1:
            sorted_beats = sorted(start_beats)
            gaps = [sorted_beats[i+1] - sorted_beats[i] for i in range(len(sorted_beats)-1)]
            metrics["timing_gap_stdev"] = statistics.stdev(gaps) if gaps else 0.0
    
    # 5. Duration distribution
    durations = [n["duration"] for n in notes if "duration" in n]
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


def compare_generations(notes_a: List[dict], notes_b: List[dict], bars: int, tempo: int) -> Dict[str, Any]:
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
