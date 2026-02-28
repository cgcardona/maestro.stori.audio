"""Muse Hub web UI route handlers.

Serves browser-readable HTML pages for navigating a Muse Hub repo —
analogous to GitHub's repository browser but for music projects.

Endpoint summary:
  GET /musehub/ui/{repo_id}                           — repo page (branch selector + commit log)
  GET /musehub/ui/{repo_id}/commits/{commit_id}       — commit detail page (metadata + artifacts)
  GET /musehub/ui/{repo_id}/pulls                     — pull request list page
  GET /musehub/ui/{repo_id}/pulls/{pr_id}             — PR detail page (with merge button)
  GET /musehub/ui/{repo_id}/issues                    — issue list page
  GET /musehub/ui/{repo_id}/issues/{number}           — issue detail page (with close button)
  GET /musehub/ui/{repo_id}/sessions                  — session list page
  GET /musehub/ui/{repo_id}/sessions/{session_id}     — session detail page
  GET /musehub/ui/{repo_id}/search                    — in-repo search page (four modes)

These routes require NO JWT auth — they return static HTML shells whose
embedded JavaScript fetches data from the authed JSON API
(``/api/v1/musehub/...``) using a token stored in ``localStorage``.

No Jinja2 is required; pages are self-contained HTML strings rendered
server-side.  No external CDN dependencies.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
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
