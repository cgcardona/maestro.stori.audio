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
  GET /musehub/ui/{owner}/{repo_slug}/commits/{commit_id}       -- commit detail + artifacts
  GET /musehub/ui/{owner}/{repo_slug}/commits/{commit_id}/diff  -- musical diff view
  GET /musehub/ui/{owner}/{repo_slug}/graph                     -- interactive DAG commit graph
  GET /musehub/ui/{owner}/{repo_slug}/pulls                     -- pull request list
  GET /musehub/ui/{owner}/{repo_slug}/pulls/{pr_id}             -- PR detail + merge button
  GET /musehub/ui/{owner}/{repo_slug}/issues                    -- issue list
  GET /musehub/ui/{owner}/{repo_slug}/issues/{number}           -- issue detail + close button
  GET /musehub/ui/{owner}/{repo_slug}/context/{ref}             -- AI context viewer
  GET /musehub/ui/{owner}/{repo_slug}/credits                   -- dynamic credits (liner notes)
  GET /musehub/ui/{owner}/{repo_slug}/embed/{ref}               -- iframe-safe audio player
  GET /musehub/ui/{owner}/{repo_slug}/search                    -- in-repo search (4 modes)
  GET /musehub/ui/{owner}/{repo_slug}/divergence                -- branch divergence radar chart
  GET /musehub/ui/{owner}/{repo_slug}/timeline                  -- chronological SVG timeline
  GET /musehub/ui/{owner}/{repo_slug}/releases                  -- release list
  GET /musehub/ui/{owner}/{repo_slug}/releases/{tag}            -- release detail + downloads
  GET /musehub/ui/{owner}/{repo_slug}/sessions                  -- recording session log
  GET /musehub/ui/{owner}/{repo_slug}/sessions/{id}             -- session detail
  GET /musehub/ui/{owner}/{repo_slug}/insights                  -- repo insights dashboard
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}            -- analysis dashboard (all 10 dimensions at a glance)
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/contour    -- melodic contour analysis
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/tempo      -- tempo analysis
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/dynamics   -- dynamics analysis
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/motifs     -- motif browser (recurring patterns, transformations)

These routes require NO JWT auth -- they return HTML shells whose embedded
JavaScript fetches data from the authed JSON API (``/api/v1/musehub/...``)
using a token stored in ``localStorage``.

The embed route sets ``X-Frame-Options: ALLOWALL`` for cross-origin iframe use.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi import status as http_status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db import get_db
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])

# Fixed-path routes registered BEFORE the /{owner}/{repo_slug} wildcard to
# prevent /explore, /trending, and /users/* from being shadowed.
fixed_router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_url(owner: str, repo_slug: str) -> str:
    """Return the canonical UI base URL for a repo."""
    return f"/musehub/ui/{owner}/{repo_slug}"


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


# ---------------------------------------------------------------------------
# Fixed-path routes (registered before wildcard routes in main.py)
# ---------------------------------------------------------------------------


@fixed_router.get("/feed", response_class=HTMLResponse, summary="Muse Hub activity feed")
async def feed_page(request: Request) -> HTMLResponse:
    """Render the activity feed page — events from followed users and watched repos."""
    return templates.TemplateResponse(request, "musehub/pages/feed.html", {"title": "Feed"})


@fixed_router.get("/search", response_class=HTMLResponse, summary="Muse Hub global search page")
async def global_search_page(request: Request, q: str = "", mode: str = "keyword") -> HTMLResponse:
    """Render the global cross-repo search page.

    Query params ``q`` and ``mode`` are pre-filled into the search form so
    that shared URLs land with the last query already populated.  Values are
    sanitised client-side before being injected into the DOM (XSS safe).
    """
    safe_q = q.replace("'", "\\'").replace('"', '\\"').replace("\n", "").replace("\r", "")
    safe_mode = mode if mode in ("keyword", "pattern") else "keyword"
    return templates.TemplateResponse(
        request,
        "musehub/pages/global_search.html",
        {
            "initial_q": safe_q,
            "initial_mode": safe_mode,
        },
    )


@fixed_router.get("/explore", response_class=HTMLResponse, summary="Muse Hub explore page")
async def explore_page(request: Request) -> HTMLResponse:
    """Render the explore/discover page -- a filterable grid of all public repos.

    No JWT required.  Fetches from the public
    ``GET /api/v1/musehub/discover/repos`` endpoint.  Filter state lives in
    query params so results are bookmarkable.
    """
    return templates.TemplateResponse(
        request,
        "musehub/explore_base.html",
        {
            "title": "Explore",
            "breadcrumb": "Explore",
            "default_sort": "created",
        },
    )


