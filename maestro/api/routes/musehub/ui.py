"""Muse Hub web UI route handlers.

Serves browser-readable HTML pages for navigating a Muse Hub repo --
analogous to GitHub's repository browser but for music projects.

All pages are rendered via Jinja2 templates stored in
``maestro/templates/musehub/``.  Route handlers resolve server-side data
(repo_id, owner, slug) and pass a minimal context dict to the template
engine; all HTML, CSS, and JavaScript lives in the template files, not here.

Endpoint summary (fixed-path):
  GET /musehub/ui/search                                  -- global cross-repo search page
  GET /musehub/ui/explore                                 -- public repo discovery grid
  GET /musehub/ui/trending                                -- repos sorted by stars
  GET /musehub/ui/users/{username}                        -- public user profile

Endpoint summary (repo-scoped):
  GET /musehub/ui/{owner}/{repo_slug}                           -- repo landing page
  GET /musehub/ui/{owner}/{repo_slug}/commits                   -- paginated commit list with branch filter
  GET /musehub/ui/{owner}/{repo_slug}/commits/{commit_id}       -- commit detail + artifacts
  GET /musehub/ui/{owner}/{repo_slug}/commits/{commit_id}/diff  -- musical diff view
  GET /musehub/ui/{owner}/{repo_slug}/graph                     -- interactive DAG commit graph
  GET /musehub/ui/{owner}/{repo_slug}/pulls                     -- pull request list
  GET /musehub/ui/{owner}/{repo_slug}/pulls/{pr_id}             -- PR detail with musical diff (radar, piano roll, audio A/B)
  GET /musehub/ui/{owner}/{repo_slug}/issues                    -- issue list
  GET /musehub/ui/{owner}/{repo_slug}/issues/{number}           -- issue detail + close button
  GET /musehub/ui/{owner}/{repo_slug}/context/{ref}             -- AI context viewer
  GET /musehub/ui/{owner}/{repo_slug}/credits                   -- dynamic credits (liner notes)
  GET /musehub/ui/{owner}/{repo_slug}/embed/{ref}               -- iframe-safe audio player
  GET /musehub/ui/{owner}/{repo_slug}/search                    -- in-repo search (4 modes)
  GET /musehub/ui/{owner}/{repo_slug}/compare/{base}...{head}   -- multi-dimensional musical diff between two refs
  GET /musehub/ui/{owner}/{repo_slug}/divergence                -- branch divergence radar chart
  GET /musehub/ui/{owner}/{repo_slug}/timeline                  -- chronological SVG timeline
  GET /musehub/ui/{owner}/{repo_slug}/releases                  -- release list
  GET /musehub/ui/{owner}/{repo_slug}/releases/{tag}            -- release detail + downloads
  GET /musehub/ui/{owner}/{repo_slug}/sessions                  -- recording session log
  GET /musehub/ui/{owner}/{repo_slug}/sessions/{id}             -- session detail
  GET /musehub/ui/{owner}/{repo_slug}/insights                  -- repo insights dashboard
  GET /musehub/ui/{owner}/{repo_slug}/tree/{ref}                -- file tree browser (repo root)
  GET /musehub/ui/{owner}/{repo_slug}/tree/{ref}/{path}         -- file tree browser (subdirectory)
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}            -- analysis dashboard (all 10 dimensions at a glance)
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/contour    -- melodic contour analysis
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/tempo      -- tempo analysis
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/dynamics   -- dynamics analysis
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/key        -- key detection analysis
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/meter      -- metric analysis
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/chord-map  -- chord map analysis
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/groove     -- rhythmic groove analysis
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/emotion    -- emotion analysis
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/form       -- formal structure analysis
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/motifs     -- motif browser (recurring patterns, transformations)
  GET /musehub/ui/{owner}/{repo_slug}/listen/{ref}              -- full-mix and per-track audio playback with track listing
  GET /musehub/ui/{owner}/{repo_slug}/listen/{ref}/{path}       -- single-stem playback page
  GET /musehub/ui/{owner}/{repo_slug}/listen/{ref}             -- Wavesurfer.js audio player (full mix)
  GET /musehub/ui/{owner}/{repo_slug}/listen/{ref}/{path}      -- Wavesurfer.js audio player (single track)
  GET /musehub/ui/{owner}/{repo_slug}/arrange/{ref}             -- arrangement matrix (instrument × section density grid)
  GET /musehub/ui/{owner}/{repo_slug}/piano-roll/{ref}          -- interactive piano roll (all tracks)
  GET /musehub/ui/{owner}/{repo_slug}/piano-roll/{ref}/{path}   -- interactive piano roll (single MIDI file)
  GET /musehub/ui/{owner}/{repo_slug}/activity                  -- repo-level event stream (commits, PRs, issues, branches, tags, sessions)

These routes require NO JWT auth -- they return HTML shells whose embedded
JavaScript fetches data from the authed JSON API (``/api/v1/musehub/...``)
using a token stored in ``localStorage``.

The embed route sets ``X-Frame-Options: ALLOWALL`` for cross-origin iframe use.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response as StarletteResponse

from maestro.api.routes.musehub.htmx_helpers import htmx_fragment_or_full, htmx_trigger, is_htmx
from maestro.api.routes.musehub.jinja2_filters import register_musehub_filters
from maestro.api.routes.musehub.json_alternate import json_or_html
from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.api.routes.musehub.ui_jsonld import jsonld_release, jsonld_repo, render_jsonld_script
from maestro.db import get_db
from maestro.models.musehub import CommitListResponse, CommitResponse, RepoResponse, TrackListingResponse
from maestro.models.musehub import (
    BranchDetailListResponse,
    CommitListResponse,
    CommitResponse,
    PRDiffResponse,
    RepoResponse,
    TagListResponse,
    TagResponse,
)
from maestro.db import musehub_models as musehub_db
from maestro.muse_cli.models import MuseCliTag
from maestro.services import musehub_divergence, musehub_listen, musehub_pull_requests, musehub_releases
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])

# Fixed-path routes registered BEFORE the /{owner}/{repo_slug} wildcard to
# prevent /explore, /trending, and /users/* from being shadowed.
fixed_router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
register_musehub_filters(templates.env)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_url(owner: str, repo_slug: str) -> str:
    """Return the canonical UI base URL for a repo."""
    return f"/musehub/ui/{owner}/{repo_slug}"


def _breadcrumbs(*segments: tuple[str, str]) -> list[dict[str, str]]:
    """Build breadcrumb_data list from (label, url) pairs.

    Each dict has ``label`` (display text) and ``url`` (link target).
    Pass an empty string for ``url`` to render the segment as plain text
    (used for the leaf/current-page segment).
    """
    return [{"label": label, "url": url} for label, url in segments]


async def _resolve_repo(
    owner: str, repo_slug: str, db: AsyncSession
) -> tuple[str, str]:
    """Resolve owner+slug to repo_id; raise 404 if not found.

    Returns (repo_id, base_url) as a convenience so callers can unpack
    both in one line.
    """
    row = await musehub_repository.get_repo_orm_by_owner_slug(db, owner, repo_slug)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Repo '{owner}/{repo_slug}' not found",
        )
    return str(row.repo_id), _base_url(owner, repo_slug)


async def _resolve_repo_full(
    owner: str, repo_slug: str, db: AsyncSession
) -> tuple[RepoResponse, str]:
    """Resolve owner+slug to a full RepoResponse; raise 404 if not found.

    Returns (repo_response, base_url).  Use this when the handler needs
    structured repo data (e.g. to return JSON via negotiate_response).
    """
    repo = await musehub_repository.get_repo_by_owner_slug(db, owner, repo_slug)
    if repo is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Repo '{owner}/{repo_slug}' not found",
        )
    return repo, _base_url(owner, repo_slug)


def _og_tags(
    *,
    title: str,
    description: str = "",
    image: str = "",
    og_type: str = "website",
    twitter_card: str = "summary",
) -> dict[str, str]:
    """Build Open Graph and Twitter Card meta tag dict for a page template.

    Returns a flat mapping of meta property name → content string.  Template
    authors receive this as ``og_meta`` in the template context and iterate
    over it to emit ``<meta property="..." content="...">`` tags in the
    document ``<head>``.

    Why a helper: OG tags are structurally repetitive (title, description, and
    image appear once for OG and once for Twitter).  Centralising the mapping
    ensures both protocol families stay in sync and reduces copy-paste errors
    in handlers.

    Call this for any page that should expose rich-preview metadata to social
    crawlers and link-unfurling bots.  Omit ``image`` when no canonical preview
    image exists — crawlers fall back to the site default.
    """
    tags: dict[str, str] = {
        "og:title": title,
        "og:type": og_type,
        "twitter:card": twitter_card,
        "twitter:title": title,
    }
    if description:
        tags["og:description"] = description
        tags["twitter:description"] = description
    if image:
        tags["og:image"] = image
        tags["twitter:image"] = image
    return tags


# ---------------------------------------------------------------------------
# Fixed-path routes (registered before wildcard routes in main.py)
# ---------------------------------------------------------------------------


@fixed_router.get("/feed", summary="Muse Hub activity feed")
async def feed_page(request: Request) -> Response:
    """Render the activity feed page — events from followed users and watched repos."""
    ctx: dict[str, object] = {"title": "Feed"}
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/feed.html", ctx),
        ctx,
    )


@fixed_router.get("/search", summary="Muse Hub global search page")
async def global_search_page(request: Request, q: str = "", mode: str = "keyword") -> Response:
    """Render the global cross-repo search page.

    Query params ``q`` and ``mode`` are pre-filled into the search form so
    that shared URLs land with the last query already populated.  Values are
    sanitised client-side before being injected into the DOM (XSS safe).
    """
    safe_q = q.replace("'", "\\'").replace('"', '\\"').replace("\n", "").replace("\r", "")
    safe_mode = mode if mode in ("keyword", "pattern") else "keyword"
    ctx: dict[str, object] = {"initial_q": safe_q, "initial_mode": safe_mode}
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/global_search.html", ctx),
        ctx,
    )


@fixed_router.get("/explore", summary="Muse Hub explore page")
async def explore_page(
    request: Request,
    lang: list[str] = Query(default=[], alias="lang", description="Language/instrument filter chips (multi-select)"),
    license_filter: str = Query(default="", alias="license", description="License filter (e.g. CC0, CC BY)"),
    sort: str = Query(default="stars", description="Sort order: stars | updated | forks | trending"),
    topic: list[str] = Query(default=[], alias="topic", description="Topic filter chips (multi-select)"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the explore/discover page — a filterable grid of all public repos.

    No JWT required.  Filter sidebar uses GET params so all filter states are
    bookmarkable and shareable.  Sidebar data (muse_tag chips, topic chips) is
    pre-loaded server-side to avoid an extra round-trip on first paint.

    Filter sources:
    - ``lang`` chips: top 30 distinct values from the ``muse_tags`` table.
    - ``topic`` chips: top 40 distinct tags from ``musehub_repos.tags`` JSON.
    - ``license``: fixed enum (CC0, CC BY, CC BY-SA, CC BY-NC, All Rights Reserved).
    - ``sort``: stars | updated | forks | trending.
    """
    # Fetch top muse_tags for the language/instrument chip cloud.
    tag_rows = await db.execute(
        sa_select(MuseCliTag.tag, func.count(MuseCliTag.tag_id).label("cnt"))
        .group_by(MuseCliTag.tag)
        .order_by(func.count(MuseCliTag.tag_id).desc())
        .limit(30)
    )
    muse_tag_chips: list[str] = [row.tag for row in tag_rows]

    # Fetch top topics from public repo tag JSON arrays (same logic as topics API).
    topic_rows = await db.execute(
        sa_select(musehub_db.MusehubRepo.tags).where(
            musehub_db.MusehubRepo.visibility == "public"
        )
    )
    topic_counts: dict[str, int] = {}
    for (tags,) in topic_rows:
        for t in tags or []:
            key = str(t).lower()
            topic_counts[key] = topic_counts.get(key, 0) + 1
    topic_chips: list[str] = [
        name
        for name, _ in sorted(topic_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:40]
    ]

    _valid_sorts = {"stars", "updated", "forks", "trending"}
    effective_sort = sort if sort in _valid_sorts else "stars"

    ctx: dict[str, object] = {
        "title": "Explore",
        "breadcrumb": "Explore",
        "default_sort": effective_sort,
        "muse_tag_chips": muse_tag_chips,
        "topic_chips": topic_chips,
        "selected_langs": lang,
        "selected_license": license_filter,
        "selected_topics": topic,
        "license_options": ["", "CC0", "CC BY", "CC BY-SA", "CC BY-NC", "All Rights Reserved"],
        "sort_options": [
            ("stars", "Most starred"),
            ("updated", "Recently updated"),
            ("forks", "Most forked"),
            ("trending", "Trending"),
        ],
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/explore.html", ctx),
        ctx,
    )


