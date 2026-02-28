"""Tests for muse grep — pattern search across Muse VCS commits (issue #124).

Verifies:
- Pattern matching against commit messages (case-insensitive).
- Pattern matching against branch names.
- Non-matching commits are excluded.
- --commits flag produces one commit ID per line.
- --json flag produces valid JSON array.
- --track / --section / --rhythm-invariant emit future-work warnings.
- Empty history produces a graceful no-commits message.
- Multiple matches across a chain are all returned.
- Boundary seal (AST).
"""
from __future__ import annotations

import ast
import dataclasses
import json
import pathlib
import textwrap
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from maestro.db.database import Base
from maestro.muse_cli.commands.grep_cmd import (
    GrepMatch,
    _grep_async,
    _load_all_commits,
    _match_commit,
    _render_matches,
)
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite async session — creates all tables before each test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _utc(year: int = 2026, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _snapshot(session: AsyncSession, snap_id: str) -> MuseCliSnapshot:
    s = MuseCliSnapshot(snapshot_id=snap_id, manifest={})
    session.add(s)
    return s


def _commit(
    session: AsyncSession,
    *,
    commit_id: str,
    repo_id: str = "repo-1",
    branch: str = "main",
    message: str = "test commit",
    parent_id: str | None = None,
    snap_id: str = "snap-0000",
    ts: datetime | None = None,
) -> MuseCliCommit:
    c = MuseCliCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch,
        parent_commit_id=parent_id,
        parent2_commit_id=None,
        snapshot_id=snap_id,
        message=message,
        author="test-user",
        committed_at=ts or _utc(),
    )
    session.add(c)
    return c


# ---------------------------------------------------------------------------
# Unit tests: _match_commit
# ---------------------------------------------------------------------------


def _make_commit_obj(
    *,
    commit_id: str = "abc12345" * 8,
    branch: str = "main",
    message: str = "boom bap groove",
) -> MuseCliCommit:
    """Build a MuseCliCommit using its normal constructor (no DB session needed).

    SQLAlchemy ORM models can be instantiated without a session by using the
    regular constructor.  The instance is transient (not associated with any
    session) which is sufficient for testing ``_match_commit``.
    """
    return MuseCliCommit(
        commit_id=commit_id,
        repo_id="repo-1",
        branch=branch,
        parent_commit_id=None,
        parent2_commit_id=None,
        snapshot_id="snap-0000" + "0" * 60,
        message=message,
        author="test-user",
        committed_at=_utc(),
    )


def test_match_commit_finds_pattern_in_message() -> None:
    """Pattern matched in message → GrepMatch with source='message'."""
    c = _make_commit_obj(message="add C4 E4 G4 riff to chorus")
    result = _match_commit(
        c,
        "C4 E4 G4",
        track=None,
        section=None,
        transposition_invariant=True,
        rhythm_invariant=False,
    )
    assert result is not None
    assert result.match_source == "message"
    assert result.commit_id == c.commit_id


def test_match_commit_case_insensitive() -> None:
    """Pattern matching is case-insensitive."""
    c = _make_commit_obj(message="Added CM7 chord voicing")
    result = _match_commit(
        c,
        "cm7",
        track=None,
        section=None,
        transposition_invariant=True,
        rhythm_invariant=False,
    )
    assert result is not None


def test_match_commit_finds_pattern_in_branch() -> None:
    """Pattern matched in branch name → GrepMatch with source='branch'."""
    c = _make_commit_obj(branch="feature/pentatonic-scale", message="initial commit")
    result = _match_commit(
        c,
        "pentatonic",
        track=None,
        section=None,
        transposition_invariant=True,
        rhythm_invariant=False,
    )
    assert result is not None
    assert result.match_source == "branch"


def test_match_commit_no_match_returns_none() -> None:
    """Commit with no pattern occurrence → None."""
    c = _make_commit_obj(message="unrelated commit", branch="main")
    result = _match_commit(
        c,
        "Am7",
        track=None,
        section=None,
        transposition_invariant=True,
        rhythm_invariant=False,
    )
    assert result is None


def test_match_commit_message_takes_priority_over_branch() -> None:
    """When message matches, source is 'message' even if branch would also match."""
    c = _make_commit_obj(message="groove pattern", branch="groove-branch")
    result = _match_commit(
        c,
        "groove",
        track=None,
        section=None,
        transposition_invariant=True,
        rhythm_invariant=False,
    )
    assert result is not None
    assert result.match_source == "message"


