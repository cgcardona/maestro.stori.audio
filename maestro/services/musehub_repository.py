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
from datetime import datetime, timezone

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db import musehub_models as db
from maestro.models.musehub import (
    BranchResponse,
    CommitResponse,
    ObjectMetaResponse,
    RepoResponse,
    SessionResponse,
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


# ── Session helpers ────────────────────────────────────────────────────────────


def _to_session_response(row: db.MusehubSession) -> SessionResponse:
    """Convert a MusehubSession ORM row to its wire representation.

    Derives ``duration_seconds`` from the start/end timestamps so callers
    never need to compute it. Returns None for active sessions.
    """
    duration: float | None = None
    if row.ended_at is not None:
        duration = (row.ended_at - row.started_at).total_seconds()
    return SessionResponse(
        session_id=row.session_id,
        started_at=row.started_at,
        ended_at=row.ended_at,
        duration_seconds=duration,
        participants=list(row.participants or []),
        intent=row.intent,
        location=row.location,
        is_active=row.is_active,
        created_at=row.created_at,
    )


async def create_session(
    session: AsyncSession,
    repo_id: str,
    *,
    started_at: datetime | None,
    participants: list[str],
    intent: str,
    location: str,
    is_active: bool = True,
) -> SessionResponse:
    """Persist a new recording session entry and return its wire representation.

    Called when the CLI pushes a ``muse session start`` event to the Hub.
    If ``started_at`` is None, the current UTC time is used.
    """
    effective_start = started_at or datetime.now(tz=timezone.utc)
    row = db.MusehubSession(
        repo_id=repo_id,
        started_at=effective_start,
        participants=participants,
        intent=intent,
        location=location,
        is_active=is_active,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    logger.info("✅ Created Muse Hub session %s for repo %s", row.session_id, repo_id)
    return _to_session_response(row)


async def stop_session(
    session: AsyncSession,
    repo_id: str,
    session_id: str,
    ended_at: datetime | None,
) -> SessionResponse | None:
    """Mark a session as stopped and return the updated representation.

    Returns None if the session does not exist or belongs to a different repo.
    Called when the CLI pushes a ``muse session stop`` event to the Hub.
    """
    stmt = select(db.MusehubSession).where(
        db.MusehubSession.session_id == session_id,
        db.MusehubSession.repo_id == repo_id,
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return None
    row.ended_at = ended_at or datetime.now(tz=timezone.utc)
    row.is_active = False
    await session.flush()
    await session.refresh(row)
    logger.info("✅ Stopped Muse Hub session %s for repo %s", session_id, repo_id)
    return _to_session_response(row)


async def list_sessions(
    session: AsyncSession,
    repo_id: str,
    *,
    limit: int = 50,
) -> tuple[list[SessionResponse], int]:
    """Return sessions for a repo, newest first (by started_at).

    Returns a tuple of (sessions, total_count). Active sessions are listed
    first within the same timestamp order so live sessions surface at the top.
    """
    base = select(db.MusehubSession).where(db.MusehubSession.repo_id == repo_id)

    total_stmt = select(func.count()).select_from(base.subquery())
    total: int = (await session.execute(total_stmt)).scalar_one()

    # Active sessions first, then newest by start time.
    rows_stmt = (
        base.order_by(
            desc(db.MusehubSession.is_active),
            desc(db.MusehubSession.started_at),
        ).limit(limit)
    )
    rows = (await session.execute(rows_stmt)).scalars().all()
    return [_to_session_response(r) for r in rows], total


async def get_session(
    session: AsyncSession, repo_id: str, session_id: str
) -> SessionResponse | None:
    """Return a single session by ID within the given repo, or None."""
    stmt = select(db.MusehubSession).where(
        db.MusehubSession.session_id == session_id,
        db.MusehubSession.repo_id == repo_id,
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return None
    return _to_session_response(row)
