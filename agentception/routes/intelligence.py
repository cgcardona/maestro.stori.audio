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

from agentception.readers.github import clear_wip_label

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])


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
