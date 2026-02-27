"""Tests for MIDI analyzer metrics."""

from __future__ import annotations

import pytest

from tourdeforce.analyzers.midi import (
    _compute_quality_score,
    _pitch_class_entropy,
    _polyphony_estimate,
    _repetition_score,
    _stdev,
    analyze_tool_call_notes,
)
from tourdeforce.models import MidiMetrics


class TestStatHelpers:

    def test_stdev_single_value(self) -> None:

        assert _stdev([42]) == 0.0

    def test_stdev_identical_values(self) -> None:

        assert _stdev([5, 5, 5, 5]) == 0.0

    def test_stdev_known_values(self) -> None:

        result = _stdev([2, 4, 4, 4, 5, 5, 7, 9])
        assert 1.9 < result < 2.2  # sample stdev ≈ 2.14

    def test_pitch_class_entropy_single_class(self) -> None:

        pitches = [60, 72, 48, 84]  # all C
        assert _pitch_class_entropy(pitches) == 0.0

    def test_pitch_class_entropy_uniform(self) -> None:

        pitches = list(range(60, 72))  # all 12 pitch classes
        entropy = _pitch_class_entropy(pitches)
        assert 3.5 < entropy < 3.6  # log2(12) ≈ 3.585

    def test_pitch_class_entropy_empty(self) -> None:

        assert _pitch_class_entropy([]) == 0.0

    def test_repetition_score_identical(self) -> None:

        pitches = [60, 62, 64, 60, 62, 64, 60, 62, 64]
        score = _repetition_score(pitches)
        assert score > 0.3  # high repetition

    def test_repetition_score_varied(self) -> None:

        pitches = [60, 64, 55, 72, 61, 68, 50, 75, 59, 66, 53, 70]
        score = _repetition_score(pitches)
        assert score < 0.5  # varied intervals → lower repetition

    def test_polyphony_single_note(self) -> None:

        poly = _polyphony_estimate([0.0], [1.0])
        assert poly > 0

    def test_polyphony_overlapping(self) -> None:

        starts = [0.0, 0.0, 0.0]
        durs = [2.0, 2.0, 2.0]
        poly = _polyphony_estimate(starts, durs)
        assert poly >= 1.5  # at least partial overlap


class TestAnalyzeToolCallNotes:

    def test_basic_analysis(self) -> None:

        notes = [
            {"pitch": 60, "startBeat": 0.0, "durationBeats": 1.0, "velocity": 80},
            {"pitch": 62, "startBeat": 1.0, "durationBeats": 1.0, "velocity": 90},
            {"pitch": 64, "startBeat": 2.0, "durationBeats": 1.0, "velocity": 70},
            {"pitch": 65, "startBeat": 3.0, "durationBeats": 1.0, "velocity": 85},
        ]
        m = analyze_tool_call_notes(notes)
        assert m.note_count_total == 4
        assert m.velocity_mean == pytest.approx(81.25)
        assert m.velocity_range == (70, 90)
        assert m.pitch_class_entropy > 0
        assert m.quality_score > 0

    def test_empty_notes(self) -> None:

        m = analyze_tool_call_notes([])
        assert m.note_count_total == 0
        assert m.quality_score == 0

    def test_garbage_detection(self) -> None:

        notes = [
            {"pitch": 0, "startBeat": 0, "durationBeats": 0, "velocity": 200},
        ]
        m = analyze_tool_call_notes(notes)
        assert m.zero_length_notes >= 1
        assert m.extreme_pitches >= 1
        assert m.impossible_velocities >= 1

    def test_realistic_drum_pattern(self) -> None:

        notes = []
        for i in range(16):
            if i % 4 == 0:
                notes.append({"pitch": 36, "startBeat": i * 0.25, "durationBeats": 0.25, "velocity": 100})
            if i % 2 == 0:
                notes.append({"pitch": 42, "startBeat": i * 0.25, "durationBeats": 0.125, "velocity": 70})
            if i % 8 == 4:
                notes.append({"pitch": 38, "startBeat": i * 0.25, "durationBeats": 0.25, "velocity": 90})

        m = analyze_tool_call_notes(notes)
        assert m.note_count_total > 10
        assert m.quality_score > 20


class TestQualityScore:

    def test_perfect_score_inputs(self) -> None:

        m = MidiMetrics(
            note_count_total=100,
            pitch_class_entropy=3.0,
            velocity_stdev=15.0,
            zero_length_notes=0,
            extreme_pitches=0,
            impossible_velocities=0,
            note_spam_regions=0,
            empty_tracks=0,
            rhythmic_density_per_bar=[8, 6, 10, 7],
            polyphony_estimate=3.0,
        )
        score = _compute_quality_score(m)
        assert score >= 80

    def test_garbage_penalized(self) -> None:

        m = MidiMetrics(
            note_count_total=100,
            pitch_class_entropy=3.0,
            velocity_stdev=15.0,
            zero_length_notes=20,
            extreme_pitches=10,
            note_spam_regions=5,
        )
        score = _compute_quality_score(m)
        assert score < 60
