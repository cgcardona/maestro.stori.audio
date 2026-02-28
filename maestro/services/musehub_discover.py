"""Muse Hub discover/explore service — public repo discovery with filtering and sorting.

This module is the ONLY place that executes the discover query. Route handlers
delegate here; no filtering or sorting logic lives in routes.

Boundary rules:
- Must NOT import state stores, SSE queues, or LLM clients.
- Must NOT import maestro.core.* modules.
- May import ORM models from maestro.db.musehub_models.
- May import Pydantic response models from maestro.models.musehub.

Sort semantics:
  "stars"    — repos with the most stars first (trending signal)
  "activity" — repos with the most recent commit first
  "commits"  — repos with the highest total commit count first
  "created"  — newest repos first (default for explore page)

Tag filtering uses a contains check on the JSON ``tags`` column. For portability
across Postgres and SQLite (tests), the check is done server-side via a
``cast(tags, Text).ilike`` pattern rather than JSON containment operators, which
differ between engines and are not needed at this scale.
"""
from __future__ import annotations

import logging
from typing import Literal

from sqlalchemy import Text, desc, func, outerjoin, select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db import musehub_models as db
from maestro.models.musehub import (
    ExploreRepoResult,
    ExploreResponse,
    ForkCreateResponse,
    ForkEntry,
    ForkListResponse,
    RepoResponse,
    StarResponse,
)

logger = logging.getLogger(__name__)

SortField = Literal["stars", "activity", "commits", "created"]

_PAGE_SIZE_MAX = 100


async def list_public_repos(
    session: AsyncSession,
    *,
    genre: str | None = None,
    key: str | None = None,
    tempo_min: int | None = None,
    tempo_max: int | None = None,
    instrumentation: str | None = None,
    sort: SortField = "created",
    page: int = 1,
    page_size: int = 24,
) -> ExploreResponse:
    """Return a paginated list of public repos that match the given filters.

    Only repos with ``visibility = 'public'`` are returned. All filter parameters
    are optional; omitting them returns all public repos in the requested sort order.

    Args:
        session: Async DB session.
        genre: Case-insensitive substring match against the repo's ``tags`` JSON.
               Matches repos where any tag contains this string (e.g. "jazz").
        key: Exact case-insensitive match against ``key_signature`` (e.g. "F# minor").
        tempo_min: Include only repos with ``tempo_bpm >= tempo_min``.
        tempo_max: Include only repos with ``tempo_bpm <= tempo_max``.
        instrumentation: Case-insensitive substring match against tags — used to
                         filter by instrument presence (e.g. "bass", "drums").
        sort: One of "stars", "activity", "commits", "created".
        page: 1-based page number.
        page_size: Number of results per page (clamped to _PAGE_SIZE_MAX).

    Returns:
        ExploreResponse with repo cards and pagination metadata.
    """
    page_size = min(page_size, _PAGE_SIZE_MAX)
    offset = (max(page, 1) - 1) * page_size

    # Aggregated sub-expressions ─────────────────────────────────────────────
    star_count_col = func.count(db.MusehubStar.star_id).label("star_count")
    commit_count_col = func.count(db.MusehubCommit.commit_id).label("commit_count")
    latest_commit_col = func.max(db.MusehubCommit.timestamp).label("latest_commit")

    # Build the base aggregated query over public repos.
    # Left-join stars and commits so repos with zero stars/commits are included.
    base_q = (
        select(
            db.MusehubRepo,
            star_count_col,
            commit_count_col,
            latest_commit_col,
        )
        .select_from(
            outerjoin(
                outerjoin(
                    db.MusehubRepo,
                    db.MusehubStar,
                    db.MusehubRepo.repo_id == db.MusehubStar.repo_id,
                ),
                db.MusehubCommit,
                db.MusehubRepo.repo_id == db.MusehubCommit.repo_id,
            )
        )
        .where(db.MusehubRepo.visibility == "public")
        .group_by(db.MusehubRepo.repo_id)
    )

    # Apply filters ──────────────────────────────────────────────────────────
    if genre:
        # Match repos where any tag contains the genre string (case-insensitive).
        # We cast the JSON column to text and use ILIKE for cross-engine compat.
        base_q = base_q.where(
            func.cast(db.MusehubRepo.tags, Text).ilike(f"%{genre.lower()}%")
        )
    if instrumentation:
        base_q = base_q.where(
            func.cast(db.MusehubRepo.tags, Text).ilike(f"%{instrumentation.lower()}%")
        )
    if key:
        base_q = base_q.where(
            func.lower(db.MusehubRepo.key_signature) == key.lower()
        )
    if tempo_min is not None:
        base_q = base_q.where(db.MusehubRepo.tempo_bpm >= tempo_min)
    if tempo_max is not None:
        base_q = base_q.where(db.MusehubRepo.tempo_bpm <= tempo_max)

    # Count total results before pagination ──────────────────────────────────
    count_q = select(func.count()).select_from(base_q.subquery())
    total: int = (await session.execute(count_q)).scalar_one()

    # Apply sort ─────────────────────────────────────────────────────────────
    if sort == "stars":
        base_q = base_q.order_by(desc("star_count"), desc(db.MusehubRepo.created_at))
    elif sort == "activity":
        base_q = base_q.order_by(desc("latest_commit"), desc(db.MusehubRepo.created_at))
    elif sort == "commits":
        base_q = base_q.order_by(desc("commit_count"), desc(db.MusehubRepo.created_at))
    else:  # "created"
        base_q = base_q.order_by(desc(db.MusehubRepo.created_at))

    rows = (await session.execute(base_q.offset(offset).limit(page_size))).all()

    results = [
        ExploreRepoResult(
            repo_id=row.MusehubRepo.repo_id,
            name=row.MusehubRepo.name,
            owner=row.MusehubRepo.owner,
            slug=row.MusehubRepo.slug,
            owner_user_id=row.MusehubRepo.owner_user_id,
            description=row.MusehubRepo.description,
            tags=list(row.MusehubRepo.tags or []),
            key_signature=row.MusehubRepo.key_signature,
            tempo_bpm=row.MusehubRepo.tempo_bpm,
            star_count=row.star_count or 0,
            commit_count=row.commit_count or 0,
            created_at=row.MusehubRepo.created_at,
        )
        for row in rows
    ]

    logger.debug("✅ Explore query: %d/%d repos (page %d, sort=%s)", len(results), total, page, sort)
    return ExploreResponse(repos=results, total=total, page=page, page_size=page_size)


