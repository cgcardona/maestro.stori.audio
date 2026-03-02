/**
 * AgentCeption — Alpine.js component library
 *
 * All Alpine component factory functions live here so templates stay logic-free.
 * Templates call these by name:  x-data="componentName(serverRenderedArgs)"
 *
 * Jinja2 data is injected at the call-site in the template attribute, never
 * inside this file.  This keeps the file static, cacheable, and free of
 * server-side template rendering bugs caused by mismatched quote styles.
 *
 * Function index:
 *   projectSwitcher()                         — nav project dropdown
 *   pipelineDashboard(initial)                — overview SSE board
 *   phaseSwitcher(label, labels, pinned)      — phase dropdown
 *   pipelineControl(paused)                   — pause/resume toggle
 *   waveControl()                             — start-wave button
 *   scalingAdvisor(initial)                   — scaling recommendation banner
 *   prViolations(initial)                     — out-of-order PR banner
 *   staleClaimCard(claim)                     — stale-claim clear action
 *   trendChart(labels, issues, prs, agents)   — telemetry sparkline
 *   configPanel(initial)                      — pipeline config editor
 *   spawnForm()                               — manual spawn form
 *   exportPanel()                             — template export
 *   importPanel()                             — template import
 *   transcriptBrowser(q, role, status)        — transcript list filters/sort
 *   transcriptDetail()                        — in-conversation search
 */

'use strict';

// ---------------------------------------------------------------------------
// Navigation — project switcher
// ---------------------------------------------------------------------------

/**
 * Fetches the pipeline-config and renders a project <select> in the nav bar.
 * Hidden via x-show when no projects are configured.
 */
function projectSwitcher() {
  return {
    projects: [],
    activeProject: null,

    async load() {
      try {
        const res = await fetch('/api/config');
        if (!res.ok) return;
        const cfg = await res.json();
        this.projects = cfg.projects || [];
        this.activeProject = cfg.active_project || null;
      } catch (_) { /* network error — silently suppress */ }
    },

    async switchProject(name) {
      if (!name || name === this.activeProject) return;
      try {
        const res = await fetch('/api/config/switch-project', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project_name: name }),
        });
        if (res.ok) window.location.reload();
      } catch (_) { /* silently suppress */ }
    },
  };
}

// ---------------------------------------------------------------------------
// Overview — pipeline dashboard (SSE-driven)
// ---------------------------------------------------------------------------

/**
 * Receives a PipelineState snapshot from the server as the initial value,
 * then subscribes to GET /events (SSE) for live updates.
 * Never polls — fully event-driven once connected.
 *
 * @param {object} initial - Server-rendered PipelineState snapshot.
 */
function pipelineDashboard(initial) {
  return {
    state: initial,
    connected: false,
    boardLoading: false,
    _es: null,

    connect() {
      this._es = new EventSource('/events');

      this._es.onopen = () => { this.connected = true; };

      this._es.onmessage = (e) => {
        try {
          this.state = JSON.parse(e.data);
          this.connected = true;
        } catch (_) {
          // Malformed JSON — keep stale state.
        }
      };

      this._es.onerror = () => {
        this.connected = false;
        // EventSource auto-reconnects; just update the indicator.
      };
    },

    /** Format a UNIX timestamp as a human-readable relative time string. */
    relativeTime(ts) {
      if (!ts) return '—';
      const secs = Math.floor(Date.now() / 1000 - ts);
      if (secs < 5)    return 'just now';
      if (secs < 60)   return secs + 's ago';
      if (secs < 3600) return Math.floor(secs / 60) + 'm ago';
      return Math.floor(secs / 3600) + 'h ago';
    },
  };
}

// ---------------------------------------------------------------------------
// Overview — phase switcher dropdown
// ---------------------------------------------------------------------------

/**
 * Phase-label selector in the pipeline board header.
 * Dispatches 'phase-switching' so the board can show a loading spinner
 * before the fetch + page reload.
 *
 * @param {string|null} initialLabel  - Currently active label.
 * @param {string[]}    allLabels     - All configured phase labels.
 * @param {boolean}     initialPinned - Whether the label is manually pinned.
 */
