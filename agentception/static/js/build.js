/**
 * build.js — Mission Control Alpine component
 *
 * Manages:
 *  - activeIssue   — the issue card currently being inspected
 *  - events[]      — structured MCP events from /build/agent/{run_id}/stream
 *  - thoughts[]    — raw CoT messages from the same SSE stream
 *  - dispatch modal — role selection and POST /api/build/dispatch
 */

export function buildPage(roleGroups) {
  return {
    // ── inspector state ──────────────────────────────────────────────────
    activeIssue: null,
    events: [],
    thoughts: [],
    streamOpen: false,
    _evtSource: null,

    // ── dispatch modal state ─────────────────────────────────────────────
    dispatchOpen: false,
    dispatchIssue: null,
    dispatchRole: 'python-developer',
    roleGroups,
    dispatching: false,
    dispatchError: null,
    dispatchSuccess: false,
    dispatchResult: null,

    // ── repo (set by inline script in template) ──────────────────────────
    get repo() { return window._buildRepo ?? ''; },

    // ── lifecycle ────────────────────────────────────────────────────────

    onInspect(issue) {
      if (this.activeIssue?.number === issue.number) return;
      this._closeStream();
      this.activeIssue = issue;
      this.events = [];
      this.thoughts = [];
      if (issue.run) {
        this._openStream(issue.run.id);
      }
    },

    clearInspect() {
      this._closeStream();
      this.activeIssue = null;
      this.events = [];
      this.thoughts = [];
    },

    _openStream(runId) {
      this._closeStream();
      const src = new EventSource(`/build/agent/${encodeURIComponent(runId)}/stream`);
      this._evtSource = src;
      this.streamOpen = true;

      src.onmessage = (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch { return; }

        if (msg.t === 'ping') return;

        if (msg.t === 'event') {
          this.events.push({ ...msg, id: Date.now() + Math.random() });
        } else if (msg.t === 'thought') {
          // Accumulate into last entry if same role and rapid succession
          const last = this.thoughts[this.thoughts.length - 1];
          if (last && last.role === msg.role && this.thoughts.length > 0) {
            last.content += '\n' + msg.content;
          } else {
            this.thoughts.push(msg);
          }
          this._scrollCot();
        }
      };

      src.onerror = () => {
        this.streamOpen = false;
      };
    },

    _closeStream() {
      if (this._evtSource) {
        this._evtSource.close();
        this._evtSource = null;
      }
      this.streamOpen = false;
    },

    _scrollCot() {
      this.$nextTick(() => {
        const el = this.$refs.cotScroll;
        if (el) el.scrollTop = el.scrollHeight;
      });
    },

    // ── dispatch modal ───────────────────────────────────────────────────

    openDispatch(issue) {
      this.dispatchIssue = issue;
      this.dispatchRole = 'python-developer';
      this.dispatchError = null;
      this.dispatchSuccess = false;
      this.dispatchResult = null;
      this.dispatching = false;
      this.dispatchOpen = true;
    },

    async submitDispatch() {
      if (!this.dispatchIssue) return;
      this.dispatching = true;
      this.dispatchError = null;

      try {
        const res = await fetch('/api/build/dispatch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            issue_number: this.dispatchIssue.number,
            issue_title: this.dispatchIssue.title,
            role: this.dispatchRole,
            repo: this.repo,
          }),
        });

        const data = await res.json();

        if (!res.ok) {
          this.dispatchError = data.detail ?? `Error ${res.status}`;
        } else {
          this.dispatchResult = data;
          this.dispatchSuccess = true;
        }
      } catch (err) {
        this.dispatchError = `Network error: ${err.message}`;
      } finally {
        this.dispatching = false;
      }
    },

    // ── helpers ──────────────────────────────────────────────────────────

    fmtTime(iso) {
      if (!iso) return '';
      try {
        return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      } catch {
        return iso;
      }
    },

    eventIcon(eventType) {
      const icons = {
        step_start: '▶',
        blocker:    '🚧',
        decision:   '💡',
        done:       '✅',
      };
      return icons[eventType] ?? '•';
    },

    eventDetail(ev) {
      const p = ev.payload ?? {};
      switch (ev.event_type) {
        case 'step_start': return p.step ?? '';
        case 'blocker':    return p.description ?? '';
        case 'decision':   return `${p.decision ?? ''} — ${p.rationale ?? ''}`;
        case 'done':       return p.summary || p.pr_url || '';
        default:           return JSON.stringify(p);
      }
    },
  };
}