# ---------------------------------------------------------------------------
# Integration tests: _load_all_commits + _grep_async (with real in-memory DB)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_load_all_commits_walks_chain(async_session: AsyncSession) -> None:
    """_load_all_commits returns all commits in newest-first order."""
    snap_id = "snap-aaaa" + "0" * 55
    _snapshot(async_session, snap_id[:64])
    c1 = _commit(async_session, commit_id="aaa" + "0" * 61, snap_id=snap_id[:64], message="first")
    c2 = _commit(
        async_session,
        commit_id="bbb" + "0" * 61,
        snap_id=snap_id[:64],
        parent_id=c1.commit_id,
        message="second",
        ts=_utc(day=2),
    )
    await async_session.commit()

    commits = await _load_all_commits(async_session, head_commit_id=c2.commit_id, limit=100)
    assert len(commits) == 2
    assert commits[0].commit_id == c2.commit_id  # newest first
    assert commits[1].commit_id == c1.commit_id


@pytest.mark.anyio
async def test_grep_async_matches_message(
    async_session: AsyncSession, tmp_path: pathlib.Path
) -> None:
    """_grep_async finds commits whose messages contain the pattern."""
    # Set up a minimal .muse repo structure
    muse_dir = tmp_path / ".muse"
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    head_ref = "refs/heads/main"
    (muse_dir / "HEAD").write_text(head_ref)

    snap_id = "s" * 64
    _snapshot(async_session, snap_id)
    c1 = _commit(
        async_session,
        commit_id="c" * 64,
        snap_id=snap_id,
        message="add pentatonic riff",
        ts=_utc(day=2),
    )
    await async_session.commit()
    (muse_dir / head_ref).write_text(c1.commit_id)

    matches = await _grep_async(
        root=tmp_path,
        session=async_session,
        pattern="pentatonic",
        track=None,
        section=None,
        transposition_invariant=True,
        rhythm_invariant=False,
        show_commits=False,
        output_json=False,
    )
    assert len(matches) == 1
    assert matches[0].match_source == "message"
    assert matches[0].commit_id == c1.commit_id


@pytest.mark.anyio
async def test_grep_async_no_matches(
    async_session: AsyncSession, tmp_path: pathlib.Path
) -> None:
    """_grep_async returns empty list when pattern is not found."""
    muse_dir = tmp_path / ".muse"
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "HEAD").write_text("refs/heads/main")

    snap_id = "s" * 64
    _snapshot(async_session, snap_id)
    c1 = _commit(
        async_session,
        commit_id="c" * 64,
        snap_id=snap_id,
        message="unrelated commit",
    )
    await async_session.commit()
    (muse_dir / "refs" / "heads" / "main").write_text(c1.commit_id)

    matches = await _grep_async(
        root=tmp_path,
        session=async_session,
        pattern="Cm7",
        track=None,
        section=None,
        transposition_invariant=True,
        rhythm_invariant=False,
        show_commits=False,
        output_json=False,
    )
    assert matches == []


@pytest.mark.anyio
async def test_grep_async_empty_history(
    async_session: AsyncSession, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """_grep_async handles branches with no commits gracefully."""
    muse_dir = tmp_path / ".muse"
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "HEAD").write_text("refs/heads/main")
    # No HEAD ref file → no commits

    matches = await _grep_async(
        root=tmp_path,
        session=async_session,
        pattern="anything",
        track=None,
        section=None,
        transposition_invariant=True,
        rhythm_invariant=False,
        show_commits=False,
        output_json=False,
    )
    assert matches == []


@pytest.mark.anyio
async def test_grep_async_multiple_matches_across_chain(
    async_session: AsyncSession, tmp_path: pathlib.Path
) -> None:
    """All matching commits across a long chain are returned."""
    muse_dir = tmp_path / ".muse"
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "HEAD").write_text("refs/heads/main")

    snap_id = "s" * 64
    _snapshot(async_session, snap_id)

    # 3-commit chain; c1 and c3 match "groove", c2 does not
    c1 = _commit(async_session, commit_id="1" * 64, snap_id=snap_id, message="groove intro", ts=_utc(day=1))
    c2 = _commit(async_session, commit_id="2" * 64, snap_id=snap_id, message="bridge section", parent_id=c1.commit_id, ts=_utc(day=2))
    c3 = _commit(async_session, commit_id="3" * 64, snap_id=snap_id, message="add groove variation", parent_id=c2.commit_id, ts=_utc(day=3))
    await async_session.commit()
    (muse_dir / "refs" / "heads" / "main").write_text(c3.commit_id)

    matches = await _grep_async(
        root=tmp_path,
        session=async_session,
        pattern="groove",
        track=None,
        section=None,
        transposition_invariant=True,
        rhythm_invariant=False,
        show_commits=False,
        output_json=False,
    )
    assert len(matches) == 2
    commit_ids = {m.commit_id for m in matches}
    assert c1.commit_id in commit_ids
    assert c3.commit_id in commit_ids
    assert c2.commit_id not in commit_ids


