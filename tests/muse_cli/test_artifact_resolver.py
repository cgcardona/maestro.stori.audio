"""Tests for ``maestro.muse_cli.artifact_resolver``.

Covers:
- ``test_resolve_artifact_working_tree`` — existing file path resolves directly.
- ``test_resolve_artifact_from_commit`` — commit-ID prefix resolution via DB.
- ``test_resolve_artifact_file_not_found_exits_1`` — non-existent path exits 1.
- ``test_resolve_artifact_ambiguous_prefix_exits_1`` — multiple commit matches.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import uuid

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.artifact_resolver import resolve_artifact_async
from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot


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


def _populate_workdir(root: pathlib.Path, files: dict[str, bytes] | None = None) -> None:
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    if files is None:
        files = {"beat.mid": b"MIDI-DATA", "lead.mp3": b"MP3-DATA"}
    for name, content in files.items():
        (workdir / name).write_bytes(content)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_artifact_working_tree(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """An existing filesystem path resolves without touching the DB."""
    _init_muse_repo(tmp_path)
    target = tmp_path / "muse-work" / "beat.mid"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"MIDI")

    resolved = await resolve_artifact_async(
        str(target), root=tmp_path, session=muse_cli_db_session
    )

    assert resolved == target.resolve()


@pytest.mark.anyio
async def test_resolve_artifact_working_tree_relative_path(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """A filename relative to muse-work/ resolves correctly."""
    _init_muse_repo(tmp_path)
    (tmp_path / "muse-work").mkdir(parents=True, exist_ok=True)
    (tmp_path / "muse-work" / "lead.mp3").write_bytes(b"MP3")

    resolved = await resolve_artifact_async(
        "lead.mp3", root=tmp_path, session=muse_cli_db_session
    )

    assert resolved == (tmp_path / "muse-work" / "lead.mp3").resolve()


@pytest.mark.anyio
async def test_resolve_artifact_from_commit(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Commit-ID prefix resolves to the correct working-tree file."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"solo.mid": b"MIDI-SOLO"})

    commit_id = await _commit_async(
        message="test take",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    resolved = await resolve_artifact_async(
        commit_id[:8], root=tmp_path, session=muse_cli_db_session
    )

    assert resolved == (tmp_path / "muse-work" / "solo.mid").resolve()


@pytest.mark.anyio
async def test_resolve_artifact_file_not_found_exits_1(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """A non-existent path that is not a hex prefix exits with USER_ERROR."""
    _init_muse_repo(tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        await resolve_artifact_async(
            "no_such_file.mid", root=tmp_path, session=muse_cli_db_session
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_resolve_artifact_ambiguous_prefix_exits_1(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """An ambiguous commit prefix (matches > 1 commit) exits with USER_ERROR."""
    common_prefix = "aaaa"
    snap_id_1 = common_prefix + "b" * 60
    snap_id_2 = common_prefix + "c" * 60
    commit_id_1 = common_prefix + "d" * 60
    commit_id_2 = common_prefix + "e" * 60
    repo_id = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.timezone.utc)

    muse_cli_db_session.add(
        MuseCliSnapshot(snapshot_id=snap_id_1, manifest={"a.mid": "x" * 64})
    )
    muse_cli_db_session.add(
        MuseCliSnapshot(snapshot_id=snap_id_2, manifest={"b.mid": "y" * 64})
    )
    await muse_cli_db_session.flush()

    muse_cli_db_session.add(
        MuseCliCommit(
            commit_id=commit_id_1,
            repo_id=repo_id,
            branch="main",
            parent_commit_id=None,
            snapshot_id=snap_id_1,
            message="first",
            author="",
            committed_at=now,
        )
    )
    muse_cli_db_session.add(
        MuseCliCommit(
            commit_id=commit_id_2,
            repo_id=repo_id,
            branch="main",
            parent_commit_id=None,
            snapshot_id=snap_id_2,
            message="second",
            author="",
            committed_at=now,
        )
    )
    await muse_cli_db_session.flush()

    with pytest.raises(typer.Exit) as exc_info:
        await resolve_artifact_async(
            common_prefix, root=tmp_path, session=muse_cli_db_session
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR
