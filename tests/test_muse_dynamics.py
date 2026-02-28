"""Tests for ``muse dynamics`` — CLI interface, flag parsing, and stub output format.

All CLI-level tests use ``typer.testing.CliRunner`` against the full ``muse``
app so that argument parsing, flag handling, and exit codes are exercised end-to-end.

Async core tests call ``_dynamics_async`` directly with an in-memory SQLite session
(defined as a local fixture — the stub does not query the DB, so the session is
injected only to satisfy the signature contract).
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from maestro.db.database import Base
import maestro.muse_cli.models  # noqa: F401  — registers MuseCli* with Base.metadata
from maestro.muse_cli.app import cli
from maestro.muse_cli.commands.dynamics import (
    TrackDynamics,
    _ARC_LABELS,
    _VALID_ARCS,
    _dynamics_async,
    _render_json,
    _render_table,
    _stub_profiles,
)
from maestro.muse_cli.errors import ExitCode

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, branch: str = "main") -> str:
    """Create a minimal .muse/ layout with one empty commit ref."""
    rid = str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text(f"refs/heads/{branch}")
    # Leave the ref file empty (no commits yet) — dynamics handles this gracefully.
    (muse / "refs" / "heads" / branch).write_text("")
    return rid


def _commit_ref(root: pathlib.Path, branch: str = "main") -> None:
    """Write a fake commit ID into the branch ref so HEAD is non-empty."""
    muse = root / ".muse"
    (muse / "refs" / "heads" / branch).write_text("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2")


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session (stub dynamics does not actually query it)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Unit — stub data
# ---------------------------------------------------------------------------


def test_stub_profiles_returns_four_tracks() -> None:
    """Stub produces exactly four tracks."""
    profiles = _stub_profiles()
    assert len(profiles) == 4


def test_stub_profiles_have_valid_arcs() -> None:
    """Every stub track has a valid arc label."""
    for p in _stub_profiles():
        assert p.arc in _VALID_ARCS, f"Unexpected arc '{p.arc}' for track '{p.name}'"


def test_stub_profiles_velocity_constraints() -> None:
    """Stub profiles have sensible velocity values (0–127 MIDI range)."""
    for p in _stub_profiles():
        assert 0 <= p.avg_velocity <= 127, f"{p.name}: avg_velocity out of range"
        assert 0 <= p.peak_velocity <= 127, f"{p.name}: peak_velocity out of range"
        assert p.peak_velocity >= p.avg_velocity, f"{p.name}: peak < avg"
        assert p.velocity_range > 0, f"{p.name}: velocity_range must be positive"


def test_track_dynamics_to_dict() -> None:
    """TrackDynamics.to_dict returns the expected keys and types."""
    td = TrackDynamics(
        name="drums", avg_velocity=88, peak_velocity=110, velocity_range=42, arc="terraced"
    )
    d = td.to_dict()
    assert d["track"] == "drums"
    assert d["avg_velocity"] == 88
    assert d["peak_velocity"] == 110
    assert d["velocity_range"] == 42
    assert d["arc"] == "terraced"


def test_arc_labels_constant_matches_valid_arcs() -> None:
    """_ARC_LABELS tuple and _VALID_ARCS frozenset are in sync."""
    assert set(_ARC_LABELS) == _VALID_ARCS


# ---------------------------------------------------------------------------
# Unit — renderers (capsys)
# ---------------------------------------------------------------------------


def test_render_table_outputs_header(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_table includes commit ref and column headers."""
    profiles = _stub_profiles()
    _render_table(profiles, commit_ref="a1b2c3d4", branch="main")
    out = capsys.readouterr().out
    assert "Dynamic profile" in out
    assert "a1b2c3d4" in out
    assert "Track" in out
    assert "Avg Vel" in out
    assert "Arc" in out


