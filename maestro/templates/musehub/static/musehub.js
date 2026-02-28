/**
 * musehub.js — shared utilities for all MuseHub web pages.
 *
 * Sections:
 *  1. Auth helpers (token storage, apiFetch, token form)
 *  2. Formatting helpers (dates, SHA, durations)
 *  3. Repo nav (header card population, tab count badges, star toggle)
 *  4. Audio player (persistent bottom bar, playback controls)
 *  5. Commit message parser (liner-notes display helpers)
 */

/* ═══════════════════════════════════════════════════════════════
 * 1. Auth helpers
 * ═══════════════════════════════════════════════════════════════ */

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
  const tf = document.getElementById('token-form');
  const content = document.getElementById('content');
  if (tf) tf.style.display = 'block';
  if (content) content.innerHTML = '';
  if (msg) {
    const msgEl = document.getElementById('token-msg');
    if (msgEl) msgEl.textContent = msg;
  }
}

function saveToken() {
  const t = document.getElementById('token-input').value.trim();
  if (t) { setToken(t); location.reload(); }
}

/* ═══════════════════════════════════════════════════════════════
 * 2. Formatting helpers
 * ═══════════════════════════════════════════════════════════════ */

function fmtDate(iso) {
  if (!iso) return '--';
  const d = new Date(iso);
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function fmtRelative(iso) {
  if (!iso) return '--';
  const diff = (Date.now() - new Date(iso)) / 1000;
  if (diff < 60)   return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  if (diff < 604800) return Math.floor(diff / 86400) + 'd ago';
  return fmtDate(iso);
}

function shortSha(sha) { return sha ? sha.substring(0, 8) : '--'; }

function fmtDuration(seconds) {
  if (!seconds || isNaN(seconds)) return '--';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function fmtSeconds(t) {
  if (isNaN(t)) return '0:00';
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function escHtml(s) {
  if (!s) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* ═══════════════════════════════════════════════════════════════
 * 3. Repo nav — header card + tab count badges
 * ═══════════════════════════════════════════════════════════════
 *
 * Call initRepoNav(repoId) from each repo page's DOMContentLoaded handler.
 * It fetches the repo metadata to populate the header card, and fetches
 * open PR / issue counts to populate the tab badges.
 *
 * The star button requires authentication; it is only shown when getToken()
 * is truthy. ═══════════════════════════════════════════════════════════════ */

async function initRepoNav(repoId) {
  try {
    // Repo metadata for header card
    const repo = await fetch(API + '/repos/' + repoId, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null).catch(() => null);

    if (repo) {
      const badge = document.getElementById('nav-visibility-badge');
      if (badge) {
        badge.textContent = repo.visibility;
        badge.className = 'badge repo-visibility-badge badge-' + (repo.visibility === 'public' ? 'clean' : 'neutral');
      }

      const keyEl = document.getElementById('nav-key');
      if (keyEl && repo.keySignature) {
        keyEl.textContent = '♩ ' + repo.keySignature;
        keyEl.style.display = '';
      }

      const bpmEl = document.getElementById('nav-bpm');
      if (bpmEl && repo.tempoBpm) {
        bpmEl.textContent = repo.tempoBpm + ' BPM';
        bpmEl.style.display = '';
      }

      const tagsEl = document.getElementById('nav-tags');
      if (tagsEl && repo.tags && repo.tags.length > 0) {
        tagsEl.innerHTML = repo.tags.map(t =>
          '<span class="nav-meta-tag">' + escHtml(t) + '</span>'
        ).join('');
      }
    }

    // Show star button if authed; load star state
    if (getToken()) {
      const starBtn = document.getElementById('nav-star-btn');
      if (starBtn) starBtn.style.display = '';
    }

    // Open PR and issue counts (non-fatal)
    Promise.all([
      fetch(API + '/repos/' + repoId + '/pull-requests?state=open', { headers: authHeaders() })
        .then(r => r.ok ? r.json() : { pull_requests: [] }).catch(() => ({ pull_requests: [] })),
      fetch(API + '/repos/' + repoId + '/issues?state=open', { headers: authHeaders() })
        .then(r => r.ok ? r.json() : { issues: [] }).catch(() => ({ issues: [] })),
    ]).then(([prData, issueData]) => {
      const prCount = (prData.pull_requests || []).length;
      const issueCount = (issueData.issues || []).length;

      const prBadge = document.getElementById('nav-pr-count');
      if (prBadge && prCount > 0) { prBadge.textContent = prCount; prBadge.style.display = ''; }

      const issueBadge = document.getElementById('nav-issue-count');
      if (issueBadge && issueCount > 0) { issueBadge.textContent = issueCount; issueBadge.style.display = ''; }
    });
  } catch (e) {
    // Nav enrichment is non-critical — page still works without it
  }
}

async function toggleStar() {
  // Placeholder — star endpoint wired in Phase 3/5
  const btn = document.getElementById('nav-star-btn');
  if (btn) {
    const icon = document.getElementById('nav-star-icon');
    if (icon) icon.textContent = icon.textContent === '☆' ? '★' : '☆';
  }
}

/* ═══════════════════════════════════════════════════════════════
 * 4. Audio player
 * ═══════════════════════════════════════════════════════════════
 *
 * queueAudio(url, title, repoName) — start playing an artifact.
 * togglePlay()                     — play / pause.
 * seekAudio(pct)                   — seek to percentage position.
 * closePlayer()                    — hide and stop.
 * ═══════════════════════════════════════════════════════════════ */

const _player = { playing: false };

function _audioEl() { return document.getElementById('player-audio'); }
function _playerBar() { return document.getElementById('audio-player'); }

async function _fetchBlobUrl(url) {
  const res = await fetch(url, { headers: { 'Authorization': 'Bearer ' + getToken() } });
  if (!res.ok) throw new Error(res.status);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

async function queueAudio(url, title, repoName) {
  const bar = _playerBar();
  const audio = _audioEl();
  if (!bar || !audio) return;

  bar.style.display = 'flex';
  document.body.classList.add('player-open');
  const t = document.getElementById('player-title');
  const r = document.getElementById('player-repo');
  if (t) t.textContent = title || 'Now Playing';
  if (r) r.textContent = repoName || '';

  try {
    const blobUrl = await _fetchBlobUrl(url);
    if (audio._blobUrl) URL.revokeObjectURL(audio._blobUrl);
    audio._blobUrl = blobUrl;
    audio.src = blobUrl;
  } catch (_) {
    audio.src = url;
  }
  audio.load();
  audio.play().catch(() => {});
  _player.playing = true;

  _updatePlayBtn();
}

function togglePlay() {
  const audio = _audioEl();
  if (!audio || !audio.src) return;
  if (_player.playing) { audio.pause(); _player.playing = false; }
  else { audio.play().catch(() => {}); _player.playing = true; }
  _updatePlayBtn();
}

function seekAudio(value) {
  const audio = _audioEl();
  if (!audio || !audio.duration) return;
  audio.currentTime = (value / 100) * audio.duration;
}

function closePlayer() {
  const bar = _playerBar();
  const audio = _audioEl();
  if (bar) bar.style.display = 'none';
  document.body.classList.remove('player-open');
  if (audio) {
    audio.pause();
    if (audio._blobUrl) { URL.revokeObjectURL(audio._blobUrl); audio._blobUrl = null; }
    audio.src = '';
  }
  _player.playing = false;
  _updatePlayBtn();
}

async function downloadArtifact(url, filename) {
  const res = await fetch(url, { headers: { 'Authorization': 'Bearer ' + getToken() } });
  if (!res.ok) return;
  const blob = await res.blob();
  const blobUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = blobUrl;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(blobUrl);
}

function onTimeUpdate() {
  const audio = _audioEl();
  if (!audio || !audio.duration) return;
  const pct = (audio.currentTime / audio.duration) * 100;
  const seek = document.getElementById('player-seek');
  const cur  = document.getElementById('player-current');
  if (seek) seek.value = pct;
  if (cur) cur.textContent = fmtSeconds(audio.currentTime);
}

function onMetadata() {
  const audio = _audioEl();
  const dur = document.getElementById('player-duration');
  if (audio && dur) dur.textContent = fmtSeconds(audio.duration);
}

function onAudioEnded() {
  _player.playing = false;
  _updatePlayBtn();
  const seek = document.getElementById('player-seek');
  if (seek) seek.value = 0;
  const cur = document.getElementById('player-current');
  if (cur) cur.textContent = '0:00';
}

function _updatePlayBtn() {
  const btn = document.getElementById('player-toggle');
  if (btn) btn.innerHTML = _player.playing ? '&#9646;&#9646;' : '&#9654;';
}

/* ═══════════════════════════════════════════════════════════════
 * 5. Commit message parser (liner-notes display)
 * ═══════════════════════════════════════════════════════════════
 *
 * Parses conventional commit format: type(scope): subject
 * Returns { type, scope, subject, badges }
 * ═══════════════════════════════════════════════════════════════ */

const _COMMIT_TYPES = {
  feat:     { label: 'feat',     color: 'var(--color-success)' },
  fix:      { label: 'fix',      color: 'var(--color-danger)' },
  refactor: { label: 'refactor', color: 'var(--color-accent)' },
  style:    { label: 'style',    color: 'var(--color-purple)' },
  docs:     { label: 'docs',     color: 'var(--text-muted)' },
  chore:    { label: 'chore',    color: 'var(--color-neutral)' },
  init:     { label: 'init',     color: 'var(--color-warning)' },
  perf:     { label: 'perf',     color: 'var(--color-orange)' },
};

function parseCommitMessage(msg) {
  if (!msg) return { type: null, scope: null, subject: msg || '' };
  // "type(scope): subject" or "type: subject"
  const m = msg.match(/^(\w+)(?:\(([^)]+)\))?:\s*(.*)/s);
  if (!m) return { type: null, scope: null, subject: msg };
  return { type: m[1].toLowerCase(), scope: m[2] || null, subject: m[3] };
}

function commitTypeBadge(type) {
  if (!type) return '';
  const t = _COMMIT_TYPES[type] || { label: type, color: 'var(--text-muted)' };
  return `<span class="badge" style="background:${t.color}20;color:${t.color};border:1px solid ${t.color}40">${escHtml(t.label)}</span>`;
}

function commitScopeBadge(scope) {
  if (!scope) return '';
  return `<span class="badge" style="background:var(--bg-overlay);color:var(--color-purple);border:1px solid var(--color-purple-bg)">${escHtml(scope)}</span>`;
}

// ── Reaction bar ─────────────────────────────────────────────────────────────

/**
 * Ordered emoji set shown in the reaction bar on every detail page.
 * Backed by POST /repos/{repo_id}/reactions which validates against _ALLOWED_EMOJIS.
 */
const REACTION_BAR_EMOJIS = ['🔥', '❤️', '👏', '✨', '🎵', '🎸', '🎹', '🥁'];

/**
 * Load reaction counts for a target and render an interactive bar into containerId.
 *
 * Calls GET /repos/{repoId}/reactions?target_type=&target_id= (repoId is the
 * page-level global set in each template's {% block page_data %}).
 * Clicking a button calls toggleReaction() which POSTs and re-renders.
 *
 * @param {string} targetType - "commit" | "pull_request" | "issue" | "release" | "session"
 * @param {string} targetId   - The entity's primary ID string
 * @param {string} containerId - DOM element id to render into
 */
async function loadReactions(targetType, targetId, containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;

  let reactions = [];
  try {
    reactions = await apiFetch(
      '/repos/' + repoId + '/reactions?target_type=' + encodeURIComponent(targetType) +
      '&target_id=' + encodeURIComponent(targetId)
    );
  } catch (_) {
    reactions = [];
  }

  const countMap = {};
  const reactedMap = {};
  (Array.isArray(reactions) ? reactions : []).forEach(function(r) {
    countMap[r.emoji] = r.count;
    reactedMap[r.emoji] = r.reacted_by_me;
  });

  const safeTT = targetType.replace(/'/g, '');
  const safeTI = String(targetId).replace(/'/g, '');
  const safeCID = containerId.replace(/'/g, '');

  container.innerHTML = '<div class="reaction-bar">' +
    REACTION_BAR_EMOJIS.map(function(emoji) {
      const count = countMap[emoji] || 0;
      const active = reactedMap[emoji] ? ' reaction-btn--active' : '';
      const countHtml = count > 0 ? '<span class="reaction-count">' + count + '</span>' : '';
      return '<button class="reaction-btn' + active + '" ' +
        'onclick="toggleReaction(\'' + safeTT + '\',\'' + safeTI + '\',\'' + emoji + '\',\'' + safeCID + '\')" ' +
        'title="' + emoji + '">' +
        emoji + countHtml +
        '</button>';
    }).join('') +
  '</div>';
}

/**
 * Toggle a single emoji reaction on a target. Re-renders the bar when done.
 * Requires a stored JWT — shows the token form if unauthenticated.
 */
async function toggleReaction(targetType, targetId, emoji, containerId) {
  if (!getToken()) {
    showTokenForm('Sign in to react');
    return;
  }
  try {
    await apiFetch('/repos/' + repoId + '/reactions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_type: targetType, target_id: String(targetId), emoji: emoji }),
    });
    await loadReactions(targetType, targetId, containerId);
  } catch (_) { /* silent: reaction toggle is non-critical */ }
}

/**
 * Parse "section:X track:Y" key-value pairs from a commit message.
 * Returns { section, track, ...rest }
 */
function parseCommitMeta(message) {
  const meta = {};
  const patterns = [
    /section:([\w-]+)/i,
    /track:([\w-]+)/i,
    /key:([\w#b]+\s*(?:major|minor|maj|min)?)/i,
    /tempo:(\d+)/i,
    /bpm:(\d+)/i,
  ];
  const keys = ['section', 'track', 'key', 'tempo', 'bpm'];
  patterns.forEach((re, i) => {
    const m = message.match(re);
    if (m) meta[keys[i]] = m[1];
  });
  return meta;
}
