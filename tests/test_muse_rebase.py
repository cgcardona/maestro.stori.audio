"""Tests for ``muse rebase`` — commit replay onto a new base.

Verifies:
- test_rebase_linear_replays_commits              — regression: linear rebase replays commits onto upstream tip
- test_rebase_noop_already_up_to_date             — noop when branch is already at upstream
- test_rebase_fast_forward_advances_pointer       — current branch behind upstream → fast-forward
- test_rebase_already_ahead_noop                  — upstream is base (current ahead) → noop
- test_rebase_interactive_plan_from_commits       — InteractivePlan.from_commits produces pick entries
- test_rebase_interactive_plan_parse_drop         — drop entries are excluded from resolved list
- test_rebase_interactive_plan_invalid_action     — unrecognised action raises ValueError
- test_rebase_interactive_plan_ambiguous_sha      — ambiguous SHA prefix raises ValueError
- test_rebase_autosquash_moves_fixup_commits      — fixup! commits reordered after their targets
- test_rebase_autosquash_no_fixups                — no fixup! commits → list unchanged
- test_rebase_compute_delta_additions             — compute_delta detects added paths
- test_rebase_compute_delta_deletions             — compute_delta detects removed paths
- test_rebase_compute_delta_modifications         — compute_delta detects modified paths
- test_rebase_apply_delta_applies_changes         — apply_delta patches an onto manifest
- test_rebase_collect_commits_since_base          — collects only commits beyond the base
- test_rebase_abort_restores_branch               — --abort rewrites branch pointer and clears state
- test_rebase_continue_replays_remaining          — --continue replays remaining commits
- test_rebase_continue_no_state_errors            — --continue with no state file exits 1
- test_rebase_abort_no_state_errors               — --abort with no state file exits 1
- test_rebase_state_roundtrip                     — write/read roundtrip for REBASE_STATE.json
- test_boundary_no_forbidden_imports              — AST boundary seal
"""
from __future__ import annotations

import ast
import datetime
import json
import pathlib
import uuid
from collections.abc import AsyncGenerator

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from maestro.db.database import Base
from maestro.muse_cli import models as _cli_models  # noqa: F401 — register tables
from maestro.muse_cli.db import insert_commit, upsert_snapshot
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot
from maestro.muse_cli.snapshot import compute_commit_id, compute_snapshot_id
from maestro.services.muse_rebase import (
    InteractivePlan,
    RebaseResult,
    RebaseState,
    apply_autosquash,
    apply_delta,
    clear_rebase_state,
    compute_delta,
    read_rebase_state,
    write_rebase_state,
    _collect_branch_commits_since_base,
    _rebase_abort_async,
    _rebase_async,
    _rebase_continue_async,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with all CLI tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def repo_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def repo_root(tmp_path: pathlib.Path, repo_id: str) -> pathlib.Path:
    """Minimal Muse repository structure."""
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    (muse_dir / "HEAD").write_text("refs/heads/main")
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "refs" / "heads" / "main").write_text("")
    (muse_dir / "repo.json").write_text(json.dumps({"repo_id": repo_id}))
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_commit(
    repo_id: str,
    branch: str,
    message: str,
    snapshot_id: str,
    parent_id: str | None = None,
) -> MuseCliCommit:
    """Build a MuseCliCommit with a deterministic commit_id."""
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=[parent_id] if parent_id else [],
        snapshot_id=snapshot_id,
        message=message,
        committed_at_iso=committed_at.isoformat(),
    )
    return MuseCliCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch,
        parent_commit_id=parent_id,
        snapshot_id=snapshot_id,
        message=message,
        author="test",
        committed_at=committed_at,
    )


async def _seed_commit(
    session: AsyncSession,
    repo_id: str,
    branch: str,
    message: str,
    manifest: dict[str, str],
    parent_id: str | None = None,
) -> MuseCliCommit:
    """Persist a snapshot + commit and return the commit."""
    snap_id = compute_snapshot_id(manifest)
    await upsert_snapshot(session, manifest=manifest, snapshot_id=snap_id)
    commit = _make_commit(repo_id, branch, message, snap_id, parent_id)
    await insert_commit(session, commit)
    await session.flush()
    return commit


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------