@fixed_router.get("/trending", response_class=HTMLResponse, summary="Muse Hub trending page")
async def trending_page(request: Request) -> HTMLResponse:
    """Render the trending page -- public repos sorted by star count.

    Identical shell to the explore page but pre-selects sort=stars so the
    most-starred compositions appear first.
    """
    return templates.TemplateResponse(
        request,
        "musehub/explore_base.html",
        {
            "title": "Trending",
            "breadcrumb": "Trending",
            "default_sort": "stars",
        },
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
    response_class=HTMLResponse,
    summary='Muse Hub user profile page',
)
async def profile_page(request: Request, username: str) -> HTMLResponse:
    """Render the public user profile page.

    Displays bio, avatar, pinned repos, all public repos with last-activity,
    a GitHub-style contribution heatmap, and aggregated session credits.
    Auth is handled client-side -- the profile itself is public.
    """
    return templates.TemplateResponse(
        request,
        "musehub/pages/profile.html",
        {
            "title": f"@{username}",
            "username": username,
        },
    )


# ---------------------------------------------------------------------------
# Repo-scoped pages
# ---------------------------------------------------------------------------


@router.get(
    "/{owner}/{repo_slug}",
    response_class=HTMLResponse,
    summary="Muse Hub repo landing page",
)
async def repo_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the repo landing page: branch selector + newest 20 commits.

    Resolves owner+slug to repo_id server-side; the JS then uses the
    internal repo_id for API calls.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/repo.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "commits",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/commits/{commit_id}",
    response_class=HTMLResponse,
    summary="Muse Hub commit detail page",
)
async def commit_page(
    request: Request,
    owner: str,
    repo_slug: str,
    commit_id: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the commit detail page: metadata + artifact browser.

    Artifacts are displayed by extension:
    - ``.webp/.png/.jpg`` → inline ``<img>``
    - ``.mp3/.ogg/.wav``  → ``<audio controls>`` player
    - other              → download link
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/commit.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "commit_id": commit_id,
            "base_url": base_url,
            "current_page": "commits",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/commits/{commit_id}/diff",
    response_class=HTMLResponse,
    summary="Muse Hub musical diff view",
)
async def diff_page(
    request: Request,
    owner: str,
    repo_slug: str,
    commit_id: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the musical diff between a commit and its parent.

    Shows key/tempo/time-signature deltas, tracks added/removed/modified,
    and side-by-side artifact comparison. Fetches commit and parent metadata
    from the API client-side.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/diff.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "commit_id": commit_id,
            "base_url": base_url,
            "current_page": "commits",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/graph",
    response_class=HTMLResponse,
    summary="Muse Hub interactive DAG commit graph",
)
async def graph_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the interactive DAG commit graph.

    Client-side SVG renderer with branch colour-coding, merge-commit diamonds,
    zoom/pan, hover popovers, and click-to-navigate.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/graph.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "graph",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/pulls",
    response_class=HTMLResponse,
    summary="Muse Hub pull request list page",
)
async def pr_list_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the PR list page with open/all state filter."""
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/pr_list.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "pulls",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/pulls/{pr_id}",
    response_class=HTMLResponse,
    summary="Muse Hub PR detail page",
)
async def pr_detail_page(
    request: Request,
    owner: str,
    repo_slug: str,
    pr_id: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the PR detail page with merge button.

    The merge button calls
    ``POST /api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/merge``
    and reloads the page on success.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/pr_detail.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "pr_id": pr_id,
            "base_url": base_url,
            "current_page": "pulls",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/issues",
    response_class=HTMLResponse,
    summary="Muse Hub issue list page",
)
async def issue_list_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the issue list page with open/closed/all state filter."""
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/issue_list.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "issues",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/context/{ref}",
    response_class=HTMLResponse,
    summary="Muse Hub AI context viewer",
)
async def context_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the AI context viewer for a given commit ref.

    Shows the MuseHubContextResponse as a structured human-readable document:
    musical state, history summary, missing elements, suggestions, and raw JSON.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/context.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "ref": ref,
            "base_url": base_url,
            "current_page": "analysis",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/issues/{number}",
    response_class=HTMLResponse,
    summary="Muse Hub issue detail page",
)
async def issue_detail_page(
    request: Request,
    owner: str,
    repo_slug: str,
    number: int,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the issue detail page with close button.

    The close button calls
    ``POST /api/v1/musehub/repos/{repo_id}/issues/{number}/close``
    and reloads the page on success.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/issue_detail.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "issue_number": number,
            "base_url": base_url,
            "current_page": "issues",
        },
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
    "/{owner}/{repo_slug}/credits",
    response_class=HTMLResponse,
    summary="Muse Hub dynamic credits page",
)
async def credits_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the dynamic credits page -- album liner notes for the repo.

    Displays every contributor with session count, inferred roles, and activity
    timeline.  Embeds ``<script type="application/ld+json">`` for machine-readable
    attribution using schema.org ``MusicComposition`` vocabulary.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/credits.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "credits",
        },
    )



