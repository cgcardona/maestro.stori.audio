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
