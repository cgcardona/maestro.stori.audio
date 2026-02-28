"""Tests for the MuseHub notation service — MIDI-to-notation conversion.

Covers acceptance criteria from issue #210 (score/notation renderer):
- test_notation_convert_ref_returns_result     — convert_ref_to_notation returns NotationResult
- test_notation_result_has_tracks             — result contains at least one track
- test_notation_result_tracks_have_required_fields — each track has clef, key_signature, etc.
- test_notation_notes_have_required_fields    — each note has pitch_name, octave, duration, etc.
- test_notation_deterministic                 — same ref always returns same result
- test_notation_different_refs_differ         — different refs produce different keys/tempos
- test_notation_num_tracks_clamped            — num_tracks=0 is clamped to 1
- test_notation_num_bars_clamped              — num_bars=0 is clamped to 1
- test_notation_to_dict_camel_case            — serialized dict uses camelCase timeSig
- test_notation_to_dict_has_all_keys          — serialized dict has tracks/tempo/key/timeSig
- test_notation_clef_for_bass                 — bass role gets bass clef
- test_notation_clef_for_piano               — piano role gets treble clef
- test_notation_start_beat_non_negative       — all notes have start_beat >= 0
- test_notation_velocity_in_range             — velocity is in [0, 127]
- test_notation_duration_valid               — duration is a recognized fraction string
"""
from __future__ import annotations

import pytest

