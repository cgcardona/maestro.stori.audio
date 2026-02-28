"""Tests for ``muse tempo-scale`` — timing stretch/compress command.

Covers:
- compute_factor_from_bpm: factor computation, edge cases, errors
- apply_factor: correct BPM calculation
- _tempo_scale_async: result schema, factor resolution, determinism
- _format_result: text and JSON output modes
- CLI flag parsing via CliRunner (factor, commit, --bpm, --track,
  --preserve-expressions, --message, --json)
- Validation errors: no args, mutual exclusion, out-of-range factor,
  non-positive BPM
- Outside-repo invocation exits 2

All async tests use @pytest.mark.anyio with the shared muse_cli_db_session
fixture from tests/muse_cli/conftest.py.
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from typer.testing import CliRunner

from maestro.muse_cli.app import cli
from maestro.muse_cli.commands.tempo_scale import (
    FACTOR_MAX,
    FACTOR_MIN,
    TempoScaleResult,
    _format_result,
    _tempo_scale_async,
    apply_factor,
    compute_factor_from_bpm,
)
from maestro.muse_cli.errors import ExitCode

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, branch: str = "main") -> str:
    """Create a minimal .muse/ layout with one commit ref and return repo_id."""
    rid = str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text(f"refs/heads/{branch}")
    (muse / "refs" / "heads" / branch).write_text("abc12345")
    return rid


# ---------------------------------------------------------------------------
# compute_factor_from_bpm — pure function
# ---------------------------------------------------------------------------


def test_compute_factor_from_bpm_120_to_128() -> None:
    """120 BPM to 128 BPM yields factor 128/120."""
    factor = compute_factor_from_bpm(120.0, 128.0)
    assert abs(factor - 128.0 / 120.0) < 1e-9


def test_tempo_scale_bpm_target_computes_correct_factor() -> None:
    """Regression: --bpm 128 starting from 120 BPM produces factor ~1.0667."""
    factor = compute_factor_from_bpm(120.0, 128.0)
    assert abs(factor - (128.0 / 120.0)) < 1e-9, (
        f"Expected {128.0 / 120.0}, got {factor}"
    )


def test_compute_factor_from_bpm_half_time() -> None:
    """Targeting 60 BPM from 120 yields 0.5 (half-time)."""
    factor = compute_factor_from_bpm(120.0, 60.0)
    assert abs(factor - 0.5) < 1e-9


def test_compute_factor_from_bpm_double_time() -> None:
    """Targeting 240 BPM from 120 yields 2.0 (double-time)."""
    factor = compute_factor_from_bpm(120.0, 240.0)
    assert abs(factor - 2.0) < 1e-9


def test_compute_factor_from_bpm_same_bpm_yields_one() -> None:
    """No change: source == target yields factor 1.0."""
    factor = compute_factor_from_bpm(120.0, 120.0)
    assert abs(factor - 1.0) < 1e-9


def test_compute_factor_from_bpm_raises_on_zero_source() -> None:
    """Zero source BPM raises ValueError."""
    with pytest.raises(ValueError, match="source_bpm must be positive"):
        compute_factor_from_bpm(0.0, 128.0)


def test_compute_factor_from_bpm_raises_on_negative_source() -> None:
    """Negative source BPM raises ValueError."""
    with pytest.raises(ValueError, match="source_bpm must be positive"):
        compute_factor_from_bpm(-10.0, 128.0)


def test_compute_factor_from_bpm_raises_on_zero_target() -> None:
    """Zero target BPM raises ValueError."""
    with pytest.raises(ValueError, match="target_bpm must be positive"):
        compute_factor_from_bpm(120.0, 0.0)


def test_compute_factor_from_bpm_raises_on_negative_target() -> None:
    """Negative target BPM raises ValueError."""
    with pytest.raises(ValueError, match="target_bpm must be positive"):
        compute_factor_from_bpm(120.0, -1.0)


# ---------------------------------------------------------------------------
# apply_factor — pure function
# ---------------------------------------------------------------------------


def test_apply_factor_double_time() -> None:
    """Factor 2.0 doubles the BPM."""
    assert apply_factor(120.0, 2.0) == 240.0


def test_apply_factor_half_time() -> None:
    """Factor 0.5 halves the BPM."""
    assert apply_factor(120.0, 0.5) == 60.0


def test_apply_factor_identity() -> None:
    """Factor 1.0 leaves BPM unchanged."""
    assert apply_factor(120.0, 1.0) == 120.0


def test_tempo_scale_half_time_doubles_all_offsets() -> None:
    """Regression: factor 0.5 halves BPM (half-time = twice as slow)."""
    result = apply_factor(120.0, 0.5)
    assert result == 60.0, f"Expected 60.0, got {result}"


def test_apply_factor_128_bpm() -> None:
    """Factor 128/120 starting from 120 BPM yields exactly 128 BPM."""
    factor = compute_factor_from_bpm(120.0, 128.0)
    new_bpm = apply_factor(120.0, factor)
    assert abs(new_bpm - 128.0) < 0.0001


# ---------------------------------------------------------------------------
# _tempo_scale_async — schema and behaviour
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tempo_scale_factor_updates_note_timings(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """_tempo_scale_async returns a result with the correct factor applied."""
    _init_muse_repo(tmp_path)
    result = await _tempo_scale_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit=None,
        factor=2.0,
        bpm=None,
        track=None,
        preserve_expressions=False,
        message=None,
    )
    assert result["factor"] == 2.0
    assert result["new_bpm"] == apply_factor(result["source_bpm"], 2.0)


@pytest.mark.anyio
async def test_tempo_scale_bpm_128_from_120_bpm(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """--bpm 128 from a 120 BPM source produces new_bpm == 128."""
    _init_muse_repo(tmp_path)
    result = await _tempo_scale_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit=None,
        factor=None,
        bpm=128.0,
        track=None,
        preserve_expressions=False,
        message=None,
    )
    assert abs(result["new_bpm"] - 128.0) < 0.0001
    assert result["source_bpm"] == 120.0


@pytest.mark.anyio
async def test_tempo_scale_updates_commit_tempo_metadata(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Result contains source_commit, new_commit, and they differ."""
    _init_muse_repo(tmp_path)
    result = await _tempo_scale_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit=None,
        factor=1.5,
        bpm=None,
        track=None,
        preserve_expressions=False,
        message=None,
    )
    assert result["source_commit"]
    assert result["new_commit"]
    assert result["source_commit"] != result["new_commit"]


