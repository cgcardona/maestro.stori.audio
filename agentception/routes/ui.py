"""HTML route handlers for the AgentCeption dashboard UI.

All routes here render Jinja2 templates. Business data comes from the
background poller via ``get_state()`` — routes are intentionally thin.
"""
from __future__ import annotations

import asyncio
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
from agentception.intelligence.guards import PRViolation, detect_out_of_order_prs
from agentception.intelligence.scaling import ScalingRecommendation, compute_recommendation
from agentception.models import AgentNode, PipelineConfig, PipelineState, RoleMeta, VALID_ROLES
from agentception.poller import get_state, tick as _poller_tick
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


def _timestamp_to_date(ts: float) -> str:
    try:
        return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return "—"


_TEMPLATES.env.filters["timestamp_to_date"] = _timestamp_to_date

# Inject global template variables so every template can reference them without
# the route handler having to pass them explicitly.
from agentception.config import settings as _settings
_TEMPLATES.env.globals["gh_repo"] = _settings.gh_repo
_TEMPLATES.env.globals["gh_base_url"] = f"https://github.com/{_settings.gh_repo}"


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

    Server-renders everything knowable at request time so the page is
    fully painted before any client-side JavaScript runs.  Only the SSE
    stream (live updates) and user interactions (button clicks) require
    client-side code after initial render.

    Data sources:
    - ``state`` — in-memory PipelineState from the background poller.
    - ``board_issues`` — from ``ac_issues`` (Postgres) via poller tick.
    - ``scaling_advice`` — computed synchronously from wave history + config.
    - ``pr_violations`` — detected from open PRs via gh CLI.
    - ``poller_paused`` — sentinel file presence check (no network call).
    - Phase labels and pin — from ``pipeline-config.json`` + memory store.
    """
    # Fire an immediate tick in the background so the SSE stream delivers
    # fresh data within seconds of the page loading — eliminates up-to-5s
    # staleness on hard refresh without adding latency to the initial render.
    asyncio.get_event_loop().create_task(_poller_tick())

    state = get_state() or PipelineState.empty()
    all_phase_labels: list[str] = []
    label_is_pinned: bool = False

    try:
        pipeline_cfg = await read_pipeline_config()
        all_phase_labels = pipeline_cfg.active_labels_order
    except Exception as exc:
        logger.warning("⚠️ Could not read pipeline config: %s", exc)

    try:
        from agentception.readers.active_label_override import get_pin
        label_is_pinned = get_pin() is not None
    except Exception as exc:
        logger.warning("⚠️ Could not read active label pin: %s", exc)

    # Fetch these three concurrently — they're independent reads.
    scaling_advice: ScalingRecommendation | None = None
    pr_violations: list[PRViolation] = []
    from pathlib import Path as _Path
    poller_paused: bool = (_Path(_settings.repo_dir) / ".cursor" / ".pipeline-pause").exists()

    try:
        waves = await aggregate_waves()
        scaling_advice = await compute_recommendation(state, waves)
    except Exception as exc:
        logger.warning("⚠️ Could not compute scaling advice for SSR: %s", exc)

    try:
        pr_violations = await detect_out_of_order_prs()
    except Exception as exc:
        logger.warning("⚠️ Could not detect PR violations for SSR: %s", exc)

    board_issues = state.board_issues
    unclaimed = [i for i in board_issues if not i.claimed]

    return _TEMPLATES.TemplateResponse(
        request,
        "overview.html",
        {
            "state": state,
            "board_issues": [i.model_dump() for i in board_issues],
            "active_phase_label": state.active_label,
            "all_phase_labels": all_phase_labels,
            "label_is_pinned": label_is_pinned,
            "total_phase_issues": len(board_issues),
            "unclaimed_count": len(unclaimed),
            # Server-rendered to eliminate client-side fetch flicker.
            "scaling_advice": scaling_advice.model_dump() if scaling_advice else None,
            "pr_violations": [v.model_dump() for v in pr_violations],
            "poller_paused": poller_paused,
        },
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


@router.get("/agents", response_class=HTMLResponse)
async def agents_list(request: Request) -> HTMLResponse:
    """Agent listing page — live agents (in-memory) plus historical runs (Postgres).

    Live agents come from the in-memory poller state (real-time, filesystem
    backed).  Postgres ``ac_agent_runs`` provides the historical run list so
    completed agents are visible even after their worktrees are removed.
    """
    from agentception.db.queries import get_agent_run_history

    state = get_state() or PipelineState.empty()

    # Flatten root + children into one list for the listing view.
    all_agents: list[AgentNode] = []
    for agent in state.agents:
        all_agents.append(agent)
        all_agents.extend(agent.children)

    # Enrich with DB run history — recent completed runs, newest first.
    run_history: list[dict[str, object]] = []
    try:
        run_history = await get_agent_run_history(limit=50)
    except Exception as exc:
        logger.debug("DB agent run history fetch skipped: %s", exc)

    return _TEMPLATES.TemplateResponse(
        request,
        "agents.html",
        {"agents": all_agents, "state": state, "run_history": run_history},
    )


@router.get("/controls", response_class=HTMLResponse)
async def controls_hub(request: Request) -> Response:
    """Controls hub — redirects to the spawn form (the primary control action)."""
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/control/spawn", status_code=302)


@router.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_detail(request: Request, agent_id: str) -> Response:
    """Agent detail page — transcript viewer and .agent-task fields.

    Data sources (in priority order):
    1. In-memory state — live status, branch, issue number from the poller.
    2. Filesystem transcript — Cursor JSONL file for the full message log.
    3. Postgres ``ac_agent_runs`` — historical run metadata and status.
    4. Postgres ``ac_agent_messages`` — stored messages when no transcript
       file is accessible (e.g. after the worktree is removed).

    Returns HTTP 404 only when the agent is absent from both in-memory state
    and the Postgres history.
    """
    from agentception.db.queries import get_agent_run_detail

    state = get_state()
    node = _find_agent(state, agent_id)

    # Try DB run detail as a fallback enrichment (or if node is not in memory).
    db_run: dict[str, object] | None = None
    db_messages: list[dict[str, object]] = []
    try:
        db_run = await get_agent_run_detail(agent_id)
        if db_run:
            db_messages = db_run.get("messages", [])  # type: ignore[assignment]
    except Exception as exc:
        logger.debug("DB agent run lookup skipped: %s", exc)

    if node is None and db_run is None:
        return _TEMPLATES.TemplateResponse(
            request,
            "agent.html",
            {"node": None, "agent_id": agent_id, "messages": [], "db_run": None},
            status_code=404,
        )

    # Filesystem transcript takes priority — it's the live Cursor session.
    messages: list[dict[str, str]] = []
    if node and node.transcript_path:
        messages = await read_transcript_messages(Path(node.transcript_path))

    # Fall back to DB messages when the filesystem transcript is absent or empty.
    if not messages and db_messages:
        messages = [
            {"role": str(m.get("role", "")), "content": str(m.get("content", ""))}
            for m in db_messages
        ]

    return _TEMPLATES.TemplateResponse(
        request,
        "agent.html",
        {
            "node": node,
            "agent_id": agent_id,
            "messages": messages,
            "db_run": db_run,
        },
    )


@router.get("/telemetry", response_class=HTMLResponse)
async def telemetry_page(request: Request) -> HTMLResponse:
    """Telemetry dashboard — wave history (filesystem) + pipeline trend (Postgres).

    Two data sources:
    - ``aggregate_waves()`` — reads ``.agent-task`` files grouped by BATCH_ID
      into WaveSummary objects for the history table / CSS bar chart.
    - ``get_pipeline_trend()`` — reads ``ac_pipeline_snapshots`` from Postgres
      for the time-series chart (issues open, agents active over time).

    Both sources degrade gracefully to empty lists on failure.
    """
    from agentception.db.queries import get_pipeline_trend

    waves, trend = await asyncio.gather(
        aggregate_waves(),
        get_pipeline_trend(hours=24, limit=500),
    )

    # Bar chart widths are percentages of the longest wave duration.
    max_duration_s: float = 0.0
    for wave in waves:
        if wave.ended_at is not None:
            max_duration_s = max(max_duration_s, wave.ended_at - wave.started_at)

    all_issues: set[int] = set()
    for wave in waves:
        all_issues.update(wave.issues_worked)
    total_issues = len(all_issues)
    total_cost_usd = round(sum(w.estimated_cost_usd for w in waves), 4)
    total_agents = sum(len(w.agents) for w in waves)

    # Derive per-snapshot agent counts from trend for sparklines.
    # Normalise to simple primitives so Jinja2 tojson stays quote-safe.
    trend_labels: list[str] = [t["polled_at"][-8:-3] for t in trend]  # HH:MM
    trend_issues: list[int] = [int(t["issues_open"]) for t in trend]
    trend_prs: list[int] = [int(t["prs_open"]) for t in trend]
    trend_agents: list[int] = [int(t["agents_active"]) for t in trend]

    return _TEMPLATES.TemplateResponse(
        request,
        "telemetry.html",
        {
            "waves": waves,
            "max_duration_s": max_duration_s,
            "total_issues": total_issues,
            "total_cost_usd": total_cost_usd,
            "total_agents": total_agents,
            # Postgres trend (for sparklines / time-series chart).
            "trend_labels": trend_labels,
            "trend_issues": trend_issues,
            "trend_prs": trend_prs,
            "trend_agents": trend_agents,
            "trend_count": len(trend),
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
    phase_labels: list[str] = []
    try:
        pipeline_cfg = await read_pipeline_config()
        phase_labels = pipeline_cfg.active_labels_order
    except Exception:
        pass
    return _TEMPLATES.TemplateResponse(
        request,
        "dag.html",
        {"dag": dag.model_dump(), "phase_labels": phase_labels},
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

    Reads unclaimed open issues from ``ac_issues`` (Postgres) so the picker
    stays fast and consistent with the board sidebar.  Falls back to an empty
    list with an error banner when the DB is unavailable.
    """
    from agentception.db.queries import get_board_issues as _get_board_issues
    from agentception.config import settings as _cfg

    error: str | None = None
    issues: list[dict[str, object]] = []
    try:
        issues = await _get_board_issues(repo=_cfg.gh_repo, include_claimed=False)
    except Exception as exc:  # pragma: no cover — DB failure path
        error = f"Could not load issues: {exc}"

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