@fixed_router.get("/trending", summary="Muse Hub trending page")
async def trending_page(request: Request) -> Response:
    """Render the trending page -- public repos sorted by star count.

    Identical shell to the explore page but pre-selects sort=stars so the
    most-starred compositions appear first.
    """
    ctx: dict[str, object] = {"title": "Trending", "breadcrumb": "Trending", "default_sort": "stars"}
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/explore_base.html", ctx),
        ctx,
    )


@fixed_router.get(
    "/{username}",
    response_class=HTMLResponse,
    summary="Muse Hub user profile shortcut — redirects to /users/{username}",
)
async def profile_redirect(username: str) -> RedirectResponse:
    """Redirect /musehub/ui/{username} → /musehub/ui/users/{username}.

    This lets breadcrumb links like /musehub/ui/gabriel resolve to the profile
    page instead of 404-ing (the two-segment /{owner}/{repo_slug} pattern only
    matches when two segments are present).
    """
    return RedirectResponse(
        url=f"/musehub/ui/users/{username}",
        status_code=http_status.HTTP_302_FOUND,
    )


@fixed_router.get(
    "/users/{username}",
    summary='Muse Hub user profile page',
)
async def profile_page(request: Request, username: str) -> Response:
    """Render the public user profile page.

    Displays bio, avatar, pinned repos, all public repos with last-activity,
    a GitHub-style contribution heatmap, and aggregated session credits.
    Auth is handled client-side -- the profile itself is public.
    """
    ctx: dict[str, object] = {
        "title": f"@{username}",
        "username": username,
        "og_meta": _og_tags(
            title=f"@{username} — Muse Hub",
            description=f"{username}'s music repos on Muse Hub",
            og_type="profile",
        ),
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/profile.html", ctx),
        ctx,
    )

# ---------------------------------------------------------------------------
# Repo-scoped pages
# ---------------------------------------------------------------------------


