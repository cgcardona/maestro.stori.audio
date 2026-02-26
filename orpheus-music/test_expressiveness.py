"""Tests for the Orpheus expressiveness layer.

Covers:
- Key detection (Krumhansl-Schmuckler)
- MIDI transposition
- Candidate scoring
- Post-processing pipeline
- Control vector activation
- Seed selector key-awareness
"""

from __future__ import annotations

import math

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from key_detection import (
    detect_key_from_pitches,
    key_to_semitones,
    transpose_distance,
    parse_key_string,
    _pearson,
    _rotate,
)
from midi_transforms import transpose_notes
from candidate_scorer import (
    score_candidate,
    select_best_candidate,
    CandidateScore,
    _key_compliance,
    _density_match,
    _register_compliance,
    _velocity_compliance,
    _pattern_diversity,
    _silence_score,
)
from orpheus_types import OrpheusNoteDict, ScoringParams
from post_processing import PostProcessor, PostProcessorConfig, build_post_processor
from generation_policy import (
    apply_controls_to_params,
    GenerationControlVector,
    quality_preset_to_batch_count,
)


# ============================================================================
# Key Detection Tests
# ============================================================================


class TestKeyDetection:
    """Test Krumhansl-Schmuckler key detection."""

    def test_c_major_scale(self) -> None:
        """C major scale should detect as C major."""
        pitches = [60, 62, 64, 65, 67, 69, 71, 72]  # C D E F G A B C
        result = detect_key_from_pitches(pitches)
        assert result is not None
        tonic, mode, confidence = result
        assert tonic == "C"
        assert mode == "major"
        assert confidence > 0.5

    def test_a_minor_scale(self) -> None:
        """A minor scale should detect as A minor."""
        pitches = [57, 59, 60, 62, 64, 65, 67, 69]  # A B C D E F G A
        result = detect_key_from_pitches(pitches)
        assert result is not None
        tonic, mode, confidence = result
        assert tonic == "A"
        assert mode == "minor"
        assert confidence > 0.5

    def test_too_few_notes(self) -> None:
        """Too few notes should return None."""
        result = detect_key_from_pitches([60, 62, 64])
        assert result is None

    def test_empty_pitches(self) -> None:
        """Empty pitch list should return None."""
        result = detect_key_from_pitches([])
        assert result is None

    def test_chromatic_detection(self) -> None:
        """Chromatic passage should still return something."""
        pitches = list(range(48, 72))  # 24 notes, all pitches
        result = detect_key_from_pitches(pitches)
        assert result is not None
        # Low confidence expected for chromatic music
        assert result[2] < 0.9

    def test_repeated_notes_single_pitch(self) -> None:
        """Many repeated notes of same pitch should still work."""
        pitches = [60] * 20
        result = detect_key_from_pitches(pitches)
        assert result is not None

    def test_g_major_pattern(self) -> None:
        """G major pentatonic should detect as G major or close relative."""
        pitches = [55, 57, 59, 62, 64, 67, 69, 71, 74, 76]  # G major pentatonic
        result = detect_key_from_pitches(pitches)
        assert result is not None
        assert result[0] in ("G", "D", "C")  # G or close relatives


class TestKeyUtilities:
    """Test key utility functions."""

    def test_key_to_semitones(self) -> None:
        assert key_to_semitones("C", "major") == 0
        assert key_to_semitones("A", "minor") == 9
        assert key_to_semitones("F#", "major") == 6
        assert key_to_semitones("G", "major") == 7

    def test_transpose_distance_same_key(self) -> None:
        assert transpose_distance("C", "major", "C", "major") == 0

    def test_transpose_distance_up_half_step(self) -> None:
        assert transpose_distance("C", "major", "C#", "major") == 1

    def test_transpose_distance_down(self) -> None:
        assert transpose_distance("C", "major", "B", "major") == -1

    def test_transpose_distance_shortest(self) -> None:
        """Should pick the shortest path (tritone = 6 or -6)."""
        d = transpose_distance("C", "major", "F#", "major")
        assert abs(d) == 6

    def test_parse_key_string_am(self) -> None:
        assert parse_key_string("Am") == ("A", "minor")

    def test_parse_key_string_c_major(self) -> None:
        assert parse_key_string("C major") == ("C", "major")

    def test_parse_key_string_fsharp_minor(self) -> None:
        assert parse_key_string("F# minor") == ("F#", "minor")

    def test_parse_key_string_bare_note(self) -> None:
        assert parse_key_string("G") == ("G", "major")

    def test_parse_key_string_empty(self) -> None:
        assert parse_key_string("") is None

    def test_pearson_perfect_correlation(self) -> None:
        x = [1.0, 2.0, 3.0]
        y = (1.0, 2.0, 3.0)
        assert abs(_pearson(x, y) - 1.0) < 0.001

    def test_rotate(self) -> None:
        data = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]
        rotated = _rotate(data, 3)
        # Left rotation by 3: element at index 3 moves to index 0
        assert rotated == [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 0.0, 1.0, 2.0]