function phaseSwitcher(initialLabel, allLabels, initialPinned) {
  return {
    current: initialLabel,
    labels: allLabels,
    pinned: initialPinned,
    open: false,

    async selectLabel(label) {
      this.open = false;
      this.$dispatch('phase-switching');
      try {
        const res = await fetch('/api/control/active-label', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ label }),
        });
        if (res.ok) {
          const data = await res.json();
          this.current = data.label;
          this.pinned = data.pinned;
          window.location.reload();
        }
      } catch (_) {}
    },

    async selectAuto() {
      this.open = false;
      this.$dispatch('phase-switching');
      try {
        const res = await fetch('/api/control/active-label', { method: 'DELETE' });
        if (res.ok) {
          const data = await res.json();
          this.current = data.label;
          this.pinned = false;
          window.location.reload();
        }
      } catch (_) {}
    },
  };
}

// ---------------------------------------------------------------------------
// Overview — pipeline pause/resume control
// ---------------------------------------------------------------------------

/**
 * Toggle the background poller between paused and running.
 *
 * @param {boolean} initialPaused - Server-rendered current pause state.
 */
function pipelineControl(initialPaused) {
  return {
    paused: initialPaused ?? false,

    async toggle() {
      const endpoint = this.paused ? '/api/control/resume' : '/api/control/pause';
      try {
        const res = await fetch(endpoint, { method: 'POST' });
        if (res.ok) {
          const data = await res.json();
          this.paused = data.paused;
        }
      } catch (_) {
        // Network error — leave state unchanged.
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Overview — wave control (start-wave button)
// ---------------------------------------------------------------------------

/**
 * Counts unclaimed board issues and fires POST /api/control/spawn-wave.
 * Data flows from the parent pipelineDashboard state via Alpine scope.
 */
function waveControl() {
  return {
    launching: false,
    waveResult: null,
    waveError: null,

    unclaimedCount() {
      const issues = this.state?.board_issues;
      if (!issues) return 0;
      return issues.filter(i => !i.claimed).length;
    },

    claimedCount() {
      const issues = this.state?.board_issues;
      if (!issues) return 0;
      return issues.filter(i => i.claimed).length;
    },

    async startWave() {
      this.launching  = true;
      this.waveResult = null;
      this.waveError  = null;
      try {
        const resp = await fetch('/api/control/spawn-wave', { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) {
          this.waveError = data.detail ?? `HTTP ${resp.status}`;
        } else {
          this.waveResult = data;
        }
      } catch (err) {
        this.waveError = `Network error: ${err.message}`;
      } finally {
        this.launching = false;
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Overview — scaling advisor banner
// ---------------------------------------------------------------------------

/**
 * Shows a recommendation banner when the pool sizing is off.
 * "Apply" posts to the API; "Dismiss" hides locally for the session.
 *
 * @param {object|null} initial - Server-rendered ScalingAdvice object.
 */
function scalingAdvisor(initial) {
  return {
    recommendation: initial ?? null,
    dismissed: false,
    applying: false,
    applyError: null,

    async apply() {
      this.applying   = true;
      this.applyError = null;
      try {
        const res = await fetch('/api/intelligence/scaling-advice/apply', { method: 'POST' });
        if (res.ok) {
          this.dismissed = true;
        } else {
          const data = await res.json().catch(() => ({}));
          this.applyError = data.detail || `HTTP ${res.status}`;
        }
      } catch (err) {
        this.applyError = err.message || 'Network error';
      } finally {
        this.applying = false;
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Overview — PR violations banner
// ---------------------------------------------------------------------------

/**
 * Displays out-of-order PR cards and lets the user close the PR from here.
 *
 * @param {object[]|null} initial - Server-rendered list of PRViolation objects.
 */
function prViolations(initial) {
  return {
    violations: initial ?? [],
    closing: null,

    async closeViolation(prNumber) {
      this.closing = prNumber;
      try {
        const res = await fetch(
          `/api/intelligence/pr-violations/${prNumber}/close`,
          { method: 'POST' },
        );
        if (res.ok) {
          this.violations = this.violations.filter(v => v.pr_number !== prNumber);
        }
      } catch (_) {
        // Network error — leave the card visible so the user can retry.
      } finally {
        this.closing = null;
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Overview — stale claim card
// ---------------------------------------------------------------------------

/**
 * Two-step confirm → clear flow for a single stale agent:wip claim.
 *
 * @param {object} claim - StaleClaim object from PipelineState.stale_claims.
 */
function staleClaimCard(claim) {
  return {
    confirming: false,
    clearing: false,
    cleared: false,
    error: null,

    async clear(issueNumber) {
      this.confirming = false;
      this.clearing   = true;
      this.error      = null;
      try {
        const res = await fetch(
          `/api/intelligence/stale-claims/${issueNumber}/clear`,
          { method: 'POST' },
        );
        if (res.ok) {
          this.cleared = true;
        } else {
          const data = await res.json().catch(() => ({}));
          this.error = data.detail || `HTTP ${res.status}`;
        }
      } catch (err) {
        this.error = err.message || 'Network error';
      } finally {
        this.clearing = false;
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Telemetry — sparkline trend chart
// ---------------------------------------------------------------------------

/**
 * Draws a three-series canvas sparkline (issues, PRs, agents).
 * No external charting library required.
 *
 * @param {string[]} labels  - X-axis time labels.
 * @param {number[]} issues  - Issue counts per bucket.
 * @param {number[]} prs     - PR counts per bucket.
 * @param {number[]} agents  - Agent counts per bucket.
 */
function trendChart(labels, issues, prs, agents) {
  return {
    labels, issues, prs, agents,

    draw() {
      const canvas = document.getElementById('trend-canvas');
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      const W = canvas.width, H = canvas.height;
      const pad = { top: 16, right: 24, bottom: 28, left: 40 };
      const iW = W - pad.left - pad.right;
      const iH = H - pad.top  - pad.bottom;

      ctx.clearRect(0, 0, W, H);

      const allVals = [...this.issues, ...this.prs, ...this.agents];
      const maxY    = Math.max(...allVals, 1);
      const n       = this.labels.length;
      if (n < 2) return;

      const xOf = i => pad.left + (i / (n - 1)) * iW;
      const yOf = v => pad.top + iH - (v / maxY) * iH;

      const series = [
        { data: this.issues, color: '#8b5cf6' },
        { data: this.prs,    color: '#06b6d4' },
        { data: this.agents, color: '#22c55e' },
      ];

      // Grid lines
      ctx.strokeStyle = 'rgba(255,255,255,0.06)';
      ctx.lineWidth = 1;
      for (let g = 0; g <= 4; g++) {
        const y = pad.top + (g / 4) * iH;
        ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + iW, y);
        ctx.stroke();
        ctx.fillStyle  = 'rgba(255,255,255,0.35)';
        ctx.font       = '10px monospace';
        ctx.textAlign  = 'right';
        ctx.fillText(Math.round(maxY * (1 - g / 4)), pad.left - 6, y + 4);
      }

      // Series lines
      series.forEach(({ data, color }) => {
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth   = 1.5;
        ctx.lineJoin    = 'round';
        data.forEach((v, i) => {
          const x = xOf(i), y = yOf(v);
          i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        });
        ctx.stroke();
      });

      // X-axis labels — ~6 evenly spaced
      const step = Math.max(1, Math.floor(n / 6));
      ctx.fillStyle = 'rgba(255,255,255,0.35)';
      ctx.font      = '10px monospace';
      ctx.textAlign = 'center';
      for (let i = 0; i < n; i += step) {
        ctx.fillText(this.labels[i], xOf(i), H - 6);
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Config — pipeline config editor
// ---------------------------------------------------------------------------

/**
 * Full config editor with drag-and-drop label reordering and save/toast.
 * Initial config is passed from the server; init() re-fetches to stay fresh.
 *
 * @param {object|null} initialConfig - Server-rendered PipelineConfig dict.
 */
function configPanel(initialConfig) {
  const defaults = { max_eng_vps: 1, max_qa_vps: 1, pool_size_per_vp: 4, active_labels_order: [] };
  return {
    config: initialConfig || defaults,
    newLabel: '',
    saving: false,
    toast: { msg: '', cls: '' },
    _dragIdx: null,

    async init() {
      try {
        const r = await fetch('/api/config');
        if (!r.ok) throw new Error('HTTP ' + r.status);
        this.config = await r.json();
      } catch (err) {
        this._showToast('Failed to load config: ' + err.message, 'err');
      }
    },

    addLabel() {
      const label = this.newLabel.trim();
      if (!label) return;
      if (this.config.active_labels_order.includes(label)) {
        this._showToast('Label already in list.', 'err');
        return;
      }
      this.config.active_labels_order.push(label);
      this.newLabel = '';
    },

    removeLabel(idx) { this.config.active_labels_order.splice(idx, 1); },

    onDragStart(evt, idx) {
      this._dragIdx = idx;
      evt.dataTransfer.effectAllowed = 'move';
      evt.dataTransfer.setData('text/plain', String(idx));
    },

    onDragOver(evt)  { evt.currentTarget.classList.add('drag-over'); },
    onDragLeave(evt) { evt.currentTarget.classList.remove('drag-over'); },

    onDrop(evt, targetIdx) {
      evt.currentTarget.classList.remove('drag-over');
      const srcIdx = this._dragIdx;
      if (srcIdx === null || srcIdx === targetIdx) return;
      const arr = this.config.active_labels_order;
      const [item] = arr.splice(srcIdx, 1);
      arr.splice(targetIdx, 0, item);
      this._dragIdx = null;
    },

    onDragEnd() {
      document.querySelectorAll('.label-item.drag-over').forEach(el => {
        el.classList.remove('drag-over');
      });
      this._dragIdx = null;
    },

    async save() {
      this.saving = true;
      this._showToast('', '');
      try {
        const r = await fetch('/api/config', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.config),
        });
        if (!r.ok) {
          const detail = await r.json().then(d => d.detail || r.statusText).catch(() => r.statusText);
          throw new Error(detail);
        }
        this.config = await r.json();
        this._showToast('✅ Config saved.', 'ok');
      } catch (err) {
        this._showToast('Save failed: ' + err.message, 'err');
      } finally {
        this.saving = false;
      }
    },

    _showToast(msg, cls) { this.toast = { msg, cls }; },
  };
}

// ---------------------------------------------------------------------------
// Spawn — manual agent spawn form
// ---------------------------------------------------------------------------

/**
 * Posts to POST /api/control/spawn and shows the worktree path on success.
 */
function spawnForm() {
  return {
    issueNumber: '',
    role: 'python-developer',
    loading: false,
    result: null,
    formError: null,

    async submit() {
      this.result    = null;
      this.formError = null;
      this.loading   = true;
      try {
        const resp = await fetch('/api/control/spawn', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            issue_number: parseInt(this.issueNumber, 10),
            role: this.role,
          }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          this.formError = data.detail ?? `HTTP ${resp.status}`;
        } else {
          this.result = data;
        }
      } catch (err) {
        this.formError = `Network error: ${err.message}`;
      } finally {
        this.loading = false;
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Templates — export / import panels
// ---------------------------------------------------------------------------

/** Role template export panel. */
function exportPanel() {
  return {
    name: '',
    version: '',
    busy: false,
    toast: { msg: '', cls: '' },

    async doExport() {
      if (!this.name || !this.version) return;
      this.busy = true;
      this._toast('', '');
      try {
        const r = await fetch('/api/templates/export', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: this.name, version: this.version }),
        });
        if (!r.ok) {
          const d = await r.json().catch(() => ({ detail: r.statusText }));
          throw new Error(d.detail || r.statusText);
        }
        const blob = await r.blob();
        const cd   = r.headers.get('Content-Disposition') || '';
        const match = cd.match(/filename="([^"]+)"/);
        const filename = match ? match[1] : 'template.tar.gz';
        const url = URL.createObjectURL(blob);
        const a   = document.createElement('a');
        a.href = url; a.download = filename; a.click();
        URL.revokeObjectURL(url);
        this._toast('✅ Exported — page will refresh to show the new entry.', 'ok');
        setTimeout(() => location.reload(), 1500);
      } catch (err) {
        this._toast('Export failed: ' + err.message, 'err');
      } finally {
        this.busy = false;
      }
    },

    _toast(msg, cls) { this.toast = { msg, cls }; },
  };
}

/** Role template import panel. */
function importPanel() {
  return {
    file: null,
    targetRepo: '',
    busy: false,
    toast: { msg: '', cls: '' },
    conflicts: [],

    onFileChange(evt) { this.file = evt.target.files[0] || null; },

    async doImport() {
      if (!this.file || !this.targetRepo) return;
      this.busy = true;
      this.conflicts = [];
      this._toast('', '');
      try {
        const fd  = new FormData();
        fd.append('file', this.file);
        const url = '/api/templates/import?target_repo=' + encodeURIComponent(this.targetRepo);
        const r   = await fetch(url, { method: 'POST', body: fd });
        if (!r.ok) {
          const d = await r.json().catch(() => ({ detail: r.statusText }));
          throw new Error(d.detail || r.statusText);
        }
        const result  = await r.json();
        this.conflicts = (result.conflicts || []).filter(c => c.exists);
        this._toast('✅ Imported ' + result.extracted.length + ' file(s) (' + result.name + ' v' + result.version + ').', 'ok');
      } catch (err) {
        this._toast('Import failed: ' + err.message, 'err');
      } finally {
        this.busy = false;
      }
    },

    _toast(msg, cls) { this.toast = { msg, cls }; },
  };
}

// ---------------------------------------------------------------------------
// Transcripts — list browser
// ---------------------------------------------------------------------------

/**
 * Client-side filter and sort for the transcript browser table.
 * Server pre-filters via query params; this handles live typing without
 * a round-trip.
 *
 * @param {string} initialQ      - Server-applied ?q= value.
 * @param {string} initialRole   - Server-applied ?role= value.
 * @param {string} initialStatus - Server-applied ?status= value.
 */
function transcriptBrowser(initialQ, initialRole, initialStatus) {
  return {
    q: initialQ || '',
    filterRole: initialRole || '',
    filterStatus: initialStatus || '',
    sort: 'mtime',

    applyFilters() {
      const qLow = this.q.toLowerCase();
      document.querySelectorAll('[data-transcript-row]').forEach(el => {
        const preview = (el.dataset.preview || '').toLowerCase();
        const role    = el.dataset.role    || '';
        const status  = el.dataset.status  || '';
        const matchQ  = !qLow || preview.includes(qLow);
        const matchR  = !this.filterRole   || role   === this.filterRole;
        const matchS  = !this.filterStatus || status === this.filterStatus;
        el.style.display = (matchQ && matchR && matchS) ? '' : 'none';
      });
      this.applySortDom();
    },

    applySortDom() {
      const tbody = document.getElementById('transcript-tbody');
      if (!tbody) return;
      const rows = Array.from(tbody.querySelectorAll('[data-transcript-row]'));
      rows.sort((a, b) => {
        switch (this.sort) {
          case 'messages': return parseInt(b.dataset.messages) - parseInt(a.dataset.messages);
          case 'words':    return parseInt(b.dataset.words)    - parseInt(a.dataset.words);
          case 'subagents':return parseInt(b.dataset.subagents)- parseInt(a.dataset.subagents);
          default:         return parseFloat(b.dataset.mtime)  - parseFloat(a.dataset.mtime);
        }
      });
      rows.forEach(r => tbody.appendChild(r));
    },
  };
}

// ---------------------------------------------------------------------------
// Transcript detail — in-conversation search + highlight
// ---------------------------------------------------------------------------

/**
 * Powers the in-thread search field on the transcript detail page.
 * Filters messages client-side via Alpine x-show and injects <mark> tags
 * for matched text via x-html.
 */
function transcriptDetail() {
  return {
    search: '',

    highlight(text) {
      if (!this.search) return this._esc(text);
      const escaped = this.search.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const re = new RegExp(escaped, 'gi');
      return this._esc(text).replace(re, m => `<mark class="hl">${m}</mark>`);
    },

    /** HTML-escape plain text before injecting highlight marks. */
    _esc(text) {
      return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    },
  };
}
