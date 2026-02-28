"""Tests for ``muse amend``.

Exercises ``_amend_async`` directly with an in-memory SQLite session so no
real Postgres instance is required.  The ``muse_cli_db_session`` fixture
(defined in tests/muse_cli/conftest.py) provides the isolated session.

Test coverage
-------------
- ``test_muse_amend_updates_last_commit``  — regression: amend replaces HEAD ref
- ``test_muse_amend_message_only``         — -m flag updates message
- ``test_muse_amend_blocked_during_merge`` — blocked when MERGE_STATE.json exists
- Plus: no-commits guard, parent inheritance, --no-edit, --reset-author,
        outside-repo exit code, empty muse-work/ guard, missing muse-work/ guard.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli.commands.amend import _amend_async
from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit


# ---------------------------------------------------------------------------
# Helpers (mirrors commit tests to keep the fixture surface minimal)
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Create a minimal .muse/ layout so _amend_async can read repo state."""
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


async def _make_commit(
    root: pathlib.Path,
    session: AsyncSession,
    *,
    message: str = "initial commit",
    files: dict[str, bytes] | None = None,
) -> str:
    """Helper: populate workdir and run _commit_async, return commit_id."""
    _populate_workdir(root, files=files)
    return await _commit_async(message=message, root=root, session=session)


# ---------------------------------------------------------------------------
# Core regression tests (named per issue acceptance criteria)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_amend_updates_last_commit(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Amend re-snapshots muse-work/ and updates .muse/refs/heads/<branch>."""
    _init_muse_repo(tmp_path)
    original_id = await _make_commit(
        tmp_path, muse_cli_db_session, message="original", files={"a.mid": b"V1"}
    )

    # Modify the working tree
    (tmp_path / "muse-work" / "a.mid").write_bytes(b"V2")

    new_id = await _amend_async(
        message=None,
        no_edit=True,
        reset_author=False,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # HEAD ref must point to the new commit
    ref_content = (tmp_path / ".muse" / "refs" / "heads" / "main").read_text().strip()
    assert ref_content == new_id
    assert new_id != original_id


@pytest.mark.anyio
async def test_muse_amend_message_only(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Amend with -m replaces the commit message."""
    _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session, message="old message")

    new_id = await _amend_async(
        message="new message",
        no_edit=False,
        reset_author=False,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == new_id)
    )
    row = result.scalar_one()
    assert row.message == "new message"


@pytest.mark.anyio
async def test_muse_amend_blocked_during_merge(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Amend is blocked when MERGE_STATE.json exists (merge in progress)."""
    _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session)

    # Simulate an in-progress merge
    merge_state = {
        "base_commit": "abc",
        "ours_commit": "def",
        "theirs_commit": "ghi",
        "conflict_paths": ["beat.mid"],
        "other_branch": "feature/x",
    }
    (tmp_path / ".muse" / "MERGE_STATE.json").write_text(json.dumps(merge_state))

    with pytest.raises(typer.Exit) as exc_info:
        await _amend_async(
            message=None,
            no_edit=True,
            reset_author=False,
            root=tmp_path,
            session=muse_cli_db_session,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Guard: no commits yet
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_amend_no_commits_exits_1(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Amend with no prior commits exits USER_ERROR."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        await _amend_async(
            message="oops",
            no_edit=False,
            reset_author=False,
            root=tmp_path,
            session=muse_cli_db_session,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Parent inheritance — amended commit keeps original's parent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_amend_preserves_grandparent(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """The amended commit's parent is the *original commit's parent*, not the original itself."""
    _init_muse_repo(tmp_path)

    # Commit 1
    cid1 = await _make_commit(
        tmp_path, muse_cli_db_session, message="commit 1", files={"a.mid": b"V1"}
    )

    # Commit 2 (this is HEAD, the one we will amend)
    (tmp_path / "muse-work" / "a.mid").write_bytes(b"V2")
    _cid2 = await _commit_async(
        message="commit 2", root=tmp_path, session=muse_cli_db_session
    )

    # Amend commit 2
    (tmp_path / "muse-work" / "a.mid").write_bytes(b"V3")
    amended_id = await _amend_async(
        message="commit 2 amended",
        no_edit=False,
        reset_author=False,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == amended_id)
    )
    amended_row = result.scalar_one()
    # The amended commit's parent must be commit 1, not commit 2
    assert amended_row.parent_commit_id == cid1


@pytest.mark.anyio
async def test_muse_amend_first_commit_has_no_parent(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Amending the very first commit produces a root commit (parent_commit_id is None)."""
    _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session, message="root commit")

    # Amend — workdir unchanged, just new message
    amended_id = await _amend_async(
        message="root amended",
        no_edit=False,
        reset_author=False,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == amended_id)
    )
    row = result.scalar_one()
    assert row.parent_commit_id is None


# ---------------------------------------------------------------------------
# --no-edit keeps original message
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_amend_no_edit_keeps_original_message(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--no-edit preserves the original commit message even when -m is also provided."""
    _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session, message="keep this message")

    # Modify workdir so we get a new snapshot
    (tmp_path / "muse-work" / "beat.mid").write_bytes(b"UPDATED")

    # Supply -m but also --no-edit; --no-edit wins
    amended_id = await _amend_async(
        message="should be ignored",
        no_edit=True,
        reset_author=False,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == amended_id)
    )
    row = result.scalar_one()
    assert row.message == "keep this message"


@pytest.mark.anyio
async def test_muse_amend_no_message_flag_keeps_original(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """When neither -m nor --no-edit is supplied, original message is kept."""
    _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session, message="original message")

    (tmp_path / "muse-work" / "beat.mid").write_bytes(b"UPDATED")

    amended_id = await _amend_async(
        message=None,
        no_edit=False,
        reset_author=False,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == amended_id)
    )
    row = result.scalar_one()
    assert row.message == "original message"