# ---------------------------------------------------------------------------
# Issues list + detail
# ---------------------------------------------------------------------------


@router.get("/issues", response_class=HTMLResponse)
async def issues_list(
    request: Request,
    state: str | None = None,
) -> HTMLResponse:
    """List all synced issues from the DB, filterable by state."""
    from agentception.db.queries import get_all_issues

    issues = await get_all_issues(repo=_settings.gh_repo, state=state)
    return _TEMPLATES.TemplateResponse(
        request,
        "issues_list.html",
        {"issues": issues, "state": state},
    )


@router.get("/issues/{number}", response_class=HTMLResponse)
async def issue_detail(request: Request, number: int) -> HTMLResponse:
    """Issue detail page — body, linked PRs, agent runs, and comments."""
    from agentception.db.queries import get_issue_detail

    issue = await get_issue_detail(repo=_settings.gh_repo, number=number)
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Issue #{number} not found in DB")
    return _TEMPLATES.TemplateResponse(request, "issue.html", {"issue": issue})


# ---------------------------------------------------------------------------
# Pull requests list + detail
# ---------------------------------------------------------------------------


@router.get("/prs", response_class=HTMLResponse)
async def prs_list(
    request: Request,
    state: str | None = None,
) -> HTMLResponse:
    """List all synced pull requests from the DB, filterable by state."""
    from agentception.db.queries import get_all_prs

    prs = await get_all_prs(repo=_settings.gh_repo, state=state)
    return _TEMPLATES.TemplateResponse(
        request,
        "prs_list.html",
        {"prs": prs, "state": state},
    )


