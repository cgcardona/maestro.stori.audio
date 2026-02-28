"""Tests for the Muse Motif Engine (muse_motif.py) and CLI commands (motif.py).

Covers:
- Pure fingerprint helpers: pitches_to_intervals, invert_intervals,
  retrograde_intervals, detect_transformation, contour_label, parse_pitch_string.
- Async service functions: find_motifs, track_motif, diff_motifs, list_motifs.
- CLI command rendering helpers: _format_find, _format_track, _format_diff,
  _format_list.

All async tests use @pytest.mark.anyio.  No live DB or external API calls.
"""
from __future__ import annotations

import json

import pytest

from maestro.muse_cli.commands.motif import (
    _format_diff,
    _format_find,
    _format_list,
    _format_track,
)
from maestro.services.muse_motif import (
    IntervalSequence,
    MotifTransformation,
    contour_label,
    detect_transformation,
    diff_motifs,
    find_motifs,
    invert_intervals,
    list_motifs,
    parse_pitch_string,
    pitches_to_intervals,
    retrograde_intervals,
    track_motif,
)


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


class TestPitchesToIntervals:
    def test_basic_ascending(self) -> None:
        pitches = (60, 62, 64, 67)
        intervals = pitches_to_intervals(pitches)
        assert intervals == (2, 2, 3)

    def test_basic_descending(self) -> None:
        pitches = (67, 65, 62, 60)
        intervals = pitches_to_intervals(pitches)
        assert intervals == (-2, -3, -2)

    def test_single_note_returns_empty(self) -> None:
        assert pitches_to_intervals((60,)) == ()

    def test_empty_returns_empty(self) -> None:
        assert pitches_to_intervals(()) == ()

    def test_two_notes_returns_one_interval(self) -> None:
        assert pitches_to_intervals((60, 62)) == (2,)

    def test_transposition_invariant(self) -> None:
        """Motif at C and motif at G should produce identical fingerprints."""
        c_major = (60, 62, 64)
        g_major = (67, 69, 71)
        assert pitches_to_intervals(c_major) == pitches_to_intervals(g_major)


class TestInvertIntervals:
    def test_inversion_negates_all(self) -> None:
        intervals: IntervalSequence = (2, 2, -1, 2)
        assert invert_intervals(intervals) == (-2, -2, 1, -2)

    def test_inversion_of_empty(self) -> None:
        assert invert_intervals(()) == ()

    def test_double_inversion_identity(self) -> None:
        intervals: IntervalSequence = (3, -2, 1)
        assert invert_intervals(invert_intervals(intervals)) == intervals


class TestRetrogradeIntervals:
    def test_retrograde_reverses_and_negates(self) -> None:
        """Retrograde of interval sequence = negation of reversed sequence."""
        intervals: IntervalSequence = (2, 2, -1)
        assert retrograde_intervals(intervals) == (1, -2, -2)

    def test_retrograde_of_symmetric(self) -> None:
        """A palindromic interval sequence reversed is its own negation."""
        intervals: IntervalSequence = (1, -1)
        retro = retrograde_intervals(intervals)
        assert retro == (1, -1)

    def test_double_retrograde_identity(self) -> None:
        intervals: IntervalSequence = (2, -3, 1)
        assert retrograde_intervals(retrograde_intervals(intervals)) == intervals


class TestDetectTransformation:
    def test_exact_match(self) -> None:
        iv: IntervalSequence = (2, 2, -1, 2)
        assert detect_transformation(iv, iv) == MotifTransformation.EXACT

    def test_inversion_detected(self) -> None:
        query: IntervalSequence = (2, 2, -1, 2)
        candidate = invert_intervals(query)
        assert detect_transformation(query, candidate) == MotifTransformation.INVERSION

    def test_retrograde_detected(self) -> None:
        query: IntervalSequence = (2, 2, -1, 2)
        candidate = retrograde_intervals(query)
        assert detect_transformation(query, candidate) == MotifTransformation.RETROGRADE

    def test_retro_inv_detected(self) -> None:
        query: IntervalSequence = (2, 2, -1, 2)
        candidate = invert_intervals(retrograde_intervals(query))
        assert detect_transformation(query, candidate) == MotifTransformation.RETRO_INV

    def test_unrelated_returns_none(self) -> None:
        query: IntervalSequence = (2, 2, -1, 2)
        candidate: IntervalSequence = (5, -3, 7)
        assert detect_transformation(query, candidate) is None


