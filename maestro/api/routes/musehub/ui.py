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
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/contour    -- melodic contour analysis
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/tempo      -- tempo analysis
  GET /musehub/ui/{owner}/{repo_slug}/analysis/{ref}/dynamics   -- dynamics analysis

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
    "/{repo_id}/analysis/{ref}",
    response_class=HTMLResponse,
    summary="Muse Hub analysis dashboard -- all musical dimensions at a glance",
)
async def analysis_dashboard_page(repo_id: str, ref: str) -> HTMLResponse:
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
    - Content negotiation: the underlying API at the same path with
      ``Accept: application/json`` returns structured JSON.

    Args:
        repo_id: UUID of the MuseHub repository.
        ref:     Commit SHA or branch name to analyse.
    """
    _DASHBOARD_CSS = """
.analysis-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 14px;
  margin-top: 4px;
}
.analysis-card {
  background: #161b22; border: 1px solid #30363d; border-radius: 8px;
  padding: 16px; display: flex; flex-direction: column; gap: 6px;
  transition: border-color 0.15s, background 0.15s;
  color: inherit;
}
.analysis-card:hover {
  border-color: #58a6ff; background: #1c2128; text-decoration: none;
}
.card-emoji { font-size: 22px; }
.card-dim { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.6px; }
.card-metric { font-size: 18px; font-weight: 700; color: #e6edf3; word-break: break-word; }
.card-sub { font-size: 12px; color: #8b949e; }
.card-spark { font-size: 14px; color: #3fb950; letter-spacing: 1px; margin-top: 2px; }
"""
    script = f"""
      const repoId = {repr(repo_id)};
      const ref    = {repr(ref)};
      const base   = '/musehub/ui/' + repoId;

      function escHtml(s) {{
        if (s === null || s === undefined) return '\u2014';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      const CARD_CONFIG = [
        {{
          id: 'key', emoji: '&#127925;', label: 'Key',
          extract: d => d.tonic && d.mode ? d.tonic + ' ' + d.mode : '\u2014',
          sub: d => d.confidence ? 'confidence: ' + (d.confidence * 100).toFixed(0) + '%' : '',
        }},
        {{
          id: 'tempo', emoji: '&#9201;&#65039;', label: 'Tempo',
          extract: d => d.bpm ? d.bpm + ' BPM' : '\u2014',
          sub: d => d.timeFeel ? 'feel: ' + d.timeFeel : '',
        }},
        {{
          id: 'meter', emoji: '&#127932;', label: 'Meter',
          extract: d => d.timeSignature || '\u2014',
          sub: d => d.isCompound ? 'compound' : 'simple',
        }},
        {{
          id: 'chord-map', emoji: '&#127929;', label: 'Chord Map',
          extract: d => d.totalChords ? d.totalChords + ' chords' : '\u2014',
          sub: d => d.progression && d.progression[0] ? d.progression[0].chord : '',
        }},
        {{
          id: 'dynamics', emoji: '&#128266;', label: 'Dynamics',
          extract: d => d.dynamicEvents && d.dynamicEvents[0] ? d.dynamicEvents[0].split('@')[0] : (d.meanVelocity ? 'vel ' + Math.round(d.meanVelocity) : '\u2014'),
          sub: d => d.dynamicRange ? 'range: ' + d.dynamicRange : '',
        }},
        {{
          id: 'groove', emoji: '&#129345;', label: 'Groove',
          extract: d => d.style ? d.style : '\u2014',
          sub: d => d.grooveScore ? 'score: ' + (d.grooveScore * 100).toFixed(0) + '%' : '',
        }},
        {{
          id: 'emotion', emoji: '&#127917;', label: 'Emotion',
          extract: d => d.primaryEmotion ? d.primaryEmotion : '\u2014',
          sub: d => d.valence !== undefined ? 'valence: ' + (d.valence > 0 ? '+' : '') + d.valence.toFixed(2) : '',
        }},
        {{
          id: 'form', emoji: '&#128203;', label: 'Form',
          extract: d => d.formLabel || '\u2014',
          sub: d => d.sections ? d.sections.length + ' sections' : '',
        }},
        {{
          id: 'motifs', emoji: '&#128257;', label: 'Motifs',
          extract: d => d.totalMotifs !== undefined ? d.totalMotifs + ' pattern' + (d.totalMotifs !== 1 ? 's' : '') : '\u2014',
          sub: d => d.motifs && d.motifs[0] && d.motifs[0].intervals ? 'M01: [' + d.motifs[0].intervals.slice(0,4).join(',') + ']' : '',
        }},
        {{
          id: 'contour', emoji: '&#128200;', label: 'Contour',
          extract: d => d.shape || '\u2014',
          sub: d => d.overallDirection ? d.overallDirection + ' \u2192' : '',
        }},
      ];

      function sparkline(values, width) {{
        if (!values || !values.length) return '';
        const w = width || 8;
        const max = Math.max(...values, 1);
        const bars = [
          '\u2581','\u2582','\u2583','\u2584',
          '\u2585','\u2586','\u2587','\u2588'
        ];
        return values.slice(0, w).map(v => {{
          const pct = Math.round((v / max) * 7);
          return bars[Math.min(pct, 7)];
        }}).join('');
      }}

      function sparklineForDim(id, data) {{
        if (id === 'dynamics' && data.velocityCurve)
          return sparkline(data.velocityCurve.map(e => e.velocity), 12);
        if (id === 'contour' && data.pitchCurve) {{
          const min = Math.min(...data.pitchCurve);
          return sparkline(data.pitchCurve.map(v => v - min + 1), 12);
        }}
        return '';
      }}

      function renderCard(cfg, dimensionEntry) {{
        const d = dimensionEntry ? dimensionEntry.data : null;
        const metric = d ? cfg.extract(d) : '\u2014';
        const sub    = d ? cfg.sub(d) : 'Not yet analyzed';
        const spark  = d ? sparklineForDim(cfg.id, d) : '';
        const href   = base + '/analysis/' + ref + '/' + cfg.id;
        return '<a href="' + href + '" class="analysis-card" style="text-decoration:none">'
          + '<div class="card-emoji">' + cfg.emoji + '</div>'
          + '<div class="card-dim">' + escHtml(cfg.label) + '</div>'
          + '<div class="card-metric">' + escHtml(metric) + '</div>'
          + (sub ? '<div class="card-sub">' + escHtml(sub) + '</div>' : '')
          + (spark ? '<div class="card-spark" aria-hidden="true">' + spark + '</div>' : '')
          + '</a>';
      }}

      async function loadBranches() {{
        try {{
          const data = await apiFetch('/repos/' + repoId + '/branches');
          const branches = data.branches || [];
          const opts = branches.map(b =>
            '<option value="' + escHtml(b.name) + '"' + (b.name === ref ? ' selected' : '') + '>'
            + escHtml(b.name) + '</option>'
          ).join('');
          document.getElementById('ref-sel').innerHTML =
            '<option value="">\u2014 select branch \u2014</option>' + opts;
        }} catch(e) {{ /* branch selector is optional */ }}
      }}

      async function load() {{
        document.getElementById('content').innerHTML =
          '<div style="margin-bottom:12px"><a href="' + base + '">&larr; Back to repo</a></div>'
          + '<div class="card" style="margin-bottom:16px">'
          + '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">'
          + '<h1 style="margin:0">&#127926; Analysis</h1>'
          + '<code style="font-size:13px;background:#0d1117;padding:3px 8px;border-radius:4px;color:#58a6ff">'
          + escHtml(ref.length > 16 ? ref.substring(0,16) + '\u2026' : ref)
          + '</code><span style="flex:1"></span>'
          + '<label style="font-size:13px;color:#8b949e;display:flex;align-items:center;gap:6px">Branch: '
          + '<select id="ref-sel" onchange="if(this.value)location.href=base+\'/analysis/\'+this.value"'
          + ' style="background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:4px 8px;font-size:13px">'
          + '<option value="">\u2014 loading \u2014</option></select></label>'
          + '</div></div>'
          + '<div id="dashboard-grid" class="analysis-grid"><p class="loading">Fetching analysis\u2026</p></div>';

        loadBranches();

        try {{
          const agg = await apiFetch('/repos/' + repoId + '/analysis/' + encodeURIComponent(ref));
          const dimMap = {{}};
          (agg.dimensions || []).forEach(d => {{ dimMap[d.dimension] = d; }});
          const cards = CARD_CONFIG.map(cfg => renderCard(cfg, dimMap[cfg.id])).join('');
          document.getElementById('dashboard-grid').innerHTML = cards;
        }} catch(e) {{
          if (e.message !== 'auth') {{
            document.getElementById('dashboard-grid').innerHTML =
              '<p class="error">&#10005; Could not load analysis: ' + escHtml(e.message) + '</p>';
          }}
        }}
      }}

      load();
    """
    html = _page(
        title=f"Analysis {ref[:12]}",
        breadcrumb=(
            f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / '
            f"analysis / {ref[:8]}"
        ),
        body_script=script,
        extra_css=_DASHBOARD_CSS,
    )
    return HTMLResponse(content=html)


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