@router.get("/prs/{number}", response_class=HTMLResponse)
async def pr_detail(request: Request, number: int) -> HTMLResponse:
    """PR detail page — CI checks, reviews, agent runs."""
    from agentception.db.queries import get_pr_detail

    pr = await get_pr_detail(repo=_settings.gh_repo, number=number)
    if pr is None:
        raise HTTPException(status_code=404, detail=f"PR #{number} not found in DB")
    return _TEMPLATES.TemplateResponse(request, "pr.html", {"pr": pr})


# ---------------------------------------------------------------------------
# Transcript browser
# ---------------------------------------------------------------------------


@router.get("/transcripts", response_class=HTMLResponse)
async def transcripts_browser(request: Request) -> HTMLResponse:
    """Browse all agent transcripts indexed from the Cursor filesystem."""
    from agentception.readers.transcripts import find_transcript_root, index_transcripts

    error: str | None = None
    transcripts: list[dict[str, object]] = []
    transcripts_dir_str: str = ""

    try:
        tr_root = await find_transcript_root()
        if tr_root is not None:
            transcripts_dir_str = str(tr_root)
            transcripts = await index_transcripts(tr_root)
        else:
            error = "Transcript directory not found — check CURSOR_PROJECTS_DIR setting."
    except Exception as exc:
        error = str(exc)

    return _TEMPLATES.TemplateResponse(
        request,
        "transcripts.html",
        {
            "transcripts": transcripts,
            "transcripts_dir": transcripts_dir_str,
            "error": error,
        },
    )