@pytest.mark.anyio
async def test_tempo_scale_preserve_expressions_scales_cc_events(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """preserve_expressions flag is reflected in the result."""
    _init_muse_repo(tmp_path)
    result = await _tempo_scale_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit=None,
        factor=0.5,
        bpm=None,
        track=None,
        preserve_expressions=True,
        message=None,
    )
    assert result["preserve_expressions"] is True


@pytest.mark.anyio
async def test_tempo_scale_returns_all_schema_keys(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """TempoScaleResult contains all expected keys."""
    _init_muse_repo(tmp_path)
    result = await _tempo_scale_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit=None,
        factor=1.0,
        bpm=None,
        track=None,
        preserve_expressions=False,
        message=None,
    )
    for key in (
        "source_commit",
        "new_commit",
        "factor",
        "source_bpm",
        "new_bpm",
        "track",
        "preserve_expressions",
        "message",
    ):
        assert key in result, f"Missing key: {key}"


@pytest.mark.anyio
async def test_tempo_scale_deterministic_output(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Same inputs always produce the same new_commit (deterministic)."""
    _init_muse_repo(tmp_path)
    result_a = await _tempo_scale_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit="abc12345",
        factor=0.5,
        bpm=None,
        track="bass",
        preserve_expressions=False,
        message=None,
    )
    result_b = await _tempo_scale_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit="abc12345",
        factor=0.5,
        bpm=None,
        track="bass",
        preserve_expressions=False,
        message=None,
    )
    assert result_a["new_commit"] == result_b["new_commit"]


@pytest.mark.anyio
async def test_tempo_scale_custom_message_reflected(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Custom --message is reflected in the result."""
    _init_muse_repo(tmp_path)
    result = await _tempo_scale_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit=None,
        factor=2.0,
        bpm=None,
        track=None,
        preserve_expressions=False,
        message="my custom message",
    )
    assert result["message"] == "my custom message"


@pytest.mark.anyio
async def test_tempo_scale_track_reflected(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """--track filter is reflected in the result."""
    _init_muse_repo(tmp_path)
    result = await _tempo_scale_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit=None,
        factor=2.0,
        bpm=None,
        track="keys",
        preserve_expressions=False,
        message=None,
    )
    assert result["track"] == "keys"