# ---------------------------------------------------------------------------
# --reset-author (stub: always produces empty author string)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_amend_reset_author_flag_accepted(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--reset-author is accepted without error (stub implementation)."""
    _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session)

    (tmp_path / "muse-work" / "beat.mid").write_bytes(b"UPDATED")

    amended_id = await _amend_async(
        message=None,
        no_edit=True,
        reset_author=True,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == amended_id)
    )
    row = result.scalar_one()
    # Stub: always empty string until a user-identity system is added
    assert row.author == ""


# ---------------------------------------------------------------------------
# Empty / missing muse-work/ guards
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_amend_missing_workdir_exits_1(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """When muse-work/ does not exist, amend exits USER_ERROR."""
    _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session)

    # Remove muse-work/ entirely
    import shutil
    shutil.rmtree(tmp_path / "muse-work")

    with pytest.raises(typer.Exit) as exc_info:
        await _amend_async(
            message=None,
            no_edit=True,
            reset_author=False,
            root=tmp_path,
            session=muse_cli_db_session,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_muse_amend_empty_workdir_exits_1(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """When muse-work/ is empty, amend exits USER_ERROR."""
    _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session)

    # Empty the workdir
    import shutil
    shutil.rmtree(tmp_path / "muse-work")
    (tmp_path / "muse-work").mkdir()

    with pytest.raises(typer.Exit) as exc_info:
        await _amend_async(
            message=None,
            no_edit=True,
            reset_author=False,
            root=tmp_path,
            session=muse_cli_db_session,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Outside-repo exit code (Typer CLI runner)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_amend_outside_repo_exits_2(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``muse amend`` exits REPO_NOT_FOUND (2) when not inside a Muse repo."""
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["amend", "--no-edit"], catch_exceptions=False)
    assert result.exit_code == ExitCode.REPO_NOT_FOUND


# ---------------------------------------------------------------------------
# Amended commit is stored in DB and head ref is updated
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_amend_new_commit_stored_in_db(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """The amended commit row exists in DB with correct repo_id and branch."""
    rid = _init_muse_repo(tmp_path)
    await _make_commit(tmp_path, muse_cli_db_session, message="before amend")

    (tmp_path / "muse-work" / "beat.mid").write_bytes(b"POST-AMEND")

    amended_id = await _amend_async(
        message="after amend",
        no_edit=False,
        reset_author=False,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == amended_id)
    )
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.repo_id == rid
    assert row.branch == "main"
    assert row.message == "after amend"
