/**
 * musehub.js — shared auth utilities for all MuseHub web pages.
 *
 * Injected by base.html so every page has access to JWT storage helpers,
 * apiFetch, the token form, and common formatting functions without
 * repeating this logic in every route handler.
 */

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
    showTokenForm('Session expired or invalid token -- please re-enter your JWT.');
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

function fmtDate(iso) {
  if (!iso) return '--';
  const d = new Date(iso);
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function shortSha(sha) { return sha ? sha.substring(0, 8) : '--'; }
