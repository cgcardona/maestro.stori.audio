"""Tests for ``muse contour`` — CLI interface, flag parsing, and stub output format.

All CLI-level tests use ``typer.testing.CliRunner`` against the full ``muse``
app so that argument parsing, flag handling, and exit codes are exercised end-to-end.

Async core tests call the internal async functions directly with an in-memory
SQLite session (the stub does not query the DB, so the session is injected only
to satisfy the signature contract).
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from maestro.db.database import Base
import maestro.muse_cli.models  # noqa: F401  — registers MuseCli* with Base.metadata
from maestro.muse_cli.app import cli
from maestro.muse_cli.commands.contour import (
    SHAPE_LABELS,
    VALID_SHAPES,
    ContourCompareResult,
    ContourResult,
    _contour_compare_async,
    _contour_detect_async,
    _contour_history_async,
    _format_compare,
    _format_detect,
    _format_history,
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
    (muse / "refs" / "heads" / branch).write_text("")
    return rid


def _commit_ref(root: pathlib.Path, branch: str = "main") -> None:
    """Write a fake commit ID into the branch ref so HEAD is non-empty."""
    muse = root / ".muse"
    (muse / "refs" / "heads" / branch).write_text("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2")


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session (stub contour does not actually query it)."""
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
# Unit — constants
# ---------------------------------------------------------------------------


def test_shape_labels_constant_matches_valid_shapes() -> None:
    """SHAPE_LABELS tuple and VALID_SHAPES frozenset are in sync."""
    assert set(SHAPE_LABELS) == VALID_SHAPES


def test_valid_shapes_contains_expected_labels() -> None:
    """All six canonical shape labels are present in VALID_SHAPES."""
    expected = {"ascending", "descending", "arch", "inverted-arch", "wave", "static"}
    assert expected == VALID_SHAPES


# ---------------------------------------------------------------------------
# Unit — formatters
# ---------------------------------------------------------------------------


def _make_result(
    *,
    shape: str = "arch",
    tessitura: int = 24,
    avg_interval: float = 2.5,
    phrase_count: int = 4,
    avg_phrase_bars: float = 8.0,
    commit: str = "a1b2c3d4",
    branch: str = "main",
    track: str = "all",
    section: str = "all",
    source: str = "stub",
) -> ContourResult:
    return ContourResult(
        shape=shape,
        tessitura=tessitura,
        avg_interval=avg_interval,
        phrase_count=phrase_count,
        avg_phrase_bars=avg_phrase_bars,
        commit=commit,
        branch=branch,
        track=track,
        section=section,
        source=source,
    )


def test_format_detect_human_readable_contains_shape() -> None:
    """_format_detect (human mode) includes shape, range, phrase info."""
    result = _make_result()
    out = _format_detect(result, as_json=False, shape_only=False)
    assert "Shape: arch" in out
    assert "Phrases: 4" in out
    assert "Angularity:" in out
    assert "stub" in out


def test_format_detect_shape_only() -> None:
    """_format_detect with shape_only=True returns just the shape line."""
    result = _make_result(shape="wave")
    out = _format_detect(result, as_json=False, shape_only=True)
    assert out == "Shape: wave"


def test_format_detect_json_is_valid() -> None:
    """_format_detect with as_json=True returns valid parseable JSON."""
    result = _make_result()
    raw = _format_detect(result, as_json=True, shape_only=False)
    payload = json.loads(raw)
    assert payload["shape"] == "arch"
    assert payload["tessitura"] == 24
    assert payload["phrase_count"] == 4
    assert payload["source"] == "stub"


def test_format_detect_range_octaves() -> None:
    """_format_detect converts tessitura semitones to octave string correctly."""
    result = _make_result(tessitura=24)
    out = _format_detect(result, as_json=False, shape_only=False)
    assert "2 octaves" in out

    result_one_octave = _make_result(tessitura=12)
    out2 = _format_detect(result_one_octave, as_json=False, shape_only=False)
    assert "1 octave" in out2


def test_format_history_human_readable() -> None:
    """_format_history renders commit, shape, range, and angularity per entry."""
    entries = [_make_result(commit="deadbeef")]
    out = _format_history(entries, as_json=False)
    assert "deadbeef" in out
    assert "arch" in out
    assert "24 st" in out


def test_format_history_empty() -> None:
    """_format_history returns a helpful message when entries list is empty."""
    out = _format_history([], as_json=False)
    assert "no contour history" in out.lower()


def test_format_history_json() -> None:
    """_format_history with as_json=True emits a JSON array."""
    entries = [_make_result()]
    raw = _format_history(entries, as_json=True)
    payload = json.loads(raw)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["shape"] == "arch"


def test_format_compare_human_readable() -> None:
    """_format_compare renders commit refs, shapes, and delta line."""
    result = ContourCompareResult(
        commit_a=_make_result(commit="aaaa1111"),
        commit_b=_make_result(commit="bbbb2222", shape="ascending"),
        shape_changed=True,
        angularity_delta=0.5,
        tessitura_delta=4,
    )
    out = _format_compare(result, as_json=False)
    assert "aaaa1111" in out
    assert "bbbb2222" in out
    assert "shape changed" in out
    assert "Delta" in out