def test_rebase_compute_delta_additions() -> None:
    """compute_delta detects paths added in the commit."""
    parent: dict[str, str] = {"a.mid": "aaa"}
    commit: dict[str, str] = {"a.mid": "aaa", "b.mid": "bbb"}
    adds, dels = compute_delta(parent, commit)
    assert adds == {"b.mid": "bbb"}
    assert dels == set()


def test_rebase_compute_delta_deletions() -> None:
    """compute_delta detects paths removed in the commit."""
    parent: dict[str, str] = {"a.mid": "aaa", "b.mid": "bbb"}
    commit: dict[str, str] = {"a.mid": "aaa"}
    adds, dels = compute_delta(parent, commit)
    assert adds == {}
    assert dels == {"b.mid"}


def test_rebase_compute_delta_modifications() -> None:
    """compute_delta detects modified paths."""
    parent: dict[str, str] = {"a.mid": "old"}
    commit: dict[str, str] = {"a.mid": "new"}
    adds, dels = compute_delta(parent, commit)
    assert adds == {"a.mid": "new"}
    assert dels == set()


def test_rebase_apply_delta_applies_changes() -> None:
    """apply_delta correctly patches the onto manifest."""
    onto: dict[str, str] = {"base.mid": "base", "old.mid": "old"}
    adds: dict[str, str] = {"new.mid": "new", "old.mid": "updated"}
    dels: set[str] = {"base.mid"}
    result = apply_delta(onto, adds, dels)
    assert result == {"old.mid": "updated", "new.mid": "new"}


def test_rebase_autosquash_moves_fixup_commits() -> None:
    """apply_autosquash moves fixup! commits after their target."""
    committed_at = datetime.datetime.now(datetime.timezone.utc)

    def _c(msg: str) -> MuseCliCommit:
        return MuseCliCommit(
            commit_id=str(uuid.uuid4()),
            repo_id="repo",
            branch="main",
            parent_commit_id=None,
            snapshot_id="snap",
            message=msg,
            author="",
            committed_at=committed_at,
        )

    target = _c("Add drums")
    fixup = _c("fixup! Add drums")
    other = _c("Add bass")

    commits = [target, other, fixup]
    result, was_reordered = apply_autosquash(commits)
    assert was_reordered is True
    # fixup should appear right after target
    idx_target = next(i for i, c in enumerate(result) if c.commit_id == target.commit_id)
    idx_fixup = next(i for i, c in enumerate(result) if c.commit_id == fixup.commit_id)
    assert idx_fixup == idx_target + 1


def test_rebase_autosquash_no_fixups() -> None:
    """apply_autosquash returns original list unchanged when no fixup! commits."""
    committed_at = datetime.datetime.now(datetime.timezone.utc)

    def _c(msg: str) -> MuseCliCommit:
        return MuseCliCommit(
            commit_id=str(uuid.uuid4()),
            repo_id="repo",
            branch="main",
            parent_commit_id=None,
            snapshot_id="snap",
            message=msg,
            author="",
            committed_at=committed_at,
        )

    commits = [_c("Add drums"), _c("Add bass")]
    result, was_reordered = apply_autosquash(commits)
    assert was_reordered is False
    assert [c.commit_id for c in result] == [c.commit_id for c in commits]


# ---------------------------------------------------------------------------
# InteractivePlan tests
# ---------------------------------------------------------------------------


def test_rebase_interactive_plan_from_commits() -> None:
    """InteractivePlan.from_commits produces a pick entry per commit."""
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commits = [
        MuseCliCommit(
            commit_id="abc" + "0" * 61,
            repo_id="r",
            branch="main",
            parent_commit_id=None,
            snapshot_id="snap",
            message="First commit",
            author="",
            committed_at=committed_at,
        ),
        MuseCliCommit(
            commit_id="def" + "0" * 61,
            repo_id="r",
            branch="main",
            parent_commit_id=None,
            snapshot_id="snap2",
            message="Second commit",
            author="",
            committed_at=committed_at,
        ),
    ]
    plan = InteractivePlan.from_commits(commits)
    assert len(plan.entries) == 2
    assert plan.entries[0][0] == "pick"
    assert plan.entries[1][0] == "pick"


def test_rebase_interactive_plan_parse_drop() -> None:
    """drop entries are excluded from resolve_against output."""
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit = MuseCliCommit(
        commit_id="abc" + "0" * 61,
        repo_id="r",
        branch="main",
        parent_commit_id=None,
        snapshot_id="snap",
        message="A commit",
        author="",
        committed_at=committed_at,
    )
    plan_text = "drop abc A commit\n"
    plan = InteractivePlan.from_text(plan_text)
    resolved = plan.resolve_against([commit])
    assert resolved == []


