"""Tests for ``muse swing`` — swing factor analysis and annotation.

Covers:
- swing_label thresholds (Straight / Light / Medium / Hard)
- _swing_detect_async returns correct schema
- _swing_history_async returns a list with correct entries
- _swing_compare_async returns head/compare/delta structure
- Output formatters for text and JSON modes
- CLI flag parsing via CliRunner (--set, --detect, --track, --compare, --history, --json)
- --set out-of-range exits 1
- Outside-repo invocation exits 2
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from typer.testing import CliRunner

from maestro.muse_cli.commands.swing import (
    FACTOR_MAX,
    FACTOR_MIN,
    LIGHT_MAX,
    MEDIUM_MAX,
    STRAIGHT_MAX,
    SwingCompareResult,
    SwingDetectResult,
    _format_compare,
    _format_detect,
    _format_history,
    _swing_compare_async,
    _swing_detect_async,
    _swing_history_async,
    swing_label,
)
from maestro.muse_cli.errors import ExitCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path) -> str:
    """Create a minimal .muse/ layout and return the repo_id."""
    rid = str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": rid, "schema_version": "1"})
    )
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("abc1234")
    return rid


# ---------------------------------------------------------------------------
# swing_label — threshold tests
# ---------------------------------------------------------------------------


def test_swing_label_straight_below_threshold() -> None:
    """Factor below STRAIGHT_MAX maps to 'Straight'."""
    assert swing_label(FACTOR_MIN) == "Straight"
    assert swing_label(STRAIGHT_MAX - 0.001) == "Straight"


def test_swing_label_light_range() -> None:
    """Factor in [STRAIGHT_MAX, LIGHT_MAX) maps to 'Light'."""
    assert swing_label(STRAIGHT_MAX) == "Light"
    assert swing_label((STRAIGHT_MAX + LIGHT_MAX) / 2) == "Light"
    assert swing_label(LIGHT_MAX - 0.001) == "Light"


def test_swing_label_medium_range() -> None:
    """Factor in [LIGHT_MAX, MEDIUM_MAX) maps to 'Medium'."""
    assert swing_label(LIGHT_MAX) == "Medium"
    assert swing_label((LIGHT_MAX + MEDIUM_MAX) / 2) == "Medium"
    assert swing_label(MEDIUM_MAX - 0.001) == "Medium"


def test_swing_label_hard_at_and_above_medium_max() -> None:
    """Factor >= MEDIUM_MAX maps to 'Hard'."""
    assert swing_label(MEDIUM_MAX) == "Hard"
    assert swing_label(FACTOR_MAX) == "Hard"


def test_swing_label_boundary_straight_exact() -> None:
    """STRAIGHT_MAX boundary belongs to 'Light', not 'Straight'."""
    assert swing_label(STRAIGHT_MAX) == "Light"


def test_swing_label_boundary_light_exact() -> None:
    """LIGHT_MAX boundary belongs to 'Medium', not 'Light'."""
    assert swing_label(LIGHT_MAX) == "Medium"


def test_swing_label_boundary_medium_exact() -> None:
    """MEDIUM_MAX boundary belongs to 'Hard', not 'Medium'."""
    assert swing_label(MEDIUM_MAX) == "Hard"


# ---------------------------------------------------------------------------
# _swing_detect_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_swing_detect_returns_correct_schema(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """_swing_detect_async returns a dict with all required keys."""
    _init_muse_repo(tmp_path)
    result = await _swing_detect_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit=None,
        track=None,
    )
    assert "factor" in result
    assert "label" in result
    assert "commit" in result
    assert "branch" in result
    assert "track" in result
    assert "source" in result


@pytest.mark.anyio
async def test_swing_detect_factor_in_valid_range(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Detected factor is within [FACTOR_MIN, FACTOR_MAX]."""
    _init_muse_repo(tmp_path)
    result = await _swing_detect_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit=None,
        track=None,
    )
    factor = result["factor"]
    assert FACTOR_MIN <= factor <= FACTOR_MAX


@pytest.mark.anyio
async def test_swing_detect_label_matches_factor(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """The label in the result is consistent with the factor."""
    _init_muse_repo(tmp_path)
    result = await _swing_detect_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit=None,
        track=None,
    )
    factor = result["factor"]
    assert result["label"] == swing_label(factor)


