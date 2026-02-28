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
  GET /musehub/ui/{repo_id}/timeline               — timeline page (chronological evolution)

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

        // Build a fast lookup from commit_id → visible set
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
          // Legend
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
          // Spine
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

        // Update scrubber thumb
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
    # Merge timeline CSS into the shared CSS before building the page
    css_with_timeline = _CSS + _TIMELINE_CSS
    # Build page HTML manually so we can inject the extra CSS
    title = f"Timeline — {repo_id[:8]}"
    breadcrumb = (
        f'<a href="/musehub/ui/{repo_id}">{repo_id[:8]}</a> / timeline'
    )
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
