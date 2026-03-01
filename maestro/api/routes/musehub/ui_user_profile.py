"""Enhanced Muse Hub user profile page — issue #459, SSR migration #563.

Replaces the stub profile handler in ui.py with a full-featured profile page:
  - 52×7 GitHub-style contribution heatmap (green intensity by commit count)
  - 8 achievement badges with server-side unlock criteria
  - Up to 6 pinned repo cards (name, description, genre, stars, forks)
  - Activity feed tab with All / Commits / PRs / Issues / Stars filter

Endpoint summary:
  GET /musehub/ui/users/{username}
    HTML (default)  → fully server-rendered Jinja2 template (profile.html).
                      All data fetched from DB on the server; no JS hydration needed.
    JSON            → EnhancedProfileResponse (badges, heatmap stats, pinned repos,
                      paginated activity feed).  Agents use this for machine-readable
                      profile data without navigating a separate /api/v1/... URL.

Why a separate module:
  profile_page() in ui.py was a stub that just passed username to the template.
  The enhanced version needs DB access, several aggregate queries, and its own
  Pydantic response model — enough complexity to warrant its own file, matching
  the pattern of ui_similarity.py, ui_blame.py, etc.

SSR migration (#563):
  Deleted _render_profile_html() which built a ~310-line f-string shell.
  The HTML path now runs the same DB queries as the JSON path and passes all
  data into profile.html via the Jinja2 template context.  No client-side
  fetching is needed for the primary profile data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response as StarletteResponse

from maestro.api.routes.musehub._templates import templates
from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.db import get_db
from maestro.db import musehub_models as dbm
from maestro.models.base import CamelModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class HeatmapDay(CamelModel):
    """One day in the 52×7 contribution heatmap."""

    date: str = Field(..., description="ISO date string (YYYY-MM-DD)")
    count: int = Field(0, ge=0, description="Commit count for this day")
    intensity: int = Field(0, ge=0, le=3, description="0=none 1=light 2=medium 3=dark")


class HeatmapStats(CamelModel):
    """Aggregate stats displayed below the heatmap grid."""

    days: list[HeatmapDay]
    total_contributions: int = Field(0, ge=0)
    longest_streak: int = Field(0, ge=0, description="Longest consecutive-day streak (days)")
    current_streak: int = Field(0, ge=0, description="Current consecutive-day streak (days)")


class Badge(CamelModel):
    """An achievement badge shown on the profile page."""

    id: str
    name: str
    description: str
    icon: str = Field(..., description="Emoji or icon character representing the badge")
    earned: bool
    earned_at: datetime | None = None


class PinnedRepoCard(CamelModel):
    """Compact pinned-repo card (up to 6 per profile)."""

    repo_id: str
    name: str
    slug: str
    owner: str
    description: str
    star_count: int = Field(0, ge=0)
    fork_count: int = Field(0, ge=0)
    language: str = Field("", description="Primary language tag derived from repo tags")
    primary_genre: str | None = Field(None, description="First genre tag on the repo, if any")


class ActivityEvent(CamelModel):
    """A single entry in the user activity feed."""

    event_id: str
    event_type: str
    description: str
    repo_name: str | None = None
    repo_slug: str | None = None
    repo_owner: str | None = None
    created_at: datetime


class EnhancedProfileResponse(CamelModel):
    """Full structured response for GET /musehub/ui/users/{username}?format=json.

    Agents consume this to inspect a user's contribution history, badges, and
    recent activity without navigating the HTML profile page.

    CC attribution fields (``is_verified``, ``cc_license``) were added in
    issue #448 to surface Public Domain / Creative Commons status inline so
    the frontend can render the CC badge without a secondary API call.
    """

    username: str
    display_name: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    location: str | None = None
    website_url: str | None = None
    twitter_handle: str | None = None
    # True for Public Domain / CC licensed archive artists (e.g. bach, chopin, kevin_macleod)
    is_verified: bool = False
    # CC attribution string such as "Public Domain" or "CC BY 4.0"; None = all rights reserved
    cc_license: str | None = None
    heatmap: HeatmapStats
    badges: list[Badge]
    pinned_repos: list[PinnedRepoCard]
    activity: list[ActivityEvent]
    page: int = Field(1, ge=1)
    per_page: int = Field(20, ge=1)
    total_events: int = Field(0, ge=0)
    activity_filter: str = Field("all")


# ---------------------------------------------------------------------------
# Heatmap helpers
# ---------------------------------------------------------------------------

_HEATMAP_WEEKS = 52


def _intensity(count: int) -> int:
    """Map a commit count to a 0–3 intensity bucket."""
    if count == 0:
        return 0
    if count <= 3:
        return 1
    if count <= 6:
        return 2
    return 3


def _compute_streaks(days: list[HeatmapDay]) -> tuple[int, int]:
    """Return (longest_streak, current_streak) from an ordered list of HeatmapDay."""
    longest = 0
    current = 0
    for day in days:
        if day.count > 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest, current


async def _build_heatmap(session: AsyncSession, user_id: str) -> HeatmapStats:
    """Compute the 52-week contribution heatmap for ``user_id``."""
    today = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = today - timedelta(weeks=_HEATMAP_WEEKS)

    # All repos owned by this user (public + private)
    repo_result = await session.execute(
        select(dbm.MusehubRepo.repo_id).where(
            dbm.MusehubRepo.owner_user_id == user_id,
            dbm.MusehubRepo.deleted_at.is_(None),
        )
    )
    repo_ids = [row[0] for row in repo_result]

    counts: dict[str, int] = {}
    if repo_ids:
        daily_result = await session.execute(
            select(
                func.date(dbm.MusehubCommit.timestamp).label("day"),
                func.count(dbm.MusehubCommit.commit_id).label("cnt"),
            )
            .where(
                dbm.MusehubCommit.repo_id.in_(repo_ids),
                dbm.MusehubCommit.timestamp >= cutoff,
            )
            .group_by(func.date(dbm.MusehubCommit.timestamp))
        )
        counts = {str(row.day): int(row.cnt) for row in daily_result}

    days: list[HeatmapDay] = []
    cursor = cutoff
    while cursor <= today:
        iso = cursor.strftime("%Y-%m-%d")
        c = counts.get(iso, 0)
        days.append(HeatmapDay(date=iso, count=c, intensity=_intensity(c)))
        cursor += timedelta(days=1)

    total = sum(d.count for d in days)
    longest, current = _compute_streaks(days)
    return HeatmapStats(
        days=days,
        total_contributions=total,
        longest_streak=longest,
        current_streak=current,
    )


# ---------------------------------------------------------------------------
# Badge helpers
# ---------------------------------------------------------------------------

_BADGE_DEFS: list[tuple[str, str, str, str]] = [
    ("first_commit", "First Commit", "Made your first commit to Muse Hub", "🎵"),
    ("genre_pioneer", "Genre Pioneer", "Explored 3+ distinct tags across your repos", "🎸"),
    ("100_commits", "100 Commits", "Reached 100 cumulative commits — serious dedication", "💯"),
    ("collaborator", "Collaborator", "Contributed to 3+ repos you don't own", "🤝"),
    ("fork_architect", "Fork Architect", "Had 5+ of your repos forked by others", "🌿"),
    ("release_engineer", "Release Engineer", "Published 3+ official releases", "🚀"),
    ("community_star", "Community Star", "Received 100+ reactions across your repos", "⭐"),
    ("bach_scholar", "Bach Scholar", "Tagged a repo or commit with a Bach reference", "🎼"),
]


async def _compute_badges(
    session: AsyncSession,
    username: str,
    user_id: str,
) -> list[Badge]:
    """Derive achievement badges from DB state for a user."""

    # ── Fetch all repos owned by user ────────────────────────────────────────
    repo_result = await session.execute(
        select(dbm.MusehubRepo).where(
            dbm.MusehubRepo.owner_user_id == user_id,
            dbm.MusehubRepo.deleted_at.is_(None),
        )
    )
    user_repos = list(repo_result.scalars())
    user_repo_ids = [r.repo_id for r in user_repos]

    # ── Aggregate queries ─────────────────────────────────────────────────────

    # Total commit count across owned repos
    total_commits = 0
    if user_repo_ids:
        commit_result = await session.execute(
            select(func.count(dbm.MusehubCommit.commit_id)).where(
                dbm.MusehubCommit.repo_id.in_(user_repo_ids)
            )
        )
        total_commits = int(commit_result.scalar() or 0)

    # Distinct genres: all tags across all repos (tags are free-form, include genre/key/instrument)
    all_tags: set[str] = set()
    for repo in user_repos:
        for tag in (repo.tags or []):
            all_tags.add(tag.lower())

    # Repos this user committed to that they don't own (collaborator badge)
    collab_repos = 0
    if username:
        collab_query = select(func.count(func.distinct(dbm.MusehubCommit.repo_id))).where(
            dbm.MusehubCommit.author == username,
        )
        if user_repo_ids:
            collab_query = collab_query.where(
                dbm.MusehubCommit.repo_id.not_in(user_repo_ids)
            )
        collab_result = await session.execute(collab_query)
        collab_repos = int(collab_result.scalar() or 0)

    # Forks of user's repos
    fork_count = 0
    if user_repo_ids:
        fork_result = await session.execute(
            select(func.count(dbm.MusehubFork.fork_id)).where(
                dbm.MusehubFork.source_repo_id.in_(user_repo_ids)
            )
        )
        fork_count = int(fork_result.scalar() or 0)

    # Total releases across owned repos
    release_count = 0
    if user_repo_ids:
        release_result = await session.execute(
            select(func.count(dbm.MusehubRelease.release_id)).where(
                dbm.MusehubRelease.repo_id.in_(user_repo_ids)
            )
        )
        release_count = int(release_result.scalar() or 0)

    # Total reactions on repos owned by user
    reaction_count = 0
    if user_repo_ids:
        reaction_result = await session.execute(
            select(func.count(dbm.MusehubReaction.reaction_id)).where(
                dbm.MusehubReaction.repo_id.in_(user_repo_ids)
            )
        )
        reaction_count = int(reaction_result.scalar() or 0)

    # Bach Scholar: any repo has "bach" in its tags
    has_bach = any("bach" in tag.lower() for tag in all_tags)
    # Also check commit messages for "bach" if no tag found
    if not has_bach and user_repo_ids:
        bach_commit_result = await session.execute(
            select(func.count(dbm.MusehubCommit.commit_id)).where(
                dbm.MusehubCommit.repo_id.in_(user_repo_ids),
                func.lower(dbm.MusehubCommit.message).contains("bach"),
            )
        )
        has_bach = int(bach_commit_result.scalar() or 0) > 0

    # ── Map criteria to earned/not-earned ────────────────────────────────────
    criteria: dict[str, bool] = {
        "first_commit": total_commits >= 1,
        "genre_pioneer": len(all_tags) >= 3,
        "100_commits": total_commits >= 100,
        "collaborator": collab_repos >= 3,
        "fork_architect": fork_count >= 5,
        "release_engineer": release_count >= 3,
        "community_star": reaction_count >= 100,
        "bach_scholar": has_bach,
    }

    return [
        Badge(id=bid, name=name, description=desc, icon=icon, earned=criteria.get(bid, False))
        for bid, name, desc, icon in _BADGE_DEFS
    ]


# ---------------------------------------------------------------------------
# Pinned repo helpers
# ---------------------------------------------------------------------------

_KNOWN_GENRE_KEYWORDS = frozenset(
    {
        "jazz", "blues", "rock", "pop", "classical", "electronic", "hip-hop", "r&b",
        "soul", "funk", "reggae", "country", "folk", "metal", "punk", "indie",
        "ambient", "experimental", "neo-soul", "bossa-nova", "afrobeats",
    }
)


def _extract_genre(tags: list[str]) -> str | None:
    """Return the first genre-looking tag from a repo's tag list."""
    for tag in tags:
        if tag.lower() in _KNOWN_GENRE_KEYWORDS or "genre:" in tag.lower():
            return tag
    return None