def test_rebase_interactive_plan_invalid_action() -> None:
    """Unrecognised action raises ValueError."""
    with pytest.raises(ValueError, match="Unknown action"):
        InteractivePlan.from_text("yolo abc Some commit\n")


def test_rebase_interactive_plan_ambiguous_sha() -> None:
    """Ambiguous SHA prefix raises ValueError."""
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commits = [
        MuseCliCommit(
            commit_id="abc" + str(i) + "0" * 60,
            repo_id="r",
            branch="main",
            parent_commit_id=None,
            snapshot_id="snap",
            message="msg",
            author="",
            committed_at=committed_at,
        )
        for i in range(2)
    ]
    plan = InteractivePlan.from_text("pick abc msg\n")
    with pytest.raises(ValueError, match="ambiguous"):
        plan.resolve_against(commits)


# ---------------------------------------------------------------------------
# RebaseState roundtrip
# ---------------------------------------------------------------------------


def test_rebase_state_roundtrip(tmp_path: pathlib.Path) -> None:
    """write_rebase_state / read_rebase_state is a lossless roundtrip."""
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()

    state = RebaseState(
        upstream_commit="upstream123",
        base_commit="base456",
        original_branch="feature",
        original_head="head789",
        commits_to_replay=["cid1", "cid2"],
        current_onto="onto000",
        completed_pairs=[["orig1", "new1"]],
        current_commit="cid1",
        conflict_paths=["beat.mid"],
    )
    write_rebase_state(tmp_path, state)

    loaded = read_rebase_state(tmp_path)
    assert loaded is not None
    assert loaded.upstream_commit == state.upstream_commit
    assert loaded.base_commit == state.base_commit
    assert loaded.original_branch == state.original_branch
    assert loaded.original_head == state.original_head
    assert loaded.commits_to_replay == state.commits_to_replay
    assert loaded.current_onto == state.current_onto
    assert loaded.completed_pairs == state.completed_pairs
    assert loaded.current_commit == state.current_commit
    assert loaded.conflict_paths == state.conflict_paths

    clear_rebase_state(tmp_path)
    assert read_rebase_state(tmp_path) is None


def test_rebase_state_missing_file(tmp_path: pathlib.Path) -> None:
    """read_rebase_state returns None when REBASE_STATE.json does not exist."""
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    assert read_rebase_state(tmp_path) is None


# ---------------------------------------------------------------------------
# Async: collect commits since base
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rebase_collect_commits_since_base(
    async_session: AsyncSession,
    repo_id: str,
) -> None:
    """_collect_branch_commits_since_base returns only commits beyond the LCA."""
    base_commit = await _seed_commit(
        async_session, repo_id, "main", "Base", {"a.mid": "a1"}
    )
    c1 = await _seed_commit(
        async_session, repo_id, "main", "C1", {"a.mid": "a2"}, base_commit.commit_id
    )
    c2 = await _seed_commit(
        async_session, repo_id, "main", "C2", {"a.mid": "a3"}, c1.commit_id
    )

    commits = await _collect_branch_commits_since_base(
        async_session, c2.commit_id, base_commit.commit_id
    )
    commit_ids = [c.commit_id for c in commits]
    assert base_commit.commit_id not in commit_ids
    assert c1.commit_id in commit_ids
    assert c2.commit_id in commit_ids
    # Oldest first
    assert commit_ids.index(c1.commit_id) < commit_ids.index(c2.commit_id)


