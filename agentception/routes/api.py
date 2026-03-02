"""JSON API routes for the AgentCeption dashboard.

These endpoints are consumed by HTMX fragments, external tools, and tests.
They are intentionally separate from the HTML UI routes so that callers
can choose their preferred serialisation format.
"""
from __future__ import annotations

from fastapi import APIRouter

from agentception.models import PipelineState
from agentception.poller import get_state

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/pipeline")
async def pipeline_api() -> PipelineState:
    """Return the current PipelineState snapshot as JSON.

    Returns an empty state (zero counts, empty agents list) before the first
    polling tick completes — callers should treat ``agents == []`` as loading,
    not as "no agents exist".
    """
    return get_state() or PipelineState.empty()
