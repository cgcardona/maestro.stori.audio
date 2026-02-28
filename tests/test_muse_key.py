"""Tests for ``muse key`` — CLI interface, flag parsing, key helpers, and stub output.

All CLI-level tests use ``typer.testing.CliRunner`` against the full ``muse``
app so that argument parsing, flag handling, and exit codes are exercised
end-to-end.

Async core tests call ``_key_detect_async`` and ``_key_history_async`` directly
with an in-memory SQLite session (the stub does not query the DB, so the session
is injected only to satisfy the signature contract).
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
from maestro.muse_cli.commands.key import (
    KeyDetectResult,
    KeyHistoryEntry,
    _format_detect,
    _format_history,
    _key_detect_async,
    _key_history_async,
    parse_key,
    relative_key,
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
    """In-memory SQLite session (stub key does not actually query it)."""
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
# Unit — parse_key
# ---------------------------------------------------------------------------


def test_parse_key_major_sharp() -> None:
    """parse_key handles sharp-tonic major keys."""
    tonic, mode = parse_key("F# major")
    assert tonic == "F#"
    assert mode == "major"


def test_parse_key_minor_flat() -> None:
    """parse_key handles flat-tonic minor keys."""
    tonic, mode = parse_key("Eb minor")
    assert tonic == "Eb"
    assert mode == "minor"


def test_parse_key_case_insensitive_mode() -> None:
    """parse_key normalises mode to lowercase."""
    tonic, mode = parse_key("C Major")
    assert mode == "major"


def test_parse_key_invalid_tonic_raises() -> None:
    """parse_key raises ValueError for unknown tonics."""
    with pytest.raises(ValueError, match="Unknown tonic"):
        parse_key("H minor")


def test_parse_key_invalid_mode_raises() -> None:
    """parse_key raises ValueError for unknown modes."""
    with pytest.raises(ValueError, match="Unknown mode"):
        parse_key("C dorian")


def test_parse_key_wrong_format_raises() -> None:
    """parse_key raises ValueError when the string has != 2 parts."""
    with pytest.raises(ValueError, match="Key must be"):
        parse_key("F#minor")


# ---------------------------------------------------------------------------
# Unit — relative_key
# ---------------------------------------------------------------------------


def test_relative_key_a_minor_is_c_major() -> None:
    """Relative major of A minor is C major."""
    assert relative_key("A", "minor") == "C major"


def test_relative_key_c_major_is_a_minor() -> None:
    """Relative minor of C major is A minor."""
    assert relative_key("C", "major") == "A minor"


def test_relative_key_f_sharp_minor_is_a_major() -> None:
    """Relative major of F# minor is A major."""
    assert relative_key("F#", "minor") == "A major"


def test_relative_key_eb_major_is_c_minor() -> None:
    """Relative minor of Eb major is C minor (via enharmonic: Eb → D#)."""
    result = relative_key("Eb", "major")
    assert result == "C minor"


def test_relative_key_wraps_around_chromatic() -> None:
    """Relative key calculation wraps correctly at B/C boundary."""
    # B minor → relative major is D major (3 semitones up from B = D)
    assert relative_key("B", "minor") == "D major"


# ---------------------------------------------------------------------------
# Unit — formatters
# ---------------------------------------------------------------------------


def test_format_detect_text() -> None:
    """_format_detect emits key, commit, and branch in text mode."""
    result: KeyDetectResult = KeyDetectResult(
        key="C major",
        tonic="C",
        mode="major",
        relative="",
        commit="a1b2c3d4",
        branch="main",
        track="all",
        source="stub",
    )
    output = _format_detect(result, as_json=False)
    assert "C major" in output
    assert "a1b2c3d4" in output
    assert "main" in output
    assert "stub" in output


def test_format_detect_text_with_relative() -> None:
    """_format_detect includes the relative key when populated."""
    result: KeyDetectResult = KeyDetectResult(
        key="A minor",
        tonic="A",
        mode="minor",
        relative="C major",
        commit="deadbeef",
        branch="feature",
        track="all",
        source="stub",
    )
    output = _format_detect(result, as_json=False)
    assert "Relative: C major" in output


def test_format_detect_json_valid() -> None:
    """_format_detect emits parseable JSON with all expected keys."""
    result: KeyDetectResult = KeyDetectResult(
        key="F# minor",
        tonic="F#",
        mode="minor",
        relative="A major",
        commit="cafe1234",
        branch="dev",
        track="bass",
        source="annotation",
    )
    raw = _format_detect(result, as_json=True)
    payload = json.loads(raw)
    assert payload["key"] == "F# minor"
    assert payload["tonic"] == "F#"
    assert payload["mode"] == "minor"
    assert payload["relative"] == "A major"
    assert payload["source"] == "annotation"


def test_format_history_text() -> None:
    """_format_history emits one line per entry in text mode."""
    entries: list[KeyHistoryEntry] = [
        KeyHistoryEntry(commit="aaa", key="C major", tonic="C", mode="major", source="stub"),
        KeyHistoryEntry(commit="bbb", key="F minor", tonic="F", mode="minor", source="annotation"),
    ]
    output = _format_history(entries, as_json=False)
    assert "aaa" in output
    assert "C major" in output
    assert "bbb" in output
    assert "F minor" in output


