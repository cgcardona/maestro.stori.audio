"""Muse Hub sync service — push and pull protocol implementation.

Implements the two core data-movement operations:
- ``ingest_push``: stores commits and objects from a client push, enforcing
  fast-forward semantics and updating the branch head.
- ``compute_pull_delta``: returns commits and objects the client does not yet
  have, keyed by their ``have_commits`` / ``have_objects`` exclusion lists.

Object content is written to disk under
  ``settings.musehub_objects_dir/<repo_id>/<object_id>``
while metadata (path, size, disk_path) is persisted to Postgres.

Boundary rules (same as musehub_repository):
- Must NOT import state stores, SSE queues, or LLM clients.
- Must NOT import maestro.core.* modules.
- May import ORM models from maestro.db.musehub_models.
- May import Pydantic models from maestro.models.musehub.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.config import settings
from maestro.db import musehub_models as db
from maestro.models.musehub import (
    CommitInput,
    CommitResponse,
    ObjectInput,
    ObjectResponse,
    PullResponse,
    PushResponse,
)
from maestro.services.musehub_embeddings import compute_embedding
from maestro.services.musehub_qdrant import MusehubQdrantClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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


def _to_object_response(row: db.MusehubObject) -> ObjectResponse:
    """Read object bytes from disk and return as base64-encoded response."""
    try:
        raw = Path(row.disk_path).read_bytes()
        content_b64 = base64.b64encode(raw).decode()
    except OSError:
        logger.warning(
            "⚠️ Object file missing on disk: %s (object_id=%s)", row.disk_path, row.object_id
        )
        content_b64 = ""
    return ObjectResponse(
        object_id=row.object_id,
        path=row.path,
        content_b64=content_b64,
    )


def _object_disk_path(repo_id: str, object_id: str) -> Path:
    """Return the canonical on-disk path for an object.

    The object_id may contain a colon (e.g. ``sha256:abc…``); replace it with
    a dash so it is safe on all filesystems.
    """
    safe_id = object_id.replace(":", "-")
    return Path(settings.musehub_objects_dir) / repo_id / safe_id


def _is_fast_forward(
    remote_head: str | None,
    head_commit_id: str,
    commits: list[CommitInput],
) -> bool:
    """Return True if the push is a fast-forward update.

    A push is fast-forward when:
    - the remote branch has no head yet (first push), or
    - the new head_commit_id equals the remote head (no-op), or
    - the remote head appears somewhere in the ancestry graph of the pushed
      commits (meaning the client built on top of the remote head).

    We build a local graph from the pushed commits and walk parents.  This
    does NOT query the DB for previously stored commits — for MVP the client
    is expected to include all commits since the common ancestor.
    """
    if remote_head is None:
        return True
    if head_commit_id == remote_head:
        return True

    parent_map: dict[str, list[str]] = {c.commit_id: c.parent_ids for c in commits}

    # BFS from head_commit_id, following parents
    visited: set[str] = set()
    frontier = [head_commit_id]
    while frontier:
        current = frontier.pop()
        if current in visited:
            continue
        visited.add(current)
        if current == remote_head:
            return True
        for parent in parent_map.get(current, []):
            if parent not in visited:
                frontier.append(parent)
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def ingest_push(
    session: AsyncSession,
    *,
    repo_id: str,
    branch: str,
    head_commit_id: str,
    commits: list[CommitInput],
    objects: list[ObjectInput],
    force: bool,
    author: str,
) -> PushResponse:
    """Store commits and objects from a push request and update the branch head.

    Raises:
        ValueError: with key ``"non_fast_forward"`` when the update would be
            non-fast-forward and ``force`` is False.  The route handler maps
            this to HTTP 409.
    """
    # ------------------------------------------------------------------
    # 1. Resolve (or create) the branch
    # ------------------------------------------------------------------
    branch_row = await _get_or_create_branch(session, repo_id=repo_id, branch=branch)

    # ------------------------------------------------------------------
    # 2. Fast-forward check
    # ------------------------------------------------------------------
    if not force and not _is_fast_forward(branch_row.head_commit_id, head_commit_id, commits):
        logger.warning(
            "⚠️ Non-fast-forward push rejected for repo=%s branch=%s remote_head=%s new_head=%s",
            repo_id,
            branch,
            branch_row.head_commit_id,
            head_commit_id,
        )
        raise ValueError("non_fast_forward")

    # ------------------------------------------------------------------
    # 3. Upsert commits
    # ------------------------------------------------------------------
    existing_commit_ids: set[str] = set()
    if commits:
        stmt = select(db.MusehubCommit.commit_id).where(
            db.MusehubCommit.repo_id == repo_id,
            db.MusehubCommit.commit_id.in_([c.commit_id for c in commits]),
        )
        result = await session.execute(stmt)
        existing_commit_ids = set(result.scalars().all())

    new_commits: list[db.MusehubCommit] = []
    for c in commits:
        if c.commit_id in existing_commit_ids:
            continue
        row = db.MusehubCommit(
            commit_id=c.commit_id,
            repo_id=repo_id,
            branch=branch,
            parent_ids=c.parent_ids,
            message=c.message,
            author=c.author if c.author else author,
            timestamp=c.timestamp,
            snapshot_id=c.snapshot_id,
        )
        new_commits.append(row)
    if new_commits:
        session.add_all(new_commits)
        logger.info("✅ Ingested %d new commits for repo=%s", len(new_commits), repo_id)

    # ------------------------------------------------------------------
    # 4. Upsert objects (write bytes to disk, metadata to DB)
    # ------------------------------------------------------------------
    existing_object_ids: set[str] = set()
    if objects:
        stmt_obj = select(db.MusehubObject.object_id).where(
            db.MusehubObject.repo_id == repo_id,
            db.MusehubObject.object_id.in_([o.object_id for o in objects]),
        )
        res_obj = await session.execute(stmt_obj)
        existing_object_ids = set(res_obj.scalars().all())

    for obj in objects:
        if obj.object_id in existing_object_ids:
            continue
        await _write_object(session, repo_id=repo_id, obj=obj)

    # ------------------------------------------------------------------
    # 5. Update branch head
    # ------------------------------------------------------------------
    branch_row.head_commit_id = head_commit_id
    logger.info(
        "✅ Branch '%s' head updated to %s for repo=%s",
        branch,
        head_commit_id,
        repo_id,
    )

    await session.flush()
    return PushResponse(ok=True, remote_head=head_commit_id)


async def compute_pull_delta(
    session: AsyncSession,
    *,
    repo_id: str,
    branch: str,
    have_commits: list[str],
    have_objects: list[str],
) -> PullResponse:
    """Return commits and objects the caller does not have.

    MVP simplification: ``have_commits`` / ``have_objects`` act as exclusion
    lists — we return everything stored for this repo/branch that is NOT in
    those lists.  No ancestry traversal is performed; the client gets all
    missing data in one response.
    """
    branch_row = await _get_branch(session, repo_id=repo_id, branch=branch)
    remote_head = branch_row.head_commit_id if branch_row else None

    # ------------------------------------------------------------------
    # Missing commits
    # ------------------------------------------------------------------
    commit_stmt = select(db.MusehubCommit).where(
        db.MusehubCommit.repo_id == repo_id,
        db.MusehubCommit.branch == branch,
    )
    if have_commits:
        commit_stmt = commit_stmt.where(
            db.MusehubCommit.commit_id.notin_(have_commits)
        )
    commit_rows = (await session.execute(commit_stmt)).scalars().all()
    missing_commits = [_to_commit_response(r) for r in commit_rows]

    # ------------------------------------------------------------------
    # Missing objects
    # ------------------------------------------------------------------
    obj_stmt = select(db.MusehubObject).where(db.MusehubObject.repo_id == repo_id)
    if have_objects:
        obj_stmt = obj_stmt.where(db.MusehubObject.object_id.notin_(have_objects))
    obj_rows = (await session.execute(obj_stmt)).scalars().all()
    missing_objects = [_to_object_response(r) for r in obj_rows]

    logger.info(
        "✅ Pull delta: %d commits, %d objects for repo=%s branch=%s",
        len(missing_commits),
        len(missing_objects),
        repo_id,
        branch,
    )
    return PullResponse(
        commits=missing_commits,
        objects=missing_objects,
        remote_head=remote_head,
    )


def embed_push_commits(
    *,
    commits: list[CommitInput],
    repo_id: str,
    branch: str,
    author: str,
    is_public: bool,
) -> None:
    """Compute and upsert musical embeddings for a batch of pushed commits.

    Called as a FastAPI BackgroundTask after a successful push so the push
    response is not blocked by embedding computation. Errors are logged but
    never raised — a missing embedding is recoverable (re-push or backfill),
    whereas crashing the background task would lose the error silently.

    This function is synchronous because QdrantClient is synchronous. It is
    safe to call from an asyncio context via ``BackgroundTasks.add_task``
    (FastAPI runs background tasks after the response in the same event loop
    thread pool for sync callables).

    Args:
        commits: CommitInput list from the push payload.
        repo_id: UUID of the owning repository.
        branch: Branch name (stored in Qdrant payload for display).
        author: Commit author identifier.
        is_public: Whether the repo is public — controls visibility filtering.
    """
    if not commits:
        return

    try:
        client = MusehubQdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
        )
        client.ensure_collection()

        for commit in commits:
            vector = compute_embedding(commit.message)
            client.upsert_embedding(
                commit_id=commit.commit_id,
                repo_id=repo_id,
                is_public=is_public,
                vector=vector,
                branch=branch,
                author=commit.author or author,
            )

        logger.info(
            "✅ Embedded %d commits for repo=%s (is_public=%s)",
            len(commits),
            repo_id,
            is_public,
        )
    except Exception as exc:
        logger.error(
            "❌ Failed to embed commits for repo=%s: %s",
            repo_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _get_branch(
    session: AsyncSession, *, repo_id: str, branch: str
) -> db.MusehubBranch | None:
    stmt = select(db.MusehubBranch).where(
        db.MusehubBranch.repo_id == repo_id,
        db.MusehubBranch.name == branch,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _get_or_create_branch(
    session: AsyncSession, *, repo_id: str, branch: str
) -> db.MusehubBranch:
    existing = await _get_branch(session, repo_id=repo_id, branch=branch)
    if existing is not None:
        return existing
    new_branch = db.MusehubBranch(repo_id=repo_id, name=branch)
    session.add(new_branch)
    await session.flush()
    logger.info("✅ Created branch '%s' for repo=%s", branch, repo_id)
    return new_branch


async def _write_object(
    session: AsyncSession,
    *,
    repo_id: str,
    obj: ObjectInput,
) -> None:
    """Decode base64 content, write to disk, and insert metadata row."""
    disk_path = _object_disk_path(repo_id, obj.object_id)

    # Write bytes to disk (async-safe: use executor for blocking I/O would be
    # ideal in high-throughput, but for MVP objects are ≤1 MB and this path
    # runs outside the hot loop).
    disk_path.parent.mkdir(parents=True, exist_ok=True)
    raw = base64.b64decode(obj.content_b64)
    disk_path.write_bytes(raw)

    row = db.MusehubObject(
        object_id=obj.object_id,
        repo_id=repo_id,
        path=obj.path,
        size_bytes=len(raw),
        disk_path=str(disk_path),
    )
    session.add(row)
    logger.info(
        "✅ Stored object %s (%d bytes) for repo=%s at %s",
        obj.object_id,
        len(raw),
        repo_id,
        disk_path,
    )
