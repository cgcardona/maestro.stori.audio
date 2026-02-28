"""Tests for ``muse cherry-pick`` — apply a specific commit's diff on top of HEAD.

Exercises:
- ``test_cherry_pick_clean_apply_creates_commit`` — regression: cherry-pick of a
  non-conflicting commit creates a new commit with the correct snapshot delta.
- ``test_cherry_pick_conflict_detection_writes_state`` — conflict detection writes
  CHERRY_PICK_STATE.json and exits 1.
- ``test_cherry_pick_abort_restores_head`` — --abort removes state file and restores HEAD.
- ``test_cherry_pick_continue_creates_commit_after_resolve`` — --continue creates
  commit from muse-work/ after conflicts are resolved.
- ``test_cherry_pick_no_commit_does_not_create_commit`` — --no-commit returns result
  without persisting a commit row.
- ``test_cherry_pick_blocked_when_merge_in_progress`` — blocked by active merge.
- ``test_cherry_pick_blocked_when_already_in_progress`` — blocked by existing
  CHERRY_PICK_STATE.json.
- ``test_cherry_pick_self_is_noop`` — cherry-picking HEAD itself exits with SUCCESS.
- ``test_cherry_pick_unknown_commit_raises_exit`` — unknown commit ID exits USER_ERROR.
- ``compute_cherry_manifest_*`` — pure-function unit tests for the manifest logic.

All async tests use ``@pytest.mark.anyio``.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.merge_engine import write_merge_state
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot
from maestro.services.muse_cherry_pick import (
    CherryPickResult,
    CherryPickState,
    _cherry_pick_abort_async,
    _cherry_pick_async,
    _cherry_pick_continue_async,
    clear_cherry_pick_state,
    compute_cherry_manifest,
    read_cherry_pick_state,
    write_cherry_pick_state,
)


# ---------------------------------------------------------------------------
# Repo / workdir helpers
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
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _populate_workdir(
    root: pathlib.Path, files: dict[str, bytes] | None = None
) -> None:
    """Create muse-work/ with the specified files."""
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    if files is None:
        files = {"beat.mid": b"MIDI-DATA", "lead.mp3": b"MP3-DATA"}
    for name, content in files.items():
        path = workdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------


def test_compute_cherry_manifest_clean_apply() -> None:
    """Cherry diff applies cleanly when HEAD did not touch the same paths."""
    base = {"beat.mid": "aaa", "bass.mid": "bbb"}
    head = {"beat.mid": "aaa", "bass.mid": "bbb", "keys.mid": "kkk"}
    cherry = {"beat.mid": "ccc", "bass.mid": "bbb"}  # cherry modified beat.mid

    cherry_diff = {"beat.mid"}  # changed in cherry vs base
    head_diff = {"keys.mid"}  # HEAD added keys.mid

    result, conflicts = compute_cherry_manifest(
        base_manifest=base,
        head_manifest=head,
        cherry_manifest=cherry,
        cherry_diff=cherry_diff,
        head_diff=head_diff,
    )

    assert conflicts == set()
    assert result["beat.mid"] == "ccc"  # cherry version
    assert result["keys.mid"] == "kkk"  # HEAD's addition preserved
    assert result["bass.mid"] == "bbb"  # unchanged


def test_compute_cherry_manifest_conflict_detection() -> None:
    """Conflict detected when both cherry and HEAD modified the same path differently."""
    base = {"beat.mid": "aaa"}
    head = {"beat.mid": "head-version"}
    cherry = {"beat.mid": "cherry-version"}

    cherry_diff = {"beat.mid"}
    head_diff = {"beat.mid"}

    result, conflicts = compute_cherry_manifest(
        base_manifest=base,
        head_manifest=head,
        cherry_manifest=cherry,
        cherry_diff=cherry_diff,
        head_diff=head_diff,
    )

    assert "beat.mid" in conflicts
    # HEAD's version left in place during conflict
    assert result["beat.mid"] == "head-version"


def test_compute_cherry_manifest_same_change_no_conflict() -> None:
    """No conflict when both sides independently made the same change."""
    base = {"beat.mid": "aaa"}
    head = {"beat.mid": "same-oid"}
    cherry = {"beat.mid": "same-oid"}

    cherry_diff = {"beat.mid"}
    head_diff = {"beat.mid"}

    result, conflicts = compute_cherry_manifest(
        base_manifest=base,
        head_manifest=head,
        cherry_manifest=cherry,
        cherry_diff=cherry_diff,
        head_diff=head_diff,
    )

    assert conflicts == set()
    assert result["beat.mid"] == "same-oid"


def test_compute_cherry_manifest_cherry_deletion() -> None:
    """Cherry-pick removes a path that cherry deleted and HEAD did not touch."""
    base = {"beat.mid": "aaa", "old.mid": "old"}
    head = {"beat.mid": "aaa", "old.mid": "old"}
    cherry = {"beat.mid": "aaa"}  # cherry deleted old.mid

    cherry_diff = {"old.mid"}  # old.mid deleted in cherry
    head_diff: set[str] = set()

    result, conflicts = compute_cherry_manifest(
        base_manifest=base,
        head_manifest=head,
        cherry_manifest=cherry,
        cherry_diff=cherry_diff,
        head_diff=head_diff,
    )

    assert conflicts == set()
    assert "old.mid" not in result


def test_read_write_clear_cherry_pick_state(tmp_path: pathlib.Path) -> None:
    """State file round-trips correctly through write/read/clear."""
    muse = tmp_path / ".muse"
    muse.mkdir()

    assert read_cherry_pick_state(tmp_path) is None

    write_cherry_pick_state(
        tmp_path,
        cherry_commit="cherry-abc",
        head_commit="head-def",
        conflict_paths=["beat.mid", "lead.mp3"],
    )

    state = read_cherry_pick_state(tmp_path)
    assert state is not None
    assert state.cherry_commit == "cherry-abc"
    assert state.head_commit == "head-def"
    assert "beat.mid" in state.conflict_paths
    assert "lead.mp3" in state.conflict_paths

    clear_cherry_pick_state(tmp_path)
    assert read_cherry_pick_state(tmp_path) is None


# ---------------------------------------------------------------------------
# Integration tests — async DB
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cherry_pick_clean_apply_creates_commit(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Regression: cherry-pick of a non-conflicting commit creates a new commit."""
    _init_muse_repo(tmp_path)

    # Commit A — baseline on main
    _populate_workdir(tmp_path, {"beat.mid": b"main-beat", "bass.mid": b"bass-v1"})
    commit_a_id = await _commit_async(
        message="main baseline",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Commit B — adds a new file (simulates a commit from another branch)
    _populate_workdir(
        tmp_path,
        {"beat.mid": b"main-beat", "bass.mid": b"bass-v1", "solo.mid": b"guitar-solo"},
    )
    commit_b_id = await _commit_async(
        message="add guitar solo",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Reset to A so B is not HEAD (simulate cherry-picking from another branch)
    ref_path = tmp_path / ".muse" / "refs" / "heads" / "main"
    ref_path.write_text(commit_a_id)

    # Cherry-pick B onto A
    result = await _cherry_pick_async(
        commit_ref=commit_b_id,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    assert not result.conflict
    assert not result.no_commit
    assert result.commit_id != ""
    assert result.cherry_commit_id == commit_b_id
    assert result.head_commit_id == commit_a_id
    assert "(cherry picked from commit" in result.message
    assert commit_b_id[:8] in result.message

    # New commit should be in DB with correct parent
    new_commit_row = await muse_cli_db_session.get(MuseCliCommit, result.commit_id)
    assert new_commit_row is not None
    assert new_commit_row.parent_commit_id == commit_a_id

    # New snapshot should include solo.mid
    snap_row = await muse_cli_db_session.get(MuseCliSnapshot, new_commit_row.snapshot_id)
    assert snap_row is not None
    manifest: dict[str, str] = dict(snap_row.manifest)
    assert "solo.mid" in manifest
    assert "beat.mid" in manifest

    # HEAD ref updated
    assert ref_path.read_text().strip() == result.commit_id


@pytest.mark.anyio
async def test_cherry_pick_conflict_detection_writes_state(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Conflict: both HEAD and cherry modified the same path → state file written, exit 1."""
    _init_muse_repo(tmp_path)

    # Commit P — shared base
    _populate_workdir(tmp_path, {"beat.mid": b"base-beat"})
    commit_p_id = await _commit_async(
        message="shared base",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Commit C (cherry) — modifies beat.mid one way
    _populate_workdir(tmp_path, {"beat.mid": b"cherry-beat"})
    commit_c_id = await _commit_async(
        message="cherry take",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Simulate HEAD also modifying beat.mid differently (reset to P, then commit HEAD)
    ref_path = tmp_path / ".muse" / "refs" / "heads" / "main"
    ref_path.write_text(commit_p_id)

    _populate_workdir(tmp_path, {"beat.mid": b"head-beat"})
    commit_head_id = await _commit_async(
        message="head modification",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Cherry-pick C onto HEAD — should conflict
    import typer

    with pytest.raises(typer.Exit) as exc_info:
        await _cherry_pick_async(
            commit_ref=commit_c_id,
            root=tmp_path,
            session=muse_cli_db_session,
        )

    from maestro.muse_cli.errors import ExitCode

    assert exc_info.value.exit_code == ExitCode.USER_ERROR

    # State file must exist
    state = read_cherry_pick_state(tmp_path)
    assert state is not None
    assert state.cherry_commit == commit_c_id
    assert state.head_commit == commit_head_id
    assert "beat.mid" in state.conflict_paths


@pytest.mark.anyio
async def test_cherry_pick_abort_restores_head(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--abort removes CHERRY_PICK_STATE.json and restores HEAD pointer."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"v1"})
    commit_a_id = await _commit_async(
        message="initial",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Simulate a paused cherry-pick state
    muse_dir = tmp_path / ".muse"
    write_cherry_pick_state(
        tmp_path,
        cherry_commit="cherry-abc",
        head_commit=commit_a_id,
        conflict_paths=["beat.mid"],
    )

    # Move HEAD pointer forward artificially to simulate partial progress
    ref_path = muse_dir / "refs" / "heads" / "main"
    ref_path.write_text("some-partial-commit-id")

    await _cherry_pick_abort_async(root=tmp_path, session=muse_cli_db_session)

    # State file removed
    assert read_cherry_pick_state(tmp_path) is None

    # HEAD restored to pre-cherry-pick commit
    assert ref_path.read_text().strip() == commit_a_id


@pytest.mark.anyio
async def test_cherry_pick_continue_creates_commit_after_resolve(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--continue creates a commit from muse-work/ after conflicts are manually resolved."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"v1"})
    commit_a_id = await _commit_async(
        message="initial",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    cherry_fake_id = "cherry" + "0" * 58  # fake 64-char hex ID

    # Write state with empty conflict_paths (conflicts resolved)
    write_cherry_pick_state(
        tmp_path,
        cherry_commit=cherry_fake_id,
        head_commit=commit_a_id,
        conflict_paths=[],
    )

    # Ensure muse-work/ has a resolved state and a real cherry commit in DB
    _populate_workdir(tmp_path, {"beat.mid": b"resolved-beat", "solo.mid": b"solo"})

    # Insert a dummy cherry commit row so the message lookup works
    from maestro.muse_cli.models import MuseCliCommit as _MuseCliCommit
    from maestro.muse_cli.snapshot import compute_snapshot_id
    import datetime as _dt

    dummy_snap_id = compute_snapshot_id({})
    from maestro.muse_cli.db import upsert_snapshot as _upsert_snapshot

    await _upsert_snapshot(
        muse_cli_db_session, manifest={}, snapshot_id=dummy_snap_id
    )
    repo_data: dict[str, str] = json.loads(
        (tmp_path / ".muse" / "repo.json").read_text()
    )
    dummy_commit = _MuseCliCommit(
        commit_id=cherry_fake_id,
        repo_id=repo_data["repo_id"],
        branch="experiment",
        parent_commit_id=None,
        snapshot_id=dummy_snap_id,
        message="the perfect guitar solo",
        author="",
        committed_at=_dt.datetime.now(_dt.timezone.utc),
    )
    muse_cli_db_session.add(dummy_commit)
    await muse_cli_db_session.flush()

    result = await _cherry_pick_continue_async(
        root=tmp_path, session=muse_cli_db_session
    )

    assert result.commit_id != ""
    assert result.cherry_commit_id == cherry_fake_id
    assert "cherry picked from commit" in result.message

    # State file cleared
    assert read_cherry_pick_state(tmp_path) is None

    # HEAD updated
    ref_path = tmp_path / ".muse" / "refs" / "heads" / "main"
    assert ref_path.read_text().strip() == result.commit_id

    # New commit in DB
    new_row = await muse_cli_db_session.get(MuseCliCommit, result.commit_id)
    assert new_row is not None
    assert new_row.parent_commit_id == commit_a_id


@pytest.mark.anyio
async def test_cherry_pick_no_commit_does_not_create_commit(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--no-commit returns result without writing a commit row to the DB."""
    _init_muse_repo(tmp_path)

    _populate_workdir(tmp_path, {"beat.mid": b"v1"})
    commit_a_id = await _commit_async(
        message="baseline",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    _populate_workdir(tmp_path, {"beat.mid": b"v1", "new.mid": b"new-file"})
    commit_b_id = await _commit_async(
        message="add new file",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Reset to A so B is not HEAD
    ref_path = tmp_path / ".muse" / "refs" / "heads" / "main"
    ref_path.write_text(commit_a_id)

    result = await _cherry_pick_async(
        commit_ref=commit_b_id,
        root=tmp_path,
        session=muse_cli_db_session,
        no_commit=True,
    )

    assert result.no_commit is True
    assert result.commit_id == ""
    assert result.cherry_commit_id == commit_b_id

    # HEAD ref unchanged
    assert ref_path.read_text().strip() == commit_a_id

    # No new commit in DB
    from sqlalchemy.future import select

    rows = (
        await muse_cli_db_session.execute(select(MuseCliCommit))
    ).scalars().all()
    assert len(rows) == 2  # only A and B


@pytest.mark.anyio
async def test_cherry_pick_blocked_when_merge_in_progress(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Cherry-pick blocked when a merge is in progress with conflicts."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"v1"})
    commit_a_id = await _commit_async(
        message="initial",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    write_merge_state(
        tmp_path,
        base_commit="base-abc",
        ours_commit="ours-def",
        theirs_commit="theirs-ghi",
        conflict_paths=["beat.mid"],
    )

    import typer

    with pytest.raises(typer.Exit) as exc_info:
        await _cherry_pick_async(
            commit_ref=commit_a_id,
            root=tmp_path,
            session=muse_cli_db_session,
        )

    from maestro.muse_cli.errors import ExitCode

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_cherry_pick_blocked_when_already_in_progress(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Cherry-pick blocked when CHERRY_PICK_STATE.json already exists."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"v1"})
    commit_a_id = await _commit_async(
        message="initial",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    write_cherry_pick_state(
        tmp_path,
        cherry_commit="cherry-abc",
        head_commit=commit_a_id,
        conflict_paths=["beat.mid"],
    )

    import typer

    with pytest.raises(typer.Exit) as exc_info:
        await _cherry_pick_async(
            commit_ref=commit_a_id,
            root=tmp_path,
            session=muse_cli_db_session,
        )

    from maestro.muse_cli.errors import ExitCode

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_cherry_pick_self_is_noop(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Cherry-picking HEAD itself exits with SUCCESS (noop)."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"v1"})
    commit_a_id = await _commit_async(
        message="head commit",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    import typer

    with pytest.raises(typer.Exit) as exc_info:
        await _cherry_pick_async(
            commit_ref=commit_a_id,
            root=tmp_path,
            session=muse_cli_db_session,
        )

    from maestro.muse_cli.errors import ExitCode

    assert exc_info.value.exit_code == ExitCode.SUCCESS


@pytest.mark.anyio
async def test_cherry_pick_unknown_commit_raises_exit(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Unknown commit ID exits with USER_ERROR."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"v1"})
    await _commit_async(
        message="initial",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    import typer

    with pytest.raises(typer.Exit) as exc_info:
        await _cherry_pick_async(
            commit_ref="deadbeef",
            root=tmp_path,
            session=muse_cli_db_session,
        )

    from maestro.muse_cli.errors import ExitCode

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_cherry_pick_abbreviated_ref(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Cherry-pick accepts an abbreviated commit SHA (prefix match)."""
    _init_muse_repo(tmp_path)

    _populate_workdir(tmp_path, {"beat.mid": b"v1"})
    commit_a_id = await _commit_async(
        message="baseline",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    _populate_workdir(tmp_path, {"beat.mid": b"v1", "bonus.mid": b"bonus"})
    commit_b_id = await _commit_async(
        message="add bonus",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Reset to A
    ref_path = tmp_path / ".muse" / "refs" / "heads" / "main"
    ref_path.write_text(commit_a_id)

    result = await _cherry_pick_async(
        commit_ref=commit_b_id[:8],
        root=tmp_path,
        session=muse_cli_db_session,
    )

    assert result.cherry_commit_id == commit_b_id
    assert result.commit_id != ""


@pytest.mark.anyio
async def test_cherry_pick_continue_blocked_with_remaining_conflicts(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--continue exits USER_ERROR when conflict_paths list is non-empty."""
    _init_muse_repo(tmp_path)
    muse = tmp_path / ".muse"
    muse.mkdir(exist_ok=True)

    write_cherry_pick_state(
        tmp_path,
        cherry_commit="cherry-abc",
        head_commit="head-def",
        conflict_paths=["beat.mid"],
    )

    import typer

    with pytest.raises(typer.Exit) as exc_info:
        await _cherry_pick_continue_async(root=tmp_path, session=muse_cli_db_session)

    from maestro.muse_cli.errors import ExitCode

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_cherry_pick_abort_when_nothing_in_progress(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--abort exits USER_ERROR when no cherry-pick is in progress."""
    _init_muse_repo(tmp_path)

    import typer

    with pytest.raises(typer.Exit) as exc_info:
        await _cherry_pick_abort_async(root=tmp_path, session=muse_cli_db_session)

    from maestro.muse_cli.errors import ExitCode

    assert exc_info.value.exit_code == ExitCode.USER_ERROR