class TestContourLabel:
    def test_ascending_step(self) -> None:
        assert contour_label((1, 2, 1)) == "ascending-step"

    def test_descending_step(self) -> None:
        assert contour_label((-1, -2, -1)) == "descending-step"

    def test_ascending_leap(self) -> None:
        assert contour_label((4, 5, 3)) == "ascending-leap"

    def test_descending_leap(self) -> None:
        assert contour_label((-4, -5, -3)) == "descending-leap"

    def test_arch_shape(self) -> None:
        assert contour_label((3, 2, -2, -3)) == "arch"

    def test_valley_shape(self) -> None:
        assert contour_label((-3, -2, 2, 3)) == "valley"

    def test_static_zero_intervals(self) -> None:
        assert contour_label((0, 0, 0)) == "static"

    def test_empty_is_static(self) -> None:
        assert contour_label(()) == "static"

    def test_oscillating(self) -> None:
        assert contour_label((2, -2, 2, -2)) == "oscillating"


class TestParsePitchString:
    def test_midi_numbers(self) -> None:
        assert parse_pitch_string("60 62 64 67") == (60, 62, 64, 67)

    def test_note_names_c_major(self) -> None:
        result = parse_pitch_string("C D E G")
        assert result == (60, 62, 64, 67)

    def test_note_names_with_sharp(self) -> None:
        result = parse_pitch_string("C C# D")
        assert result == (60, 61, 62)

    def test_mixed_case_note_names(self) -> None:
        result = parse_pitch_string("c d e g")
        assert result == (60, 62, 64, 67)

    def test_invalid_token_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse pitch token"):
            parse_pitch_string("C D XQ")

    def test_out_of_range_midi_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_pitch_string("200")

    def test_single_note(self) -> None:
        assert parse_pitch_string("60") == (60,)


# ---------------------------------------------------------------------------
# Async service tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_motif_find_detects_recurring_pattern() -> None:
    """find_motifs returns a result with at least one motif group above min-length."""
    result = await find_motifs(
        commit_id="abc12345",
        branch="main",
        min_length=3,
    )
    assert result.commit_id == "abc12345"
    assert result.branch == "main"
    assert result.min_length == 3
    assert result.total_found > 0
    assert len(result.motifs) == result.total_found


@pytest.mark.anyio
async def test_motif_find_min_length_filter() -> None:
    """Increasing min-length reduces (or equals) the number of detected motifs."""
    result_short = await find_motifs(
        commit_id="deadbeef",
        branch="main",
        min_length=2,
    )
    result_long = await find_motifs(
        commit_id="deadbeef",
        branch="main",
        min_length=8,
    )
    assert result_long.total_found <= result_short.total_found


@pytest.mark.anyio
async def test_motif_find_track_filter_respected() -> None:
    """find_motifs with a track filter propagates the track name to occurrences."""
    result = await find_motifs(
        commit_id="abc12345",
        branch="main",
        min_length=3,
        track="bass",
    )
    for group in result.motifs:
        for occ in group.occurrences:
            assert occ.track == "bass"


@pytest.mark.anyio
async def test_motif_find_source_is_stub() -> None:
    """Stub implementation always returns source='stub'."""
    result = await find_motifs(commit_id="abc12345", branch="main", min_length=3)
    assert result.source == "stub"


@pytest.mark.anyio
async def test_motif_find_motifs_sorted_by_count_descending() -> None:
    """Motif groups are sorted by occurrence count, highest first."""
    result = await find_motifs(commit_id="abc12345", branch="main", min_length=3)
    counts = [g.count for g in result.motifs]
    assert counts == sorted(counts, reverse=True)


@pytest.mark.anyio
async def test_motif_track_finds_transpositions() -> None:
    """track_motif parses the pattern and returns a MotifTrackResult."""
    result = await track_motif(pattern="C D E G", commit_ids=["abc12345"])
    assert result.pattern == "C D E G"
    assert result.fingerprint == (2, 2, 3)
    assert result.total_commits_scanned == 1
    assert len(result.occurrences) == 1


@pytest.mark.anyio
async def test_motif_track_empty_commit_list() -> None:
    """track_motif with no commits returns an empty occurrence list."""
    result = await track_motif(pattern="60 62 64", commit_ids=[])
    assert result.total_commits_scanned == 0
    assert len(result.occurrences) == 0