@router.get(
    "/{owner}/{repo_slug}",
    summary="Muse Hub repo home page",
)
async def repo_page(
    request: Request,
    owner: str,
    repo_slug: str,
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the repo home page with arrangement matrix, audio player, stats, and recent commits.

    Also renders four enrichment panels:
    - Contributors: top-10 avatar grid derived from the credits endpoint.
    - Activity heatmap: 52-week GitHub-style heatmap from commit timestamps.
    - Instrument bar: stacked distribution of instrument tracks from commit objects.
    - Clone widget: musehub://, SSH, and HTTPS clone URLs with copy-to-clipboard.

    Content negotiation:
    - ``?format=json`` or ``Accept: application/json`` → full ``RepoResponse`` with camelCase keys.
    - Everything else → HTML home page via ``repo_home.html`` template.

    One URL, two audiences — agents get structured data, humans get rich HTML.

    Clone URL variants passed to the template:
    - ``clone_url_musehub``: native DAW protocol (``musehub://{owner}/{slug}``)
    - ``clone_url_ssh``: SSH git remote (``ssh://git@musehub.stori.app/{owner}/{slug}.git``)
    - ``clone_url_https``: HTTPS git remote (``https://musehub.stori.app/{owner}/{slug}.git``)
    """
    repo, base_url = await _resolve_repo_full(owner, repo_slug, db)
    page_url = str(request.url)
    return await negotiate_response(
        request=request,
        template_name="musehub/pages/repo_home.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": str(repo.repo_id),
            "base_url": base_url,
            "current_page": "home",
            "jsonld_script": render_jsonld_script(jsonld_repo(repo, page_url)),
            "og_meta": _og_tags(
                title=f"{owner}/{repo_slug} — Muse Hub",
                description=repo.description or f"Music composition repository by {owner}",
                og_type="website",
            ),
            # Clone URL variants for the clone widget panel — derived server-side
            # so the template never has to reconstruct them from owner/slug.
            "clone_url_musehub": f"musehub://{owner}/{repo_slug}",
            "clone_url_ssh": f"ssh://git@musehub.stori.app/{owner}/{repo_slug}.git",
            "clone_url_https": f"https://musehub.stori.app/{owner}/{repo_slug}.git",
        },
        templates=templates,
        json_data=repo,
        format_param=format,
    )


@router.get(
    "/{owner}/{repo_slug}/commits",
    summary="Muse Hub commits list page",
)
async def commits_list_page(
    request: Request,
    owner: str,
    repo_slug: str,
    branch: str | None = Query(None, description="Filter commits by branch name"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    per_page: int = Query(30, ge=1, le=200, description="Commits per page"),
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    author: str | None = Query(None, description="Filter by commit author"),
    q: str | None = Query(None, description="Full-text search over commit messages"),
    date_from: str | None = Query(None, alias="dateFrom", description="ISO date lower bound (inclusive), e.g. 2026-01-01"),
    date_to: str | None = Query(None, alias="dateTo", description="ISO date upper bound (inclusive), e.g. 2026-12-31"),
    tag_filter: str | None = Query(None, alias="tag", description="Filter by muse_tag prefix, e.g. 'emotion:happy', 'stage:chorus'"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the paginated commits list page or return structured commit data as JSON.

    HTML (default): renders ``commits.html`` with:
    - Rich filter bar: author dropdown, date range pickers, message search, tag filter.
    - Per-commit metadata badges: tempo (♩ BPM), key, emotion, stage, instruments.
    - Compare mode: checkbox per row; selecting exactly 2 activates a compare link.
    - Visual mini-lane: DAG dots with merge-commit indicators.
    - Paginated history, branch selector.

    JSON (``Accept: application/json`` or ``?format=json``): returns
    ``CommitListResponse`` with the newest commits first for the requested page.

    Filter params (``author``, ``q``, ``dateFrom``, ``dateTo``, ``tag``) are
    applied server-side so pagination counts stay accurate.  They are forwarded
    through pagination links so the filter state persists across pages.
    """
    from datetime import date as _date, timedelta as _td

    import sqlalchemy as _sa

    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)

    # ── Build the filtered SQLAlchemy query ──────────────────────────────────
    base_stmt = sa_select(musehub_db.MusehubCommit).where(
        musehub_db.MusehubCommit.repo_id == repo_id
    )
    if branch:
        base_stmt = base_stmt.where(musehub_db.MusehubCommit.branch == branch)
    if author:
        base_stmt = base_stmt.where(musehub_db.MusehubCommit.author == author)
    if q:
        base_stmt = base_stmt.where(
            musehub_db.MusehubCommit.message.ilike(f"%{q}%")
        )
    if date_from:
        try:
            df = _date.fromisoformat(date_from)
            base_stmt = base_stmt.where(musehub_db.MusehubCommit.timestamp >= df.isoformat())
        except ValueError:
            pass  # ignore malformed date — show all results
    if date_to:
        try:
            dt = _date.fromisoformat(date_to)
            dt_end = (dt + _td(days=1)).isoformat()  # inclusive upper bound
            base_stmt = base_stmt.where(musehub_db.MusehubCommit.timestamp < dt_end)
        except ValueError:
            pass

    # tag_filter matches muse_tag namespace prefixes embedded in commit messages
    # (e.g. "emotion:happy", "stage:chorus") since musehub_commits has no
    # separate tags column — tags live in commit messages by convention.
    if tag_filter:
        base_stmt = base_stmt.where(
            musehub_db.MusehubCommit.message.ilike(f"%{tag_filter}%")
        )

    total_stmt = sa_select(func.count()).select_from(base_stmt.subquery())
    total: int = (await db.execute(total_stmt)).scalar_one()

    offset = (page - 1) * per_page
    rows_stmt = (
        base_stmt.order_by(_sa.desc(musehub_db.MusehubCommit.timestamp))
        .offset(offset)
        .limit(per_page)
    )
    rows = (await db.execute(rows_stmt)).scalars().all()

    # Build CommitResponse objects inline — same mapping as the service layer.
    commits = [
        CommitResponse(
            commit_id=r.commit_id,
            branch=r.branch,
            parent_ids=list(r.parent_ids or []),
            message=r.message,
            author=r.author,
            timestamp=r.timestamp,
            snapshot_id=r.snapshot_id,
        )
        for r in rows
    ]

    # ── Distinct authors for the filter dropdown ──────────────────────────────
    author_stmt = (
        sa_select(musehub_db.MusehubCommit.author)
        .where(musehub_db.MusehubCommit.repo_id == repo_id)
        .distinct()
        .order_by(musehub_db.MusehubCommit.author)
    )
    all_authors: list[str] = list((await db.execute(author_stmt)).scalars().all())

    branches = await musehub_repository.list_branches(db, repo_id)
    total_pages = max(1, (total + per_page - 1) // per_page)

    # ── Active filter set (forwarded to pagination links) ────────────────────
    active_filters: dict[str, str] = {}
    if branch:
        active_filters["branch"] = branch
    if author:
        active_filters["author"] = author
    if q:
        active_filters["q"] = q
    if date_from:
        active_filters["dateFrom"] = date_from
    if date_to:
        active_filters["dateTo"] = date_to
    if tag_filter:
        active_filters["tag"] = tag_filter

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/commits.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "commits",
            "commits": commits,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "branch": branch,
            "branches": branches,
            "all_authors": all_authors,
            "filter_author": author or "",
            "filter_q": q or "",
            "filter_date_from": date_from or "",
            "filter_date_to": date_to or "",
            "filter_tag": tag_filter or "",
            "active_filters": active_filters,
            "breadcrumb_data": _breadcrumbs(
                (owner, f"/musehub/ui/{owner}"),
                (repo_slug, base_url),
                ("commits", ""),
            ),
        },
        templates=templates,
        json_data=CommitListResponse(commits=commits, total=total),
        format_param=format,
    )


@router.get(
    "/{owner}/{repo_slug}/commits/{commit_id}",
    summary="Muse Hub commit detail page",
)
async def commit_page(
    request: Request,
    owner: str,
    repo_slug: str,
    commit_id: str,
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the commit detail page with inline audio player, muse_tags metadata,
    reactions, comment thread, and cross-reference panel.

    HTML (default): rich commit detail page via Jinja2 with:
    - Inline WaveSurfer.js audio player (full mix + per-stem track selector +
      volume control).  Falls back to ``<audio>`` when WaveSurfer is unavailable.
    - Full metadata panel sourced from analysis APIs (tempo_bpm, key,
      time_signature; emotion/stage tags rendered as colored pills).  DB-stored
      ``muse_tags`` are also rendered via the namespace-aware ``tagPill()``
      helper; ``ref:`` tags whose value is a URL open that source directly.
    - Reactions row (8 emoji types) backed by the existing reactions API.
    - Threaded comment section with add/edit/delete and nested reply support.
    - Cross-references panel showing PRs, issues, and sessions that mention
      this commit hash.
    - A 2-sentence prose summary (``buildProseSummary``) synthesised from key,
      tempo, emotion, and diff-dimension data.

    JSON (``Accept: application/json`` or ``?format=json``): returns the full
    ``CommitResponse`` Pydantic model with camelCase keys, or a minimal context
    dict if the commit is not yet in the DB (not yet synced).

    Artifacts are displayed by extension:
    - ``.webp/.png/.jpg`` → inline ``<img>``
    - ``.mp3/.ogg/.wav``  → ``<audio controls>`` player / WaveSurfer stem
    - ``.mid/.midi``      → piano-roll preview card
    - ``.abc/.musicxml``  → score preview via abcjs
    - other              → download link
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    commit = await musehub_repository.get_commit(db, repo_id, commit_id)
    commit_description = commit.message if commit is not None else ""
    short_id = commit_id[:8]
    listen_url = f"{base_url}/listen/{commit_id}"
    embed_url = f"{base_url}/embed/{commit_id}"
    return await negotiate_response(
        request=request,
        template_name="musehub/pages/commit.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "commit_id": commit_id,
            "short_id": short_id,
            "base_url": base_url,
            "listen_url": listen_url,
            "embed_url": embed_url,
            "current_page": "commits",
            "breadcrumb_data": _breadcrumbs(
                (owner, f"/musehub/ui/{owner}"),
                (repo_slug, base_url),
                ("commits", base_url),
                (short_id, ""),
            ),
            "og_meta": _og_tags(
                title=f"Commit {short_id} · {owner}/{repo_slug} — Muse Hub",
                description=commit_description,
                og_type="music.song",
            ),
        },
        templates=templates,
        json_data=commit,
        format_param=format,
    )


@router.get(
    "/{owner}/{repo_slug}/commits/{commit_id}/diff",
    summary="Muse Hub musical diff view",
)
async def diff_page(
    request: Request,
    owner: str,
    repo_slug: str,
    commit_id: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the musical diff between a commit and its parent.

    Shows key/tempo/time-signature deltas, tracks added/removed/modified,
    and side-by-side artifact comparison. Fetches commit and parent metadata
    from the API client-side.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "commit_id": commit_id,
        "base_url": base_url,
        "current_page": "commits",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/diff.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/graph",
    summary="Muse Hub interactive DAG commit graph",
)
async def graph_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the interactive DAG commit graph.

    Client-side SVG renderer with branch colour-coding, merge-commit diamonds,
    zoom/pan, hover popovers, and click-to-navigate.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_url": base_url,
        "current_page": "graph",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/graph.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/pulls",
    summary="Muse Hub pull request list page",
)
async def pr_list_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the PR list page with open/all state filter."""
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_url": base_url,
        "current_page": "pulls",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/pr_list.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/pulls/{pr_id}",
    response_class=HTMLResponse,
    summary="Muse Hub PR detail page with musical diff",
)
async def pr_detail_page(
    request: Request,
    owner: str,
    repo_slug: str,
    pr_id: str,
    format: str | None = Query(None, pattern="^json$", description="Set to 'json' to receive structured data"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the PR detail page with musical diff, reviewer panel, and sidebar.

    HTML response includes the full musical diff UI enhanced with five additions:

    1. **Reviewer status panel** — avatar chips for each requested reviewer with
       pending / approved / changes_requested / dismissed badges, plus a
       "Submit your review" form (approve / request changes / comment) for
       authenticated maintainers.

    2. **Merge options** — three strategy buttons (merge commit / squash / rebase)
       with a "Delete branch after merge" checkbox.  All controls are disabled when
       the PR is not mergeable (``pr.mergeable == false``).

    3. **Collapsible commit diff panels** — one ``<details>`` element per head-branch
       commit showing similarity % to the base branch and an inline 8-axis
       emotion-diff mini radar chart (via the ``/analysis/{ref}/similarity`` and
       ``/analysis/{ref}/emotion-diff`` APIs).

    4. **Markdown description** — ``pr.body`` is rendered as formatted HTML rather
       than a raw ``<pre>`` block, using the inline ``renderMarkdown()`` helper.

    5. **Labels and milestone sidebar** — labels assigned to the PR are shown as
       colour-coded chips with add / remove controls for maintainers; the milestone
       title (if set) is displayed below.

    JSON response (``?format=json`` or ``Accept: application/json``) returns the
    PR metadata merged with per-dimension diff scores — suitable for AI agent
    consumption to reason about musical impact before approving a merge.

    The merge action calls
    ``POST /api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/merge``
    with the selected ``mergeStrategy`` and optional ``deleteBranch`` flag.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)

    context: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "pr_id": pr_id,
        "base_url": base_url,
        "current_page": "pulls",
    }

    # For JSON responses, eagerly compute the diff so agents get full data.
    json_data: PRDiffResponse | None = None
    if format == "json":
        pr = await musehub_pull_requests.get_pr(db, repo_id, pr_id)
        if pr is not None:
            try:
                result = await musehub_divergence.compute_hub_divergence(
                    db,
                    repo_id=repo_id,
                    branch_a=pr.to_branch,
                    branch_b=pr.from_branch,
                )
                json_data = musehub_divergence.build_pr_diff_response(
                    pr_id=pr_id,
                    from_branch=pr.from_branch,
                    to_branch=pr.to_branch,
                    result=result,
                )
            except ValueError:
                json_data = musehub_divergence.build_zero_diff_response(
                    pr_id=pr_id,
                    repo_id=repo_id,
                    from_branch=pr.from_branch,
                    to_branch=pr.to_branch,
                )

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/pr_detail.html",
        context=context,
        templates=templates,
        json_data=json_data,
        format_param=format,
    )


@router.get(
    "/{owner}/{repo_slug}/issues",
    summary="Muse Hub issue list page",
)
async def issue_list_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the issue list page with open/closed/all state filter."""
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_url": base_url,
        "current_page": "issues",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/issue_list.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/context/{ref}",
    summary="Muse Hub AI context viewer",
)
async def context_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the AI context viewer for a given commit ref.

    Shows the MuseHubContextResponse as a structured human-readable document:
    musical state, history summary, missing elements, suggestions, and raw JSON.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "base_url": base_url,
        "current_page": "analysis",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/context.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/issues/{number}",
    summary="Muse Hub issue detail page",
)
async def issue_detail_page(
    request: Request,
    owner: str,
    repo_slug: str,
    number: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the issue detail page with close button.

    The close button calls
    ``POST /api/v1/musehub/repos/{repo_id}/issues/{number}/close``
    and reloads the page on success.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "issue_number": number,
        "base_url": base_url,
        "current_page": "issues",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/issue_detail.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/embed/{ref}",
    summary="Embeddable MuseHub audio player widget",
)
async def embed_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render a compact, iframe-safe audio player for a MuseHub repo commit.

    Why this route exists: external sites embed MuseHub compositions via
    ``<iframe src="/musehub/ui/{owner}/{repo_slug}/embed/{ref}">``.

    Contract:
    - No JWT required -- public repos can be embedded without auth.
    - Returns ``X-Frame-Options: ALLOWALL`` so browsers permit cross-origin framing.
    - Audio fetched from ``/api/v1/musehub/repos/{repo_id}/objects`` at runtime.
    """
    repo_id, _ = await _resolve_repo(owner, repo_slug, db)
    short_ref = ref[:8] if len(ref) >= 8 else ref
    listen_url = _base_url(owner, repo_slug)
    content = templates.TemplateResponse(
        request,
        "musehub/pages/embed.html",
        {
            "title": f"Player {short_ref}",
            "repo_id": repo_id,
            "ref": ref,
            "listen_url": listen_url,
        },
    )
    return Response(
        content=content.body,
        media_type="text/html",
        headers={"X-Frame-Options": "ALLOWALL"},
    )


@router.get(
    "/{owner}/{repo_slug}/listen/{ref}",
    summary="Muse Hub listen page — full-mix and per-track audio playback",
)
async def listen_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the listen page with a full-mix player and per-track listing.

    Why this route exists: musicians need a dedicated listening experience to
    evaluate each stem's contribution to the mix without exporting files to a
    DAW.  The page surfaces the full-mix audio at the top, then lists each
    audio artifact with its own player, mute/solo controls, a mini waveform
    visualisation, a download button, and a link to the piano-roll view.

    Content negotiation:
    - HTML (default): interactive listen page via Jinja2.
    - JSON (``Accept: application/json`` or ``?format=json``):
      returns ``TrackListingResponse`` with all audio URLs.

    Graceful fallback: when no audio renders exist the page shows a call-to-
    action rather than an empty list, so musicians know what to do next.
    No JWT required — the HTML shell's JS handles auth for private repos.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    json_data = await musehub_listen.build_track_listing(db, repo_id, ref)

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/listen.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "ref": ref,
            "base_url": base_url,
            "current_page": "listen",
        },
        templates=templates,
        json_data=json_data,
        format_param=format,
    )


@router.get(
    "/{owner}/{repo_slug}/listen/{ref}/{path:path}",
    summary="Muse Hub listen page — individual stem playback",
)
async def listen_track_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    path: str,
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the per-track listen page for a single stem artifact.

    Why this route exists: ``path`` identifies a specific stem (e.g.
    ``tracks/bass.mp3``).  This page focuses the player on that one file
    and provides a "Back to full mix" link, a download button, and the
    piano-roll viewer if a matching image artifact exists.

    Content negotiation mirrors ``listen_page``: JSON returns a single-track
    ``TrackListingResponse`` with ``has_renders=True`` when the file exists.

    No JWT required — HTML shell; JS handles auth for private repos.
    """
    import os

    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    objects = await musehub_repository.list_objects(db, repo_id)

    object_map: dict[str, str] = {obj.path: obj.object_id for obj in objects}
    image_exts: frozenset[str] = frozenset({".webp", ".png", ".jpg", ".jpeg"})
    api_base = f"/api/v1/musehub/repos/{repo_id}"

    target_obj = next((obj for obj in objects if obj.path == path), None)
    has_renders = target_obj is not None

    from maestro.models.musehub import AudioTrackEntry

    tracks: list[AudioTrackEntry] = []
    full_mix_url: str | None = None

    if target_obj:
        stem = os.path.splitext(os.path.basename(target_obj.path))[0]
        piano_roll_url: str | None = None
        for p, oid in object_map.items():
            if os.path.splitext(p)[1].lower() in image_exts and os.path.splitext(os.path.basename(p))[0] == stem:
                piano_roll_url = f"{api_base}/objects/{oid}/content"
                break
        tracks = [
            AudioTrackEntry(
                name=stem,
                path=target_obj.path,
                object_id=target_obj.object_id,
                audio_url=f"{api_base}/objects/{target_obj.object_id}/content",
                piano_roll_url=piano_roll_url,
                size_bytes=target_obj.size_bytes,
            )
        ]
        full_mix_url = f"{api_base}/objects/{target_obj.object_id}/content"

    json_data = TrackListingResponse(
        repo_id=repo_id,
        ref=ref,
        full_mix_url=full_mix_url,
        tracks=tracks,
        has_renders=has_renders,
    )

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/listen.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "ref": ref,
            "track_path": path,
            "base_url": base_url,
            "current_page": "listen",
        },
        templates=templates,
        json_data=json_data,
        format_param=format,
    )


