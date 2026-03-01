"""Enhanced Muse Hub user profile page — issue #459.

Replaces the stub profile handler in ui.py with a full-featured profile page:
  - 52×7 GitHub-style contribution heatmap (green intensity by commit count)
  - 8 achievement badges with server-side unlock criteria
  - Up to 6 pinned repo cards (name, description, genre, stars, forks)
  - Activity feed tab with All / Commits / PRs / Issues / Stars filter

Endpoint summary:
  GET /musehub/ui/users/{username}
    HTML (default)  → rich profile shell; JavaScript hydrates from ?format=json.
    JSON            → EnhancedProfileResponse (badges, heatmap stats, pinned repos,
                      paginated activity feed).  Agents use this for machine-readable
                      profile data without navigating a separate /api/v1/... URL.

Why a separate module:
  profile_page() in ui.py was a stub that just passed username to the template.
  The enhanced version needs DB access, several aggregate queries, and its own
  Pydantic response model — enough complexity to warrant its own file, matching
  the pattern of ui_similarity.py, ui_blame.py, etc.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response as StarletteResponse

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
    """

    username: str
    bio: str | None = None
    avatar_url: str | None = None
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
        bio=profile.bio,
        avatar_url=profile.avatar_url,
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
# Inline HTML shell (rendered for human browsers)
# ---------------------------------------------------------------------------

_EVENT_ICONS: dict[str, str] = {
    "commit_pushed": "📦",
    "pr_opened": "🔀",
    "pr_merged": "✅",
    "pr_closed": "❌",
    "issue_opened": "🐛",
    "issue_closed": "✔️",
    "branch_created": "🌿",
    "branch_deleted": "🗑️",
    "tag_pushed": "🏷️",
    "session_started": "▶️",
    "session_ended": "⏹️",
}


