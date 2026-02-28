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
from maestro.models.musehub import ExploreRepoResult, ExploreResponse, StarResponse

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
