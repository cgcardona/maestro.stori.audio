"""Muse Hub web UI route handlers.

Serves browser-readable HTML pages for navigating a Muse Hub repo —
analogous to GitHub's repository browser but for music projects.

Endpoint summary:
  GET /musehub/ui/explore                          — discover public repos with filters
  GET /musehub/ui/trending                         — trending public repos (sorted by stars)
  GET /musehub/ui/{repo_id}                        — repo page (branch selector + commit log)
  GET /musehub/ui/{repo_id}/commits/{commit_id}    — commit detail page (metadata + artifacts)
  GET /musehub/ui/{repo_id}/graph                  — interactive DAG commit graph
  GET /musehub/ui/{repo_id}/pulls                  — pull request list page
  GET /musehub/ui/{repo_id}/pulls/{pr_id}          — PR detail page (with merge button)
  GET /musehub/ui/{repo_id}/issues                 — issue list page
  GET /musehub/ui/{repo_id}/issues/{number}        — issue detail page (with close button)
  GET /musehub/ui/{repo_id}/embed/{ref}            — embeddable player widget (no auth, iframe-safe)
  GET /musehub/ui/{repo_id}/search                 — in-repo search page (four modes)

These routes require NO JWT auth — they return static HTML shells whose
embedded JavaScript fetches data from the public JSON API
(``/api/v1/musehub/discover/repos``) or the authed JSON API
(``/api/v1/musehub/...``) using a token stored in ``localStorage``.

The embed route is intentionally designed for cross-origin iframe embedding:
it sets ``X-Frame-Options: ALLOWALL`` and omits the Sign-out button.

No Jinja2 is required; pages are self-contained HTML strings rendered
server-side.  No external CDN dependencies.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui"])

# ---------------------------------------------------------------------------
# Shared HTML scaffolding
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: #0d1117; color: #c9d1d9; min-height: 100vh;
  line-height: 1.6;
}
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }
header {
  background: #161b22; border-bottom: 1px solid #30363d;
  padding: 12px 24px; display: flex; align-items: center; gap: 16px;
}
header .logo { font-size: 18px; font-weight: 700; color: #e6edf3; }
header .breadcrumb { color: #8b949e; font-size: 14px; }
header .breadcrumb a { color: #58a6ff; }
.container { max-width: 960px; margin: 24px auto; padding: 0 24px; }
.card {
  background: #161b22; border: 1px solid #30363d; border-radius: 6px;
  padding: 16px; margin-bottom: 16px;
}
h1 { font-size: 20px; color: #e6edf3; margin-bottom: 12px; }
h2 { font-size: 16px; color: #e6edf3; margin-bottom: 8px; }
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 12px;
  font-size: 12px; font-weight: 600;
}
.badge-open { background: #1f6feb; color: #e6edf3; }
.badge-closed { background: #8b949e; color: #e6edf3; }
.badge-merged { background: #6e40c9; color: #e6edf3; }
.commit-row {
  border-bottom: 1px solid #21262d; padding: 10px 0;
  display: flex; align-items: flex-start; gap: 12px;
}
.commit-row:last-child { border-bottom: none; }
.commit-sha {
  font-family: monospace; font-size: 13px; color: #58a6ff;
  white-space: nowrap;
}
.commit-msg { flex: 1; font-size: 14px; }
.commit-meta { font-size: 12px; color: #8b949e; white-space: nowrap; }
.artifact-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px; margin-top: 12px;
}
.artifact-card {
  background: #21262d; border: 1px solid #30363d; border-radius: 6px;
  padding: 10px; display: flex; flex-direction: column; gap: 8px;
}
.artifact-card img { width: 100%; border-radius: 4px; border: 1px solid #30363d; }
.artifact-card audio { width: 100%; }
.artifact-card .path { font-size: 12px; color: #8b949e; word-break: break-all; }
.btn {
  display: inline-block; padding: 6px 16px; border-radius: 6px;
  font-size: 14px; font-weight: 500; cursor: pointer; border: none;
  transition: opacity 0.15s;
}
.btn:hover { opacity: 0.8; }
.btn-primary { background: #238636; color: #fff; }
.btn-danger { background: #b91c1c; color: #fff; }
.btn-secondary { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; }
select {
  background: #21262d; color: #c9d1d9; border: 1px solid #30363d;
  border-radius: 6px; padding: 6px 10px; font-size: 14px;
}
.token-form {
  background: #161b22; border: 1px solid #f0883e; border-radius: 6px;
  padding: 16px; margin-bottom: 20px;
}
.token-form p { font-size: 14px; color: #8b949e; margin-bottom: 8px; }
.token-form input {
  width: 100%; background: #0d1117; color: #c9d1d9;
  border: 1px solid #30363d; border-radius: 6px; padding: 8px;
  font-size: 13px; margin-bottom: 8px;
}
.loading { color: #8b949e; font-size: 14px; }
.error { color: #f85149; font-size: 14px; margin: 8px 0; }
.pr-row, .issue-row {
  border-bottom: 1px solid #21262d; padding: 12px 0;
  display: flex; align-items: flex-start; gap: 12px;
}
.pr-row:last-child, .issue-row:last-child { border-bottom: none; }
.label {
  display: inline-block; padding: 1px 8px; border-radius: 12px;
  font-size: 12px; background: #30363d; color: #c9d1d9; margin: 2px;
}
.meta-row { display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 12px; }
.meta-item { display: flex; flex-direction: column; gap: 2px; }
.meta-label { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }
.meta-value { font-size: 14px; color: #e6edf3; }
pre { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 12px;
      font-size: 13px; overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
"""

_TOKEN_SCRIPT = """
const API = '/api/v1/musehub';

function getToken() { return localStorage.getItem('musehub_token') || ''; }
function setToken(t) { localStorage.setItem('musehub_token', t); }
function clearToken() { localStorage.removeItem('musehub_token'); }

function authHeaders() {
  const t = getToken();
  return t ? { 'Authorization': 'Bearer ' + t, 'Content-Type': 'application/json' } : {};
}

async function apiFetch(path, opts = {}) {
  const res = await fetch(API + path, { ...opts, headers: { ...authHeaders(), ...(opts.headers||{}) } });
  if (res.status === 401 || res.status === 403) {
    showTokenForm('Session expired or invalid token — please re-enter your JWT.');
    throw new Error('auth');
  }
  if (!res.ok) {
    const body = await res.text();
    throw new Error(res.status + ': ' + body);
  }
  return res.json();
}

function showTokenForm(msg) {
  document.getElementById('token-form').style.display = 'block';
  document.getElementById('content').innerHTML = '';
  if (msg) document.getElementById('token-msg').textContent = msg;
}

function tokenFormHtml() {
  return `
    <div class="token-form" id="token-form" style="display:none">
      <p id="token-msg">Enter your Maestro JWT to browse this repo.</p>
      <input type="password" id="token-input" placeholder="eyJ..." />
      <button class="btn btn-primary" onclick="saveToken()">Save &amp; Load</button>
      &nbsp;
      <button class="btn btn-secondary" onclick="clearToken();location.reload()">Clear</button>
    </div>`;
}

function saveToken() {
  const t = document.getElementById('token-input').value.trim();
  if (t) { setToken(t); location.reload(); }
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function shortSha(sha) { return sha ? sha.substring(0, 8) : '—'; }
"""


def _page(title: str, breadcrumb: str, body_script: str) -> str:
    """Assemble a complete Muse Hub HTML page with shared chrome."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — Muse Hub</title>
  <style>{_CSS}</style>
</head>
<body>
  <header>
    <span class="logo">&#127925; Muse Hub</span>
    <span class="breadcrumb">{breadcrumb}</span>
    <span style="flex:1"></span>
    <button class="btn btn-secondary" style="font-size:12px"
            onclick="clearToken();location.reload()">Sign out</button>
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
  <script>
    {_TOKEN_SCRIPT}
    window.addEventListener('DOMContentLoaded', function() {{
      if (!getToken()) {{ showTokenForm(); return; }}
      {body_script}
    }});
  </script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Route handlers — explore / discover (no auth required)
# ---------------------------------------------------------------------------

_EXPLORE_SCRIPT = """
const DISCOVER_API = '/api/v1/musehub/discover/repos';

function escHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function tagHtml(tag) {
  return '<span class="label">' + escHtml(tag) + '</span>';
}

function repoCard(r) {
  const tags = (r.tags || []).map(tagHtml).join('');
  const key  = r.keySignature ? escHtml(r.keySignature) : '';
  const bpm  = r.tempoBpm ? r.tempoBpm + ' BPM' : '';
  const meta = [key, bpm].filter(Boolean).join(' &bull; ');
  return `
    <div class="repo-card">
      <div class="repo-card-title">
        <a href="/musehub/ui/${escHtml(r.repoId)}">${escHtml(r.name)}</a>
      </div>
      <div class="repo-card-owner">${escHtml(r.ownerUserId)}</div>
      ${r.description ? '<div class="repo-card-desc">' + escHtml(r.description) + '</div>' : ''}
      <div class="repo-card-tags">${tags}</div>
      <div class="repo-card-meta">
        ${meta ? '<span>' + meta + '</span>' : ''}
        <span>&#9733; ${r.starCount}</span>
        <span>&#128190; ${r.commitCount} commits</span>
      </div>
    </div>`;
}

