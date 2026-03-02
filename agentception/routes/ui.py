"""HTML route handlers for the AgentCeption dashboard UI.

All routes here render Jinja2 templates. Business data comes from the
background poller via ``get_state()`` — routes are intentionally thin.
"""
from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import Response

from agentception.intelligence.ab_results import ABVariantResult, compute_ab_results
from agentception.intelligence.analyzer import IssueAnalysis, analyze_issue
from agentception.intelligence.dag import DependencyDAG, build_dag
from agentception.models import AgentNode, PipelineConfig, PipelineState, RoleMeta, VALID_ROLES
from agentception.poller import get_state
from agentception.readers.github import get_open_issues
from agentception.readers.pipeline_config import read_pipeline_config
from agentception.readers.transcripts import read_transcript_messages
from agentception.routes.roles import list_roles
from agentception.telemetry import WaveSummary, aggregate_waves

logger = logging.getLogger(__name__)

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
    Open unclaimed issues are fetched from GitHub and displayed in the board
    sidebar with an "Analyze" button each.
    """
    state = get_state() or PipelineState.empty()
    board_issues: list[dict[str, object]] = []
    try:
        all_open = await get_open_issues()
        board_issues = [iss for iss in all_open if not _issue_is_claimed(iss)]
    except Exception as exc:  # pragma: no cover — network failure path
        logger.warning("⚠️ Could not fetch open issues for board sidebar: %s", exc)
    return _TEMPLATES.TemplateResponse(
        request,
        "overview.html",
        {"state": state, "board_issues": board_issues},
    )


@router.post("/api/analyze/issue/{number}/partial", response_class=HTMLResponse)
async def analyze_partial(request: Request, number: int) -> HTMLResponse:
    """Return an HTMX partial with analysis results for a single GitHub issue.

    Calls :func:`~agentception.intelligence.analyzer.analyze_issue` with the
    given issue number, then renders ``partials/analysis.html`` with the
    :class:`~agentception.intelligence.analyzer.IssueAnalysis` result.

    Intended to be called by the "Analyze" button on each issue card in the
    GitHub board sidebar via ``hx-post`` / ``hx-swap="innerHTML"``.

    Parameters
    ----------
    number:
        GitHub issue number to analyse.

    Raises
    ------
    HTTP 404
        When the GitHub CLI cannot find the issue.
    HTTP 500
        When the ``gh`` subprocess fails for any other reason.
    """
    try:
        analysis: IssueAnalysis = await analyze_issue(number)
    except RuntimeError as exc:
        detail = str(exc)
        status = 404 if "not found" in detail.lower() else 500
        raise HTTPException(status_code=status, detail=detail) from exc
    logger.info("✅ Analysis complete for issue #%d: %s", number, analysis.parallelism)
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/analysis.html",
        {"a": analysis},
    )


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


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request) -> HTMLResponse:
    """Pipeline configuration panel — sliders for VP count and pool size.

    Renders the pipeline config UI (AC-305): allocation sliders for max_eng_vps,
    max_qa_vps, pool_size_per_vp, and a drag-and-drop label order editor.
    The page loads current values from ``GET /api/config`` on mount via Alpine.js
    and persists changes via ``PUT /api/config`` on save.

    Pre-populates the ``config`` template variable from the config file so the
    initial render reflects current values even before Alpine.js hydrates.
    On any read error the page still renders with hardcoded defaults — the save
    button is always accessible.
    """
    config: PipelineConfig | None = None
    try:
        config = await read_pipeline_config()
    except Exception:  # pragma: no cover — filesystem error path
        pass
    return _TEMPLATES.TemplateResponse(
        request,
        "config.html",
        {"config": config},
    )


@router.get("/dag", response_class=HTMLResponse)
async def dag_page(request: Request) -> HTMLResponse:
    """Dependency DAG visualisation — D3.js force-directed graph of issue dependencies.

    Fetches all open issues, parses their dependency declarations, and renders
    an interactive SVG graph using D3.js (loaded from CDN).  Nodes are coloured
    by ``agentception/*`` phase label; the ``agent:wip`` issues are highlighted
    with a green stroke; closed nodes are rendered at 50% opacity.

    Callers who need the raw DAG data should use ``GET /api/dag`` instead.
    """
    dag: DependencyDAG = await build_dag()
    return _TEMPLATES.TemplateResponse(
        request,
        "dag.html",
        {"dag": dag.model_dump()},
    )


@router.get("/ab-testing", response_class=HTMLResponse)
async def ab_testing_page(request: Request) -> HTMLResponse:
    """A/B role variant comparison dashboard — side-by-side outcome metrics.

    Renders the A/B results page (AC-505): two comparison cards showing
    PRs opened, merge rate, average reviewer grade, and batch count for each
    role variant.  A winner badge is shown when one variant's merge rate or
    average grade clearly outperforms the other.

    Data comes from :func:`~agentception.intelligence.ab_results.compute_ab_results`.
    On any computation error the page renders with zero-value results and an
    error banner rather than returning HTTP 500 so the UI stays accessible.
    """
    error: str | None = None
    variant_a: ABVariantResult | None = None
    variant_b: ABVariantResult | None = None
    try:
        variant_a, variant_b = await compute_ab_results()
    except Exception as exc:  # pragma: no cover — infrastructure error path
        error = f"Could not compute A/B results: {exc}"
        logger.warning("⚠️  A/B results computation failed: %s", exc)

    # Determine winner based on merge rate; fall back to no winner on a tie.
    winner: str | None = None
    if variant_a is not None and variant_b is not None:
        if variant_a.merge_rate > variant_b.merge_rate:
            winner = "A"
        elif variant_b.merge_rate > variant_a.merge_rate:
            winner = "B"

    return _TEMPLATES.TemplateResponse(
        request,
        "ab_testing.html",
        {
            "variant_a": variant_a,
            "variant_b": variant_b,
            "winner": winner,
            "error": error,
        },
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


@router.get("/templates", response_class=HTMLResponse)
async def templates_ui(request: Request) -> HTMLResponse:
    """Template marketplace — export and import pipeline configuration bundles.

    Renders the templates management page which lets the user:
    - Export the current pipeline config as a versioned ``.tar.gz``.
    - Import a template archive into any target repo.
    - Browse previously exported templates.
    """
    from agentception.readers.templates import list_stored_templates

    stored = list_stored_templates()
    return _TEMPLATES.TemplateResponse(
        request,
        "templates.html",
        {"stored_templates": stored},
    )