# ---------------------------------------------------------------------------
# Async: full rebase pipeline
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rebase_linear_replays_commits(
    async_session: AsyncSession,
    repo_id: str,
    repo_root: pathlib.Path,
) -> None:
    """Regression: linear rebase replays commits onto the upstream tip.

    Topology:
      base → upstream (on 'dev')
      base → c1 → c2 (on 'main')

    After rebase main onto dev:
      dev → c1' → c2'  (main)
    """
    muse_dir = repo_root / ".muse"

    # Seed base commit (common ancestor)
    base = await _seed_commit(
        async_session, repo_id, "main", "Base", {"common.mid": "v0"}
    )

    # Upstream (dev) advances from base
    upstream = await _seed_commit(
        async_session, repo_id, "dev", "Dev work", {"common.mid": "v0", "dev.mid": "d1"},
        base.commit_id,
    )

    # Current branch (main) has two commits since base
    c1 = await _seed_commit(
        async_session, repo_id, "main", "Add piano",
        {"common.mid": "v0", "piano.mid": "p1"},
        base.commit_id,
    )
    c2 = await _seed_commit(
        async_session, repo_id, "main", "Add strings",
        {"common.mid": "v0", "piano.mid": "p1", "strings.mid": "s1"},
        c1.commit_id,
    )

    # Set up repo HEAD on main
    (muse_dir / "HEAD").write_text("refs/heads/main")
    (muse_dir / "refs" / "heads" / "main").write_text(c2.commit_id)
    (muse_dir / "refs" / "heads" / "dev").write_text(upstream.commit_id)

    result = await _rebase_async(
        upstream="dev",
        root=repo_root,
        session=async_session,
    )

    assert isinstance(result, RebaseResult)
    assert result.noop is False
    assert result.aborted is False
    assert len(result.replayed) == 2

    # Original commits should be mapped to new commit IDs
    original_ids = {p.original_commit_id for p in result.replayed}
    assert c1.commit_id in original_ids
    assert c2.commit_id in original_ids

    # Branch pointer should be updated to the last replayed commit
    new_head = (muse_dir / "refs" / "heads" / "main").read_text().strip()
    new_commit_ids = {p.new_commit_id for p in result.replayed}
    assert new_head in new_commit_ids

    # The new HEAD should have a different commit_id (rebased)
    assert new_head != c2.commit_id


@pytest.mark.anyio
async def test_rebase_noop_already_up_to_date(
    async_session: AsyncSession,
    repo_id: str,
    repo_root: pathlib.Path,
) -> None:
    """Rebase is a no-op when HEAD equals the upstream tip."""
    muse_dir = repo_root / ".muse"

    commit = await _seed_commit(
        async_session, repo_id, "main", "Initial", {"a.mid": "v1"}
    )
    (muse_dir / "HEAD").write_text("refs/heads/main")
    (muse_dir / "refs" / "heads" / "main").write_text(commit.commit_id)
    (muse_dir / "refs" / "heads" / "dev").write_text(commit.commit_id)

    result = await _rebase_async(
        upstream="dev",
        root=repo_root,
        session=async_session,
    )

    assert result.noop is True
    assert result.replayed == ()


@pytest.mark.anyio
async def test_rebase_fast_forward_advances_pointer(
    async_session: AsyncSession,
    repo_id: str,
    repo_root: pathlib.Path,
) -> None:
    """When current branch is behind upstream, fast-forward the pointer."""
    muse_dir = repo_root / ".muse"

    base = await _seed_commit(
        async_session, repo_id, "main", "Base", {"a.mid": "v0"}
    )
    upstream = await _seed_commit(
        async_session, repo_id, "dev", "Dev ahead", {"a.mid": "v1"}, base.commit_id
    )

    # main is at base (behind dev)
    (muse_dir / "HEAD").write_text("refs/heads/main")
    (muse_dir / "refs" / "heads" / "main").write_text(base.commit_id)
    (muse_dir / "refs" / "heads" / "dev").write_text(upstream.commit_id)

    result = await _rebase_async(
        upstream="dev",
        root=repo_root,
        session=async_session,
    )

    assert result.noop is True
    assert result.replayed == ()
    new_head = (muse_dir / "refs" / "heads" / "main").read_text().strip()
    assert new_head == upstream.commit_id


@pytest.mark.anyio
async def test_rebase_already_ahead_noop(
    async_session: AsyncSession,
    repo_id: str,
    repo_root: pathlib.Path,
) -> None:
    """When upstream is the merge base (current branch ahead), result is noop."""
    muse_dir = repo_root / ".muse"

    upstream = await _seed_commit(
        async_session, repo_id, "dev", "Dev base", {"a.mid": "v0"}
    )
    current = await _seed_commit(
        async_session, repo_id, "main", "Ahead", {"a.mid": "v1"}, upstream.commit_id
    )

    (muse_dir / "HEAD").write_text("refs/heads/main")
    (muse_dir / "refs" / "heads" / "main").write_text(current.commit_id)
    (muse_dir / "refs" / "heads" / "dev").write_text(upstream.commit_id)

    result = await _rebase_async(
        upstream="dev",
        root=repo_root,
        session=async_session,
    )

    assert result.noop is True