# ============================================================================
# MIDI Transposition Tests
# ============================================================================


class TestTransposeNotes:
    """Test note-level transposition."""

    def test_transpose_up(self) -> None:
        notes = [{"pitch": 60, "start_beat": 0.0}]
        result = transpose_notes(notes, 5)
        assert result[0]["pitch"] == 65

    def test_transpose_down(self) -> None:
        notes = [{"pitch": 60, "start_beat": 0.0}]
        result = transpose_notes(notes, -3)
        assert result[0]["pitch"] == 57

    def test_transpose_clamp_high(self) -> None:
        notes = [{"pitch": 125, "start_beat": 0.0}]
        result = transpose_notes(notes, 10)
        assert result[0]["pitch"] == 127

    def test_transpose_clamp_low(self) -> None:
        notes = [{"pitch": 3, "start_beat": 0.0}]
        result = transpose_notes(notes, -10)
        assert result[0]["pitch"] == 0

    def test_transpose_zero(self) -> None:
        notes = [{"pitch": 60, "start_beat": 0.0}]
        result = transpose_notes(notes, 0)
        assert result[0]["pitch"] == 60

    def test_transpose_drums_skipped(self) -> None:
        notes = [{"pitch": 36, "start_beat": 0.0}]
        result = transpose_notes(notes, 5, is_drums=True)
        assert result[0]["pitch"] == 36


# ============================================================================
# Candidate Scoring Tests
# ============================================================================