def test_render_table_shows_all_tracks(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_table emits one row per profile."""
    profiles = _stub_profiles()
    _render_table(profiles, commit_ref="a1b2c3d4", branch="main")
    out = capsys.readouterr().out
    for p in profiles:
        assert p.name in out
        assert p.arc in out


def test_render_json_is_valid(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_json emits parseable JSON with the expected top-level keys."""
    profiles = _stub_profiles()
    _render_json(profiles, commit_ref="a1b2c3d4", branch="main")
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert payload["commit"] == "a1b2c3d4"
    assert payload["branch"] == "main"
    assert isinstance(payload["tracks"], list)
    assert len(payload["tracks"]) == len(profiles)
    for entry in payload["tracks"]:
        assert "track" in entry
        assert "avg_velocity" in entry
        assert "arc" in entry


# ---------------------------------------------------------------------------
# Async core — _dynamics_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dynamics_async_default_output(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_dynamics_async with no filters shows all stub tracks."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _dynamics_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section=None,
        compare=None,
        history=False,
        peak=False,
        range_flag=False,
        arc=False,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "Dynamic profile" in out
    assert "drums" in out
    assert "bass" in out
    assert "keys" in out
    assert "lead" in out


@pytest.mark.anyio
async def test_dynamics_async_json_mode(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_dynamics_async --json emits valid JSON with all four tracks."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _dynamics_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section=None,
        compare=None,
        history=False,
        peak=False,
        range_flag=False,
        arc=False,
        as_json=True,
    )

    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert len(payload["tracks"]) == 4


@pytest.mark.anyio
async def test_dynamics_async_track_filter(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--track filters output to prefix-matched tracks only."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _dynamics_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track="drum",
        section=None,
        compare=None,
        history=False,
        peak=False,
        range_flag=False,
        arc=False,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "drums" in out
    assert "bass" not in out
    assert "keys" not in out


@pytest.mark.anyio
async def test_dynamics_async_arc_filter_valid(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--arc with a valid arc label filters tracks to that arc only."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _dynamics_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track="flat",
        section=None,
        compare=None,
        history=False,
        peak=False,
        range_flag=False,
        arc=True,
        as_json=False,
    )

    out = capsys.readouterr().out
    # "bass" has arc "flat"
    assert "bass" in out
    # "drums" has arc "terraced" — should be absent
    assert "drums" not in out


@pytest.mark.anyio
async def test_dynamics_async_arc_filter_invalid_exits(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """--arc with an invalid arc label exits with USER_ERROR."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    import typer

    with pytest.raises(typer.Exit) as exc_info:
        await _dynamics_async(
            root=tmp_path,
            session=db_session,
            commit=None,
            track="notanarc",
            section=None,
            compare=None,
            history=False,
            peak=False,
            range_flag=False,
            arc=True,
            as_json=False,
        )
    assert exc_info.value.exit_code == int(ExitCode.USER_ERROR)


@pytest.mark.anyio
async def test_dynamics_async_no_commits_exits_success(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With no commits and no explicit commit arg, exits 0 with informative message."""
    _init_muse_repo(tmp_path)
    # No _commit_ref call — branch ref is empty.

    import typer

    with pytest.raises(typer.Exit) as exc_info:
        await _dynamics_async(
            root=tmp_path,
            session=db_session,
            commit=None,
            track=None,
            section=None,
            compare=None,
            history=False,
            peak=False,
            range_flag=False,
            arc=False,
            as_json=False,
        )
    assert exc_info.value.exit_code == int(ExitCode.SUCCESS)
    out = capsys.readouterr().out
    assert "No commits yet" in out


@pytest.mark.anyio
async def test_dynamics_async_peak_filter(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--peak removes tracks whose peak is at or below the branch average peak."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _dynamics_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section=None,
        compare=None,
        history=False,
        peak=True,
        range_flag=False,
        arc=False,
        as_json=False,
    )

    out = capsys.readouterr().out
    # At least some tracks should appear.
    assert "Dynamic profile" in out


@pytest.mark.anyio
async def test_dynamics_async_range_sort_json(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--range sorts tracks by velocity_range descending."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _dynamics_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section=None,
        compare=None,
        history=False,
        peak=False,
        range_flag=True,
        arc=False,
        as_json=True,
    )

    raw = capsys.readouterr().out
    payload = json.loads(raw)
    ranges = [t["velocity_range"] for t in payload["tracks"]]
    assert ranges == sorted(ranges, reverse=True)


@pytest.mark.anyio
async def test_dynamics_async_explicit_commit_ref(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An explicit commit ref appears in the output header."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _dynamics_async(
        root=tmp_path,
        session=db_session,
        commit="deadbeef",
        track=None,
        section=None,
        compare=None,
        history=False,
        peak=False,
        range_flag=False,
        arc=False,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "deadbeef" in out


@pytest.mark.anyio
async def test_dynamics_async_history_flag_warns(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--history emits a stub boundary warning but still renders the table."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _dynamics_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section=None,
        compare=None,
        history=True,
        peak=False,
        range_flag=False,
        arc=False,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "--history" in out
    assert "Dynamic profile" in out


@pytest.mark.anyio
async def test_dynamics_async_compare_flag_warns(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--compare emits a stub boundary warning but still renders the table."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _dynamics_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        section=None,
        compare="abc123",
        history=False,
        peak=False,
        range_flag=False,
        arc=False,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "--compare" in out
    assert "Dynamic profile" in out


# ---------------------------------------------------------------------------
# CLI integration — CliRunner
# ---------------------------------------------------------------------------


def test_cli_dynamics_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse dynamics`` exits 2 when invoked outside a Muse repository."""
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["dynamics"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.REPO_NOT_FOUND)
    assert "not a muse repository" in result.output.lower()


def test_cli_dynamics_help_lists_flags(tmp_path: pathlib.Path) -> None:
    """``muse dynamics --help`` shows all documented flags."""
    result = runner.invoke(cli, ["dynamics", "--help"])
    assert result.exit_code == 0
    for flag in ("--track", "--section", "--compare", "--history", "--peak", "--range", "--arc", "--json"):
        assert flag in result.output, f"Flag '{flag}' not found in help output"


def test_cli_dynamics_appears_in_muse_help() -> None:
    """``muse --help`` lists the dynamics subcommand."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "dynamics" in result.output