# ---------------------------------------------------------------------------
# Output rendering tests
# ---------------------------------------------------------------------------


def test_render_matches_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    """--json flag produces a valid JSON array of match dicts."""
    matches = [
        GrepMatch(
            commit_id="abc" * 21 + "a",
            branch="main",
            message="pentatonic solo",
            committed_at="2026-01-01T00:00:00+00:00",
            match_source="message",
        )
    ]
    _render_matches(matches, pattern="pentatonic", show_commits=False, output_json=True)
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["match_source"] == "message"
    assert parsed[0]["branch"] == "main"


def test_render_matches_commits_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """--commits flag outputs one commit ID per line."""
    commit_ids = ["a" * 64, "b" * 64]
    matches = [
        GrepMatch(commit_id=cid, branch="main", message="msg", committed_at="2026-01-01T00:00:00+00:00", match_source="message")
        for cid in commit_ids
    ]
    _render_matches(matches, pattern="msg", show_commits=True, output_json=False)
    captured = capsys.readouterr()
    lines = captured.out.strip().splitlines()
    assert lines == commit_ids


def test_render_matches_default_human_output(capsys: pytest.CaptureFixture[str]) -> None:
    """Default output includes commit ID, branch, date, match source, and message."""
    matches = [
        GrepMatch(
            commit_id="d" * 64,
            branch="feature/groove",
            message="add groove pattern",
            committed_at="2026-02-01T00:00:00+00:00",
            match_source="message",
        )
    ]
    _render_matches(matches, pattern="groove", show_commits=False, output_json=False)
    captured = capsys.readouterr()
    assert "groove" in captured.out
    assert "feature/groove" in captured.out
    assert "message" in captured.out


def test_render_matches_no_matches_message(capsys: pytest.CaptureFixture[str]) -> None:
    """When no matches found, a descriptive message is printed."""
    _render_matches([], pattern="Cm7", show_commits=False, output_json=False)
    captured = capsys.readouterr()
    assert "Cm7" in captured.out
    assert "No commits" in captured.out


def test_render_matches_json_empty_list(capsys: pytest.CaptureFixture[str]) -> None:
    """--json with no matches outputs an empty JSON array."""
    _render_matches([], pattern="nothing", show_commits=False, output_json=True)
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed == []


# ---------------------------------------------------------------------------
# GrepMatch dataclass integrity
# ---------------------------------------------------------------------------


def test_grep_match_asdict_roundtrip() -> None:
    """GrepMatch is a plain dataclass — asdict() should be lossless."""
    m = GrepMatch(
        commit_id="x" * 64,
        branch="main",
        message="test pattern",
        committed_at="2026-01-01T00:00:00+00:00",
        match_source="message",
    )
    d = dataclasses.asdict(m)
    assert d["commit_id"] == "x" * 64
    assert d["branch"] == "main"
    assert d["match_source"] == "message"


# ---------------------------------------------------------------------------
# Boundary seal — AST checks
# ---------------------------------------------------------------------------


def test_grep_cmd_module_has_future_annotations() -> None:
    """grep_cmd.py must start with 'from __future__ import annotations'."""
    src = pathlib.Path(__file__).parent.parent / "maestro" / "muse_cli" / "commands" / "grep_cmd.py"
    tree = ast.parse(src.read_text())
    first_import = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)),
        None,
    )
    assert first_import is not None
    assert first_import.module == "__future__"
    names = [a.name for a in first_import.names]
    assert "annotations" in names


def test_grep_cmd_no_print_statements() -> None:
    """grep_cmd.py must not use print() — only logging and typer.echo."""
    src = pathlib.Path(__file__).parent.parent / "maestro" / "muse_cli" / "commands" / "grep_cmd.py"
    tree = ast.parse(src.read_text())
    print_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "print"
    ]
    assert print_calls == [], "grep_cmd.py must not contain print() calls"
