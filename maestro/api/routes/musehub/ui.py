"""Muse Hub web UI route handlers.

Serves browser-readable HTML pages for navigating a Muse Hub repo —
analogous to GitHub's repository browser but for music projects.

Endpoint summary:
  GET /musehub/ui/search                           — global cross-repo search page
  GET /musehub/ui/{repo_id}                        — repo page (branch selector + commit log)
  GET /musehub/ui/{repo_id}/commits/{commit_id}    — commit detail page (metadata + artifacts)
  GET /musehub/ui/{repo_id}/graph                  — interactive DAG commit graph
  GET /musehub/ui/{repo_id}/pulls                  — pull request list page
  GET /musehub/ui/{repo_id}/pulls/{pr_id}          — PR detail page (with merge button)
  GET /musehub/ui/{repo_id}/issues                 — issue list page
  GET /musehub/ui/{repo_id}/issues/{number}        — issue detail page (with close button)
  GET /musehub/ui/{repo_id}/credits                — dynamic credits page (album liner notes)
  GET /musehub/ui/{repo_id}/embed/{ref}            — embeddable player widget (no auth, iframe-safe)
  GET /musehub/ui/{repo_id}/search                 — in-repo search page (four modes)

These routes require NO JWT auth — they return static HTML shells whose
embedded JavaScript fetches data from the authed JSON API
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


def _page(title: str, breadcrumb: str, body_script: str, extra_css: str = "") -> str:
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
# Route handlers
# ---------------------------------------------------------------------------


@router.get("/search", response_class=HTMLResponse, summary="Muse Hub global search page")
async def global_search_page(
    q: str = "",
    mode: str = "keyword",
) -> HTMLResponse:
    """Render the global cross-repo search page.

    The page is a static HTML shell; JavaScript fetches results from
    ``GET /api/v1/musehub/search`` using the stored localStorage JWT.

    Query parameters are pre-filled into the search form so that a browser
    navigation or a URL share lands with the last query already populated.
    These parameters are sanitised client-side before being rendered into the
    DOM — ``escHtml`` prevents XSS from adversarial query strings.
    """
    safe_q = q.replace("'", "\\'").replace('"', '\\"').replace("\n", "").replace("\r", "")
    safe_mode = mode if mode in ("keyword", "pattern") else "keyword"
    script = f"""
      const INITIAL_Q    = {repr(safe_q)};
      const INITIAL_MODE = {repr(safe_mode)};

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      function audioHtml(groupId, audioOid) {{
        if (!audioOid) return '';
        const url = '/api/v1/musehub/repos/' + encodeURIComponent(groupId) + '/objects/' + encodeURIComponent(audioOid) + '/content';
        return '<audio controls src="' + url + '" style="width:100%;margin-top:6px"></audio>';
      }}

      function renderGroups(groups) {{
        if (!groups || groups.length === 0) {{
          return '<p class="loading">No results found.</p>';
        }}
        return groups.map(g => {{
          const matchRows = g.matches.map(m => `
            <div class="commit-row">
              <a class="commit-sha" href="/musehub/ui/${{encodeURIComponent(g.repoId)}}/commits/${{escHtml(m.commitId)}}">${{shortSha(m.commitId)}}</a>
              <span class="commit-msg">${{escHtml(m.message)}}</span>
              <span class="commit-meta">${{escHtml(m.author)}} &bull; ${{fmtDate(m.timestamp)}}</span>
            </div>`).join('');

          const moreNote = g.totalMatches > g.matches.length
            ? `<p style="font-size:12px;color:#8b949e;margin-top:6px">Showing ${{g.matches.length}} of ${{g.totalMatches}} matches in this repo.</p>`
            : '';

          return `
            <div class="card">
              <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
                <h2 style="margin:0">
                  <a href="/musehub/ui/${{encodeURIComponent(g.repoId)}}">${{escHtml(g.repoName)}}</a>
                </h2>
                <span class="badge badge-open" style="font-size:11px">${{escHtml(g.repoVisibility)}}</span>
                <span style="font-size:12px;color:#8b949e">owner: ${{escHtml(g.repoOwner)}}</span>
              </div>
              ${{matchRows}}
              ${{moreNote}}
              ${{audioHtml(g.repoId, g.matches[0] && g.matches[0].audioObjectId)}}
            </div>`;
        }}).join('');
      }}

      async function search(page) {{
        const q    = document.getElementById('q-input').value.trim();
        const mode = document.getElementById('mode-sel').value;
        if (!q) {{ document.getElementById('results').innerHTML = ''; return; }}

        document.getElementById('results').innerHTML = '<p class="loading">Searching&#8230;</p>';
        try {{
          const params = new URLSearchParams({{ q, mode, page: page || 1, page_size: 10 }});
          const data = await apiFetch('/search?' + params.toString());
          const groups = data.groups || [];
          const total  = data.totalReposSearched || 0;
          const pg     = data.page || 1;
          const ps     = data.pageSize || 10;

          const summary = `<p style="font-size:13px;color:#8b949e;margin-bottom:12px">
            ${{groups.length}} repo${{groups.length !== 1 ? 's' : ''}} with matches
            &mdash; ${{total}} public repo${{total !== 1 ? 's' : ''}} searched
            (page ${{pg}})
          </p>`;

          const prevBtn = pg > 1
            ? `<button class="btn btn-secondary" style="font-size:13px" onclick="search(${{pg-1}})">&#8592; Prev</button>`
            : '';
          const nextBtn = groups.length === ps
            ? `<button class="btn btn-secondary" style="font-size:13px" onclick="search(${{pg+1}})">Next &#8594;</button>`
            : '';
          const pager = (prevBtn || nextBtn)
            ? `<div style="display:flex;gap:8px;margin-top:12px">${{prevBtn}}${{nextBtn}}</div>`
            : '';

          document.getElementById('results').innerHTML = summary + renderGroups(groups) + pager;

          // Update URL bar without reload so the search is shareable
          const url = new URL(window.location.href);
          url.searchParams.set('q', q);
          url.searchParams.set('mode', mode);
          history.replaceState(null, '', url.toString());
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('results').innerHTML = '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      // Pre-fill form from URL params / server-injected values
      document.getElementById('q-input').value    = INITIAL_Q;
      document.getElementById('mode-sel').value   = INITIAL_MODE;

      if (INITIAL_Q) {{ search(1); }}

      document.getElementById('search-form').addEventListener('submit', function(e) {{
        e.preventDefault();
        search(1);
      }});
    """

    body_html = """
      <div class="card" style="margin-bottom:16px">
        <h1 style="margin-bottom:12px">&#128269; Global Search</h1>
        <form id="search-form" style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end">
          <input id="q-input" type="text" placeholder="Search commit messages&#8230;"
                 style="flex:1;min-width:200px;background:#0d1117;color:#c9d1d9;
                        border:1px solid #30363d;border-radius:6px;padding:8px 12px;font-size:14px" />
          <select id="mode-sel"
                  style="background:#21262d;color:#c9d1d9;border:1px solid #30363d;
                         border-radius:6px;padding:8px 10px;font-size:14px">
            <option value="keyword">Keyword</option>
            <option value="pattern">Pattern (LIKE)</option>
          </select>
          <button class="btn btn-primary" type="submit">Search</button>
        </form>
      </div>
      <div id="results"></div>
    """

    # Embed body_html as a static section before the dynamic script runs
    full_script = (
        f"document.getElementById('content').innerHTML = {repr(body_html)};\n" + script
    )
    html = _page(
        title="Global Search",
        breadcrumb='<a href="/musehub/ui/search">Global Search</a>',
        body_script=full_script,
    )
    return HTMLResponse(content=html)


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
                <a href="${{base}}/releases" class="btn btn-secondary">Releases</a>
                <a href="${{base}}/credits" class="btn btn-secondary">&#127926; Credits</a>
                <a href="${{base}}/sessions" class="btn btn-secondary">Sessions</a>
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
    "/{repo_id}/credits",
    response_class=HTMLResponse,
    summary="Muse Hub dynamic credits page",
)
async def credits_page(repo_id: str) -> HTMLResponse:
    """Render the dynamic credits page — album liner notes for the repo.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/credits`` and displays
    every contributor with their session count, inferred roles, and activity
    timeline.  Sort can be toggled via a dropdown (count / recency / alpha).

    Embeds a ``<script type="application/ld+json">`` block for machine-readable
    attribution using schema.org ``MusicComposition`` vocabulary.

    Auth is handled client-side via localStorage JWT, matching all other UI pages.
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const base   = '/musehub/ui/' + repoId;


      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      function fmtYear(iso) {{
        if (!iso) return '—';
        return new Date(iso).getFullYear();
      }}

      function contributorRow(c) {{
        const roles = (c.contributionTypes || []).map(r =>
          '<span class="label">' + escHtml(r) + '</span>'
        ).join(' ');
        const window = fmtDate(c.firstActive) + ' &ndash; ' + fmtDate(c.lastActive);
        return `
          <div class="commit-row" style="align-items:flex-start;flex-direction:column;gap:6px">
            <div style="display:flex;align-items:center;gap:10px;width:100%">
              <span style="font-size:15px;color:#e6edf3;font-weight:600;flex:1">
                ${{escHtml(c.author)}}
              </span>
              <span class="badge badge-open" style="font-size:12px;background:#1a3a5c">
                ${{c.sessionCount}} session${{c.sessionCount !== 1 ? 's' : ''}}
              </span>
            </div>
            <div>${{roles}}</div>
            <div style="font-size:12px;color:#8b949e">${{window}}</div>
          </div>`;
      }}

      function injectJsonLd(credits) {{
        const contributors = (credits.contributors || []).map(c => ({{
          '@type': 'Person',
          name: c.author,
          roleName: (c.contributionTypes || []).join(', '),
        }}));
        const ld = {{
          '@context': 'https://schema.org',
          '@type': 'MusicComposition',
          identifier: credits.repoId,
          contributor: contributors,
        }};
        const el = document.createElement('script');
        el.type = 'application/ld+json';
        el.textContent = JSON.stringify(ld, null, 2);
        document.head.appendChild(el);
      }}

      async function load(sort) {{
        try {{
          const credits = await apiFetch('/repos/' + repoId + '/credits?sort=' + sort);
          const contributors = credits.contributors || [];

          injectJsonLd(credits);

          const rows = contributors.length === 0
            ? '<p class="loading">No sessions recorded yet. Start a session with <code>muse session start</code>.</p>'
            : contributors.map(contributorRow).join('');

          document.getElementById('content').innerHTML = `
            <div style="margin-bottom:12px">
              <a href="${{base}}">&larr; Back to repo</a>
            </div>
            <div class="card">
              <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;flex-wrap:wrap">
                <h1 style="margin:0">&#127926; Credits</h1>
                <span style="flex:1"></span>
                <span style="font-size:13px;color:#8b949e">
                  ${{credits.totalContributors}} contributor${{credits.totalContributors !== 1 ? 's' : ''}}
                </span>
                <label style="font-size:13px;color:#8b949e;display:flex;align-items:center;gap:6px">
                  Sort:
                  <select onchange="load(this.value)">
                    <option value="count"   ${{sort==='count'  ?'selected':''}}>Most prolific</option>
                    <option value="recency" ${{sort==='recency'?'selected':''}}>Most recent</option>
                    <option value="alpha"   ${{sort==='alpha'  ?'selected':''}}>A &ndash; Z</option>
                  </select>
                </label>
              </div>
              ${{rows}}
            </div>
            <p style="font-size:11px;color:#8b949e;margin-top:8px;text-align:center">
              Machine-readable credits embedded as JSON-LD (schema.org/MusicComposition)
            </p>`;
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('content').innerHTML = '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      load('count');
    """
    html = _page(
        title="Credits",
        breadcrumb=f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / credits',
        body_script=script,
    )
    return HTMLResponse(content=html)


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


_PROFILE_CSS = """
.profile-header {
  display: flex; align-items: flex-start; gap: 24px; margin-bottom: 24px;
}
.avatar {
  width: 80px; height: 80px; border-radius: 50%;
  background: #21262d; border: 2px solid #30363d;
  display: flex; align-items: center; justify-content: center;
  font-size: 32px; flex-shrink: 0;
}
.avatar img { width: 100%; height: 100%; border-radius: 50%; object-fit: cover; }
.profile-meta { flex: 1; }
.profile-meta h1 { font-size: 22px; color: #e6edf3; margin-bottom: 4px; }
.bio { font-size: 14px; color: #8b949e; margin-bottom: 12px; }
.contrib-graph {
  display: flex; gap: 2px; flex-wrap: wrap; overflow-x: auto;
}
.contrib-week { display: flex; flex-direction: column; gap: 2px; }
.contrib-day {
  width: 10px; height: 10px; border-radius: 2px; background: #161b22;
  border: 1px solid #30363d;
}
.contrib-day[data-count="0"] { background: #161b22; }
.contrib-day[data-count="1"] { background: #0e4429; border-color: #0e4429; }
.contrib-day[data-count="2"] { background: #006d32; border-color: #006d32; }
.contrib-day[data-count="3"] { background: #26a641; border-color: #26a641; }
.contrib-day[data-count="4"] { background: #39d353; border-color: #39d353; }
.repo-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.repo-card {
  background: #161b22; border: 1px solid #30363d; border-radius: 6px;
  padding: 14px; display: flex; flex-direction: column; gap: 6px;
}
.repo-card h3 { font-size: 15px; margin: 0; }
.repo-card .repo-meta { font-size: 12px; color: #8b949e; }
.credits-badge {
  display: inline-flex; align-items: center; gap: 8px;
  background: #1f6feb22; border: 1px solid #1f6feb; border-radius: 6px;
  padding: 8px 14px; font-size: 14px;
}
.credits-badge .num { font-size: 22px; font-weight: 700; color: #58a6ff; }
"""


_TIMELINE_CSS = """
.timeline-toolbar {
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  margin-bottom: 16px; padding: 12px 16px;
  background: #161b22; border: 1px solid #30363d; border-radius: 6px;
}
.layer-toggle {
  display: flex; align-items: center; gap: 6px; cursor: pointer;
  font-size: 13px; color: #c9d1d9; user-select: none;
}
.layer-toggle input[type=checkbox] { cursor: pointer; accent-color: #58a6ff; }
.zoom-select { display: flex; align-items: center; gap: 8px; margin-left: auto; }
#timeline-svg-container {
  overflow-x: auto; background: #0d1117;
  border: 1px solid #30363d; border-radius: 6px; padding: 0;
}
#timeline-svg { display: block; }
.scrubber-bar {
  height: 4px; background: #30363d; border-radius: 2px; margin: 12px 16px;
  cursor: pointer; position: relative;
}
.scrubber-thumb {
  width: 14px; height: 14px; background: #58a6ff; border-radius: 50%;
  position: absolute; top: -5px; transform: translateX(-50%);
  cursor: grab; box-shadow: 0 0 0 2px #0d1117;
}
.tooltip {
  position: fixed; background: #21262d; border: 1px solid #30363d;
  border-radius: 6px; padding: 8px 12px; font-size: 12px; color: #c9d1d9;
  pointer-events: none; z-index: 1000; max-width: 260px;
  display: none;
}
.audio-modal {
  position: fixed; inset: 0; background: rgba(0,0,0,.6);
  display: flex; align-items: center; justify-content: center; z-index: 2000;
}
.audio-modal-box {
  background: #161b22; border: 1px solid #30363d; border-radius: 8px;
  padding: 24px; min-width: 300px; max-width: 480px;
}
.audio-modal-box h3 { margin-bottom: 12px; color: #e6edf3; font-size: 15px; }
.audio-modal-box audio { width: 100%; margin-bottom: 12px; }
"""


@router.get(
    "/users/{username}",
    response_class=HTMLResponse,
    summary="Muse Hub user profile page",
)
async def profile_page(username: str) -> HTMLResponse:
    """Render the public user profile page.

    Displays: bio, avatar, pinned repos, all public repos with last-activity,
    a GitHub-style contribution heatmap (52 weeks of daily commit counts), and
    aggregated session credits.  Auth is handled client-side — the profile
    itself is public; editing controls appear only when the visitor's JWT
    matches the profile owner.

    Returns 200 with an HTML shell even when the API returns 404 — the JS
    renders the 404 message inline so the browser gets a proper HTML response.
    """
    script = f"""
      const username = {repr(username)};
      const API_PROFILE = '/api/v1/musehub/users/' + username;

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      function bucketCount(n) {{
        if (n === 0) return 0;
        if (n <= 2)  return 1;
        if (n <= 5)  return 2;
        if (n <= 9)  return 3;
        return 4;
      }}

      function buildContribGraph(graph) {{
        // Group days into weeks (7 days per column)
        const weeks = [];
        let week = [];
        graph.forEach((d, i) => {{
          week.push(d);
          if (week.length === 7) {{ weeks.push(week); week = []; }}
        }});
        if (week.length) weeks.push(week);

        const weeksHtml = weeks.map(w => {{
          const days = w.map(d => {{
            const b = bucketCount(d.count);
            return `<div class="contrib-day" data-count="${{b}}" title="${{d.date}}: ${{d.count}} commit${{d.count !== 1 ? 's' : ''}}"></div>`;
          }}).join('');
          return `<div class="contrib-week">${{days}}</div>`;
        }}).join('');

        return `<div class="contrib-graph">${{weeksHtml}}</div>`;
      }}

      function repoCardHtml(r) {{
        const lastAct = r.lastActivityAt ? fmtDate(r.lastActivityAt) : 'No commits yet';
        return `<div class="repo-card">
          <h3><a href="/musehub/ui/${{r.repoId}}">${{escHtml(r.name)}}</a></h3>
          <div class="repo-meta">
            <span class="badge badge-${{r.visibility}}">${{r.visibility}}</span>
            &bull; Last activity: ${{lastAct}}
          </div>
        </div>`;
      }}

      async function load() {{
        let profile;
        try {{
          profile = await fetch(API_PROFILE).then(r => {{
            if (r.status === 404) throw new Error('404');
            if (!r.ok) throw new Error(r.status);
            return r.json();
          }});
        }} catch(e) {{
          if (e.message === '404') {{
            document.getElementById('content').innerHTML =
              '<div class="card"><p class="error">&#10005; No profile found for <strong>' + escHtml(username) + '</strong>.</p></div>';
          }} else {{
            document.getElementById('content').innerHTML =
              '<p class="error">Failed to load profile: ' + escHtml(e.message) + '</p>';
          }}
          return;
        }}

        const avatarHtml = profile.avatarUrl
          ? `<img src="${{escHtml(profile.avatarUrl)}}" alt="avatar" />`
          : '&#127925;';

        const pinnedRepos = (profile.repos || []).filter(r => (profile.pinnedRepoIds || []).includes(r.repoId));
        const publicRepos = profile.repos || [];

        const pinnedSection = pinnedRepos.length > 0 ? `
          <div class="card">
            <h2 style="margin-bottom:12px">&#128204; Pinned</h2>
            <div class="repo-grid">${{pinnedRepos.map(repoCardHtml).join('')}}</div>
          </div>` : '';

        const reposSection = publicRepos.length > 0 ? `
          <div class="card">
            <h2 style="margin-bottom:12px">&#127963; Public Repositories (${{publicRepos.length}})</h2>
            <div class="repo-grid">${{publicRepos.map(repoCardHtml).join('')}}</div>
          </div>` : '<div class="card"><p class="loading">No public repositories yet.</p></div>';

        const graphSection = `
          <div class="card">
            <h2 style="margin-bottom:12px">&#128200; Contribution Activity (last 52 weeks)</h2>
            ${{buildContribGraph(profile.contributionGraph || [])}}
            <p style="font-size:12px;color:#8b949e;margin-top:8px">
              Less &nbsp;
              <span style="display:inline-flex;gap:2px;vertical-align:middle">
                ${{[0,1,2,3,4].map(n => '<span class="contrib-day" data-count="' + n + '" style="display:inline-block"></span>').join('')}}
              </span>
              &nbsp; More
            </p>
          </div>`;

        document.getElementById('content').innerHTML = `
          <div class="card profile-header">
            <div class="avatar">${{avatarHtml}}</div>
            <div class="profile-meta">
              <h1>${{escHtml(profile.username)}}</h1>
              ${{profile.bio ? '<p class="bio">' + escHtml(profile.bio) + '</p>' : ''}}
              <div class="credits-badge">
                <span class="num">${{profile.sessionCredits || 0}}</span>
                <span>session credits</span>
              </div>
            </div>
          </div>
          ${{pinnedSection}}
          ${{graphSection}}
          ${{reposSection}}
        `;
      }}

      load();
    """
    html = _page(
        title=f"@{username}",
        breadcrumb=f'<a href="/musehub/ui/users/{username}">@{username}</a>',
        body_script=script,
        extra_css=_PROFILE_CSS,
    )
    return HTMLResponse(content=html)


@router.get(
    "/{repo_id}/divergence",
    response_class=HTMLResponse,
    summary="Muse Hub divergence visualization page",
)
async def divergence_page(repo_id: str) -> HTMLResponse:
    """Render the divergence visualization page: radar chart + dimension detail panels.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/divergence?branch_a=...&branch_b=...``
    for the raw data, then renders:
    - A five-axis radar chart (melodic/harmonic/rhythmic/structural/dynamic)
    - Level labels (NONE/LOW/MED/HIGH) per dimension
    - Overall divergence score as a percentage
    - Per-dimension detail panels (click to expand)

    Auth is handled client-side via localStorage JWT.  No Jinja2 required.
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const apiBase = '/api/v1/musehub/repos/' + repoId;
      const uiBase  = '/musehub/ui/' + repoId;

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      const DIMENSIONS = ['melodic','harmonic','rhythmic','structural','dynamic'];
      const LEVEL_COLOR = {{ NONE:'#1f6feb', LOW:'#388bfd', MED:'#f0883e', HIGH:'#f85149' }};
      const LEVEL_BG    = {{ NONE:'#0d2942', LOW:'#102a4c', MED:'#341a00', HIGH:'#3d0000' }};
      const AXIS_LABELS = {{
        melodic:'Melodic', harmonic:'Harmonic', rhythmic:'Rhythmic',
        structural:'Structural', dynamic:'Dynamic'
      }};

      function levelBadge(level) {{
        const color = LEVEL_COLOR[level] || '#8b949e';
        return `<span style="display:inline-block;padding:1px 7px;border-radius:10px;
          font-size:11px;font-weight:700;color:#fff;background:${{color}}">${{level}}</span>`;
      }}

      function radarSvg(dims) {{
        const cx = 180, cy = 180, r = 140;
        const n = dims.length;
        const pts = dims.map((d, i) => {{
          const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
          const sr = d.score * r;
          return {{ x: cx + sr * Math.cos(angle), y: cy + sr * Math.sin(angle) }};
        }});
        const bgPts = DIMENSIONS.map((_, i) => {{
          const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
          return `${{cx + r * Math.cos(angle)}},${{cy + r * Math.sin(angle)}}`;
        }}).join(' ');
        const scorePoly = pts.map(p => `${{p.x}},${{p.y}}`).join(' ');

        const axisLines = DIMENSIONS.map((_, i) => {{
          const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
          const ex = cx + r * Math.cos(angle), ey = cy + r * Math.sin(angle);
          return `<line x1="${{cx}}" y1="${{cy}}" x2="${{ex}}" y2="${{ey}}"
            stroke="#30363d" stroke-width="1"/>`;
        }}).join('');

        const gridLines = [0.25, 0.5, 0.75, 1.0].map(frac => {{
          const gPts = DIMENSIONS.map((_, i) => {{
            const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
            return `${{cx + frac * r * Math.cos(angle)}},${{cy + frac * r * Math.sin(angle)}}`;
          }}).join(' ');
          return `<polygon points="${{gPts}}" fill="none" stroke="#21262d" stroke-width="1"/>`;
        }}).join('');

        const labels = dims.map((d, i) => {{
          const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
          const lx = cx + (r + 22) * Math.cos(angle);
          const ly = cy + (r + 22) * Math.sin(angle);
          const color = LEVEL_COLOR[d.level] || '#8b949e';
          return `<text x="${{lx}}" y="${{ly + 4}}" text-anchor="middle"
            font-size="12" fill="${{color}}" font-family="system-ui">${{AXIS_LABELS[d.dimension]}}</text>`;
        }}).join('');

        const dots = pts.map((p, i) => {{
          const color = LEVEL_COLOR[dims[i].level] || '#58a6ff';
          return `<circle cx="${{p.x}}" cy="${{p.y}}" r="4" fill="${{color}}" stroke="#0d1117" stroke-width="2"/>`;
        }}).join('');

        return `<svg viewBox="0 0 360 360" xmlns="http://www.w3.org/2000/svg"
            style="width:100%;max-width:360px;display:block;margin:0 auto">
          ${{gridLines}}${{axisLines}}
          <polygon points="${{bgPts}}" fill="rgba(88,166,255,0.04)" stroke="#30363d" stroke-width="1"/>
          <polygon points="${{scorePoly}}" fill="rgba(248,81,73,0.18)" stroke="#f85149" stroke-width="2"/>
          ${{labels}}${{dots}}
        </svg>`;
      }}

      function dimensionPanel(d, expanded) {{
        const bg = LEVEL_BG[d.level] || '#161b22';
        const id = 'dim-' + d.dimension;
        const detail = expanded ? `
          <div style="margin-top:10px;font-size:13px;color:#8b949e">
            <div>${{escHtml(d.description)}}</div>
            <div style="margin-top:6px;display:flex;gap:16px">
              <span>Branch A: <b style="color:#e6edf3">${{d.branchACommits}} commit(s)</b></span>
              <span>Branch B: <b style="color:#e6edf3">${{d.branchBCommits}} commit(s)</b></span>
            </div>
          </div>` : '';
        const pct = Math.round(d.score * 100);
        return `<div id="${{id}}" class="card" style="background:${{bg}};cursor:pointer;margin-bottom:8px"
            onclick="toggleDim('${{d.dimension}}')">
          <div style="display:flex;align-items:center;gap:12px">
            <span style="font-size:14px;color:#e6edf3;font-weight:600;min-width:90px">
              ${{AXIS_LABELS[d.dimension]}}</span>
            ${{levelBadge(d.level)}}
            <div style="flex:1;height:6px;background:#21262d;border-radius:3px;overflow:hidden">
              <div style="height:100%;width:${{pct}}%;background:${{LEVEL_COLOR[d.level] || '#58a6ff'}};
                border-radius:3px;transition:width .3s"></div>
            </div>
            <span style="font-size:13px;color:#8b949e;white-space:nowrap">${{pct}}%</span>
          </div>
          ${{detail}}
        </div>`;
      }}

      const _expanded = {{}};
      function toggleDim(dim) {{
        _expanded[dim] = !_expanded[dim];
        renderDims(window._lastDims || []);
      }}

      function renderDims(dims) {{
        window._lastDims = dims;
        document.getElementById('dim-panels').innerHTML =
          dims.map(d => dimensionPanel(d, !!_expanded[d.dimension])).join('');
      }}

      const params = new URLSearchParams(location.search);
      let _branchA = params.get('branch_a') || '';
      let _branchB = params.get('branch_b') || '';

      async function loadBranches() {{
        try {{
          const data = await apiFetch('/repos/' + repoId + '/branches');
          return (data.branches || []).map(b => b.name);
        }} catch(e) {{ return []; }}
      }}

      async function loadDivergence(bA, bB) {{
        if (!bA || !bB) {{
          document.getElementById('radar-area').innerHTML =
            '<p class="loading">Select two branches to compare.</p>';
          document.getElementById('dim-panels').innerHTML = '';
          document.getElementById('overall-area').innerHTML = '';
          return;
        }}
        document.getElementById('radar-area').innerHTML = '<p class="loading">Computing&#8230;</p>';
        try {{
          const d = await apiFetch('/repos/' + repoId + '/divergence?branch_a=' +
            encodeURIComponent(bA) + '&branch_b=' + encodeURIComponent(bB));
          const pct = Math.round((d.overallScore || 0) * 100);
          document.getElementById('radar-area').innerHTML = radarSvg(d.dimensions || []);
          document.getElementById('overall-area').innerHTML = `
            <div style="text-align:center;margin:12px 0">
              <div style="font-size:32px;font-weight:700;color:#e6edf3">${{pct}}%</div>
              <div style="font-size:12px;color:#8b949e;margin-top:2px">overall divergence</div>
              ${{d.commonAncestor ? `<div style="font-size:11px;color:#8b949e;margin-top:4px;font-family:monospace">
                base: ${{d.commonAncestor.substring(0,8)}}</div>` : ''}}
            </div>`;
          renderDims(d.dimensions || []);
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('radar-area').innerHTML =
              '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      async function load() {{
        const branches = await loadBranches();
        const opts = branches.map(b =>
          '<option value="' + escHtml(b) + '">' + escHtml(b) + '</option>').join('');

        document.getElementById('content').innerHTML = `
          <div style="margin-bottom:12px">
            <a href="${{uiBase}}">&larr; Back to repo</a>
          </div>
          <div class="card">
            <h1 style="margin-bottom:12px">Divergence Visualization</h1>
            <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px">
              <div>
                <div class="meta-label" style="font-size:11px;color:#8b949e;margin-bottom:4px">BRANCH A</div>
                <select id="sel-a" onchange="onBranchChange()">
                  <option value="">Select branch&#8230;</option>${{opts}}
                </select>
              </div>
              <div style="display:flex;align-items:flex-end;padding-bottom:4px;font-size:18px;color:#8b949e">
                vs
              </div>
              <div>
                <div class="meta-label" style="font-size:11px;color:#8b949e;margin-bottom:4px">BRANCH B</div>
                <select id="sel-b" onchange="onBranchChange()">
                  <option value="">Select branch&#8230;</option>${{opts}}
                </select>
              </div>
            </div>
            <div id="radar-area"><p class="loading">Select two branches to compare.</p></div>
            <div id="overall-area"></div>
          </div>
          <div id="dim-panels"></div>`;

        if (_branchA) document.getElementById('sel-a').value = _branchA;
        if (_branchB) document.getElementById('sel-b').value = _branchB;
        if (_branchA && _branchB) loadDivergence(_branchA, _branchB);
      }}

      function onBranchChange() {{
        _branchA = document.getElementById('sel-a').value;
        _branchB = document.getElementById('sel-b').value;
        const url = new URL(location.href);
        url.searchParams.set('branch_a', _branchA);
        url.searchParams.set('branch_b', _branchB);
        history.replaceState(null,'',url.toString());
        loadDivergence(_branchA, _branchB);
      }}

      load();
    """
    html = _page(
        title="Divergence",
        breadcrumb=f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / divergence',
        body_script=script,
    )
    return HTMLResponse(content=html)


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


@router.get(
    "/{repo_id}/timeline",
    response_class=HTMLResponse,
    summary="Muse Hub timeline page — chronological evolution",
)
async def timeline_page(repo_id: str) -> HTMLResponse:
    """Render the timeline page: layered chronological visualisation of a repo.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/timeline`` and renders four
    independently toggleable layers onto an SVG canvas:
    - Commits: markers with message on hover; click to preview audio
    - Emotion: valence/energy/tension line chart overlaid on the timeline
    - Sections: coloured section-change markers
    - Tracks: track add/remove markers

    A time scrubber at the bottom allows scrubbing through history.
    Zoom controls (day/week/month/all-time) adjust the visible window.
    Auth is handled client-side via localStorage JWT.
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const base   = '/musehub/ui/' + repoId;
      const API_TL = '/api/v1/musehub/repos/' + repoId + '/timeline';

      // ── State ───────────────────────────────────────────────────────────────
      let tlData = null;       // raw TimelineResponse from API
      let zoom   = 'all';      // day | week | month | all
      let layers = {{ commits: true, emotion: true, sections: true, tracks: true }};
      let scrubPct = 1.0;      // 0.0 = oldest, 1.0 = newest

      // SVG dimensions
      const SVG_H      = 320;
      const PAD_L      = 48;
      const PAD_R      = 24;
      const PAD_TOP    = 40;
      const PAD_BOT    = 40;
      const CHART_H    = SVG_H - PAD_TOP - PAD_BOT;  // usable vertical span
      const COMMIT_Y   = PAD_TOP + CHART_H * 0.5;    // baseline for commit markers
      const EMOTION_Y0 = PAD_TOP + CHART_H * 0.1;    // top of emotion area
      const EMOTION_YH = CHART_H * 0.35;             // height of emotion chart
      const MARKER_Y   = PAD_TOP + CHART_H * 0.72;   // section/track markers row

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      // ── Zoom filtering ───────────────────────────────────────────────────────
      function msForZoom(z) {{
        switch(z) {{
          case 'day':   return 24 * 3600 * 1000;
          case 'week':  return 7  * 24 * 3600 * 1000;
          case 'month': return 30 * 24 * 3600 * 1000;
          default:      return Infinity;
        }}
      }}

      function visibleCommits() {{
        if (!tlData || !tlData.commits || tlData.commits.length === 0) return [];
        const all = tlData.commits;
        const span = msForZoom(zoom);
        if (span === Infinity) return all;
        const newest = new Date(all[all.length - 1].timestamp).getTime();
        return all.filter(c => newest - new Date(c.timestamp).getTime() <= span);
      }}

      // ── SVG rendering ────────────────────────────────────────────────────────
      function tsToX(ts, tMin, tMax, svgW) {{
        if (tMax === tMin) return PAD_L + (svgW - PAD_L - PAD_R) / 2;
        return PAD_L + ((ts - tMin) / (tMax - tMin)) * (svgW - PAD_L - PAD_R);
      }}

      function renderTimeline() {{
        const container = document.getElementById('timeline-svg-container');
        if (!tlData) {{ container.innerHTML = '<p class="loading" style="padding:16px">Loading&#8230;</p>'; return; }}

        const vcs = visibleCommits();
        if (vcs.length === 0) {{
          container.innerHTML = '<p class="loading" style="padding:16px">No commits in this zoom window.</p>';
          return;
        }}

        const svgW = Math.max(container.clientWidth || 800, PAD_L + PAD_R + vcs.length * 28);
        const timestamps = vcs.map(c => new Date(c.timestamp).getTime());
        const tMin = Math.min(...timestamps);
        const tMax = Math.max(...timestamps);

        const visibleIds = new Set(vcs.map(c => c.commitId));

        let paths = '';
        let markers = '';
        let axisLabels = '';

        // ── Axis labels ──────────────────────────────────────────────────────
        const labelCount = Math.min(6, vcs.length);
        for (let i = 0; i < labelCount; i++) {{
          const idx = Math.round(i * (vcs.length - 1) / Math.max(1, labelCount - 1));
          const c = vcs[idx];
          const x = tsToX(new Date(c.timestamp).getTime(), tMin, tMax, svgW);
          const d = new Date(c.timestamp);
          const label = d.toLocaleDateString(undefined, {{ month:'short', day:'numeric' }});
          axisLabels += `<text x="${{x}}" y="${{SVG_H - 8}}" text-anchor="middle"
            font-size="10" fill="#8b949e">${{escHtml(label)}}</text>`;
        }}

        // ── Emotion line chart ───────────────────────────────────────────────
        if (layers.emotion && tlData.emotion) {{
          const visEmo = tlData.emotion.filter(e => visibleIds.has(e.commitId));
          const lineFor = (field, colour) => {{
            if (visEmo.length < 2) return '';
            const pts = visEmo.map(e => {{
              const x = tsToX(new Date(e.timestamp).getTime(), tMin, tMax, svgW);
              const y = EMOTION_Y0 + EMOTION_YH * (1 - e[field]);
              return x + ',' + y;
            }}).join(' ');
            return `<polyline points="${{pts}}" fill="none" stroke="${{colour}}"
              stroke-width="1.5" opacity="0.8" />`;
          }};
          paths += lineFor('valence', '#58a6ff');
          paths += lineFor('energy',  '#3fb950');
          paths += lineFor('tension', '#f78166');
          paths += `<text x="${{PAD_L}}" y="${{EMOTION_Y0 - 6}}" font-size="10" fill="#8b949e">Emotion</text>
            <circle cx="${{PAD_L + 52}}" cy="${{EMOTION_Y0 - 8}}" r="3" fill="#58a6ff"/>
            <text x="${{PAD_L + 58}}" y="${{EMOTION_Y0 - 5}}" font-size="9" fill="#58a6ff">valence</text>
            <circle cx="${{PAD_L + 102}}" cy="${{EMOTION_Y0 - 8}}" r="3" fill="#3fb950"/>
            <text x="${{PAD_L + 108}}" y="${{EMOTION_Y0 - 5}}" font-size="9" fill="#3fb950">energy</text>
            <circle cx="${{PAD_L + 148}}" cy="${{EMOTION_Y0 - 8}}" r="3" fill="#f78166"/>
            <text x="${{PAD_L + 154}}" y="${{EMOTION_Y0 - 5}}" font-size="9" fill="#f78166">tension</text>`;
        }}

        // ── Commit markers ───────────────────────────────────────────────────
        if (layers.commits) {{
          vcs.forEach(c => {{
            const x = tsToX(new Date(c.timestamp).getTime(), tMin, tMax, svgW);
            const sha = c.commitId.substring(0, 8);
            const msg = escHtml((c.message || '').substring(0, 60));
            const author = escHtml(c.author || '');
            const ts = new Date(c.timestamp).toLocaleString();
            markers += `
              <g class="commit-marker" data-id="${{c.commitId}}"
                 onclick="openAudioModal('${{c.commitId}}', '${{sha}}')"
                 style="cursor:pointer"
                 onmouseenter="showTip(event, '${{sha}}<br>${{msg}}<br>${{author}} &bull; ${{ts}}')"
                 onmouseleave="hideTip()">
                <line x1="${{x}}" y1="${{COMMIT_Y - 12}}" x2="${{x}}" y2="${{COMMIT_Y + 12}}"
                  stroke="#30363d" stroke-width="1" />
                <circle cx="${{x}}" cy="${{COMMIT_Y}}" r="6" fill="#58a6ff"
                  stroke="#0d1117" stroke-width="2" />
              </g>`;
          }});
          if (vcs.length > 1) {{
            const x0 = tsToX(tMin, tMin, tMax, svgW);
            const x1 = tsToX(tMax, tMin, tMax, svgW);
            paths = `<line x1="${{x0}}" y1="${{COMMIT_Y}}" x2="${{x1}}" y2="${{COMMIT_Y}}"
              stroke="#30363d" stroke-width="1.5" />` + paths;
          }}
        }}

        // ── Section markers ──────────────────────────────────────────────────
        if (layers.sections && tlData.sections) {{
          const visSec = tlData.sections.filter(s => visibleIds.has(s.commitId));
          visSec.forEach(s => {{
            const x = tsToX(new Date(s.timestamp).getTime(), tMin, tMax, svgW);
            const label = escHtml(s.sectionName);
            const action = s.action === 'removed' ? '−' : '+';
            const colour = s.action === 'removed' ? '#f78166' : '#3fb950';
            markers += `
              <g onmouseenter="showTip(event, '${{action}} ${{label}} section')" onmouseleave="hideTip()">
                <rect x="${{x - 5}}" y="${{MARKER_Y - 10}}" width="10" height="10"
                  fill="${{colour}}" rx="2" opacity="0.9"/>
                <text x="${{x}}" y="${{MARKER_Y + 16}}" text-anchor="middle"
                  font-size="9" fill="${{colour}}">${{label}}</text>
              </g>`;
          }});
        }}

        // ── Track markers ────────────────────────────────────────────────────
        if (layers.tracks && tlData.tracks) {{
          const visTrk = tlData.tracks.filter(t => visibleIds.has(t.commitId));
          visTrk.forEach((t, i) => {{
            const x = tsToX(new Date(t.timestamp).getTime(), tMin, tMax, svgW);
            const label = escHtml(t.trackName);
            const action = t.action === 'removed' ? '−' : '+';
            const colour = t.action === 'removed' ? '#e3b341' : '#a371f7';
            const yOff = MARKER_Y + 32 + (i % 2) * 14;
            markers += `
              <g onmouseenter="showTip(event, '${{action}} ${{label}} track')" onmouseleave="hideTip()">
                <circle cx="${{x}}" cy="${{yOff}}" r="4" fill="${{colour}}" opacity="0.85"/>
                <text x="${{x + 6}}" y="${{yOff + 4}}" font-size="9" fill="${{colour}}">${{label}}</text>
              </g>`;
          }});
        }}

        container.innerHTML = `
          <svg id="timeline-svg" width="${{svgW}}" height="${{SVG_H}}"
               xmlns="http://www.w3.org/2000/svg">
            <rect width="${{svgW}}" height="${{SVG_H}}" fill="#0d1117"/>
            ${{paths}}
            ${{markers}}
            ${{axisLabels}}
          </svg>`;

        const thumb = document.getElementById('scrubber-thumb');
        if (thumb) thumb.style.left = (scrubPct * 100) + '%';
      }}

      // ── Tooltip ──────────────────────────────────────────────────────────────
      const tip = document.getElementById('tooltip');
      function showTip(evt, html) {{
        tip.innerHTML = html;
        tip.style.display = 'block';
        tip.style.left = (evt.clientX + 12) + 'px';
        tip.style.top  = (evt.clientY - 8) + 'px';
      }}
      function hideTip() {{ tip.style.display = 'none'; }}

      // ── Audio modal ──────────────────────────────────────────────────────────
      function openAudioModal(commitId, sha) {{
        const existing = document.getElementById('audio-modal');
        if (existing) existing.remove();
        const modal = document.createElement('div');
        modal.id = 'audio-modal';
        modal.className = 'audio-modal';
        modal.innerHTML = `
          <div class="audio-modal-box">
            <h3>&#9654; Preview at commit ${{sha}}</h3>
            <p style="font-size:12px;color:#8b949e;margin-bottom:12px">
              Audio artifacts from this commit state.
              Audio playback requires MP3 artifacts to be stored for this repo.
            </p>
            <audio controls>
              <source src="/api/v1/musehub/repos/${{repoId}}/commits/${{commitId}}/audio" type="audio/mpeg">
              No audio available for this commit.
            </audio>
            <div style="text-align:right;margin-top:8px">
              <a href="${{base}}/commits/${{commitId}}" class="btn btn-secondary" style="font-size:12px">
                View commit
              </a>
              &nbsp;
              <button class="btn btn-secondary" onclick="document.getElementById('audio-modal').remove()"
                style="font-size:12px">Close</button>
            </div>
          </div>`;
        modal.addEventListener('click', e => {{ if (e.target === modal) modal.remove(); }});
        document.body.appendChild(modal);
      }}

      // ── Scrubber ─────────────────────────────────────────────────────────────
      function initScrubber() {{
        const bar = document.getElementById('scrubber-bar');
        if (!bar) return;
        let dragging = false;
        function updateFromEvent(e) {{
          const rect = bar.getBoundingClientRect();
          const pct  = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
          scrubPct = pct;
          const thumb = document.getElementById('scrubber-thumb');
          if (thumb) thumb.style.left = (pct * 100) + '%';
        }}
        bar.addEventListener('mousedown', e => {{ dragging = true; updateFromEvent(e); }});
        document.addEventListener('mousemove', e => {{ if (dragging) updateFromEvent(e); }});
        document.addEventListener('mouseup', () => {{ dragging = false; }});
      }}

      // ── Layer toggles ─────────────────────────────────────────────────────────
      function toggleLayer(name, checked) {{
        layers[name] = checked;
        renderTimeline();
      }}

      // ── Zoom ─────────────────────────────────────────────────────────────────
      function setZoom(z) {{
        zoom = z;
        document.querySelectorAll('.zoom-btn').forEach(b => {{
          b.style.background = b.dataset.zoom === z ? '#1f6feb' : '#21262d';
        }});
        renderTimeline();
      }}

      // ── Load ─────────────────────────────────────────────────────────────────
      async function load() {{
        try {{
          const data = await apiFetch('/repos/' + repoId + '/timeline?limit=200');
          tlData = data;

          const total = data.totalCommits || 0;
          document.getElementById('content').innerHTML = `
            <div style="margin-bottom:12px;display:flex;align-items:center;gap:12px">
              <a href="${{base}}">&larr; Back to repo</a>
              <span style="color:#8b949e;font-size:13px">${{total}} commit${{total !== 1 ? 's' : ''}}</span>
            </div>

            <div class="timeline-toolbar">
              <label class="layer-toggle">
                <input type="checkbox" checked onchange="toggleLayer('commits', this.checked)"> Commits
              </label>
              <label class="layer-toggle">
                <input type="checkbox" checked onchange="toggleLayer('emotion', this.checked)"> Emotion
              </label>
              <label class="layer-toggle">
                <input type="checkbox" checked onchange="toggleLayer('sections', this.checked)"> Sections
              </label>
              <label class="layer-toggle">
                <input type="checkbox" checked onchange="toggleLayer('tracks', this.checked)"> Tracks
              </label>
              <div class="zoom-select">
                <span style="font-size:12px;color:#8b949e">Zoom:</span>
                <button class="btn zoom-btn" data-zoom="day" onclick="setZoom('day')"
                  style="font-size:11px;padding:3px 10px;background:#21262d">Day</button>
                <button class="btn zoom-btn" data-zoom="week" onclick="setZoom('week')"
                  style="font-size:11px;padding:3px 10px;background:#21262d">Week</button>
                <button class="btn zoom-btn" data-zoom="month" onclick="setZoom('month')"
                  style="font-size:11px;padding:3px 10px;background:#21262d">Month</button>
                <button class="btn zoom-btn" data-zoom="all" onclick="setZoom('all')"
                  style="font-size:11px;padding:3px 10px;background:#1f6feb">All</button>
              </div>
            </div>

            <div id="timeline-svg-container"></div>

            <div class="scrubber-bar" id="scrubber-bar">
              <div class="scrubber-thumb" id="scrubber-thumb" style="left:100%"></div>
            </div>

            <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:12px;font-size:12px;color:#8b949e">
              <span>&#9679; <span style="color:#58a6ff">blue</span> = commit marker (click to preview audio)</span>
              <span>&#9632; <span style="color:#3fb950">green</span> = section added</span>
              <span>&#9632; <span style="color:#f78166">red</span> = section removed</span>
              <span>&#9679; <span style="color:#a371f7">purple</span> = track added</span>
              <span>&#9679; <span style="color:#e3b341">yellow</span> = track removed</span>
            </div>`;

          initScrubber();
          renderTimeline();
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('content').innerHTML = '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      load();
    """
    css_with_timeline = _CSS + _TIMELINE_CSS
    title = f"Timeline — {repo_id[:8]}"
    breadcrumb = f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / timeline'
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — Muse Hub</title>
  <style>{css_with_timeline}</style>
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
  <div class="tooltip" id="tooltip"></div>
  <script>
    {_TOKEN_SCRIPT}
    window.addEventListener('DOMContentLoaded', function() {{
      if (!getToken()) {{ showTokenForm(); return; }}
      {script}
    }});
  </script>
</body>
</html>"""
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


@router.get(
    "/{repo_id}/releases",
    response_class=HTMLResponse,
    summary="Muse Hub release list page",
)
async def release_list_page(repo_id: str) -> HTMLResponse:
    """Render the release list page: all published versions newest first.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/releases``.
    Each release shows its tag, title, creation date, and a link to the
    detail page where release notes and download packages are available.
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const base   = '/musehub/ui/' + repoId;

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      async function load() {{
        try {{
          const data     = await apiFetch('/repos/' + repoId + '/releases');
          const releases = data.releases || [];

          const rows = releases.length === 0
            ? '<p class="loading">No releases published yet.</p>'
            : releases.map(r => `
              <div class="release-row">
                <span class="badge badge-release">${{escHtml(r.tag)}}</span>
                <div style="flex:1">
                  <a href="${{base}}/releases/${{encodeURIComponent(r.tag)}}">${{escHtml(r.title)}}</a>
                  <div style="font-size:12px;color:#8b949e;margin-top:2px">
                    Released ${{fmtDate(r.createdAt)}}
                    ${{r.commitId ? ' &bull; commit <span style="font-family:monospace">' + r.commitId.substring(0,8) + '</span>' : ''}}
                  </div>
                </div>
              </div>`).join('');

          document.getElementById('content').innerHTML = `
            <div style="margin-bottom:12px">
              <a href="${{base}}">&larr; Back to repo</a>
            </div>
            <div class="card">
              <h1 style="margin-bottom:16px">Releases</h1>
              ${{rows}}
            </div>`;
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('content').innerHTML = '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      load();
    """
    html = _page(
        title="Releases",
        breadcrumb=f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / releases',
        body_script=script,
    )
    return HTMLResponse(content=html)


@router.get(
    "/{repo_id}/releases/{tag}",
    response_class=HTMLResponse,
    summary="Muse Hub release detail page",
)
async def release_detail_page(repo_id: str, tag: str) -> HTMLResponse:
    """Render the release detail page: title, tag, release notes, download packages.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/releases/{tag}``.
    Download packages (MIDI bundle, stems, MP3, MusicXML, metadata) are
    rendered as download cards; unavailable packages show a "not available"
    indicator instead of a broken link.
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const tag    = {repr(tag)};
      const base   = '/musehub/ui/' + repoId;

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      function downloadCard(label, desc, url) {{
        if (url) {{
          return `
            <div class="download-card">
              <span class="pkg-name">${{label}}</span>
              <span class="pkg-desc">${{desc}}</span>
              <a class="btn btn-secondary" href="${{escHtml(url)}}" download>&#11015; Download</a>
            </div>`;
        }}
        return `
          <div class="download-card" style="opacity:0.5">
            <span class="pkg-name">${{label}}</span>
            <span class="pkg-desc">${{desc}}</span>
            <span style="font-size:12px;color:#8b949e">Not available</span>
          </div>`;
      }}

      async function load() {{
        try {{
          const r = await apiFetch('/repos/' + repoId + '/releases/' + encodeURIComponent(tag));
          const dl = r.downloadUrls || {{}};

          const downloads = `
            <div class="download-grid">
              ${{downloadCard('Full MIDI', 'All tracks as a single .mid file', dl.midiBubdle || dl.midiBuddle || dl.midiBundle)}}
              ${{downloadCard('MIDI Stems', 'Individual track stems (zip of .mid files)', dl.stems)}}
              ${{downloadCard('MP3 Mix', 'Full mix audio render', dl.mp3)}}
              ${{downloadCard('MusicXML', 'Notation export for sheet music editors', dl.musicxml)}}
              ${{downloadCard('Metadata', 'JSON manifest: tempo, key, arrangement', dl.metadata)}}
            </div>`;

          document.getElementById('content').innerHTML = `
            <div style="margin-bottom:12px">
              <a href="${{base}}/releases">&larr; Back to releases</a>
            </div>
            <div class="card">
              <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
                <h1 style="margin:0">${{escHtml(r.title)}}</h1>
                <span class="badge badge-release">${{escHtml(r.tag)}}</span>
              </div>
              <div class="meta-row">
                <div class="meta-item">
                  <span class="meta-label">Released</span>
                  <span class="meta-value">${{fmtDate(r.createdAt)}}</span>
                </div>
                ${{r.commitId ? `
                <div class="meta-item">
                  <span class="meta-label">Commit</span>
                  <span class="meta-value">
                    <a href="${{base}}/commits/${{r.commitId}}" style="font-family:monospace">
                      ${{r.commitId.substring(0,8)}}
                    </a>
                  </span>
                </div>` : ''}}
              </div>
              ${{r.body ? '<h2 style="margin-top:16px;margin-bottom:8px">Release Notes</h2><pre>' + escHtml(r.body) + '</pre>' : ''}}
              <h2 style="margin-top:16px;margin-bottom:8px">Download Packages</h2>
              ${{downloads}}
            </div>`;
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('content').innerHTML = '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      load();
    """
    safe_tag = tag[:20] if len(tag) > 20 else tag
    html = _page(
        title=f"Release {safe_tag}",
        breadcrumb=(
            f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / '
            f'<a href="/musehub/ui/{repo_id}/releases">releases</a> / {safe_tag}'
        ),
        body_script=script,
    )
    return HTMLResponse(content=html)


@router.get(
    "/{repo_id}/sessions",
    response_class=HTMLResponse,
    summary="Muse Hub session list page",
)
async def session_list_page(repo_id: str) -> HTMLResponse:
    """Render the session list page: recording sessions newest first.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/sessions`` and displays
    each session's start time, duration, participants, and intent.  Links to
    the session detail page for full metadata.
    """
    script = f"""
      const repoId = {repr(repo_id)};
      const base   = '/musehub/ui/' + repoId;

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      function fmtDuration(startIso, endIso) {{
        if (!startIso || !endIso) return '—';
        const ms = new Date(endIso) - new Date(startIso);
        if (ms < 0) return '—';
        const h = Math.floor(ms / 3600000);
        const m = Math.floor((ms % 3600000) / 60000);
        return h > 0 ? h + 'h ' + m + 'm' : m + 'm';
      }}

      async function load() {{
        try {{
          const data = await apiFetch('/repos/' + repoId + '/sessions?limit=50');
          const sessions = data.sessions || [];

          const rows = sessions.length === 0
            ? '<p class="loading">No sessions pushed to this repo yet.</p>'
            : sessions.map(s => `
              <div class="commit-row">
                <a class="commit-sha" href="${{base}}/sessions/${{s.sessionId}}">${{escHtml(s.sessionId.substring(0,8))}}</a>
                <div class="commit-msg" style="flex:1">
                  <a href="${{base}}/sessions/${{s.sessionId}}">
                    ${{escHtml(s.intent) || '<em style="color:#8b949e">no intent recorded</em>'}}
                  </a>
                  <div style="font-size:12px;color:#8b949e;margin-top:2px">
                    ${{(s.participants||[]).map(p => '<span class="label">' + escHtml(p) + '</span>').join('')}}
                    ${{s.location ? '&nbsp;&bull;&nbsp;' + escHtml(s.location) : ''}}
                  </div>
                </div>
                <div class="commit-meta">
                  <div>${{fmtDate(s.startedAt)}}</div>
                  <div style="color:#8b949e;font-size:11px">${{fmtDuration(s.startedAt, s.endedAt)}}</div>
                </div>
              </div>`).join('');

          document.getElementById('content').innerHTML = `
            <div style="margin-bottom:12px">
              <a href="${{base}}">&larr; Back to repo</a>
            </div>
            <div class="card">
              <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
                <h1 style="margin:0">Recording Sessions</h1>
                <span style="color:#8b949e;font-size:13px">${{data.total}} total</span>
              </div>
              ${{rows}}
            </div>`;
        }} catch(e) {{
          if (e.message !== 'auth')
            document.getElementById('content').innerHTML = '<p class="error">&#10005; ' + escHtml(e.message) + '</p>';
        }}
      }}

      load();
    """
    html = _page(
        title="Sessions",
        breadcrumb=f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / sessions',
        body_script=script,
    )
    return HTMLResponse(content=html)


@router.get(
    "/{repo_id}/sessions/{session_id}",
    response_class=HTMLResponse,
    summary="Muse Hub session detail page",
)
async def session_detail_page(repo_id: str, session_id: str) -> HTMLResponse:
    """Render the full session detail page.

    Fetches ``GET /api/v1/musehub/repos/{repo_id}/sessions/{session_id}`` and
    displays all session metadata: start/end times, duration, location, intent,
    participants with session-count badges, commits made during the session
    (linked to commit detail pages), and closing notes rendered verbatim.

    Renders a 404 message if the API returns 404, so agents can distinguish
    a missing session from a server error.
    """
    script = f"""
      const repoId    = {repr(repo_id)};
      const sessionId = {repr(session_id)};
      const base      = '/musehub/ui/' + repoId;

      function escHtml(s) {{
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      function fmtDuration(startIso, endIso) {{
        if (!startIso || !endIso) return '—';
        const ms = new Date(endIso) - new Date(startIso);
        if (ms < 0) return '—';
        const h = Math.floor(ms / 3600000);
        const m = Math.floor((ms % 3600000) / 60000);
        return h > 0 ? h + 'h ' + m + 'm' : m + 'm';
      }}

      async function loadSessionCounts() {{
        try {{
          const data = await apiFetch('/repos/' + repoId + '/sessions?limit=200');
          const sessions = data.sessions || [];
          const counts = {{}};
          sessions.forEach(s => {{
            (s.participants || []).forEach(p => {{
              counts[p] = (counts[p] || 0) + 1;
            }});
          }});
          return counts;
        }} catch(e) {{
          return {{}};
        }}
      }}

      async function load() {{
        try {{
          const [session, counts] = await Promise.all([
            apiFetch('/repos/' + repoId + '/sessions/' + sessionId),
            loadSessionCounts(),
          ]);

          const duration = fmtDuration(session.startedAt, session.endedAt);

          const participantHtml = (session.participants || []).length === 0
            ? '<p style="color:#8b949e;font-size:14px">No participants recorded.</p>'
            : (session.participants || []).map(p => `
                <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid #21262d">
                  <span style="flex:1;font-size:14px">${{escHtml(p)}}</span>
                  <span class="badge" style="background:#1f6feb;color:#e6edf3">
                    ${{counts[p] || 1}} session${{(counts[p] || 1) !== 1 ? 's' : ''}}
                  </span>
                </div>`).join('');

          const commitHtml = (session.commits || []).length === 0
            ? '<p style="color:#8b949e;font-size:14px">No commits associated with this session.</p>'
            : (session.commits || []).map(c => `
                <div class="commit-row">
                  <a class="commit-sha" href="${{base}}/commits/${{c}}">${{c.substring(0,8)}}</a>
                  <span class="commit-msg">
                    <a href="${{base}}/commits/${{c}}">${{c}}</a>
                  </span>
                </div>`).join('');

          const notesHtml = session.notes
            ? '<pre>' + escHtml(session.notes) + '</pre>'
            : '<p style="color:#8b949e;font-size:14px">No closing notes.</p>';

          const allSessions = await apiFetch('/repos/' + repoId + '/sessions?limit=200')
            .then(d => d.sessions || []).catch(() => []);
          const idx = allSessions.findIndex(s => s.sessionId === sessionId);
          const prevSession = idx >= 0 && idx + 1 < allSessions.length ? allSessions[idx + 1] : null;
          const nextSession = idx > 0 ? allSessions[idx - 1] : null;

          const navHtml = `
            <div style="display:flex;justify-content:space-between;margin-top:12px;font-size:13px">
              <span>
                ${{prevSession
                  ? '<a href="' + base + '/sessions/' + prevSession.sessionId + '">&larr; Previous session</a>'
                  : '<span style="color:#8b949e">No previous session</span>'}}
              </span>
              <span>
                ${{nextSession
                  ? '<a href="' + base + '/sessions/' + nextSession.sessionId + '">Next session &rarr;</a>'
                  : '<span style="color:#8b949e">No next session</span>'}}
              </span>
            </div>`;

          document.getElementById('content').innerHTML = `
            <div style="margin-bottom:12px">
              <a href="${{base}}/sessions">&larr; Back to sessions</a>
            </div>
            <div class="card">
              <h1>Recording Session</h1>
              <div class="meta-row">
                <div class="meta-item">
                  <span class="meta-label">Started</span>
                  <span class="meta-value">${{fmtDate(session.startedAt)}}</span>
                </div>
                <div class="meta-item">
                  <span class="meta-label">Ended</span>
                  <span class="meta-value">${{session.endedAt ? fmtDate(session.endedAt) : '—'}}</span>
                </div>
                <div class="meta-item">
                  <span class="meta-label">Duration</span>
                  <span class="meta-value">${{duration}}</span>
                </div>
                ${{session.location ? `
                <div class="meta-item">
                  <span class="meta-label">Location</span>
                  <span class="meta-value">${{escHtml(session.location)}}</span>
                </div>` : ''}}
                <div class="meta-item">
                  <span class="meta-label">Session ID</span>
                  <span class="meta-value" style="font-family:monospace;font-size:12px">${{escHtml(session.sessionId)}}</span>
                </div>
              </div>
              ${{session.intent ? `
              <div style="margin-top:8px">
                <span class="meta-label">Intent</span>
                <p style="margin-top:4px;font-size:14px;color:#c9d1d9">${{escHtml(session.intent)}}</p>
              </div>` : ''}}
              ${{navHtml}}
            </div>
            <div class="card">
              <h2>Participants (${{(session.participants||[]).length}})</h2>
              <div style="margin-top:8px">${{participantHtml}}</div>
            </div>
            <div class="card">
              <h2>Commits (${{(session.commits||[]).length}})</h2>
              <div style="margin-top:8px">${{commitHtml}}</div>
            </div>
            <div class="card">
              <h2>Closing Notes</h2>
              <div style="margin-top:8px">${{notesHtml}}</div>
            </div>`;
        }} catch(e) {{
          if (e.message !== 'auth') {{
            const msg = e.message.startsWith('404') ? 'Session not found.' : escHtml(e.message);
            document.getElementById('content').innerHTML =
              '<div style="margin-bottom:12px"><a href="' + base + '/sessions">&larr; Back to sessions</a></div>' +
              '<p class="error">&#10005; ' + msg + '</p>';
          }}
        }}
      }}

      load();
    """
    html = _page(
        title=f"Session {session_id[:8]}",
        breadcrumb=(
            f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / '
            f'<a href="/musehub/ui/{repo_id}/sessions">sessions</a> / {session_id[:8]}'
        ),
        body_script=script,
    )
    return HTMLResponse(content=html)