@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}",
    response_class=HTMLResponse,
    summary="Muse Hub analysis dashboard -- all musical dimensions at a glance",
)
async def analysis_dashboard_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
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
    return templates.TemplateResponse(
        request,
        "musehub/pages/analysis.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "ref": ref,
            "base_url": base_url,
            "current_page": "analysis",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/search",
    response_class=HTMLResponse,
    summary="Muse Hub in-repo search page",
)
async def search_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the in-repo search page with four mode tabs.

    Modes:
    - Musical Properties (``mode=property``) -- filter by harmony/rhythm/etc.
    - Natural Language (``mode=ask``) -- free-text question over commit history.
    - Keyword (``mode=keyword``) -- keyword overlap scored search.
    - Pattern (``mode=pattern``) -- substring match against messages and branches.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/search.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "search",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}/motifs",
    response_class=HTMLResponse,
    summary="Muse Hub motif browser page",
)
async def motifs_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
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
    return templates.TemplateResponse(
        request,
        "musehub/pages/motifs.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "ref": ref,
            "base_url": base_url,
            "current_page": "analysis",
        },
    )




@router.get(
    "/{owner}/{repo_slug}/divergence",
    response_class=HTMLResponse,
    summary="Muse Hub divergence visualization page",
)
async def divergence_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the divergence visualization: radar chart + dimension detail panels.

    Compares two branches across five musical dimensions
    (melodic/harmonic/rhythmic/structural/dynamic).
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/divergence.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "analysis",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/timeline",
    response_class=HTMLResponse,
    summary="Muse Hub timeline page",
)
async def timeline_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the layered chronological timeline visualisation.

    Four independently toggleable layers: commits, emotion line chart,
    section markers, and track add/remove markers.  Includes a time
    scrubber and zoom controls (day/week/month/all-time).
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/timeline.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "timeline",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/releases",
    response_class=HTMLResponse,
    summary="Muse Hub release list page",
)
async def release_list_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the release list page: all published versions newest first."""
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/release_list.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "releases",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/releases/{tag}",
    response_class=HTMLResponse,
    summary="Muse Hub release detail page",
)
async def release_detail_page(
    request: Request,
    owner: str,
    repo_slug: str,
    tag: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the release detail page: title, release notes, download packages.

    Download packages (MIDI bundle, stems, MP3, MusicXML, metadata) are
    rendered as download cards; unavailable packages show a "not available"
    indicator.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/release_detail.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "tag": tag,
            "base_url": base_url,
            "current_page": "releases",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/sessions",
    response_class=HTMLResponse,
    summary="Muse Hub session log page",
)
async def sessions_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the session log page -- all recording sessions newest first.

    Active sessions are highlighted with a live indicator at the top of the list.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/sessions.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "sessions",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/sessions/{session_id}",
    response_class=HTMLResponse,
    summary="Muse Hub session detail page",
)
async def session_detail_page(
    request: Request,
    owner: str,
    repo_slug: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the full session detail page.

    Shows metadata, participants with session-count badges, commits made during
    the session, and closing notes.  Renders a 404 message inline if the API
    returns 404, so agents can distinguish missing sessions from server errors.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/session_detail.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "session_id": session_id,
            "base_url": base_url,
            "current_page": "sessions",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/insights",
    response_class=HTMLResponse,
    summary="Muse Hub repo insights dashboard",
)
async def insights_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the repo insights dashboard.

    Shows commit frequency heatmap, contributor breakdown, musical evolution
    timeline (key/BPM/energy across commits), branch activity, and download
    statistics.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/insights.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "insights",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}/contour",
    response_class=HTMLResponse,
    summary="Muse Hub melodic contour analysis page",
)
async def contour_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the melodic contour analysis page for a Muse commit ref.

    Visualises per-track melodic shapes, tessitura, and cross-commit contour
    comparison via a pitch-curve line graph in SVG.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/contour.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "ref": ref,
            "base_url": base_url,
            "current_page": "analysis",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}/tempo",
    response_class=HTMLResponse,
    summary="Muse Hub tempo analysis page",
)
async def tempo_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the tempo analysis page for a Muse commit ref.

    Displays BPM, time feel, stability, and a timeline of tempo change events.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/tempo.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "ref": ref,
            "base_url": base_url,
            "current_page": "analysis",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/analysis/{ref}/dynamics",
    response_class=HTMLResponse,
    summary="Muse Hub dynamics analysis page",
)
async def dynamics_analysis_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the dynamics analysis page for a Muse commit ref.

    Visualises velocity profiles, arc classifications, and per-track loudness
    so a mixing engineer can spot dynamic imbalances without running the CLI.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/dynamics.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "ref": ref,
            "base_url": base_url,
            "current_page": "analysis",
        },
    )
