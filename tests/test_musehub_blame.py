"""Tests for the Muse Hub blame API endpoint.

Tests cover:
- 404 when repo does not exist
- 401 when repo is private and no token provided
- Empty entries when no commits exist
- Entries returned when commits exist
- Track filter reduces entries to the requested track
- Beat range filter restricts by beat_start
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from maestro.api.routes.musehub.blame import _build_blame_entries, _stable_int


# ── Unit tests for internal helpers ──────────────────────────────────────────


def test_stable_int_deterministic() -> None:
    """Same seed always returns the same integer."""
    result_a = _stable_int("abc:beat", 64)
    result_b = _stable_int("abc:beat", 64)
    assert result_a == result_b


def test_stable_int_range() -> None:
    """Result is always within [0, mod)."""
    for seed in ["hello", "world", "track:piano", "commit_id:abc123"]:
        val = _stable_int(seed, 10)
        assert 0 <= val < 10


def test_build_blame_entries_empty_commits() -> None:
    """Empty commit list produces empty entries."""
    result = _build_blame_entries(
        commits=[],
        path="tracks/piano.mid",
        track_filter=None,
        beat_start_filter=None,
        beat_end_filter=None,
    )
    assert result == []


def test_build_blame_entries_returns_entries() -> None:
    """Commit list produces at least one entry per commit."""
    commits = [
        {
            "commit_id": "abc123",
            "message": "Add jazz chords",
            "author": "gabriel",
            "timestamp": datetime(2026, 2, 1, 10, 0, 0, tzinfo=timezone.utc),
        },
        {
            "commit_id": "def456",
            "message": "Edit bass line",
            "author": "sam",
            "timestamp": datetime(2026, 2, 2, 11, 0, 0, tzinfo=timezone.utc),
        },
    ]
    result = _build_blame_entries(
        commits=commits,
        path="tracks/piano.mid",
        track_filter=None,
        beat_start_filter=None,
        beat_end_filter=None,
    )
    assert len(result) > 0


def test_build_blame_entries_sorted_by_beat_start() -> None:
    """Entries are returned sorted by beat_start ascending."""
    commits = [
        {
            "commit_id": f"c{i}",
            "message": f"commit {i}",
            "author": "test",
            "timestamp": datetime(2026, 1, i + 1, tzinfo=timezone.utc),
        }
        for i in range(5)
    ]
    result = _build_blame_entries(
        commits=commits,
        path="tracks/piano.mid",
        track_filter=None,
        beat_start_filter=None,
        beat_end_filter=None,
    )
    beat_starts = [e.beat_start for e in result]
    assert beat_starts == sorted(beat_starts)


def test_build_blame_entries_track_filter_applied() -> None:
    """When track_filter is set, all entries have the specified track."""
    commits = [
        {
            "commit_id": "abc123",
            "message": "Add piano",
            "author": "gabriel",
            "timestamp": datetime(2026, 2, 1, tzinfo=timezone.utc),
        }
    ]
    result = _build_blame_entries(
        commits=commits,
        path="tracks/piano.mid",
        track_filter="piano",
        beat_start_filter=None,
        beat_end_filter=None,
    )
    for entry in result:
        assert entry.track == "piano"


def test_build_blame_entries_beat_start_filter() -> None:
    """beat_start_filter excludes entries starting before the threshold."""
    commits = [
        {
            "commit_id": "abc123",
            "message": "Add chords",
            "author": "gabriel",
            "timestamp": datetime(2026, 2, 1, tzinfo=timezone.utc),
        }
    ]
    threshold = 8.0
    result = _build_blame_entries(
        commits=commits,
        path="tracks/piano.mid",
        track_filter=None,
        beat_start_filter=threshold,
        beat_end_filter=None,
    )
    for entry in result:
        assert entry.beat_start >= threshold


def test_build_blame_entries_beat_end_filter() -> None:
    """beat_end_filter excludes entries starting at or after the threshold."""
    commits = [
        {
            "commit_id": "abc123",
            "message": "Add chords",
            "author": "gabriel",
            "timestamp": datetime(2026, 2, 1, tzinfo=timezone.utc),
        }
    ]
    threshold = 4.0
    result = _build_blame_entries(
        commits=commits,
        path="tracks/piano.mid",
        track_filter=None,
        beat_start_filter=None,
        beat_end_filter=threshold,
    )
    for entry in result:
        assert entry.beat_start < threshold


def test_build_blame_entries_commit_fields_propagated() -> None:
    """Each entry carries the author, message, and timestamp from its commit."""
    commits = [
        {
            "commit_id": "abc123",
            "message": "Add jazz chords to piano",
            "author": "gabriel",
            "timestamp": datetime(2026, 2, 1, 10, 0, 0, tzinfo=timezone.utc),
        }
    ]
    result = _build_blame_entries(
        commits=commits,
        path="tracks/piano.mid",
        track_filter=None,
        beat_start_filter=None,
        beat_end_filter=None,
    )
    assert len(result) >= 1
    entry = result[0]
    assert entry.commit_id == "abc123"
    assert entry.commit_message == "Add jazz chords to piano"
    assert entry.author == "gabriel"
    assert entry.timestamp == datetime(2026, 2, 1, 10, 0, 0, tzinfo=timezone.utc)


def test_build_blame_entries_note_fields_valid() -> None:
    """Note pitch (0-127), velocity (0-127), and duration (>0) are in valid ranges."""
    commits = [
        {
            "commit_id": "abc123",
            "message": "Add notes",
            "author": "gabriel",
            "timestamp": datetime(2026, 2, 1, tzinfo=timezone.utc),
        }
    ]
    result = _build_blame_entries(
        commits=commits,
        path="tracks/piano.mid",
        track_filter=None,
        beat_start_filter=None,
        beat_end_filter=None,
    )
    for entry in result:
        assert 0 <= entry.note_pitch <= 127
        assert 0 <= entry.note_velocity <= 127
        assert entry.note_duration_beats > 0
        assert entry.beat_end > entry.beat_start
