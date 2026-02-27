"""Unit tests for the Muse CLI merge engine (pure functions + find_merge_base).

All async tests use ``@pytest.mark.anyio``.  Pure-function tests are
synchronous and exercise the filesystem-free merge logic in isolation.
``find_merge_base`` tests use the in-memory SQLite session from ``conftest.py``.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.merge_engine import (
    MergeState,
    apply_merge,
    detect_conflicts,
    diff_snapshots,
    find_merge_base,
    read_merge_state,
    write_merge_state,
)
from maestro.muse_cli.models import MuseCliCommit
from maestro.muse_cli.snapshot import compute_snapshot_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_commit(
    session: AsyncSession,
    *,
    parent: str | None = None,
    parent2: str | None = None,
    branch: str = "main",
) -> MuseCliCommit:
    """Build (but don't yet persist) a MuseCliCommit with a random commit_id."""
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    manifest: dict[str, str] = {}
    snapshot_id = compute_snapshot_id(manifest)
    commit_id = str(uuid.uuid4()).replace("-", "")[:64].ljust(64, "0")
    return MuseCliCommit(
        commit_id=commit_id,
        repo_id="test-repo",
        branch=branch,
        parent_commit_id=parent,
        parent2_commit_id=parent2,
        snapshot_id=snapshot_id,
        message="test commit",
        author="",
        committed_at=now,
    )


# ---------------------------------------------------------------------------
# diff_snapshots — pure function tests
# ---------------------------------------------------------------------------


def test_diff_snapshots_empty_base_all_added() -> None:
    """Every path in other is 'added' when base is empty."""
    changed = diff_snapshots({}, {"a.mid": "aaa", "b.mid": "bbb"})
    assert changed == {"a.mid", "b.mid"}


def test_diff_snapshots_deleted_paths() -> None:
    """Paths removed from other relative to base are detected."""
    changed = diff_snapshots({"a.mid": "aaa", "b.mid": "bbb"}, {"a.mid": "aaa"})
    assert changed == {"b.mid"}


def test_diff_snapshots_modified_paths() -> None:
    """Paths with different object_ids are detected as modified."""
    changed = diff_snapshots({"a.mid": "old"}, {"a.mid": "new"})
    assert changed == {"a.mid"}


def test_diff_snapshots_unchanged_paths_excluded() -> None:
    """Paths with identical object_ids are NOT included."""
    changed = diff_snapshots({"a.mid": "same"}, {"a.mid": "same"})
    assert changed == set()


def test_diff_snapshots_mixed() -> None:
    """Added, modified, deleted, and unchanged paths handled correctly."""
    base = {"a.mid": "aaa", "b.mid": "bbb", "c.mid": "ccc"}
    other = {"a.mid": "aaa", "b.mid": "BBB", "d.mid": "ddd"}
    changed = diff_snapshots(base, other)
    assert changed == {"b.mid", "c.mid", "d.mid"}


# ---------------------------------------------------------------------------
# detect_conflicts — pure function tests
# ---------------------------------------------------------------------------


def test_detect_conflicts_disjoint_no_conflicts() -> None:
    """No conflict when each branch changes different paths."""
    assert detect_conflicts({"a.mid"}, {"b.mid"}) == set()


def test_detect_conflicts_same_path_is_conflict() -> None:
    """Same path changed on both sides is a conflict."""
    assert detect_conflicts({"beat.mid", "x.mid"}, {"beat.mid", "y.mid"}) == {"beat.mid"}


def test_detect_conflicts_empty_inputs() -> None:
    assert detect_conflicts(set(), set()) == set()


# ---------------------------------------------------------------------------
# apply_merge — pure function tests
# ---------------------------------------------------------------------------


def test_apply_merge_takes_ours_only_change() -> None:
    """A path changed only on ours is taken from ours manifest."""
    base = {"a.mid": "base"}
    ours = {"a.mid": "ours"}
    theirs = {"a.mid": "base"}
    ours_changed = {"a.mid"}
    theirs_changed: set[str] = set()
    merged = apply_merge(base, ours, theirs, ours_changed, theirs_changed, set())
    assert merged["a.mid"] == "ours"


def test_apply_merge_takes_theirs_only_change() -> None:
    """A path changed only on theirs is taken from theirs manifest."""
    base = {"a.mid": "base"}
    ours = {"a.mid": "base"}
    theirs = {"a.mid": "theirs"}
    merged = apply_merge(base, ours, theirs, set(), {"a.mid"}, set())
    assert merged["a.mid"] == "theirs"


def test_apply_merge_deleted_on_ours() -> None:
    """A path deleted on ours (not in ours manifest) is removed from merged."""
    base = {"a.mid": "base", "b.mid": "base"}
    ours = {"b.mid": "base"}   # a.mid deleted on ours
    theirs = {"a.mid": "base", "b.mid": "base"}
    ours_changed = {"a.mid"}
    merged = apply_merge(base, ours, theirs, ours_changed, set(), set())
    assert "a.mid" not in merged


def test_apply_merge_conflict_paths_not_applied() -> None:
    """Conflict paths are excluded — base version is kept."""
    base = {"x.mid": "base"}
    ours = {"x.mid": "ours"}
    theirs = {"x.mid": "theirs"}
    ours_changed = {"x.mid"}
    theirs_changed = {"x.mid"}
    conflict_paths = {"x.mid"}
    merged = apply_merge(base, ours, theirs, ours_changed, theirs_changed, conflict_paths)
    # Conflict path keeps base version (neither side applied).
    assert merged["x.mid"] == "base"


def test_apply_merge_both_sides_add_different_files() -> None:
    """Non-conflicting additions from both sides appear in merged manifest."""
    base: dict[str, str] = {}
    ours = {"a.mid": "aaa"}
    theirs = {"b.mid": "bbb"}
    merged = apply_merge(base, ours, theirs, {"a.mid"}, {"b.mid"}, set())
    assert merged == {"a.mid": "aaa", "b.mid": "bbb"}


# ---------------------------------------------------------------------------
# read_merge_state / write_merge_state — filesystem tests
# ---------------------------------------------------------------------------


def test_read_merge_state_no_file_returns_none(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".muse").mkdir()
    assert read_merge_state(tmp_path) is None


def test_write_and_read_merge_state_roundtrip(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".muse").mkdir()
    write_merge_state(
        tmp_path,
        base_commit="base000",
        ours_commit="ours111",
        theirs_commit="theirs222",
        conflict_paths=["beat.mid", "lead.mp3"],
        other_branch="feature/x",
    )
    state = read_merge_state(tmp_path)
    assert state is not None
    assert state.base_commit == "base000"
    assert state.ours_commit == "ours111"
    assert state.theirs_commit == "theirs222"
    assert sorted(state.conflict_paths) == ["beat.mid", "lead.mp3"]
    assert state.other_branch == "feature/x"


def test_read_merge_state_invalid_json_returns_none(tmp_path: pathlib.Path) -> None:
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    (muse_dir / "MERGE_STATE.json").write_text("not-valid-json{{")
    assert read_merge_state(tmp_path) is None


# ---------------------------------------------------------------------------
# find_merge_base — async tests (require DB session)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_find_merge_base_lca(muse_cli_db_session: AsyncSession) -> None:
    """LCA is correct for a simple fork-and-rejoin graph.

    Graph:
        base ← A ← ours
             ↖
              B ← theirs

    Expected LCA = base.
    """
    import datetime

    session = muse_cli_db_session
    now = datetime.datetime.now(datetime.timezone.utc)
    snapshot_id = compute_snapshot_id({})

    def _commit(cid: str, parent: str | None = None, parent2: str | None = None) -> MuseCliCommit:
        return MuseCliCommit(
            commit_id=cid,
            repo_id="test",
            branch="main",
            parent_commit_id=parent,
            parent2_commit_id=parent2,
            snapshot_id=snapshot_id,
            message="msg",
            author="",
            committed_at=now,
        )

    # Persist an empty snapshot so FK constraints pass.
    from maestro.muse_cli.models import MuseCliSnapshot
    session.add(MuseCliSnapshot(snapshot_id=snapshot_id, manifest={}))
    await session.flush()

    base = _commit("base" + "0" * 60)
    commit_a = _commit("aaaa" + "0" * 60, parent=base.commit_id)
    commit_b = _commit("bbbb" + "0" * 60, parent=base.commit_id)
    session.add_all([base, commit_a, commit_b])
    await session.flush()

    lca = await find_merge_base(session, commit_a.commit_id, commit_b.commit_id)
    assert lca == base.commit_id


@pytest.mark.anyio
async def test_find_merge_base_same_commit(muse_cli_db_session: AsyncSession) -> None:
    """LCA of a commit with itself is the commit itself."""
    import datetime

    session = muse_cli_db_session
    snapshot_id = compute_snapshot_id({})
    from maestro.muse_cli.models import MuseCliSnapshot
    session.add(MuseCliSnapshot(snapshot_id=snapshot_id, manifest={}))

    now = datetime.datetime.now(datetime.timezone.utc)
    c = MuseCliCommit(
        commit_id="cccc" + "0" * 60,
        repo_id="test",
        branch="main",
        parent_commit_id=None,
        parent2_commit_id=None,
        snapshot_id=snapshot_id,
        message="x",
        author="",
        committed_at=now,
    )
    session.add(c)
    await session.flush()

    lca = await find_merge_base(session, c.commit_id, c.commit_id)
    assert lca == c.commit_id


@pytest.mark.anyio
async def test_find_merge_base_linear_returns_ancestor(
    muse_cli_db_session: AsyncSession,
) -> None:
    """For a linear history A ← B, LCA(A, B) = A."""
    import datetime

    session = muse_cli_db_session
    snapshot_id = compute_snapshot_id({})
    from maestro.muse_cli.models import MuseCliSnapshot
    session.add(MuseCliSnapshot(snapshot_id=snapshot_id, manifest={}))
    await session.flush()

    now = datetime.datetime.now(datetime.timezone.utc)
    commit_a = MuseCliCommit(
        commit_id="aaaa" + "1" * 60,
        repo_id="r",
        branch="main",
        parent_commit_id=None,
        parent2_commit_id=None,
        snapshot_id=snapshot_id,
        message="a",
        author="",
        committed_at=now,
    )
    commit_b = MuseCliCommit(
        commit_id="bbbb" + "1" * 60,
        repo_id="r",
        branch="main",
        parent_commit_id=commit_a.commit_id,
        parent2_commit_id=None,
        snapshot_id=snapshot_id,
        message="b",
        author="",
        committed_at=now,
    )
    session.add_all([commit_a, commit_b])
    await session.flush()

    lca = await find_merge_base(session, commit_a.commit_id, commit_b.commit_id)
    assert lca == commit_a.commit_id


@pytest.mark.anyio
async def test_find_merge_base_disjoint_returns_none(
    muse_cli_db_session: AsyncSession,
) -> None:
    """Disjoint histories (no shared ancestor) return None."""
    import datetime

    session = muse_cli_db_session
    snapshot_id = compute_snapshot_id({})
    from maestro.muse_cli.models import MuseCliSnapshot
    session.add(MuseCliSnapshot(snapshot_id=snapshot_id, manifest={}))
    await session.flush()

    now = datetime.datetime.now(datetime.timezone.utc)

    def _c(cid: str) -> MuseCliCommit:
        return MuseCliCommit(
            commit_id=cid,
            repo_id="r",
            branch="main",
            parent_commit_id=None,
            parent2_commit_id=None,
            snapshot_id=snapshot_id,
            message="x",
            author="",
            committed_at=now,
        )

    c1 = _c("1111" + "0" * 60)
    c2 = _c("2222" + "0" * 60)
    session.add_all([c1, c2])
    await session.flush()

    lca = await find_merge_base(session, c1.commit_id, c2.commit_id)
    assert lca is None