async def _build_pinned_repos(
    session: AsyncSession,
    pinned_repo_ids: list[str],
) -> list[PinnedRepoCard]:
    """Fetch up to 6 pinned repos with star and fork counts."""
    if not pinned_repo_ids:
        return []

    repo_result = await session.execute(
        select(dbm.MusehubRepo).where(
            dbm.MusehubRepo.repo_id.in_(pinned_repo_ids),
            dbm.MusehubRepo.deleted_at.is_(None),
        )
    )
    repos = list(repo_result.scalars())

    # Preserve pinned order
    repo_map = {r.repo_id: r for r in repos}
    ordered = [repo_map[rid] for rid in pinned_repo_ids if rid in repo_map][:6]

    if not ordered:
        return []

    ordered_ids = [r.repo_id for r in ordered]

    # Star counts
    star_result = await session.execute(
        select(
            dbm.MusehubStar.repo_id,
            func.count(dbm.MusehubStar.star_id).label("cnt"),
        )
        .where(dbm.MusehubStar.repo_id.in_(ordered_ids))
        .group_by(dbm.MusehubStar.repo_id)
    )
    star_counts = {row.repo_id: int(row.cnt) for row in star_result}

    # Fork counts (repos forked FROM these repos)
    fork_result = await session.execute(
        select(
            dbm.MusehubFork.source_repo_id,
            func.count(dbm.MusehubFork.fork_id).label("cnt"),
        )
        .where(dbm.MusehubFork.source_repo_id.in_(ordered_ids))
        .group_by(dbm.MusehubFork.source_repo_id)
    )
    fork_counts = {row.source_repo_id: int(row.cnt) for row in fork_result}

    return [
        PinnedRepoCard(
            repo_id=r.repo_id,
            name=r.name,
            slug=r.slug,
            owner=r.owner,
            description=r.description or "",
            star_count=star_counts.get(r.repo_id, 0),
            fork_count=fork_counts.get(r.repo_id, 0),
            language=next((t for t in (r.tags or []) if "lang:" in t.lower()), ""),
            primary_genre=_extract_genre(r.tags or []),
        )
        for r in ordered
    ]


