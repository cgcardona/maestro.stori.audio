"""Unit and integration tests for the ``muse resolve``, ``muse merge --continue``,
``muse merge --abort``, and conflict-aware ``muse status`` commands.

All async tests use ``@pytest.mark.anyio``.  Tests exercise the testable async
cores directly with in-memory SQLite and ``tmp_path`` so no real Postgres or
Docker instance is required.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.merge import _merge_abort_async, _merge_async, _merge_continue_async
from maestro.muse_cli.commands.resolve import resolve_conflict_async
from maestro.muse_cli.commands.status import _status_async
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.merge_engine import (
    apply_resolution,
    is_conflict_resolved,
    read_merge_state,
    write_merge_state,
)
from maestro.muse_cli.object_store import write_object


# ---------------------------------------------------------------------------
# Test helpers (shared with other muse_cli test modules)
# ---------------------------------------------------------------------------


def _init_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Create minimal ``.muse/`` layout for testing."""
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _write_workdir(root: pathlib.Path, files: dict[str, bytes]) -> None:
    """Overwrite muse-work/ with exactly the given files."""
    import shutil

    workdir = root / "muse-work"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir()
    for name, content in files.items():
        (workdir / name).write_bytes(content)


def _create_branch(root: pathlib.Path, branch: str, from_branch: str = "main") -> None:
    muse = root / ".muse"
    src = muse / "refs" / "heads" / from_branch
    dst = muse / "refs" / "heads" / branch
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text() if src.exists() else "")


def _switch_branch(root: pathlib.Path, branch: str) -> None:
    (root / ".muse" / "HEAD").write_text(f"refs/heads/{branch}")


def _head_commit(root: pathlib.Path, branch: str | None = None) -> str:
    muse = root / ".muse"
    if branch is None:
        head_ref = (muse / "HEAD").read_text().strip()
        branch = head_ref.rsplit("/", 1)[-1]
    ref_path = muse / "refs" / "heads" / branch
    return ref_path.read_text().strip() if ref_path.exists() else ""


