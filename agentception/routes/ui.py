"""HTML route handlers for the AgentCeption dashboard UI.

All routes here render Jinja2 templates. Business data comes from the
background poller via ``get_state()`` — routes are intentionally thin.
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import Response

from agentception.models import AgentNode, PipelineState, RoleMeta, VALID_ROLES
from agentception.poller import get_state
from agentception.readers.github import get_open_issues
from agentception.readers.transcripts import read_transcript_messages
from agentception.routes.roles import list_roles
from agentception.telemetry import WaveSummary, aggregate_waves

_HERE = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_HERE.parent / "templates"))
# Register path filters used by agent.html kill-endpoint modal.
_TEMPLATES.env.filters["basename"] = os.path.basename
_TEMPLATES.env.filters["dirname"] = os.path.dirname


def _format_ts(ts: float) -> str:
    """Format a UNIX timestamp as a short UTC datetime string for the telemetry table."""
    try:
        return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "—"


def _format_number(n: int) -> str:
    """Format an integer with thousands separators for readability."""
    return f"{n:,}"


_TEMPLATES.env.filters["format_ts"] = _format_ts
_TEMPLATES.env.filters["format_number"] = _format_number

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


@router.get("/telemetry", response_class=HTMLResponse)
async def telemetry_page(request: Request) -> HTMLResponse:
    """Telemetry dashboard — wave history as a CSS bar chart and summary table.

    Aggregates all ``.agent-task`` files grouped by BATCH_ID into WaveSummary
    objects. The chart is CSS-only (no JS charting library): bar widths are
    computed server-side as percentages of the longest wave duration.
    Returns an empty-wave view when no wave data is present.
    """
    waves: list[WaveSummary] = await aggregate_waves()

    # Compute bar widths as percentages of the longest wave duration so all
    # bars are proportional and the chart fills its container.
    max_duration_s: float = 0.0
    for wave in waves:
        if wave.ended_at is not None:
            max_duration_s = max(max_duration_s, wave.ended_at - wave.started_at)

    # Pre-compute summary totals in Python so the template stays logic-free.
    all_issues: set[int] = set()
    for wave in waves:
        all_issues.update(wave.issues_worked)
    total_issues = len(all_issues)
    total_cost_usd = round(sum(w.estimated_cost_usd for w in waves), 4)
    total_agents = sum(len(w.agents) for w in waves)

    return _TEMPLATES.TemplateResponse(
        request,
        "telemetry.html",
        {
            "waves": waves,
            "max_duration_s": max_duration_s,
            "total_issues": total_issues,
            "total_cost_usd": total_cost_usd,
            "total_agents": total_agents,
        },
    )


@router.get("/roles", response_class=HTMLResponse)
async def roles_page(request: Request) -> HTMLResponse:
    """Role Studio — Monaco editor for live editing of managed role and cursor files.

    Renders the Role Studio UI (AC-302): a two-panel layout with a file list
    on the left and a Monaco editor on the right. File content is loaded into
    the editor via ``GET /api/roles/{slug}`` when a file row is clicked.
    Save triggers ``PUT /api/roles/{slug}`` with the editor content.

    On any API read error the page renders with an empty roles list and a
    visible error banner — the editor chrome always mounts so Monaco can
    load and the UI stays accessible.
    """
    roles: list[RoleMeta] = []
    error: str | None = None
    try:
        roles = await list_roles()
    except Exception as exc:  # pragma: no cover — filesystem error path
        error = f"Could not load role file list: {exc}"

    return _TEMPLATES.TemplateResponse(
        request,
        "roles.html",
        {"roles": roles, "error": error},
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
