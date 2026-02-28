"""Tests for ``muse recall``.

All async tests call ``_recall_async`` directly with an in-memory SQLite
session and a ``tmp_path`` repo root — no real Postgres or running process
required.  Commits are seeded via ``_commit_async`` so the two commands
are exercised as an integrated pair.

Covers:
- Keyword match returns top-N results sorted by score (highest first).
- ``--limit`` restricts result count.
- ``--threshold`` filters low-scoring commits.
- ``--since`` / ``--until`` date filters.
- ``--json`` emits valid JSON with the expected schema.
- Query with zero matches returns empty result set (not an error).
- CLI invocation outside a repo exits with code 2.
- ``--since`` / ``--until`` with bad date strings exits with code 1.
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.recall import _recall_async, _score, _tokenize
from maestro.muse_cli.errors import ExitCode


# ---------------------------------------------------------------------------
# Repo / workdir helpers (mirror test_log.py pattern)
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
# Unit tests for scoring helpers
# ---------------------------------------------------------------------------


class TestScoringHelpers:
    """Unit tests for ``_tokenize`` and ``_score`` — no DB required."""

    def test_tokenize_splits_on_whitespace(self) -> None:
        assert _tokenize("dark jazz bassline") == {"dark", "jazz", "bassline"}

    def test_tokenize_is_lowercase(self) -> None:
        assert _tokenize("DARK Jazz") == {"dark", "jazz"}

    def test_tokenize_ignores_punctuation(self) -> None:
        tokens = _tokenize("boom, bap! drum-fill")
        assert "boom" in tokens
        assert "bap" in tokens
        assert "drum" in tokens
        assert "fill" in tokens

    def test_score_full_match_returns_one(self) -> None:
        q = _tokenize("jazz bassline")
        score = _score(q, "this is a jazz bassline")
        assert score == 1.0

    def test_score_no_match_returns_zero(self) -> None:
        q = _tokenize("jazz bassline")
        score = _score(q, "rock guitar solo")
        assert score == 0.0

    def test_score_partial_match(self) -> None:
        q = _tokenize("jazz drum fill")
        score = _score(q, "a cool jazz moment")
        assert 0.0 < score < 1.0
        assert score == pytest.approx(1 / 3, rel=1e-6)

    def test_score_empty_query_returns_zero(self) -> None:
        score = _score(set(), "anything")
        assert score == 0.0


# ---------------------------------------------------------------------------
# test_recall_returns_top_n_by_score
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recall_returns_top_n_by_score(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Commits with higher keyword overlap rank before those with lower overlap."""
    _init_muse_repo(tmp_path)
    await _make_commits(
        tmp_path,
        muse_cli_db_session,
        [
            "boom bap drum pattern",
            "jazz piano chord voicing",
            "boom bap jazz fusion groove",
            "classical string quartet",
        ],
    )

    results = await _recall_async(
        root=tmp_path,
        session=muse_cli_db_session,
        query="boom bap jazz",
        limit=5,
        threshold=0.0,
        branch=None,
        since=None,
        until=None,
        as_json=False,
    )

    assert len(results) > 0
    assert results[0]["message"] == "boom bap jazz fusion groove"
    assert results[0]["score"] == pytest.approx(1.0)

    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# test_recall_limit_restricts_result_count
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recall_limit_restricts_result_count(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--limit 2`` returns at most 2 results even if more match."""
    _init_muse_repo(tmp_path)
    await _make_commits(
        tmp_path,
        muse_cli_db_session,
        [
            "jazz piano solo",
            "jazz drum groove",
            "jazz bass walk",
            "jazz chord progression",
        ],
    )

    results = await _recall_async(
        root=tmp_path,
        session=muse_cli_db_session,
        query="jazz",
        limit=2,
        threshold=0.0,
        branch=None,
        since=None,
        until=None,
        as_json=False,
    )

    assert len(results) == 2


# ---------------------------------------------------------------------------
# test_recall_threshold_filters_low_scores
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recall_threshold_filters_low_scores(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Commits with score < threshold are excluded from results."""
    _init_muse_repo(tmp_path)
    await _make_commits(
        tmp_path,
        muse_cli_db_session,
        [
            "jazz piano solo",
            "classical strings",
        ],
    )

    results = await _recall_async(
        root=tmp_path,
        session=muse_cli_db_session,
        query="jazz",
        limit=5,
        threshold=0.6,
        branch=None,
        since=None,
        until=None,
        as_json=False,
    )

    assert all(r["score"] >= 0.6 for r in results)
    messages = [r["message"] for r in results]
    assert "jazz piano solo" in messages
    assert "classical strings" not in messages


# ---------------------------------------------------------------------------
# test_recall_no_matches_returns_empty
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recall_no_matches_returns_empty(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A query that matches nothing returns an empty list (not an error)."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["rock guitar riff"])

    results = await _recall_async(
        root=tmp_path,
        session=muse_cli_db_session,
        query="jazz bassline",
        limit=5,
        threshold=0.6,
        branch=None,
        since=None,
        until=None,
        as_json=False,
    )

    assert results == []
    out = capsys.readouterr().out
    assert "No matching commits found" in out


# ---------------------------------------------------------------------------
# test_recall_json_output_valid_schema
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recall_json_output_valid_schema(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--json`` output is valid JSON with expected fields."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["jazz drum groove"])
    capsys.readouterr()  # discard commit output before testing recall JSON

    await _recall_async(
        root=tmp_path,
        session=muse_cli_db_session,
        query="jazz drum",
        limit=5,
        threshold=0.0,
        branch=None,
        since=None,
        until=None,
        as_json=True,
    )

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) >= 1

    entry = parsed[0]
    assert "rank" in entry
    assert "score" in entry
    assert "commit_id" in entry
    assert "date" in entry
    assert "branch" in entry
    assert "message" in entry
    assert entry["rank"] == 1
    assert isinstance(entry["score"], float)
    assert entry["branch"] == "main"


# ---------------------------------------------------------------------------
# test_recall_rank_field_is_sequential
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recall_rank_field_is_sequential(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``rank`` starts at 1 and increments sequentially."""
    _init_muse_repo(tmp_path)
    await _make_commits(
        tmp_path,
        muse_cli_db_session,
        ["jazz piano", "jazz drums", "jazz bass"],
    )

    results = await _recall_async(
        root=tmp_path,
        session=muse_cli_db_session,
        query="jazz",
        limit=5,
        threshold=0.0,
        branch=None,
        since=None,
        until=None,
        as_json=False,
    )

    assert [r["rank"] for r in results] == list(range(1, len(results) + 1))


# ---------------------------------------------------------------------------
# test_recall_since_filter_excludes_older_commits
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recall_since_filter_excludes_older_commits(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--since`` set to the future excludes all commits."""
    from datetime import datetime, timezone

    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["jazz rhythm section"])

    future = datetime(2099, 1, 1, tzinfo=timezone.utc)

    results = await _recall_async(
        root=tmp_path,
        session=muse_cli_db_session,
        query="jazz",
        limit=5,
        threshold=0.0,
        branch=None,
        since=future,
        until=None,
        as_json=False,
    )

    assert results == []


# ---------------------------------------------------------------------------
# test_recall_until_filter_excludes_newer_commits
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recall_until_filter_excludes_newer_commits(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--until`` set in the past excludes all commits."""
    from datetime import datetime, timezone

    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["jazz rhythm section"])

    past = datetime(2000, 1, 1, tzinfo=timezone.utc)

    results = await _recall_async(
        root=tmp_path,
        session=muse_cli_db_session,
        query="jazz",
        limit=5,
        threshold=0.0,
        branch=None,
        since=None,
        until=past,
        as_json=False,
    )

    assert results == []


# ---------------------------------------------------------------------------
# test_recall_outside_repo_exits_2
# ---------------------------------------------------------------------------


def test_recall_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse recall`` outside a .muse/ directory exits with code 2."""
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["recall", "jazz"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == ExitCode.REPO_NOT_FOUND


# ---------------------------------------------------------------------------
# test_recall_bad_since_date_exits_1
# ---------------------------------------------------------------------------


def test_recall_bad_since_date_exits_1() -> None:
    """``--since`` with a non-YYYY-MM-DD value exits with code 1.

    Date validation occurs before repo discovery so no repo setup is needed.
    """
    import typer
    from maestro.muse_cli.commands.recall import recall as recall_cmd

    with pytest.raises(typer.Exit) as exc_info:
        recall_cmd(
            ctx=None,  # type: ignore[arg-type]
            query="jazz",
            limit=5,
            threshold=0.6,
            branch=None,
            since="not-a-date",
            until=None,
            as_json=False,
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR



# ---------------------------------------------------------------------------
# test_recall_bad_until_date_exits_1
# ---------------------------------------------------------------------------


def test_recall_bad_until_date_exits_1() -> None:
    """``--until`` with a non-YYYY-MM-DD value exits with code 1.

    Date validation occurs before repo discovery so no repo setup is needed.
    """
    import typer
    from maestro.muse_cli.commands.recall import recall as recall_cmd

    with pytest.raises(typer.Exit) as exc_info:
        recall_cmd(
            ctx=None,  # type: ignore[arg-type]
            query="jazz",
            limit=5,
            threshold=0.6,
            branch=None,
            since=None,
            until="2026/01/01",
            as_json=False,
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR
