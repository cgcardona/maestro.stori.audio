"""
Tests for quality metrics system.

Quality metrics provide objective measures for A/B testing and
monitoring generation quality over time.
"""
import pytest
from quality_metrics import analyze_quality, compare_generations
from generation_policy import intent_to_controls


def test_analyze_empty_notes():
    """Test quality analysis with no notes."""
    result = analyze_quality([], bars=4, tempo=120)
    assert result["note_count"] == 0
    assert "error" in result


def test_analyze_basic_notes():
    """Test quality analysis with simple note pattern."""
    notes = [
        {"pitch": 60, "startBeat": 0.0, "duration": 0.5, "velocity": 80},
        {"pitch": 64, "startBeat": 1.0, "duration": 0.5, "velocity": 90},
        {"pitch": 67, "startBeat": 2.0, "duration": 0.5, "velocity": 85},
    ]
    
    result = analyze_quality(notes, bars=4, tempo=120)
    
    assert result["note_count"] == 3
    assert result["notes_per_bar"] == 0.75  # 3 notes / 4 bars
    assert result["pitch_min"] == 60
    assert result["pitch_max"] == 67
    assert result["pitch_range"] == 7
    assert "quality_score" in result


def test_quality_score_reasonable_density():
    """Test quality score favors reasonable note density."""
    # Good density (~8 notes per bar)
    good_notes = [
        {"pitch": 60 + i, "startBeat": i * 0.5, "duration": 0.25, "velocity": 80}
        for i in range(32)  # 32 notes over 4 bars = 8/bar
    ]
    
    # Too sparse (1 note per bar)
    sparse_notes = [
        {"pitch": 60, "startBeat": float(i), "duration": 1.0, "velocity": 80}
        for i in range(4)
    ]
    
    good_result = analyze_quality(good_notes, bars=4, tempo=120)
    sparse_result = analyze_quality(sparse_notes, bars=4, tempo=120)
    
    # Good density should score higher
    assert good_result.get("quality_score", 0) > sparse_result.get("quality_score", 0)


def test_velocity_variation_scoring():
    """Test that appropriate velocity variation scores well."""
    # Good variation (15% around mean of 80)
    good_notes = [
        {"pitch": 60, "startBeat": 0.0, "duration": 0.5, "velocity": 80},
        {"pitch": 60, "startBeat": 0.5, "duration": 0.5, "velocity": 92},
        {"pitch": 60, "startBeat": 1.0, "duration": 0.5, "velocity": 68},
        {"pitch": 60, "startBeat": 1.5, "duration": 0.5, "velocity": 84},
    ]
    
    # Flat (no variation)
    flat_notes = [
        {"pitch": 60, "startBeat": float(i) * 0.5, "duration": 0.5, "velocity": 80}
        for i in range(8)
    ]
    
    good_result = analyze_quality(good_notes, bars=2, tempo=120)
    flat_result = analyze_quality(flat_notes, bars=2, tempo=120)
    
    # Variation should be detected
    assert good_result["velocity_variation"] > 0.1
    assert flat_result["velocity_variation"] < 0.01


def test_pitch_range_scoring():
    """Test that reasonable pitch ranges score better."""
    # Good range (2 octaves)
    good_notes = [
        {"pitch": 60 + i * 2, "startBeat": i * 0.5, "duration": 0.5, "velocity": 80}
        for i in range(13)  # C4 to C6 (pitches 60..84, range=24)
    ]
    
    # Too narrow (single note)
    narrow_notes = [
        {"pitch": 60, "startBeat": float(i) * 0.5, "duration": 0.5, "velocity": 80}
        for i in range(8)
    ]
    
    good_result = analyze_quality(good_notes, bars=4, tempo=120)
    narrow_result = analyze_quality(narrow_notes, bars=4, tempo=120)
    
    assert good_result["pitch_range"] == 24
    assert narrow_result["pitch_range"] == 0


def test_pattern_repetition():
    """Test repetition analysis."""
    # Some repetition (musical)
    musical_notes = [
        {"pitch": 60, "startBeat": 0.0, "duration": 0.5, "velocity": 80},
        {"pitch": 64, "startBeat": 0.5, "duration": 0.5, "velocity": 80},
        {"pitch": 60, "startBeat": 1.0, "duration": 0.5, "velocity": 80},  # Repeat
        {"pitch": 64, "startBeat": 1.5, "duration": 0.5, "velocity": 80},  # Repeat
        {"pitch": 67, "startBeat": 2.0, "duration": 0.5, "velocity": 80},  # New
    ]
    
    result = analyze_quality(musical_notes, bars=2, tempo=120)
    
    # Should have moderate repetition (sweet spot)
    assert 0.2 <= result["pattern_repetition_rate"] <= 0.6


def test_compare_generations():
    """Test comparison between two generations."""
    # Better generation (good density, variation)
    better_notes = [
        {"pitch": 60 + i % 7, "startBeat": i * 0.5, "duration": 0.5, "velocity": 80 + i % 20}
        for i in range(32)  # 8 notes/bar
    ]
    
    # Worse generation (sparse, flat)
    worse_notes = [
        {"pitch": 60, "startBeat": float(i), "duration": 1.0, "velocity": 80}
        for i in range(4)  # 1 note/bar
    ]
    
    comparison = compare_generations(better_notes, worse_notes, bars=4, tempo=120)
    
    assert comparison["winner"] in ["a", "b"]
    assert "confidence" in comparison
    assert comparison["generation_a"]["note_count"] > comparison["generation_b"]["note_count"]


def test_quality_metrics_all_fields():
    """Test that all expected metrics are computed."""
    notes = [
        {"pitch": 60 + i, "startBeat": i * 0.5, "duration": 0.5, "velocity": 80 + i * 2}
        for i in range(16)
    ]
    
    metrics = analyze_quality(notes, bars=4, tempo=120)
    
    # Check all expected fields are present
    expected_fields = [
        "note_count", "notes_per_bar", "notes_per_beat",
        "pitch_min", "pitch_max", "pitch_range", "pitch_mean", "pitch_stdev",
        "velocity_mean", "velocity_stdev", "velocity_variation",
        "duration_mean", "duration_stdev",
        "quality_score"
    ]
    
    for field in expected_fields:
        assert field in metrics, f"Missing metric: {field}"


def test_quality_score_range():
    """Test quality score is always between 0 and 1."""
    test_cases = [
        # Various note patterns
        [{"pitch": 60, "startBeat": 0.0, "duration": 0.5, "velocity": 80}],  # Minimal
        [{"pitch": 60 + i, "startBeat": i * 0.25, "duration": 0.25, "velocity": 80} for i in range(64)],  # Dense
        [{"pitch": 60, "startBeat": float(i), "duration": 0.5, "velocity": 80} for i in range(8)],  # Repetitive
    ]
    
    for notes in test_cases:
        metrics = analyze_quality(notes, bars=4, tempo=120)
        if "quality_score" in metrics:
            assert 0.0 <= metrics["quality_score"] <= 1.0


def test_tempo_adjustments():
    """Test tempo affects complexity."""
    # Slow tempo
    slow_controls = intent_to_controls(genre="trap", tempo=70)
    # Fast tempo
    fast_controls = intent_to_controls(genre="trap", tempo=180)
    
    # Slow can be more complex
    assert slow_controls.complexity >= fast_controls.complexity
