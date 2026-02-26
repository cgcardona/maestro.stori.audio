"""
Tests for quality metrics system.

Quality metrics provide objective measures for A/B testing and
monitoring generation quality over time.
"""
from __future__ import annotations

import pytest
from quality_metrics import analyze_quality, compare_generations
from generation_policy import intent_to_controls
from storpheus_types import StorpheusNoteDict


def _note(pitch: int, start_beat: float, duration_beats: float, velocity: int) -> StorpheusNoteDict:
    """Construct a fully-typed note dict for tests."""
    return StorpheusNoteDict(
        pitch=pitch,
        start_beat=start_beat,
        duration_beats=duration_beats,
        velocity=velocity,
    )


def test_analyze_empty_notes() -> None:
    """Test quality analysis with no notes."""
    result = analyze_quality([], bars=4, tempo=120)
    assert result["note_count"] == 0
    assert "error" in result


def test_analyze_basic_notes() -> None:
    """Test quality analysis with simple note pattern."""
    notes = [
        _note(60, 0.0, 0.5, 80),
        _note(64, 1.0, 0.5, 90),
        _note(67, 2.0, 0.5, 85),
    ]

    result = analyze_quality(notes, bars=4, tempo=120)

    assert result["note_count"] == 3
    assert result["notes_per_bar"] == 0.75  # 3 notes / 4 bars
    assert result["pitch_min"] == 60
    assert result["pitch_max"] == 67
    assert result["pitch_range"] == 7
    assert "quality_score" in result


def test_quality_score_reasonable_density() -> None:
    """Test quality score favors reasonable note density."""
    good_notes = [
        _note(60 + i, i * 0.5, 0.25, 80)
        for i in range(32)  # 32 notes over 4 bars = 8/bar
    ]
    sparse_notes = [
        _note(60, float(i), 1.0, 80)
        for i in range(4)  # 1 note/bar
    ]

    good_result = analyze_quality(good_notes, bars=4, tempo=120)
    sparse_result = analyze_quality(sparse_notes, bars=4, tempo=120)

    assert good_result.get("quality_score", 0) > sparse_result.get("quality_score", 0)


def test_velocity_variation_scoring() -> None:
    """Test that appropriate velocity variation scores well."""
    good_notes = [
        _note(60, 0.0, 0.5, 80),
        _note(60, 0.5, 0.5, 92),
        _note(60, 1.0, 0.5, 68),
        _note(60, 1.5, 0.5, 84),
    ]
    flat_notes = [
        _note(60, i * 0.5, 0.5, 80)
        for i in range(8)
    ]

    good_result = analyze_quality(good_notes, bars=2, tempo=120)
    flat_result = analyze_quality(flat_notes, bars=2, tempo=120)

    assert good_result["velocity_variation"] > 0.1
    assert flat_result["velocity_variation"] < 0.01


def test_pitch_range_scoring() -> None:
    """Test that reasonable pitch ranges score better."""
    good_notes = [
        _note(60 + i * 2, i * 0.5, 0.5, 80)
        for i in range(13)  # C4 to C6 (pitches 60..84, range=24)
    ]
    narrow_notes = [
        _note(60, i * 0.5, 0.5, 80)
        for i in range(8)
    ]

    good_result = analyze_quality(good_notes, bars=4, tempo=120)
    narrow_result = analyze_quality(narrow_notes, bars=4, tempo=120)

    assert good_result["pitch_range"] == 24
    assert narrow_result["pitch_range"] == 0


def test_pattern_repetition() -> None:
    """Test repetition analysis."""
    musical_notes = [
        _note(60, 0.0, 0.5, 80),
        _note(64, 0.5, 0.5, 80),
        _note(60, 1.0, 0.5, 80),  # Repeat
        _note(64, 1.5, 0.5, 80),  # Repeat
        _note(67, 2.0, 0.5, 80),  # New
    ]

    result = analyze_quality(musical_notes, bars=2, tempo=120)

    assert 0.2 <= result["pattern_repetition_rate"] <= 0.6


def test_compare_generations() -> None:
    """Test comparison between two generations."""
    better_notes = [
        _note(60 + i % 7, i * 0.5, 0.5, 80 + i % 20)
        for i in range(32)  # 8 notes/bar
    ]
    worse_notes = [
        _note(60, float(i), 1.0, 80)
        for i in range(4)  # 1 note/bar
    ]

    comparison = compare_generations(better_notes, worse_notes, bars=4, tempo=120)

    assert comparison["winner"] in ["a", "b"]
    assert "confidence" in comparison
    assert comparison["generation_a"]["note_count"] > comparison["generation_b"]["note_count"]


def test_quality_metrics_all_fields() -> None:
    """Test that all expected metrics are computed."""
    notes = [
        _note(60 + i, i * 0.5, 0.5, min(127, 80 + i * 2))
        for i in range(16)
    ]

    metrics = analyze_quality(notes, bars=4, tempo=120)

    expected_fields = [
        "note_count", "notes_per_bar", "notes_per_beat",
        "pitch_min", "pitch_max", "pitch_range", "pitch_mean", "pitch_stdev",
        "velocity_mean", "velocity_stdev", "velocity_variation",
        "duration_mean", "duration_stdev",
        "quality_score",
    ]
    for field in expected_fields:
        assert field in metrics, f"Missing metric: {field}"


def test_quality_score_range() -> None:
    """Test quality score is always between 0 and 1."""
    test_cases: list[list[StorpheusNoteDict]] = [
        [_note(60, 0.0, 0.5, 80)],
        [_note(60 + i, i * 0.25, 0.25, 80) for i in range(64)],
        [_note(60, float(i), 0.5, 80) for i in range(8)],
    ]

    for notes in test_cases:
        metrics = analyze_quality(notes, bars=4, tempo=120)
        if "quality_score" in metrics:
            assert 0.0 <= metrics["quality_score"] <= 1.0


def test_tempo_adjustments() -> None:
    """Test tempo affects complexity."""
    slow_controls = intent_to_controls(genre="trap", tempo=70)
    fast_controls = intent_to_controls(genre="trap", tempo=180)

    assert slow_controls.complexity >= fast_controls.complexity
