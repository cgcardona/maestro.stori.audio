"""Tests for the MIDI parsing pipeline.

Covers: parse_midi_to_notes, filter_channels_for_instruments,
_channels_to_keep, rejection_score.

These functions are the critical path between Orpheus output and Maestro input.
If they break, every generation silently produces garbage.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest
from midiutil import MIDIFile

from music_service import (
    parse_midi_to_notes,
    filter_channels_for_instruments,
    _channels_to_keep,
)
from quality_metrics import rejection_score


# =============================================================================
# Helpers
# =============================================================================


def _make_midi(
    notes: list[tuple[int, int, float, float, int]],
    tempo: int = 120,
    program_changes: dict[int, int] | None = None,
    cc_events: list[tuple[int, int, float, int]] | None = None,
    pitch_bends: list[tuple[int, float, int]] | None = None,
) -> str:
    """Create a temp MIDI file from (channel, pitch, start_beat, duration, velocity).

    Returns the file path.
    """
    channels = {n[0] for n in notes}
    if cc_events:
        channels.update(e[0] for e in cc_events)
    num_tracks = max(len(channels), 1)
    midi = MIDIFile(num_tracks)
    midi.addTempo(0, 0, tempo)

    if program_changes:
        for ch, prog in program_changes.items():
            track = 0
            midi.addProgramChange(track, ch, 0, prog)

    for ch, pitch, start, dur, vel in notes:
        midi.addNote(0, ch, pitch, start, dur, vel)

    if cc_events:
        for ch, cc_num, beat, value in cc_events:
            midi.addControllerEvent(0, ch, beat, cc_num, value)

    if pitch_bends:
        for ch, beat, value in pitch_bends:
            midi.addPitchWheelEvent(0, ch, beat, value)

    fd, path = tempfile.mkstemp(suffix=".mid")
    with os.fdopen(fd, "wb") as f:
        midi.writeFile(f)
    return path


# =============================================================================
# parse_midi_to_notes
# =============================================================================


class TestParseMidiToNotes:
    """Unit tests for MIDI file → note dict parsing."""

    def test_single_note(self) -> None:
        """Parse a single note."""
        path = _make_midi([(0, 60, 0.0, 1.0, 80)])
        result = parse_midi_to_notes(path, tempo=120)
        os.unlink(path)

        assert 0 in result["notes"]
        notes = result["notes"][0]
        assert len(notes) == 1
        assert notes[0]["pitch"] == 60
        assert notes[0]["velocity"] == 80

    def test_multiple_channels(self) -> None:
        """Notes on different channels are separated."""
        path = _make_midi([
            (0, 60, 0.0, 1.0, 80),
            (1, 64, 0.0, 1.0, 90),
            (9, 36, 0.0, 0.5, 100),
        ])
        result = parse_midi_to_notes(path, tempo=120)
        os.unlink(path)

        assert 0 in result["notes"]
        assert 1 in result["notes"]
        assert 9 in result["notes"]

    def test_program_changes_captured(self) -> None:
        """Program change messages are captured per channel."""
        path = _make_midi(
            [(0, 60, 0.0, 1.0, 80)],
            program_changes={0: 33, 1: 40},
        )
        result = parse_midi_to_notes(path, tempo=120)
        os.unlink(path)

        assert result["program_changes"].get(0) == 33
        assert result["program_changes"].get(1) == 40

    def test_cc_events_captured(self) -> None:
        """Control change events are parsed."""
        path = _make_midi(
            [(0, 60, 0.0, 1.0, 80)],
            cc_events=[(0, 64, 0.0, 127)],
        )
        result = parse_midi_to_notes(path, tempo=120)
        os.unlink(path)

        assert 0 in result["cc_events"]
        assert result["cc_events"][0][0]["cc"] == 64
        assert result["cc_events"][0][0]["value"] == 127

    def test_empty_result_structure(self) -> None:
        """An empty MIDI file returns the correct dict shape."""
        midi = MIDIFile(1)
        midi.addTempo(0, 0, 120)
        fd, path = tempfile.mkstemp(suffix=".mid")
        with os.fdopen(fd, "wb") as f:
            midi.writeFile(f)

        result = parse_midi_to_notes(path, tempo=120)
        os.unlink(path)

        assert "notes" in result
        assert "cc_events" in result
        assert "pitch_bends" in result
        assert "aftertouch" in result
        assert "program_changes" in result

    def test_note_duration_calculated(self) -> None:
        """Note duration is calculated from note_on/note_off delta."""
        path = _make_midi([(0, 60, 0.0, 2.0, 80)])
        result = parse_midi_to_notes(path, tempo=120)
        os.unlink(path)

        note = result["notes"][0][0]
        assert note["duration_beats"] > 0


# =============================================================================
# _channels_to_keep
# =============================================================================


class TestChannelsToKeep:
    """Unit tests for channel selection logic."""

    def test_empty_instruments_keeps_all(self) -> None:
        """No instrument filter → keep all channels."""
        channels = {0, 1, 9}
        assert _channels_to_keep(channels, []) == channels

    def test_drums_keeps_channel_9(self) -> None:
        """Requesting drums keeps channel 9."""
        channels = {0, 1, 9}
        result = _channels_to_keep(channels, ["drums"])
        assert 9 in result

    def test_bass_keeps_melodic_channel_0(self) -> None:
        """Bass (melodic index 0) maps to first melodic channel."""
        channels = {0, 1, 9}
        result = _channels_to_keep(channels, ["bass"])
        assert 0 in result

    def test_piano_keeps_melodic_channel_1(self) -> None:
        """Piano (melodic index 1) maps to second melodic channel."""
        channels = {0, 1, 2, 9}
        result = _channels_to_keep(channels, ["piano"])
        assert 1 in result

    def test_fallback_when_preferred_channel_missing(self) -> None:
        """When preferred melodic index doesn't exist, falls back gracefully."""
        channels = {0, 9}
        result = _channels_to_keep(channels, ["guitar"])
        assert len(result) > 0
        assert 9 not in result or "drums" in ["guitar"]

    def test_multiple_instruments(self) -> None:
        """Multiple instruments select multiple channels."""
        channels = {0, 1, 9}
        result = _channels_to_keep(channels, ["drums", "bass"])
        assert 9 in result
        assert 0 in result

    def test_empty_result_falls_back_to_all(self) -> None:
        """If filtering produces empty set, return all channels."""
        channels = {0, 1}
        result = _channels_to_keep(channels, ["xyzzy_nonexistent"])
        assert len(result) > 0