# ---------------------------------------------------------------------------
# muse status — conflict display during merge
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_status_shows_conflicts_during_merge(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    """Status during a merge shows unmerged paths and the --continue hint."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"beat.mid": b"BASE"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)

    write_merge_state(
        tmp_path,
        base_commit="base000",
        ours_commit="ours111",
        theirs_commit="their222",
        conflict_paths=["beat.mid", "lead.mid"],
        other_branch="experiment",
    )

    await _status_async(root=tmp_path, session=muse_cli_db_session)
    output = capsys.readouterr().out

    assert "You have unmerged paths." in output
    assert "muse merge --continue" in output
    assert "beat.mid" in output
    assert "lead.mid" in output


# ---------------------------------------------------------------------------
# muse resolve --ours
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_ours_removes_from_conflict_list(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """``--ours`` removes the path from MERGE_STATE.json conflict_paths."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"beat.mid": b"OURS"})
    await _commit_async(message="initial", root=tmp_path, session=muse_cli_db_session)

    write_merge_state(
        tmp_path,
        base_commit="base000",
        ours_commit="ours111",
        theirs_commit="their222",
        conflict_paths=["beat.mid"],
    )

    await resolve_conflict_async(
        file_path="beat.mid", ours=True, root=tmp_path, session=muse_cli_db_session
    )

    state = read_merge_state(tmp_path)
    assert state is not None
    # Path must be gone from conflict_paths but MERGE_STATE.json still present.
    assert "beat.mid" not in state.conflict_paths


@pytest.mark.anyio
async def test_resolve_ours_does_not_modify_file(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """``--ours`` leaves muse-work/ untouched."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"beat.mid": b"OUR_CONTENT"})
    await _commit_async(message="initial", root=tmp_path, session=muse_cli_db_session)

    write_merge_state(
        tmp_path,
        base_commit="base000",
        ours_commit="ours111",
        theirs_commit="their222",
        conflict_paths=["beat.mid"],
    )

    await resolve_conflict_async(
        file_path="beat.mid", ours=True, root=tmp_path, session=muse_cli_db_session
    )

    assert (tmp_path / "muse-work" / "beat.mid").read_bytes() == b"OUR_CONTENT"


@pytest.mark.anyio
async def test_resolve_ours_all_cleared_state_preserved_for_continue(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """When all conflicts resolved via --ours, MERGE_STATE.json stays (for --continue)."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"beat.mid": b"OURS"})
    await _commit_async(message="initial", root=tmp_path, session=muse_cli_db_session)

    write_merge_state(
        tmp_path,
        base_commit="base000",
        ours_commit="ours111",
        theirs_commit="their222",
        conflict_paths=["beat.mid"],
    )

    await resolve_conflict_async(
        file_path="beat.mid", ours=True, root=tmp_path, session=muse_cli_db_session
    )

    # MERGE_STATE.json must still exist (--continue reads ours/theirs commit IDs).
    state = read_merge_state(tmp_path)
    assert state is not None
    assert state.ours_commit == "ours111"
    assert state.theirs_commit == "their222"
    assert state.conflict_paths == []


# ---------------------------------------------------------------------------
# muse resolve --theirs
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_theirs_applies_their_file(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """``--theirs`` copies the theirs branch's object to muse-work/."""
    _init_repo(tmp_path)

    # Set up both branches with a conflict on beat.mid.
    _write_workdir(tmp_path, {"beat.mid": b"BASE"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)
    _create_branch(tmp_path, "experiment")

    # Advance main.
    _write_workdir(tmp_path, {"beat.mid": b"OURS_VERSION"})
    await _commit_async(message="main step", root=tmp_path, session=muse_cli_db_session)
    ours_commit = _head_commit(tmp_path, "main")

    # Advance experiment.
    _switch_branch(tmp_path, "experiment")
    _write_workdir(tmp_path, {"beat.mid": b"THEIRS_VERSION"})
    await _commit_async(message="exp step", root=tmp_path, session=muse_cli_db_session)
    theirs_commit = _head_commit(tmp_path, "experiment")

    # Put ours version back in muse-work (simulating the state after merge conflict).
    _switch_branch(tmp_path, "main")
    _write_workdir(tmp_path, {"beat.mid": b"OURS_VERSION"})

    write_merge_state(
        tmp_path,
        base_commit="base000",
        ours_commit=ours_commit,
        theirs_commit=theirs_commit,
        conflict_paths=["beat.mid"],
    )

    await resolve_conflict_async(
        file_path="beat.mid", ours=False, root=tmp_path, session=muse_cli_db_session
    )

    # The file in muse-work must now contain the theirs content.
    assert (tmp_path / "muse-work" / "beat.mid").read_bytes() == b"THEIRS_VERSION"
    state = read_merge_state(tmp_path)
    assert state is not None
    assert "beat.mid" not in state.conflict_paths


@pytest.mark.anyio
async def test_resolve_theirs_missing_object_exits_1(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """``--theirs`` exits 1 when the object is not in the local store."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"beat.mid": b"BASE"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)

    # Build a theirs commit that has a snapshot with beat.mid → some object_id.
    _create_branch(tmp_path, "experiment")
    _switch_branch(tmp_path, "experiment")
    _write_workdir(tmp_path, {"beat.mid": b"THEIRS_CONTENT"})
    await _commit_async(message="exp step", root=tmp_path, session=muse_cli_db_session)
    theirs_commit = _head_commit(tmp_path, "experiment")

    # Now delete the object from the local store so it's missing.
    from maestro.muse_cli.db import get_commit_snapshot_manifest
    from maestro.muse_cli.object_store import object_path

    theirs_manifest = await get_commit_snapshot_manifest(muse_cli_db_session, theirs_commit)
    assert theirs_manifest is not None
    obj_id = theirs_manifest["beat.mid"]
    obj_file = object_path(tmp_path, obj_id)
    obj_file.unlink()

    _switch_branch(tmp_path, "main")
    _write_workdir(tmp_path, {"beat.mid": b"OURS_VERSION"})
    ours_commit = _head_commit(tmp_path, "main")

    write_merge_state(
        tmp_path,
        base_commit="base000",
        ours_commit=ours_commit,
        theirs_commit=theirs_commit,
        conflict_paths=["beat.mid"],
    )

    with pytest.raises(typer.Exit) as exc_info:
        await resolve_conflict_async(
            file_path="beat.mid", ours=False, root=tmp_path, session=muse_cli_db_session
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# muse resolve — error cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_nonexistent_path_exits_1(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Resolving a path not in conflict_paths exits 1 with a clear error."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"beat.mid": b"V"})
    await _commit_async(message="initial", root=tmp_path, session=muse_cli_db_session)

    write_merge_state(
        tmp_path,
        base_commit="base000",
        ours_commit="ours111",
        theirs_commit="their222",
        conflict_paths=["beat.mid"],
    )

    with pytest.raises(typer.Exit) as exc_info:
        await resolve_conflict_async(
            file_path="nonexistent.mid",
            ours=True,
            root=tmp_path,
            session=muse_cli_db_session,
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_resolve_no_merge_in_progress_exits_1(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Resolving when no merge is in progress exits 1."""
    _init_repo(tmp_path)
    (tmp_path / ".muse").mkdir(exist_ok=True)  # ensure .muse exists

    with pytest.raises(typer.Exit) as exc_info:
        await resolve_conflict_async(
            file_path="beat.mid",
            ours=True,
            root=tmp_path,
            session=muse_cli_db_session,
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# muse merge --continue
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_continue_creates_commit_when_clean(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """``--continue`` creates a merge commit once all conflicts are resolved."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"beat.mid": b"BASE"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)
    base_commit = _head_commit(tmp_path)
    _create_branch(tmp_path, "experiment")

    # Diverge both branches.
    _write_workdir(tmp_path, {"beat.mid": b"OURS"})
    await _commit_async(message="main", root=tmp_path, session=muse_cli_db_session)
    ours_commit = _head_commit(tmp_path, "main")

    _switch_branch(tmp_path, "experiment")
    _write_workdir(tmp_path, {"beat.mid": b"THEIRS"})
    await _commit_async(message="exp", root=tmp_path, session=muse_cli_db_session)
    theirs_commit = _head_commit(tmp_path, "experiment")

    # Simulate post-conflict state: MERGE_STATE with no remaining conflicts.
    _switch_branch(tmp_path, "main")
    _write_workdir(tmp_path, {"beat.mid": b"RESOLVED"})
    write_merge_state(
        tmp_path,
        base_commit=base_commit,
        ours_commit=ours_commit,
        theirs_commit=theirs_commit,
        conflict_paths=[],  # all resolved
        other_branch="experiment",
    )

    await _merge_continue_async(root=tmp_path, session=muse_cli_db_session)

    # MERGE_STATE.json must be gone.
    assert read_merge_state(tmp_path) is None

    # A new merge commit must exist at main HEAD.
    merge_commit_id = _head_commit(tmp_path, "main")
    assert merge_commit_id != ours_commit


@pytest.mark.anyio
async def test_merge_continue_fails_with_remaining_conflicts(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """``--continue`` exits 1 when unresolved conflicts remain."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"beat.mid": b"BASE"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)

    write_merge_state(
        tmp_path,
        base_commit="base000",
        ours_commit="ours111",
        theirs_commit="their222",
        conflict_paths=["beat.mid"],  # still has conflict
    )

    with pytest.raises(typer.Exit) as exc_info:
        await _merge_continue_async(root=tmp_path, session=muse_cli_db_session)

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_merge_continue_no_merge_in_progress_exits_1(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """``--continue`` exits 1 when no merge is in progress."""
    _init_repo(tmp_path)
    (tmp_path / ".muse").mkdir(exist_ok=True)

    with pytest.raises(typer.Exit) as exc_info:
        await _merge_continue_async(root=tmp_path, session=muse_cli_db_session)

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# muse merge --abort
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_abort_restores_state(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """``--abort`` restores ours version of conflicted files and deletes MERGE_STATE.json."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"beat.mid": b"BASE"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)
    _create_branch(tmp_path, "experiment")

    # Advance main.
    _write_workdir(tmp_path, {"beat.mid": b"OURS_PRE_MERGE"})
    await _commit_async(message="main step", root=tmp_path, session=muse_cli_db_session)
    ours_commit = _head_commit(tmp_path, "main")

    # Advance experiment.
    _switch_branch(tmp_path, "experiment")
    _write_workdir(tmp_path, {"beat.mid": b"THEIRS_VERSION"})
    await _commit_async(message="exp step", root=tmp_path, session=muse_cli_db_session)
    theirs_commit = _head_commit(tmp_path, "experiment")

    # Simulate post-conflict state: workdir has a messy partially-resolved file.
    _switch_branch(tmp_path, "main")
    _write_workdir(tmp_path, {"beat.mid": b"PARTIALLY_RESOLVED_MESS"})
    write_merge_state(
        tmp_path,
        base_commit="base000",
        ours_commit=ours_commit,
        theirs_commit=theirs_commit,
        conflict_paths=["beat.mid"],
        other_branch="experiment",
    )

    await _merge_abort_async(root=tmp_path, session=muse_cli_db_session)

    # MERGE_STATE.json must be cleared.
    assert read_merge_state(tmp_path) is None

    # muse-work/beat.mid must be restored to the ours (pre-merge) version.
    assert (tmp_path / "muse-work" / "beat.mid").read_bytes() == b"OURS_PRE_MERGE"


@pytest.mark.anyio
async def test_merge_abort_no_merge_in_progress_exits_1(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """``--abort`` exits 1 when no merge is in progress."""
    _init_repo(tmp_path)
    (tmp_path / ".muse").mkdir(exist_ok=True)

    with pytest.raises(typer.Exit) as exc_info:
        await _merge_abort_async(root=tmp_path, session=muse_cli_db_session)

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_merge_abort_removes_theirs_only_file(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """``--abort`` removes files that exist only on the theirs branch (not in ours manifest)."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"base.mid": b"BASE"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)
    _create_branch(tmp_path, "experiment")

    # Main makes no change to theirs-only-file.
    ours_commit = _head_commit(tmp_path, "main")

    # Experiment adds a new file that main doesn't have.
    _switch_branch(tmp_path, "experiment")
    _write_workdir(tmp_path, {"base.mid": b"BASE", "theirs_only.mid": b"EXTRA"})
    await _commit_async(message="exp adds file", root=tmp_path, session=muse_cli_db_session)
    theirs_commit = _head_commit(tmp_path, "experiment")

    # Simulate: theirs_only.mid was copied into workdir before the conflict was detected.
    _switch_branch(tmp_path, "main")
    _write_workdir(tmp_path, {"base.mid": b"BASE", "theirs_only.mid": b"EXTRA"})
    write_merge_state(
        tmp_path,
        base_commit="base000",
        ours_commit=ours_commit,
        theirs_commit=theirs_commit,
        conflict_paths=["theirs_only.mid"],
        other_branch="experiment",
    )

    await _merge_abort_async(root=tmp_path, session=muse_cli_db_session)

    # theirs_only.mid must be gone (it wasn't in ours manifest).
    assert not (tmp_path / "muse-work" / "theirs_only.mid").exists()
    assert read_merge_state(tmp_path) is None


# ---------------------------------------------------------------------------
# merge_engine helpers — unit tests
# ---------------------------------------------------------------------------


def test_apply_resolution_writes_file(tmp_path: pathlib.Path) -> None:
    """apply_resolution() copies object content to muse-work/<rel_path>."""
    content = b"RESOLVED_CONTENT"
    object_id = "a" * 64  # fake sha256 (64 hex chars)
    write_object(tmp_path, object_id, content)
    (tmp_path / "muse-work").mkdir()

    apply_resolution(tmp_path, "beat.mid", object_id)

    assert (tmp_path / "muse-work" / "beat.mid").read_bytes() == content


def test_apply_resolution_missing_object_raises(tmp_path: pathlib.Path) -> None:
    """apply_resolution() raises FileNotFoundError for missing objects."""
    (tmp_path / ".muse" / "objects").mkdir(parents=True)
    (tmp_path / "muse-work").mkdir()

    with pytest.raises(FileNotFoundError):
        apply_resolution(tmp_path, "beat.mid", "b" * 64)


def test_is_conflict_resolved_true_when_absent(tmp_path: pathlib.Path) -> None:
    """is_conflict_resolved() returns True when path not in conflict list."""
    (tmp_path / ".muse").mkdir()
    write_merge_state(
        tmp_path,
        base_commit="b",
        ours_commit="o",
        theirs_commit="t",
        conflict_paths=["other.mid"],
    )
    state = read_merge_state(tmp_path)
    assert state is not None
    assert is_conflict_resolved(state, "beat.mid") is True


def test_is_conflict_resolved_false_when_present(tmp_path: pathlib.Path) -> None:
    """is_conflict_resolved() returns False when path still in conflict list."""
    (tmp_path / ".muse").mkdir()
    write_merge_state(
        tmp_path,
        base_commit="b",
        ours_commit="o",
        theirs_commit="t",
        conflict_paths=["beat.mid"],
    )
    state = read_merge_state(tmp_path)
    assert state is not None
    assert is_conflict_resolved(state, "beat.mid") is False


# ---------------------------------------------------------------------------
# CLI — outside-repo guard
# ---------------------------------------------------------------------------


def test_resolve_outside_repo_exits_2() -> None:
    """``muse resolve`` outside a Muse repo exits 2."""
    from typer.testing import CliRunner

    from maestro.muse_cli.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["resolve", "beat.mid", "--ours"], catch_exceptions=False)
    assert result.exit_code == ExitCode.REPO_NOT_FOUND


def test_merge_abort_outside_repo_exits_2() -> None:
    """``muse merge --abort`` outside a Muse repo exits 2."""
    from typer.testing import CliRunner

    from maestro.muse_cli.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["merge", "--abort"], catch_exceptions=False)
    assert result.exit_code == ExitCode.REPO_NOT_FOUND