class TestCandidateScoring:
    """Test the multi-dimensional candidate scorer."""

    def _make_notes(
        self,
        n: int = 32,
        pitch_center: int = 60,
        pitch_range: int = 12,
        velocity_range: tuple[int, int] = (60, 100),
        bars: int = 4,
    ) -> list[OrpheusNoteDict]:
        import random
        rng = random.Random(42)
        return [
            OrpheusNoteDict(
                pitch=pitch_center + rng.randint(-pitch_range // 2, pitch_range // 2),
                start_beat=(i / n) * bars * 4,
                duration_beats=0.5,
                velocity=rng.randint(*velocity_range),
            )
            for i in range(n)
        ]

    def test_score_basic(self) -> None:
        notes = self._make_notes()
        result = score_candidate(
            notes, {0: notes}, batch_index=0,
            params=ScoringParams(bars=4, target_key=None, expected_channels=1),
        )
        assert 0.0 <= result.total_score <= 1.0
        assert result.note_count == 32
        assert result.batch_index == 0

    def test_score_with_key_target(self) -> None:
        c_major_notes: list[OrpheusNoteDict] = [
            OrpheusNoteDict(pitch=p, start_beat=i * 0.5, duration_beats=0.5, velocity=80)
            for i, p in enumerate([60, 62, 64, 65, 67, 69, 71, 72] * 4)
        ]
        result = score_candidate(
            c_major_notes, {0: c_major_notes},
            batch_index=0,
            params=ScoringParams(bars=4, target_key="C major", expected_channels=1),
        )
        assert result.dimensions["key_compliance"] > 0.6

    def test_score_empty_notes(self) -> None:
        result = score_candidate(
            [], {}, batch_index=0,
            params=ScoringParams(bars=4, target_key=None, expected_channels=1),
        )
        assert result.total_score >= 0.0
        assert result.note_count == 0

    def test_select_best(self) -> None:
        scores = [
            CandidateScore(batch_index=0, total_score=0.5, note_count=20),
            CandidateScore(batch_index=1, total_score=0.8, note_count=25),
            CandidateScore(batch_index=2, total_score=0.3, note_count=30),
        ]
        best = select_best_candidate(scores)
        assert best.batch_index == 1
        assert best.total_score == 0.8

    def test_key_compliance_no_target(self) -> None:
        assert _key_compliance([60, 62, 64], None) == 0.5

    def test_density_match_ideal(self) -> None:
        notes: list[OrpheusNoteDict] = [
            OrpheusNoteDict(pitch=60, start_beat=i * 0.5, duration_beats=0.5, velocity=80)
            for i in range(32)
        ]
        score = _density_match(notes, 4, 8.0)
        assert score > 0.9

    def test_density_match_default_orpheus_output(self) -> None:
        """Typical Orpheus output (~111 notes/bar) scores reasonably with no target."""
        notes: list[OrpheusNoteDict] = [
            OrpheusNoteDict(pitch=60, start_beat=i * 0.036, duration_beats=0.1, velocity=80)
            for i in range(444)
        ]
        score = _density_match(notes, 4, None)
        assert score > 0.3, f"Default density should handle typical Orpheus output, got {score}"

    def test_register_compliance(self) -> None:
        pitches = list(range(55, 66))  # centered around 60
        score = _register_compliance(pitches, 60, 12)
        assert score > 0.7

    def test_velocity_compliance_all_in_range(self) -> None:
        notes: list[OrpheusNoteDict] = [
            OrpheusNoteDict(pitch=60, start_beat=float(i), duration_beats=0.5, velocity=v)
            for i, v in enumerate(range(60, 100))
        ]
        score = _velocity_compliance(notes, 60, 100)
        assert score == 1.0

    def test_silence_score_full(self) -> None:
        notes: list[OrpheusNoteDict] = [
            OrpheusNoteDict(pitch=60, start_beat=float(i), duration_beats=0.5, velocity=80)
            for i in range(16)
        ]
        score = _silence_score(notes, 4)
        assert score == 1.0


# ============================================================================
# Post-Processing Tests
# ============================================================================


class TestPostProcessing:
    """Test the post-processing pipeline."""

    def test_velocity_scaling(self) -> None:
        config = PostProcessorConfig(velocity_floor=40, velocity_ceiling=80)
        pp = PostProcessor(config)
        notes: list[OrpheusNoteDict] = [
            OrpheusNoteDict(pitch=60, start_beat=0.0, duration_beats=0.5, velocity=10),
            OrpheusNoteDict(pitch=62, start_beat=0.5, duration_beats=0.5, velocity=127),
            OrpheusNoteDict(pitch=64, start_beat=1.0, duration_beats=0.5, velocity=64),
        ]
        result = pp.process(notes)
        velocities = [n["velocity"] for n in result]
        assert min(velocities) >= 40
        assert max(velocities) <= 80

    def test_register_normalization(self) -> None:
        config = PostProcessorConfig(register_center=72)
        pp = PostProcessor(config)
        notes: list[OrpheusNoteDict] = [
            OrpheusNoteDict(pitch=48, start_beat=0.0, duration_beats=0.5, velocity=80),
            OrpheusNoteDict(pitch=50, start_beat=0.5, duration_beats=0.5, velocity=80),
            OrpheusNoteDict(pitch=52, start_beat=1.0, duration_beats=0.5, velocity=80),
        ]
        result = pp.process(notes)
        median = sorted(n["pitch"] for n in result)[1]
        assert abs(median - 72) <= 12  # within one octave

    def test_quantization_16th(self) -> None:
        config = PostProcessorConfig(subdivision=16)
        pp = PostProcessor(config)
        notes: list[OrpheusNoteDict] = [
            OrpheusNoteDict(pitch=60, start_beat=0.13, duration_beats=0.5, velocity=80),
            OrpheusNoteDict(pitch=62, start_beat=0.51, duration_beats=0.5, velocity=80),
        ]
        result = pp.process(notes)
        assert result[0]["start_beat"] in (0.0, 0.25)
        assert result[1]["start_beat"] == 0.5

    def test_duration_cleanup(self) -> None:
        config = PostProcessorConfig(min_duration_beats=0.25, max_duration_beats=2.0)
        pp = PostProcessor(config)
        notes: list[OrpheusNoteDict] = [
            OrpheusNoteDict(pitch=60, start_beat=0.0, duration_beats=0.05, velocity=80),
            OrpheusNoteDict(pitch=62, start_beat=1.0, duration_beats=4.0, velocity=80),
        ]
        result = pp.process(notes)
        assert result[0]["duration_beats"] == 0.25
        assert result[1]["duration_beats"] == 2.0

    def test_swing(self) -> None:
        config = PostProcessorConfig(swing_amount=0.5)
        pp = PostProcessor(config)
        notes: list[OrpheusNoteDict] = [
            OrpheusNoteDict(pitch=60, start_beat=0.0, duration_beats=0.25, velocity=80),
            OrpheusNoteDict(pitch=62, start_beat=0.25, duration_beats=0.25, velocity=80),
            OrpheusNoteDict(pitch=64, start_beat=0.5, duration_beats=0.25, velocity=80),
        ]
        result = pp.process(notes)
        assert result[0]["start_beat"] == 0.0  # downbeat unchanged
        assert result[1]["start_beat"] > 0.25  # odd 16th delayed

    def test_no_transforms_when_disabled(self) -> None:
        config = PostProcessorConfig(velocity_floor=40, velocity_ceiling=80, enabled=False)
        pp = PostProcessor(config)
        notes: list[OrpheusNoteDict] = [
            OrpheusNoteDict(pitch=60, start_beat=0.0, duration_beats=0.5, velocity=10),
        ]
        result = pp.process(notes)
        assert result[0]["velocity"] == 10

    def test_build_from_constraints(self) -> None:
        from music_service import GenerationConstraintsPayload
        gc = GenerationConstraintsPayload(
            velocity_floor=50, velocity_ceiling=90,
            register_center=65, register_spread=18,
            subdivision=16, swing_amount=0.3,
        )
        pp = build_post_processor(generation_constraints=gc)
        assert pp.config.velocity_floor == 50
        assert pp.config.register_center == 65
        assert pp.config.subdivision == 16
        assert abs(pp.config.swing_amount - 0.3) < 0.01

    def test_build_from_role_profile(self) -> None:
        from music_service import RoleProfileSummary
        rp = RoleProfileSummary(register_mean_pitch=55.0, swing_ratio=0.15)
        pp = build_post_processor(role_profile_summary=rp)
        assert pp.config.register_center == 55
        assert pp.config.swing_amount > 0.0


# ============================================================================
# Control Vector Activation Tests
# ============================================================================


class TestControlVectorActivation:
    """Test that the control vector actually affects Gradio params."""

    def test_high_creativity_raises_temperature(self) -> None:
        controls = GenerationControlVector(creativity=1.0, groove=0.5)
        params = apply_controls_to_params(controls, bars=4)
        assert params["temperature"] >= 0.95

    def test_low_creativity_lowers_temperature(self) -> None:
        controls = GenerationControlVector(creativity=0.0, groove=0.5)
        params = apply_controls_to_params(controls, bars=4)
        assert params["temperature"] <= 0.75

    def test_high_groove_raises_top_p(self) -> None:
        controls = GenerationControlVector(creativity=0.5, groove=1.0)
        params = apply_controls_to_params(controls, bars=4)
        assert params["top_p"] >= 0.97

    def test_params_within_safe_ranges(self) -> None:
        for creativity in [0.0, 0.25, 0.5, 0.75, 1.0]:
            for groove in [0.0, 0.25, 0.5, 0.75, 1.0]:
                controls = GenerationControlVector(creativity=creativity, groove=groove)
                params = apply_controls_to_params(controls, bars=4)
                assert 0.7 <= params["temperature"] <= 1.0
                assert 0.90 <= params["top_p"] <= 0.98
                assert params["num_gen_tokens"] >= 512
                assert params["num_prime_tokens"] >= 2048

    def test_quality_preset_batch_count(self) -> None:
        assert quality_preset_to_batch_count("fast") == 1
        assert quality_preset_to_batch_count("balanced") == 3
        assert quality_preset_to_batch_count("quality") == 10
        assert quality_preset_to_batch_count("unknown") == 3