# =============================================================================
# filter_channels_for_instruments
# =============================================================================


class TestFilterChannelsForInstruments:
    """Integration tests for parsed-dict filtering."""

    def test_filters_notes_by_instrument(self) -> None:
        """Only notes on kept channels survive filtering."""
        parsed: dict[str, Any] = {
            "notes": {0: [{"pitch": 60}], 1: [{"pitch": 64}], 9: [{"pitch": 36}]},
            "cc_events": {},
            "pitch_bends": {},
            "aftertouch": {},
        }
        result = filter_channels_for_instruments(parsed, ["drums"])
        assert 9 in result["notes"]
        assert 0 not in result["notes"]
        assert 1 not in result["notes"]

    def test_preserves_all_sub_dicts(self) -> None:
        """Filtering preserves the dict shape for all event types."""
        parsed: dict[str, Any] = {
            "notes": {0: [{"pitch": 60}]},
            "cc_events": {0: [{"cc": 64, "beat": 0, "value": 127}]},
            "pitch_bends": {0: [{"beat": 0, "value": 0}]},
            "aftertouch": {0: [{"beat": 0, "value": 64}]},
        }
        result = filter_channels_for_instruments(parsed, ["bass"])
        for key in ("notes", "cc_events", "pitch_bends", "aftertouch"):
            assert key in result


# =============================================================================
# rejection_score
# =============================================================================


