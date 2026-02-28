"""Muse Hub web UI route handlers.

Serves browser-readable HTML pages for navigating a Muse Hub repo —
analogous to GitHub's repository browser but for music projects.

Endpoint summary:
  GET /musehub/ui/{repo_id}                        — repo page (branch selector + commit log)
  GET /musehub/ui/{repo_id}/commits/{commit_id}    — commit detail page (metadata + artifacts)
  GET /musehub/ui/{repo_id}/pulls                  — pull request list page
  GET /musehub/ui/{repo_id}/pulls/{pr_id}          — PR detail page (with merge button)
  GET /musehub/ui/{repo_id}/issues                 — issue list page
  GET /musehub/ui/{repo_id}/issues/{number}        — issue detail page (with close button)
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
# Route handlers
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
