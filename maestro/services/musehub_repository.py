"""Muse Hub persistence adapter — single point of DB access for Hub entities.

This module is the ONLY place that touches the musehub_* tables.
Route handlers delegate here; no business logic lives in routes.

Boundary rules:
- Must NOT import state stores, SSE queues, or LLM clients.
- Must NOT import maestro.core.* modules.
- May import ORM models from maestro.db.musehub_models.
- May import Pydantic response models from maestro.models.musehub.
"""
from __future__ import annotations

import logging

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db import musehub_models as db
from maestro.models.musehub import (
    BranchResponse,
    CommitResponse,
    DagEdge,
    DagGraphResponse,
    DagNode,
    ObjectMetaResponse,
    RepoResponse,
)

logger = logging.getLogger(__name__)


def _repo_clone_url(repo_id: str) -> str:
    """Derive a deterministic clone URL from the repo ID.

    The URL format is intentionally simple for MVP; it will be parameterised
    by ``settings.musehub_base_url`` once that setting is introduced.
    """
    return f"/musehub/repos/{repo_id}"


def _to_repo_response(row: db.MusehubRepo) -> RepoResponse:
    return RepoResponse(
        repo_id=row.repo_id,
        name=row.name,
        visibility=row.visibility,
        owner_user_id=row.owner_user_id,
        clone_url=_repo_clone_url(row.repo_id),
        created_at=row.created_at,
    )


def _to_branch_response(row: db.MusehubBranch) -> BranchResponse:
    return BranchResponse(
        branch_id=row.branch_id,
        name=row.name,
        head_commit_id=row.head_commit_id,
    )


def _to_commit_response(row: db.MusehubCommit) -> CommitResponse:
    return CommitResponse(
        commit_id=row.commit_id,
        branch=row.branch,
        parent_ids=list(row.parent_ids or []),
        message=row.message,
        author=row.author,
        timestamp=row.timestamp,
        snapshot_id=row.snapshot_id,
    )


async def create_repo(
    session: AsyncSession,
    *,
    name: str,
    visibility: str,
    owner_user_id: str,
) -> RepoResponse:
    """Persist a new remote repo and return its wire representation."""
    repo = db.MusehubRepo(name=name, visibility=visibility, owner_user_id=owner_user_id)
    session.add(repo)
    await session.flush()  # populate default columns before reading
    await session.refresh(repo)
    logger.info("✅ Created Muse Hub repo %s (%s) for user %s", repo.repo_id, name, owner_user_id)
    return _to_repo_response(repo)


async def get_repo(session: AsyncSession, repo_id: str) -> RepoResponse | None:
    """Return repo metadata, or None if not found."""
    result = await session.get(db.MusehubRepo, repo_id)
    if result is None:
        return None
    return _to_repo_response(result)


async def list_branches(session: AsyncSession, repo_id: str) -> list[BranchResponse]:
    """Return all branches for a repo, ordered by name."""
    stmt = (
        select(db.MusehubBranch)
        .where(db.MusehubBranch.repo_id == repo_id)
        .order_by(db.MusehubBranch.name)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_branch_response(r) for r in rows]


def _to_object_meta_response(row: db.MusehubObject) -> ObjectMetaResponse:
    return ObjectMetaResponse(
        object_id=row.object_id,
        path=row.path,
        size_bytes=row.size_bytes,
        created_at=row.created_at,
    )


