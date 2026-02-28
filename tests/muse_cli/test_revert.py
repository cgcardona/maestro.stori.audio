"""Tests for ``muse revert`` — safe undo via forward commit.

Exercises:
- ``test_muse_revert_creates_undo_commit`` — regression: full revert creates a
  new commit pointing to the parent's snapshot.
- ``test_muse_revert_no_commit_stages_only`` — --no-commit writes to
  muse-work/ without creating a DB commit.
- ``test_muse_revert_scoped_by_track`` — --track limits which paths are
  reverted; other paths remain at HEAD state.
- ``test_muse_revert_blocked_during_merge`` — blocked when a merge is in
  progress (conflict_paths non-empty).
- ``test_muse_revert_root_commit`` — reverting the root commit produces an
  empty snapshot.
- ``test_muse_revert_noop_when_already_reverted`` — reverting a commit that
  is already at its parent's state emits a noop.
- ``compute_revert_manifest_*`` — pure-function unit tests for the manifest
  computation.

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
from maestro.services.muse_revert import (
    RevertResult,
    _revert_async,
    compute_revert_manifest,
)


# ---------------------------------------------------------------------------
# Repo / workdir helpers (shared with test_commit.py pattern)
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


def test_compute_revert_manifest_full_revert() -> None:
    """Full (unscoped) revert returns the parent manifest verbatim."""
    parent = {"beat.mid": "aaa", "lead.mp3": "bbb"}
    head = {"beat.mid": "ccc", "lead.mp3": "bbb", "synth.mid": "ddd"}

    result, scoped = compute_revert_manifest(parent_manifest=parent, head_manifest=head)

    assert result == parent
    assert scoped == ()


def test_compute_revert_manifest_scoped_by_track() -> None:
    """--track reverts only paths under tracks/<track>/."""
    parent = {
        "tracks/drums/beat.mid": "old-drums",
        "tracks/bass/bass.mid": "old-bass",
        "sections/verse/lead.mid": "verse-lead",
    }
    head = {
        "tracks/drums/beat.mid": "new-drums",
        "tracks/bass/bass.mid": "new-bass",
        "sections/verse/lead.mid": "verse-lead",
        "tracks/drums/fill.mid": "head-only-fill",
    }

    result, scoped = compute_revert_manifest(
        parent_manifest=parent, head_manifest=head, track="drums"
    )

    # drums paths should come from parent or be removed if head-only
    assert result["tracks/drums/beat.mid"] == "old-drums"
    # fill.mid exists only in head (under drums) → should be removed
    assert "tracks/drums/fill.mid" not in result
    # bass and section paths remain at HEAD
    assert result["tracks/bass/bass.mid"] == "new-bass"
    assert result["sections/verse/lead.mid"] == "verse-lead"
    # Scoped paths should include drums paths from both manifests
    assert "tracks/drums/beat.mid" in scoped
    assert "tracks/drums/fill.mid" in scoped
    assert "tracks/bass/bass.mid" not in scoped


def test_compute_revert_manifest_scoped_by_section() -> None:
    """--section reverts only paths under sections/<section>/."""
    parent = {"sections/chorus/chords.mid": "old-chords"}
    head = {
        "sections/chorus/chords.mid": "new-chords",
        "sections/verse/lead.mid": "verse-lead",
    }

    result, scoped = compute_revert_manifest(
        parent_manifest=parent, head_manifest=head, section="chorus"
    )

    assert result["sections/chorus/chords.mid"] == "old-chords"
    assert result["sections/verse/lead.mid"] == "verse-lead"
    assert "sections/chorus/chords.mid" in scoped
    assert "sections/verse/lead.mid" not in scoped


def test_compute_revert_manifest_empty_parent() -> None:
    """Reverting the root commit (no parent) produces an empty manifest."""
    result, scoped = compute_revert_manifest(
        parent_manifest={},
        head_manifest={"beat.mid": "aaa"},
    )
    assert result == {}
    assert scoped == ()


# ---------------------------------------------------------------------------
# Integration tests — async DB
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_revert_creates_undo_commit(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Regression: muse revert <commit> creates a new commit on the parent's snapshot."""
    _init_muse_repo(tmp_path)

    # Commit A — initial state
    _populate_workdir(tmp_path, {"beat.mid": b"take-1"})
    commit_a_id = await _commit_async(
        message="initial take",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Commit B — a bad arrangement
    _populate_workdir(tmp_path, {"beat.mid": b"bad-take"})
    commit_b_id = await _commit_async(
        message="bad drum arrangement",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Revert B → should create commit C pointing to A's snapshot
    result = await _revert_async(
        commit_ref=commit_b_id,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    assert not result.noop
    assert not result.no_commit
    assert result.commit_id != ""
    assert result.message == f"Revert 'bad drum arrangement'"
    assert result.target_commit_id == commit_b_id
    assert result.parent_commit_id == commit_a_id

    # The new commit should point to A's snapshot
    commit_a_row = await muse_cli_db_session.get(MuseCliCommit, commit_a_id)
    commit_c_row = await muse_cli_db_session.get(MuseCliCommit, result.commit_id)
    assert commit_c_row is not None
    assert commit_c_row.snapshot_id == commit_a_row.snapshot_id  # type: ignore[union-attr]
    assert commit_c_row.parent_commit_id == commit_b_id

    # HEAD ref file updated to new commit
    head_ref = (tmp_path / ".muse" / "HEAD").read_text().strip()
    ref_path = tmp_path / ".muse" / pathlib.Path(head_ref)
    assert ref_path.read_text().strip() == result.commit_id


@pytest.mark.anyio
async def test_muse_revert_no_commit_stages_only(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--no-commit: no new commit row is created; muse-work/ deletions are applied."""
    _init_muse_repo(tmp_path)

    # Commit A — initial state
    _populate_workdir(tmp_path, {"beat.mid": b"take-1"})
    commit_a_id = await _commit_async(
        message="initial take",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Commit B — adds an extra file
    _populate_workdir(tmp_path, {"beat.mid": b"take-1", "extra.mid": b"EXTRA"})
    commit_b_id = await _commit_async(
        message="added extra track",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Revert B with --no-commit
    result = await _revert_async(
        commit_ref=commit_b_id,
        root=tmp_path,
        session=muse_cli_db_session,
        no_commit=True,
    )

    assert result.no_commit is True
    assert result.commit_id == ""  # no commit created
    # extra.mid should have been deleted from muse-work/
    assert "extra.mid" in result.paths_deleted
    assert not (tmp_path / "muse-work" / "extra.mid").exists()

    # No new commit in DB
    from sqlalchemy.future import select

    commit_count = (
        await muse_cli_db_session.execute(select(MuseCliCommit))
    ).scalars().all()
    assert len(commit_count) == 2  # only A and B, not a C


@pytest.mark.anyio
async def test_muse_revert_scoped_by_track(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--track reverts only the specified track; other paths stay at HEAD."""
    _init_muse_repo(tmp_path)

    # Commit A — initial state for both drums and bass
    _populate_workdir(
        tmp_path,
        {
            "tracks/drums/beat.mid": b"drums-v1",
            "tracks/bass/bass.mid": b"bass-v1",
        },
    )
    commit_a_id = await _commit_async(
        message="initial arrangement",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Commit B — new drums, same bass
    _populate_workdir(
        tmp_path,
        {
            "tracks/drums/beat.mid": b"drums-v2",
            "tracks/bass/bass.mid": b"bass-v1",
        },
    )
    commit_b_id = await _commit_async(
        message="updated drums only",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Revert B --track drums
    result = await _revert_async(
        commit_ref=commit_b_id,
        root=tmp_path,
        session=muse_cli_db_session,
        track="drums",
    )

    assert not result.noop
    assert result.commit_id != ""
    assert "tracks/drums/beat.mid" in result.scoped_paths
    assert "tracks/bass/bass.mid" not in result.scoped_paths

    # The revert commit's snapshot: drums should be at v1, bass still at v1 (same)
    commit_c_row = await muse_cli_db_session.get(MuseCliCommit, result.commit_id)
    assert commit_c_row is not None
    snap_row = await muse_cli_db_session.get(MuseCliSnapshot, commit_c_row.snapshot_id)
    assert snap_row is not None
    manifest: dict[str, str] = dict(snap_row.manifest)

    # Drums path should come from A's snapshot (v1)
    commit_a_snap_row = await muse_cli_db_session.get(MuseCliCommit, commit_a_id)
    a_snap = await muse_cli_db_session.get(
        MuseCliSnapshot, commit_a_snap_row.snapshot_id  # type: ignore[union-attr]
    )
    assert a_snap is not None
    a_manifest: dict[str, str] = dict(a_snap.manifest)
    assert manifest.get("tracks/drums/beat.mid") == a_manifest.get(
        "tracks/drums/beat.mid"
    )


@pytest.mark.anyio
async def test_muse_revert_blocked_during_merge(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """muse revert is blocked when a merge is in-progress with conflicts."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"take-1"})
    commit_a_id = await _commit_async(
        message="initial take",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Simulate an in-progress merge with conflicts
    write_merge_state(
        tmp_path,
        base_commit="base-abc",
        ours_commit="ours-def",
        theirs_commit="theirs-ghi",
        conflict_paths=["beat.mid"],
        other_branch="feature/bad-drums",
    )

    import typer

    with pytest.raises(typer.Exit) as exc_info:
        await _revert_async(
            commit_ref=commit_a_id,
            root=tmp_path,
            session=muse_cli_db_session,
        )

    from maestro.muse_cli.errors import ExitCode

    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_muse_revert_root_commit_produces_empty_snapshot(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Reverting the root commit (no parent) creates a commit with an empty snapshot."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"take-1"})
    root_commit_id = await _commit_async(
        message="root commit",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    result = await _revert_async(
        commit_ref=root_commit_id,
        root=tmp_path,
        session=muse_cli_db_session,
    )

    assert not result.noop
    assert result.commit_id != ""
    assert result.parent_commit_id == ""  # root has no parent

    snap_row = await muse_cli_db_session.get(MuseCliSnapshot, result.revert_snapshot_id)
    assert snap_row is not None
    assert snap_row.manifest == {}


@pytest.mark.anyio
async def test_muse_revert_abbreviated_commit_ref(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """muse revert accepts an abbreviated commit SHA (prefix match)."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"v1"})
    commit_a_id = await _commit_async(
        message="v1",
        root=tmp_path,
        session=muse_cli_db_session,
    )
    _populate_workdir(tmp_path, {"beat.mid": b"v2"})
    commit_b_id = await _commit_async(
        message="v2",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Use abbreviated prefix (first 8 chars)
    result = await _revert_async(
        commit_ref=commit_b_id[:8],
        root=tmp_path,
        session=muse_cli_db_session,
    )

    assert result.target_commit_id == commit_b_id
    assert result.commit_id != ""


@pytest.mark.anyio
async def test_muse_revert_noop_when_already_reverted(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Reverting the same commit twice is a noop on the second attempt.

    After the first revert, HEAD's snapshot equals the target commit's parent
    snapshot. A second revert of the same target would produce the identical
    snapshot → noop.
    """
    _init_muse_repo(tmp_path)

    # Commit A — initial state
    _populate_workdir(tmp_path, {"beat.mid": b"v1"})
    commit_a_id = await _commit_async(
        message="initial take",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Commit B — changed state
    _populate_workdir(tmp_path, {"beat.mid": b"v2"})
    commit_b_id = await _commit_async(
        message="updated arrangement",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # First revert: B → creates commit C whose snapshot == A's snapshot
    first = await _revert_async(
        commit_ref=commit_b_id,
        root=tmp_path,
        session=muse_cli_db_session,
    )
    assert not first.noop
    assert first.parent_commit_id == commit_a_id

    # Second revert of the same B: HEAD is now C (snapshot == A's snapshot).
    # Reverting B again would produce the same snapshot as HEAD → noop.
    second = await _revert_async(
        commit_ref=commit_b_id,
        root=tmp_path,
        session=muse_cli_db_session,
    )
    assert second.noop is True
    assert second.commit_id == ""
    assert second.target_commit_id == commit_b_id


@pytest.mark.anyio
async def test_muse_revert_unknown_commit_raises_exit(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """muse revert with an unknown commit ID exits with USER_ERROR."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)
    await _commit_async(
        message="initial",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    import typer

    with pytest.raises(typer.Exit) as exc_info:
        await _revert_async(
            commit_ref="deadbeef",
            root=tmp_path,
            session=muse_cli_db_session,
        )

    from maestro.muse_cli.errors import ExitCode

    assert exc_info.value.exit_code == ExitCode.USER_ERROR