@pytest.mark.anyio
async def test_motif_track_multiple_commits() -> None:
    """track_motif scans all provided commit IDs."""
    commit_ids = ["aaa11111", "bbb22222", "ccc33333"]
    result = await track_motif(pattern="C D E", commit_ids=commit_ids)
    assert result.total_commits_scanned == 3
    assert len(result.occurrences) == 3
    found_ids = {occ.commit_id for occ in result.occurrences}
    assert found_ids == {"aaa11111"[:8], "bbb22222"[:8], "ccc33333"[:8]}


@pytest.mark.anyio
async def test_motif_track_invalid_pattern_raises() -> None:
    """track_motif raises ValueError for an unparseable pattern."""
    with pytest.raises(ValueError):
        await track_motif(pattern="C D XYZ", commit_ids=["abc"])


@pytest.mark.anyio
async def test_motif_diff_identifies_inversion() -> None:
    """diff_motifs returns a MotifDiffResult with a valid transformation."""
    result = await diff_motifs(commit_a_id="aaa11111", commit_b_id="bbb22222")
    assert result.commit_a.commit_id == "aaa11111"[:8]
    assert result.commit_b.commit_id == "bbb22222"[:8]
    assert result.transformation in MotifTransformation.__members__.values()
    assert result.description


@pytest.mark.anyio
async def test_motif_diff_source_is_stub() -> None:
    result = await diff_motifs(commit_a_id="aaa", commit_b_id="bbb")
    assert result.source == "stub"


@pytest.mark.anyio
async def test_motif_list_returns_named_motifs() -> None:
    """list_motifs returns a MotifListResult with named motif entries."""
    result = await list_motifs(muse_dir_path="/tmp/fake-muse")
    assert len(result.motifs) > 0
    names = {m.name for m in result.motifs}
    assert "main-theme" in names


@pytest.mark.anyio
async def test_motif_list_source_is_stub() -> None:
    result = await list_motifs(muse_dir_path="/tmp/fake-muse")
    assert result.source == "stub"


@pytest.mark.anyio
async def test_motif_list_fingerprints_are_non_empty() -> None:
    result = await list_motifs(muse_dir_path="/tmp/fake-muse")
    for motif in result.motifs:
        assert len(motif.fingerprint) > 0


# ---------------------------------------------------------------------------
# Formatter / rendering tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_format_find_text_output() -> None:
    """_format_find produces non-empty tabular text."""
    result = await find_motifs(commit_id="abc12345", branch="main", min_length=3)
    text = _format_find(result, as_json=False)
    assert "Recurring motifs" in text
    assert "ascending" in text or "descending" in text or "arch" in text


@pytest.mark.anyio
async def test_format_find_json_output_valid() -> None:
    """_format_find with as_json=True produces parseable JSON."""
    result = await find_motifs(commit_id="abc12345", branch="main", min_length=3)
    raw = _format_find(result, as_json=True)
    parsed = json.loads(raw)
    assert "motifs" in parsed
    assert parsed["total_found"] == result.total_found


@pytest.mark.anyio
async def test_format_track_text_output() -> None:
    result = await track_motif(pattern="C D E G", commit_ids=["abc12345"])
    text = _format_track(result, as_json=False)
    assert "Tracking motif" in text


@pytest.mark.anyio
async def test_format_track_json_output_valid() -> None:
    result = await track_motif(pattern="C D E G", commit_ids=["abc12345"])
    raw = _format_track(result, as_json=True)
    parsed = json.loads(raw)
    assert "fingerprint" in parsed
    assert "occurrences" in parsed


@pytest.mark.anyio
async def test_format_diff_text_output() -> None:
    result = await diff_motifs(commit_a_id="aaa11111", commit_b_id="bbb22222")
    text = _format_diff(result, as_json=False)
    assert "Motif diff" in text
    assert "Transformation" in text


@pytest.mark.anyio
async def test_format_diff_json_output_valid() -> None:
    result = await diff_motifs(commit_a_id="aaa11111", commit_b_id="bbb22222")
    raw = _format_diff(result, as_json=True)
    parsed = json.loads(raw)
    assert "transformation" in parsed
    assert "commit_a" in parsed
    assert "commit_b" in parsed


@pytest.mark.anyio
async def test_format_list_text_output() -> None:
    result = await list_motifs(muse_dir_path="/tmp/fake-muse")
    text = _format_list(result, as_json=False)
    assert "main-theme" in text


@pytest.mark.anyio
async def test_format_list_json_output_valid() -> None:
    result = await list_motifs(muse_dir_path="/tmp/fake-muse")
    raw = _format_list(result, as_json=True)
    parsed = json.loads(raw)
    assert "motifs" in parsed
    assert len(parsed["motifs"]) > 0