# ---------------------------------------------------------------------------
# Worktrees & git browser
# ---------------------------------------------------------------------------


@router.get("/worktrees", response_class=HTMLResponse)
async def worktrees_page(request: Request) -> HTMLResponse:
    """Live view of git worktrees, local branches, and stash."""
    from agentception.readers.git import list_git_branches, list_git_stash, list_git_worktrees

    worktrees: list[dict[str, object]] = []
    branches: list[dict[str, object]] = []
    stash: list[dict[str, object]] = []

    try:
        worktrees, branches, stash = await asyncio.gather(
            list_git_worktrees(),
            list_git_branches(),
            list_git_stash(),
        )
    except Exception as exc:
        logger.warning("⚠️  Worktrees page git read failed: %s", exc)

    return _TEMPLATES.TemplateResponse(
        request,
        "worktrees.html",
        {"worktrees": worktrees, "branches": branches, "stash": stash},
    )


# ---------------------------------------------------------------------------
# .cursor/ docs viewer
# ---------------------------------------------------------------------------

_CURSOR_DOCS: list[dict[str, str]] = [
    {"slug": "agent-command-policy",       "label": "Agent Command Policy",    "file": "agent-command-policy.md"},
    {"slug": "conflict-rules",             "label": "Conflict Rules",           "file": "conflict-rules.md"},
    {"slug": "multi-tier-agent-architecture", "label": "Multi-tier Architecture", "file": "multi-tier-agent-architecture.md"},
    {"slug": "parallel-issue-to-pr",       "label": "Parallel Issue → PR",     "file": "parallel-issue-to-pr.md"},
    {"slug": "parallel-pr-review",         "label": "Parallel PR Review",      "file": "parallel-pr-review.md"},
    {"slug": "pipeline-howto",             "label": "Pipeline How-to",         "file": "pipeline-howto.md"},
    {"slug": "parallel-bugs-to-issues",    "label": "Parallel Bugs → Issues",  "file": "parallel-bugs-to-issues.md"},
]

_CURSOR_DIR = Path(_settings.repo_dir) / ".cursor"


@router.get("/docs", response_class=HTMLResponse)
async def docs_index(request: Request) -> HTMLResponse:
    """Redirect to the first available doc."""
    first = next((d for d in _CURSOR_DOCS if (_CURSOR_DIR / d["file"]).exists()), None)
    if first:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/docs/{first['slug']}", status_code=302)  # type: ignore[return-value]
    raise HTTPException(status_code=404, detail="No .cursor/ docs found")


@router.get("/docs/{slug}", response_class=HTMLResponse)
async def docs_viewer(request: Request, slug: str) -> HTMLResponse:
    """Render a .cursor/ markdown doc as plain text with syntax highlighting."""
    doc_meta = next((d for d in _CURSOR_DOCS if d["slug"] == slug), None)
    if doc_meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown doc slug: {slug}")

    file_path = _CURSOR_DIR / doc_meta["file"]
    content: str | None = None
    error: str | None = None

    try:
        content = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        error = f"File not found: {file_path}"
    except OSError as exc:
        error = str(exc)

    return _TEMPLATES.TemplateResponse(
        request,
        "docs.html",
        {
            "slug": slug,
            "file_path": str(file_path),
            "content": content,
            "error": error,
            "available_docs": [
                {"slug": d["slug"], "label": d["label"]}
                for d in _CURSOR_DOCS
            ],
        },
    )
