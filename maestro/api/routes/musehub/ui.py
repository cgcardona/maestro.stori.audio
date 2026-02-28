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
  GET /musehub/ui/{owner}/{repo_slug}/tree/{ref}                -- file tree browser (repo root)
  GET /musehub/ui/{owner}/{repo_slug}/tree/{ref}/{path}         -- file tree browser (subdirectory)
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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response as StarletteResponse

from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.db import get_db
from maestro.models.musehub import (
    BranchDetailListResponse,
    CommitListResponse,
    CommitResponse,
    RepoResponse,
    TagListResponse,
    TagResponse,
)
from maestro.services import musehub_releases
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

    Content negotiation:
    - ``?format=json`` or ``Accept: application/json`` → full ``RepoResponse`` with camelCase keys.
    - Everything else → HTML home page via ``repo_home.html`` template.

    One URL, two audiences — agents get structured data, humans get rich HTML.
    """
    repo, base_url = await _resolve_repo_full(owner, repo_slug, db)
    return await negotiate_response(
        request=request,
        template_name="musehub/pages/repo_home.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": str(repo.repo_id),
            "base_url": base_url,
            "current_page": "home",
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
    limit: int = Query(50, ge=1, le=200, description="Max commits to return"),
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
) -> StarletteResponse:
    """Render the commits list page or return structured commit data as JSON.

    HTML (default): renders the repo commits list via Jinja2.
    JSON (``Accept: application/json`` or ``?format=json``): returns
    ``CommitListResponse`` with the newest commits first.

    Agents use this to inspect a repo's commit history without navigating
    a separate ``/api/v1/...`` endpoint.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    commits, total = await musehub_repository.list_commits(
        db, repo_id, branch=branch, limit=limit
    )
    return await negotiate_response(
        request=request,
        template_name="musehub/pages/repo.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "commits",
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
    """Render the commit detail page or return structured commit data as JSON.

    HTML (default): metadata + artifact browser rendered via Jinja2.
    JSON (``Accept: application/json`` or ``?format=json``): returns the full
    ``CommitResponse`` Pydantic model with camelCase keys, or a minimal context
    dict if the commit is not yet in the DB (not yet synced).

    Artifacts are displayed by extension:
    - ``.webp/.png/.jpg`` → inline ``<img>``
    - ``.mp3/.ogg/.wav``  → ``<audio controls>`` player
    - other              → download link
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    commit = await musehub_repository.get_commit(db, repo_id, commit_id)
    return await negotiate_response(
        request=request,
        template_name="musehub/pages/commit.html",
        context={
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "commit_id": commit_id,
            "base_url": base_url,
            "current_page": "commits",
            "breadcrumb_data": _breadcrumbs(
                (owner, f"/musehub/ui/{owner}"),
                (repo_slug, base_url),
                ("commits", base_url),
                (commit_id[:8], ""),
            ),
        },
        templates=templates,
        json_data=commit,
        format_param=format,
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
    "/{repo_id}/analysis/{ref}/emotion",
    response_class=HTMLResponse,
    summary="Muse Hub emotion map page — energy/valence/tension/darkness across time and commits",
)
async def emotion_map_page(repo_id: str, ref: str) -> HTMLResponse:
    """Render the emotion map page for a Muse repo ref.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/analysis/{ref}/emotion-map``
    and visualises the four emotion vectors (energy, valence, tension, darkness)
    as SVG line/area charts showing:

    - **Evolution chart**: all four dimensions sampled beat-by-beat within this ref.
    - **Trajectory chart**: per-commit summary vectors across the recent commit history.
    - **Drift list**: Euclidean drift distances between consecutive commits.
    - **Narrative**: auto-generated text describing the emotional journey.
    - **Filters**: track and section dropdowns that reload the data on change.

    Auth is handled client-side via localStorage JWT (same as all other UI pages).
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const ref    = {repr(ref)};
      const base   = '/musehub/ui/' + repoId;

      function escHtml(s) {{
        if (s === null || s === undefined) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      // ── Colour palette for the four emotion axes ───────────────────────
      const AXIS_COLORS = {{
        energy:   '#f0883e',
        valence:  '#3fb950',
        tension:  '#f85149',
        darkness: '#bc8cff',
      }};
      const AXES = ['energy', 'valence', 'tension', 'darkness'];

      // ── SVG line chart helper ──────────────────────────────────────────
      // data: Array of {{x, values: {{energy, valence, tension, darkness}}}}
      function buildLineChart(data, xLabel, width, height) {{
        if (!data || data.length === 0) return '<p class="loading">No data.</p>';
        const pad = {{ t: 20, r: 20, b: 36, l: 44 }};
        const w = width - pad.l - pad.r;
        const h = height - pad.t - pad.b;
        const xs = data.map(d => d.x);
        const xMin = xs[0], xMax = xs[xs.length - 1];
        const xScale = v => w * (xMax === xMin ? 0.5 : (v - xMin) / (xMax - xMin));
        const yScale = v => h * (1 - Math.max(0, Math.min(1, v)));

        let paths = '';
        AXES.forEach(axis => {{
          const pts = data.map(d => `${{xScale(d.x).toFixed(1)}},${{yScale(d.values[axis]).toFixed(1)}}`).join(' ');
          paths += `<polyline points="${{pts}}" fill="none" stroke="${{AXIS_COLORS[axis]}}" stroke-width="2" opacity="0.85"/>`;
          // Dots at first and last
          if (data.length > 0) {{
            const first = data[0], last = data[data.length - 1];
            paths += `<circle cx="${{xScale(first.x).toFixed(1)}}" cy="${{yScale(first.values[axis]).toFixed(1)}}" r="3" fill="${{AXIS_COLORS[axis]}}"/>`;
            paths += `<circle cx="${{xScale(last.x).toFixed(1)}}" cy="${{yScale(last.values[axis]).toFixed(1)}}" r="3" fill="${{AXIS_COLORS[axis]}}"/>`;
          }}
        }});

        // Y gridlines at 0, 0.25, 0.5, 0.75, 1.0
        let grid = '';
        [0, 0.25, 0.5, 0.75, 1.0].forEach(v => {{
          const y = yScale(v).toFixed(1);
          grid += `<line x1="0" y1="${{y}}" x2="${{w}}" y2="${{y}}" stroke="#30363d" stroke-width="1"/>`;
          grid += `<text x="-4" y="${{y}}" text-anchor="end" font-size="10" fill="#8b949e" dominant-baseline="middle">${{v.toFixed(2)}}</text>`;
        }});

        // X axis ticks (up to 5)
        const ticks = data.filter((_, i) => i === 0 || i === data.length - 1 || i % Math.max(1, Math.floor(data.length / 4)) === 0);
        let xTicks = '';
        ticks.forEach(d => {{
          const x = xScale(d.x).toFixed(1);
          xTicks += `<line x1="${{x}}" y1="0" x2="${{x}}" y2="${{h}}" stroke="#21262d" stroke-width="1"/>`;
          xTicks += `<text x="${{x}}" y="${{h + 16}}" text-anchor="middle" font-size="10" fill="#8b949e">${{typeof d.x === 'number' ? d.x.toFixed(0) : escHtml(String(d.x))}}</text>`;
        }});

        return `
          <svg width="${{width}}" height="${{height}}" style="overflow:visible">
            <g transform="translate(${{pad.l}},${{pad.t}})">
              ${{grid}}${{xTicks}}${{paths}}
              <text x="${{w/2}}" y="${{h + 32}}" text-anchor="middle" font-size="11" fill="#8b949e">${{escHtml(xLabel)}}</text>
            </g>
          </svg>`;
      }}

      // ── Legend HTML ────────────────────────────────────────────────────
      const legendHtml = AXES.map(axis =>
        `<span style="display:inline-flex;align-items:center;gap:6px;margin-right:16px">
          <svg width="20" height="4"><rect width="20" height="4" rx="2" fill="${{AXIS_COLORS[axis]}}"/></svg>
          <span style="font-size:13px;color:#c9d1d9;text-transform:capitalize">${{axis}}</span>
        </span>`
      ).join('');

      // ── Main load function ─────────────────────────────────────────────
      async function load(track, section) {{
        document.getElementById('content').innerHTML = '<p class="loading">Loading emotion map&#8230;</p>';
        try {{
          let url = '/repos/' + repoId + '/analysis/' + ref + '/emotion-map';
          const params = new URLSearchParams();
          if (track)   params.set('track', track);
          if (section) params.set('section', section);
          if ([...params].length) url += '?' + params.toString();

          const data = await apiFetch(url);

          // ── Evolution chart data ───────────────────────────────────────
          const evoData = (data.evolution || []).map(p => ({{
            x: p.beat,
            values: p.vector,
          }}));

          // ── Trajectory chart data ──────────────────────────────────────
          const traj = data.trajectory || [];
          const trajData = traj.map((c, i) => ({{
            x: i,
            values: c.vector,
          }}));

          // ── Drift rows ─────────────────────────────────────────────────
          const driftRows = (data.drift || []).map(d => `
            <div style="display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid #21262d">
              <code style="font-size:12px;color:#58a6ff">${{escHtml(d.fromCommit.substring(0,8))}}</code>
              <span style="color:#8b949e">&#8594;</span>
              <code style="font-size:12px;color:#58a6ff">${{escHtml(d.toCommit.substring(0,8))}}</code>
              <span style="flex:1"></span>
              <span class="badge" style="background:#21262d;color:#c9d1d9">
                &#916; ${{d.drift.toFixed(3)}}
              </span>
              <span class="label" style="text-transform:capitalize">${{escHtml(d.dominantChange)}}</span>
            </div>`).join('');

          // ── Trajectory commit list ─────────────────────────────────────
          const trajCommits = traj.map((c, i) => `
            <div style="display:flex;align-items:center;gap:12px;padding:6px 0;border-bottom:1px solid #21262d">
              <span style="font-size:12px;color:#8b949e;min-width:20px;text-align:right">${{i + 1}}</span>
              <code style="font-size:12px;color:#58a6ff">${{escHtml(c.commitId.substring(0,8))}}</code>
              <span style="flex:1;font-size:13px">${{escHtml(c.message)}}</span>
              <span class="label" style="text-transform:capitalize">${{escHtml(c.primaryEmotion)}}</span>
            </div>`).join('');

          // ── Render all sections ────────────────────────────────────────
          document.getElementById('content').innerHTML = `
            <div style="margin-bottom:12px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">
              <a href="${{base}}">&larr; Back to repo</a>
              <span style="color:#8b949e;font-size:13px">ref: <code>${{escHtml(ref.substring(0,16))}}</code></span>
            </div>

            <!-- Filters -->
            <div class="card" style="margin-bottom:16px">
              <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
                <span style="font-size:13px;color:#8b949e">Filters:</span>
                <label style="font-size:13px;color:#8b949e;display:flex;align-items:center;gap:6px">
                  Track:
                  <input id="filter-track" type="text" placeholder="e.g. bass" value="${{escHtml(track||'')}}"
                    style="background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:4px 8px;font-size:13px;width:120px"/>
                </label>
                <label style="font-size:13px;color:#8b949e;display:flex;align-items:center;gap:6px">
                  Section:
                  <input id="filter-section" type="text" placeholder="e.g. chorus" value="${{escHtml(section||'')}}"
                    style="background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:4px 8px;font-size:13px;width:120px"/>
                </label>
                <button class="btn btn-secondary" style="font-size:12px"
                        onclick="load(document.getElementById('filter-track').value.trim()||null, document.getElementById('filter-section').value.trim()||null)">
                  Apply
                </button>
              </div>
            </div>

            <!-- Source attribution + source badge -->
            <div class="card" style="border-color:#1f6feb;margin-bottom:16px">
              <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
                <span style="font-size:16px">&#127925;</span>
                <h2 style="margin:0;font-size:16px">Emotion Map</h2>
                <span class="badge" style="background:#21262d;color:#c9d1d9;text-transform:capitalize">
                  source: ${{escHtml(data.source)}}
                </span>
                <span style="flex:1"></span>
                <span style="font-size:12px;color:#8b949e">${{data.evolution.length}} beats &bull; ${{traj.length}} commits</span>
              </div>
              <p style="font-size:14px;color:#8b949e;margin-top:10px;line-height:1.6">${{escHtml(data.narrative)}}</p>
            </div>

            <!-- Legend -->
            <div style="margin-bottom:12px;padding:8px 12px;background:#161b22;border:1px solid #30363d;border-radius:6px;display:flex;flex-wrap:wrap;gap:4px">
              ${{legendHtml}}
            </div>

            <!-- Evolution chart -->
            <div class="card" style="margin-bottom:16px;overflow-x:auto">
              <h2 style="margin-bottom:12px">&#128200; Emotion Evolution (beat-by-beat)</h2>
              <div id="evo-chart">
                ${{buildLineChart(evoData, 'Beat', Math.max(600, Math.min(900, evoData.length * 18)), 200)}}
              </div>
            </div>

            <!-- Trajectory chart -->
            <div class="card" style="margin-bottom:16px;overflow-x:auto">
              <h2 style="margin-bottom:12px">&#128198; Cross-commit Emotion Trajectory</h2>
              <div id="traj-chart">
                ${{buildLineChart(trajData, 'Commit index', Math.max(400, traj.length * 80), 180)}}
              </div>
              <div style="margin-top:12px">${{trajCommits}}</div>
            </div>

            <!-- Drift distances -->
            <div class="card" style="margin-bottom:16px">
              <h2 style="margin-bottom:12px">&#8645; Emotion Drift Between Commits</h2>
              ${{driftRows || '<p class="loading">No drift data — fewer than 2 commits in trajectory.</p>'}}
            </div>

            <!-- Summary vector -->
            <div class="card">
              <h2 style="margin-bottom:12px">&#128301; Summary Vector (mean across evolution)</h2>
              <div class="meta-row">
                ${{AXES.map(axis => `
                  <div class="meta-item">
                    <span class="meta-label" style="color:${{AXIS_COLORS[axis]}};text-transform:capitalize">${{axis}}</span>
                    <span class="meta-value">${{(data.summaryVector[axis] * 100).toFixed(1)}}%</span>
                    <div style="margin-top:4px;height:6px;background:#21262d;border-radius:3px;width:120px">
                      <div style="height:6px;background:${{AXIS_COLORS[axis]}};border-radius:3px;width:${{(data.summaryVector[axis] * 100).toFixed(1)}}%"></div>
                    </div>
                  </div>`).join('')}}
              </div>
            </div>`;
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('content').innerHTML =
              '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      load(null, null);
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
  <title>Emotion Map {short_ref} — Muse Hub</title>
</head>
<body>
  <header>
    <span class="logo">&#127925; Muse Hub</span>
    <span class="breadcrumb">
      <a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> /
      analysis / {short_ref} / emotion
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
    return HTMLResponse(content=html)


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


@router.get(
    "/{owner}/{repo_slug}/tree/{ref}",
    response_class=HTMLResponse,
    summary="Muse Hub file tree browser — repo root",
)
async def tree_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
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
    return templates.TemplateResponse(
        request,
        "musehub/pages/tree.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "ref": ref,
            "dir_path": "",
            "base_url": base_url,
            "current_page": "tree",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/tree/{ref}/{path:path}",
    response_class=HTMLResponse,
    summary="Muse Hub file tree browser — subdirectory",
)
async def tree_subdir_page(
    request: Request,
    owner: str,
    repo_slug: str,
    ref: str,
    path: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the file tree browser for a subdirectory at a given ref.

    Behaves identically to ``tree_page`` but scoped to the subdirectory
    identified by ``path`` (e.g. "tracks", "tracks/stems").  The breadcrumb
    expands to show each path segment as a clickable link.

    Files are clickable and navigate to the blob viewer:
    /{owner}/{repo_slug}/blob/{ref}/{path}
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    return templates.TemplateResponse(
        request,
        "musehub/pages/tree.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "ref": ref,
            "dir_path": path,
            "base_url": base_url,
            "current_page": "tree",
        },
    )


@router.get(
    "/{owner}/{repo_slug}/groove-check",
    response_class=HTMLResponse,
    summary="Muse Hub groove check page",
)
async def groove_check_page(
    request: Request,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
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
    return templates.TemplateResponse(
        request,
        "musehub/pages/groove_check.html",
        {
            "owner": owner,
            "repo_slug": repo_slug,
            "repo_id": repo_id,
            "base_url": base_url,
            "current_page": "groove-check",
        },
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
    response_class=HTMLResponse,
    summary="Muse Hub form and structure page",
)
async def form_structure_page(
    request: Request,
    repo_id: str,
    ref: str,
) -> HTMLResponse:
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
    return templates.TemplateResponse(
        request,
        "musehub/pages/form_structure.html",
        {
            "repo_id": repo_id,
            "ref": ref,
            "short_ref": short_ref,
        },
    )


@router.get(
    "/{repo_id}/analysis/{ref}/harmony",
    response_class=HTMLResponse,
    summary="Muse Hub harmony analysis page",
)
async def harmony_analysis_page(repo_id: str, ref: str) -> HTMLResponse:
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
    return HTMLResponse(content=html)