class TestRejectionScore:
    """Unit tests for the fast rejection sampling scorer."""

    def test_empty_notes_returns_zero(self) -> None:
        assert rejection_score([], bars=4) == 0.0

    def test_good_generation_scores_high(self) -> None:
        """A musically reasonable generation scores above 0.5."""
        notes = [
            {"pitch": 60 + (i % 12), "startBeat": float(i * 0.5), "velocity": 80}
            for i in range(32)
        ]
        score = rejection_score(notes, bars=4)
        assert score > 0.5

    def test_single_repeated_note_scores_lower(self) -> None:
        """Degenerate single-note repetition scores lower than varied notes."""
        varied = [
            {"pitch": 60 + (i % 7), "startBeat": float(i * 0.5), "velocity": 80}
            for i in range(32)
        ]
        repeated = [
            {"pitch": 60, "startBeat": float(i * 0.5), "velocity": 80}
            for i in range(32)
        ]
        assert rejection_score(varied, bars=4) > rejection_score(repeated, bars=4)

    def test_score_in_0_1_range(self) -> None:
        """Score is always between 0 and 1."""
        for note_count in (1, 4, 16, 64, 256):
            notes = [
                {"pitch": 60 + (i % 24), "startBeat": float(i * 0.25), "velocity": 80}
                for i in range(note_count)
            ]
            score = rejection_score(notes, bars=max(note_count // 8, 1))
            assert 0.0 <= score <= 1.0

    def test_sparse_bars_penalized(self) -> None:
        """Notes clustered in one bar are penalized vs evenly distributed."""
        clustered = [
            {"pitch": 60 + i, "startBeat": float(i * 0.1), "velocity": 80}
            for i in range(16)
        ]
        distributed = [
            {"pitch": 60 + i, "startBeat": float(i), "velocity": 80}
            for i in range(16)
        ]
        assert rejection_score(distributed, bars=4) >= rejection_score(clustered, bars=4)


# =============================================================================
# Fuzzy cache lookup
# =============================================================================


class TestFuzzyCache:
    """Tests for fuzzy (epsilon) cache matching."""

    def setup_method(self) -> None:
        from music_service import _result_cache
        _result_cache.clear()

    def test_exact_match_returns_result(self) -> None:
        from music_service import (
            cache_result, fuzzy_cache_lookup, get_cache_key,
            GenerateRequest, _cache_key_data, EmotionVectorPayload,
        )
        req = GenerateRequest(
            genre="trap", tempo=140, instruments=["drums", "bass"],
            bars=4, emotion_vector=EmotionVectorPayload(valence=-0.5),
        )
        cache_result(
            get_cache_key(req),
            {"success": True, "tool_calls": [], "metadata": {}},
            key_data=_cache_key_data(req),
        )
        result = fuzzy_cache_lookup(req)
        assert result is not None
        assert result["success"] is True

    def test_near_miss_returns_approximate(self) -> None:
        from music_service import (
            cache_result, fuzzy_cache_lookup, get_cache_key,
            GenerateRequest, _cache_key_data, EmotionVectorPayload,
        )
        req1 = GenerateRequest(
            genre="trap", tempo=140, instruments=["drums", "bass"],
            bars=4, emotion_vector=EmotionVectorPayload(valence=-0.5, energy=0.5),
        )
        cache_result(
            get_cache_key(req1),
            {"success": True, "tool_calls": [], "metadata": {"original": True}},
            key_data=_cache_key_data(req1),
        )
        req2 = GenerateRequest(
            genre="trap", tempo=140, instruments=["drums", "bass"],
            bars=4, emotion_vector=EmotionVectorPayload(valence=-0.1, energy=0.8),
        )
        result = fuzzy_cache_lookup(req2, epsilon=1.0)
        assert result is not None
        assert result.get("metadata", {}).get("approximate") is True

    def test_different_genre_no_match(self) -> None:
        from music_service import (
            cache_result, fuzzy_cache_lookup, get_cache_key,
            GenerateRequest, _cache_key_data,
        )
        req1 = GenerateRequest(genre="trap", tempo=140, instruments=["drums"])
        cache_result(
            get_cache_key(req1),
            {"success": True, "tool_calls": [], "metadata": {}},
            key_data=_cache_key_data(req1),
        )
        req2 = GenerateRequest(genre="jazz", tempo=140, instruments=["drums"])
        result = fuzzy_cache_lookup(req2)
        assert result is None

    def test_empty_cache_returns_none(self) -> None:
        from music_service import fuzzy_cache_lookup, GenerateRequest
        req = GenerateRequest(genre="trap", tempo=140)
        assert fuzzy_cache_lookup(req) is None


# =============================================================================
# Cache disk persistence
# =============================================================================


class TestCachePersistence:
    """Tests for save/load cache to disk."""

    def setup_method(self) -> None:
        from music_service import _result_cache
        _result_cache.clear()

    def test_save_and_load_roundtrip(self, tmp_path: Any) -> None:
        from music_service import (
            _result_cache, _save_cache_to_disk, _load_cache_from_disk,
            cache_result, _CACHE_FILE, CacheEntry,
        )
        import music_service

        original_cache_file = music_service._CACHE_FILE
        music_service._CACHE_FILE = tmp_path / "cache.json"
        try:
            cache_result("test_key", {"success": True, "tool_calls": []})
            _save_cache_to_disk()

            _result_cache.clear()
            assert len(_result_cache) == 0

            loaded = _load_cache_from_disk()
            assert loaded == 1
            assert "test_key" in _result_cache
        finally:
            music_service._CACHE_FILE = original_cache_file

    def test_load_skips_expired_entries(self, tmp_path: Any) -> None:
        from music_service import (
            _result_cache, _save_cache_to_disk, _load_cache_from_disk,
            CacheEntry, CACHE_TTL_SECONDS,
        )
        import music_service
        from time import time

        original_cache_file = music_service._CACHE_FILE
        music_service._CACHE_FILE = tmp_path / "cache.json"
        try:
            _result_cache["expired_key"] = CacheEntry(
                result={"data": 1},
                timestamp=time() - CACHE_TTL_SECONDS - 100,
                hits=0,
            )
            _save_cache_to_disk()

            _result_cache.clear()
            loaded = _load_cache_from_disk()
            assert loaded == 0
        finally:
            music_service._CACHE_FILE = original_cache_file