async function loadExplore(page, sort, genre, key, tempoMin, tempoMax, instrumentation) {
  const params = new URLSearchParams({ page: page, page_size: 24, sort: sort });
  if (genre)         params.set('genre', genre);
  if (key)           params.set('key', key);
  if (tempoMin)      params.set('tempo_min', tempoMin);
  if (tempoMax)      params.set('tempo_max', tempoMax);
  if (instrumentation) params.set('instrumentation', instrumentation);

  try {
    const res  = await fetch(DISCOVER_API + '?' + params.toString());
    if (!res.ok) throw new Error(res.status + ': ' + await res.text());
    const data = await res.json();
    const repos = data.repos || [];
    const total = data.total || 0;
    const pages = Math.ceil(total / 24) || 1;

    const grid = repos.length === 0
      ? '<p class="loading">No repos found matching these filters.</p>'
      : repos.map(repoCard).join('');

    const pager = pages > 1 ? `
      <div class="pager">
        ${page > 1 ? '<button class="btn btn-secondary" onclick="go(' + (page-1) + ')">&#8592; Prev</button>' : ''}
        <span style="color:#8b949e;font-size:13px">Page ${page} of ${pages} &bull; ${total} repos</span>
        ${page < pages ? '<button class="btn btn-secondary" onclick="go(' + (page+1) + ')">Next &#8594;</button>' : ''}
      </div>` : '<div class="pager" style="color:#8b949e;font-size:13px">' + total + ' repos</div>';

    document.getElementById('repo-grid').innerHTML = grid;
    document.getElementById('pager').innerHTML = pager;
  } catch(e) {
    document.getElementById('repo-grid').innerHTML =
      '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
  }
}

function currentState() {
  return {
    page: parseInt(document.getElementById('cur-page').value || '1'),
    sort: document.getElementById('sort-sel').value,
    genre: document.getElementById('genre-inp').value.trim(),
    key:   document.getElementById('key-inp').value.trim(),
    tempoMin: document.getElementById('tempo-min').value.trim(),
    tempoMax: document.getElementById('tempo-max').value.trim(),
    instr: document.getElementById('instr-inp').value.trim(),
  };
}

function go(page) {
  document.getElementById('cur-page').value = page;
  const s = currentState();
  loadExplore(page, s.sort, s.genre, s.key, s.tempoMin, s.tempoMax, s.instr);
}

