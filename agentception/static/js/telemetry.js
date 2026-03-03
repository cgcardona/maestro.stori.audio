'use strict';

/**
 * telemetryDash()
 *
 * Minimal Alpine component that owns tab state for the telemetry D3 dashboard.
 * The actual D3 rendering lives in telemetry.js (loaded only on /telemetry).
 *
 * Tab IDs map to window.telemetry render functions:
 *   'Gantt' → window.telemetry.renderGantt
 *   'CostArea' → window.telemetry.renderCostArea
 *   etc.
 */
export function telemetryDash() {
  return {
    activeTab: 'Trend',

    switchTab(tab) {
      this.activeTab = tab;
      this.$nextTick(() => {
        if (window.telemetry && window.telemetry['render' + tab]) {
          window.telemetry['render' + tab]();
        }
      });
    },

    init() {
      this.$nextTick(() => {
        if (window.telemetry) {
          window.telemetry.renderTrend();
        }
      });
    },
  };
}