def test_format_history_json() -> None:
    """_format_history emits parseable JSON list."""
    entries: list[KeyHistoryEntry] = [
        KeyHistoryEntry(commit="aaa", key="C major", tonic="C", mode="major", source="stub"),
    ]
    raw = _format_history(entries, as_json=True)
    payload = json.loads(raw)
    assert isinstance(payload, list)
    assert payload[0]["key"] == "C major"


def test_format_history_empty() -> None:
    """_format_history returns a descriptive message for empty history."""
    output = _format_history([], as_json=False)
    assert "no key history" in output.lower()


# ---------------------------------------------------------------------------
# Async core — _key_detect_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_key_detect_async_returns_result(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """_key_detect_async returns a valid KeyDetectResult."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _key_detect_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        show_relative=False,
    )

    assert result["key"]
    assert result["tonic"]
    assert result["mode"] in ("major", "minor")
    assert result["commit"]
    assert result["branch"]
    assert result["track"] == "all"
    assert result["source"] in ("stub", "detected", "annotation")


@pytest.mark.anyio
async def test_key_detect_async_track_filter(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """--track populates the track field in the result."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _key_detect_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track="bass",
        show_relative=False,
    )

    assert result["track"] == "bass"


@pytest.mark.anyio
async def test_key_detect_async_relative(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """--relative populates the relative field."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _key_detect_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        show_relative=True,
    )

    assert result["relative"] != ""


@pytest.mark.anyio
async def test_key_detect_async_no_relative_by_default(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """Relative field is empty when show_relative=False."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _key_detect_async(
        root=tmp_path,
        session=db_session,
        commit=None,
        track=None,
        show_relative=False,
    )

    assert result["relative"] == ""


@pytest.mark.anyio
async def test_key_detect_async_explicit_commit(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """An explicit commit SHA appears in the result."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    result = await _key_detect_async(
        root=tmp_path,
        session=db_session,
        commit="deadbeef",
        track=None,
        show_relative=False,
    )

    assert result["commit"] == "deadbeef"


# ---------------------------------------------------------------------------
# Async core — _key_history_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_key_history_async_returns_list(
    tmp_path: pathlib.Path,
    db_session: AsyncSession,
) -> None:
    """_key_history_async returns a non-empty list of KeyHistoryEntry."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    entries = await _key_history_async(
        root=tmp_path,
        session=db_session,
        track=None,
    )

    assert isinstance(entries, list)
    assert len(entries) >= 1
    for entry in entries:
        assert "commit" in entry
        assert "key" in entry
        assert "tonic" in entry
        assert "mode" in entry


# ---------------------------------------------------------------------------
# CLI integration — CliRunner
# ---------------------------------------------------------------------------


def test_cli_key_help_lists_flags() -> None:
    """``muse key --help`` shows all documented flags."""
    result = runner.invoke(cli, ["key", "--help"])
    assert result.exit_code == 0
    for flag in ("--set", "--track", "--relative", "--history", "--json"):
        assert flag in result.output, f"Flag '{flag}' not found in help output"


def test_cli_key_appears_in_muse_help() -> None:
    """``muse --help`` lists the key subcommand."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "key" in result.output


def test_cli_key_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse key`` exits with REPO_NOT_FOUND when invoked outside a Muse repository."""
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["key"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.REPO_NOT_FOUND)
    assert "not a muse repository" in result.output.lower()


def test_cli_key_set_valid_key(tmp_path: pathlib.Path) -> None:
    """``muse key --set 'F# minor'`` annotates successfully."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["key", "--set", "F# minor"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert "F#" in result.output
    assert "minor" in result.output


def test_cli_key_set_invalid_key_exits_user_error(tmp_path: pathlib.Path) -> None:
    """``muse key --set 'H minor'`` exits USER_ERROR for unknown tonic."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["key", "--set", "H minor"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.USER_ERROR)


def test_cli_key_set_json_output(tmp_path: pathlib.Path) -> None:
    """``muse key --set 'Eb major' --json`` emits valid JSON."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli, ["key", "--set", "Eb major", "--json"], catch_exceptions=False
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["key"] == "Eb major"
    assert payload["tonic"] == "Eb"
    assert payload["mode"] == "major"
    assert payload["source"] == "annotation"


def test_cli_key_set_with_relative(tmp_path: pathlib.Path) -> None:
    """``muse key --set 'A minor' --relative`` includes the relative key."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            cli, ["key", "--set", "A minor", "--relative"], catch_exceptions=False
        )
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert "C major" in result.output


def test_cli_key_default_detect(tmp_path: pathlib.Path) -> None:
    """``muse key`` with no flags detects and prints the key."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["key"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert "Key:" in result.output


def test_cli_key_json_mode(tmp_path: pathlib.Path) -> None:
    """``muse key --json`` emits parseable JSON."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["key", "--json"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "key" in payload
    assert "tonic" in payload
    assert "mode" in payload


def test_cli_key_history(tmp_path: pathlib.Path) -> None:
    """``muse key --history`` prints the commit-to-key mapping."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["key", "--history"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert result.output.strip()  # non-empty output


def test_cli_key_history_json(tmp_path: pathlib.Path) -> None:
    """``muse key --history --json`` emits a JSON list."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["key", "--history", "--json"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert len(payload) >= 1


def test_cli_key_relative_flag(tmp_path: pathlib.Path) -> None:
    """``muse key --relative`` includes a 'Relative:' line in output."""
    _init_muse_repo(tmp_path)
    _commit_ref(tmp_path)

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["key", "--relative"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == 0
    assert "Relative:" in result.output