async def get_commit(
    session: AsyncSession, repo_id: str, commit_id: str
) -> CommitResponse | None:
    """Return a single commit by ID, or None if not found in this repo."""
    stmt = (
        select(db.MusehubCommit)
        .where(
            db.MusehubCommit.repo_id == repo_id,
            db.MusehubCommit.commit_id == commit_id,
        )
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return None
    return _to_commit_response(row)


async def list_objects(
    session: AsyncSession, repo_id: str
) -> list[ObjectMetaResponse]:
    """Return all object metadata for a repo (no binary content), ordered by path."""
    stmt = (
        select(db.MusehubObject)
        .where(db.MusehubObject.repo_id == repo_id)
        .order_by(db.MusehubObject.path)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_object_meta_response(r) for r in rows]


async def get_object_row(
    session: AsyncSession, repo_id: str, object_id: str
) -> db.MusehubObject | None:
    """Return the raw ORM object row for content delivery, or None if not found.

    Route handlers use this to stream the file from ``disk_path``.
    """
    stmt = (
        select(db.MusehubObject)
        .where(
            db.MusehubObject.repo_id == repo_id,
            db.MusehubObject.object_id == object_id,
        )
    )
    return (await session.execute(stmt)).scalars().first()


async def get_object_by_path(
    session: AsyncSession, repo_id: str, path: str
) -> db.MusehubObject | None:
    """Return the most-recently-created object matching ``path`` in a repo.

    Used by the raw file endpoint to resolve a human-readable path
    (e.g. ``tracks/bass.mid``) to the stored artifact on disk.  When
    multiple objects share the same path (re-pushed content), the newest
    one wins — consistent with Git's ref semantics where HEAD always
    points at the latest version.

    Args:
        session: Active async DB session.
        repo_id: UUID of the target repo.
        path: Client-supplied relative file path, e.g. ``tracks/bass.mid``.

    Returns:
        The matching ORM row, or ``None`` if no object with that path exists.
    """
    stmt = (
        select(db.MusehubObject)
        .where(
            db.MusehubObject.repo_id == repo_id,
            db.MusehubObject.path == path,
        )
        .order_by(desc(db.MusehubObject.created_at))
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def list_commits(
    session: AsyncSession,
    repo_id: str,
    *,
    branch: str | None = None,
    limit: int = 50,
) -> tuple[list[CommitResponse], int]:
    """Return commits for a repo, newest first, optionally filtered by branch.

    Returns a tuple of (commits, total_count).
    """
    base = select(db.MusehubCommit).where(db.MusehubCommit.repo_id == repo_id)
    if branch:
        base = base.where(db.MusehubCommit.branch == branch)

    total_stmt = select(func.count()).select_from(base.subquery())
    total: int = (await session.execute(total_stmt)).scalar_one()

    rows_stmt = base.order_by(desc(db.MusehubCommit.timestamp)).limit(limit)
    rows = (await session.execute(rows_stmt)).scalars().all()
    return [_to_commit_response(r) for r in rows], total


async def list_commits_dag(
    session: AsyncSession,
    repo_id: str,
) -> DagGraphResponse:
    """Return the full commit graph for a repo as a topologically sorted DAG.

    Fetches every commit for the repo (no limit — required for correct DAG
    traversal). Applies Kahn's algorithm to produce a topological ordering
    from oldest ancestor to newest commit, which graph renderers can consume
    directly without additional sorting.

    Edges flow child → parent (source = child, target = parent) following the
    standard directed graph convention where arrows point toward ancestors.

    Branch head commits are identified by querying the branches table. The
    highest-timestamp commit across all branches is designated as HEAD for
    display purposes when no explicit HEAD ref exists.

    Agent use case: call this to reason about the project's branching topology,
    find common ancestors, or identify which branches contain a given commit.
    """
    # Fetch all commits for this repo
    stmt = select(db.MusehubCommit).where(db.MusehubCommit.repo_id == repo_id)
    all_rows = (await session.execute(stmt)).scalars().all()

    if not all_rows:
        return DagGraphResponse(nodes=[], edges=[], head_commit_id=None)

    # Build lookup map
    row_map: dict[str, db.MusehubCommit] = {r.commit_id: r for r in all_rows}

    # Fetch all branches to identify HEAD candidates and branch labels
    branch_stmt = select(db.MusehubBranch).where(db.MusehubBranch.repo_id == repo_id)
    branch_rows = (await session.execute(branch_stmt)).scalars().all()

    # Map commit_id → branch names pointing at it
    branch_label_map: dict[str, list[str]] = {}
    for br in branch_rows:
        if br.head_commit_id and br.head_commit_id in row_map:
            branch_label_map.setdefault(br.head_commit_id, []).append(br.name)

    # Identify HEAD: the branch head with the most recent timestamp, or the
    # most recent commit overall when no branches exist
    head_commit_id: str | None = None
    if branch_rows:
        latest_ts = None
        for br in branch_rows:
            if br.head_commit_id and br.head_commit_id in row_map:
                ts = row_map[br.head_commit_id].timestamp
                if latest_ts is None or ts > latest_ts:
                    latest_ts = ts
                    head_commit_id = br.head_commit_id
    if head_commit_id is None:
        head_commit_id = max(all_rows, key=lambda r: r.timestamp).commit_id

    # Kahn's topological sort (oldest → newest)
    # in-degree = number of children that list this commit as parent
    in_degree: dict[str, int] = {r.commit_id: 0 for r in all_rows}
    children_map: dict[str, list[str]] = {r.commit_id: [] for r in all_rows}

    edges: list[DagEdge] = []
    for row in all_rows:
        for parent_id in (row.parent_ids or []):
            if parent_id in row_map:
                edges.append(DagEdge(source=row.commit_id, target=parent_id))
                in_degree[row.commit_id] = in_degree.get(row.commit_id, 0)
                children_map.setdefault(parent_id, []).append(row.commit_id)
                # Parents have their child-count tracked via in_degree of children
                # For Kahn's on a DAG where edges go child→parent, we need the
                # reverse: treat parent edges as dependencies of children.
                # Re-define: in_degree[child] = number of parents child has in graph
                in_degree[row.commit_id] += 1

    # Kahn's algorithm: start from commits with no parents (roots)
    from collections import deque

    queue: deque[str] = deque(
        cid for cid, deg in in_degree.items() if deg == 0
    )
    topo_order: list[str] = []

    while queue:
        cid = queue.popleft()
        topo_order.append(cid)
        for child_id in children_map.get(cid, []):
            in_degree[child_id] -= 1
            if in_degree[child_id] == 0:
                queue.append(child_id)

    # Handle cycles or disconnected commits (append remaining in timestamp order)
    remaining = set(row_map.keys()) - set(topo_order)
    if remaining:
        sorted_remaining = sorted(remaining, key=lambda c: row_map[c].timestamp)
        topo_order.extend(sorted_remaining)

    nodes: list[DagNode] = []
    for cid in topo_order:
        row = row_map[cid]
        nodes.append(
            DagNode(
                commit_id=row.commit_id,
                message=row.message,
                author=row.author,
                timestamp=row.timestamp,
                branch=row.branch,
                parent_ids=list(row.parent_ids or []),
                is_head=(row.commit_id == head_commit_id),
                branch_labels=branch_label_map.get(row.commit_id, []),
                tag_labels=[],
            )
        )

    logger.debug("✅ Built DAG for repo %s: %d nodes, %d edges", repo_id, len(nodes), len(edges))
    return DagGraphResponse(nodes=nodes, edges=edges, head_commit_id=head_commit_id)