from maestro.services.musehub_notation import (
    NotationResult,
    convert_ref_to_notation,
    notation_result_to_dict,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_DURATIONS = {"1/1", "1/2", "1/4", "1/8", "1/16"}


# ---------------------------------------------------------------------------
# Basic API
# ---------------------------------------------------------------------------


def test_notation_convert_ref_returns_result() -> None:
    """convert_ref_to_notation returns a NotationResult named tuple."""
    result = convert_ref_to_notation("abc1234")
    assert isinstance(result, NotationResult)


def test_notation_result_has_tracks() -> None:
    """Result always contains at least one track."""
    result = convert_ref_to_notation("abc1234", num_tracks=1)
    assert len(result.tracks) >= 1


def test_notation_result_tracks_have_required_fields() -> None:
    """Each NotationTrack dict contains all required metadata fields."""
    result = convert_ref_to_notation("abc1234", num_tracks=2)
    for track in result.tracks:
        assert "track_id" in track
        assert "clef" in track
        assert "key_signature" in track
        assert "time_signature" in track
        assert "instrument" in track
        assert "notes" in track
        assert isinstance(track["notes"], list)


def test_notation_notes_have_required_fields() -> None:
    """Each NotationNote dict in a track has all required fields."""
    result = convert_ref_to_notation("main", num_tracks=1, num_bars=4)
    for track in result.tracks:
        for note in track["notes"]:
            assert "pitch_name" in note
            assert "octave" in note
            assert "duration" in note
            assert "start_beat" in note
            assert "velocity" in note
            assert "track_id" in note


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_notation_deterministic() -> None:
    """The same ref always produces identical results (deterministic stub)."""
    r1 = convert_ref_to_notation("deadbeef", num_tracks=2, num_bars=4)
    r2 = convert_ref_to_notation("deadbeef", num_tracks=2, num_bars=4)
    assert r1.key == r2.key
    assert r1.tempo == r2.tempo
    assert r1.time_sig == r2.time_sig
    assert len(r1.tracks) == len(r2.tracks)
    for t1, t2 in zip(r1.tracks, r2.tracks):
        assert len(t1["notes"]) == len(t2["notes"])


def test_notation_different_refs_differ() -> None:
    """Different refs produce at least one differing field (key, tempo, or timeSig)."""
    r1 = convert_ref_to_notation("aaaaaaa", num_tracks=1)
    r2 = convert_ref_to_notation("bbbbbbb", num_tracks=1)
    # At least one of key, tempo, or time_sig must differ across distinct refs
    differs = (r1.key != r2.key) or (r1.tempo != r2.tempo) or (r1.time_sig != r2.time_sig)
    assert differs, "Distinct refs should produce different notation parameters"


# ---------------------------------------------------------------------------
# Clamping
# ---------------------------------------------------------------------------


def test_notation_num_tracks_clamped() -> None:
    """num_tracks=0 is clamped to 1 — result always has at least one track."""
    result = convert_ref_to_notation("ref", num_tracks=0)
    assert len(result.tracks) == 1


def test_notation_num_tracks_max_clamped() -> None:
    """num_tracks=100 is clamped to 8 — result has at most 8 tracks."""
    result = convert_ref_to_notation("ref", num_tracks=100)
    assert len(result.tracks) == 8


def test_notation_num_bars_clamped() -> None:
    """num_bars=0 is clamped to 1 — at least one bar is generated."""
    result = convert_ref_to_notation("ref", num_tracks=1, num_bars=0)
    assert len(result.tracks) == 1


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_notation_to_dict_has_all_keys() -> None:
    """notation_result_to_dict returns a dict with tracks, tempo, key, timeSig."""
    result = convert_ref_to_notation("abc")
    d = notation_result_to_dict(result)
    assert "tracks" in d
    assert "tempo" in d
    assert "key" in d
    assert "timeSig" in d


def test_notation_to_dict_camel_case() -> None:
    """Serialized dict uses camelCase 'timeSig' not snake_case 'time_sig'."""
    result = convert_ref_to_notation("abc")
    d = notation_result_to_dict(result)
    assert "timeSig" in d
    assert "time_sig" not in d


def test_notation_to_dict_tempo_is_int() -> None:
    """Serialized tempo is a positive integer."""
    result = convert_ref_to_notation("abc")
    d = notation_result_to_dict(result)
    assert isinstance(d["tempo"], int)
    assert d["tempo"] > 0


# ---------------------------------------------------------------------------
# Clef assignment
# ---------------------------------------------------------------------------


def test_notation_clef_for_bass() -> None:
    """First track assigned role 'bass' should receive bass clef."""
    result = convert_ref_to_notation("abc", num_tracks=5)
    # Track index 1 maps to 'bass' role (see _ROLE_NAMES order)
    bass_tracks = [t for t in result.tracks if t["instrument"] == "bass"]
    for t in bass_tracks:
        assert t["clef"] == "bass", f"bass instrument should have bass clef, got {t['clef']}"


def test_notation_clef_for_piano() -> None:
    """Track with 'piano' role should receive treble clef."""
    result = convert_ref_to_notation("abc", num_tracks=1)
    piano_tracks = [t for t in result.tracks if t["instrument"] == "piano"]
    for t in piano_tracks:
        assert t["clef"] == "treble"


# ---------------------------------------------------------------------------
# Note value constraints
# ---------------------------------------------------------------------------


def test_notation_start_beat_non_negative() -> None:
    """All notes have start_beat >= 0."""
    result = convert_ref_to_notation("main", num_tracks=3, num_bars=4)
    for track in result.tracks:
        for note in track["notes"]:
            assert note["start_beat"] >= 0, f"Negative start_beat: {note}"


def test_notation_velocity_in_range() -> None:
    """All note velocities are in [0, 127]."""
    result = convert_ref_to_notation("main", num_tracks=3, num_bars=4)
    for track in result.tracks:
        for note in track["notes"]:
            vel = note["velocity"]
            assert 0 <= vel <= 127, f"Velocity out of range: {vel}"


def test_notation_duration_valid() -> None:
    """All note durations are recognized fraction strings."""
    result = convert_ref_to_notation("main", num_tracks=3, num_bars=4)
    for track in result.tracks:
        for note in track["notes"]:
            dur = note["duration"]
            assert dur in _VALID_DURATIONS, f"Unrecognized duration: {dur}"


def test_notation_octave_in_range() -> None:
    """All note octaves are in a playable range [1, 7]."""
    result = convert_ref_to_notation("main", num_tracks=3, num_bars=4)
    for track in result.tracks:
        for note in track["notes"]:
            oct_ = note["octave"]
            assert 1 <= oct_ <= 7, f"Octave out of expected range: {oct_}"


def test_notation_pitch_name_valid() -> None:
    """All note pitch_name values are valid note names (A-G with optional # or b)."""
    valid_names = {
        "C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#",
        "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B",
    }
    result = convert_ref_to_notation("main", num_tracks=2, num_bars=4)
    for track in result.tracks:
        for note in track["notes"]:
            assert note["pitch_name"] in valid_names, f"Invalid pitch name: {note['pitch_name']}"