@pytest.mark.anyio
async def test_swing_detect_explicit_commit_reflected(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """When a commit SHA is given, it appears in the result."""
    _init_muse_repo(tmp_path)
    result = await _swing_detect_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit="deadbeef",
        track=None,
    )
    assert result["commit"] == "deadbeef"


@pytest.mark.anyio
async def test_swing_detect_track_reflected(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """When a track filter is given, it appears in the result."""
    _init_muse_repo(tmp_path)
    result = await _swing_detect_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit=None,
        track="bass",
    )
    assert result["track"] == "bass"


@pytest.mark.anyio
async def test_swing_detect_no_track_defaults_to_all(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """When no track is given, track defaults to 'all'."""
    _init_muse_repo(tmp_path)
    result = await _swing_detect_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit=None,
        track=None,
    )
    assert result["track"] == "all"


# ---------------------------------------------------------------------------
# _swing_history_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_swing_history_returns_list(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """_swing_history_async returns a non-empty list."""
    _init_muse_repo(tmp_path)
    entries = await _swing_history_async(
        root=tmp_path, session=muse_cli_db_session, track=None
    )
    assert isinstance(entries, list)
    assert len(entries) >= 1


@pytest.mark.anyio
async def test_swing_history_entries_have_correct_keys(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Each entry in the history list has the expected keys."""
    _init_muse_repo(tmp_path)
    entries = await _swing_history_async(
        root=tmp_path, session=muse_cli_db_session, track=None
    )
    for entry in entries:
        assert "factor" in entry
        assert "label" in entry
        assert "commit" in entry


# ---------------------------------------------------------------------------
# _swing_compare_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_swing_compare_returns_head_compare_delta(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """_swing_compare_async returns a dict with head, compare, and delta."""
    _init_muse_repo(tmp_path)
    result = await _swing_compare_async(
        root=tmp_path,
        session=muse_cli_db_session,
        compare_commit="abc123",
        track=None,
    )
    assert "head" in result
    assert "compare" in result
    assert "delta" in result


@pytest.mark.anyio
async def test_swing_compare_delta_is_numeric(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """The delta field is a finite float."""
    _init_muse_repo(tmp_path)
    result = await _swing_compare_async(
        root=tmp_path,
        session=muse_cli_db_session,
        compare_commit="abc123",
        track=None,
    )
    delta = result["delta"]
    assert isinstance(delta, float)


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _make_detect_result(
    factor: float = 0.55,
    label: str = "Light",
    commit: str = "abc1234",
    branch: str = "main",
    track: str = "all",
    source: str = "stub",
) -> SwingDetectResult:
    return SwingDetectResult(
        factor=factor,
        label=label,
        commit=commit,
        branch=branch,
        track=track,
        source=source,
    )


def test_format_detect_text_contains_factor_and_label() -> None:
    """Text format for detect includes factor and label strings."""
    output = _format_detect(_make_detect_result(), as_json=False)
    assert "0.55" in output
    assert "Light" in output


def test_format_detect_json_is_valid() -> None:
    """JSON format for detect parses cleanly."""
    output = _format_detect(_make_detect_result(), as_json=True)
    parsed = json.loads(output)
    assert parsed["factor"] == 0.55
    assert parsed["label"] == "Light"


def test_format_history_text_contains_commit_and_label() -> None:
    """Text format for history includes commit SHA and label."""
    entries = [_make_detect_result(commit="deadbeef")]
    output = _format_history(entries, as_json=False)
    assert "deadbeef" in output
    assert "Light" in output


def test_format_history_empty_list_shows_placeholder() -> None:
    """Empty history list shows a human-readable placeholder."""
    output = _format_history([], as_json=False)
    assert "no swing history" in output


def test_format_history_json_is_valid() -> None:
    """JSON format for history parses cleanly."""
    entries = [_make_detect_result(commit="deadbeef")]
    output = _format_history(entries, as_json=True)
    parsed = json.loads(output)
    assert isinstance(parsed, list)
    assert parsed[0]["label"] == "Light"


def test_format_compare_text_shows_delta_sign() -> None:
    """Positive delta is prefixed with '+' in text mode."""
    result = SwingCompareResult(
        head=_make_detect_result(factor=0.57),
        compare=_make_detect_result(factor=0.55),
        delta=0.02,
    )
    output = _format_compare(result, as_json=False)
    assert "+0.02" in output


def test_format_compare_negative_delta_no_plus_sign() -> None:
    """Negative delta has no '+' prefix."""
    result = SwingCompareResult(
        head=_make_detect_result(factor=0.55),
        compare=_make_detect_result(factor=0.57),
        delta=-0.02,
    )
    output = _format_compare(result, as_json=False)
    assert "-0.02" in output
    assert "+-0.02" not in output


def test_format_compare_json_is_valid() -> None:
    """JSON format for compare parses cleanly."""
    result = SwingCompareResult(
        head=_make_detect_result(factor=0.57),
        compare=_make_detect_result(factor=0.55),
        delta=0.02,
    )
    output = _format_compare(result, as_json=True)
    parsed = json.loads(output)
    assert "delta" in parsed
    assert parsed["delta"] == 0.02


# ---------------------------------------------------------------------------
# CLI flag parsing via CliRunner
# ---------------------------------------------------------------------------


def _make_repo(root: pathlib.Path) -> pathlib.Path:
    """Create a muse repo and return the root."""
    _init_muse_repo(root)
    return root


def test_swing_cli_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse swing`` exits 2 when not inside a Muse repository."""
    from maestro.muse_cli.app import cli

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["swing"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.REPO_NOT_FOUND)


def test_swing_cli_set_valid_annotates(tmp_path: pathlib.Path) -> None:
    """``muse swing --set 0.6`` succeeds and echoes the annotation."""
    from maestro.muse_cli.app import cli

    _init_muse_repo(tmp_path)
    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["swing", "--set", "0.6"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert "0.6" in result.output


def test_swing_cli_set_out_of_range_exits_1(tmp_path: pathlib.Path) -> None:
    """``muse swing --set`` with a value outside [0.5, 0.67] exits 1."""
    from maestro.muse_cli.app import cli

    _init_muse_repo(tmp_path)
    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["swing", "--set", "0.9"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.USER_ERROR)


def test_swing_cli_set_below_minimum_exits_1(tmp_path: pathlib.Path) -> None:
    """``muse swing --set 0.3`` (below minimum) exits 1."""
    from maestro.muse_cli.app import cli

    _init_muse_repo(tmp_path)
    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["swing", "--set", "0.3"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.USER_ERROR)


def test_swing_cli_json_flag_emits_valid_json(tmp_path: pathlib.Path) -> None:
    """``muse swing --json`` emits valid JSON output."""
    from maestro.muse_cli.app import cli

    _init_muse_repo(tmp_path)
    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["swing", "--json"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert "factor" in parsed
    assert "label" in parsed


def test_swing_cli_history_flag_succeeds(tmp_path: pathlib.Path) -> None:
    """``muse swing --history`` exits 0 and emits output."""
    from maestro.muse_cli.app import cli

    _init_muse_repo(tmp_path)
    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["swing", "--history"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert len(result.output.strip()) > 0


def test_swing_cli_compare_flag_succeeds(tmp_path: pathlib.Path) -> None:
    """``muse swing --compare abc123`` exits 0 and shows HEAD/Compare/Delta."""
    from maestro.muse_cli.app import cli

    _init_muse_repo(tmp_path)
    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli, ["swing", "--compare", "abc123"], catch_exceptions=False
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    output = result.output
    assert "HEAD" in output
    assert "Compare" in output
    assert "Delta" in output


def test_swing_cli_track_flag_reflected_in_set_output(tmp_path: pathlib.Path) -> None:
    """``muse swing --set 0.6 --track bass`` includes track name in output."""
    from maestro.muse_cli.app import cli

    _init_muse_repo(tmp_path)
    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli, ["swing", "--set", "0.6", "--track", "bass"], catch_exceptions=False
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert "bass" in result.output


def test_swing_cli_set_json_combined(tmp_path: pathlib.Path) -> None:
    """``muse swing --set 0.6 --json`` emits JSON with the annotation factor."""
    from maestro.muse_cli.app import cli

    _init_muse_repo(tmp_path)
    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli, ["swing", "--set", "0.6", "--json"], catch_exceptions=False
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["factor"] == 0.6
    assert parsed["label"] == swing_label(0.6)
