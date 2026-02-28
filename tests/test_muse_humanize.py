"""Tests for ``muse humanize`` — CLI interface, flag parsing, and stub output.

All CLI-level tests use ``typer.testing.CliRunner`` against the full ``muse``
app.  Async core tests call ``_humanize_async`` directly with an in-memory
SQLite session — the stub does not query the DB, so the session satisfies only
the signature contract.

Covered acceptance criteria:
- ``--seed 42`` produces identical results every time (deterministic)
- ``--natural`` increases timing variance vs no humanization
- ``--tight`` stays within its documented bounds
- ``--timing-only`` preserves velocity (velocity_range == 0 for all tracks)
- ``--velocity-only`` preserves timing (timing_range_ms == 0 for all tracks)
- ``--track bass`` leaves other tracks unchanged
- Drum channel is excluded from timing variation (drum_channel_excluded=True)
"""
from __future__ import annotations

import json
import os
import pathlib
import random
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from maestro.db.database import Base
import maestro.muse_cli.models  # noqa: F401  — registers MuseCli* ORM models
from maestro.muse_cli.app import cli
from maestro.muse_cli.commands.humanize import (
    LOOSE_TIMING_MS,
    LOOSE_VELOCITY,
    NATURAL_TIMING_MS,
    NATURAL_VELOCITY,
    TIGHT_TIMING_MS,
    TIGHT_VELOCITY,
    HumanizeResult,
    TrackHumanizeResult,
    _apply_humanization,
    _humanize_async,
    _render_json,
    _render_table,
    _resolve_preset,
    _timing_ms_for_factor,
    _velocity_range_for_factor,
)
from maestro.muse_cli.errors import ExitCode

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, branch: str = "main") -> str:
    rid = str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text(f"refs/heads/{branch}")
    (muse / "refs" / "heads" / branch).write_text("")
    return rid


def _commit_ref(root: pathlib.Path, branch: str = "main") -> None:
    muse = root / ".muse"
    (muse / "refs" / "heads" / branch).write_text(
        "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    )


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session (stub humanize does not query it)."""
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
# Unit — _resolve_preset
# ---------------------------------------------------------------------------


def test_resolve_preset_default_is_natural() -> None:
    """With no flags set, default preset is 'natural'."""
    label, factor = _resolve_preset(tight=False, natural=False, loose=False, factor=None)
    assert label == "natural"
    assert 0.0 < factor < 1.0


def test_resolve_preset_tight() -> None:
    """--tight maps to a factor < natural."""
    label, factor = _resolve_preset(tight=True, natural=False, loose=False, factor=None)
    assert label == "tight"
    assert factor < 0.6


def test_resolve_preset_loose() -> None:
    """--loose maps to factor=1.0."""
    label, factor = _resolve_preset(tight=False, natural=False, loose=True, factor=None)
    assert label == "loose"
    assert factor == 1.0


def test_resolve_preset_custom_factor() -> None:
    """--factor overrides preset flags."""
    label, factor = _resolve_preset(tight=False, natural=False, loose=False, factor=0.42)
    assert label == "custom"
    assert factor == 0.42


def test_resolve_preset_multiple_presets_raises() -> None:
    """Specifying two preset flags simultaneously raises ValueError."""
    with pytest.raises(ValueError, match="Only one"):
        _resolve_preset(tight=True, natural=False, loose=True, factor=None)


# ---------------------------------------------------------------------------
# Unit — timing / velocity helpers
# ---------------------------------------------------------------------------


def test_timing_ms_for_factor_zero() -> None:
    assert _timing_ms_for_factor(0.0) == 0


def test_timing_ms_for_factor_one() -> None:
    assert _timing_ms_for_factor(1.0) == LOOSE_TIMING_MS


def test_timing_ms_for_natural_within_ceiling() -> None:
    assert _timing_ms_for_factor(0.6) <= NATURAL_TIMING_MS


def test_velocity_range_for_factor_zero() -> None:
    assert _velocity_range_for_factor(0.0) == 0


def test_velocity_range_for_factor_one() -> None:
    assert _velocity_range_for_factor(1.0) == LOOSE_VELOCITY


# ---------------------------------------------------------------------------
# Unit — _apply_humanization
# ---------------------------------------------------------------------------


def test_humanize_seed_produces_deterministic_output() -> None:
    """Regression: identical seeds produce identical TrackHumanizeResult values."""
    rng_a = random.Random(42)
    rng_b = random.Random(42)
    result_a = _apply_humanization(
        track_name="bass",
        timing_ms=12,
        velocity_range=10,
        timing_only=False,
        velocity_only=False,
        rng=rng_a,
    )
    result_b = _apply_humanization(
        track_name="bass",
        timing_ms=12,
        velocity_range=10,
        timing_only=False,
        velocity_only=False,
        rng=rng_b,
    )
    assert result_a == result_b


def test_humanize_natural_increases_timing_variance() -> None:
    """--natural produces non-zero timing range for non-drum tracks."""
    rng = random.Random(7)
    result = _apply_humanization(
        track_name="bass",
        timing_ms=NATURAL_TIMING_MS,
        velocity_range=NATURAL_VELOCITY,
        timing_only=False,
        velocity_only=False,
        rng=rng,
    )
    assert result["timing_range_ms"] > 0


def test_humanize_tight_stays_within_bounds() -> None:
    """--tight result stays within TIGHT_TIMING_MS and TIGHT_VELOCITY."""
    rng = random.Random(1)
    timing_ms = _timing_ms_for_factor(0.25)
    vel_range = _velocity_range_for_factor(0.25)
    result = _apply_humanization(
        track_name="keys",
        timing_ms=timing_ms,
        velocity_range=vel_range,
        timing_only=False,
        velocity_only=False,
        rng=rng,
    )
    assert result["timing_range_ms"] <= TIGHT_TIMING_MS
    assert result["velocity_range"] <= TIGHT_VELOCITY


def test_humanize_timing_only_preserves_velocity() -> None:
    """--timing-only sets velocity_range to 0."""
    rng = random.Random(2)
    result = _apply_humanization(
        track_name="lead",
        timing_ms=12,
        velocity_range=10,
        timing_only=True,
        velocity_only=False,
        rng=rng,
    )
    assert result["velocity_range"] == 0


def test_humanize_velocity_only_preserves_timing() -> None:
    """--velocity-only sets timing_range_ms to 0."""
    rng = random.Random(3)
    result = _apply_humanization(
        track_name="lead",
        timing_ms=12,
        velocity_range=10,
        timing_only=False,
        velocity_only=True,
        rng=rng,
    )
    assert result["timing_range_ms"] == 0


def test_humanize_drum_channel_excluded_from_timing_variation() -> None:
    """Drum track is excluded from timing variation."""
    rng = random.Random(4)
    result = _apply_humanization(
        track_name="drums",
        timing_ms=12,
        velocity_range=10,
        timing_only=False,
        velocity_only=False,
        rng=rng,
    )
    assert result["drum_channel_excluded"] is True
    assert result["timing_range_ms"] == 0


def test_humanize_drum_channel_velocity_applied() -> None:
    """Drum track still receives velocity humanization."""
    rng = random.Random(5)
    result = _apply_humanization(
        track_name="drums",
        timing_ms=12,
        velocity_range=10,
        timing_only=False,
        velocity_only=False,
        rng=rng,
    )
    assert result["velocity_range"] > 0


# ---------------------------------------------------------------------------
# Async core — _humanize_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_humanize_async_default_output(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_humanize_async with defaults renders a table with all four stub tracks."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _humanize_async(
        root=tmp_path,
        session=db_session,
        source_commit=None,
        preset="natural",
        factor=0.6,
        seed=None,
        timing_only=False,
        velocity_only=False,
        track=None,
        section=None,
        message=None,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "Humanize" in out
    assert "drums" in out
    assert "bass" in out
    assert result["preset"] == "natural"
    assert len(result["tracks"]) == 4


@pytest.mark.anyio
async def test_humanize_async_seed_deterministic(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--seed 42 produces identical commit IDs across two invocations."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result_a = await _humanize_async(
        root=tmp_path,
        session=db_session,
        source_commit=None,
        preset="natural",
        factor=0.6,
        seed=42,
        timing_only=False,
        velocity_only=False,
        track=None,
        section=None,
        message=None,
        as_json=False,
    )
    capsys.readouterr()

    result_b = await _humanize_async(
        root=tmp_path,
        session=db_session,
        source_commit=None,
        preset="natural",
        factor=0.6,
        seed=42,
        timing_only=False,
        velocity_only=False,
        track=None,
        section=None,
        message=None,
        as_json=False,
    )

    assert result_a["new_commit_id"] == result_b["new_commit_id"]
    assert result_a["tracks"] == result_b["tracks"]


@pytest.mark.anyio
async def test_humanize_async_track_scoped_leaves_other_tracks_unchanged(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--track bass: only the bass track appears in the result."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _humanize_async(
        root=tmp_path,
        session=db_session,
        source_commit=None,
        preset="natural",
        factor=0.6,
        seed=10,
        timing_only=False,
        velocity_only=False,
        track="bass",
        section=None,
        message=None,
        as_json=False,
    )

    track_names = [t["track"] for t in result["tracks"]]
    assert track_names == ["bass"]


@pytest.mark.anyio
async def test_humanize_async_json_mode(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--json emits parseable JSON with the expected top-level keys."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _humanize_async(
        root=tmp_path,
        session=db_session,
        source_commit=None,
        preset="tight",
        factor=0.25,
        seed=99,
        timing_only=False,
        velocity_only=False,
        track=None,
        section=None,
        message=None,
        as_json=True,
    )

    raw = capsys.readouterr().out
    payload = json.loads(raw)
    for key in ("commit", "branch", "preset", "factor", "seed", "tracks", "new_commit_id"):
        assert key in payload, f"Missing key: {key}"
    assert payload["preset"] == "tight"
    assert isinstance(payload["tracks"], list)


@pytest.mark.anyio
async def test_humanize_async_timing_only_all_velocities_zero(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--timing-only: all track results have velocity_range == 0."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _humanize_async(
        root=tmp_path,
        session=db_session,
        source_commit=None,
        preset="natural",
        factor=0.6,
        seed=1,
        timing_only=True,
        velocity_only=False,
        track=None,
        section=None,
        message=None,
        as_json=False,
    )

    for tr in result["tracks"]:
        assert tr["velocity_range"] == 0, f"{tr['track']}: expected velocity_range=0"


@pytest.mark.anyio
async def test_humanize_async_velocity_only_all_timing_zero(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--velocity-only: all track results have timing_range_ms == 0."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _humanize_async(
        root=tmp_path,
        session=db_session,
        source_commit=None,
        preset="natural",
        factor=0.6,
        seed=1,
        timing_only=False,
        velocity_only=True,
        track=None,
        section=None,
        message=None,
        as_json=False,
    )

    for tr in result["tracks"]:
        assert tr["timing_range_ms"] == 0, f"{tr['track']}: expected timing_range_ms=0"


@pytest.mark.anyio
async def test_humanize_async_explicit_source_commit_in_output(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An explicit source commit ref appears in the rendered output."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    await _humanize_async(
        root=tmp_path,
        session=db_session,
        source_commit="deadbeef",
        preset="natural",
        factor=0.6,
        seed=None,
        timing_only=False,
        velocity_only=False,
        track=None,
        section=None,
        message=None,
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "deadbeef" in out


# ---------------------------------------------------------------------------
# Renderer unit tests
# ---------------------------------------------------------------------------


def _make_result() -> HumanizeResult:
    tracks: list[TrackHumanizeResult] = [
        TrackHumanizeResult(
            track="bass",
            timing_range_ms=12,
            velocity_range=10,
            notes_affected=64,
            drum_channel_excluded=False,
        ),
        TrackHumanizeResult(
            track="drums",
            timing_range_ms=0,
            velocity_range=10,
            notes_affected=48,
            drum_channel_excluded=True,
        ),
    ]
    return HumanizeResult(
        commit="a1b2c3d4",
        branch="main",
        source_commit="a1b2c3d4",
        preset="natural",
        factor=0.6,
        seed=None,
        timing_only=False,
        velocity_only=False,
        track_filter=None,
        section_filter=None,
        tracks=tracks,
        new_commit_id="deadbeef",
    )


def test_render_table_includes_commit_and_preset(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _render_table(_make_result())
    out = capsys.readouterr().out
    assert "natural" in out
    assert "a1b2c3d4" in out


def test_render_table_shows_drum_excluded(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _render_table(_make_result())
    out = capsys.readouterr().out
    assert "yes" in out


def test_render_json_is_valid(capsys: pytest.CaptureFixture[str]) -> None:
    _render_json(_make_result())
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert payload["preset"] == "natural"
    assert payload["tracks"][0]["track"] == "bass"


# ---------------------------------------------------------------------------
# CLI integration — CliRunner
# ---------------------------------------------------------------------------


def test_cli_humanize_appears_in_muse_help() -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "humanize" in result.output


def test_cli_humanize_help_lists_flags() -> None:
    result = runner.invoke(cli, ["humanize", "--help"])
    assert result.exit_code == 0
    for flag in (
        "--tight",
        "--natural",
        "--loose",
        "--factor",
        "--timing-only",
        "--velocity-only",
        "--track",
        "--section",
        "--seed",
        "--message",
        "--json",
    ):
        assert flag in result.output, f"Flag '{flag}' missing from help"


def test_cli_humanize_outside_repo_exits_repo_not_found(tmp_path: pathlib.Path) -> None:
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["humanize"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.REPO_NOT_FOUND)
    assert "not a muse repository" in result.output.lower()


def test_cli_humanize_timing_and_velocity_only_mutually_exclusive(
    tmp_path: pathlib.Path,
) -> None:
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli,
            ["humanize", "--timing-only", "--velocity-only"],
            catch_exceptions=False,
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.USER_ERROR)


def test_cli_humanize_two_presets_exits_user_error(tmp_path: pathlib.Path) -> None:
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli,
            ["humanize", "--tight", "--loose"],
            catch_exceptions=False,
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.USER_ERROR)
