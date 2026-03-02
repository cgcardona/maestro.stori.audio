"""HTML route handlers for the AgentCeption dashboard UI.

All routes here render Jinja2 templates. Business data comes from the
background poller via ``get_state()`` — routes are intentionally thin.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import Response

from agentception.models import AgentNode, PipelineState, VALID_ROLES
from agentception.poller import get_state
from agentception.readers.github import get_open_issues
from agentception.readers.transcripts import read_transcript_messages

_HERE = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_HERE.parent / "templates"))

router = APIRouter(tags=["ui"])


def _issue_is_claimed(iss: dict[str, object]) -> bool:
    """Return True when an issue carries the ``agent:wip`` label.

    Handles both list-of-strings and list-of-label-objects shapes so the
    helper works correctly regardless of which GitHub reader format is used.
    """
    raw = iss.get("labels")
    if not isinstance(raw, list):
        return False
    for lbl in raw:
        if isinstance(lbl, str) and lbl == "agent:wip":
            return True
        if isinstance(lbl, dict):
            name = lbl.get("name")
            if name == "agent:wip":
                return True
    return False


def _find_agent(state: PipelineState | None, agent_id: str) -> AgentNode | None:
    """Search the agent tree for an AgentNode matching ``agent_id``.

    Searches root agents first, then their children (one level deep, matching
    the current tree depth supported by the poller). Returns ``None`` when the
    state is empty or the ID is not found.
    """
    if state is None:
        return None
    for agent in state.agents:
        if agent.id == agent_id:
            return agent
        for child in agent.children:
            if child.id == agent_id:
                return child
    return None


@router.get("/", response_class=HTMLResponse)
async def overview(request: Request) -> HTMLResponse:
    """Dashboard overview — live agent hierarchy tree and GitHub board sidebar.

    Renders on every request with the latest polled state. The page connects
    to ``GET /events`` via SSE to receive live updates without reloading.
    """
    state = get_state() or PipelineState.empty()
    return _TEMPLATES.TemplateResponse(request, "overview.html", {"state": state})


@router.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_detail(request: Request, agent_id: str) -> Response:
    """Agent detail page — transcript viewer and .agent-task fields.

    Renders the full conversation transcript (user/assistant messages),
    parsed .agent-task key/value table, and quick-action buttons.
    Returns HTTP 404 when the agent ID is not in the current pipeline state.
    """
    state = get_state()
    node = _find_agent(state, agent_id)
    if node is None:
        return _TEMPLATES.TemplateResponse(
            request,
            "agent.html",
            {"node": None, "agent_id": agent_id, "messages": []},
            status_code=404,
        )
    messages: list[dict[str, str]] = []
    if node.transcript_path:
        messages = await read_transcript_messages(Path(node.transcript_path))
    return _TEMPLATES.TemplateResponse(
        request,
        "agent.html",
        {"node": node, "agent_id": agent_id, "messages": messages},
    )


@router.get("/control/spawn", response_class=HTMLResponse)
async def spawn_form(request: Request) -> HTMLResponse:
    """Issue picker form for manually spawning a new engineer agent.

    Fetches all open, unclaimed issues (those without ``agent:wip``) from
    GitHub and renders a form that posts to ``POST /api/control/spawn``.
    On any GitHub read error the page renders with an empty issue list and
    an error banner — it never raises HTTP 500 so the controls page stays
    accessible even when GitHub is unreachable.
    """
    error: str | None = None
    issues: list[dict[str, object]] = []
    try:
        all_open = await get_open_issues()
        issues = [
            iss for iss in all_open
            if not _issue_is_claimed(iss)
        ]
    except Exception as exc:  # pragma: no cover — network failure path
        error = f"Could not load issues from GitHub: {exc}"

    return _TEMPLATES.TemplateResponse(
        request,
        "spawn.html",
        {
            "issues": issues,
            "roles": sorted(VALID_ROLES),
            "error": error,
        },
    )
