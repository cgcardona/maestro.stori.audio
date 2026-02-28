"""Tests for ``muse ask``.

All async tests call ``_ask_async`` directly with an in-memory SQLite
session and a ``tmp_path`` repo root — no real Postgres or running
process required.  Commits are seeded via ``_commit_async`` so the
commands are tested as an integrated pair.
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.ask import AnswerResult, _ask_async, _keywords
from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.errors import ExitCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": rid, "schema_version": "1"})
    )
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _write_workdir(root: pathlib.Path, files: dict[str, bytes]) -> None:
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    for name, content in files.items():
        (workdir / name).write_bytes(content)


async def _make_commits(
    root: pathlib.Path,
    session: AsyncSession,
    messages: list[str],
    file_seed: int = 0,
) -> list[str]:
    """Create N commits on the repo, each with unique file content."""
    commit_ids: list[str] = []
    for i, msg in enumerate(messages):
        _write_workdir(root, {f"track_{file_seed + i}.mid": f"MIDI-{file_seed + i}".encode()})
        cid = await _commit_async(message=msg, root=root, session=session)
        commit_ids.append(cid)
    return commit_ids


# ---------------------------------------------------------------------------
# _keywords unit tests
# ---------------------------------------------------------------------------


def test_keywords_extracts_meaningful_tokens() -> None:
    """Non-trivial keywords are extracted from a natural language question."""
    tokens = _keywords("what tempo changes did I make last week?")
    assert "tempo" in tokens
    assert "changes" in tokens
    # stop words removed
    assert "what" not in tokens
    assert "did" not in tokens
    assert "last" not in tokens


def test_keywords_empty_string_returns_empty() -> None:
    """Empty input yields no keywords."""
    assert _keywords("") == []


def test_keywords_all_stopwords_returns_empty() -> None:
    """A question made entirely of stop-words yields no keywords."""
    result = _keywords("what is the")
    assert result == []


def test_keywords_deduplicates_not_applied_but_all_present() -> None:
    """Keywords preserves order and includes each meaningful token."""
    tokens = _keywords("boom bap groove boom")
    assert "boom" in tokens
    assert "bap" in tokens
    assert "groove" in tokens


# ---------------------------------------------------------------------------
# _ask_async — basic matching
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ask_matches_keyword_in_commit_message(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``muse ask`` returns commits whose messages contain the keyword."""
    _init_muse_repo(tmp_path)
    await _make_commits(
        tmp_path,
        muse_cli_db_session,
        ["boom bap take 1", "jazz piano intro", "boom bap take 2"],
    )

    result = await _ask_async(
        question="boom bap",
        root=tmp_path,
        session=muse_cli_db_session,
        branch=None,
        since=None,
        until=None,
        cite=False,
    )

    assert result.total_searched == 3
    assert len(result.matches) == 2
    messages = [c.message for c in result.matches]
    assert all("boom bap" in m for m in messages)