# ---------------------------------------------------------------------------
# Async: abort
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rebase_abort_restores_branch(repo_root: pathlib.Path) -> None:
    """--abort restores the branch pointer to original_head."""
    muse_dir = repo_root / ".muse"
    (muse_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    original_head = "deadbeef" + "0" * 56
    (muse_dir / "refs" / "heads" / "main").write_text("newhead" + "0" * 57)

    state = RebaseState(
        upstream_commit="upstream" + "0" * 56,
        base_commit="base" + "0" * 60,
        original_branch="main",
        original_head=original_head,
        commits_to_replay=["rem1"],
        current_onto="onto" + "0" * 60,
        completed_pairs=[],
        current_commit="cur" + "0" * 61,
        conflict_paths=["beat.mid"],
    )
    write_rebase_state(repo_root, state)

    result = await _rebase_abort_async(root=repo_root)

    assert result.aborted is True
    restored = (muse_dir / "refs" / "heads" / "main").read_text().strip()
    assert restored == original_head
    assert read_rebase_state(repo_root) is None


@pytest.mark.anyio
async def test_rebase_abort_no_state_errors(repo_root: pathlib.Path) -> None:
    """--abort when no REBASE_STATE.json exits with USER_ERROR."""
    with pytest.raises(typer.Exit) as exc_info:
        await _rebase_abort_async(root=repo_root)
    assert exc_info.value.exit_code == 1


# ---------------------------------------------------------------------------
# Async: continue
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rebase_continue_no_state_errors(
    async_session: AsyncSession,
    repo_root: pathlib.Path,
) -> None:
    """--continue when no REBASE_STATE.json exits with USER_ERROR."""
    with pytest.raises(typer.Exit) as exc_info:
        await _rebase_continue_async(root=repo_root, session=async_session)
    assert exc_info.value.exit_code == 1


@pytest.mark.anyio
async def test_rebase_continue_replays_remaining(
    async_session: AsyncSession,
    repo_id: str,
    repo_root: pathlib.Path,
) -> None:
    """--continue replays remaining commits and advances the branch pointer."""
    muse_dir = repo_root / ".muse"

    # Seed a commit to represent the "onto" tip (already completed part)
    onto = await _seed_commit(
        async_session, repo_id, "main", "Onto tip", {"a.mid": "v1"}
    )

    # Seed a commit to replay
    remaining = await _seed_commit(
        async_session, repo_id, "main", "Remaining", {"a.mid": "v1", "b.mid": "b1"},
        onto.commit_id,
    )

    (muse_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (muse_dir / "refs" / "heads" / "main").write_text(onto.commit_id)

    state = RebaseState(
        upstream_commit=onto.commit_id,
        base_commit="base" + "0" * 60,
        original_branch="main",
        original_head="original" + "0" * 56,
        commits_to_replay=[remaining.commit_id],
        current_onto=onto.commit_id,
        completed_pairs=[],
        current_commit="",
        conflict_paths=[],
    )
    write_rebase_state(repo_root, state)

    result = await _rebase_continue_async(root=repo_root, session=async_session)

    assert result.aborted is False
    assert len(result.replayed) == 1
    new_head = (muse_dir / "refs" / "heads" / "main").read_text().strip()
    assert new_head == result.replayed[0].new_commit_id
    assert new_head != remaining.commit_id
    assert read_rebase_state(repo_root) is None


# ---------------------------------------------------------------------------
# Boundary seal — AST import check
# ---------------------------------------------------------------------------


def test_boundary_no_forbidden_imports() -> None:
    """muse_rebase must not import StateStore, EntityRegistry, or executor modules."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "muse_rebase",
        pathlib.Path(__file__).parent.parent
        / "maestro"
        / "services"
        / "muse_rebase.py",
    )
    assert spec is not None
    source_path = spec.origin
    assert source_path is not None

    tree = ast.parse(pathlib.Path(source_path).read_text())
    forbidden = {"StateStore", "EntityRegistry", "get_or_create_store"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden, (
                    f"Forbidden import {alias.name!r} in muse_rebase.py"
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not any(f in module for f in {"executor", "maestro_handlers"}), (
                f"Forbidden module import {module!r} in muse_rebase.py"
            )
            for alias in node.names:
                assert alias.name not in forbidden, (
                    f"Forbidden import {alias.name!r} from {module!r} in muse_rebase.py"
                )