# ---------------------------------------------------------------------------
# Activity feed helpers
# ---------------------------------------------------------------------------

_ACTIVITY_EVENT_TYPES = frozenset(
    {
        "commit_pushed", "pr_opened", "pr_merged", "pr_closed",
        "issue_opened", "issue_closed", "branch_created", "branch_deleted",
        "tag_pushed", "session_started", "session_ended",
    }
)

# Maps filter tab → set of event_type values to include
_FILTER_MAP: dict[str, frozenset[str]] = {
    "all": _ACTIVITY_EVENT_TYPES,
    "commits": frozenset({"commit_pushed"}),
    "prs": frozenset({"pr_opened", "pr_merged", "pr_closed"}),
    "issues": frozenset({"issue_opened", "issue_closed"}),
    "stars": frozenset({"tag_pushed"}),  # re-using tag_pushed; stars are tracked separately
}


async def _build_activity(
    session: AsyncSession,
    user_id: str,
    username: str,
    activity_filter: str,
    page: int,
    per_page: int,
) -> tuple[list[ActivityEvent], int]:
    """Fetch paginated activity events for repos owned by ``user_id``."""

    repo_result = await session.execute(
        select(dbm.MusehubRepo.repo_id, dbm.MusehubRepo.name, dbm.MusehubRepo.slug, dbm.MusehubRepo.owner).where(
            dbm.MusehubRepo.owner_user_id == user_id,
            dbm.MusehubRepo.deleted_at.is_(None),
        )
    )
    repo_rows = list(repo_result)
    repo_ids = [row[0] for row in repo_rows]
    repo_meta: dict[str, tuple[str, str, str]] = {
        row[0]: (row[1], row[2], row[3]) for row in repo_rows
    }

    if not repo_ids:
        return [], 0

    allowed_types = _FILTER_MAP.get(activity_filter, _ACTIVITY_EVENT_TYPES)

    base_filter = [
        dbm.MusehubEvent.repo_id.in_(repo_ids),
        dbm.MusehubEvent.event_type.in_(list(allowed_types)),
    ]

    total_result = await session.execute(
        select(func.count(dbm.MusehubEvent.event_id)).where(*base_filter)
    )
    total = int(total_result.scalar() or 0)

    offset = (page - 1) * per_page
    events_result = await session.execute(
        select(dbm.MusehubEvent)
        .where(*base_filter)
        .order_by(desc(dbm.MusehubEvent.created_at))
        .limit(per_page)
        .offset(offset)
    )
    events = list(events_result.scalars())

    activity: list[ActivityEvent] = []
    for ev in events:
        meta = repo_meta.get(ev.repo_id)
        activity.append(
            ActivityEvent(
                event_id=ev.event_id,
                event_type=ev.event_type,
                description=ev.description,
                repo_name=meta[0] if meta else None,
                repo_slug=meta[1] if meta else None,
                repo_owner=meta[2] if meta else None,
                created_at=ev.created_at,
            )
        )

    return activity, total