async def star_repo(session: AsyncSession, repo_id: str, user_id: str) -> StarResponse:
    """Add a star for user_id on repo_id. Idempotent — duplicate stars are silently ignored.

    Returns StarResponse with the new total star count and ``starred=True``.
    Raises ValueError if the repo does not exist or is not public.
    """
    repo = await session.get(db.MusehubRepo, repo_id)
    if repo is None:
        raise ValueError(f"Repo {repo_id!r} not found")
    if repo.visibility != "public":
        raise ValueError(f"Repo {repo_id!r} is not public")

    # Check for existing star to make the operation idempotent.
    existing = (
        await session.execute(
            select(db.MusehubStar).where(
                db.MusehubStar.repo_id == repo_id,
                db.MusehubStar.user_id == user_id,
            )
        )
    ).scalars().first()

    if existing is None:
        star = db.MusehubStar(repo_id=repo_id, user_id=user_id)
        session.add(star)
        await session.flush()
        logger.info("✅ User %s starred repo %s", user_id, repo_id)

    count: int = (
        await session.execute(
            select(func.count(db.MusehubStar.star_id)).where(
                db.MusehubStar.repo_id == repo_id
            )
        )
    ).scalar_one()

    return StarResponse(starred=True, star_count=count)


async def unstar_repo(session: AsyncSession, repo_id: str, user_id: str) -> StarResponse:
    """Remove a star for user_id on repo_id. Idempotent — no-op if not starred.

    Returns StarResponse with the new total star count and ``starred=False``.
    """
    existing = (
        await session.execute(
            select(db.MusehubStar).where(
                db.MusehubStar.repo_id == repo_id,
                db.MusehubStar.user_id == user_id,
            )
        )
    ).scalars().first()

    if existing is not None:
        await session.delete(existing)
        await session.flush()
        logger.info("✅ User %s unstarred repo %s", user_id, repo_id)

    count: int = (
        await session.execute(
            select(func.count(db.MusehubStar.star_id)).where(
                db.MusehubStar.repo_id == repo_id
            )
        )
    ).scalar_one()

    return StarResponse(starred=False, star_count=count)




