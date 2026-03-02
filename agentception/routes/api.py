"""JSON API routes for the AgentCeption dashboard.

These endpoints are consumed by HTMX fragments, external tools, and tests.
They are intentionally separate from the HTML UI routes so that callers
can choose their preferred serialisation format.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from agentception.config import settings
from agentception.models import AgentNode, PipelineState
from agentception.poller import get_state
from agentception.readers.transcripts import read_transcript_messages
from agentception.routes.ui import _find_agent

router = APIRouter(prefix="/api", tags=["api"])

# Path to the sentinel file that pauses the agent pipeline.
# Writing this file tells CTO and Eng VP loops to wait rather than spawn agents.
_SENTINEL: Path = settings.repo_dir / ".cursor" / ".pipeline-pause"


@router.post("/control/pause", tags=["control"])
async def pause_pipeline() -> dict[str, bool]:
    """Create the pipeline-pause sentinel file, halting agent spawning.

    Idempotent — calling pause when already paused is a no-op.
    The CTO and Eng VP role files check for this sentinel at the top of
    every loop iteration and sleep instead of dispatching new agents.
    """
    _SENTINEL.touch()
    return {"paused": True}


@router.post("/control/resume", tags=["control"])
async def resume_pipeline() -> dict[str, bool]:
    """Remove the pipeline-pause sentinel file, allowing agent spawning to continue.

    Idempotent — calling resume when not paused is a no-op.
    """
    _SENTINEL.unlink(missing_ok=True)
    return {"paused": False}


@router.get("/control/status", tags=["control"])
async def control_status() -> dict[str, bool]:
    """Return the current pause state of the agent pipeline.

    Returns ``{"paused": true}`` when the sentinel file exists,
    ``{"paused": false}`` otherwise.
    """
    return {"paused": _SENTINEL.exists()}


@router.get("/pipeline")
async def pipeline_api() -> PipelineState:
    """Return the current PipelineState snapshot as JSON.

    Returns an empty state (zero counts, empty agents list) before the first
    polling tick completes — callers should treat ``agents == []`` as loading,
    not as "no agents exist".
    """
    return get_state() or PipelineState.empty()


@router.get("/agents")
async def agents_api() -> list[AgentNode]:
    """Return the flat list of root-level AgentNodes from the current pipeline state.

    Children are embedded inside each AgentNode's ``children`` field.
    Returns an empty list before the first polling tick completes.
    """
    state = get_state() or PipelineState.empty()
    return state.agents


@router.get("/agents/{agent_id}")
async def agent_api(agent_id: str) -> AgentNode:
    """Return a single AgentNode by ID from the current pipeline state.

    Searches root agents and their children (one level deep). Raises HTTP 404
    when the agent ID is not found in the current state.
    """
    state = get_state()
    node = _find_agent(state, agent_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return node


@router.get("/agents/{agent_id}/transcript")
async def transcript_api(agent_id: str) -> list[dict[str, str]]:
    """Return the parsed transcript messages for a given agent.

    Each element is ``{"role": "user"|"assistant", "text": "..."}``.
    Returns an empty list when the agent has no transcript file.
    Raises HTTP 404 when the agent ID is not found in the current state.
    """
    state = get_state()
    node = _find_agent(state, agent_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    if not node.transcript_path:
        return []
    return await read_transcript_messages(Path(node.transcript_path))
