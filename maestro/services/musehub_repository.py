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
        description=row.description,
        tags=list(row.tags or []),
        key_signature=row.key_signature,
        tempo_bpm=row.tempo_bpm,
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
    description: str = "",
    tags: list[str] | None = None,
    key_signature: str | None = None,
    tempo_bpm: int | None = None,
) -> RepoResponse:
    """Persist a new remote repo and return its wire representation."""
    repo = db.MusehubRepo(
        name=name,
        visibility=visibility,
        owner_user_id=owner_user_id,
        description=description,
        tags=tags or [],
        key_signature=key_signature,
        tempo_bpm=tempo_bpm,
    )
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
