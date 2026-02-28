"""Tests for ``muse chord-map`` — CLI interface, flag parsing, and stub output.

All CLI-level tests use ``typer.testing.CliRunner`` against the full ``muse``
app so that argument parsing, flag handling, and exit codes are exercised
end-to-end.

Async core tests call ``_chord_map_async`` directly with an in-memory SQLite
session (defined as a local fixture — the stub does not query the DB, so the
session is injected only to satisfy the signature contract).
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
from maestro.muse_cli.commands.chord_map import (
    ChordMapResult,
    _VALID_FORMATS,
    _chord_map_async,
    _render_json,
    _render_mermaid,
    _render_text,
    _stub_chord_events,
    _stub_voice_leading_steps,
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
    """In-memory SQLite session (stub chord-map does not actually query it)."""
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


def test_stub_chord_events_returns_expected_count() -> None:
    """Stub produces the expected number of chord events."""
    events = _stub_chord_events()
    assert len(events) == 6


def test_stub_chord_events_bar_numbers_positive() -> None:
    """All stub chord events have positive bar numbers."""
    for event in _stub_chord_events():
        assert event["bar"] >= 1
        assert event["beat"] >= 1


def test_stub_chord_events_duration_positive() -> None:
    """All stub chord events have positive duration."""
    for event in _stub_chord_events():
        assert event["duration"] > 0


def test_stub_chord_events_chord_nonempty() -> None:
    """Every stub chord event has a non-empty chord symbol."""
    for event in _stub_chord_events():
        assert event["chord"]


def test_stub_voice_leading_steps_count() -> None:
    """Stub produces one voice-leading step per chord transition."""
    steps = _stub_voice_leading_steps()
    events = _stub_chord_events()
    assert len(steps) == len(events) - 1


def test_stub_voice_leading_steps_movements_nonempty() -> None:
    """Each voice-leading step has at least one movement."""
    for step in _stub_voice_leading_steps():
        assert step["movements"]


def test_valid_formats_contains_expected() -> None:
    """_VALID_FORMATS contains text, json, and mermaid."""
    assert "text" in _VALID_FORMATS
    assert "json" in _VALID_FORMATS
    assert "mermaid" in _VALID_FORMATS


# ---------------------------------------------------------------------------
# Unit — renderers
# ---------------------------------------------------------------------------


def _make_result(*, voice_leading: bool = False) -> ChordMapResult:
    vl = _stub_voice_leading_steps() if voice_leading else []
    return ChordMapResult(
        commit="a1b2c3d4",
        branch="main",
        track="all",
        section="",
        chords=_stub_chord_events(),
        voice_leading=vl,
    )


def test_render_text_includes_commit_ref() -> None:
    """_render_text includes the commit ref in the header."""
    result = _make_result()
    output = _render_text(result)
    assert "a1b2c3d4" in output


def test_render_text_includes_branch() -> None:
    """_render_text shows the branch name."""
    result = _make_result()
    output = _render_text(result)
    assert "main" in output


def test_render_text_includes_chord_symbols() -> None:
    """_render_text contains all chord symbols from stub data."""
    result = _make_result()
    output = _render_text(result)
    assert "Cmaj9" in output
    assert "Am11" in output
    assert "Dm7" in output
    assert "G7" in output


def test_render_text_voice_leading_shows_arrows() -> None:
    """_render_text with voice_leading includes arrow notation."""
    result = _make_result(voice_leading=True)
    output = _render_text(result)
    assert "->" in output


def test_render_text_no_voice_leading_shows_blocks() -> None:
    """_render_text without voice_leading shows block characters."""
    result = _make_result(voice_leading=False)
    output = _render_text(result)
    assert "Bar" in output


def test_render_json_is_valid_json() -> None:
    """_render_json emits parseable JSON."""
    result = _make_result()
    raw = _render_json(result)
    payload = json.loads(raw)
    assert payload["commit"] == "a1b2c3d4"
    assert payload["branch"] == "main"


def test_render_json_chords_list() -> None:
    """_render_json includes a 'chords' list with chord entries."""
    result = _make_result()
    payload = json.loads(_render_json(result))
    assert isinstance(payload["chords"], list)
    assert len(payload["chords"]) == len(_stub_chord_events())
    for entry in payload["chords"]:
        assert "chord" in entry
        assert "bar" in entry
        assert "duration" in entry


def test_render_json_voice_leading_empty_by_default() -> None:
    """_render_json without voice_leading has an empty voice_leading list."""
    result = _make_result(voice_leading=False)
    payload = json.loads(_render_json(result))
    assert payload["voice_leading"] == []


def test_render_json_voice_leading_populated() -> None:
    """_render_json with voice_leading has non-empty voice_leading list."""
    result = _make_result(voice_leading=True)
    payload = json.loads(_render_json(result))
    assert len(payload["voice_leading"]) > 0
    step = payload["voice_leading"][0]
    assert "from_chord" in step
    assert "to_chord" in step
    assert "movements" in step


def test_render_mermaid_starts_with_timeline() -> None:
    """_render_mermaid begins with the Mermaid timeline keyword."""
    result = _make_result()
    output = _render_mermaid(result)
    assert output.startswith("timeline")


def test_render_mermaid_includes_commit() -> None:
    """_render_mermaid title includes the commit ref."""
    result = _make_result()
    output = _render_mermaid(result)
    assert "a1b2c3d4" in output


def test_render_mermaid_includes_bars() -> None:
    """_render_mermaid contains section labels for bars."""
    result = _make_result()
    output = _render_mermaid(result)
    assert "Bar 1" in output
    assert "Bar 3" in output


# ---------------------------------------------------------------------------
# Async core — _chord_map_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_chord_map_async_default(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """_chord_map_async with no args returns a non-empty ChordMapResult."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _chord_map_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        section=None,
        track=None,
        bar_grid=True,
        fmt="text",
        voice_leading=False,
    )

    assert result["commit"]
    assert result["branch"] == "main"
    assert result["track"] == "all"
    assert result["section"] == ""
    assert len(result["chords"]) > 0
    assert result["voice_leading"] == []


