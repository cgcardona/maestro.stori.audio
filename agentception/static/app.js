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
 *   agentCard()                               — expandable agent row with live transcript
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
    // GH_BASE_URL is provided via data-gh-base-url on the root element so no
    // inline <script> tag is needed.  Set in connect() once $el is available.
    GH_BASE_URL: '',

    // Global kill modal — lives here so SSE re-renders never reset it.
    killModal: { show: false, agentId: null, issueNumber: null, killing: false },

    connect() {
      this.GH_BASE_URL = this.$el.dataset.ghBaseUrl ?? '';
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

    /** Open the kill confirmation modal for a specific agent. */
    openKillModal(agentId, issueNumber) {
      this.killModal = { show: true, agentId, issueNumber: issueNumber ?? null, killing: false };
    },

    /** Close the kill modal without acting. */
    closeKillModal() {
      this.killModal.show = false;
    },

    /** Perform the kill: DELETE worktree via API, then close modal. */
    async confirmKill() {
      const id = this.killModal.agentId;
      if (!id) return;
      this.killModal.killing = true;
      try {
        await fetch(`/api/control/kill/${encodeURIComponent(id)}`, { method: 'POST' });
      } catch (e) {
        console.error('Kill failed', e);
      } finally {
        this.killModal = { show: false, agentId: null, issueNumber: null, killing: false };
      }
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

    /** Find the first live agent working on a given issue number (for board cross-reference). */
    agentForIssue(issueNumber) {
      if (!this.state || !this.state.agents) return null;
      return this.state.agents.find(a => a.issue_number === issueNumber) || null;
    },

    /** Sum of message_count across all currently tracked agents. */
    totalMsgCount() {
      if (!this.state || !this.state.agents) return 0;
      return this.state.agents.reduce((sum, a) => sum + (a.message_count || 0), 0);
    },
  };
}

// ---------------------------------------------------------------------------
// Overview — expandable agent card with live transcript
// ---------------------------------------------------------------------------

/**
 * Powers each agent row in the Agent Tree.
 *
 * The card manages its own expansion state and the on-demand transcript fetch.
 * Reactive agent data (status, issue_number, etc.) lives in the parent SSE
 * scope and is passed in via x-effect so re-fetching triggers automatically
 * when message_count changes.
 *
 * Usage in template:
 *   <div x-data="agentCard()" x-effect="checkRefresh(agent, agent.message_count)">
 */