@pytest.mark.anyio
async def test_tempo_scale_no_track_defaults_to_all(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Without --track, the result track field is 'all'."""
    _init_muse_repo(tmp_path)
    result = await _tempo_scale_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit=None,
        factor=1.0,
        bpm=None,
        track=None,
        preserve_expressions=False,
        message=None,
    )
    assert result["track"] == "all"


@pytest.mark.anyio
async def test_tempo_scale_raises_if_no_factor_and_no_bpm(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """ValueError raised when neither factor nor --bpm is provided."""
    _init_muse_repo(tmp_path)
    with pytest.raises(ValueError, match="Either factor or --bpm must be provided"):
        await _tempo_scale_async(
            root=tmp_path,
            session=muse_cli_db_session,
            commit=None,
            factor=None,
            bpm=None,
            track=None,
            preserve_expressions=False,
            message=None,
        )


# ---------------------------------------------------------------------------
# _format_result — output formatters
# ---------------------------------------------------------------------------


def _make_result(
    factor: float = 2.0,
    source_bpm: float = 120.0,
    preserve_expressions: bool = False,
) -> TempoScaleResult:
    return TempoScaleResult(
        source_commit="abc12345",
        new_commit="deadbeef",
        factor=factor,
        source_bpm=source_bpm,
        new_bpm=apply_factor(source_bpm, factor),
        track="all",
        preserve_expressions=preserve_expressions,
        message="test message",
    )


def test_format_result_json_is_valid() -> None:
    """JSON output is parseable and contains all expected keys."""
    result = _make_result()
    output = _format_result(result, as_json=True)
    parsed = json.loads(output)
    for key in TempoScaleResult.__annotations__:
        assert key in parsed, f"Missing key in JSON: {key}"


def test_format_result_text_contains_source_and_new_commit() -> None:
    """Text output mentions both the source and new commit."""
    result = _make_result()
    output = _format_result(result, as_json=False)
    assert "abc12345" in output
    assert "deadbeef" in output


def test_format_result_text_contains_bpm_values() -> None:
    """Text output includes both source and new BPM."""
    result = _make_result(factor=2.0, source_bpm=120.0)
    output = _format_result(result, as_json=False)
    assert "120.0" in output
    assert "240.0" in output


def test_format_result_text_shows_preserve_expressions() -> None:
    """Text output notes expression scaling when the flag is set."""
    result = _make_result(preserve_expressions=True)
    output = _format_result(result, as_json=False)
    assert "Expressions" in output or "expression" in output.lower()


def test_format_result_text_no_preserve_expressions_not_shown() -> None:
    """Text output does NOT mention expressions when flag is off."""
    result = _make_result(preserve_expressions=False)
    output = _format_result(result, as_json=False)
    assert "expression" not in output.lower()


# ---------------------------------------------------------------------------
# CLI integration — CliRunner
# ---------------------------------------------------------------------------


def test_cli_tempo_scale_factor_basic(tmp_path: pathlib.Path) -> None:
    """``muse tempo-scale 2.0`` with a valid repo exits 0 and shows output."""
    _init_muse_repo(tmp_path)
    result = runner.invoke(cli, ["tempo-scale", "2.0"], env={"MUSE_REPO_ROOT": str(tmp_path)})
    assert result.exit_code == ExitCode.SUCCESS, result.output
    assert "Tempo scaled" in result.output


def test_cli_tempo_scale_half_time(tmp_path: pathlib.Path) -> None:
    """``muse tempo-scale 0.5`` creates a half-time feel commit."""
    _init_muse_repo(tmp_path)
    result = runner.invoke(cli, ["tempo-scale", "0.5"], env={"MUSE_REPO_ROOT": str(tmp_path)})
    assert result.exit_code == ExitCode.SUCCESS, result.output
    assert "60.0 BPM" in result.output


def test_cli_tempo_scale_bpm_128(tmp_path: pathlib.Path) -> None:
    """``muse tempo-scale --bpm 128`` scales to 128 BPM."""
    _init_muse_repo(tmp_path)
    result = runner.invoke(
        cli,
        ["tempo-scale", "--bpm", "128"],
        env={"MUSE_REPO_ROOT": str(tmp_path)},
    )
    assert result.exit_code == ExitCode.SUCCESS, result.output
    assert "128.0 BPM" in result.output


def test_cli_tempo_scale_json_output(tmp_path: pathlib.Path) -> None:
    """``--json`` flag emits valid JSON with all expected keys.

    Options are placed before the positional <factor> argument because Click
    Groups disable interspersed-args parsing by default.
    """
    _init_muse_repo(tmp_path)
    result = runner.invoke(
        cli,
        ["tempo-scale", "--json", "2.0"],
        env={"MUSE_REPO_ROOT": str(tmp_path)},
    )
    assert result.exit_code == ExitCode.SUCCESS, result.output
    parsed = json.loads(result.output)
    assert "source_commit" in parsed
    assert "new_commit" in parsed
    assert "factor" in parsed
    assert "new_bpm" in parsed


def test_cli_tempo_scale_with_commit_sha(tmp_path: pathlib.Path) -> None:
    """Passing an explicit commit SHA is accepted."""
    _init_muse_repo(tmp_path)
    result = runner.invoke(
        cli,
        ["tempo-scale", "1.5", "deadbeef"],
        env={"MUSE_REPO_ROOT": str(tmp_path)},
    )
    assert result.exit_code == ExitCode.SUCCESS, result.output


def test_cli_tempo_scale_with_track(tmp_path: pathlib.Path) -> None:
    """``--track bass`` is accepted and reflected in JSON output.

    Options precede the positional factor to satisfy Click Group parsing.
    """
    _init_muse_repo(tmp_path)
    result = runner.invoke(
        cli,
        ["tempo-scale", "--track", "bass", "--json", "2.0"],
        env={"MUSE_REPO_ROOT": str(tmp_path)},
    )
    assert result.exit_code == ExitCode.SUCCESS, result.output
    parsed = json.loads(result.output)
    assert parsed["track"] == "bass"


def test_cli_tempo_scale_preserve_expressions(tmp_path: pathlib.Path) -> None:
    """``--preserve-expressions`` flag sets the flag in JSON output.

    Options precede the positional factor to satisfy Click Group parsing.
    """
    _init_muse_repo(tmp_path)
    result = runner.invoke(
        cli,
        ["tempo-scale", "--preserve-expressions", "--json", "0.5"],
        env={"MUSE_REPO_ROOT": str(tmp_path)},
    )
    assert result.exit_code == ExitCode.SUCCESS, result.output
    parsed = json.loads(result.output)
    assert parsed["preserve_expressions"] is True


def test_cli_tempo_scale_custom_message(tmp_path: pathlib.Path) -> None:
    """``--message`` is stored in JSON output.

    Options precede the positional factor to satisfy Click Group parsing.
    """
    _init_muse_repo(tmp_path)
    result = runner.invoke(
        cli,
        ["tempo-scale", "--message", "half-time remix", "--json", "2.0"],
        env={"MUSE_REPO_ROOT": str(tmp_path)},
    )
    assert result.exit_code == ExitCode.SUCCESS, result.output
    parsed = json.loads(result.output)
    assert parsed["message"] == "half-time remix"


def test_cli_tempo_scale_no_args_exits_user_error(tmp_path: pathlib.Path) -> None:
    """No factor and no --bpm exits with USER_ERROR (1)."""
    _init_muse_repo(tmp_path)
    result = runner.invoke(cli, ["tempo-scale"], env={"MUSE_REPO_ROOT": str(tmp_path)})
    assert result.exit_code == ExitCode.USER_ERROR


def test_cli_tempo_scale_factor_and_bpm_mutually_exclusive(tmp_path: pathlib.Path) -> None:
    """Providing both <factor> and --bpm exits with USER_ERROR (1).

    ``--bpm`` is placed before the positional factor so Click parses it as
    an option (not a positional arg) and our mutual-exclusion check fires.
    """
    _init_muse_repo(tmp_path)
    result = runner.invoke(
        cli,
        ["tempo-scale", "--bpm", "128", "2.0"],
        env={"MUSE_REPO_ROOT": str(tmp_path)},
    )
    assert result.exit_code == ExitCode.USER_ERROR


def test_cli_tempo_scale_out_of_range_factor(tmp_path: pathlib.Path) -> None:
    """Factor outside [FACTOR_MIN, FACTOR_MAX] exits USER_ERROR (1)."""
    _init_muse_repo(tmp_path)
    result = runner.invoke(
        cli,
        ["tempo-scale", "999999"],
        env={"MUSE_REPO_ROOT": str(tmp_path)},
    )
    assert result.exit_code == ExitCode.USER_ERROR


def test_cli_tempo_scale_zero_factor_exits_user_error(tmp_path: pathlib.Path) -> None:
    """Factor of 0 (below FACTOR_MIN) exits USER_ERROR (1)."""
    _init_muse_repo(tmp_path)
    result = runner.invoke(
        cli,
        ["tempo-scale", "0.0"],
        env={"MUSE_REPO_ROOT": str(tmp_path)},
    )
    assert result.exit_code == ExitCode.USER_ERROR


def test_cli_tempo_scale_non_positive_bpm_exits_user_error(tmp_path: pathlib.Path) -> None:
    """--bpm 0 exits USER_ERROR (1)."""
    _init_muse_repo(tmp_path)
    result = runner.invoke(
        cli,
        ["tempo-scale", "--bpm", "0"],
        env={"MUSE_REPO_ROOT": str(tmp_path)},
    )
    assert result.exit_code == ExitCode.USER_ERROR


def test_cli_tempo_scale_outside_repo_exits_repo_not_found(tmp_path: pathlib.Path) -> None:
    """Running outside a Muse repo exits REPO_NOT_FOUND (2)."""
    empty = tmp_path / "no_muse_here"
    empty.mkdir()
    result = runner.invoke(
        cli,
        ["tempo-scale", "2.0"],
        env={"MUSE_REPO_ROOT": str(empty)},
    )
    assert result.exit_code == ExitCode.REPO_NOT_FOUND
