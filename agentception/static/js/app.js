/**
 * AgentCeption — Alpine.js component library entry point
 *
 * All Alpine component factory functions live in domain-specific ES modules.
 * This entry point imports them all and assigns them to window so that
 * templates can reference them via x-data="functionName(args)" without any
 * changes to the HTML.
 *
 * Jinja2 data is injected at the call-site in the template attribute, never
 * inside this file.  This keeps the compiled output static, cacheable, and
 * free of server-side template rendering bugs caused by mismatched quote styles.
 *
 * Module index:
 *   nav.js          — projectSwitcher
 *   overview.js     — pipelineDashboard, agentCard, phaseSwitcher,
 *                     pipelineControl, sweepControl, waveControl,
 *                     scalingAdvisor, prViolations, staleClaimCard, issueCard
 *   agents.js       — agentsPage, missionControl
 *   telemetry.js    — telemetryDash
 *   dag.js          — dagVisualization
 *   config.js       — configPanel
 *   roles.js        — roleDetail, rolesEditor
 *   brain_dump.js   — brainDump
 *   transcripts.js  — transcriptBrowser, transcriptDetail
 *   templates.js    — exportPanel, importPanel, envSandbox
 *   api.js          — apiEndpoint
 */

'use strict';

import { projectSwitcher } from './nav.js';
import {
  pipelineDashboard, agentCard, phaseSwitcher, pipelineControl,
  sweepControl, waveControl, scalingAdvisor, prViolations,
  staleClaimCard, issueCard,
} from './overview.js';
import { agentsPage, missionControl } from './agents.js';
import { telemetryDash } from './telemetry.js';
import { dagVisualization } from './dag.js';
import { configPanel } from './config.js';
import { roleDetail, rolesEditor } from './roles.js';
import { brainDump } from './brain_dump.js';
import { transcriptBrowser, transcriptDetail } from './transcripts.js';
import { exportPanel, importPanel, envSandbox } from './templates.js';
import { apiEndpoint } from './api.js';

// Expose all Alpine component factory functions globally so templates can
// reference them via x-data="functionName()" without any changes.
Object.assign(window, {
  projectSwitcher,
  pipelineDashboard, agentCard, phaseSwitcher, pipelineControl,
  sweepControl, waveControl, scalingAdvisor, prViolations,
  staleClaimCard, issueCard,
  agentsPage, missionControl,
  telemetryDash,
  dagVisualization,
  configPanel,
  roleDetail, rolesEditor,
  brainDump,
  transcriptBrowser, transcriptDetail,
  exportPanel, importPanel, envSandbox,
  apiEndpoint,
});
