'use strict';

/**
 * Powers the Brain Dump form — text input → phase preview → create issues → done.
 *
 * State machine steps:
 *   input      — user types their dump and clicks "Plan my issues"
 *   previewing — phase cards are shown; user confirms or goes back to edit
 *   loading    — POST /api/control/spawn-coordinator in flight
 *   done       — coordinator queued successfully
 *
 * Static data (loading messages) is read from data attributes on the root
 * element so they live in Python/Jinja, not in this file.
 * Seeds are rendered server-side by Jinja; each button passes its text via
 * $el.dataset.text so no seed array is needed here.
 *
 * HTMX refreshes the recent-runs sidebar after a successful submit:
 *   htmx.trigger('#bd-recent-runs', 'refresh')
 */
export function brainDump() {
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

    // Phase preview state (populated after POST /api/brain-dump/plan)
    phases: [],
    planError: '',

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

    /**
     * Step 1: call the Phase Planner and show a phase card preview.
     * Fires when the user clicks "Plan my issues →".
     */
    async submit() {
      const trimmed = this.text.trim();
      if (!trimmed) return;
      this.submitting = true;
      this.errorMsg = '';
      this.planError = '';
      this.funnelStage = 1;
      try {
        const resp = await fetch('/api/brain-dump/plan', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ dump: trimmed }),
        });
        if (!resp.ok) {
          const body = await resp.json().catch(() => ({}));
          throw new Error(body.detail || `HTTP ${resp.status}`);
        }
        const data = await resp.json();
        this.phases = data.phases || [];
        this.step = 'previewing';
        this.funnelStage = 2;
      } catch (err) {
        this.errorMsg = err.message;
        this.funnelStage = 0;
      } finally {
        this.submitting = false;
      }
    },

    /**
     * Step 2: user confirmed the plan — fire the coordinator.
     * Called from the "Looks good — Create Issues" button.
     */
    async confirmAndCreate() {
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
        this._stopLoadingAnimation(2);
        this.errorMsg = err.message;
        this.step = 'previewing';
      } finally {
        this.submitting = false;
      }
    },

    /**
     * Go back from the preview step to input, keeping the textarea content.
     */
    editDump() {
      this.step = 'input';
      this.funnelStage = 0;
      this.phases = [];
      this.planError = '';
      this.errorMsg = '';
      this.$nextTick(() => {
        if (this.$refs.textarea) this.autoGrow(this.$refs.textarea);
      });
    },

    reset() {
      this._stopLoadingAnimation(0);
      this.step = 'input';
      this.funnelStage = 0;
      this.text = '';
      this.labelPrefix = '';
      this.showOptions = false;
      this.errorMsg = '';
      this.planError = '';
      this.phases = [];
      this.result = {};
    },
  };
}
