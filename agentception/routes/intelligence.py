"""Intelligence-layer API routes for the AgentCeption dashboard.

Provides action endpoints for anomalies detected by
:mod:`agentception.intelligence.guards`.  The "clear stale claim" endpoint
is the primary consumer: the dashboard surfaces a "Clear Label" button for
each stale claim and POSTs here to remove the ``agent:wip`` label.

Why a dedicated router?
- Keeps destructive write operations (label removal) separate from read-only
  data routes so they can be rate-limited or gated independently.
- ``/api/intelligence/`` signals to callers that these endpoints act on
  machine-detected anomalies rather than direct user operations.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from agentception.intelligence.dag import DependencyDAG, build_dag
from agentception.intelligence.scaling import ScalingRecommendation, compute_recommendation
from agentception.poller import get_state
from agentception.models import PipelineState
from agentception.readers.github import clear_wip_label
from agentception.telemetry import aggregate_waves

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])


@router.get("/dag")
async def dag_api() -> DependencyDAG:
    """Return the full dependency DAG for all open issues.

    Fetches every open issue via the GitHub CLI, parses ``Depends on #N``
    declarations from each body, and returns a directed graph of dependencies.

    The response is consumed by the AC-402 DAG visualisation and by the
    intelligence layer's scheduling logic to prevent early assignment.

    Raises
    ------
    HTTP 500
        When the GitHub CLI subprocess fails (e.g. auth error, rate-limit).
    """
    try:
        return await build_dag()
    except RuntimeError as exc:
        logger.error("❌ Failed to build dependency DAG: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to build dependency DAG: {exc}",
        ) from exc


@router.post("/stale-claims/{issue_number}/clear")
async def clear_stale_claim(issue_number: int) -> dict[str, int]:
    """Remove the ``agent:wip`` label from a stale-claim issue.

    Intended to be called from the dashboard "Clear Label" button when the
    poller detects that an issue carries ``agent:wip`` but has no live worktree.
    After clearing the label the issue re-enters the scheduling pool and the
    next polling tick will stop reporting it as a stale claim.

    Parameters
    ----------
    issue_number:
        GitHub issue number whose ``agent:wip`` label should be removed.

    Returns
    -------
    dict
        ``{"cleared": issue_number}`` on success.

    Raises
    ------
    HTTP 500
        When the ``gh`` CLI command fails (e.g. auth error or rate-limit).
    """
    try:
        await clear_wip_label(issue_number)
    except RuntimeError as exc:
        logger.error("❌ Failed to clear agent:wip from issue #%d: %s", issue_number, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear agent:wip label from issue #{issue_number}: {exc}",
        ) from exc

    logger.info("✅ Cleared stale claim: removed agent:wip from issue #%d", issue_number)
    return {"cleared": issue_number}


@router.get("/scaling-advice", tags=["intelligence"])
async def scaling_advice_api() -> ScalingRecommendation:
    """Return a scaling recommendation based on current pipeline state and wave history.

    Evaluates queue depth (open issues), PR backlog (open PRs), and mean wave
    duration to produce an actionable recommendation.  Uses the current
    ``PipelineState`` snapshot from the poller and historical wave data from
    the telemetry layer — no GitHub API calls are made during this request.

    Returns
    -------
    ScalingRecommendation
        The recommended action (``increase_qa_vps``, ``increase_pool``, or
        ``no_change``) along with rationale and confidence level.

    Raises
    ------
    HTTP 500
        When wave aggregation or config reads fail unexpectedly.
    """
    try:
        state = get_state() or PipelineState.empty()
        waves = await aggregate_waves()
        return await compute_recommendation(state, waves)
    except Exception as exc:
        logger.error("❌ Failed to compute scaling recommendation: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to compute scaling recommendation: {exc}",
        ) from exc
