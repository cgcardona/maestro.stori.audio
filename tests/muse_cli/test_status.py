"""Tests for ``muse status`` — working-tree diff and merge state display.

All DB-dependent tests use ``_status_async`` directly with an in-memory
SQLite session (via the ``muse_cli_db_session`` fixture in conftest.py)
so no real Postgres instance is required.

Async tests use ``@pytest.mark.anyio`` (configured for asyncio mode in
pyproject.toml).
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.status import _status_async
from maestro.muse_cli.errors import ExitCode


# ---------------------------------------------------------------------------
# Helpers (mirror commit test helpers to keep tests self-contained)
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Create a minimal .muse/ layout."""
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": rid, "schema_version": "1"})
    )
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")  # no commits yet
    return rid


def _populate_workdir(root: pathlib.Path, files: dict[str, bytes] | None = None) -> None:
    """Create muse-work/ with the given files."""
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    if files is None:
        files = {"beat.mid": b"MIDI-DATA"}
    for name, content in files.items():
        (workdir / name).write_bytes(content)


# ---------------------------------------------------------------------------
# Clean working tree
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_status_clean_after_commit(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """After a commit with no subsequent changes, status reports a clean tree."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"MIDI"})

    await _commit_async(
        message="initial commit",
        root=tmp_path,
        session=muse_cli_db_session,
    )
    # Flush so the snapshot row is visible to _status_async in the same session.
    await muse_cli_db_session.flush()

    await _status_async(root=tmp_path, session=muse_cli_db_session)

    captured = capsys.readouterr()
    assert "nothing to commit, working tree clean" in captured.out


# ---------------------------------------------------------------------------
# Uncommitted changes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_status_shows_modified_file(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A file changed after the last commit appears as 'modified:'."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"VERSION1"})

    await _commit_async(
        message="initial",
        root=tmp_path,
        session=muse_cli_db_session,
    )
    await muse_cli_db_session.flush()

    # Modify the file without committing.
    (tmp_path / "muse-work" / "beat.mid").write_bytes(b"VERSION2")

    await _status_async(root=tmp_path, session=muse_cli_db_session)

    captured = capsys.readouterr()
    assert "modified:" in captured.out
    assert "beat.mid" in captured.out


@pytest.mark.anyio
async def test_status_shows_new_file(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A file added to muse-work/ after the last commit appears as 'new file:'."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"MIDI"})

    await _commit_async(
        message="initial",
        root=tmp_path,
        session=muse_cli_db_session,
    )
    await muse_cli_db_session.flush()

    # Add a new file that was not in the committed snapshot.
    (tmp_path / "muse-work" / "lead.mp3").write_bytes(b"MP3")

    await _status_async(root=tmp_path, session=muse_cli_db_session)

    captured = capsys.readouterr()
    assert "new file:" in captured.out
    assert "lead.mp3" in captured.out


@pytest.mark.anyio
async def test_status_shows_deleted_file(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A file removed from muse-work/ after the last commit appears as 'deleted:'."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"MIDI", "scratch.mid": b"TMP"})

    await _commit_async(
        message="initial",
        root=tmp_path,
        session=muse_cli_db_session,
    )
    await muse_cli_db_session.flush()

    # Remove one file without committing.
    (tmp_path / "muse-work" / "scratch.mid").unlink()

    await _status_async(root=tmp_path, session=muse_cli_db_session)

    captured = capsys.readouterr()
    assert "deleted:" in captured.out
    assert "scratch.mid" in captured.out


# ---------------------------------------------------------------------------
# Untracked files (no commits yet)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_status_shows_untracked(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Files in muse-work/ on a branch with no commits are listed as untracked."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"MIDI", "lead.mp3": b"MP3"})

    # Do NOT commit — branch has no history.
    await _status_async(root=tmp_path, session=muse_cli_db_session)

    captured = capsys.readouterr()
    assert "Untracked files" in captured.out
    assert "beat.mid" in captured.out
    assert "lead.mp3" in captured.out


# ---------------------------------------------------------------------------
# In-progress merge
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_status_during_merge_shows_conflicts(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When MERGE_STATE.json is present, conflict paths appear with 'both modified:'."""
    _init_muse_repo(tmp_path)

    merge_state = {
        "conflicts": ["beat.mid", "lead.mp3"],
        "other_branch": "feature/variation-b",
    }
    (tmp_path / ".muse" / "MERGE_STATE.json").write_text(json.dumps(merge_state))

    await _status_async(root=tmp_path, session=muse_cli_db_session)

    captured = capsys.readouterr()
    assert "You have unmerged paths" in captured.out
    assert "both modified:" in captured.out
    assert "beat.mid" in captured.out
    assert "lead.mp3" in captured.out


# ---------------------------------------------------------------------------
# No commits yet (clean working tree)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_status_no_commits_yet(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A repo with no commits and no muse-work/ files shows 'no commits yet'."""
    _init_muse_repo(tmp_path)
    # No muse-work/ directory, no commits.

    await _status_async(root=tmp_path, session=muse_cli_db_session)

    captured = capsys.readouterr()
    assert "no commits yet" in captured.out


# ---------------------------------------------------------------------------
# Outside a repo
# ---------------------------------------------------------------------------


def test_status_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse status`` exits 2 when there is no ``.muse/`` directory."""
    from typer.testing import CliRunner

    from maestro.muse_cli.app import cli

    runner = CliRunner()

    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["status"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == int(ExitCode.REPO_NOT_FOUND)
    assert "not a muse repository" in result.output.lower()
