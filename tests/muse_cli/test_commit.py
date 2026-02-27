"""Integration tests for ``muse commit``.

Tests exercise ``_commit_async`` directly with an in-memory SQLite session
so no real Postgres instance is required.  The ``muse_cli_db_session``
fixture (defined in tests/muse_cli/conftest.py) provides the isolated
SQLite session.

All async tests use ``@pytest.mark.anyio`` (configured for asyncio mode
in pyproject.toml).
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliObject, MuseCliSnapshot
from maestro.muse_cli.snapshot import (
    build_snapshot_manifest,
    compute_snapshot_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Create a minimal .muse/ layout so _commit_async can read repo state."""
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
    """Create muse-work/ with one or more files."""
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    if files is None:
        files = {"beat.mid": b"MIDI-DATA", "lead.mp3": b"MP3-DATA"}
    for name, content in files.items():
        (workdir / name).write_bytes(content)


# ---------------------------------------------------------------------------
# Basic commit creation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_creates_postgres_row(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    repo_id = _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    commit_id = await _commit_async(
        message="boom bap demo take 1",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == commit_id)
    )
    row = result.scalar_one_or_none()
    assert row is not None, "commit row must exist after _commit_async"
    assert row.message == "boom bap demo take 1"
    assert row.repo_id == repo_id
    assert row.branch == "main"


@pytest.mark.anyio
async def test_commit_id_is_deterministic(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """commit_id is a 64-char sha256 hex string stored exactly once in DB.

    Pure determinism of ``compute_commit_id`` is covered by
    ``test_snapshot.py::test_commit_id_parametrized_deterministic``.
    Here we verify the integration contract: _commit_async returns a
    valid object ID and the row is findable by that ID.
    """
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"track.mid": b"CONSISTENT"})

    commit_id = await _commit_async(
        message="determinism check",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Valid sha256 hex digest
    assert len(commit_id) == 64
    assert all(c in "0123456789abcdef" for c in commit_id)

    # Stored in DB and findable by its own ID (no duplication)
    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == commit_id)
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].message == "determinism check"


@pytest.mark.anyio
async def test_commit_snapshot_content_addressed_same_files_same_id(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Same files → same snapshot_id on two successive commits."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"a.mid": b"CONSTANT"})

    cid1 = await _commit_async(
        message="first", root=tmp_path, session=muse_cli_db_session
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == cid1)
    )
    snap_id_1 = result.scalar_one().snapshot_id

    # Manually compute expected snapshot_id from the on-disk files
    manifest = build_snapshot_manifest(tmp_path / "muse-work")
    assert compute_snapshot_id(manifest) == snap_id_1


@pytest.mark.anyio
async def test_commit_snapshot_content_addressed_changed_file_new_id(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Changing a file produces a different snapshot_id."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"a.mid": b"VERSION1"})

    cid1 = await _commit_async(
        message="v1", root=tmp_path, session=muse_cli_db_session
    )
    r1 = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == cid1)
    )
    snap1 = r1.scalar_one().snapshot_id

    # Change file content and commit again
    (tmp_path / "muse-work" / "a.mid").write_bytes(b"VERSION2")
    cid2 = await _commit_async(
        message="v2", root=tmp_path, session=muse_cli_db_session
    )
    r2 = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == cid2)
    )
    snap2 = r2.scalar_one().snapshot_id

    assert snap1 != snap2


@pytest.mark.anyio
async def test_commit_moves_branch_head(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """After commit, .muse/refs/heads/main contains the new commit_id."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    commit_id = await _commit_async(
        message="update head", root=tmp_path, session=muse_cli_db_session
    )

    ref_content = (tmp_path / ".muse" / "refs" / "heads" / "main").read_text().strip()
    assert ref_content == commit_id


@pytest.mark.anyio
async def test_commit_sets_parent_pointer(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Second commit's parent_commit_id equals the first commit_id."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"V1"})

    cid1 = await _commit_async(
        message="first", root=tmp_path, session=muse_cli_db_session
    )

    # Change content so it's not a "nothing to commit" situation
    (tmp_path / "muse-work" / "beat.mid").write_bytes(b"V2")
    cid2 = await _commit_async(
        message="second", root=tmp_path, session=muse_cli_db_session
    )

    r2 = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == cid2)
    )
    row2 = r2.scalar_one()
    assert row2.parent_commit_id == cid1


@pytest.mark.anyio
async def test_commit_objects_are_deduplicated(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """The same file committed twice → exactly one object row in DB."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"SHARED"})

    await _commit_async(message="c1", root=tmp_path, session=muse_cli_db_session)

    # Second workdir with same file content but different name → same object_id
    (tmp_path / "muse-work" / "copy.mid").write_bytes(b"SHARED")
    await _commit_async(message="c2", root=tmp_path, session=muse_cli_db_session)

    result = await muse_cli_db_session.execute(select(MuseCliObject))
    all_objects = result.scalars().all()
    object_ids = {o.object_id for o in all_objects}
    # Both files have identical bytes → same object_id → only 1 row for that content
    import hashlib
    shared_oid = hashlib.sha256(b"SHARED").hexdigest()
    assert shared_oid in object_ids
    # Ensure no duplicate rows for shared_oid
    shared_rows = [o for o in all_objects if o.object_id == shared_oid]
    assert len(shared_rows) == 1


# ---------------------------------------------------------------------------
# Nothing to commit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_nothing_to_commit_exits_zero(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Committing the same working tree twice exits 0 with the clean-tree message."""
    import typer

    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    await _commit_async(
        message="initial", root=tmp_path, session=muse_cli_db_session
    )

    # Second commit with unchanged tree should exit 0
    with pytest.raises(typer.Exit) as exc_info:
        await _commit_async(
            message="nothing changed", root=tmp_path, session=muse_cli_db_session
        )

    assert exc_info.value.exit_code == ExitCode.SUCCESS

    captured = capsys.readouterr()
    assert "Nothing to commit" in captured.out


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_outside_repo_exits_2(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """_commit_async never calls require_repo — that's the Typer callback's job.
    This test uses the Typer CLI runner to verify exit code 2 when there is
    no .muse/ directory.
    """
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["commit", "-m", "no repo"], catch_exceptions=False)
    assert result.exit_code == ExitCode.REPO_NOT_FOUND


@pytest.mark.anyio
async def test_commit_no_workdir_exits_1(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """When muse-work/ does not exist, commit exits with USER_ERROR (1)."""
    import typer

    _init_muse_repo(tmp_path)
    # Deliberately do NOT create muse-work/

    with pytest.raises(typer.Exit) as exc_info:
        await _commit_async(
            message="no workdir", root=tmp_path, session=muse_cli_db_session
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_commit_empty_workdir_exits_1(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """When muse-work/ exists but is empty, commit exits with USER_ERROR (1)."""
    import typer

    _init_muse_repo(tmp_path)
    (tmp_path / "muse-work").mkdir()  # empty directory

    with pytest.raises(typer.Exit) as exc_info:
        await _commit_async(
            message="empty", root=tmp_path, session=muse_cli_db_session
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR
