"""End-to-end integration tests for the full conflict resolution workflow.

These tests exercise the complete cycle:

    muse merge <branch>     → conflict detected, MERGE_STATE.json written
    muse resolve <path> ... → path removed from conflict list
    muse merge --continue   → merge commit created, MERGE_STATE.json cleared
    muse log                → merge commit visible in history

A separate test covers the abort path:

    muse merge <branch>     → conflict detected
    muse merge --abort      → pre-merge state restored

Both tests require two real branches with divergent commits, making them true
integration tests rather than unit tests.  They use in-memory SQLite and
``tmp_path`` — no real database or Docker is needed.

All async tests use ``@pytest.mark.anyio``.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.merge import _merge_abort_async, _merge_async, _merge_continue_async
from maestro.muse_cli.commands.resolve import resolve_conflict_async
from maestro.muse_cli.db import get_commit_snapshot_manifest
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.merge_engine import read_merge_state
from maestro.muse_cli.models import MuseCliCommit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(root: pathlib.Path) -> str:
    rid = str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _write_workdir(root: pathlib.Path, files: dict[str, bytes]) -> None:
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
# Full conflict → resolve → continue cycle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_full_conflict_resolve_cycle(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """End-to-end: merge → conflict → resolve → continue → log shows merge commit.

    Scenario:
    1. Create base commit on ``main`` with ``beat.mid``.
    2. Branch ``experiment`` from ``main``.
    3. Advance ``main``: modify ``beat.mid`` → OURS_VERSION.
    4. Advance ``experiment``: modify ``beat.mid`` → THEIRS_VERSION.
    5. Merge ``experiment`` into ``main`` → conflict on ``beat.mid``.
    6. Resolve via ``--theirs`` (file content replaced in muse-work/).
    7. Run ``muse merge --continue`` → merge commit created.
    8. Verify: MERGE_STATE.json cleared, merge commit has two parents, log
       shows all three commits (base, ours, merge).
    """
    _init_repo(tmp_path)

    # --- Step 1: base commit ---
    _write_workdir(tmp_path, {"beat.mid": b"BASE"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)
    base_commit = _head_commit(tmp_path, "main")

    # --- Step 2: branch experiment ---
    _create_branch(tmp_path, "experiment")

    # --- Step 3: advance main ---
    _write_workdir(tmp_path, {"beat.mid": b"OURS_VERSION"})
    await _commit_async(message="main: ours version", root=tmp_path, session=muse_cli_db_session)
    ours_commit = _head_commit(tmp_path, "main")

    # --- Step 4: advance experiment ---
    _switch_branch(tmp_path, "experiment")
    _write_workdir(tmp_path, {"beat.mid": b"THEIRS_VERSION"})
    await _commit_async(
        message="experiment: theirs version", root=tmp_path, session=muse_cli_db_session
    )
    theirs_commit = _head_commit(tmp_path, "experiment")

    # --- Step 5: merge → conflict ---
    _switch_branch(tmp_path, "main")
    _write_workdir(tmp_path, {"beat.mid": b"OURS_VERSION"})  # restore ours in workdir

    with pytest.raises(typer.Exit) as exc_info:
        await _merge_async(
            branch="experiment", root=tmp_path, session=muse_cli_db_session
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR

    merge_state = read_merge_state(tmp_path)
    assert merge_state is not None
    assert "beat.mid" in merge_state.conflict_paths

    # --- Step 6: resolve --theirs (copies THEIRS_VERSION to muse-work) ---
    await resolve_conflict_async(
        file_path="beat.mid",
        ours=False,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # File must now contain theirs content.
    assert (tmp_path / "muse-work" / "beat.mid").read_bytes() == b"THEIRS_VERSION"

    # Conflict list must be empty (MERGE_STATE.json still present for --continue).
    state_after_resolve = read_merge_state(tmp_path)
    assert state_after_resolve is not None
    assert state_after_resolve.conflict_paths == []

    # --- Step 7: merge --continue ---
    await _merge_continue_async(root=tmp_path, session=muse_cli_db_session)

    # MERGE_STATE.json must be gone.
    assert read_merge_state(tmp_path) is None

    # --- Step 8: verify merge commit ---
    merge_commit_id = _head_commit(tmp_path, "main")
    assert merge_commit_id not in (base_commit, ours_commit, theirs_commit)

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == merge_commit_id)
    )
    merge_commit = result.scalar_one()
    # Two parents: ours and theirs.
    assert merge_commit.parent_commit_id == ours_commit
    assert merge_commit.parent2_commit_id == theirs_commit

    # Merged snapshot must contain the resolved content (theirs version).
    merged_manifest = await get_commit_snapshot_manifest(muse_cli_db_session, merge_commit_id)
    assert merged_manifest is not None
    assert "beat.mid" in merged_manifest

    # Total commits in DB: base + ours + theirs + merge = 4.
    all_commits_result = await muse_cli_db_session.execute(select(MuseCliCommit))
    all_commits = all_commits_result.scalars().all()
    assert len(all_commits) == 4


# ---------------------------------------------------------------------------
# Full conflict → abort cycle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_full_conflict_abort_cycle(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """End-to-end: merge → conflict → abort → pre-merge state restored.

    After abort:
    - ``muse-work/`` contains the ours version.
    - ``MERGE_STATE.json`` is gone.
    - No merge commit was created.
    """
    _init_repo(tmp_path)

    # Base commit.
    _write_workdir(tmp_path, {"beat.mid": b"BASE"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)
    _create_branch(tmp_path, "experiment")

    # Advance main.
    _write_workdir(tmp_path, {"beat.mid": b"OURS_CLEAN"})
    await _commit_async(message="main", root=tmp_path, session=muse_cli_db_session)
    ours_commit = _head_commit(tmp_path, "main")

    # Advance experiment.
    _switch_branch(tmp_path, "experiment")
    _write_workdir(tmp_path, {"beat.mid": b"THEIRS_CLEAN"})
    await _commit_async(message="experiment", root=tmp_path, session=muse_cli_db_session)

    # Trigger conflict.
    _switch_branch(tmp_path, "main")
    _write_workdir(tmp_path, {"beat.mid": b"OURS_CLEAN"})
    with pytest.raises(typer.Exit):
        await _merge_async(
            branch="experiment", root=tmp_path, session=muse_cli_db_session
        )

    assert read_merge_state(tmp_path) is not None

    # Simulate partial manual edits (user started editing but wants to abort).
    (tmp_path / "muse-work" / "beat.mid").write_bytes(b"MESSY_PARTIAL_EDIT")

    # Abort.
    await _merge_abort_async(root=tmp_path, session=muse_cli_db_session)

    # Post-abort: MERGE_STATE.json cleared.
    assert read_merge_state(tmp_path) is None

    # Post-abort: muse-work restored to ours (pre-merge) content.
    assert (tmp_path / "muse-work" / "beat.mid").read_bytes() == b"OURS_CLEAN"

    # No merge commit created — DB has exactly 3 commits (base + main + experiment).
    all_commits_result = await muse_cli_db_session.execute(select(MuseCliCommit))
    all_commits = all_commits_result.scalars().all()
    assert len(all_commits) == 3

    # main HEAD unchanged after abort.
    assert _head_commit(tmp_path, "main") == ours_commit


# ---------------------------------------------------------------------------
# Multiple conflicts — partial resolution then continue
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_partial_resolution_then_continue(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Resolving conflicts one at a time then --continue works correctly."""
    _init_repo(tmp_path)

    # Base with two files.
    _write_workdir(tmp_path, {"a.mid": b"BASE_A", "b.mid": b"BASE_B"})
    await _commit_async(message="base", root=tmp_path, session=muse_cli_db_session)
    _create_branch(tmp_path, "feature")

    # Main modifies both files.
    _write_workdir(tmp_path, {"a.mid": b"MAIN_A", "b.mid": b"MAIN_B"})
    await _commit_async(message="main", root=tmp_path, session=muse_cli_db_session)
    ours_commit = _head_commit(tmp_path, "main")

    # Feature also modifies both files.
    _switch_branch(tmp_path, "feature")
    _write_workdir(tmp_path, {"a.mid": b"FEAT_A", "b.mid": b"FEAT_B"})
    await _commit_async(message="feature", root=tmp_path, session=muse_cli_db_session)
    theirs_commit = _head_commit(tmp_path, "feature")

    # Trigger conflict.
    _switch_branch(tmp_path, "main")
    _write_workdir(tmp_path, {"a.mid": b"MAIN_A", "b.mid": b"MAIN_B"})
    with pytest.raises(typer.Exit):
        await _merge_async(branch="feature", root=tmp_path, session=muse_cli_db_session)

    state = read_merge_state(tmp_path)
    assert state is not None
    assert sorted(state.conflict_paths) == ["a.mid", "b.mid"]

    # Resolve a.mid --ours.
    await resolve_conflict_async(
        file_path="a.mid", ours=True, root=tmp_path, session=muse_cli_db_session
    )
    # b.mid still in conflict.
    state2 = read_merge_state(tmp_path)
    assert state2 is not None
    assert state2.conflict_paths == ["b.mid"]

    # Resolve b.mid --theirs.
    await resolve_conflict_async(
        file_path="b.mid", ours=False, root=tmp_path, session=muse_cli_db_session
    )
    # All clear.
    state3 = read_merge_state(tmp_path)
    assert state3 is not None
    assert state3.conflict_paths == []
    assert (tmp_path / "muse-work" / "b.mid").read_bytes() == b"FEAT_B"

    # Continue.
    await _merge_continue_async(root=tmp_path, session=muse_cli_db_session)
    assert read_merge_state(tmp_path) is None

    merge_commit_id = _head_commit(tmp_path, "main")
    assert merge_commit_id not in (ours_commit, theirs_commit)