async def create_fork(
    session: AsyncSession,
    *,
    source_repo_id: str,
    user_id: str,
    owner: str,
) -> ForkCreateResponse:
    """Fork a public repo into the authenticated user's namespace.

    Creates a new ``MusehubRepo`` owned by ``owner`` / ``user_id`` that is a
    copy of the source repo, then copies all commits from the source into the
    new fork and records the lineage in ``MusehubFork``.

    Args:
        session: Async DB session.
        source_repo_id: UUID of the repo to fork.
        user_id: JWT ``sub`` of the authenticated user (becomes ``owner_user_id``).
        owner: Human-readable owner identifier (typically user_id for now).

    Returns:
        ForkCreateResponse with the new fork repo and lineage metadata.

    Raises:
        ValueError: If the source repo does not exist.
    """
    from maestro.services import musehub_repository

    source_orm = await session.get(db.MusehubRepo, source_repo_id)
    if source_orm is None:
        raise ValueError(f"Source repo {source_repo_id!r} not found")

    source_owner: str = source_orm.owner
    source_slug: str = source_orm.slug

    fork_repo: RepoResponse = await musehub_repository.create_repo(
        session,
        name=source_orm.name,
        owner=owner,
        visibility="public",
        owner_user_id=user_id,
        description=source_orm.description or "",
        tags=list(source_orm.tags) if source_orm.tags else [],
        key_signature=source_orm.key_signature,
        tempo_bpm=source_orm.tempo_bpm,
    )

    commits_result = await session.execute(
        select(db.MusehubCommit).where(db.MusehubCommit.repo_id == source_repo_id)
    )
    source_commits = commits_result.scalars().all()
    for src_commit in source_commits:
        session.add(
            db.MusehubCommit(
                commit_id=src_commit.commit_id,
                repo_id=fork_repo.repo_id,
                branch=src_commit.branch,
                parent_ids=list(src_commit.parent_ids),
                message=src_commit.message,
                author=src_commit.author,
                timestamp=src_commit.timestamp,
                snapshot_id=src_commit.snapshot_id,
            )
        )

    session.add(
        db.MusehubFork(
            source_repo_id=source_repo_id,
            fork_repo_id=fork_repo.repo_id,
            forked_by=user_id,
        )
    )
    await session.flush()

    logger.info(
        "✅ User %s forked repo %s → %s (%s/%s)",
        user_id, source_repo_id, fork_repo.repo_id, owner, fork_repo.slug,
    )

    return ForkCreateResponse(
        fork_repo=fork_repo,
        source_repo_id=source_repo_id,
        source_owner=source_owner,
        source_slug=source_slug,
    )


async def list_forks(
    session: AsyncSession,
    source_repo_id: str,
) -> ForkListResponse:
    """Return all forks of a repo ordered by creation time (newest first).

    Args:
        session: Async DB session.
        source_repo_id: UUID of the source repo whose forks are listed.

    Returns:
        ForkListResponse with fork entries and total count.
    """
    result = await session.execute(
        select(db.MusehubFork, db.MusehubRepo)
        .join(db.MusehubRepo, db.MusehubRepo.repo_id == db.MusehubFork.fork_repo_id)
        .where(db.MusehubFork.source_repo_id == source_repo_id)
        .order_by(db.MusehubFork.created_at.desc())
    )
    rows = result.all()
    entries = [
        ForkEntry(
            fork_id=fork_row.fork_id,
            fork_repo_id=fork_row.fork_repo_id,
            source_repo_id=fork_row.source_repo_id,
            forked_by=fork_row.forked_by,
            fork_owner=repo_row.owner,
            fork_slug=repo_row.slug,
            created_at=fork_row.created_at,
        )
        for fork_row, repo_row in rows
    ]
    return ForkListResponse(forks=entries, total=len(entries))