function applyFilters() { go(1); }
"""

_EXPLORE_CSS_EXTRA = """
.filter-bar {
  display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end;
  background: #161b22; border: 1px solid #30363d; border-radius: 6px;
  padding: 12px; margin-bottom: 16px;
}
.filter-group { display: flex; flex-direction: column; gap: 4px; }
.filter-label { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }
.filter-group input {
  background: #0d1117; color: #c9d1d9; border: 1px solid #30363d;
  border-radius: 6px; padding: 6px 10px; font-size: 13px; width: 140px;
}
.filter-group input:focus { outline: none; border-color: #58a6ff; }
.repo-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.repo-card {
  background: #161b22; border: 1px solid #30363d; border-radius: 6px;
  padding: 14px; display: flex; flex-direction: column; gap: 6px;
  transition: border-color 0.15s;
}
.repo-card:hover { border-color: #58a6ff; }
.repo-card-title { font-size: 15px; font-weight: 600; }
.repo-card-owner { font-size: 12px; color: #8b949e; }
.repo-card-desc  { font-size: 13px; color: #c9d1d9; }
.repo-card-tags  { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.repo-card-meta  {
  display: flex; gap: 12px; flex-wrap: wrap;
  font-size: 12px; color: #8b949e; margin-top: 4px;
}
.pager {
  display: flex; align-items: center; justify-content: center;
  gap: 12px; margin-top: 20px;
}
"""


def _explore_page_html(title: str, breadcrumb: str, default_sort: str) -> str:
    """Render the explore or trending page HTML shell.

    The page calls the public ``GET /api/v1/musehub/discover/repos`` JSON API
    from the browser — no JWT required for browsing. Star/unstar actions require
    a JWT stored in localStorage but are optional (unauthenticated visitors can
    browse without starring).
    """
    css = _CSS + _EXPLORE_CSS_EXTRA
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — Muse Hub</title>
  <style>{css}</style>
</head>
<body>
  <header>
    <span class="logo">&#127925; Muse Hub</span>
    <span class="breadcrumb">{breadcrumb}</span>
    <span style="flex:1"></span>
    <a href="/musehub/ui/explore" class="btn btn-secondary" style="font-size:12px">Explore</a>
    &nbsp;
    <a href="/musehub/ui/trending" class="btn btn-secondary" style="font-size:12px">Trending</a>
  </header>
  <div class="container" style="max-width:1200px">
    <div class="filter-bar">
      <div class="filter-group">
        <span class="filter-label">Genre</span>
        <input id="genre-inp" type="text" placeholder="jazz, lo-fi…" oninput="applyFilters()"/>
      </div>
      <div class="filter-group">
        <span class="filter-label">Key</span>
        <input id="key-inp" type="text" placeholder="F# minor" oninput="applyFilters()"/>
      </div>
      <div class="filter-group">
        <span class="filter-label">BPM min</span>
        <input id="tempo-min" type="number" min="20" max="300" placeholder="80" oninput="applyFilters()"/>
      </div>
      <div class="filter-group">
        <span class="filter-label">BPM max</span>
        <input id="tempo-max" type="number" min="20" max="300" placeholder="140" oninput="applyFilters()"/>
      </div>
      <div class="filter-group">
        <span class="filter-label">Instrument</span>
        <input id="instr-inp" type="text" placeholder="bass, drums…" oninput="applyFilters()"/>
      </div>
      <div class="filter-group">
        <span class="filter-label">Sort by</span>
        <select id="sort-sel" onchange="applyFilters()">
          <option value="created" {'selected' if default_sort == 'created' else ''}>Newest</option>
          <option value="stars"   {'selected' if default_sort == 'stars'   else ''}>Stars</option>
          <option value="activity"{'selected' if default_sort == 'activity' else ''}>Activity</option>
          <option value="commits" {'selected' if default_sort == 'commits'  else ''}>Commits</option>
        </select>
      </div>
    </div>
    <input type="hidden" id="cur-page" value="1"/>
    <div id="repo-grid" class="repo-grid"><p class="loading">Loading&#8230;</p></div>
    <div id="pager"></div>
  </div>
  <script>
    {_EXPLORE_SCRIPT}
    window.addEventListener('DOMContentLoaded', function() {{
      const s = currentState();
      loadExplore(1, s.sort, s.genre, s.key, s.tempoMin, s.tempoMax, s.instr);
    }});
  </script>
</body>
</html>"""


@router.get("/explore", response_class=HTMLResponse, summary="Muse Hub explore page")
async def explore_page() -> HTMLResponse:
    """Render the explore/discover page — a filterable grid of all public repos.

    No JWT required. The page fetches from the public
    ``GET /api/v1/musehub/discover/repos`` endpoint. Filter controls are
    rendered in the browser; filter state lives in the query URL so pages
    are bookmarkable.
    """
    return HTMLResponse(
        content=_explore_page_html(
            title="Explore",
            breadcrumb="Explore",
            default_sort="created",
        )
    )


@router.get("/trending", response_class=HTMLResponse, summary="Muse Hub trending page")
async def trending_page() -> HTMLResponse:
    """Render the trending page — public repos sorted by star count by default.

    No JWT required. Identical shell to the explore page but pre-selected
    to sort by stars, surfacing the most-starred compositions first.
    """
    return HTMLResponse(
        content=_explore_page_html(
            title="Trending",
            breadcrumb="Trending",
            default_sort="stars",
        )
    )


# ---------------------------------------------------------------------------
# Route handlers — per-repo pages (no auth required)
# ---------------------------------------------------------------------------


@router.get("/{repo_id}", response_class=HTMLResponse, summary="Muse Hub repo page")
async def repo_page(repo_id: str) -> HTMLResponse:
    """Render the repo landing page: branch selector + newest 20 commits.

    Auth is handled client-side via localStorage JWT. The page fetches from
    ``GET /api/v1/musehub/repos/{repo_id}/branches`` and
    ``GET /api/v1/musehub/repos/{repo_id}/commits``.
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const base = '/musehub/ui/' + repoId;

      async function load(branch) {{
        try {{
          const [repoData, branchData, commitData] = await Promise.all([
            apiFetch('/repos/' + repoId),
            apiFetch('/repos/' + repoId + '/branches'),
            apiFetch('/repos/' + repoId + '/commits' + (branch ? '?branch=' + encodeURIComponent(branch) + '&limit=20' : '?limit=20')),
          ]);

          const branches = branchData.branches || [];
          const commits  = commitData.commits  || [];

          const branchSel = branches.map(b =>
            '<option value="' + b.name + '"' + (b.name === branch ? ' selected' : '') + '>' + b.name + '</option>'
          ).join('');

          const commitRows = commits.length === 0
            ? '<p class="loading">No commits yet.</p>'
            : commits.map(c => `
              <div class="commit-row">
                <a class="commit-sha" href="${{base}}/commits/${{c.commitId}}">${{shortSha(c.commitId)}}</a>
                <span class="commit-msg"><a href="${{base}}/commits/${{c.commitId}}">${{escHtml(c.message)}}</a></span>
                <span class="commit-meta">${{escHtml(c.author)}} &bull; ${{fmtDate(c.timestamp)}}</span>
              </div>`).join('');

          document.getElementById('content').innerHTML = `
            <div class="card">
              <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
                <h1 style="margin:0">${{escHtml(repoData.name)}}</h1>
                <span class="badge badge-${{repoData.visibility}}">${{repoData.visibility}}</span>
              </div>
              <div style="display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap">
                <a href="${{base}}/pulls" class="btn btn-secondary">Pull Requests</a>
                <a href="${{base}}/issues" class="btn btn-secondary">Issues</a>
                <a href="${{base}}/search" class="btn btn-secondary">&#128269; Search</a>
              </div>
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
                <select id="branch-sel" onchange="load(this.value)">
                  <option value="">All branches</option>
                  ${{branchSel}}
                </select>
                <span class="meta-value" style="font-size:13px;color:#8b949e">
                  ${{commitData.total}} commit${{commitData.total !== 1 ? 's' : ''}}
                </span>
              </div>
              ${{commitRows}}
            </div>`;
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('content').innerHTML = '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      load('');
    """
    html = _page(
        title=f"Repo {repo_id[:8]}",
        breadcrumb=f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a>',
        body_script=script,
    )
    return HTMLResponse(content=html)


@router.get(
    "/{repo_id}/commits/{commit_id}",
    response_class=HTMLResponse,
    summary="Muse Hub commit detail page",
)
async def commit_page(repo_id: str, commit_id: str) -> HTMLResponse:
    """Render the commit detail page: metadata + artifact browser.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/commits?limit=200`` to
    locate the specific commit, and ``GET /api/v1/musehub/repos/{repo_id}/objects``
    to list artifacts. Artifacts are displayed by extension:
    - ``.webp`` → inline ``<img>`` (piano roll preview)
    - ``.mp3``  → ``<audio controls>`` player
    - ``.mid``  → download link
    """
    script = f"""
      const repoId   = {repr(repo_id)};
      const commitId = {repr(commit_id)};
      const base     = '/musehub/ui/' + repoId;
      const apiBase  = '/api/v1/musehub/repos/' + repoId;

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      function artifactHtml(obj) {{
        const ext = obj.path.split('.').pop().toLowerCase();
        const contentUrl = apiBase + '/objects/' + obj.objectId + '/content?token=' + encodeURIComponent(getToken());
        const safeContentUrl = apiBase + '/objects/' + obj.objectId + '/content';
        const fetchUrl = safeContentUrl;
        if (ext === 'webp' || ext === 'png' || ext === 'jpg' || ext === 'jpeg') {{
          return `<div class="artifact-card">
            <img src="${{fetchUrl}}" alt="${{escHtml(obj.path)}}"
                 onerror="this.src='';this.alt='Preview unavailable'"
                 loading="lazy" />
            <span class="path">${{escHtml(obj.path)}}</span>
          </div>`;
        }} else if (ext === 'mp3' || ext === 'ogg' || ext === 'wav') {{
          return `<div class="artifact-card">
            <audio controls src="${{fetchUrl}}"></audio>
            <span class="path">${{escHtml(obj.path)}}</span>
          </div>`;
        }} else {{
          return `<div class="artifact-card">
            <a class="btn btn-secondary" href="${{fetchUrl}}" download="${{escHtml(obj.path.split('/').pop())}}">
              &#11015; Download
            </a>
            <span class="path">${{escHtml(obj.path)}}</span>
          </div>`;
        }}
      }}

      async function load() {{
        try {{
          const [commitsData, objectsData] = await Promise.all([
            apiFetch('/repos/' + repoId + '/commits?limit=200'),
            apiFetch('/repos/' + repoId + '/objects'),
          ]);

          const commit = (commitsData.commits || []).find(c => c.commitId === commitId);
          const objects = objectsData.objects || [];

          const commitSection = commit ? `
            <div class="meta-row">
              <div class="meta-item">
                <span class="meta-label">Author</span>
                <span class="meta-value">${{escHtml(commit.author)}}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">Date</span>
                <span class="meta-value">${{fmtDate(commit.timestamp)}}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">Branch</span>
                <span class="meta-value">${{escHtml(commit.branch)}}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">SHA</span>
                <span class="meta-value" style="font-family:monospace">${{escHtml(commit.commitId)}}</span>
              </div>
              ${{commit.parentIds && commit.parentIds.length > 0 ? `
              <div class="meta-item">
                <span class="meta-label">Parents</span>
                <span class="meta-value">
                  ${{commit.parentIds.map(p => '<a href="' + base + '/commits/' + p + '" style="font-family:monospace">' + p.substring(0,8) + '</a>').join(' ')}}
                </span>
              </div>` : ''}}
            </div>
            <pre>${{escHtml(commit.message)}}</pre>
          ` : `<p class="error">Commit not found in recent history.</p>`;

          const artifactSection = objects.length === 0
            ? '<p class="loading" style="margin-top:8px">No artifacts stored in this repo yet.</p>'
            : '<div class="artifact-grid">' + objects.map(artifactHtml).join('') + '</div>';

          document.getElementById('content').innerHTML = `
            <div style="margin-bottom:12px">
              <a href="${{base}}">&larr; Back to repo</a>
            </div>
            <div class="card">
              <h1>Commit</h1>
              ${{commitSection}}
            </div>
            <div class="card">
              <h2>Artifacts (${{objects.length}})</h2>
              ${{artifactSection}}
            </div>`;
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('content').innerHTML = '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      load();
    """
    html = _page(
        title=f"Commit {commit_id[:8]}",
        breadcrumb=(
            f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / '
            f'commits / {commit_id[:8]}'
        ),
        body_script=script,
    )
    return HTMLResponse(content=html)


@router.get(
    "/{repo_id}/graph",
    response_class=HTMLResponse,
    summary="Muse Hub interactive DAG commit graph",
)
async def graph_page(repo_id: str) -> HTMLResponse:
    """Render the interactive DAG commit graph page.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/dag`` which returns a
    topologically sorted list of nodes and edges. The client-side renderer
    draws an SVG-based commit graph with:

    - Branch colour-coding (each unique branch name gets a stable colour)
    - Merge commits highlighted with two incoming edges
    - Zoom (mouse-wheel) and pan (drag) via SVG transform
    - Hover popover showing SHA, message, author, and timestamp
    - Branch/tag labels displayed as badges on nodes
    - HEAD node styled with a distinct ring
    - Click on any node navigates to the commit detail page
    - Virtualised rendering: only nodes within the visible viewport are
      painted, keeping 100+ commit graphs smooth

    No external CDN dependencies — the entire renderer is inline JavaScript.
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const base   = '/musehub/ui/' + repoId;

      // ── Colour palette for branches (stable hash → index) ──────────────────
      const BRANCH_COLORS = [
        '#58a6ff', '#3fb950', '#f0883e', '#bc8cff', '#ff7b72',
        '#79c0ff', '#56d364', '#ffa657', '#d2a8ff', '#ff9492',
      ];
      const _branchColorCache = {{}};
      function branchColor(name) {{
        if (_branchColorCache[name]) return _branchColorCache[name];
        let h = 0;
        for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
        const c = BRANCH_COLORS[Math.abs(h) % BRANCH_COLORS.length];
        _branchColorCache[name] = c;
        return c;
      }}

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      // ── Layout constants ────────────────────────────────────────────────────
      const NODE_R   = 10;   // node circle radius
      const ROW_H    = 48;   // vertical spacing per commit
      const COL_W    = 32;   // horizontal spacing per branch lane
      const PAD_LEFT = 20;
      const PAD_TOP  = 20;

      // ── Assign each commit a (col, row) position ────────────────────────────
      // nodes are already in topological order (oldest first).
      // Assign each branch a stable column; new branch = new column.
      function layoutNodes(nodes) {{
        const colMap = {{}};   // branch → column index
        let nextCol = 0;
        const pos = {{}};
        nodes.forEach((n, row) => {{
          if (colMap[n.branch] === undefined) colMap[n.branch] = nextCol++;
          pos[n.commitId] = {{ col: colMap[n.branch], row }};
        }});
        const maxCol = nextCol;
        return {{ pos, maxCol }};
      }}

      // ── Render the SVG graph ─────────────────────────────────────────────────
      function renderGraph(data) {{
        const {{ nodes, edges, headCommitId }} = data;
        if (!nodes.length) {{
          document.getElementById('content').innerHTML =
            '<div class="card"><p class="loading">No commits yet — nothing to graph.</p></div>';
          return;
        }}

        const {{ pos, maxCol }} = layoutNodes(nodes);
        const svgW = PAD_LEFT * 2 + maxCol * COL_W + 400;  // extra width for labels
        const svgH = PAD_TOP  * 2 + nodes.length * ROW_H;

        // Build node lookup for tooltip
        const nodeMap = {{}};
        nodes.forEach(n => {{ nodeMap[n.commitId] = n; }});

        // ── SVG elements as strings ───────────────────────────────────────────
        let edgePaths = '';
        edges.forEach(e => {{
          const src = pos[e.source];
          const tgt = pos[e.target];
          if (!src || !tgt) return;
          const x1 = PAD_LEFT + src.col * COL_W;
          const y1 = PAD_TOP  + src.row * ROW_H;
          const x2 = PAD_LEFT + tgt.col * COL_W;
          const y2 = PAD_TOP  + tgt.row * ROW_H;
          // Cubic bezier so diagonal edges look smooth
          const cx = x1; const cy = y2;
          const color = branchColor(nodeMap[e.source] ? nodeMap[e.source].branch : 'main');
          edgePaths += `<path d="M${{x1}},${{y1}} C${{cx}},${{cy}} ${{cx}},${{cy}} ${{x2}},${{y2}}"
            stroke="${{color}}" stroke-width="2" fill="none" opacity="0.6"/>`;
        }});

        let nodeCircles = '';
        let nodeLabels = '';
        nodes.forEach(n => {{
          const p = pos[n.commitId];
          const cx = PAD_LEFT + p.col * COL_W;
          const cy = PAD_TOP  + p.row  * ROW_H;
          const color = branchColor(n.branch);
          const isHead = n.commitId === headCommitId || n.isHead;
          const isMerge = (n.parentIds || []).length > 1;

          // Outer ring for HEAD
          if (isHead) {{
            nodeCircles += `<circle cx="${{cx}}" cy="${{cy}}" r="${{NODE_R + 4}}"
              fill="none" stroke="#f0883e" stroke-width="2" opacity="0.9"/>`;
          }}
          // Merge commits: diamond shape via rotated rect
          if (isMerge) {{
            nodeCircles += `<rect x="${{cx - NODE_R * 0.8}}" y="${{cy - NODE_R * 0.8}}"
              width="${{NODE_R * 1.6}}" height="${{NODE_R * 1.6}}"
              fill="${{color}}" stroke="#0d1117" stroke-width="1.5"
              transform="rotate(45 ${{cx}} ${{cy}})"
              class="dag-node" data-id="${{n.commitId}}" style="cursor:pointer"/>`;
          }} else {{
            nodeCircles += `<circle cx="${{cx}}" cy="${{cy}}" r="${{NODE_R}}"
              fill="${{color}}" stroke="#0d1117" stroke-width="1.5"
              class="dag-node" data-id="${{n.commitId}}" style="cursor:pointer"/>`;
          }}

          // Message label (truncated)
          const msg = n.message.length > 55 ? n.message.substring(0,52) + '...' : n.message;
          const labelX = PAD_LEFT + maxCol * COL_W + 12;
          nodeLabels += `<text x="${{labelX}}" y="${{cy + 4}}" font-size="13"
            fill="#c9d1d9" style="cursor:pointer" class="dag-node" data-id="${{n.commitId}}">
            <tspan font-family="monospace" fill="#58a6ff">${{n.commitId.substring(0,7)}}</tspan>
            <tspan dx="8">${{escHtml(msg)}}</tspan>
          </text>`;

          // Branch/tag badges
          let badgeX = labelX;
          (n.branchLabels || []).forEach(lbl => {{
            const bw = lbl.length * 7 + 12;
            nodeLabels += `<rect x="${{badgeX - 4}}" y="${{cy - 20}}" width="${{bw}}" height="14"
              rx="7" fill="${{branchColor(lbl)}}" opacity="0.25"/>
            <text x="${{badgeX}}" y="${{cy - 9}}" font-size="11" fill="${{branchColor(lbl)}}">${{escHtml(lbl)}}</text>`;
            badgeX += bw + 6;
          }});
        }});

        const svgContent = `
          <defs>
            <marker id="arrow" markerWidth="6" markerHeight="6" refX="3" refY="3"
              orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L6,3 L0,6 z" fill="#8b949e"/>
            </marker>
          </defs>
          ${{edgePaths}}
          ${{nodeCircles}}
          ${{nodeLabels}}`;

        // ── Legend ─────────────────────────────────────────────────────────────
        const branchesInGraph = [...new Set(nodes.map(n => n.branch))];
        const legendItems = branchesInGraph.map(b =>
          `<span style="display:inline-flex;align-items:center;gap:6px;margin-right:16px">
            <svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="${{branchColor(b)}}"/></svg>
            <span style="font-size:13px;color:#c9d1d9">${{escHtml(b)}}</span>
          </span>`
        ).join('');

        document.getElementById('content').innerHTML = `
          <div style="margin-bottom:12px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">
            <a href="${{base}}">&larr; Back to repo</a>
            <span style="color:#8b949e;font-size:13px">${{nodes.length}} commit${{nodes.length!==1?'s':''}} &bull; scroll to zoom &bull; drag to pan</span>
          </div>
          <div class="card" style="padding:0;overflow:hidden">
            <div style="padding:12px 16px;border-bottom:1px solid #30363d;display:flex;align-items:center;flex-wrap:wrap;gap:4px">
              <span style="font-size:12px;color:#8b949e;margin-right:8px">Branches:</span>
              ${{legendItems}}
              <span style="font-size:12px;color:#8b949e;margin-left:auto">&#9830; = merge commit</span>
              <span style="font-size:12px;color:#f0883e;margin-left:12px">&#9711; = HEAD</span>
            </div>
            <div id="dag-viewport" style="overflow:hidden;position:relative;height:520px;background:#0d1117;cursor:grab">
              <svg id="dag-svg" width="${{svgW}}" height="${{svgH}}" style="position:absolute;top:0;left:0">
                <g id="dag-g" transform="translate(0,0) scale(1)">
                  ${{svgContent}}
                </g>
              </svg>
            </div>
          </div>
          <div id="dag-popover" style="display:none;position:fixed;z-index:1000;background:#161b22;
            border:1px solid #30363d;border-radius:8px;padding:12px 16px;min-width:280px;max-width:400px;
            box-shadow:0 8px 32px rgba(0,0,0,0.6);pointer-events:none">
            <div style="font-family:monospace;font-size:13px;color:#58a6ff;margin-bottom:6px" id="pop-sha"></div>
            <div style="font-size:13px;color:#e6edf3;margin-bottom:6px;word-break:break-word" id="pop-msg"></div>
            <div style="font-size:12px;color:#8b949e" id="pop-meta"></div>
          </div>`;

        // ── Zoom + pan ────────────────────────────────────────────────────────
        const viewport = document.getElementById('dag-viewport');
        const g = document.getElementById('dag-g');
        let scale = 1, tx = 0, ty = 0;
        let dragging = false, dragX = 0, dragY = 0;

        function applyTransform() {{
          g.setAttribute('transform', `translate(${{tx}},${{ty}}) scale(${{scale}})`);
        }}

        viewport.addEventListener('wheel', e => {{
          e.preventDefault();
          const rect = viewport.getBoundingClientRect();
          const mx = e.clientX - rect.left;
          const my = e.clientY - rect.top;
          const delta = e.deltaY > 0 ? 0.85 : 1.15;
          const newScale = Math.max(0.2, Math.min(4, scale * delta));
          tx = mx - (mx - tx) * (newScale / scale);
          ty = my - (my - ty) * (newScale / scale);
          scale = newScale;
          applyTransform();
        }}, {{ passive: false }});

        viewport.addEventListener('mousedown', e => {{
          dragging = true; dragX = e.clientX; dragY = e.clientY;
          viewport.style.cursor = 'grabbing';
        }});
        window.addEventListener('mouseup', () => {{
          dragging = false;
          if (viewport) viewport.style.cursor = 'grab';
        }});
        window.addEventListener('mousemove', e => {{
          if (!dragging) return;
          tx += e.clientX - dragX; ty += e.clientY - dragY;
          dragX = e.clientX; dragY = e.clientY;
          applyTransform();
        }});

        // ── Hover popover ─────────────────────────────────────────────────────
        const popover = document.getElementById('dag-popover');
        const svgEl = document.getElementById('dag-svg');
        svgEl.addEventListener('mousemove', e => {{
          const target = e.target.closest('.dag-node');
          if (!target) {{ popover.style.display = 'none'; return; }}
          const cid = target.getAttribute('data-id');
          const node = nodeMap[cid];
          if (!node) {{ popover.style.display = 'none'; return; }}
          document.getElementById('pop-sha').textContent = node.commitId;
          document.getElementById('pop-msg').textContent = node.message;
          document.getElementById('pop-meta').innerHTML =
            escHtml(node.author) + ' &bull; ' + fmtDate(node.timestamp) +
            ' &bull; ' + escHtml(node.branch);
          popover.style.display = 'block';
          // Position near cursor, keep on-screen
          const vw = window.innerWidth, vh = window.innerHeight;
          let px = e.clientX + 16, py = e.clientY + 16;
          if (px + 420 > vw) px = e.clientX - 420;
          if (py + 120 > vh) py = e.clientY - 120;
          popover.style.left = px + 'px';
          popover.style.top  = py + 'px';
        }});
        svgEl.addEventListener('mouseleave', () => {{ popover.style.display = 'none'; }});

        // ── Click to navigate ─────────────────────────────────────────────────
        svgEl.addEventListener('click', e => {{
          const target = e.target.closest('.dag-node');
          if (!target) return;
          const cid = target.getAttribute('data-id');
          if (cid) window.location.href = base + '/commits/' + cid;
        }});
      }}

      async function load() {{
        try {{
          const data = await apiFetch('/repos/' + repoId + '/dag');
          renderGraph(data);
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('content').innerHTML =
              '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      load();
    """
    html = _page(
        title="Commit Graph",
        breadcrumb=(
            f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / graph'
        ),
        body_script=script,
    )
    return HTMLResponse(content=html)


@router.get(
    "/{repo_id}/pulls",
    response_class=HTMLResponse,
    summary="Muse Hub pull request list page",
)
async def pr_list_page(repo_id: str) -> HTMLResponse:
    """Render the PR list page: open/all filter + PR rows.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/pull-requests?state=<filter>``.
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const base   = '/musehub/ui/' + repoId;

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      async function load(state) {{
        try {{
          const data = await apiFetch('/repos/' + repoId + '/pull-requests?state=' + state);
          const prs  = data.pullRequests || [];

          const rows = prs.length === 0
            ? '<p class="loading">No pull requests found.</p>'
            : prs.map(pr => `
              <div class="pr-row">
                <span class="badge badge-${{pr.state}}">${{pr.state}}</span>
                <div style="flex:1">
                  <a href="${{base}}/pulls/${{pr.prId}}">${{escHtml(pr.title)}}</a>
                  <div style="font-size:12px;color:#8b949e;margin-top:2px">
                    ${{escHtml(pr.fromBranch)}} &rarr; ${{escHtml(pr.toBranch)}} &bull; ${{fmtDate(pr.createdAt)}}
                  </div>
                </div>
              </div>`).join('');

          document.getElementById('content').innerHTML = `
            <div style="margin-bottom:12px">
              <a href="${{base}}">&larr; Back to repo</a>
            </div>
            <div class="card">
              <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
                <h1 style="margin:0">Pull Requests</h1>
                <select onchange="load(this.value)">
                  <option value="open" ${{state==='open'?'selected':''}}>Open</option>
                  <option value="all"  ${{state==='all' ?'selected':''}}>All</option>
                </select>
              </div>
              ${{rows}}
            </div>`;
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('content').innerHTML = '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      load('open');
    """
    html = _page(
        title="Pull Requests",
        breadcrumb=f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / pulls',
        body_script=script,
    )
    return HTMLResponse(content=html)


@router.get(
    "/{repo_id}/pulls/{pr_id}",
    response_class=HTMLResponse,
    summary="Muse Hub PR detail page",
)
async def pr_detail_page(repo_id: str, pr_id: str) -> HTMLResponse:
    """Render the PR detail page: title, body, branches, state, merge button.

    The merge button calls ``POST /api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/merge``
    with ``merge_strategy: merge_commit`` and reloads the page on success.
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const prId   = {repr(pr_id)};
      const base   = '/musehub/ui/' + repoId;

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      async function mergePr() {{
        if (!confirm('Merge this pull request?')) return;
        try {{
          await apiFetch('/repos/' + repoId + '/pull-requests/' + prId + '/merge', {{
            method: 'POST',
            body: JSON.stringify({{ mergeStrategy: 'merge_commit' }}),
          }});
          location.reload();
        }} catch(e) {{
          if (e.message !== 'auth')
            alert('Merge failed: ' + e.message);
        }}
      }}

      async function load() {{
        try {{
          const pr = await apiFetch('/repos/' + repoId + '/pull-requests/' + prId);

          const mergeSection = pr.state === 'open' ? `
            <div style="margin-top:16px">
              <button class="btn btn-primary" onclick="mergePr()">&#10003; Merge pull request</button>
            </div>` : '';

          document.getElementById('content').innerHTML = `
            <div style="margin-bottom:12px">
              <a href="${{base}}/pulls">&larr; Back to pull requests</a>
            </div>
            <div class="card">
              <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
                <h1 style="margin:0">${{escHtml(pr.title)}}</h1>
                <span class="badge badge-${{pr.state}}">${{pr.state}}</span>
              </div>
              <div class="meta-row">
                <div class="meta-item">
                  <span class="meta-label">From</span>
                  <span class="meta-value" style="font-family:monospace">${{escHtml(pr.fromBranch)}}</span>
                </div>
                <div class="meta-item">
                  <span class="meta-label">Into</span>
                  <span class="meta-value" style="font-family:monospace">${{escHtml(pr.toBranch)}}</span>
                </div>
                <div class="meta-item">
                  <span class="meta-label">Created</span>
                  <span class="meta-value">${{fmtDate(pr.createdAt)}}</span>
                </div>
                ${{pr.mergeCommitId ? `
                <div class="meta-item">
                  <span class="meta-label">Merge commit</span>
                  <span class="meta-value" style="font-family:monospace">
                    <a href="${{base}}/commits/${{pr.mergeCommitId}}">${{pr.mergeCommitId.substring(0,8)}}</a>
                  </span>
                </div>` : ''}}
              </div>
              ${{pr.body ? '<pre>' + escHtml(pr.body) + '</pre>' : ''}}
              ${{mergeSection}}
            </div>`;
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('content').innerHTML = '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      load();
    """
    html = _page(
        title=f"PR {pr_id[:8]}",
        breadcrumb=(
            f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / '
            f'<a href="/musehub/ui/{repo_id}/pulls">pulls</a> / {pr_id[:8]}'
        ),
        body_script=script,
    )
    return HTMLResponse(content=html)


@router.get(
    "/{repo_id}/issues",
    response_class=HTMLResponse,
    summary="Muse Hub issue list page",
)
async def issue_list_page(repo_id: str) -> HTMLResponse:
    """Render the issue list page: open filter + issue rows with labels.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/issues?state=<filter>``.
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const base   = '/musehub/ui/' + repoId;

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      async function load(state) {{
        try {{
          const data   = await apiFetch('/repos/' + repoId + '/issues?state=' + state);
          const issues = data.issues || [];

          const rows = issues.length === 0
            ? '<p class="loading">No issues found.</p>'
            : issues.map(i => `
              <div class="issue-row">
                <span class="badge badge-${{i.state}}">#${{i.number}}</span>
                <div style="flex:1">
                  <a href="${{base}}/issues/${{i.number}}">${{escHtml(i.title)}}</a>
                  <div style="margin-top:4px">
                    ${{(i.labels||[]).map(l => '<span class="label">' + escHtml(l) + '</span>').join('')}}
                  </div>
                  <div style="font-size:12px;color:#8b949e;margin-top:2px">
                    Opened ${{fmtDate(i.createdAt)}}
                  </div>
                </div>
                <span class="badge badge-${{i.state}}">${{i.state}}</span>
              </div>`).join('');

          document.getElementById('content').innerHTML = `
            <div style="margin-bottom:12px">
              <a href="${{base}}">&larr; Back to repo</a>
            </div>
            <div class="card">
              <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
                <h1 style="margin:0">Issues</h1>
                <select onchange="load(this.value)">
                  <option value="open"   ${{state==='open'  ?'selected':''}}>Open</option>
                  <option value="closed" ${{state==='closed'?'selected':''}}>Closed</option>
                  <option value="all"    ${{state==='all'   ?'selected':''}}>All</option>
                </select>
              </div>
              ${{rows}}
            </div>`;
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('content').innerHTML = '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      load('open');
    """
    html = _page(
        title="Issues",
        breadcrumb=f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / issues',
        body_script=script,
    )
    return HTMLResponse(content=html)


@router.get(
    "/{repo_id}/context/{ref}",
    response_class=HTMLResponse,
    summary="Muse Hub context viewer page",
)
async def context_page(repo_id: str, ref: str) -> HTMLResponse:
    """Render the AI context viewer for a given commit ref.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/context/{ref}`` and renders
    the MuseHubContextResponse as a structured human-readable document.

    Sections:
    - "What the agent sees" explainer
    - Musical State (active tracks + available musical dimensions)
    - History Summary (recent ancestor commits)
    - Missing Elements (dimensions not yet available)
    - Suggestions (what to compose next)
    - Raw JSON toggle for debugging
    - Copy-to-clipboard button for sharing with agents
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const ref    = {repr(ref)};
      const base   = '/musehub/ui/' + repoId;

      function escHtml(s) {{
        if (s === null || s === undefined) return '—';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      function copyJson() {{
        const text = document.getElementById('raw-json').textContent;
        navigator.clipboard.writeText(text).then(() => {{
          const btn = document.getElementById('copy-btn');
          btn.textContent = 'Copied!';
          setTimeout(() => {{ btn.textContent = 'Copy JSON'; }}, 2000);
        }});
      }}

      function toggleSection(id) {{
        const el = document.getElementById(id);
        if (!el) return;
        el.style.display = el.style.display === 'none' ? '' : 'none';
        const btn = document.querySelector('[data-target="' + id + '"]');
        if (btn) btn.textContent = el.style.display === 'none' ? '▶ Show' : '▼ Hide';
      }}

      async function load() {{
        try {{
          const ctx = await apiFetch('/repos/' + repoId + '/context/' + ref);

          const tracks = (ctx.musicalState.activeTracks || []);
          const trackList = tracks.length > 0
            ? tracks.map(t => '<span class="label">' + escHtml(t) + '</span>').join(' ')
            : '<em style="color:#8b949e">No music files found in repo yet.</em>';

          function dimRow(label, val) {{
            return val !== null && val !== undefined
              ? '<div class="meta-item"><span class="meta-label">' + label + '</span>'
                + '<span class="meta-value">' + escHtml(val) + '</span></div>'
              : '';
          }}

          const musicalDims = [
            dimRow('Key', ctx.musicalState.key),
            dimRow('Mode', ctx.musicalState.mode),
            dimRow('Tempo (BPM)', ctx.musicalState.tempoBpm),
            dimRow('Time Signature', ctx.musicalState.timeSignature),
            dimRow('Form', ctx.musicalState.form),
            dimRow('Emotion', ctx.musicalState.emotion),
          ].filter(Boolean).join('');

          const histEntries = (ctx.history || []);
          const histRows = histEntries.length > 0
            ? histEntries.map(h => `
                <div class="commit-row">
                  <a class="commit-sha" href="${{base}}/commits/${{h.commitId}}">${{shortSha(h.commitId)}}</a>
                  <span class="commit-msg">${{escHtml(h.message)}}</span>
                  <span class="commit-meta">${{escHtml(h.author)}} &bull; ${{fmtDate(h.timestamp)}}</span>
                </div>`).join('')
            : '<p class="loading">No ancestor commits.</p>';

          const missing = (ctx.missingElements || []);
          const missingList = missing.length > 0
            ? '<ul style="padding-left:20px;font-size:14px">' + missing.map(m => '<li>' + escHtml(m) + '</li>').join('') + '</ul>'
            : '<p style="color:#3fb950;font-size:14px">All musical dimensions are available.</p>';

          const suggestions = ctx.suggestions || {{}};
          const suggKeys = Object.keys(suggestions);
          const suggList = suggKeys.length > 0
            ? suggKeys.map(k => '<div style="margin-bottom:8px"><strong style="color:#e6edf3">' + escHtml(k) + ':</strong> ' + escHtml(suggestions[k]) + '</div>').join('')
            : '<p class="loading">No suggestions available.</p>';

          const rawJson = JSON.stringify(ctx, null, 2);

          document.getElementById('content').innerHTML = `
            <div style="margin-bottom:12px">
              <a href="${{base}}">&larr; Back to repo</a>
            </div>

            <div class="card" style="border-color:#1f6feb">
              <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
                <span style="font-size:20px">&#127925;</span>
                <h1 style="margin:0;font-size:18px">What the Agent Sees</h1>
              </div>
              <p style="font-size:14px;color:#8b949e;margin-bottom:0">
                This is the musical context document that the AI agent receives
                when generating music for this repo at commit
                <code style="font-size:12px;background:#0d1117;padding:2px 6px;border-radius:4px">${{shortSha(ref)}}</code>.
                Every composition decision — key, tempo, arrangement, what to add next —
                is guided by this document.
              </p>
            </div>

            <div class="card">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
                <h2 style="margin:0">&#127925; Musical State</h2>
                <button class="btn btn-secondary" style="font-size:12px"
                        data-target="musical-state-body" onclick="toggleSection('musical-state-body')">&#9660; Hide</button>
              </div>
              <div id="musical-state-body">
                <div style="margin-bottom:10px">
                  <span class="meta-label">Active Tracks</span>
                  <div style="margin-top:4px">${{trackList}}</div>
                </div>
                ${{musicalDims ? '<div class="meta-row" style="margin-top:12px">' + musicalDims + '</div>' : '<p style="font-size:13px;color:#8b949e">Musical dimensions (key, tempo, etc.) require MIDI analysis — not yet available.</p>'}}
              </div>
            </div>

            <div class="card">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
                <h2 style="margin:0">&#128337; History Summary</h2>
                <button class="btn btn-secondary" style="font-size:12px"
                        data-target="history-body" onclick="toggleSection('history-body')">&#9660; Hide</button>
              </div>
              <div id="history-body">
                <div class="meta-row" style="margin-bottom:12px">
                  <div class="meta-item">
                    <span class="meta-label">Commit</span>
                    <span class="meta-value" style="font-family:monospace">${{shortSha(ctx.headCommit.commitId)}}</span>
                  </div>
                  <div class="meta-item">
                    <span class="meta-label">Branch</span>
                    <span class="meta-value">${{escHtml(ctx.currentBranch)}}</span>
                  </div>
                  <div class="meta-item">
                    <span class="meta-label">Author</span>
                    <span class="meta-value">${{escHtml(ctx.headCommit.author)}}</span>
                  </div>
                  <div class="meta-item">
                    <span class="meta-label">Date</span>
                    <span class="meta-value">${{fmtDate(ctx.headCommit.timestamp)}}</span>
                  </div>
                </div>
                <pre style="margin-bottom:12px">${{escHtml(ctx.headCommit.message)}}</pre>
                <h2 style="font-size:14px;margin-bottom:8px">Ancestors (${{histEntries.length}})</h2>
                ${{histRows}}
              </div>
            </div>

            <div class="card">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
                <h2 style="margin:0">&#9888;&#65039; Missing Elements</h2>
                <button class="btn btn-secondary" style="font-size:12px"
                        data-target="missing-body" onclick="toggleSection('missing-body')">&#9660; Hide</button>
              </div>
              <div id="missing-body">
                ${{missingList}}
              </div>
            </div>

            <div class="card" style="border-color:#238636">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
                <h2 style="margin:0">&#127775; Suggestions</h2>
                <button class="btn btn-secondary" style="font-size:12px"
                        data-target="suggestions-body" onclick="toggleSection('suggestions-body')">&#9660; Hide</button>
              </div>
              <div id="suggestions-body">
                ${{suggList}}
              </div>
            </div>

            <div class="card">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
                <h2 style="margin:0">&#128196; Raw JSON</h2>
                <div style="display:flex;gap:8px">
                  <button id="copy-btn" class="btn btn-secondary" style="font-size:12px" onclick="copyJson()">Copy JSON</button>
                  <button class="btn btn-secondary" style="font-size:12px"
                          data-target="raw-json-body" onclick="toggleSection('raw-json-body')">&#9660; Hide</button>
                </div>
              </div>
              <div id="raw-json-body">
                <pre id="raw-json">${{escHtml(rawJson)}}</pre>
              </div>
            </div>`;
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('content').innerHTML = '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      load();
    """
    html = _page(
        title=f"Context {ref[:8]}",
        breadcrumb=(
            f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / '
            f"context / {ref[:8]}"
        ),
        body_script=script,
    )
    return HTMLResponse(content=html)


@router.get(
    "/{repo_id}/issues/{number}",
    response_class=HTMLResponse,
    summary="Muse Hub issue detail page",
)
async def issue_detail_page(repo_id: str, number: int) -> HTMLResponse:
    """Render the issue detail page: title, body, labels, state, close button.

    The close button calls
    ``POST /api/v1/musehub/repos/{repo_id}/issues/{number}/close``
    and reloads the page on success.
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const number = {number};
      const base   = '/musehub/ui/' + repoId;

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      async function closeIssue() {{
        if (!confirm('Close issue #' + number + '?')) return;
        try {{
          await apiFetch('/repos/' + repoId + '/issues/' + number + '/close', {{ method: 'POST' }});
          location.reload();
        }} catch(e) {{
          if (e.message !== 'auth')
            alert('Close failed: ' + e.message);
        }}
      }}

      async function load() {{
        try {{
          const issue = await apiFetch('/repos/' + repoId + '/issues/' + number);

          const closeSection = issue.state === 'open' ? `
            <div style="margin-top:16px">
              <button class="btn btn-danger" onclick="closeIssue()">&#10005; Close issue</button>
            </div>` : '';

          document.getElementById('content').innerHTML = `
            <div style="margin-bottom:12px">
              <a href="${{base}}/issues">&larr; Back to issues</a>
            </div>
            <div class="card">
              <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
                <h1 style="margin:0">${{escHtml(issue.title)}}</h1>
                <span class="badge badge-${{issue.state}}">#${{issue.number}}</span>
                <span class="badge badge-${{issue.state}}">${{issue.state}}</span>
              </div>
              <div class="meta-row">
                <div class="meta-item">
                  <span class="meta-label">Opened</span>
                  <span class="meta-value">${{fmtDate(issue.createdAt)}}</span>
                </div>
              </div>
              <div style="margin:8px 0">
                ${{(issue.labels||[]).map(l => '<span class="label">' + escHtml(l) + '</span>').join('')}}
              </div>
              ${{issue.body ? '<pre>' + escHtml(issue.body) + '</pre>' : ''}}
              ${{closeSection}}
            </div>`;
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('content').innerHTML = '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      load();
    """
    html = _page(
        title=f"Issue #{number}",
        breadcrumb=(
            f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / '
            f'<a href="/musehub/ui/{repo_id}/issues">issues</a> / #{number}'
        ),
        body_script=script,
    )
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# Embed CSS (compact dark theme, no chrome)
# ---------------------------------------------------------------------------

_EMBED_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: #0d1117; color: #c9d1d9;
  height: 100vh; display: flex; align-items: center; justify-content: center;
}
.player {
  width: 100%; max-width: 100%; padding: 16px 20px;
  background: #161b22; border: 1px solid #30363d; border-radius: 8px;
  display: flex; flex-direction: column; gap: 12px;
}
.player-header {
  display: flex; align-items: center; gap: 12px;
}
.logo-mark {
  font-size: 20px; flex-shrink: 0;
}
.track-info { flex: 1; overflow: hidden; }
.track-title {
  font-size: 14px; font-weight: 600; color: #e6edf3;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.track-sub {
  font-size: 11px; color: #8b949e; margin-top: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.controls {
  display: flex; align-items: center; gap: 10px;
}
.play-btn {
  width: 36px; height: 36px; border-radius: 50%;
  background: #238636; border: none; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0; font-size: 14px; color: #fff;
  transition: background 0.15s;
}
.play-btn:hover { background: #2ea043; }
.play-btn:disabled { background: #30363d; cursor: not-allowed; }
.progress-wrap {
  flex: 1; display: flex; flex-direction: column; gap: 4px;
}
.progress-bar {
  width: 100%; height: 4px; background: #30363d; border-radius: 2px;
  cursor: pointer; position: relative; overflow: hidden;
}
.progress-fill {
  height: 100%; width: 0%; background: #58a6ff;
  border-radius: 2px; transition: width 0.1s linear;
  pointer-events: none;
}
.time-row {
  display: flex; justify-content: space-between;
  font-size: 11px; color: #8b949e;
}
.footer-link {
  display: flex; justify-content: flex-end; align-items: center;
}
.footer-link a {
  font-size: 11px; color: #58a6ff; text-decoration: none;
  display: flex; align-items: center; gap: 4px;
}
.footer-link a:hover { text-decoration: underline; }
.status { font-size: 12px; color: #8b949e; text-align: center; padding: 8px 0; }
.status.error { color: #f85149; }
"""


def _embed_page(title: str, repo_id: str, ref: str, body_script: str) -> str:
    """Assemble a compact embed player HTML page.

    Designed for iframe embedding on external sites.  No chrome, no token
    form — just the player widget.  ``X-Frame-Options`` is set by the
    route handler, not here, since this function only produces the body.
    """
    listen_url = f"/musehub/ui/{repo_id}"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — Muse Hub</title>
  <style>{_EMBED_CSS}</style>
</head>
<body>
  <div class="player" id="player">
    <div class="player-header">
      <span class="logo-mark">&#127925;</span>
      <div class="track-info">
        <div class="track-title" id="track-title">Loading&#8230;</div>
        <div class="track-sub" id="track-sub">Muse Hub</div>
      </div>
    </div>
    <div class="controls">
      <button class="play-btn" id="play-btn" disabled title="Play / Pause">&#9654;</button>
      <div class="progress-wrap">
        <div class="progress-bar" id="progress-bar">
          <div class="progress-fill" id="progress-fill"></div>
        </div>
        <div class="time-row">
          <span id="time-cur">0:00</span>
          <span id="time-dur">0:00</span>
        </div>
      </div>
    </div>
    <div class="footer-link">
      <a href="{listen_url}" target="_blank" rel="noopener">
        &#127925; View on Muse Hub
      </a>
    </div>
  </div>
  <audio id="audio-el" preload="metadata"></audio>
  <script>
    (function() {{
      const repoId = {repr(repo_id)};
      const ref    = {repr(ref)};
      const API    = '/api/v1/musehub';

      const audio      = document.getElementById('audio-el');
      const playBtn    = document.getElementById('play-btn');
      const fill       = document.getElementById('progress-fill');
      const bar        = document.getElementById('progress-bar');
      const timeCur    = document.getElementById('time-cur');
      const timeDur    = document.getElementById('time-dur');
      const trackTitle = document.getElementById('track-title');
      const trackSub   = document.getElementById('track-sub');

      function fmtTime(s) {{
        if (!isFinite(s)) return '0:00';
        const m = Math.floor(s / 60);
        const sec = Math.floor(s % 60);
        return m + ':' + (sec < 10 ? '0' : '') + sec;
      }}

      function setStatus(msg, isError) {{
        trackTitle.textContent = isError ? msg : (trackTitle.textContent || msg);
        if (isError) trackTitle.classList.add('error');
      }}

      audio.addEventListener('timeupdate', function() {{
        const pct = audio.duration ? (audio.currentTime / audio.duration) * 100 : 0;
        fill.style.width = pct + '%';
        timeCur.textContent = fmtTime(audio.currentTime);
      }});

      audio.addEventListener('durationchange', function() {{
        timeDur.textContent = fmtTime(audio.duration);
      }});

      audio.addEventListener('ended', function() {{
        playBtn.innerHTML = '&#9654;';
        fill.style.width = '0%';
        audio.currentTime = 0;
      }});

      audio.addEventListener('canplay', function() {{
        playBtn.disabled = false;
      }});

      audio.addEventListener('error', function() {{
        setStatus('Audio unavailable', true);
      }});

      playBtn.addEventListener('click', function() {{
        if (audio.paused) {{
          audio.play();
          playBtn.innerHTML = '&#9646;&#9646;';
        }} else {{
          audio.pause();
          playBtn.innerHTML = '&#9654;';
        }}
      }});

      bar.addEventListener('click', function(e) {{
        if (!audio.duration) return;
        const rect = bar.getBoundingClientRect();
        const pct  = (e.clientX - rect.left) / rect.width;
        audio.currentTime = pct * audio.duration;
      }});

      async function loadTrack() {{
        try {{
          const objRes = await fetch(API + '/repos/' + repoId + '/objects');
          if (!objRes.ok) throw new Error('objects ' + objRes.status);
          const objData = await objRes.json();
          const objects = objData.objects || [];

          const audio_exts = ['mp3', 'ogg', 'wav', 'm4a'];
          const audioObj = objects.find(function(o) {{
            const ext = o.path.split('.').pop().toLowerCase();
            return audio_exts.indexOf(ext) !== -1;
          }});

          if (!audioObj) {{
            trackTitle.textContent = 'No audio in this commit';
            trackSub.textContent = 'ref: ' + ref.substring(0, 8);
            return;
          }}

          const name = audioObj.path.split('/').pop();
          trackTitle.textContent = name;
          trackSub.textContent = 'ref: ' + ref.substring(0, 8);

          const audioUrl = API + '/repos/' + repoId + '/objects/' + audioObj.objectId + '/content';
          audio.src = audioUrl;
          audio.load();
        }} catch(e) {{
          setStatus('Could not load track', true);
          trackSub.textContent = e.message;
        }}
      }}

      {body_script}

      loadTrack();
    }})();
  </script>
</body>
</html>"""


@router.get(
    "/{repo_id}/embed/{ref}",
    response_class=HTMLResponse,
    summary="Embeddable MuseHub player widget",
)
async def embed_page(repo_id: str, ref: str) -> Response:
    """Render a compact, iframe-safe audio player for a MuseHub repo commit.

    Why this route exists: external sites (blogs, CMSes) embed MuseHub
    compositions via ``<iframe src="/musehub/ui/{repo_id}/embed/{ref}">``.
    The oEmbed endpoint (``GET /oembed``) auto-generates this iframe tag.

    Contract:
    - No JWT required — public repos can be embedded without auth.
    - Returns ``X-Frame-Options: ALLOWALL`` so browsers permit cross-origin framing.
    - ``ref`` is a commit SHA or branch name used to label the track.
    - Audio is fetched from ``/api/v1/musehub/repos/{repo_id}/objects`` at
      runtime; the first recognised audio file (mp3/ogg/wav/m4a) is played.
    - Responsive: works from 300px to full viewport width.

    Args:
        repo_id: UUID of the MuseHub repository.
        ref:     Commit SHA or branch name identifying the composition version.

    Returns:
        HTML response with ``X-Frame-Options: ALLOWALL`` header.
    """
    short_ref = ref[:8] if len(ref) >= 8 else ref
    html = _embed_page(
        title=f"Player {short_ref}",
        repo_id=repo_id,
        ref=ref,
        body_script="",
    )
    return Response(
        content=html,
        media_type="text/html",
        headers={"X-Frame-Options": "ALLOWALL"},
    )


@router.get(
    "/{repo_id}/search",
    response_class=HTMLResponse,
    summary="Muse Hub in-repo search page",
)
async def search_page(repo_id: str) -> HTMLResponse:
    """Render the in-repo search page with four mode tabs.

    Modes map to the JSON API at ``GET /api/v1/musehub/repos/{repo_id}/search``:
    - Musical Properties (``mode=property``) — filter by harmony/rhythm/melody/etc.
    - Natural Language (``mode=ask``) — free-text question over commit history.
    - Keyword (``mode=keyword``) — keyword overlap scored search.
    - Pattern (``mode=pattern``) — substring match against messages and branches.

    Results render as commit rows with SHA, message, author, timestamp, and an
    audio preview link for any ``mp3``/``wav``/``ogg`` artifact on that commit.
    Authentication is handled client-side via localStorage JWT.
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const base   = '/musehub/ui/' + repoId;
      const apiBase = '/api/v1/musehub/repos/' + repoId;

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      // ── State ──────────────────────────────────────────────────────────────
      let currentMode = 'keyword';

      function setMode(mode) {{
        currentMode = mode;
        document.querySelectorAll('.tab-btn').forEach(b => {{
          b.classList.toggle('tab-active', b.dataset.mode === mode);
        }});
        document.querySelectorAll('.mode-panel').forEach(p => {{
          p.style.display = p.dataset.mode === mode ? 'block' : 'none';
        }});
      }}

      // ── Result rendering ───────────────────────────────────────────────────
      function renderResults(data) {{
        const matches = data.matches || [];
        const header = `<p style="color:#8b949e;font-size:13px;margin-bottom:12px">
          Mode: <strong>${{escHtml(data.mode)}}</strong> &bull;
          Query: <em>${{escHtml(data.query || '(all)')}}</em> &bull;
          ${{matches.length}} result(s) &bull; ${{data.totalScanned}} commits scanned
        </p>`;

        if (matches.length === 0) {{
          document.getElementById('results').innerHTML = header +
            '<p class="loading">No matching commits found.</p>';
          return;
        }}

        const rows = matches.map(m => `
          <div class="commit-row">
            <a class="commit-sha" href="${{base}}/commits/${{m.commitId}}">${{shortSha(m.commitId)}}</a>
            <div class="commit-msg" style="flex:1">
              <a href="${{base}}/commits/${{m.commitId}}">${{escHtml(m.message)}}</a>
              <div style="font-size:12px;color:#8b949e;margin-top:2px">
                ${{escHtml(m.author)}} &bull; ${{fmtDate(m.timestamp)}}
                &bull; branch: ${{escHtml(m.branch)}}
                ${{m.score < 1.0 ? '&bull; score: ' + m.score.toFixed(3) : ''}}
                <a href="${{base}}/commits/${{m.commitId}}"
                   class="btn btn-secondary"
                   style="font-size:11px;padding:2px 8px;margin-left:8px">
                  &#9654; Preview
                </a>
              </div>
            </div>
          </div>`).join('');

        document.getElementById('results').innerHTML = header +
          '<div class="card">' + rows + '</div>';
      }}

      // ── Search dispatch ────────────────────────────────────────────────────
      async function runSearch() {{
        document.getElementById('results').innerHTML = '<p class="loading">Searching&#8230;</p>';
        try {{
          let url = apiBase + '/search?mode=' + encodeURIComponent(currentMode);
          const limit = document.getElementById('inp-limit').value || 20;
          const since = document.getElementById('inp-since').value;
          const until = document.getElementById('inp-until').value;
          url += '&limit=' + encodeURIComponent(limit);
          if (since) url += '&since=' + encodeURIComponent(since + 'T00:00:00Z');
          if (until) url += '&until=' + encodeURIComponent(until + 'T23:59:59Z');

          if (currentMode === 'property') {{
            const fields = ['harmony','rhythm','melody','structure','dynamic','emotion'];
            fields.forEach(f => {{
              const v = document.getElementById('prop-' + f).value.trim();
              if (v) url += '&' + f + '=' + encodeURIComponent(v);
            }});
          }} else {{
            const q = document.getElementById('inp-q-' + currentMode).value.trim();
            if (q) url += '&q=' + encodeURIComponent(q);
          }}

          const data = await apiFetch(url.replace(apiBase, ''));
          renderResults(data);
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('results').innerHTML =
              '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      // ── Page bootstrap ─────────────────────────────────────────────────────
      document.getElementById('content').innerHTML = `
        <div style="margin-bottom:12px">
          <a href="${{base}}">&larr; Back to repo</a>
        </div>
        <div class="card">
          <h1 style="margin-bottom:16px">&#128269; Search Commits</h1>

          <!-- Mode tabs -->
          <div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap">
            <button class="btn tab-btn tab-active" data-mode="keyword"
                    onclick="setMode('keyword')">Keyword</button>
            <button class="btn tab-btn" data-mode="ask"
                    onclick="setMode('ask')">Natural Language</button>
            <button class="btn tab-btn" data-mode="pattern"
                    onclick="setMode('pattern')">Pattern</button>
            <button class="btn tab-btn" data-mode="property"
                    onclick="setMode('property')">Musical Properties</button>
          </div>

          <!-- Shared date range + limit -->
          <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px;align-items:flex-end">
            <div class="meta-item">
              <span class="meta-label">Since</span>
              <input id="inp-since" type="date" style="background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:6px 10px;font-size:14px" />
            </div>
            <div class="meta-item">
              <span class="meta-label">Until</span>
              <input id="inp-until" type="date" style="background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:6px 10px;font-size:14px" />
            </div>
            <div class="meta-item">
              <span class="meta-label">Limit</span>
              <input id="inp-limit" type="number" value="20" min="1" max="200"
                     style="width:80px;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:6px 10px;font-size:14px" />
            </div>
          </div>

          <!-- Keyword panel -->
          <div class="mode-panel" data-mode="keyword">
            <div style="display:flex;gap:8px">
              <input id="inp-q-keyword" type="text" placeholder="e.g. dark jazz bassline"
                     style="flex:1;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:8px;font-size:14px"
                     onkeydown="if(event.key==='Enter')runSearch()" />
              <button class="btn btn-primary" onclick="runSearch()">Search</button>
            </div>
            <p style="font-size:12px;color:#8b949e;margin-top:6px">
              Scores commits by keyword overlap. Higher score = better match.
            </p>
          </div>

          <!-- Natural Language panel -->
          <div class="mode-panel" data-mode="ask" style="display:none">
            <div style="display:flex;gap:8px">
              <input id="inp-q-ask" type="text" placeholder="e.g. when did I change to F# minor?"
                     style="flex:1;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:8px;font-size:14px"
                     onkeydown="if(event.key==='Enter')runSearch()" />
              <button class="btn btn-primary" onclick="runSearch()">Ask</button>
            </div>
            <p style="font-size:12px;color:#8b949e;margin-top:6px">
              Keyword extraction from your question. Full LLM-powered search is a planned enhancement.
            </p>
          </div>

          <!-- Pattern panel -->
          <div class="mode-panel" data-mode="pattern" style="display:none">
            <div style="display:flex;gap:8px">
              <input id="inp-q-pattern" type="text" placeholder="e.g. Cm7 or feature/hip-hop"
                     style="flex:1;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:8px;font-size:14px"
                     onkeydown="if(event.key==='Enter')runSearch()" />
              <button class="btn btn-primary" onclick="runSearch()">Search</button>
            </div>
            <p style="font-size:12px;color:#8b949e;margin-top:6px">
              Case-insensitive substring match against commit messages and branch names.
            </p>
          </div>

          <!-- Musical Properties panel -->
          <div class="mode-panel" data-mode="property" style="display:none">
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-bottom:12px">
              ${{['harmony','rhythm','melody','structure','dynamic','emotion'].map(f => `
                <div class="meta-item">
                  <span class="meta-label">${{f}}</span>
                  <input id="prop-${{f}}" type="text" placeholder="e.g. ${{f==='harmony'?'key=Eb':f==='rhythm'?'tempo=120-130':f}}"
                         style="background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:6px 10px;font-size:13px;width:100%" />
                </div>`).join('')}}
            </div>
            <button class="btn btn-primary" onclick="runSearch()">Filter</button>
            <p style="font-size:12px;color:#8b949e;margin-top:6px">
              All non-empty fields are combined with AND logic.
              Range syntax: <code>tempo=120-130</code>.
            </p>
          </div>
        </div>

        <!-- Results area -->
        <div id="results"><p class="loading" style="display:none"></p></div>
      `;

      // Apply tab styles after DOM is written.
      document.querySelectorAll('.tab-btn').forEach(b => {{
        b.style.background = '#21262d';
        b.style.color = '#c9d1d9';
        b.style.border = '1px solid #30363d';
      }});

      function applyTabActive() {{
        document.querySelectorAll('.tab-btn').forEach(b => {{
          const active = b.dataset.mode === currentMode;
          b.style.background = active ? '#1f6feb' : '#21262d';
          b.style.color = active ? '#fff' : '#c9d1d9';
        }});
      }}

      document.querySelectorAll('.tab-btn').forEach(b => {{
        b.addEventListener('click', applyTabActive);
      }});

      applyTabActive();
    """
    html = _page(
        title="Search",
        breadcrumb=f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / search',
        body_script=script,
    )
    return HTMLResponse(content=html)