@pytest.mark.anyio
async def test_ask_no_matches_returns_empty_list(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """A query with no matching commits returns an empty matches list."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["ambient drone take 1"])

    result = await _ask_async(
        question="hip hop",
        root=tmp_path,
        session=muse_cli_db_session,
        branch=None,
        since=None,
        until=None,
        cite=False,
    )

    assert result.total_searched == 1
    assert result.matches == []


@pytest.mark.anyio
async def test_ask_empty_repo_returns_zero_searched(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """On a repo with no commits the answer reports 0 commits searched."""
    _init_muse_repo(tmp_path)

    result = await _ask_async(
        question="anything",
        root=tmp_path,
        session=muse_cli_db_session,
        branch=None,
        since=None,
        until=None,
        cite=False,
    )

    assert result.total_searched == 0
    assert result.matches == []


# ---------------------------------------------------------------------------
# _ask_async — --branch filter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ask_branch_filter_restricts_search(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--branch`` restricts the search to commits on that branch."""
    _init_muse_repo(tmp_path)
    # Commit on main (default HEAD branch)
    await _make_commits(tmp_path, muse_cli_db_session, ["groove session main"])

    result = await _ask_async(
        question="groove",
        root=tmp_path,
        session=muse_cli_db_session,
        branch="other-branch",
        since=None,
        until=None,
        cite=False,
    )

    # No commits on other-branch → nothing searched
    assert result.total_searched == 0
    assert result.matches == []


@pytest.mark.anyio
async def test_ask_branch_filter_returns_matching_branch(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Commits on the specified branch are included in the search."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["groove session on main"])

    result = await _ask_async(
        question="groove",
        root=tmp_path,
        session=muse_cli_db_session,
        branch="main",
        since=None,
        until=None,
        cite=False,
    )

    assert result.total_searched == 1
    assert len(result.matches) == 1


# ---------------------------------------------------------------------------
# _ask_async — --since / --until filters
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ask_since_filter_excludes_older_commits(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--since`` excludes commits before the given date."""
    from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot, MuseCliObject

    _init_muse_repo(tmp_path)
    # Seed one commit using the high-level helper (today's date)
    await _make_commits(tmp_path, muse_cli_db_session, ["new session today"])

    # Use a future date to exclude everything
    future_date = date(2099, 1, 1)
    result = await _ask_async(
        question="session",
        root=tmp_path,
        session=muse_cli_db_session,
        branch=None,
        since=future_date,
        until=None,
        cite=False,
    )

    assert result.total_searched == 0


@pytest.mark.anyio
async def test_ask_until_filter_excludes_newer_commits(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--until`` excludes commits after the given date."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["new session today"])

    # Use a past date so today's commit is excluded
    past_date = date(2000, 1, 1)
    result = await _ask_async(
        question="session",
        root=tmp_path,
        session=muse_cli_db_session,
        branch=None,
        since=None,
        until=past_date,
        cite=False,
    )

    assert result.total_searched == 0


# ---------------------------------------------------------------------------
# AnswerResult rendering
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ask_plain_output_contains_expected_text(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Plain-text output contains the header and note lines."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["piano loop session"])

    result = await _ask_async(
        question="piano loop",
        root=tmp_path,
        session=muse_cli_db_session,
        branch=None,
        since=None,
        until=None,
        cite=False,
    )

    plain = result.to_plain()
    assert "Based on Muse history" in plain
    assert "commits searched" in plain
    assert "Note: Full LLM-powered answer generation" in plain
    assert "piano loop session" in plain


@pytest.mark.anyio
async def test_ask_cite_flag_shows_full_commit_id(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--cite`` flag makes the answer include the full 64-char commit ID."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["synth pad session"])

    result = await _ask_async(
        question="synth pad",
        root=tmp_path,
        session=muse_cli_db_session,
        branch=None,
        since=None,
        until=None,
        cite=True,
    )

    plain = result.to_plain()
    assert cids[0] in plain  # full 64-char ID present


@pytest.mark.anyio
async def test_ask_no_cite_shows_short_commit_id(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Without ``--cite`` only the short (8-char) commit ID appears."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["drum pattern session"])

    result = await _ask_async(
        question="drum pattern",
        root=tmp_path,
        session=muse_cli_db_session,
        branch=None,
        since=None,
        until=None,
        cite=False,
    )

    plain = result.to_plain()
    assert cids[0][:8] in plain
    # Full ID should NOT appear (only the 8-char prefix is shown)
    assert cids[0][8:] not in plain


@pytest.mark.anyio
async def test_ask_json_output_is_valid_json(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--json`` output is valid JSON with expected top-level keys."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["bass groove take 3"])

    result = await _ask_async(
        question="bass groove",
        root=tmp_path,
        session=muse_cli_db_session,
        branch=None,
        since=None,
        until=None,
        cite=False,
    )

    payload = json.loads(result.to_json())
    assert "question" in payload
    assert "total_searched" in payload
    assert "matches" in payload
    assert "note" in payload
    assert payload["question"] == "bass groove"
    assert payload["total_searched"] == 1
    assert len(payload["matches"]) == 1
    assert payload["matches"][0]["message"] == "bass groove take 3"


@pytest.mark.anyio
async def test_ask_json_cite_flag_includes_full_id(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--json --cite`` shows full commit IDs in the JSON output."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["keys session"])

    result = await _ask_async(
        question="keys",
        root=tmp_path,
        session=muse_cli_db_session,
        branch=None,
        since=None,
        until=None,
        cite=True,
    )

    payload = json.loads(result.to_json())
    assert payload["matches"][0]["commit_id"] == cids[0]


# ---------------------------------------------------------------------------
# CLI interface (CliRunner)
# ---------------------------------------------------------------------------


def test_ask_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse ask`` outside a .muse/ directory exits with code 2."""
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["ask", "anything"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == ExitCode.REPO_NOT_FOUND


def test_ask_plain_text_no_matches_message(tmp_path: pathlib.Path) -> None:
    """Plain text output for zero matches includes '(no matching commits)'."""
    result = AnswerResult(
        question="anything",
        total_searched=5,
        matches=[],
        cite=False,
    )
    plain = result.to_plain()
    assert "(no matching commits)" in plain
    assert "5 commits searched" in plain
