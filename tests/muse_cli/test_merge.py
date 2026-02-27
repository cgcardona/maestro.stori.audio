"""Integration tests for ``muse merge``.

Tests exercise ``_merge_async`` directly with an in-memory SQLite session and
a ``tmp_path`` root so no real Postgres instance is required.

All async tests use ``@pytest.mark.anyio``.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import uuid

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.merge import _merge_async
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.merge_engine import read_merge_state, write_merge_state
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot
from maestro.muse_cli.snapshot import compute_snapshot_id


# ---------------------------------------------------------------------------
# Helpers
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
    """Overwrite muse-work/ with exactly the given files (cleans stale files)."""
    import shutil

    workdir = root / "muse-work"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir()
    for name, content in files.items():
        (workdir / name).write_bytes(content)


def _create_branch(root: pathlib.Path, branch: str, from_branch: str = "main") -> None:
    """Create a new branch pointing at the same commit as from_branch."""
    muse = root / ".muse"
    src = muse / "refs" / "heads" / from_branch
    dst = muse / "refs" / "heads" / branch
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text() if src.exists() else "")


def _switch_branch(root: pathlib.Path, branch: str) -> None:
    """Update HEAD to point at branch."""
    (root / ".muse" / "HEAD").write_text(f"refs/heads/{branch}")


def _head_commit(root: pathlib.Path, branch: str | None = None) -> str:
    """Return current HEAD commit_id for the branch (default: current branch)."""
    muse = root / ".muse"
    if branch is None:
        head_ref = (muse / "HEAD").read_text().strip()
        branch = head_ref.rsplit("/", 1)[-1]
    ref_path = muse / "refs" / "heads" / branch
    return ref_path.read_text().strip() if ref_path.exists() else ""


async def _persist_empty_snapshot(session: AsyncSession) -> str:
    """Upsert the canonical empty-manifest snapshot so FK constraints pass."""
    sid = compute_snapshot_id({})
    existing = await session.get(MuseCliSnapshot, sid)
    if existing is None:
        session.add(MuseCliSnapshot(snapshot_id=sid, manifest={}))
        await session.flush()
    return sid


# ---------------------------------------------------------------------------
# Fast-forward merge tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_fast_forward_moves_pointer(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """FF merge: when target is ahead, HEAD advances without a new commit."""
    rid = _init_repo(tmp_path)
    _write_workdir(tmp_path, {"beat.mid": b"V1"})
    # First commit on main.
    await _commit_async(message="initial", root=tmp_path, session=muse_cli_db_session)
    initial_commit = _head_commit(tmp_path)

    # Create experiment branch from main and advance it.
    _create_branch(tmp_path, "experiment")
    _switch_branch(tmp_path, "experiment")
    _write_workdir(tmp_path, {"beat.mid": b"V2"})
    await _commit_async(message="experiment step", root=tmp_path, session=muse_cli_db_session)
    experiment_commit = _head_commit(tmp_path, "experiment")

    # Switch back to main and merge experiment → should fast-forward.
    _switch_branch(tmp_path, "main")
    await _merge_async(branch="experiment", root=tmp_path, session=muse_cli_db_session)

    # main HEAD should now point at experiment's commit.
    assert _head_commit(tmp_path, "main") == experiment_commit
    # No new merge commit created — DB still has exactly 2 commits.
    result = await muse_cli_db_session.execute(select(MuseCliCommit))
    commits = result.scalars().all()
    assert len(commits) == 2  # initial + experiment (no merge commit added)


@pytest.mark.anyio
async def test_merge_already_up_to_date_exits_0(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Merging a branch that is behind current HEAD exits 0."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"a.mid": b"V1"})
    await _commit_async(message="initial", root=tmp_path, session=muse_cli_db_session)

    # Create stale branch pointing at same commit.
    _create_branch(tmp_path, "stale")

    # Advance main.
    _write_workdir(tmp_path, {"a.mid": b"V2"})
    await _commit_async(message="ahead", root=tmp_path, session=muse_cli_db_session)

    # Merging stale into main → already up-to-date.
    with pytest.raises(typer.Exit) as exc_info:
        await _merge_async(branch="stale", root=tmp_path, session=muse_cli_db_session)

    assert exc_info.value.exit_code == ExitCode.SUCCESS


# ---------------------------------------------------------------------------
# 3-way merge tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_creates_merge_commit_two_parents(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """3-way merge creates a commit with exactly two parent IDs."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"base.mid": b"BASE"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)
    base_commit = _head_commit(tmp_path)

    # Branch off: create 'feature' from main.
    _create_branch(tmp_path, "feature")

    # Advance main with a unique change.
    _write_workdir(tmp_path, {"base.mid": b"BASE", "main_only.mid": b"MAIN"})
    await _commit_async(message="main step", root=tmp_path, session=muse_cli_db_session)
    ours_commit = _head_commit(tmp_path)

    # Advance feature with a different unique change.
    _switch_branch(tmp_path, "feature")
    _write_workdir(tmp_path, {"base.mid": b"BASE", "feature_only.mid": b"FEAT"})
    await _commit_async(
        message="feature step", root=tmp_path, session=muse_cli_db_session
    )
    theirs_commit = _head_commit(tmp_path, "feature")

    # Merge feature into main (both diverged from base).
    _switch_branch(tmp_path, "main")
    await _merge_async(branch="feature", root=tmp_path, session=muse_cli_db_session)

    # A new merge commit must exist.
    merge_commit_id = _head_commit(tmp_path, "main")
    assert merge_commit_id != ours_commit

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == merge_commit_id)
    )
    merge_commit = result.scalar_one()
    assert merge_commit.parent_commit_id == ours_commit
    assert merge_commit.parent2_commit_id == theirs_commit


@pytest.mark.anyio
async def test_merge_auto_merges_non_conflicting(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Files changed on only one branch are taken without conflict."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"shared.mid": b"BASE"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)

    # Feature adds a new file.
    _create_branch(tmp_path, "feature")
    _switch_branch(tmp_path, "feature")
    _write_workdir(tmp_path, {"shared.mid": b"BASE", "new.mid": b"NEW"})
    await _commit_async(message="feature adds new.mid", root=tmp_path, session=muse_cli_db_session)
    theirs_commit = _head_commit(tmp_path, "feature")

    # Main modifies the shared file (different from feature).
    _switch_branch(tmp_path, "main")
    _write_workdir(tmp_path, {"shared.mid": b"MAIN_CHANGE"})
    await _commit_async(message="main changes shared", root=tmp_path, session=muse_cli_db_session)

    # Merge should succeed (no conflicts).
    await _merge_async(branch="feature", root=tmp_path, session=muse_cli_db_session)

    # No MERGE_STATE.json written.
    assert read_merge_state(tmp_path) is None

    # The merge commit's snapshot must contain both the main change and the new file.
    merge_commit_id = _head_commit(tmp_path, "main")
    from maestro.muse_cli.db import get_commit_snapshot_manifest
    merged_manifest = await get_commit_snapshot_manifest(
        muse_cli_db_session, merge_commit_id
    )
    assert merged_manifest is not None
    assert "new.mid" in merged_manifest


@pytest.mark.anyio
async def test_merge_detects_conflict_same_path(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Both branches changed same file → MERGE_STATE.json written, exit 1."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"beat.mid": b"BASE"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)

    _create_branch(tmp_path, "experiment")

    # Main modifies beat.mid.
    _write_workdir(tmp_path, {"beat.mid": b"MAIN_VERSION"})
    await _commit_async(message="main changes beat", root=tmp_path, session=muse_cli_db_session)

    # Experiment also modifies beat.mid.
    _switch_branch(tmp_path, "experiment")
    _write_workdir(tmp_path, {"beat.mid": b"EXPERIMENT_VERSION"})
    await _commit_async(message="experiment changes beat", root=tmp_path, session=muse_cli_db_session)

    # Try to merge back into main → conflict expected.
    _switch_branch(tmp_path, "main")
    with pytest.raises(typer.Exit) as exc_info:
        await _merge_async(
            branch="experiment", root=tmp_path, session=muse_cli_db_session
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_merge_state_json_structure(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """MERGE_STATE.json contains all required fields on conflict."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"beat.mid": b"BASE"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)

    _create_branch(tmp_path, "experiment")

    _write_workdir(tmp_path, {"beat.mid": b"MAIN_V"})
    await _commit_async(message="main", root=tmp_path, session=muse_cli_db_session)
    ours_commit = _head_commit(tmp_path, "main")

    _switch_branch(tmp_path, "experiment")
    _write_workdir(tmp_path, {"beat.mid": b"EXP_V"})
    await _commit_async(message="exp", root=tmp_path, session=muse_cli_db_session)
    theirs_commit = _head_commit(tmp_path, "experiment")

    _switch_branch(tmp_path, "main")
    with pytest.raises(typer.Exit):
        await _merge_async(
            branch="experiment", root=tmp_path, session=muse_cli_db_session
        )

    state = read_merge_state(tmp_path)
    assert state is not None
    assert state.ours_commit == ours_commit
    assert state.theirs_commit == theirs_commit
    assert state.base_commit is not None
    assert "beat.mid" in state.conflict_paths

    # Validate the raw JSON has all required keys.
    raw = json.loads((tmp_path / ".muse" / "MERGE_STATE.json").read_text())
    for key in ("base_commit", "ours_commit", "theirs_commit", "conflict_paths"):
        assert key in raw, f"Missing key: {key}"


@pytest.mark.anyio
async def test_merge_conflict_blocks_further_commit(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """``muse commit`` while in conflicted state exits 1."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"beat.mid": b"BASE"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)

    # Write a MERGE_STATE.json with conflicts.
    write_merge_state(
        tmp_path,
        base_commit="base000",
        ours_commit="ours111",
        theirs_commit="their222",
        conflict_paths=["beat.mid"],
    )

    # Attempt to commit while conflicts exist.
    with pytest.raises(typer.Exit) as exc_info:
        await _commit_async(
            message="should fail", root=tmp_path, session=muse_cli_db_session
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_merge_in_progress_blocks_second_merge(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Second ``muse merge`` during a conflict exits 1 with clear message."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"a.mid": b"BASE"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)

    # Simulate a merge already in progress.
    write_merge_state(
        tmp_path,
        base_commit="base000",
        ours_commit="ours111",
        theirs_commit="their222",
        conflict_paths=["a.mid"],
        other_branch="feature",
    )

    with pytest.raises(typer.Exit) as exc_info:
        await _merge_async(
            branch="feature", root=tmp_path, session=muse_cli_db_session
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Error / edge cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_outside_repo_exits_2(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Invoking the merge Typer command outside a repo exits 2."""
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["merge", "feature"], catch_exceptions=False)
    assert result.exit_code == ExitCode.REPO_NOT_FOUND


@pytest.mark.anyio
async def test_merge_target_branch_no_commits_exits_1(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Merging a branch that doesn't exist / has no commits exits 1."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"a.mid": b"V"})
    await _commit_async(message="initial", root=tmp_path, session=muse_cli_db_session)

    with pytest.raises(typer.Exit) as exc_info:
        await _merge_async(
            branch="nonexistent", root=tmp_path, session=muse_cli_db_session
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_merge_same_branch_exits_0(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Merging a branch into itself (same HEAD) exits 0 — already up-to-date."""
    _init_repo(tmp_path)
    _write_workdir(tmp_path, {"a.mid": b"V"})
    await _commit_async(message="initial", root=tmp_path, session=muse_cli_db_session)
    # Create an alias branch pointing at the same commit.
    _create_branch(tmp_path, "alias")

    with pytest.raises(typer.Exit) as exc_info:
        await _merge_async(
            branch="alias", root=tmp_path, session=muse_cli_db_session
        )

    assert exc_info.value.exit_code == ExitCode.SUCCESS