@router.get(
    "/{owner}/{repo_slug}/credits",
    summary="Muse Hub dynamic credits page",
)
async def credits_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the dynamic credits page -- album liner notes for the repo.

    Displays every contributor with session count, inferred roles, and activity
    timeline.  Embeds ``<script type="application/ld+json">`` for machine-readable
    attribution using schema.org ``MusicComposition`` vocabulary.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_url": base_url,
        "current_page": "credits",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/credits.html", ctx),
        ctx,
    )

@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}",
    summary="Muse Hub analysis dashboard -- all musical dimensions at a glance",
)
async def analysis_dashboard_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the analysis dashboard: summary cards for all 10 musical dimensions.

    Why this exists: musicians and AI agents need a single entry point that
    shows the full musical character of a composition at a glance -- key,
    tempo, meter, dynamics, groove, emotion, form, motifs, chord map, and
    contour -- without issuing 13 separate analysis commands.

    Contract:
    - No JWT required -- HTML shell; JS fetches authed data via localStorage token.
    - Fetches ``GET /api/v1/musehub/repos/{repo_id}/analysis/{ref}`` (aggregate).
    - Branch selector fetches ``GET /api/v1/musehub/repos/{repo_id}/branches``.
    - Each card links to the dedicated per-dimension analysis page.
    - Graceful empty state when analysis data is not yet available.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "base_url": base_url,
        "current_page": "analysis",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/analysis.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/search",
    summary="Muse Hub in-repo search page",
)
async def search_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the in-repo search page with four mode tabs.

    Modes:
    - Musical Properties (``mode=property``) -- filter by harmony/rhythm/etc.
    - Natural Language (``mode=ask``) -- free-text question over commit history.
    - Keyword (``mode=keyword``) -- keyword overlap scored search.
    - Pattern (``mode=pattern``) -- substring match against messages and branches.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_url": base_url,
        "current_page": "search",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/search.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}/motifs",
    summary="Muse Hub motif browser page",
)
async def motifs_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the motif browser for a given commit ref.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/analysis/{ref}/motifs``
    and renders:
    - All detected motifs with interval pattern and occurrence count
    - Mini piano roll visualising the note pattern for each motif
    - Contour label (arch, valley, oscillating, etc.)
    - Transformation badges (inversion, retrograde, transposition)
    - Motif recurrence grid (tracks x sections heatmap)
    - Cross-track sharing indicators
    - Track and section filters

    Auth is handled client-side via localStorage JWT, matching all other UI
    pages.  No JWT is required to render the HTML shell.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "base_url": base_url,
        "current_page": "analysis",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/motifs.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/arrange/{ref}",
    summary="Muse Hub arrangement matrix page",
)
async def arrange_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the arrangement matrix page for a given commit ref.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/arrange/{ref}`` and renders
    an interactive instrument × section grid where:

    - Y-axis: instruments (bass, keys, guitar, drums, lead, pads)
    - X-axis: sections (intro, verse_1, chorus, bridge, outro)
    - Cell colour intensity encodes note density (0 = silent, max = densest)
    - Cell click navigates to the piano roll for that instrument + section
    - Hover tooltip shows note count, beat range, and pitch range
    - Row summaries show per-instrument note totals and section activity counts
    - Column summaries show per-section note totals and active instrument counts

    Auth is handled client-side via localStorage JWT, matching all other UI
    pages.  No JWT is required to render the HTML shell.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "base_url": base_url,
        "current_page": "arrange",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/arrange.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/compare/{refs}",
    response_class=HTMLResponse,
    summary="Muse Hub compare view — multi-dimensional musical diff between two refs",
)
async def compare_page(
    request: Request,
    owner: str,
    repo_slug: str,
    refs: str,
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the compare view for two refs (branches, tags, or commit SHAs).

    The ``refs`` path segment encodes both refs separated by ``...``:
    ``main...feature-branch`` compares ``main`` (base) against
    ``feature-branch`` (head).

    HTML (default): renders the compare page with radar chart, dimension
    panels, piano roll, emotion diff, and commit list.
    JSON (``Accept: application/json`` or ``?format=json``): returns the
    full ``CompareResponse`` from the API endpoint.

    Returns 404 when:
    - The repo owner/slug is unknown.
    - The ``refs`` value does not contain the ``...`` separator.
    - Either ref has no commits in this repo (delegated to API response).
    """
    if "..." not in refs:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Invalid compare spec '{refs}' — expected format: base...head",
        )
    base_ref, head_ref = refs.split("...", 1)
    if not base_ref or not head_ref:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Both base and head refs must be non-empty",
        )
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)

    context = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "refs": refs,
        "base_url": base_url,
        "current_page": "compare",
        "breadcrumb_data": _breadcrumbs(
            (owner, f"/musehub/ui/{owner}"),
            (repo_slug, base_url),
            ("compare", ""),
            (f"{base_ref}...{head_ref}", ""),
        ),
    }
    return await negotiate_response(
        request=request,
        template_name="musehub/pages/compare.html",
        context=context,
        templates=templates,
        json_data=None,
        format_param=format,
    )


@router.get(
    "/{owner}/{repo_slug}/divergence",
    summary="Muse Hub divergence visualization page",
)
async def divergence_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the divergence visualization: radar chart + dimension detail panels.

    Compares two branches across five musical dimensions
    (melodic/harmonic/rhythmic/structural/dynamic).
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_url": base_url,
        "current_page": "analysis",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/divergence.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/timeline",
    summary="Muse Hub timeline page",
)
async def timeline_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the layered chronological timeline visualisation.

    Four independently toggleable layers: commits, emotion line chart,
    section markers, and track add/remove markers.  Includes a time
    scrubber and zoom controls (day/week/month/all-time).
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_url": base_url,
        "current_page": "timeline",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/timeline.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/releases",
    summary="Muse Hub release list page",
)
async def release_list_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the release list page: all published versions newest first."""
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_url": base_url,
        "current_page": "releases",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/release_list.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/releases/{tag}",
    summary="Muse Hub release detail page",
)
async def release_detail_page(
    request: Request,
    owner: str,
    repo_slug: str,
    tag: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the release detail page: title, release notes, download packages.

    Download packages (MIDI bundle, stems, MP3, MusicXML, metadata) are
    rendered as download cards; unavailable packages show a "not available"
    indicator.
    """
    repo, base_url = await _resolve_repo_full(owner, repo_slug, db)
    repo_id = str(repo.repo_id)
    release = await musehub_releases.get_release_by_tag(db, repo_id, tag)
    page_url = str(request.url)
    jsonld_script: str | None = None
    if release is not None:
        jsonld_script = render_jsonld_script(jsonld_release(release, repo, page_url))
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "tag": tag,
        "base_url": base_url,
        "current_page": "releases",
        "jsonld_script": jsonld_script,
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/release_detail.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/sessions",
    summary="Muse Hub session log page",
)
async def sessions_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the session log page -- all recording sessions newest first.

    Active sessions are highlighted with a live indicator at the top of the list.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_url": base_url,
        "current_page": "sessions",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/sessions.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/sessions/{session_id}",
    summary="Muse Hub session detail page",
)
async def session_detail_page(
    request: Request,
    owner: str,
    repo_slug: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the full session detail page.

    Shows metadata, participants with session-count badges, commits made during
    the session, and closing notes.  Renders a 404 message inline if the API
    returns 404, so agents can distinguish missing sessions from server errors.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "session_id": session_id,
        "base_url": base_url,
        "current_page": "sessions",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/session_detail.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/insights",
    summary="Muse Hub repo insights dashboard",
)
async def insights_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the repo insights dashboard.

    Shows commit frequency heatmap, contributor breakdown, musical evolution
    timeline (key/BPM/energy across commits), branch activity, and download
    statistics.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_url": base_url,
        "current_page": "insights",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/insights.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}/contour",
    summary="Muse Hub melodic contour analysis page",
)
async def contour_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the melodic contour analysis page for a Muse commit ref.

    Visualises per-track melodic shapes, tessitura, and cross-commit contour
    comparison via a pitch-curve line graph in SVG.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "base_url": base_url,
        "current_page": "analysis",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/contour.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}/tempo",
    summary="Muse Hub tempo analysis page",
)
async def tempo_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the tempo analysis page for a Muse commit ref.

    Displays BPM, time feel, stability, and a timeline of tempo change events.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "base_url": base_url,
        "current_page": "analysis",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/tempo.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}/dynamics",
    summary="Muse Hub dynamics analysis page",
)
async def dynamics_analysis_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the dynamics analysis page for a Muse commit ref.

    Visualises velocity profiles, arc classifications, and per-track loudness
    so a mixing engineer can spot dynamic imbalances without running the CLI.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "base_url": base_url,
        "current_page": "analysis",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/dynamics.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}/key",
    summary="Muse Hub key detection analysis page",
)
async def key_analysis_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the key detection analysis page for a Muse commit ref.

    Displays the detected tonic, mode, relative key, confidence bar, and a
    ranked list of alternate key candidates.  Agents use this to confirm the
    tonal centre before generating harmonically compatible material.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "base_url": base_url,
        "current_page": "analysis",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/key.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}/meter",
    summary="Muse Hub meter analysis page",
)
async def meter_analysis_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the metric analysis page for a Muse commit ref.

    Shows the primary time signature, compound/simple classification, a
    beat-strength profile bar chart, and any irregular-meter sections.
    Agents use this to generate rhythmically coherent material.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "base_url": base_url,
        "current_page": "analysis",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/meter.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}/chord-map",
    summary="Muse Hub chord map analysis page",
)
async def chord_map_analysis_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the chord map analysis page for a Muse commit ref.

    Lists the full chord progression with beat positions, Roman-numeral
    harmonic functions, tension scores, and a tension-curve SVG graph.
    Agents use this to generate harmonically idiomatic accompaniment.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "base_url": base_url,
        "current_page": "analysis",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/chord_map.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}/groove",
    summary="Muse Hub groove analysis page",
)
async def groove_analysis_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the rhythmic groove analysis page for a Muse commit ref.

    Displays groove style, BPM, grid resolution, onset deviation, groove
    score gauge, and a swing-factor bar.  Agents use this to match rhythmic
    feel when generating continuation material.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "base_url": base_url,
        "current_page": "analysis",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/groove.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}/emotion",
    summary="Muse Hub emotion analysis page",
)
async def emotion_analysis_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the emotion analysis page for a Muse commit ref.

    Displays primary emotion label, valence-arousal plot, tension bar, and
    confidence score.  Agents use this to maintain emotional continuity or
    introduce deliberate contrast in the next section.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "base_url": base_url,
        "current_page": "analysis",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/emotion.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}/form",
    summary="Muse Hub form analysis page",
)
async def form_analysis_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the formal structure analysis page for a Muse commit ref.

    Shows the detected macro form label (e.g. AABA, verse-chorus), a colour-coded
    section timeline, and a per-section table with beat ranges and function labels.
    Agents use this to understand where they are in the compositional arc.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "base_url": base_url,
        "current_page": "analysis",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/form.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/tree/{ref}",
    summary="Muse Hub file tree browser — repo root",
)
async def tree_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the file tree browser for the repo root at a given ref.

    Displays all top-level files and directories with music-aware file-type
    icons (MIDI=piano, MP3/WAV=waveform, JSON=braces, images=photo).
    The branch/tag selector dropdown allows switching ref without a page reload.
    Breadcrumbs show: {owner} / {repo} / tree / {ref}.

    Content negotiation: the embedded JavaScript also uses this URL to fetch
    a JSON listing from GET /api/v1/musehub/repos/{repo_id}/tree/{ref} when
    the Accept header is application/json.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "dir_path": "",
        "base_url": base_url,
        "current_page": "tree",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/tree.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/tree/{ref}/{path:path}",
    summary="Muse Hub file tree browser — subdirectory",
)
async def tree_subdir_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    path: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the file tree browser for a subdirectory at a given ref.

    Behaves identically to ``tree_page`` but scoped to the subdirectory
    identified by ``path`` (e.g. "tracks", "tracks/stems").  The breadcrumb
    expands to show each path segment as a clickable link.

    Files are clickable and navigate to the blob viewer:
    /{owner}/{repo_slug}/blob/{ref}/{path}
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "dir_path": path,
        "base_url": base_url,
        "current_page": "tree",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/tree.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/groove-check",
    summary="Muse Hub groove check page",
)
async def groove_check_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the rhythmic consistency dashboard for a repo.

    Displays a summary of groove metrics, an SVG bar chart of groove scores
    over the commit window, and a per-commit table with status badges.

    The chart encodes status as bar colour: green = OK, orange = WARN,
    red = FAIL.  Threshold and limit can be adjusted via controls that
    re-fetch the underlying ``GET /api/v1/musehub/repos/{repo_id}/groove-check``
    endpoint client-side.

    Auth is handled client-side via localStorage JWT, consistent with all other
    Muse Hub UI pages.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_url": base_url,
        "current_page": "groove-check",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/groove_check.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/branches",
    summary="Muse Hub branch list page",
)
async def branches_page(
    request: Request,
    owner: str,
    repo_slug: str,
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the branch list page or return structured branch data as JSON.

    HTML (default): lists all branches with HEAD commit info, ahead/behind counts,
    musical divergence scores (placeholder), compare links, and New Pull Request buttons.
    JSON (``Accept: application/json`` or ``?format=json``): returns
    ``BranchDetailListResponse`` with per-branch ahead/behind counts.

    Content negotiation — one URL, two audiences: musicians get rich HTML,
    agents get structured JSON to programmatically inspect branch state.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    branch_data: BranchDetailListResponse = (
        await musehub_repository.list_branches_with_detail(db, repo_id)
    )
    return await negotiate_response(
        request=request,
        template_name="musehub/pages/branches.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "branches",
        },
        templates=templates,
        json_data=branch_data,
        format_param=format,
    )


@router.get(
    "/{owner}/{repo_slug}/tags",
    summary="Muse Hub tag browser page",
)
async def tags_page(
    request: Request,
    owner: str,
    repo_slug: str,
    namespace: str | None = Query(None, description="Filter tags by namespace prefix"),
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the tag browser page or return structured tag data as JSON.

    Tags are sourced from repo releases.  The tag browser groups tags by their
    namespace prefix (the text before ``:``, e.g. ``emotion``, ``genre``,
    ``instrument``) — tags without a colon fall into the ``version`` namespace.

    HTML (default): filterable list of tags grouped by namespace with commit info.
    JSON (``Accept: application/json`` or ``?format=json``): returns
    ``TagListResponse`` with namespace grouping and optional ``?namespace`` filtering.

    Click a tag to navigate to the commit detail page for that release's commit.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    releases = await musehub_releases.list_releases(db, repo_id)

    all_tags: list[TagResponse] = []
    for release in releases:
        tag_str = release.tag
        if ":" in tag_str:
            ns, _ = tag_str.split(":", 1)
        else:
            ns = "version"
        all_tags.append(
            TagResponse(
                tag=tag_str,
                namespace=ns,
                commit_id=release.commit_id,
                message=release.title,
                created_at=release.created_at,
            )
        )

    if namespace:
        filtered_tags = [t for t in all_tags if t.namespace == namespace]
    else:
        filtered_tags = all_tags

    namespaces: list[str] = sorted({t.namespace for t in all_tags})
    tag_data = TagListResponse(tags=filtered_tags, namespaces=namespaces)

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/tags.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "tags",
            "active_namespace": namespace or "",
        },
        templates=templates,
        json_data=tag_data,
        format_param=format,
    )


@router.get(
    "/{repo_id}/form-structure/{ref}",
    summary="Muse Hub form and structure page",
)
async def form_structure_page(
    request: Request,
    repo_id: str,
    ref: str,
) -> Response:
    """Render the form and structure analysis page for a commit ref.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/form-structure/{ref}`` and
    renders three structural analysis panels:

    - **Section map**: SVG timeline of intro/verse/chorus/bridge/outro bars,
      colour-coded by section type, with bar numbers and length labels.
    - **Repetition structure**: which sections repeat, how many times, and
      their mean pairwise similarity score.
    - **Section comparison**: similarity heatmap rendered as an SVG grid
      where cell colour intensity encodes the 0–1 cosine similarity between
      every pair of formal sections.

    Auth is handled client-side via localStorage JWT, matching all other UI
    pages.  No JWT is required to load the HTML shell.
    """
    short_ref = ref[:8] if len(ref) >= 8 else ref
    ctx: dict[str, object] = {"repo_id": repo_id, "ref": ref, "short_ref": short_ref}
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/form_structure.html", ctx),
        ctx,
    )


@router.get(
    "/{repo_id}/analysis/{ref}/harmony",
    summary="Muse Hub harmony analysis page",
)
async def harmony_analysis_page(request: Request, repo_id: str, ref: str) -> Response:
    """Render the harmony analysis page for a Muse commit ref.

    Fetches harmonic and key data from:
    - ``GET /api/v1/musehub/repos/{repo_id}/analysis/{ref}/harmony``
    - ``GET /api/v1/musehub/repos/{repo_id}/analysis/{ref}/key``

    Displays:
    - Detected key, mode, and relative key
    - Chord progression timeline with beat positions
    - Tension curve graph (SVG, sampled per beat)
    - Modulation markers at key change points
    - Voice-leading quality indicator (smooth vs angular)
    - Track and section filter dropdowns
    - Key history across commits (if history data available)

    Auth is handled client-side via localStorage JWT — no JWT required to
    receive the HTML shell.  JSON content negotiation is handled by the
    existing analysis API endpoints.
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const ref    = {repr(ref)};
      const base   = '/musehub/ui/' + repoId;
      const apiBase = '/api/v1/musehub/repos/' + encodeURIComponent(repoId);

      function escHtml(s) {{
        if (s === null || s === undefined) return '—';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      // ── Filter state ───────────────────────────────────────────────────────
      let currentTrack   = '';
      let currentSection = '';

      // ── Tension curve SVG renderer ─────────────────────────────────────────
      function renderTensionCurve(tensionCurve) {{
        if (!tensionCurve || tensionCurve.length === 0) {{
          return '<p class="loading">No tension data available.</p>';
        }}
        const W = 600, H = 80, PAD = 8;
        const innerW = W - PAD * 2;
        const innerH = H - PAD * 2;
        const pts = tensionCurve.map((t, i) => {{
          const x = PAD + (i / (tensionCurve.length - 1 || 1)) * innerW;
          const y = PAD + (1 - t) * innerH;
          return x + ',' + y;
        }});
        const polyline = '<polyline points="' + pts.join(' ') + '" fill="none" stroke="#58a6ff" stroke-width="2"/>';

        // Danger zone above 0.75
        const dangerY = PAD + (1 - 0.75) * innerH;
        const danger = '<rect x="' + PAD + '" y="' + PAD + '" width="' + innerW + '" height="' + (dangerY - PAD) + '" fill="#f8514922" />';

        // Grid lines at 0.25, 0.5, 0.75
        const gridLines = [0.25, 0.5, 0.75].map(v => {{
          const y = PAD + (1 - v) * innerH;
          return '<line x1="' + PAD + '" y1="' + y + '" x2="' + (W - PAD) + '" y2="' + y + '" stroke="#30363d" stroke-dasharray="3"/>';
        }}).join('');

        const labels = [
          '<text x="' + (PAD - 4) + '" y="' + (PAD + innerH) + '" font-size="9" fill="#8b949e" text-anchor="end">0</text>',
          '<text x="' + (PAD - 4) + '" y="' + (PAD + innerH * 0.25) + '" font-size="9" fill="#8b949e" text-anchor="end">0.75</text>',
          '<text x="' + (PAD - 4) + '" y="' + (PAD + innerH * 0.5) + '" font-size="9" fill="#8b949e" text-anchor="end">0.5</text>',
          '<text x="' + (PAD - 4) + '" y="' + (PAD + innerH * 0.75) + '" font-size="9" fill="#8b949e" text-anchor="end">0.25</text>',
          '<text x="' + (PAD - 4) + '" y="' + PAD + '" font-size="9" fill="#8b949e" text-anchor="end">1</text>',
        ].join('');

        return '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;height:' + H + 'px;overflow:visible">'
          + danger + gridLines + polyline + labels + '</svg>';
      }}

      // ── Chord timeline renderer ────────────────────────────────────────────
      function renderChordTimeline(chords, totalBeats) {{
        if (!chords || chords.length === 0) {{
          return '<p class="loading">No chord data available.</p>';
        }}
        const beats = totalBeats || 32;
        const rows = chords.map(c => {{
          const widthPct = ((1 / beats) * 100).toFixed(2);
          const leftPct  = ((c.beat / beats) * 100).toFixed(2);
          const tensionColor = c.tension > 0.75
            ? '#f85149'
            : c.tension > 0.5
            ? '#f0883e'
            : c.tension > 0.25
            ? '#ffa657'
            : '#3fb950';
          return `<div title="Beat ${{c.beat.toFixed(1)}}: ${{escHtml(c.chord)}} (${{escHtml(c.function)}}) tension=${{c.tension.toFixed(2)}}"
              style="position:absolute;left:${{leftPct}}%;top:0;bottom:0;
                     border-left:2px solid ${{tensionColor}};
                     background:${{tensionColor}}18;
                     min-width:2px;cursor:help">
            <span style="font-size:10px;color:#e6edf3;white-space:nowrap;
                         writing-mode:vertical-rl;text-orientation:mixed;
                         padding:2px 1px;line-height:1">${{escHtml(c.chord)}}</span>
          </div>`;
        }}).join('');

        // Beat ruler
        const rulerMarks = [];
        const step = beats <= 16 ? 1 : beats <= 32 ? 4 : beats <= 64 ? 8 : 16;
        for (let b = 0; b <= beats; b += step) {{
          const pct = (b / beats * 100).toFixed(2);
          rulerMarks.push(
            '<div style="position:absolute;left:' + pct + '%;top:0;bottom:0;border-left:1px solid #30363d">'
            + '<span style="font-size:9px;color:#8b949e;position:absolute;top:2px;left:2px">' + b + '</span>'
            + '</div>'
          );
        }}

        return `
          <div style="position:relative;height:80px;overflow:hidden;background:#0d1117;
                      border:1px solid #30363d;border-radius:4px;margin-bottom:6px">
            ${{rulerMarks.join('')}}
            ${{rows}}
          </div>
          <p style="font-size:11px;color:#8b949e">
            ${{chords.length}} chords over ${{beats}} beats &bull;
            <span style="color:#3fb950">&#9632;</span> low tension &nbsp;
            <span style="color:#ffa657">&#9632;</span> medium &nbsp;
            <span style="color:#f0883e">&#9632;</span> high &nbsp;
            <span style="color:#f85149">&#9632;</span> very high
          </p>`;
      }}

      // ── Modulation marker renderer ─────────────────────────────────────────
      function renderModulationPoints(modulations, totalBeats) {{
        if (!modulations || modulations.length === 0) {{
          return '<p style="color:#3fb950;font-size:13px">No modulations detected — piece remains in one key.</p>';
        }}
        const beats = totalBeats || 32;
        const rows = modulations.map(m => `
          <div style="display:flex;align-items:center;gap:12px;padding:6px 0;
                      border-bottom:1px solid #21262d">
            <span style="font-family:monospace;font-size:13px;color:#f0883e;min-width:60px">
              Beat ${{m.beat.toFixed(1)}}
            </span>
            <span style="font-size:13px">
              ${{escHtml(m.fromKey)}} &rarr; <strong style="color:#58a6ff">${{escHtml(m.toKey)}}</strong>
            </span>
            <div style="flex:1;background:#21262d;border-radius:4px;height:6px">
              <div style="width:${{(m.confidence * 100).toFixed(0)}}%;height:100%;
                          background:#3fb950;border-radius:4px"></div>
            </div>
            <span style="font-size:12px;color:#8b949e;min-width:40px">
              ${{(m.confidence * 100).toFixed(0)}}%
            </span>
          </div>`).join('');

        return `
          <div style="margin-bottom:6px">
            ${{rows}}
          </div>
          <p style="font-size:11px;color:#8b949e">Confidence = key detection certainty at the modulation point.</p>`;
      }}

      // ── Voice-leading indicator ────────────────────────────────────────────
      function renderVoiceLeading(chords) {{
        if (!chords || chords.length < 2) {{
          return '<p class="loading">Insufficient chords for voice-leading analysis.</p>';
        }}
        const avgTension = chords.reduce((s, c) => s + c.tension, 0) / chords.length;
        const tensionDiffs = chords.slice(1).map((c, i) => Math.abs(c.tension - chords[i].tension));
        const avgDiff = tensionDiffs.reduce((s, d) => s + d, 0) / tensionDiffs.length;
        const isSmooth = avgDiff < 0.15;
        const label = isSmooth ? 'Smooth' : 'Angular';
        const color = isSmooth ? '#3fb950' : '#f0883e';
        const pct = Math.min(100, Math.round(avgDiff * 500));
        return `
          <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
            <div>
              <span style="font-size:20px;font-weight:700;color:${{color}}">${{label}}</span>
              <p style="font-size:12px;color:#8b949e;margin-top:2px">
                Mean tension step: ${{avgDiff.toFixed(3)}} &bull;
                Mean tension: ${{avgTension.toFixed(2)}}
              </p>
            </div>
            <div style="flex:1;min-width:120px">
              <div style="background:#21262d;border-radius:4px;height:8px">
                <div style="width:${{pct}}%;height:100%;background:${{color}};border-radius:4px"></div>
              </div>
              <div style="display:flex;justify-content:space-between;font-size:10px;color:#8b949e;margin-top:2px">
                <span>Smooth</span><span>Angular</span>
              </div>
            </div>
          </div>`;
      }}

      // ── Main loader ────────────────────────────────────────────────────────
      async function load() {{
        document.getElementById('content').innerHTML = '<p class="loading">Loading harmony analysis&#8230;</p>';
        try {{
          const trackQ   = currentTrack   ? '?track='   + encodeURIComponent(currentTrack)   : '';
          const sectionQ = currentSection ? (trackQ ? '&' : '?') + 'section=' + encodeURIComponent(currentSection) : '';
          const qs = trackQ + sectionQ;

          const [harmonyResp, keyResp] = await Promise.all([
            apiFetch('/repos/' + encodeURIComponent(repoId) + '/analysis/' + encodeURIComponent(ref) + '/harmony' + qs),
            apiFetch('/repos/' + encodeURIComponent(repoId) + '/analysis/' + encodeURIComponent(ref) + '/key'),
          ]);

          const harmony = harmonyResp.data;
          const key     = keyResp.data;

          // Relative key label
          const keyLabel = harmony.tonic + ' ' + harmony.mode;
          const relKeyLabel = key.relativeKey || '—';
          const altKeys = (key.alternateKeys || [])
            .map(ak => escHtml(ak.tonic + ' ' + ak.mode) + ' (' + (ak.confidence * 100).toFixed(0) + '%)')
            .join(' &bull; ') || '—';

          const filterTrackOpts = ['', 'bass', 'keys', 'guitar', 'drums', 'lead', 'pads']
            .map(t => '<option value="' + t + '"' + (t === currentTrack ? ' selected' : '') + '>'
              + (t || 'All tracks') + '</option>').join('');
          const filterSectionOpts = ['', 'intro', 'verse_1', 'chorus', 'bridge', 'outro']
            .map(s => '<option value="' + s + '"' + (s === currentSection ? ' selected' : '') + '>'
              + (s || 'All sections') + '</option>').join('');

          document.getElementById('content').innerHTML = `
            <div style="margin-bottom:12px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">
              <a href="${{base}}">&larr; Back to repo</a>
              <span style="color:#8b949e;font-size:13px">
                Analysis for <code style="background:#161b22;padding:2px 6px;border-radius:4px">${{escHtml(ref.substring(0,12))}}</code>
              </span>
            </div>

            <!-- Key summary card -->
            <div class="card" style="border-color:#1f6feb">
              <div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:12px">
                <div>
                  <h1 style="margin:0;font-size:26px">
                    &#127929; ${{escHtml(keyLabel)}}
                  </h1>
                  <div style="font-size:14px;color:#8b949e;margin-top:4px">
                    Relative key: <strong style="color:#58a6ff">${{escHtml(relKeyLabel)}}</strong>
                    &bull; Confidence: <strong>${{(harmony.keyConfidence * 100).toFixed(0)}}%</strong>
                  </div>
                  ${{altKeys !== '—' ? '<div style="font-size:12px;color:#8b949e;margin-top:4px">Alternates: ' + altKeys + '</div>' : ''}}
                </div>
                <div style="display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap">
                  <label style="font-size:13px;color:#8b949e;display:flex;align-items:center;gap:6px">
                    Track:
                    <select id="track-sel" onchange="setFilter()" style="min-width:100px">
                      ${{filterTrackOpts}}
                    </select>
                  </label>
                  <label style="font-size:13px;color:#8b949e;display:flex;align-items:center;gap:6px">
                    Section:
                    <select id="section-sel" onchange="setFilter()" style="min-width:100px">
                      ${{filterSectionOpts}}
                    </select>
                  </label>
                </div>
              </div>
            </div>

            <!-- Chord progression timeline -->
            <div class="card">
              <h2 style="margin-bottom:12px">&#127926; Chord Progression Timeline</h2>
              ${{renderChordTimeline(harmony.chordProgression, harmony.totalBeats)}}
            </div>

            <!-- Tension curve -->
            <div class="card">
              <h2 style="margin-bottom:8px">&#128200; Tension Curve</h2>
              <p style="font-size:12px;color:#8b949e;margin-bottom:8px">
                Harmonic tension sampled per beat (0=relaxed, 1=dissonant).
                Red zone = tension &gt; 0.75.
              </p>
              ${{renderTensionCurve(harmony.tensionCurve)}}
              <div style="display:flex;justify-content:space-between;font-size:11px;color:#8b949e;margin-top:4px">
                <span>Beat 0</span>
                <span>Beat ${{harmony.totalBeats}}</span>
              </div>
            </div>

            <!-- Modulation markers -->
            <div class="card">
              <h2 style="margin-bottom:12px">&#127908; Modulation Points</h2>
              ${{renderModulationPoints(harmony.modulationPoints, harmony.totalBeats)}}
            </div>

            <!-- Voice-leading quality -->
            <div class="card">
              <h2 style="margin-bottom:12px">&#127917; Voice-Leading Quality</h2>
              ${{renderVoiceLeading(harmony.chordProgression)}}
            </div>

            <!-- Key history section (commits) -->
            <div class="card">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
                <h2 style="margin:0">&#128337; Key History Across Commits</h2>
                <button class="btn btn-secondary" style="font-size:12px"
                        data-target="history-body" onclick="toggleSection('history-body')">&#9660; Hide</button>
              </div>
              <div id="history-body">
                <div id="key-history-content">
                  <p class="loading">Loading commit history&#8230;</p>
                </div>
              </div>
            </div>
          `;

          // Async-load key history from commits
          loadKeyHistory();

        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('content').innerHTML =
              '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      // ── Key history loader (async after main render) ───────────────────────
      async function loadKeyHistory() {{
        try {{
          const commitsData = await apiFetch(
            '/repos/' + encodeURIComponent(repoId) + '/commits?limit=20'
          );
          const commits = commitsData.commits || [];
          if (commits.length === 0) {{
            document.getElementById('key-history-content').innerHTML =
              '<p class="loading">No commit history available.</p>';
            return;
          }}

          // Fetch harmony for each commit in parallel (limit to 8)
          const recent = commits.slice(0, 8);
          const results = await Promise.allSettled(
            recent.map(c => apiFetch(
              '/repos/' + encodeURIComponent(repoId) + '/analysis/' + encodeURIComponent(c.commitId) + '/harmony'
            ))
          );

          const historyRows = recent.map((c, i) => {{
            const r = results[i];
            if (r.status === 'rejected') return '';
            const h = r.value.data;
            const isCurrent = c.commitId === ref || c.commitId.startsWith(ref) || ref.startsWith(c.commitId);
            return `
              <div class="commit-row" style="${{isCurrent ? 'background:#1f6feb18;border-radius:4px;padding:6px 8px;' : ''}}">
                <a class="commit-sha" href="${{base}}/analysis/${{c.commitId}}/harmony">
                  ${{c.commitId.substring(0,8)}}
                </a>
                <div style="flex:1">
                  <span style="font-size:13px;color:#e6edf3;font-weight:${{isCurrent?'600':'400'}}">
                    ${{escHtml(h.tonic + ' ' + h.mode)}}
                    ${{isCurrent ? '<span class="badge badge-open" style="font-size:10px;margin-left:6px">current</span>' : ''}}
                  </span>
                  <div style="font-size:11px;color:#8b949e">
                    Confidence: ${{(h.keyConfidence * 100).toFixed(0)}}%
                    &bull; ${{escHtml(c.message)}}
                  </div>
                </div>
                <span class="commit-meta">${{fmtDate(c.timestamp)}}</span>
              </div>`;
          }}).filter(Boolean).join('');

          document.getElementById('key-history-content').innerHTML =
            historyRows || '<p class="loading">No comparable commits found.</p>';
        }} catch(e) {{
          if (e.message !== 'auth') {{
            const el = document.getElementById('key-history-content');
            if (el) el.innerHTML = '<p class="error">&#10005; Could not load history: ' + escHtml(e.message) + '</p>';
          }}
        }}
      }}

      // ── Filter handler ────────────────────────────────────────────────────
      function setFilter() {{
        const trackSel   = document.getElementById('track-sel');
        const sectionSel = document.getElementById('section-sel');
        currentTrack   = trackSel   ? trackSel.value   : '';
        currentSection = sectionSel ? sectionSel.value : '';
        load();
      }}

      function toggleSection(id) {{
        const el = document.getElementById(id);
        if (!el) return;
        el.style.display = el.style.display === 'none' ? '' : 'none';
        const btn = document.querySelector('[data-target="' + id + '"]');
        if (btn) btn.textContent = el.style.display === 'none' ? '&#9654; Show' : '&#9660; Hide';
      }}

      load();
    """
    short_ref = ref[:8] if len(ref) >= 8 else ref
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="/musehub/static/tokens.css">
  <link rel="stylesheet" href="/musehub/static/components.css">
  <link rel="stylesheet" href="/musehub/static/layout.css">
  <link rel="stylesheet" href="/musehub/static/icons.css">
  <link rel="stylesheet" href="/musehub/static/music.css">
  <title>Harmony Analysis {short_ref} — Muse Hub</title>
</head>
<body>
  <header>
    <span class="logo">&#127925; Muse Hub</span>
    <span class="breadcrumb">
      <a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> /
      analysis / {short_ref} / harmony
    </span>
  </header>
  <div class="container">
    <div class="token-form" id="token-form" style="display:none">
      <p id="token-msg">Enter your Maestro JWT to browse this repo.</p>
      <input type="password" id="token-input" placeholder="eyJ..." />
      <button class="btn btn-primary" onclick="saveToken()">Save &amp; Load</button>
      &nbsp;
      <button class="btn btn-secondary" onclick="clearToken();location.reload()">Clear</button>
    </div>
    <div id="content"><p class="loading">Loading&#8230;</p></div>
  </div>
  <script src="/musehub/static/musehub.js"></script>
  <script>
    window.addEventListener('DOMContentLoaded', function() {{
      {script}
    }});
  </script>
</body>
</html>"""
    return json_or_html(
        request,
        lambda: HTMLResponse(content=html),
        {"repo_id": repo_id, "ref": ref, "short_ref": short_ref},
    )


@router.get(
    "/{owner}/{repo_slug}/piano-roll/{ref}",
    summary="Muse Hub piano roll — all MIDI tracks",
)
async def piano_roll_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the Canvas-based interactive piano roll for all MIDI tracks at ``ref``.

    The page shell fetches a list of MIDI artifacts at the given ref from the
    ``GET /api/v1/musehub/repos/{repo_id}/objects`` endpoint, then calls
    ``GET /api/v1/musehub/repos/{repo_id}/objects/{id}/parse-midi`` for each
    selected file.  The parsed note data is rendered into a Canvas element via
    ``piano-roll.js``.

    Features:
    - Pitch on Y-axis with a piano keyboard strip
    - Beat grid on X-axis with measure markers
    - Per-track colour coding (design system palette)
    - Velocity mapped to rectangle opacity
    - Zoom controls (horizontal and vertical sliders)
    - Pan via click-drag
    - Hover tooltip: pitch name, velocity, beat position, duration

    No JWT required — HTML shell; JS fetches authed data via localStorage token.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    short_ref = ref[:8] if len(ref) >= 8 else ref
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "short_ref": short_ref,
        "path": None,
        "base_url": base_url,
        "current_page": "piano-roll",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/piano_roll.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/piano-roll/{ref}/{path:path}",
    summary="Muse Hub piano roll — single MIDI track",
)
async def piano_roll_track_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    path: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the Canvas-based piano roll scoped to a single MIDI file ``path``.

    Identical to :func:`piano_roll_page` but restricts the view to one specific
    MIDI artifact identified by its repo-relative path
    (e.g. ``tracks/bass.mid``).  The ``path`` segment is forwarded to the
    template as a JavaScript string; the client-side code resolves the
    matching object ID via the objects list API.

    Useful for per-track deep-dive links from the tree browser or commit
    detail page.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    short_ref = ref[:8] if len(ref) >= 8 else ref
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "short_ref": short_ref,
        "path": path,
        "base_url": base_url,
        "current_page": "piano-roll",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/piano_roll.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/blob/{ref}/{path:path}",
    summary="Muse Hub file blob viewer — music-aware file rendering",
)
async def blob_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    path: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the music-aware blob viewer for a single file at a given ref.

    Dispatches to the appropriate rendering mode based on file extension:
    - .mid/.midi → piano roll preview with "View in Piano Roll" quick link
    - .mp3/.wav/.flac → <audio> player with "Listen" quick link
    - .json → syntax-highlighted, formatted JSON with collapsible sections
    - .webp/.png/.jpg → inline <img> display
    - .xml → syntax-highlighted XML (MusicXML support)
    - Other → hex dump preview with raw download link

    Metadata shown: filename, size, SHA, commit date.
    Raw download button links to /{owner}/{repo_slug}/raw/{ref}/{path}.

    Auth: no JWT required for public repos.  Private-repo auth is
    handled client-side via localStorage JWT (consistent with other
    MuseHub UI pages).
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    filename = path.split("/")[-1] if path else ""
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "file_path": path,
        "filename": filename,
        "base_url": base_url,
        "current_page": "tree",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/blob.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/score/{ref}",
    summary="Muse Hub score renderer — full score, all tracks",
)
async def score_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the sheet music score page for a given commit ref (all tracks).

    Displays all instrument parts as standard music notation rendered via a
    lightweight SVG renderer.  The page fetches quantized notation JSON from
    ``GET /api/v1/musehub/repos/{repo_id}/notation/{ref}`` and draws:

    - Staff lines (treble and bass clefs as appropriate)
    - Key signature and time signature
    - Note heads, stems, flags, ledger lines
    - Accidental markers (sharps and flats)
    - Track/part selector dropdown

    No JWT is required to render the HTML shell.  Auth is handled client-side
    via localStorage JWT, matching all other UI pages.

    For a single-part view use the ``score/{ref}/{path}`` variant which filters
    to one instrument track.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "base_url": base_url,
        "path": "",
        "current_page": "score",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/score.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/activity",
    summary="Muse Hub activity feed — repo-level event stream",
)
async def activity_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the repo-level activity feed page.

    Shows a chronological, paginated event stream for the repo covering:
    commit pushes, PR opens/merges/closes, issue opens/closes, branch and tag
    operations, and recording sessions.  A dropdown filters by event type.

    No JWT is required to render the HTML shell.  Auth is handled client-side
    via localStorage JWT, matching all other UI pages.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_url": base_url,
        "current_page": "activity",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/activity.html", ctx),
        ctx,
    )


@router.get(
    "/{owner}/{repo_slug}/score/{ref}/{path:path}",
    summary="Muse Hub score renderer — single-track part view",
)
async def score_part_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    path: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Render the sheet music score page filtered to a single instrument part.

    Identical to ``score/{ref}`` but the ``path`` segment identifies a specific
    instrument track (e.g. ``piano``, ``bass``, ``guitar``).  The client-side
    renderer pre-selects that track in the part selector on load.

    No JWT is required to render the HTML shell.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    ctx: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "ref": ref,
        "base_url": base_url,
        "path": path,
        "current_page": "score",
    }
    return json_or_html(
        request,
        lambda: templates.TemplateResponse(request, "musehub/pages/score.html", ctx),
        ctx,
    )