def _render_profile_html(username: str) -> str:
    """Generate the self-contained HTML shell for the enhanced profile page.

    All data is fetched client-side:
    - Profile/heatmap/repos from ``GET /api/v1/musehub/users/{username}``
    - Badges, pinned repos, activity from ``GET /musehub/ui/users/{username}?format=json``

    The page is intentionally a shell so it loads instantly and stays
    auth-agnostic — the JavaScript handles token refresh and 401s.
    """
    safe_username = username.replace("'", "\\'").replace('"', '\\"')
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="/musehub/static/tokens.css">
  <link rel="stylesheet" href="/musehub/static/components.css">
  <link rel="stylesheet" href="/musehub/static/layout.css">
  <title>@{username} — Muse Hub</title>
  <style>
    .heatmap-grid {{display:flex;gap:2px;overflow-x:auto;padding-bottom:4px}}
    .heatmap-col {{display:flex;flex-direction:column;gap:2px}}
    .heatmap-cell {{width:12px;height:12px;border-radius:2px;cursor:help}}
    .heatmap-cell[data-intensity="0"] {{background:#161b22}}
    .heatmap-cell[data-intensity="1"] {{background:#0e4429}}
    .heatmap-cell[data-intensity="2"] {{background:#006d32}}
    .heatmap-cell[data-intensity="3"] {{background:#39d353}}
    .badge-grid {{display:flex;flex-wrap:wrap;gap:12px}}
    .badge-card {{
      display:flex;align-items:center;gap:10px;padding:10px 14px;
      border-radius:6px;border:1px solid var(--border,#30363d);
      background:var(--canvas-subtle,#161b22);min-width:200px;flex:1
    }}
    .badge-card.earned {{border-color:#39d353;background:#0e4429}}
    .badge-card.unearned {{opacity:.45}}
    .badge-icon {{font-size:24px;line-height:1}}
    .badge-info .badge-name {{font-weight:600;font-size:14px}}
    .badge-info .badge-desc {{font-size:12px;color:#8b949e;margin-top:2px}}
    .pinned-grid {{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}}
    .pinned-card {{
      padding:12px;border:1px solid var(--border,#30363d);border-radius:6px;
      background:var(--canvas-subtle,#161b22)
    }}
    .pinned-card h3 {{margin:0 0 6px;font-size:14px}}
    .pinned-card .pinned-desc {{font-size:12px;color:#8b949e;margin:0 0 8px}}
    .pinned-meta {{display:flex;gap:12px;font-size:12px;color:#8b949e}}
    .tab-bar {{display:flex;border-bottom:1px solid var(--border,#30363d);gap:0;margin-bottom:16px}}
    .tab-btn {{
      padding:8px 16px;border:none;background:none;cursor:pointer;
      color:#8b949e;border-bottom:2px solid transparent;font-size:14px
    }}
    .tab-btn.active {{color:#e6edf3;border-bottom-color:#58a6ff;font-weight:600}}
    .activity-row {{
      display:flex;align-items:flex-start;gap:10px;padding:8px 0;
      border-bottom:1px solid var(--border,#21262d);font-size:13px
    }}
    .activity-icon {{font-size:16px;line-height:1.4;flex-shrink:0}}
    .activity-desc {{flex:1}}
    .activity-meta {{color:#8b949e;font-size:11px;white-space:nowrap}}
    .profile-hdr {{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap}}
    .avatar-lg {{
      width:72px;height:72px;border-radius:50%;display:flex;
      align-items:center;justify-content:center;font-size:32px;
      background:#1f6feb;flex-shrink:0;overflow:hidden
    }}
    .avatar-lg img {{width:100%;height:100%;object-fit:cover}}
  </style>
</head>
<body>
  <header>
    <span class="logo">&#127925; Muse Hub</span>
    <span class="breadcrumb">
      <a href="/musehub/ui/users/{username}">@{username}</a>
    </span>
  </header>
  <div class="container">
    <div id="profile-hdr" class="card"><p class="loading">Loading profile…</p></div>
    <div id="pinned-section"></div>
    <div id="badges-section"></div>
    <div id="heatmap-section"></div>
    <div id="tabs-section" class="card" style="display:none">
      <div class="tab-bar">
        <button class="tab-btn active" data-tab="repos" onclick="switchTab('repos')">Repos</button>
        <button class="tab-btn" data-tab="stars" onclick="switchTab('stars')">Stars</button>
        <button class="tab-btn" data-tab="followers" onclick="switchTab('followers')">Followers</button>
        <button class="tab-btn" data-tab="activity" onclick="switchTab('activity')">Activity</button>
      </div>
      <div id="tab-content"><p class="loading">Loading…</p></div>
    </div>
  </div>

  <script src="/musehub/static/musehub.js"></script>
  <script>
  (function() {{
    const username = '{safe_username}';
    const API_PROFILE = '/api/v1/musehub/users/' + username;
    const API_ENHANCED = '/musehub/ui/users/' + username + '?format=json';
    const API_STARRED  = '/api/v1/musehub/users/' + username + '/starred';
    const API_FOLLOWERS = '/api/v1/musehub/users/' + username + '/followers-list';
    const API_FOLLOWING = '/api/v1/musehub/users/' + username + '/following-list';

    let currentTab = 'repos';
    let activityFilter = 'all';
    let activityPage = 1;
    let totalActivityEvents = 0;
    let cachedRepos = [];

    function esc(s) {{
      if (!s) return '';
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }}

    function fmtRelative(ts) {{
      if (!ts) return '';
      const d = new Date(ts);
      const diff = Math.floor((Date.now() - d.getTime()) / 1000);
      if (diff < 60) return 'just now';
      if (diff < 3600) return Math.floor(diff/60) + 'm ago';
      if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
      return Math.floor(diff/86400) + 'd ago';
    }}

    // ── Heatmap ──────────────────────────────────────────────────────────────
    function renderHeatmap(stats) {{
      const days = stats.days || [];
      // Group into columns of 7 (week columns)
      const cols = [];
      let col = [];
      for (let i = 0; i < days.length; i++) {{
        col.push(days[i]);
        if (col.length === 7) {{ cols.push(col); col = []; }}
      }}
      if (col.length) cols.push(col);

      const colsHtml = cols.map(c => {{
        const cells = c.map(d =>
          `<div class="heatmap-cell" data-intensity="${{d.intensity}}"
               title="${{esc(d.date)}}: ${{d.count}} commit${{d.count !== 1 ? 's' : ''}}"></div>`
        ).join('');
        return `<div class="heatmap-col">${{cells}}</div>`;
      }}).join('');

      const legend = [0,1,2,3].map(n =>
        `<div class="heatmap-cell" data-intensity="${{n}}" style="display:inline-block"></div>`
      ).join('');

      document.getElementById('heatmap-section').innerHTML = `
        <div class="card">
          <h2 style="margin-bottom:12px">📈 Contribution Activity</h2>
          <div class="heatmap-grid">${{colsHtml}}</div>
          <div style="display:flex;align-items:center;gap:8px;margin-top:8px;font-size:12px;color:#8b949e">
            Less ${{legend}} More
            &nbsp;·&nbsp; ${{stats.totalContributions || 0}} contributions in the last year
            &nbsp;·&nbsp; Longest streak: ${{stats.longestStreak || 0}} days
            &nbsp;·&nbsp; Current streak: ${{stats.currentStreak || 0}} days
          </div>
        </div>`;
    }}

    // ── Badges ───────────────────────────────────────────────────────────────
    function renderBadges(badges) {{
      const cards = badges.map(b => {{
        const cls = b.earned ? 'earned' : 'unearned';
        return `<div class="badge-card ${{cls}}" title="${{esc(b.description)}}">
          <div class="badge-icon">${{esc(b.icon)}}</div>
          <div class="badge-info">
            <div class="badge-name">${{esc(b.name)}}</div>
            <div class="badge-desc">${{esc(b.description)}}</div>
          </div>
        </div>`;
      }}).join('');

      const earned = badges.filter(b => b.earned).length;
      document.getElementById('badges-section').innerHTML = `
        <div class="card">
          <h2 style="margin-bottom:12px">🏆 Achievements (${{earned}}/${{badges.length}})</h2>
          <div class="badge-grid">${{cards}}</div>
        </div>`;
    }}

    // ── Pinned repos ─────────────────────────────────────────────────────────
    function renderPinned(pinnedRepos, isOwner) {{
      if (!pinnedRepos || pinnedRepos.length === 0) return;
      const cards = pinnedRepos.map(r => {{
        const genre = r.primaryGenre ? `<span>🎵 ${{esc(r.primaryGenre)}}</span>` : '';
        const lang  = r.language     ? `<span>🔤 ${{esc(r.language)}}</span>`     : '';
        return `<div class="pinned-card">
          <h3><a href="/musehub/ui/${{esc(r.owner)}}/${{esc(r.slug)}}">${{esc(r.name)}}</a></h3>
          ${{r.description ? `<p class="pinned-desc">${{esc(r.description)}}</p>` : ''}}
          <div class="pinned-meta">
            ${{genre}} ${{lang}}
            <span>⭐ ${{r.starCount}}</span>
            <span>🌿 ${{r.forkCount}}</span>
          </div>
        </div>`;
      }}).join('');

      const customizeBtn = isOwner
        ? `<button class="btn btn-secondary" style="float:right;font-size:12px"
             onclick="alert('Customize pins: drag and drop reordering coming soon!')">
             Customize pins</button>`
        : '';

      document.getElementById('pinned-section').innerHTML = `
        <div class="card">
          <h2 style="margin-bottom:12px">📌 Pinned Repositories ${{customizeBtn}}</h2>
          <div class="pinned-grid">${{cards}}</div>
        </div>`;
    }}

    // ── Profile header ───────────────────────────────────────────────────────
    function renderProfileHeader(profile) {{
      const hue = username.split('').reduce((a,c) => a + c.charCodeAt(0), 0) % 360;
      const initial = (username[0] || '?').toUpperCase();
      const avatarInner = profile.avatarUrl
        ? `<img src="${{esc(profile.avatarUrl)}}" alt="avatar">`
        : `<span style="color:#fff;font-weight:700">${{initial}}</span>`;
      const avatarStyle = profile.avatarUrl ? '' : `background:hsl(${{hue}},55%,40%)`;
      const myUsername = getToken ? (() => {{ try {{ const t=getToken(); if(!t) return ''; const p=JSON.parse(atob(t.split('.')[1])); return p.sub||p.username||''; }} catch(e){{return '';}} }})() : '';
      const isOwner = myUsername === username;

      document.getElementById('profile-hdr').innerHTML = `
        <div class="profile-hdr">
          <div class="avatar-lg" style="${{avatarStyle}}">${{avatarInner}}</div>
          <div style="flex:1">
            <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
              <h1 style="margin:0;font-size:22px">@${{esc(profile.username)}}</h1>
              ${{!isOwner ? `<button class="btn btn-secondary" id="follow-btn"
                  onclick="toggleFollow()" style="font-size:13px">Follow</button>` : ''}}
            </div>
            ${{profile.bio ? `<p style="margin:6px 0;color:#8b949e">${{esc(profile.bio)}}</p>` : ''}}
            <div style="font-size:13px;color:#8b949e;margin-top:4px" id="follow-stats"></div>
          </div>
        </div>`;

      return isOwner;
    }}

    // ── Follow state ─────────────────────────────────────────────────────────
    async function loadFollowState() {{
      try {{
        const r = await fetch('/api/v1/musehub/users/' + username + '/followers', {{ headers: authHeaders() }});
        if (!r.ok) return;
        const data = await r.json();
        const el = document.getElementById('follow-stats');
        if (el) el.textContent = (data.follower_count||0) + ' followers · ' + (data.following_count||0) + ' following';
        const btn = document.getElementById('follow-btn');
        if (btn && getToken && getToken()) {{
          btn.dataset.following = data.following ? 'true' : 'false';
          btn.textContent = data.following ? 'Following' : 'Follow';
        }}
      }} catch(_) {{}}
    }}

    async function toggleFollow() {{
      const btn = document.getElementById('follow-btn');
      if (!btn) return;
      const isFollowing = btn.dataset.following === 'true';
      const method = isFollowing ? 'DELETE' : 'POST';
      try {{
        await fetch('/api/v1/musehub/users/' + username + '/follow', {{ method, headers: authHeaders() }});
        btn.dataset.following = isFollowing ? 'false' : 'true';
        btn.textContent = isFollowing ? 'Follow' : 'Following';
      }} catch(_) {{}}
    }}

    // ── Tab: Repos ───────────────────────────────────────────────────────────
    function renderReposTab(repos) {{
      if (!repos || repos.length === 0) {{
        document.getElementById('tab-content').innerHTML = '<p class="loading">No public repositories yet.</p>';
        return;
      }}
      const cards = repos.map(r => {{
        const lastAct = r.lastActivityAt ? fmtRelative(r.lastActivityAt) : 'no commits';
        return `<div class="card" style="margin-bottom:8px">
          <h3 style="margin:0 0 4px"><a href="/musehub/ui/${{esc(r.owner)}}/${{esc(r.slug)}}">${{esc(r.name)}}</a></h3>
          <div style="font-size:12px;color:#8b949e">
            <span class="badge badge-${{r.visibility}}">${{r.visibility}}</span>
            &nbsp;· Last activity: ${{lastAct}}
          </div>
        </div>`;
      }}).join('');
      document.getElementById('tab-content').innerHTML = `<div>${{cards}}</div>`;
    }}

    // ── Tab: Stars ───────────────────────────────────────────────────────────
    async function loadStarsTab() {{
      document.getElementById('tab-content').innerHTML = '<p class="loading">Loading starred repos…</p>';
      try {{
        const data = await fetch(API_STARRED).then(r => r.json());
        const starred = data.starred || [];
        if (!starred.length) {{
          document.getElementById('tab-content').innerHTML = '<p class="loading">No starred repositories yet.</p>';
          return;
        }}
        const cards = starred.map(entry => {{
          const r = entry.repo || entry;
          return `<div class="card" style="margin-bottom:8px">
            <h3 style="margin:0 0 4px"><a href="/musehub/ui/${{esc(r.owner)}}/${{esc(r.slug)}}">${{esc(r.owner)}}/${{esc(r.name)}}</a></h3>
            ${{r.description ? `<p style="font-size:12px;color:#8b949e;margin:4px 0">${{esc(r.description)}}</p>` : ''}}
          </div>`;
        }}).join('');
        document.getElementById('tab-content').innerHTML = cards;
      }} catch(e) {{
        document.getElementById('tab-content').innerHTML = '<p class="error">Failed to load starred repos.</p>';
      }}
    }}

    // ── Tab: Followers/Following ─────────────────────────────────────────────
    let socialSubTab = 'followers';
    async function loadSocialTab(sub) {{
      socialSubTab = sub;
      const url = sub === 'followers' ? API_FOLLOWERS : API_FOLLOWING;
      document.getElementById('tab-content').innerHTML = `
        <div style="display:flex;gap:8px;margin-bottom:12px">
          <button class="btn ${{sub==='followers'?'btn-primary':'btn-secondary'}}" onclick="loadSocialTab('followers')">Followers</button>
          <button class="btn ${{sub==='following'?'btn-primary':'btn-secondary'}}" onclick="loadSocialTab('following')">Following</button>
        </div>
        <div id="social-list"><p class="loading">Loading…</p></div>`;
      try {{
        const users = await fetch(url).then(r => r.json());
        const list = Array.isArray(users) ? users : (users.users || []);
        const el = document.getElementById('social-list');
        if (!el) return;
        if (!list.length) {{ el.innerHTML = `<p class="loading">No ${{sub}} yet.</p>`; return; }}
        el.innerHTML = list.map(u => `
          <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border,#21262d)">
            <a href="/musehub/ui/users/${{esc(u.username)}}" style="font-weight:600">@${{esc(u.username)}}</a>
            ${{u.bio ? `<span style="color:#8b949e;font-size:12px">${{esc(u.bio.slice(0,60))}}</span>` : ''}}
          </div>`).join('');
      }} catch(e) {{
        const el = document.getElementById('social-list');
        if (el) el.innerHTML = '<p class="error">Failed to load.</p>';
      }}
    }}

    // ── Tab: Activity ────────────────────────────────────────────────────────
    const FILTER_LABELS = {{all:'All',commits:'Commits',prs:'PRs',issues:'Issues',stars:'Stars'}};
    const EVENT_ICONS = {{
      commit_pushed:'📦',pr_opened:'🔀',pr_merged:'✅',pr_closed:'❌',
      issue_opened:'🐛',issue_closed:'✔️',branch_created:'🌿',branch_deleted:'🗑️',
      tag_pushed:'🏷️',session_started:'▶️',session_ended:'⏹️'
    }};

    async function loadActivityTab(filter, page) {{
      activityFilter = filter || activityFilter;
      activityPage   = page   || 1;

      const filterBtns = Object.keys(FILTER_LABELS).map(f => {{
        const active = f === activityFilter ? 'btn-primary' : 'btn-secondary';
        return `<button class="btn ${{active}}" style="font-size:12px"
          onclick="loadActivityTab('${{f}}',1)">${{FILTER_LABELS[f]}}</button>`;
      }}).join('');

      const url = API_ENHANCED + '&tab=' + activityFilter + '&page=' + activityPage + '&per_page=20';
      document.getElementById('tab-content').innerHTML = `
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px">${{filterBtns}}</div>
        <div id="activity-list"><p class="loading">Loading activity…</p></div>`;

      try {{
        const data = await fetch(url).then(r => r.json());
        totalActivityEvents = data.totalEvents || 0;
        const events = data.activity || [];
        const listEl = document.getElementById('activity-list');
        if (!listEl) return;
        if (!events.length) {{
          listEl.innerHTML = '<p class="loading">No activity found.</p>';
          return;
        }}
        const rows = events.map(ev => {{
          const icon = EVENT_ICONS[ev.eventType] || '📌';
          const repoLink = ev.repoOwner && ev.repoSlug
            ? ` in <a href="/musehub/ui/${{esc(ev.repoOwner)}}/${{esc(ev.repoSlug)}}">${{esc(ev.repoName||ev.repoSlug)}}</a>`
            : '';
          return `<div class="activity-row">
            <div class="activity-icon">${{icon}}</div>
            <div class="activity-desc">${{esc(ev.description)}}${{repoLink}}</div>
            <div class="activity-meta">${{fmtRelative(ev.createdAt)}}</div>
          </div>`;
        }}).join('');

        const totalPages = Math.ceil(totalActivityEvents / 20);
        const pageBtns = totalPages > 1 ? `
          <div style="display:flex;gap:8px;margin-top:12px;justify-content:center">
            ${{activityPage > 1 ? `<button class="btn btn-secondary" onclick="loadActivityTab(activityFilter,${{activityPage-1}})">&larr; Prev</button>` : ''}}
            <span style="font-size:13px;color:#8b949e;padding:6px">Page ${{activityPage}} of ${{totalPages}}</span>
            ${{activityPage < totalPages ? `<button class="btn btn-secondary" onclick="loadActivityTab(activityFilter,${{activityPage+1}})">Next &rarr;</button>` : ''}}
          </div>` : '';

        listEl.innerHTML = rows + pageBtns;
      }} catch(e) {{
        const el = document.getElementById('activity-list');
        if (el) el.innerHTML = '<p class="error">Failed to load activity: ' + esc(e.message) + '</p>';
      }}
    }}

    // ── Tab switcher ─────────────────────────────────────────────────────────
    function switchTab(tab) {{
      currentTab = tab;
      document.querySelectorAll('.tab-btn').forEach(b => {{
        b.classList.toggle('active', b.dataset.tab === tab);
      }});
      switch (tab) {{
        case 'repos':     renderReposTab(cachedRepos); break;
        case 'stars':     loadStarsTab(); break;
        case 'followers': loadSocialTab('followers'); break;
        case 'activity':  loadActivityTab('all', 1); break;
      }}
    }}

    // ── Main loader ──────────────────────────────────────────────────────────
    async function init() {{
      try {{
        // Fetch both in parallel
        const [profileData, enhancedData] = await Promise.all([
          fetch(API_PROFILE).then(r => {{ if (!r.ok) throw new Error(r.status); return r.json(); }}),
          fetch(API_ENHANCED).then(r => {{ if (!r.ok) throw new Error(r.status); return r.json(); }}),
        ]);

        const isOwner = renderProfileHeader(profileData);
        loadFollowState();

        // Heatmap uses profileData.contributionGraph if available, else enhancedData.heatmap
        const heatmapStats = enhancedData.heatmap || {{
          days: (profileData.contributionGraph || []).map(d => ({{...d, intensity: d.count===0?0:d.count<=3?1:d.count<=6?2:3}})),
          totalContributions: 0, longestStreak: 0, currentStreak: 0
        }};
        renderHeatmap(heatmapStats);
        renderBadges(enhancedData.badges || []);
        renderPinned(enhancedData.pinnedRepos || [], isOwner);

        // Cache repos for the Repos tab
        cachedRepos = profileData.repos || [];
        document.getElementById('tabs-section').style.display = '';
        renderReposTab(cachedRepos);
      }} catch(e) {{
        document.getElementById('profile-hdr').innerHTML =
          `<p class="error">&#10005; Could not load profile for @${{esc(username)}}: ${{esc(e.message)}}</p>`;
      }}
    }}

    init();
  }})();
  </script>
</body>
</html>"""
    return html


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

    HTML (default):
        Returns a self-contained HTML shell.  Client-side JavaScript fetches
        profile data, contribution heatmap, badges, pinned repos, and activity
        from ``/api/v1/musehub/users/{username}`` and from the ``?format=json``
        alternate of this same URL.

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
    # Determine if the caller wants JSON
    accept = request.headers.get("accept", "")
    wants_json = format == "json" or "application/json" in accept

    if wants_json:
        data = await _build_enhanced_profile(db, username, tab, page, per_page)
        return JSONResponse(content=data.model_dump(by_alias=True, mode="json"))

    # HTML path — lightweight shell, no DB access needed
    return HTMLResponse(content=_render_profile_html(username))
