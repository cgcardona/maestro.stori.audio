"""Tests for chord name to pitch utilities."""
from __future__ import annotations

import pytest

from app.core.chord_utils import (
    chord_root_pitch_class,
    chord_to_root_and_fifth_midi,
    chord_to_scale_degrees,
    chord_to_midi_voicing,
)


class TestChordRootPitchClass:
    """Test chord_root_pitch_class."""

    def test_c_returns_0(self) -> None:

        assert chord_root_pitch_class("C") == 0
        assert chord_root_pitch_class("c") == 0

    def test_sharps(self) -> None:

        assert chord_root_pitch_class("C#") == 1
        assert chord_root_pitch_class("F#") == 6
        assert chord_root_pitch_class("G#") == 8

    def test_flats(self) -> None:

        assert chord_root_pitch_class("Db") == 1
        assert chord_root_pitch_class("Eb") == 3
        assert chord_root_pitch_class("Bb") == 10

    def test_flats_normalized_to_sharps_internally(self) -> None:

        assert chord_root_pitch_class("DB") == 1
        assert chord_root_pitch_class("GB") == 6
        assert chord_root_pitch_class("AB") == 8
        assert chord_root_pitch_class("BB") == 10

    def test_empty_or_none_defaults_to_c(self) -> None:

        assert chord_root_pitch_class("") == 0
        assert chord_root_pitch_class(None) == 0  # type: ignore[arg-type]  # intentional None: testing defensive default

    def test_strips_whitespace(self) -> None:

        assert chord_root_pitch_class("  G  ") == 7

    def test_minor_chord_name_still_returns_root(self) -> None:

        assert chord_root_pitch_class("Cm") == 0
        assert chord_root_pitch_class("Am") == 9
        assert chord_root_pitch_class("F#m") == 6


class TestChordToRootAndFifthMidi:
    """Test chord_to_root_and_fifth_midi."""

    def test_c_octave_4(self) -> None:

        root, fifth = chord_to_root_and_fifth_midi("C", 4)
        assert root == 4 * 12 + 0  # 48
        assert fifth == root + 7  # 55

    def test_a_minor_octave_3(self) -> None:

        root, fifth = chord_to_root_and_fifth_midi("Am", 3)
        assert root == 3 * 12 + 9  # 45
        assert fifth == 52

    def test_fifth_is_perfect_fifth_above_root(self) -> None:

        root, fifth = chord_to_root_and_fifth_midi("G", 5)
        assert fifth - root == 7


class TestChordToScaleDegrees:
    """Test chord_to_scale_degrees."""

    def test_major_three_degrees(self) -> None:

        # root, major third, fifth
        degs = chord_to_scale_degrees("C", num_degrees=3)
        assert degs == [0, 4, 7]

    def test_minor_three_degrees(self) -> None:

        degs = chord_to_scale_degrees("Cm", num_degrees=3)
        assert degs == [0, 3, 7]

    def test_four_degrees_adds_seventh(self) -> None:

        degs_maj = chord_to_scale_degrees("C", num_degrees=4)
        assert degs_maj == [0, 4, 7, 11]
        degs_min = chord_to_scale_degrees("Am", num_degrees=4)
        assert degs_min == [0, 3, 7, 10]

    def test_respects_num_degrees(self) -> None:

        assert len(chord_to_scale_degrees("C", num_degrees=2)) == 2
        # Implementation caps at 4 (root, third, fifth, seventh)
        assert len(chord_to_scale_degrees("C", num_degrees=4)) == 4
        assert len(chord_to_scale_degrees("C", num_degrees=5)) == 4


class TestChordToMidiVoicing:
    """Test chord_to_midi_voicing."""

    def test_c_major_four_voices_octave_4(self) -> None:

        midi = chord_to_midi_voicing("C", octave=4, num_voices=4)
        assert len(midi) == 4
        base = 4 * 12  # 48
        assert midi[0] == base + 0
        assert midi[1] == base + 4
        assert midi[2] == base + 7
        assert midi[3] == base + 11

    def test_a_minor_three_voices(self) -> None:

        midi = chord_to_midi_voicing("Am", octave=4, num_voices=3)
        assert len(midi) == 3
        base = 4 * 12 + 9  # A = 57
        assert midi == [base + 0, base + 3, base + 7]

    def test_default_four_voices(self) -> None:

        midi = chord_to_midi_voicing("G", octave=3)
        assert len(midi) == 4
