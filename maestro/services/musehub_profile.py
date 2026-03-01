"""Muse Hub profile persistence adapter.

Single point of DB access for user-profile entities (``musehub_profiles``).
Aggregates cross-repo data (public repos, contribution graph, session credits)
so that route handlers stay thin.

Boundary rules:
- Must NOT import state stores, SSE queues, or LLM clients.
- Must NOT import maestro.core.* modules.
- May import ORM models from maestro.db.musehub_models.
- May import Pydantic response models from maestro.models.musehub.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db import musehub_models as db
from maestro.models.musehub import (
    ContributionDay,
    ProfileResponse,
    ProfileRepoSummary,
    ProfileUpdateRequest,
)

logger = logging.getLogger(__name__)

_MAX_PINNED = 6
_CONTRIBUTION_WEEKS = 52


def _utc_today() -> datetime:
    return datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Converters
# ---------------------------------------------------------------------------


def _to_profile_response(
    profile: db.MusehubProfile,
    repos: list[ProfileRepoSummary],
    contribution_graph: list[ContributionDay],
    session_credits: int,
) -> ProfileResponse:
    return ProfileResponse(
        user_id=profile.user_id,
        username=profile.username,
        display_name=profile.display_name,
        bio=profile.bio,
        avatar_url=profile.avatar_url,
        location=profile.location,
        website_url=profile.website_url,
        twitter_handle=profile.twitter_handle,
        is_verified=profile.is_verified,
        cc_license=profile.cc_license,
        pinned_repo_ids=list(profile.pinned_repo_ids or []),
        repos=repos,
        contribution_graph=contribution_graph,
        session_credits=session_credits,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


# ---------------------------------------------------------------------------
# Profile CRUD
# ---------------------------------------------------------------------------


async def get_profile_by_username(
    session: AsyncSession, username: str
) -> db.MusehubProfile | None:
    """Fetch a profile row by URL-friendly username. Returns None if not found."""
    result = await session.execute(
        select(db.MusehubProfile).where(db.MusehubProfile.username == username)
    )
    return result.scalar_one_or_none()


async def get_profile_by_user_id(
    session: AsyncSession, user_id: str
) -> db.MusehubProfile | None:
    """Fetch a profile row by the owner's user_id (JWT sub). Returns None if not found."""
    result = await session.execute(
        select(db.MusehubProfile).where(db.MusehubProfile.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def create_profile(
    session: AsyncSession,
    *,
    user_id: str,
    username: str,
    bio: str | None = None,
    avatar_url: str | None = None,
) -> db.MusehubProfile:
    """Create a new user profile. Raises if ``username`` is already taken."""
    profile = db.MusehubProfile(
        user_id=user_id,
        username=username,
        bio=bio,
        avatar_url=avatar_url,
        pinned_repo_ids=[],
    )
    session.add(profile)
    await session.flush()
    return profile


async def update_profile(
    session: AsyncSession,
    profile: db.MusehubProfile,
    patch: ProfileUpdateRequest,
) -> db.MusehubProfile:
    """Apply a partial update to an existing profile and flush to DB.

    Only fields present in ``patch`` (non-None) are updated so callers can
    send a sparse payload.
    """
    if patch.display_name is not None:
        profile.display_name = patch.display_name
    if patch.bio is not None:
        profile.bio = patch.bio
    if patch.avatar_url is not None:
        profile.avatar_url = patch.avatar_url
    if patch.location is not None:
        profile.location = patch.location
    if patch.website_url is not None:
        profile.website_url = patch.website_url
    if patch.twitter_handle is not None:
        profile.twitter_handle = patch.twitter_handle
    if patch.pinned_repo_ids is not None:
        profile.pinned_repo_ids = patch.pinned_repo_ids[:_MAX_PINNED]
    profile.updated_at = datetime.now(tz=timezone.utc)
    session.add(profile)
    await session.flush()
    return profile


# ---------------------------------------------------------------------------
# Aggregation queries
# ---------------------------------------------------------------------------


async def get_public_repos(
    session: AsyncSession, user_id: str
) -> list[ProfileRepoSummary]:
    """Return public repos owned by ``user_id``, newest first.

    Last-activity timestamp is the most recent commit timestamp across all
    branches in the repo.  Star count is always 0 at MVP (no star mechanism).
    """
    repo_rows_result = await session.execute(
        select(db.MusehubRepo)
        .where(
            db.MusehubRepo.owner_user_id == user_id,
            db.MusehubRepo.visibility == "public",
        )
        .order_by(desc(db.MusehubRepo.created_at))
    )
    repo_rows = list(repo_rows_result.scalars())

    if not repo_rows:
        return []

    # Fetch latest commit timestamp per repo in one query
    repo_ids = [r.repo_id for r in repo_rows]
    latest_result = await session.execute(
        select(
            db.MusehubCommit.repo_id,
            func.max(db.MusehubCommit.timestamp).label("last_activity"),
        )
        .where(db.MusehubCommit.repo_id.in_(repo_ids))
        .group_by(db.MusehubCommit.repo_id)
    )
    last_activity: dict[str, datetime] = {row.repo_id: row.last_activity for row in latest_result}

    return [
        ProfileRepoSummary(
            repo_id=r.repo_id,
            name=r.name,
            owner=r.owner,
            slug=r.slug,
            visibility=r.visibility,
            star_count=0,
            last_activity_at=last_activity.get(r.repo_id),
            created_at=r.created_at,
        )
        for r in repo_rows
    ]


async def get_contribution_graph(
    session: AsyncSession, user_id: str
) -> list[ContributionDay]:
    """Return the last 52 weeks of daily commit activity for a user.

    Counts commits across ALL repos owned by ``user_id`` (public and private)
    so the owner sees their full creative history.  Visitors see the same data
    because the profile page is public (no server-side filtering by caller).

    Returns a list of 364 days in ascending date order, with ``count=0`` for
    days with no commits.  The client renders this as a GitHub-style heatmap.
    """
    today = _utc_today()
    cutoff = today - timedelta(weeks=_CONTRIBUTION_WEEKS)

    # Find all repos for this user (public + private)
    repo_result = await session.execute(
        select(db.MusehubRepo.repo_id).where(db.MusehubRepo.owner_user_id == user_id)
    )
    repo_ids = [row[0] for row in repo_result]

    if not repo_ids:
        return _empty_contribution_graph(today, cutoff)

    # Daily commit counts
    daily_result = await session.execute(
        select(
            func.date(db.MusehubCommit.timestamp).label("day"),
            func.count(db.MusehubCommit.commit_id).label("cnt"),
        )
        .where(
            db.MusehubCommit.repo_id.in_(repo_ids),
            db.MusehubCommit.timestamp >= cutoff,
        )
        .group_by(func.date(db.MusehubCommit.timestamp))
    )
    counts: dict[str, int] = {str(row.day): row.cnt for row in daily_result}

    # Build a dense calendar — every day from cutoff to today
    days: list[ContributionDay] = []
    cursor = cutoff
    while cursor <= today:
        iso = cursor.strftime("%Y-%m-%d")
        days.append(ContributionDay(date=iso, count=counts.get(iso, 0)))
        cursor += timedelta(days=1)

    return days


def _empty_contribution_graph(today: datetime, cutoff: datetime) -> list[ContributionDay]:
    """Return a zero-filled contribution graph for users with no repos."""
    days: list[ContributionDay] = []
    cursor = cutoff
    while cursor <= today:
        days.append(ContributionDay(date=cursor.strftime("%Y-%m-%d"), count=0))
        cursor += timedelta(days=1)
    return days


async def get_session_credits(session: AsyncSession, user_id: str) -> int:
    """Count total commits across all repos for a user.

    Commits represent creative composition sessions — each push of a musical
    snapshot to MuseHub is a unit of creative credit.  This is the MVP proxy
    for "session credits"; a future release may tie this to actual token usage.
    """
    repo_result = await session.execute(
        select(db.MusehubRepo.repo_id).where(db.MusehubRepo.owner_user_id == user_id)
    )
    repo_ids = [row[0] for row in repo_result]

    if not repo_ids:
        return 0

    result = await session.execute(
        select(func.count(db.MusehubCommit.commit_id)).where(
            db.MusehubCommit.repo_id.in_(repo_ids)
        )
    )
    count = result.scalar()
    return int(count) if count is not None else 0


# ---------------------------------------------------------------------------
# Full profile assembly
# ---------------------------------------------------------------------------


async def get_full_profile(
    session: AsyncSession, username: str
) -> ProfileResponse | None:
    """Assemble a complete ProfileResponse for the given username.

    Returns None if no profile with that username exists.
    Runs three sequential aggregate queries (repos, contribution graph, credits)
    against the shared AsyncSession — concurrent access to a single session is
    unsafe with SQLAlchemy's async driver.
    """
    profile = await get_profile_by_username(session, username)
    if profile is None:
        return None

    repos, contribution_graph, session_credits = (
        await get_public_repos(session, profile.user_id),
        await get_contribution_graph(session, profile.user_id),
        await get_session_credits(session, profile.user_id),
    )

    return _to_profile_response(profile, repos, contribution_graph, session_credits)