function agentCard() {
  return {
    expanded:         false,
    transcript:       [],
    transcriptLoading: false,
    transcriptError:  null,
    _prevMsgCount:    0,

    /** Toggle expand/collapse; fetch transcript on first open. */
    toggle(agentId) {
      this.expanded = !this.expanded;
      if (this.expanded && this.transcript.length === 0) {
        this.fetchTranscript(agentId);
      }
    },

    /**
     * Fetch the agent's transcript from the REST endpoint.
     * Called on expand and when message_count changes while expanded.
     */
    async fetchTranscript(agentId) {
      this.transcriptLoading = true;
      this.transcriptError   = null;
      try {
        const res = await fetch(`/api/agents/${encodeURIComponent(agentId)}/transcript`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        // API may return an array directly or {messages:[]} — handle both.
        this.transcript = Array.isArray(data) ? data : (data.messages || []);
        this._scrollFeed();
      } catch (err) {
        this.transcriptError = err.message || 'Failed to load transcript';
      } finally {
        this.transcriptLoading = false;
      }
    },

    /**
     * Called from x-effect="checkRefresh(agent, agent.message_count)".
     * The explicit agent.message_count arg makes Alpine track it as a dependency
     * so this re-runs on every SSE tick where the count changed.
     */
    checkRefresh(agent, msgCount) {
      const count = msgCount || 0;
      if (this.expanded && count !== this._prevMsgCount) {
        this.fetchTranscript(agent.id);
      }
      this._prevMsgCount = count;
    },

    /** Scroll the transcript feed to the latest message. */
    _scrollFeed() {
      this.$nextTick(() => {
        const feed = this.$el.querySelector('.transcript-feed');
        if (feed) feed.scrollTop = feed.scrollHeight;
      });
    },

    /** Format a kebab-case role name as Title Case words. */
    formatRole(role) {
      return (role || '').split('-')
        .map(w => w.charAt(0).toUpperCase() + w.slice(1))
        .join(' ');
    },

    /** True for statuses that mean the agent is actively working. */
    isActive(status) {
      return status === 'implementing' || status === 'reviewing';
    },

    /** Clip long text for compact transcript preview. */
    truncate(text, maxLen) {
      if (!text) return '';
      return text.length > maxLen ? text.slice(0, maxLen) + '…' : text;
    },

    /**
     * Parse a COGNITIVE_ARCH string into display-ready fields.
     * Format: "figure_id:skill1:skill2"
     * Returns an object with emoji, displayName, archetype, archetypeKey, skillDomains.
     */
    parseArch(archStr) {
      if (!archStr) return { emoji: '🤖', displayName: '', archetype: '', archetypeKey: 'default', skillDomains: [], figureId: null };

      const FIGURE_MAP = {
        steve_jobs:         { name: 'Steve Jobs',          emoji: '🔮', archetype: 'The Visionary',   key: 'visionary' },
        satya_nadella:      { name: 'Satya Nadella',       emoji: '🏛️', archetype: 'The Architect',   key: 'architect' },
        jeff_bezos:         { name: 'Jeff Bezos',          emoji: '⚙️', archetype: 'The Operator',    key: 'operator' },
        werner_vogels:      { name: 'Werner Vogels',       emoji: '🏛️', archetype: 'The Architect',   key: 'architect' },
        linus_torvalds:     { name: 'Linus Torvalds',      emoji: '⚡', archetype: 'The Hacker',      key: 'hacker' },
        margaret_hamilton:  { name: 'Margaret Hamilton',   emoji: '🛡️', archetype: 'The Guardian',    key: 'guardian' },
        bjarne_stroustrup:  { name: 'Bjarne Stroustrup',   emoji: '⚡', archetype: 'The Hacker',      key: 'hacker' },
        martin_fowler:      { name: 'Martin Fowler',       emoji: '📚', archetype: 'The Scholar',     key: 'scholar' },
        kent_beck:          { name: 'Kent Beck',           emoji: '🔧', archetype: 'The Pragmatist',  key: 'pragmatist' },
        yann_lecun:         { name: 'Yann LeCun',          emoji: '📚', archetype: 'The Scholar',     key: 'scholar' },
        andrej_karpathy:    { name: 'Andrej Karpathy',     emoji: '📚', archetype: 'The Scholar',     key: 'scholar' },
        turing:             { name: 'Alan Turing',         emoji: '📚', archetype: 'The Scholar',     key: 'scholar' },
        hopper:             { name: 'Grace Hopper',        emoji: '🧑‍🏫', archetype: 'The Mentor',   key: 'mentor' },
        dijkstra:           { name: 'Edsger Dijkstra',     emoji: '📚', archetype: 'The Scholar',     key: 'scholar' },
        knuth:              { name: 'Donald Knuth',        emoji: '📚', archetype: 'The Scholar',     key: 'scholar' },
        ritchie:            { name: 'Dennis Ritchie',      emoji: '⚡', archetype: 'The Hacker',      key: 'hacker' },
        guido_van_rossum:   { name: 'Guido van Rossum',   emoji: '🔧', archetype: 'The Pragmatist',  key: 'pragmatist' },
        mccarthy:           { name: 'John McCarthy',       emoji: '📚', archetype: 'The Scholar',     key: 'scholar' },
        lovelace:           { name: 'Ada Lovelace',        emoji: '🔮', archetype: 'The Visionary',   key: 'visionary' },
        von_neumann:        { name: 'John von Neumann',    emoji: '📚', archetype: 'The Scholar',     key: 'scholar' },
        shannon:            { name: 'Claude Shannon',      emoji: '📚', archetype: 'The Scholar',     key: 'scholar' },
        feynman:            { name: 'Richard Feynman',     emoji: '📚', archetype: 'The Scholar',     key: 'scholar' },
        einstein:           { name: 'Albert Einstein',     emoji: '🔮', archetype: 'The Visionary',   key: 'visionary' },
        hamming:            { name: 'Richard Hamming',     emoji: '📚', archetype: 'The Scholar',     key: 'scholar' },
        bruce_schneier:     { name: 'Bruce Schneier',      emoji: '🛡️', archetype: 'The Guardian',    key: 'guardian' },
      };

      const ARCHETYPE_MAP = {
        the_visionary:  { emoji: '🔮', name: 'The Visionary',  key: 'visionary' },
        the_architect:  { emoji: '🏛️', name: 'The Architect',  key: 'architect' },
        the_hacker:     { emoji: '⚡', name: 'The Hacker',     key: 'hacker' },
        the_guardian:   { emoji: '🛡️', name: 'The Guardian',   key: 'guardian' },
        the_scholar:    { emoji: '📚', name: 'The Scholar',    key: 'scholar' },
        the_mentor:     { emoji: '🧑‍🏫', name: 'The Mentor',  key: 'mentor' },
        the_operator:   { emoji: '⚙️', name: 'The Operator',   key: 'operator' },
        the_pragmatist: { emoji: '🔧', name: 'The Pragmatist', key: 'pragmatist' },
      };

      const parts = archStr.split(':').map(p => p.trim()).filter(Boolean);
      const first = parts[0];
      const figData = FIGURE_MAP[first];

      if (figData) {
        return {
          figureId: first,
          displayName: figData.name,
          emoji: figData.emoji,
          archetype: figData.archetype,
          archetypeKey: figData.key,
          skillDomains: parts.slice(1),
        };
      }

      // Check if first part is an archetype
      const archData = ARCHETYPE_MAP[first];
      if (archData) {
        return {
          figureId: null,
          displayName: archData.name,
          emoji: archData.emoji,
          archetype: archData.name,
          archetypeKey: archData.key,
          skillDomains: parts.slice(1),
        };
      }

      // Unknown — treat all parts as skill domains
      return {
        figureId: null,
        displayName: parts.join(' · '),
        emoji: '🤖',
        archetype: '',
        archetypeKey: 'default',
        skillDomains: parts,
      };
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
// Overview — sweep stale branches / claims
// ---------------------------------------------------------------------------

/**
 * Calls POST /api/control/sweep to delete stale branches and clear orphan
 * agent:wip labels in one shot.  Lives inside the "Stale" summary-item so
 * it shares the same Alpine scope as the count badge.
 */
function sweepControl() {
  return {
    sweeping: false,

    async sweep() {
      if (!confirm('Delete all stale agent branches and clear orphan agent:wip labels?')) return;
      this.sweeping = true;
      try {
        const resp = await fetch('/api/control/sweep', { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) {
          alert(`Sweep failed: ${data.detail ?? resp.status}`);
          return;
        }
        const b = data.deleted_branches?.length ?? 0;
        const w = data.cleared_wip_labels?.length ?? 0;
        const e = data.errors?.length ?? 0;
        const msg = [`Swept: ${b} branch${b !== 1 ? 'es' : ''} deleted, ${w} label${w !== 1 ? 's' : ''} cleared`];
        if (e > 0) msg.push(`(${e} error${e !== 1 ? 's' : ''} — check logs)`);
        alert(msg.join(' '));
      } catch (err) {
        alert(`Network error: ${err.message}`);
      } finally {
        this.sweeping = false;
      }
    },
  };
}

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

    init() {
      // Data flows from the parent pipelineDashboard scope via SSE — no polling needed here.
    },

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
// Overview — issue card (Analyze button)
// ---------------------------------------------------------------------------

/**
 * Powers each issue card in the GitHub Board's open issues list.
 *
 * Keeps the inline fetch out of the template.  The analysisHtml is injected
 * via x-html after the POST returns so no HTMX processing is needed.
 *
 * @param {number} issueNumber - GitHub issue number for the API call.
 */
function issueCard(issueNumber) {
  return {
    analysisHtml: '',
    analyzing: false,
    analyzeError: null,

    async analyze() {
      this.analyzing = true;
      this.analyzeError = null;
      try {
        const r = await fetch(`/api/analyze/issue/${issueNumber}/partial`, { method: 'POST' });
        this.analysisHtml = await r.text();
      } catch (err) {
        this.analyzeError = err.message;
      } finally {
        this.analyzing = false;
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

// ── Cognitive Architecture Studio ────────────────────────────────────────────
//
// roleDetail(slug, fileExists, personas)
//   Alpine component for the center panel (loaded via HTMX partial).
//   Manages tab state, persona selection, composer form, and dispatches
//   the `role:selected` window event so rolesEditor() loads the file.
//
// rolesEditor()
//   Alpine component for the right panel (Monaco editor).
//   Initialises Monaco once, listens for `role:selected`, handles
//   save / diff / commit.
// ----------------------------------------------------------------------------

function roleDetail(slug, fileExists, personas) {
  return {
    slug,
    fileExists,
    personas,
    activeTab: 'personas',
    selectedPersonaId: null,
    figure: '',
    atomOverrides: {},
    skills: [],
    copied: false,

    init() {
      if (this.fileExists) {
        window.dispatchEvent(new CustomEvent('role:selected', { detail: { slug: this.slug } }));
      }
    },

    get archPreview() {
      const parts = [];
      if (this.figure) parts.push(this.figure);
      for (const s of this.skills) parts.push(s);
      return parts.length ? `COGNITIVE_ARCH=${parts.join(':')}` : '(select a figure or skill)';
    },

    selectPersona(id) {
      this.selectedPersonaId = id;
    },

    applyPersona(id) {
      const persona = this.personas.find(p => p.id === id);
      if (!persona) return;
      this.figure = id;
      this.atomOverrides = persona.overrides ? { ...persona.overrides } : {};
      this.skills = [];
      this.activeTab = 'composer';
    },

    async copyArchString() {
      await navigator.clipboard.writeText(this.archPreview);
      this.copied = true;
      setTimeout(() => { this.copied = false; }, 1500);
    },

    resetComposer() {
      this.figure = '';
      this.atomOverrides = {};
      this.skills = [];
    },
  };
}

function rolesEditor() {
  return {
    editor: null,
    currentSlug: null,
    currentPath: null,
    status: '',
    statusClass: '',
    breadcrumb: '← select a role to edit',
    canSave: false,
    canDiff: false,
    diffVisible: false,
    diffTitle: '',
    diffLines: [],
    diffCommitReady: false,
    diffCommitting: false,

    init() {
      // Guard against Alpine re-running x-init (e.g. after HTMX swaps nearby DOM).
      // Monaco throws "Element already has context attribute" if create() is called
      // on a container that already owns an editor instance.
      if (this.editor) return;

      // Monaco AMD loader is added to this page only — configure CDN path and boot
      require.config({ paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.0/min/vs' } });
      require(['vs/editor/editor.main'], () => {
        // Second guard: the AMD callback can fire more than once if the module
        // was already cached by a previous require() call.
        if (this.editor) return;
        if (this.$refs.editorPlaceholder) this.$refs.editorPlaceholder.style.display = 'none';
        this.editor = monaco.editor.create(this.$refs.editorContainer, {
          value: '',
          language: 'markdown',
          theme: 'vs-dark',
          automaticLayout: true,
          minimap: { enabled: false },
          wordWrap: 'on',
          scrollBeyondLastLine: false,
          readOnly: true,
        });
      });
    },

    async loadRole(slug) {
      if (!this.editor) {
        this.setStatus('Monaco loading — try again in a moment.', 'err');
        return;
      }
      const path = `.cursor/roles/${slug}.md`;
      this.setStatus(`Loading ${path}…`);
      try {
        const r = await fetch(`/api/roles/${encodeURIComponent(slug)}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        this.editor.setValue(data.content);
        this.editor.updateOptions({ readOnly: false });
        this.currentSlug = slug;
        this.currentPath = path;
        this.breadcrumb = path;
        this.canSave = true;
        this.canDiff = true;
        this.setStatus(`Loaded ${data.meta.line_count} lines · ${data.meta.last_commit_message || '(uncommitted)'}`, 'ok');
      } catch (err) {
        this.setStatus(`Failed to load: ${err.message}`, 'err');
      }
    },

    async saveRole() {
      if (!this.editor || !this.currentSlug) return;
      this.canSave = false;
      this.setStatus('Saving…');
      try {
        const r = await fetch(`/api/roles/${encodeURIComponent(this.currentSlug)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: this.editor.getValue() }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        this.canSave = true;
        this.setStatus(`✅ Saved — diff: ${data.diff ? data.diff.split('\n').length : 0} line(s).`, 'ok');
      } catch (err) {
        this.canSave = true;
        this.setStatus(`Save failed: ${err.message}`, 'err');
      }
    },

    async previewDiff() {
      if (!this.editor || !this.currentSlug) return;
      this.diffTitle = `Diff — ${this.currentPath}`;
      this.diffLines = [];
      this.diffCommitReady = false;
      this.diffVisible = true;
      try {
        const r = await fetch(`/api/roles/${encodeURIComponent(this.currentSlug)}/diff`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: this.editor.getValue() }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        this.diffLines = this._parseDiff(data.diff);
        this.diffCommitReady = true;
      } catch (err) {
        this.diffLines = [{ cls: 'diff-empty', text: `Failed: ${err.message}` }];
      }
    },

    _parseDiff(diff) {
      if (!diff || !diff.trim()) {
        return [{ cls: 'diff-empty', text: 'No changes — content is identical to HEAD.' }];
      }
      return diff.split('\n').map(line => {
        let cls = 'diff-line';
        if (line.startsWith('+') && !line.startsWith('+++')) cls += ' added';
        else if (line.startsWith('-') && !line.startsWith('---')) cls += ' removed';
        else if (line.startsWith('@@')) cls += ' hunk';
        return { cls, text: line };
      });
    },

    async saveAndCommit() {
      if (!this.editor || !this.currentSlug) return;
      this.diffCommitting = true;
      try {
        const r = await fetch(`/api/roles/${encodeURIComponent(this.currentSlug)}/commit`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: this.editor.getValue() }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        this.diffVisible = false;
        this.setStatus(`✅ Committed — SHA ${data.commit_sha.slice(0, 8)} · ${data.message}`, 'ok');
      } catch (err) {
        this.setStatus(`Commit failed: ${err.message}`, 'err');
      } finally {
        this.diffCommitting = false;
      }
    },

    setStatus(msg, cls = '') {
      this.status = msg;
      this.statusClass = cls;
    },
  };
}

// ---------------------------------------------------------------------------
// Brain Dump page
// ---------------------------------------------------------------------------

/**
 * Powers the Brain Dump form — text input → loading animation → done panel.
 *
 * Static data (loading messages) is read from data attributes on the root
 * element so they live in Python/Jinja, not in this file.
 * Seeds are rendered server-side by Jinja; each button passes its text via
 * $el.dataset.text so no seed array is needed here.
 *
 * HTMX refreshes the recent-runs sidebar after a successful submit:
 *   htmx.trigger('#bd-recent-runs', 'refresh')
 */
function brainDump() {
  return {
    step: 'input',
    funnelStage: 0,
    text: '',
    labelPrefix: '',
    showOptions: false,
    focused: false,
    submitting: false,
    errorMsg: '',
    result: {},
    loadingMsg: '',
    _loadingMsgs: [],
    _loadingTimer: null,

    init() {
      // Loading messages are defined in Python, injected via data attribute.
      this._loadingMsgs = JSON.parse(this.$el.dataset.loadingMsgs || '[]');
      this.loadingMsg = this._loadingMsgs[0] ?? '';
    },

    get lineCount() {
      return this.text.split('\n').filter(l => l.trim()).length;
    },

    stageClass(n) {
      if (this.funnelStage > n) return 'bd-funnel-stage--done';
      if (this.funnelStage === n && this.step !== 'input') return 'bd-funnel-stage--active bd-funnel-stage--pulse';
      if (n === 0 && this.step === 'input') return 'bd-funnel-stage--active';
      return '';
    },

    autoGrow(el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 600) + 'px';
    },

    async pasteClipboard() {
      try {
        const t = await navigator.clipboard.readText();
        this.text = (this.text ? this.text + '\n' : '') + t;
        await this.$nextTick();
        this.autoGrow(this.$refs.textarea);
      } catch (_) {}
    },

    // Called from Jinja-rendered seed buttons: @click="appendSeed($el.dataset.text)"
    appendSeed(txt) {
      this.text = (this.text.trim() ? this.text.trim() + '\n' : '') + txt;
      this.$nextTick(() => this.autoGrow(this.$refs.textarea));
    },

    _startLoadingAnimation() {
      let i = 0;
      this.funnelStage = 1;
      this.loadingMsg = this._loadingMsgs[0] ?? '';
      this._loadingTimer = setInterval(() => {
        i = (i + 1) % this._loadingMsgs.length;
        this.loadingMsg = this._loadingMsgs[i] ?? '';
        this.funnelStage = Math.min(i + 1, 5);
      }, 700);
    },

    _stopLoadingAnimation(finalStage) {
      if (this._loadingTimer) { clearInterval(this._loadingTimer); this._loadingTimer = null; }
      this.funnelStage = finalStage;
    },

    async submit() {
      const trimmed = this.text.trim();
      if (!trimmed) return;
      this.submitting = true;
      this.errorMsg = '';
      this.step = 'loading';
      this._startLoadingAnimation();
      try {
        const resp = await fetch('/api/control/spawn-coordinator', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ brain_dump: trimmed, label_prefix: this.labelPrefix.trim() }),
        });
        if (!resp.ok) {
          const body = await resp.json().catch(() => ({}));
          throw new Error(body.detail || `HTTP ${resp.status}`);
        }
        this.result = await resp.json();
        this._stopLoadingAnimation(5);
        this.step = 'done';
        // Refresh the recent-runs sidebar via HTMX so the new run appears.
        const sidebar = document.getElementById('bd-recent-runs');
        if (sidebar && typeof htmx !== 'undefined') htmx.trigger(sidebar, 'refresh');
      } catch (err) {
        this._stopLoadingAnimation(0);
        this.errorMsg = err.message;
        this.step = 'input';
      } finally {
        this.submitting = false;
      }
    },

    reset() {
      this._stopLoadingAnimation(0);
      this.step = 'input';
      this.funnelStage = 0;
      this.text = '';
      this.labelPrefix = '';
      this.showOptions = false;
      this.errorMsg = '';
      this.result = {};
    },
  };
}

// ---------------------------------------------------------------------------
// API Reference — per-endpoint execution
// ---------------------------------------------------------------------------

/**
 * Powers each endpoint card on /api.
 *
 * Data is read from data-* attributes on the root element so all static
 * values (method, path template, param specs, body field definitions) live
 * in Python/Jinja rather than in this file.
 *
 * data-method        — HTTP verb (GET, POST, …)
 * data-path          — path template, e.g. /agents/{agent_id}
 * data-query-params  — JSON array of {name, required, description} objects
 * data-body-fields   — JSON array of {name, type, required, default} objects
 */
function apiEndpoint() {
  return {
    open: false,
    tryOpen: false,
    method: 'GET',
    pathTemplate: '/',
    pathParamNames: [],   // e.g. ['agent_id']
    pathParamValues: {},  // {agent_id: ''}
    queryParams: [],      // [{name, required, description}]
    queryValues: {},      // {name: ''}
    bodyFields: [],
    bodyJson: '',
    response: null,
    sending: false,

    init() {
      this.method       = this.$el.dataset.method || 'GET';
      this.pathTemplate = this.$el.dataset.path   || '/';

      // Query params
      try { this.queryParams = JSON.parse(this.$el.dataset.queryParams || '[]'); }
      catch { this.queryParams = []; }
      for (const p of this.queryParams) this.queryValues[p.name] = '';

      // Body fields → JSON skeleton
      try { this.bodyFields = JSON.parse(this.$el.dataset.bodyFields || '[]'); }
      catch { this.bodyFields = []; }
      if (this.bodyFields.length) {
        const skeleton = {};
        for (const f of this.bodyFields) {
          if (f.default !== null && f.default !== undefined && f.default !== '') {
            skeleton[f.name] = f.default;
          } else if (f.type === 'integer' || f.type === 'number') {
            skeleton[f.name] = 0;
          } else if (f.type === 'boolean') {
            skeleton[f.name] = false;
          } else if (f.type && f.type.startsWith('array')) {
            skeleton[f.name] = [];
          } else {
            skeleton[f.name] = '';
          }
        }
        this.bodyJson = JSON.stringify(skeleton, null, 2);
      }

      // Path params — extract {name} tokens from template
      const matches = this.pathTemplate.match(/\{(\w+)\}/g) || [];
      this.pathParamNames = matches.map(m => m.slice(1, -1));
      for (const n of this.pathParamNames) this.pathParamValues[n] = '';
    },

    get resolvedPath() {
      let url = this.pathTemplate;
      for (const [k, v] of Object.entries(this.pathParamValues)) {
        url = url.replace(`{${k}}`, v ? encodeURIComponent(v) : `{${k}}`);
      }
      const qs = Object.entries(this.queryValues)
        .filter(([, v]) => v !== '')
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
      if (qs.length) url += '?' + qs.join('&');
      return url;
    },

    // Build a curl command that mirrors the current form state.
    get curlCommand() {
      const base = window.location.origin;
      let cmd = `curl -X ${this.method} '${base}${this.resolvedPath}'`;
      const hasBody = !['GET', 'HEAD'].includes(this.method) && this.bodyJson.trim();
      if (hasBody) {
        // Escape single quotes inside the JSON for a POSIX shell literal.
        const escaped = this.bodyJson.replace(/'/g, "'\\''");
        cmd += ` \\\n  -H 'Content-Type: application/json' \\\n  -d '${escaped}'`;
      }
      return cmd;
    },

    formatSize(bytes) {
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    },

    async send() {
      this.sending = true;
      this.response = null;
      const t0 = performance.now();
      try {
        const opts = { method: this.method, headers: {} };
        if (!['GET', 'HEAD', 'DELETE'].includes(this.method) && this.bodyJson.trim()) {
          opts.headers['Content-Type'] = 'application/json';
          opts.body = this.bodyJson;
        }
        const resp = await fetch(this.resolvedPath, opts);
        const elapsed = Math.round(performance.now() - t0);
        const text = await resp.text();

        // Collect all response headers.
        const headers = [];
        resp.headers.forEach((value, name) => headers.push({ name, value }));
        const contentType = resp.headers.get('content-type') || '';
        const size = new TextEncoder().encode(text).length;

        // Pretty-print JSON bodies; leave everything else as-is.
        let body = text;
        try { body = JSON.stringify(JSON.parse(text), null, 2); } catch { /* not JSON */ }

        this.response = { status: resp.status, ok: resp.ok, body, elapsed, size, contentType, headers, curl: this.curlCommand };
      } catch (err) {
        const elapsed = Math.round(performance.now() - t0);
        this.response = { status: 0, ok: false, body: String(err), elapsed, size: 0, contentType: '', headers: [], curl: this.curlCommand };
      } finally {
        this.sending = false;
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Worktrees page — worktree card (expand / collapse + HTMX lazy-load)
// ---------------------------------------------------------------------------

/**
 * Per-card state for the worktrees page.
 *
 * Handles:
 *   - toggle open/close with CSS transition
 *   - lazy HTMX detail load on first open (fires `detail-load` event once)
 *   - single-worktree delete via DELETE /api/worktrees/{slug}
 */
function worktreeCard(slug) {
  return {
    slug,
    open: false,
    loaded: false,

    toggle() {
      this.open = !this.open;
      if (this.open && !this.loaded) {
        this.loaded = true;
        const detail = document.getElementById('wt-detail-' + this.slug);
        if (detail) htmx.trigger(detail, 'detail-load');
      }
    },

    async deleteWorktree(s) {
      if (!confirm(`Remove worktree "${s}"?\n\nThis deletes the working directory. The branch is kept.`)) return;
      const resp = await fetch(`/api/worktrees/${s}`, { method: 'DELETE' });
      if (resp.ok) {
        // Animate the card out before reloading.
        this.$el.style.opacity = '0';
        this.$el.style.transform = 'translateY(-4px)';
        setTimeout(() => location.reload(), 300);
      } else {
        const data = await resp.json().catch(() => ({}));
        alert(`Failed to remove worktree: ${data.detail ?? resp.status}`);
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Worktrees page — branch manager (sweep + single-branch delete)
// ---------------------------------------------------------------------------

/**
 * Manages bulk-sweep and per-branch delete on the worktrees page.
 * Moved from the inline <script> in worktrees.html.
 */
function branchManager() {
  return {
    sweeping: false,

    async deleteBranch(name) {
      if (!confirm(`Delete branch "${name}"?`)) return;
      const resp = await fetch('/api/control/sweep', { method: 'POST' });
      if (resp.ok) location.reload();
      else alert(`Failed: ${(await resp.json().catch(() => ({}))).detail ?? resp.status}`);
    },

    async sweepAll() {
      if (!confirm('Delete all stale agent branches and clear orphan labels?')) return;
      this.sweeping = true;
      const resp = await fetch('/api/control/sweep', { method: 'POST' });
      this.sweeping = false;
      if (resp.ok) location.reload();
      else alert(`Sweep failed: ${(await resp.json().catch(() => ({}))).detail ?? resp.status}`);
    },
  };
}
