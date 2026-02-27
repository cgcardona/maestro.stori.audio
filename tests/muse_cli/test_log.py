"""Tests for ``muse log``.

All async tests call ``_log_async`` directly with an in-memory SQLite
session and a ``tmp_path`` repo root — no real Postgres or running process
required.  Commits are seeded via ``_commit_async`` so the two commands
are tested as an integrated pair.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.log import _log_async
from maestro.muse_cli.errors import ExitCode


# ---------------------------------------------------------------------------
# Helpers (shared with test_commit.py pattern)
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
# test_log_shows_commits_newest_first
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_shows_commits_newest_first(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Three sequential commits appear in the log newest-first."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["take 1", "take 2", "take 3"])

    capsys.readouterr()  # discard ✅ output from _commit_async calls
    await _log_async(
        root=tmp_path, session=muse_cli_db_session, limit=1000, graph=False
    )
    out = capsys.readouterr().out

    # All three commit IDs should appear
    for cid in cids:
        assert cid in out

    # Newest (take 3) should appear before oldest (take 1)
    assert out.index(cids[2]) < out.index(cids[0])
    # take 3 message first, take 1 last
    assert out.index("take 3") < out.index("take 1")


# ---------------------------------------------------------------------------
# test_log_shows_correct_parent_chain
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_shows_correct_parent_chain(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each commit's ``Parent:`` line shows the short ID of its predecessor."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["first", "second", "third"])

    await _log_async(
        root=tmp_path, session=muse_cli_db_session, limit=1000, graph=False
    )
    out = capsys.readouterr().out

    # "third" commit (cids[2]) should show cids[1][:8] as its parent
    assert f"Parent: {cids[1][:8]}" in out
    # "second" commit shows cids[0][:8] as parent
    assert f"Parent: {cids[0][:8]}" in out
    # "first" commit has no parent — no Parent line for it
    lines = out.splitlines()
    # Find the block for the first commit (last in output = oldest)
    first_commit_idx = out.index(cids[0])
    first_commit_block = out[first_commit_idx:]
    # The first block should not have a Parent: line before the next "commit " line
    next_commit = first_commit_block.find("commit ", 8)  # skip the "commit <id>" itself
    if next_commit == -1:
        block = first_commit_block
    else:
        block = first_commit_block[:next_commit]
    assert "Parent:" not in block


# ---------------------------------------------------------------------------
# test_log_limit_restricts_output
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_limit_restricts_output(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--limit 2 shows exactly the two most recent commits."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(
        tmp_path, muse_cli_db_session, ["take 1", "take 2", "take 3"]
    )

    await _log_async(
        root=tmp_path, session=muse_cli_db_session, limit=2, graph=False
    )
    out = capsys.readouterr().out

    assert out.count("commit ") == 2
    # Most recent two appear
    assert cids[2] in out  # take 3
    assert cids[1] in out  # take 2
    # Oldest excluded
    assert cids[0] not in out


# ---------------------------------------------------------------------------
# test_log_shows_single_parent_line
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_shows_single_parent_line(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A commit with one parent shows exactly one ``Parent:`` line.

    Note: merge commits with two parents (``parent2_commit_id``) are
    deferred to issue #35 (``muse merge``).  This test documents the
    current single-parent behavior.
    """
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["v1", "v2"])

    await _log_async(
        root=tmp_path, session=muse_cli_db_session, limit=1000, graph=False
    )
    out = capsys.readouterr().out

    # Only one Parent: line in the entire output (the second commit references the first)
    assert out.count("Parent:") == 1
    assert f"Parent: {cids[0][:8]}" in out


# ---------------------------------------------------------------------------
# test_log_no_commits_exits_zero
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_no_commits_exits_zero(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``muse log`` on a repo with no commits exits 0 with a friendly message."""
    import typer

    _init_muse_repo(tmp_path)
    # Deliberately do NOT commit anything

    with pytest.raises(typer.Exit) as exc_info:
        await _log_async(
            root=tmp_path, session=muse_cli_db_session, limit=1000, graph=False
        )

    assert exc_info.value.exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "No commits yet" in out
    assert "main" in out


# ---------------------------------------------------------------------------
# test_log_outside_repo_exits_2
# ---------------------------------------------------------------------------


def test_log_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse log`` outside a .muse/ directory exits with code 2."""
    import os
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["log"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == ExitCode.REPO_NOT_FOUND


# ---------------------------------------------------------------------------
# test_log_graph_produces_ascii_output
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_graph_produces_ascii_output(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--graph`` output contains ASCII graph characters (* and |)."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["beat 1", "beat 2", "beat 3"])

    await _log_async(
        root=tmp_path, session=muse_cli_db_session, limit=1000, graph=True
    )
    out = capsys.readouterr().out

    assert "*" in out
    # Each commit message should appear in the graph output
    assert "beat 1" in out
    assert "beat 2" in out
    assert "beat 3" in out


# ---------------------------------------------------------------------------
# test_log_head_marker_on_newest_commit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_head_marker_on_newest_commit(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The most recent commit is labelled ``(HEAD -> main)``."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["first", "second"])

    await _log_async(
        root=tmp_path, session=muse_cli_db_session, limit=1000, graph=False
    )
    out = capsys.readouterr().out

    # Only the newest commit (cids[1]) carries the HEAD marker
    assert f"{cids[1]}  (HEAD -> main)" in out
    # Older commit does NOT carry it
    assert f"{cids[0]}  (HEAD -> main)" not in out


# ---------------------------------------------------------------------------
# test_log_limit_one_shows_only_head
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_log_limit_one_shows_only_head(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--limit 1`` shows only the HEAD commit, regardless of chain length."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["a", "b", "c"])

    await _log_async(
        root=tmp_path, session=muse_cli_db_session, limit=1, graph=False
    )
    out = capsys.readouterr().out

    assert out.count("commit ") == 1
    assert cids[2] in out
    assert cids[1] not in out
    assert cids[0] not in out