def test_format_compare_json() -> None:
    """_format_compare with as_json=True emits parseable JSON."""
    result = ContourCompareResult(
        commit_a=_make_result(commit="aaa"),
        commit_b=_make_result(commit="bbb"),
        shape_changed=False,
        angularity_delta=0.0,
        tessitura_delta=0,
    )
    raw = _format_compare(result, as_json=True)
    payload = json.loads(raw)
    assert "commit_a" in payload
    assert "commit_b" in payload
    assert payload["shape_changed"] is False
    assert payload["angularity_delta"] == 0.0


# ---------------------------------------------------------------------------
# Async core — _contour_detect_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_contour_detect_async_returns_contour_result(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """_contour_detect_async returns a ContourResult with all expected keys."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _contour_detect_async(
        root=tmp_path, session=db_session, commit=None, track=None, section=None
    )

    assert result["shape"] in VALID_SHAPES
    assert isinstance(result["tessitura"], int)
    assert result["tessitura"] > 0
    assert isinstance(result["avg_interval"], float)
    assert result["phrase_count"] > 0
    assert result["branch"] == "main"
    assert result["track"] == "all"
    assert result["section"] == "all"


@pytest.mark.anyio
async def test_contour_detect_async_uses_explicit_commit(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """When commit is provided, it appears as the commit field in the result."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _contour_detect_async(
        root=tmp_path, session=db_session, commit="deadbeef", track=None, section=None
    )
    assert result["commit"] == "deadbeef"


@pytest.mark.anyio
async def test_contour_detect_async_track_filter(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """Track name is propagated into the result when specified."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _contour_detect_async(
        root=tmp_path, session=db_session, commit=None, track="keys", section=None
    )
    assert result["track"] == "keys"


@pytest.mark.anyio
async def test_contour_detect_async_section_filter(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """Section name is propagated into the result when specified."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _contour_detect_async(
        root=tmp_path, session=db_session, commit=None, track=None, section="verse"
    )
    assert result["section"] == "verse"


@pytest.mark.anyio
async def test_contour_classifies_arch_shape(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """Stub contour returns 'arch' as the default shape label."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _contour_detect_async(
        root=tmp_path, session=db_session, commit=None, track=None, section=None
    )
    assert result["shape"] == "arch"


# ---------------------------------------------------------------------------
# Async core — _contour_compare_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_contour_compare_detects_angularity_change(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """_contour_compare_async returns a ContourCompareResult with delta fields."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _contour_compare_async(
        root=tmp_path,
        session=db_session,
        commit_a=None,
        commit_b="HEAD~10",
        track=None,
        section=None,
    )

    assert "commit_a" in result
    assert "commit_b" in result
    assert "angularity_delta" in result
    assert "tessitura_delta" in result
    assert isinstance(result["shape_changed"], bool)
    assert result["commit_b"]["commit"] == "HEAD~10"


@pytest.mark.anyio
async def test_contour_compare_shape_changed_flag(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """shape_changed is False when both sides return the same stub shape."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _contour_compare_async(
        root=tmp_path,
        session=db_session,
        commit_a=None,
        commit_b="some-ref",
        track=None,
        section=None,
    )
    assert result["shape_changed"] is False


# ---------------------------------------------------------------------------
# Async core — _contour_history_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_contour_history_returns_evolution(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """_contour_history_async returns a non-empty list of ContourResult entries."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    entries = await _contour_history_async(
        root=tmp_path, session=db_session, track=None, section=None
    )

    assert len(entries) >= 1
    for entry in entries:
        assert entry["shape"] in VALID_SHAPES
        assert isinstance(entry["tessitura"], int)


@pytest.mark.anyio
async def test_contour_history_with_track(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """_contour_history_async propagates track into all returned entries."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    entries = await _contour_history_async(
        root=tmp_path, session=db_session, track="lead", section=None
    )

    for entry in entries:
        assert entry["track"] == "lead"


# ---------------------------------------------------------------------------
# CLI integration — CliRunner
# ---------------------------------------------------------------------------


def test_cli_contour_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse contour`` exits 2 when invoked outside a Muse repository."""
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["contour"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.REPO_NOT_FOUND)
    assert "not a muse repository" in result.output.lower()


def test_cli_contour_help_lists_flags() -> None:
    """``muse contour --help`` shows all documented flags."""
    result = runner.invoke(cli, ["contour", "--help"])
    assert result.exit_code == 0
    for flag in ("--track", "--section", "--compare", "--history", "--shape", "--json"):
        assert flag in result.output, f"Flag '{flag}' not found in help output"


def test_cli_contour_appears_in_muse_help() -> None:
    """``muse --help`` lists the contour subcommand."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "contour" in result.output