@pytest.mark.anyio
async def test_chord_map_async_explicit_commit(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """An explicit commit ref is reflected in the result."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _chord_map_async(
        root=tmp_path,
        session=db_session,
        commit="deadbeef",
        section=None,
        track=None,
        bar_grid=True,
        fmt="text",
        voice_leading=False,
    )

    assert result["commit"] == "deadbeef"


@pytest.mark.anyio
async def test_chord_map_async_track_filter(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """Passing --track sets the track field in the result."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _chord_map_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        section=None,
        track="piano",
        bar_grid=True,
        fmt="text",
        voice_leading=False,
    )

    assert result["track"] == "piano"
    for event in result["chords"]:
        assert event["track"] == "piano"


@pytest.mark.anyio
async def test_chord_map_async_section_filter(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """Passing --section records the section in the result."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _chord_map_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        section="verse",
        track=None,
        bar_grid=True,
        fmt="text",
        voice_leading=False,
    )

    assert result["section"] == "verse"


@pytest.mark.anyio
async def test_chord_map_async_voice_leading(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """--voice-leading populates the voice_leading field."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _chord_map_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        section=None,
        track=None,
        bar_grid=True,
        fmt="text",
        voice_leading=True,
    )

    assert len(result["voice_leading"]) > 0
    step = result["voice_leading"][0]
    assert step["from_chord"]
    assert step["to_chord"]
    assert step["movements"]


@pytest.mark.anyio
async def test_chord_map_async_json_format(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """fmt='json' still returns a valid ChordMapResult from _chord_map_async."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _chord_map_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        section=None,
        track=None,
        bar_grid=True,
        fmt="json",
        voice_leading=False,
    )

    assert isinstance(result["chords"], list)


# ---------------------------------------------------------------------------
# CLI integration — CliRunner
# ---------------------------------------------------------------------------


def test_cli_chord_map_outside_repo_exits(tmp_path: pathlib.Path) -> None:
    """``muse chord-map`` exits with REPO_NOT_FOUND outside a Muse repo."""
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["chord-map"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.REPO_NOT_FOUND)
    assert "not a muse repository" in result.output.lower()


def test_cli_chord_map_help_lists_flags() -> None:
    """``muse chord-map --help`` shows all documented flags."""
    result = runner.invoke(cli, ["chord-map", "--help"])
    assert result.exit_code == 0
    for flag in ("--section", "--track", "--bar-grid", "--format", "--voice-leading"):
        assert flag in result.output, f"Flag '{flag}' missing from help"


def test_cli_chord_map_appears_in_muse_help() -> None:
    """``muse --help`` lists the chord-map subcommand."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "chord-map" in result.output


def test_cli_chord_map_invalid_format_exits_user_error(tmp_path: pathlib.Path) -> None:
    """``muse chord-map --format badformat`` exits with USER_ERROR."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli, ["chord-map", "--format", "badformat"], catch_exceptions=False
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.USER_ERROR)


def test_cli_chord_map_text_output(tmp_path: pathlib.Path) -> None:
    """``muse chord-map`` (default text) includes chord symbols in output."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["chord-map"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert "Chord map" in result.output
    assert "Cmaj9" in result.output


def test_cli_chord_map_json_output(tmp_path: pathlib.Path) -> None:
    """``muse chord-map --format json`` emits valid JSON."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli, ["chord-map", "--format", "json"], catch_exceptions=False
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "chords" in payload
    assert "commit" in payload


def test_cli_chord_map_mermaid_output(tmp_path: pathlib.Path) -> None:
    """``muse chord-map --format mermaid`` emits a Mermaid timeline block."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli, ["chord-map", "--format", "mermaid"], catch_exceptions=False
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert "timeline" in result.output


def test_cli_chord_map_voice_leading_output(tmp_path: pathlib.Path) -> None:
    """``muse chord-map --voice-leading`` includes arrow notation in output."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli, ["chord-map", "--voice-leading"], catch_exceptions=False
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert "->" in result.output