# ---------------------------------------------------------------------------
# Full JSON payload assembly
# ---------------------------------------------------------------------------


async def _build_enhanced_profile(
    session: AsyncSession,
    username: str,
    activity_filter: str,
    page: int,
    per_page: int,
) -> EnhancedProfileResponse:
    """Assemble the full EnhancedProfileResponse from DB.

    Raises HTTP 404 when the username has no registered profile.
    """
    from maestro.services import musehub_profile as profile_svc  # local import avoids circular

    profile = await profile_svc.get_profile_by_username(session, username)
    if profile is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No profile found for username '{username}'",
        )

    heatmap, badges, pinned_repos, (activity, total_events) = (
        await _build_heatmap(session, profile.user_id),
        await _compute_badges(session, username, profile.user_id),
        await _build_pinned_repos(session, list(profile.pinned_repo_ids or [])),
        await _build_activity(session, profile.user_id, username, activity_filter, page, per_page),
    )

    return EnhancedProfileResponse(
        username=profile.username,
        display_name=profile.display_name,
        bio=profile.bio,
        avatar_url=profile.avatar_url,
        location=profile.location,
        website_url=profile.website_url,
        twitter_handle=profile.twitter_handle,
        is_verified=profile.is_verified,
        cc_license=profile.cc_license,
        heatmap=heatmap,
        badges=badges,
        pinned_repos=pinned_repos,
        activity=activity,
        page=page,
        per_page=per_page,
        total_events=total_events,
        activity_filter=activity_filter,
    )


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.get(
    "/users/{username}",
    summary="Enhanced Muse Hub user profile — heatmap, badges, pinned repos, activity feed",
)
async def profile_page(
    request: Request,
    username: str,
    format: str | None = Query(None, description="Set to 'json' for structured data"),
    tab: str = Query("all", description="Activity filter: all | commits | prs | issues | stars"),
    page: int = Query(1, ge=1, description="Activity page (1-indexed)"),
    per_page: int = Query(20, ge=1, le=100, description="Activity events per page"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the enhanced Muse Hub user profile page or return structured JSON.

    Both HTML and JSON paths run the same DB queries — no JS hydration required
    for the primary profile data.  The Jinja2 template (profile.html) renders
    all data server-side.

    HTML (default):
        Returns a fully server-rendered Jinja2 page with heatmap cells, badge
        cards, pinned repos, and activity feed all embedded in the HTML at
        request time.

    JSON (``Accept: application/json`` or ``?format=json``):
        Returns ``EnhancedProfileResponse`` with:
        - ``heatmap`` — 52×7 contribution grid with per-day commit counts and
          intensity buckets (0=none, 1=light, 2=medium, 3=dark) plus streak stats.
        - ``badges`` — 8 achievement badges with ``earned`` flag.
        - ``pinnedRepos`` — up to 6 pinned repo cards with star/fork counts,
          primary genre, and description.
        - ``activity`` — paginated activity feed filtered by ``?tab``.

    No JWT required — profile pages are publicly accessible.
    Returns 404 when the username has no registered Muse Hub profile.
    """
    data = await _build_enhanced_profile(db, username, tab, page, per_page)

    # Build a 52-column (week-major) grid for the heatmap template
    weeks: list[list[HeatmapDay]] = []
    week: list[HeatmapDay] = []
    for day in data.heatmap.days:
        week.append(day)
        if len(week) == 7:
            weeks.append(week)
            week = []
    if week:
        weeks.append(week)

    context = {
        "username": username,
        "profile": data,
        "heatmap_weeks": weeks,
        "heatmap_stats": data.heatmap,
        "badges": data.badges,
        "pinned_repos": data.pinned_repos,
        "activity": data.activity,
        "activity_filter": data.activity_filter,
        "page": data.page,
        "per_page": data.per_page,
        "total_events": data.total_events,
    }

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/profile.html",
        context=context,
        templates=templates,
        json_data=data,
        format_param=format,
    )
