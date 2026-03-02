"""HTML route handlers for the AgentCeption dashboard UI.

All routes here render Jinja2 templates. Business data comes from the
background poller via ``get_state()`` — routes are intentionally thin.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
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
from agentception.routes.roles import get_atoms, get_personas, get_taxonomy, list_roles
from agentception.telemetry import WaveSummary, aggregate_waves

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_HERE.parent / "templates"))


def _timestamp_to_date(ts: float) -> str:
    try:
        return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return "—"


_TEMPLATES.env.filters["timestamp_to_date"] = _timestamp_to_date


def _md_to_html(text: str) -> str:
    """Convert Markdown text to safe HTML for use in Jinja templates.

    Pre-processes docstring-style text to ensure bullet lists are detected:
    Python docstrings often place ``- item`` immediately after a sentence with
    only a single newline, but the Markdown spec requires a blank line before
    a bullet list.  We insert that blank line automatically.

    Enabled extensions:
    - ``fenced_code``  — triple-backtick code blocks
    - ``tables``       — GFM-style tables
    """
    import re
    import markdown as _md
    from markupsafe import Markup

    # Insert a blank line before bullet/numbered list items that follow a
    # non-blank line so the Markdown parser recognises them as a list block.
    text = re.sub(r"([^\n])\n([ \t]*(?:[-*+]|\d+\.) )", r"\1\n\n\2", text)

    result = _md.markdown(
        text,
        extensions=["fenced_code", "tables"],
        output_format="html",
    )
    return Markup(result)


_TEMPLATES.env.filters["markdown"] = _md_to_html

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


def _dirname(path: str) -> str:
    """Return the parent directory of a path string (equivalent to os.path.dirname)."""
    import os
    return os.path.dirname(path)


_TEMPLATES.env.filters["dirname"] = _dirname

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
    """Search the live agent tree for an AgentNode by its ID.

    ``agent_id`` is the worktree basename (e.g. ``issue-732``), which is the
    canonical ID assigned by the poller.  Searches root agents then children.
    Returns ``None`` when the state is empty or no match is found.
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

    Data enrichments served to the template:
    - ``stats``         — KPI counts (total, active, done, success_rate, avg_duration_s).
    - ``batches``       — run history grouped by batch_id, newest batch first.
    - ``all_roles``     — unique roles seen in history (for filter dropdown).
    - ``all_statuses``  — unique statuses seen (for filter chips).
    """
    import datetime
    from agentception.db.queries import get_agent_run_history
    from agentception.routes.roles import resolve_cognitive_arch

    state = get_state() or PipelineState.empty()

    # Flatten root + children into one list for the listing view.
    all_agents: list[AgentNode] = []
    for agent in state.agents:
        all_agents.append(agent)
        all_agents.extend(agent.children)

    # Enrich each live agent with persona data for the card display.
    agents_enriched: list[dict[str, object]] = []
    for ag in all_agents:
        persona = resolve_cognitive_arch(ag.cognitive_arch)
        agents_enriched.append({
            "node": ag,
            "persona": persona,
        })

    # Fetch full history from Postgres.
    run_history: list[dict[str, object]] = []
    try:
        run_history = await get_agent_run_history(limit=200)
    except Exception as exc:
        logger.debug("DB agent run history fetch skipped: %s", exc)

    # ── Compute duration + enrich each history row ────────────────────────
    def _parse_iso(s: object) -> datetime.datetime | None:
        if not isinstance(s, str):
            return None
        try:
            return datetime.datetime.fromisoformat(s.rstrip("Z"))
        except ValueError:
            return None

    def _fmt_duration(seconds: float) -> str:
        if seconds < 60:
            return f"{int(seconds)}s"
        if seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"

    enriched_history: list[dict[str, object]] = []
    total_duration_s = 0.0
    completed_count = 0
    for run in run_history:
        spawned = _parse_iso(run.get("spawned_at"))
        completed = _parse_iso(run.get("completed_at")) or _parse_iso(run.get("last_activity_at"))
        duration_s: float | None = None
        duration_str = "—"
        if spawned and completed and completed > spawned:
            duration_s = (completed - spawned).total_seconds()
            duration_str = _fmt_duration(duration_s)
            if run.get("status") == "done":
                total_duration_s += duration_s
                completed_count += 1

        enriched_history.append({
            **run,  # type: ignore[arg-type]
            "duration_s": duration_s,
            "duration_str": duration_str,
            "spawned_fmt": str(run.get("spawned_at", ""))[:16].replace("T", " "),
            "completed_fmt": str(run.get("completed_at", ""))[:16].replace("T", " ") if run.get("completed_at") else "—",
        })

    # ── Group history by batch_id, newest batch first ─────────────────────
    batches: list[dict[str, object]] = []
    seen_batches: dict[str, dict[str, object]] = {}
    for run in enriched_history:
        bid = str(run.get("batch_id") or "ungrouped")
        if bid not in seen_batches:
            batch_entry: dict[str, object] = {
                "batch_id": bid,
                "runs": [],
                "spawned_at": run.get("spawned_at"),
                "spawned_fmt": run.get("spawned_fmt"),
            }
            seen_batches[bid] = batch_entry
            batches.append(batch_entry)
        batch_runs = seen_batches[bid]["runs"]
        assert isinstance(batch_runs, list)
        batch_runs.append(run)

    # ── Aggregate KPI stats ───────────────────────────────────────────────
    total = len(enriched_history)
    done_count = sum(1 for r in enriched_history if r.get("status") == "done")
    success_rate = round(done_count / total * 100) if total else 0
    avg_duration_str = _fmt_duration(total_duration_s / completed_count) if completed_count else "—"

    stats = {
        "total": total,
        "active": len(all_agents),
        "done": done_count,
        "success_rate": success_rate,
        "avg_duration": avg_duration_str,
    }

    all_roles = sorted({str(r.get("role") or "") for r in enriched_history if r.get("role")})
    all_statuses = sorted({str(r.get("status") or "") for r in enriched_history if r.get("status")})

    return _TEMPLATES.TemplateResponse(
        request,
        "agents.html",
        {
            "agents": agents_enriched,
            "state": state,
            "batches": batches,
            "stats": stats,
            "all_roles": all_roles,
            "all_statuses": all_statuses,
            "run_count": total,
        },
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

    # Agent has left the live poller state (worktree gone) but exists in the
    # DB.  Synthesise a lightweight AgentNode so the template renders its full
    # detail view without a separate code path.
    if node is None and db_run is not None:
        from agentception.models import AgentStatus as _AgentStatus
        raw_status = str(db_run.get("status", "unknown")).lower()
        try:
            synth_status = _AgentStatus(raw_status)
        except ValueError:
            synth_status = _AgentStatus.UNKNOWN
        node = AgentNode(
            id=str(db_run.get("id", agent_id)),
            role=str(db_run.get("role", "unknown")),
            status=synth_status,
            issue_number=db_run.get("issue_number"),  # type: ignore[arg-type]
            pr_number=db_run.get("pr_number"),  # type: ignore[arg-type]
            branch=db_run.get("branch"),  # type: ignore[arg-type]
            batch_id=db_run.get("batch_id"),  # type: ignore[arg-type]
            worktree_path=db_run.get("worktree_path"),  # type: ignore[arg-type]
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

    from agentception.routes.roles import resolve_cognitive_arch
    persona = resolve_cognitive_arch(node.cognitive_arch if node else None)

    return _TEMPLATES.TemplateResponse(
        request,
        "agent.html",
        {
            "node": node,
            "agent_id": agent_id,
            "messages": messages,
            "db_run": db_run,
            "persona": persona,
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
    """Cognitive Architecture Studio — server-side rendered org tree with HTMX role selection."""
    try:
        taxonomy = await get_taxonomy()
        personas_resp = await get_personas()
        atoms_resp = await get_atoms()
    except Exception as exc:
        return _TEMPLATES.TemplateResponse(
            request, "roles.html",
            {"taxonomy": None, "personas_by_id": {}, "atoms": [], "error": str(exc)},
        )

    personas_by_id = {p.id: p for p in personas_resp.personas}
    return _TEMPLATES.TemplateResponse(
        request, "roles.html",
        {
            "taxonomy": taxonomy,
            "personas_by_id": personas_by_id,
            "atoms": atoms_resp.atoms,
            "error": None,
        },
    )


@router.get("/roles/{slug}/detail", response_class=HTMLResponse)
async def role_detail_partial(request: Request, slug: str) -> HTMLResponse:
    """HTMX partial — rendered when a role is selected in the org tree.

    Returns the center panel: persona cards for this role + the composer form.
    The editor content is loaded separately via the Monaco init in app.js.
    """
    try:
        taxonomy = await get_taxonomy()
        personas_resp = await get_personas()
        atoms_resp = await get_atoms()
    except Exception as exc:
        return HTMLResponse(f'<p class="text-muted" style="padding:1rem">Error: {exc}</p>')

    # Find the role in the taxonomy
    selected_role = None
    for level in taxonomy.levels:
        for role in level.roles:
            if role.slug == slug:
                selected_role = role
                break

    if selected_role is None:
        return HTMLResponse('<p class="text-muted" style="padding:1rem">Role not found.</p>')

    # Filter personas compatible with this role
    compatible_personas = [
        p for p in personas_resp.personas
        if p.id in selected_role.compatible_figures
    ]

    # Collect all unique skill domains across the full taxonomy (used by composer)
    all_skill_domains: list[str] = []
    seen_skills: set[str] = set()
    for level in taxonomy.levels:
        for role in level.roles:
            for s in role.compatible_skill_domains:
                if s not in seen_skills:
                    seen_skills.add(s)
                    all_skill_domains.append(s)
    all_skill_domains.sort()

    # Serialize Pydantic models to plain dicts so Jinja2 tojson works correctly.
    # `personas_json` is embedded as JSON in the Alpine component for client-side
    # "Apply to Composer" logic; `personas` and `all_personas` are for Jinja2 loops.
    personas_json = [p.model_dump() for p in compatible_personas]

    return _TEMPLATES.TemplateResponse(
        request, "_role_detail.html",
        {
            "role": selected_role,
            "personas": compatible_personas,
            "personas_json": personas_json,
            "all_personas": personas_resp.personas,
            "atoms": atoms_resp.atoms,
            "skill_domains": all_skill_domains,
            "slug": slug,
        },
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


# ---------------------------------------------------------------------------
# Brain Dump page — static data (defined once, passed to Jinja)
# ---------------------------------------------------------------------------

_BD_FUNNEL_STAGES = [
    {"icon": "🧠", "label": "Dump",    "desc": "Your raw input"},
    {"icon": "📋", "label": "Analyze", "desc": "Classify items"},
    {"icon": "🗂️", "label": "Phase",   "desc": "Group by dependency"},
    {"icon": "🏷️", "label": "Label",   "desc": "Create GitHub labels"},
    {"icon": "📝", "label": "Issues",  "desc": "File structured tickets"},
    {"icon": "🤖", "label": "Agents",  "desc": "Dispatch to engineers"},
]

_BD_SEEDS = [
    {
        "label": "🐛 Bug triage",
        "text": (
            "- Login fails intermittently on mobile\n"
            "- Rate limiter not applied to /api/public\n"
            "- CSV export hangs for reports > 10k rows\n"
            "- Dark mode toggle state lost on refresh"
        ),
    },
    {
        "label": "🗓️ Sprint planning",
        "text": (
            "- Migrate auth to JWT with refresh tokens\n"
            "- Add pagination to the issues API\n"
            "- Write integration tests for the billing flow\n"
            "- Document the webhook contract"
        ),
    },
    {
        "label": "💡 Feature ideas",
        "text": (
            "- Let users star/pin their favourite agents\n"
            "- Add Slack notifications for PR merges\n"
            "- Dark mode across the entire dashboard\n"
            "- Export pipeline config as a shareable template"
        ),
    },
    {
        "label": "🏗️ Tech debt",
        "text": (
            "- Replace legacy jQuery with Alpine across all pages\n"
            "- Remove the deprecated v1 API endpoints\n"
            "- Add mypy strict mode to the agentception module\n"
            "- Consolidate duplicate GitHub fetch helpers"
        ),
    },
]

_BD_LOADING_MSGS: list[str] = [
    "Analyzing your dump…",
    "Planning phases…",
    "Setting up labels…",
    "Preparing issues…",
    "Dispatching coordinator…",
]


async def _build_recent_dumps() -> list[dict[str, str]]:
    """Scan the worktrees directory and return metadata for the 6 most recent brain-dump runs."""
    from agentception.config import settings as _cfg

    recent_dumps: list[dict[str, str]] = []
    worktrees_dir = _cfg.worktrees_dir
    try:
        if worktrees_dir.exists():
            candidates = sorted(
                (d for d in worktrees_dir.iterdir() if d.is_dir() and d.name.startswith("brain-dump-")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for d in candidates[:6]:
                label_prefix = ""
                preview = ""
                task_file = d / ".agent-task"
                if task_file.exists():
                    try:
                        content = task_file.read_text(encoding="utf-8")
                        for raw_line in content.splitlines():
                            if raw_line.startswith("LABEL_PREFIX="):
                                label_prefix = raw_line.split("=", 1)[1].strip()
                        if "BRAIN_DUMP:" in content:
                            dump_part = content.split("BRAIN_DUMP:", 1)[1].strip()
                            first = next((ln.strip() for ln in dump_part.splitlines() if ln.strip()), "")
                            preview = first[:90]
                    except OSError:
                        pass
                ts_raw = d.name[len("brain-dump-"):]
                try:
                    ts_fmt = f"{ts_raw[:4]}-{ts_raw[4:6]}-{ts_raw[6:8]} {ts_raw[9:11]}:{ts_raw[11:13]}"
                except Exception:
                    ts_fmt = ts_raw
                recent_dumps.append({"slug": d.name, "label_prefix": label_prefix, "preview": preview, "ts": ts_fmt})
    except OSError:
        pass
    return recent_dumps


@router.get("/brain-dump", response_class=HTMLResponse)
async def brain_dump_page(request: Request) -> HTMLResponse:
    """Brain Dump — convert free-form text into phased GitHub issues."""
    from agentception.config import settings as _cfg

    recent_dumps = await _build_recent_dumps()
    return _TEMPLATES.TemplateResponse(
        request,
        "brain_dump.html",
        {
            "recent_dumps": recent_dumps,
            "gh_repo": _cfg.gh_repo,
            "funnel_stages": _BD_FUNNEL_STAGES,
            "seeds": _BD_SEEDS,
            "loading_msgs": _BD_LOADING_MSGS,
        },
    )


@router.get("/brain-dump/recent-runs", response_class=HTMLResponse)
async def brain_dump_recent_runs(request: Request) -> HTMLResponse:
    """HTMX partial — returns the recent-runs sidebar section.

    Triggered by Alpine after a successful brain-dump submit so the sidebar
    updates without a full page reload.
    """
    from agentception.config import settings as _cfg

    recent_dumps = await _build_recent_dumps()
    return _TEMPLATES.TemplateResponse(
        request,
        "_bd_recent_runs.html",
        {"recent_dumps": recent_dumps, "gh_repo": _cfg.gh_repo},
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
# Cognitive architecture detail page
# ---------------------------------------------------------------------------


@router.get("/cognitive-arch/{arch_id}", response_class=HTMLResponse)
async def cognitive_arch_detail(request: Request, arch_id: str) -> HTMLResponse:
    """Cognitive architecture detail page — full visualisation of a figure or composed arch.

    ``arch_id`` is a URL-safe version of the COGNITIVE_ARCH string, with colons
    replaced by hyphens (e.g. ``steve_jobs-python-fastapi`` for
    ``steve_jobs:python:fastapi``).  The route normalises both forms so links
    from agent cards work regardless of encoding.
    """
    from agentception.routes.roles import resolve_cognitive_arch

    # Accept both colon-separated (raw) and hyphen-separated (URL-safe) forms.
    arch_str = arch_id.replace("-", ":") if ":" not in arch_id else arch_id
    # But figure IDs use underscores — only replace hyphens that are between parts.
    # Re-split on colons to normalise; if no colons, try underscore-safe split.
    persona = resolve_cognitive_arch(arch_str)

    return _TEMPLATES.TemplateResponse(
        request,
        "cognitive_arch.html",
        {
            "arch_id": arch_id,
            "arch_str": arch_str,
            "persona": persona,
        },
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
    """Browse all agent transcripts indexed from the Cursor filesystem.

    Query parameters:
    - ``role``   — filter to a specific inferred role string
    - ``status`` — "done" or "unknown"
    - ``issue``  — filter to transcripts mentioning a specific issue number
    - ``q``      — free-text search against the preview text (case-insensitive)
    """
    from agentception.readers.transcripts import find_transcript_root, index_transcripts

    error: str | None = None
    transcripts: list[dict[str, object]] = []
    transcripts_dir_str: str = ""

    filter_role: str = request.query_params.get("role", "").strip()
    filter_status: str = request.query_params.get("status", "").strip()
    filter_issue_raw: str = request.query_params.get("issue", "").strip()
    filter_q: str = request.query_params.get("q", "").strip().lower()
    filter_issue: int | None = int(filter_issue_raw) if filter_issue_raw.isdigit() else None

    try:
        tr_root = await find_transcript_root()
        if tr_root is not None:
            transcripts_dir_str = str(tr_root)
            all_transcripts = await index_transcripts(tr_root)

            # Server-side filter pass
            for t in all_transcripts:
                if filter_role and t.get("role") != filter_role:
                    continue
                if filter_status and t.get("status") != filter_status:
                    continue
                if filter_issue is not None:
                    li = t.get("linked_issues")
                    if not isinstance(li, list) or filter_issue not in li:
                        continue
                if filter_q:
                    preview = t.get("preview")
                    if not isinstance(preview, str) or filter_q not in preview.lower():
                        continue
                transcripts.append(t)
        else:
            error = "Transcript directory not found — check CURSOR_PROJECTS_DIR setting."
    except Exception as exc:
        error = str(exc)

    # Collect unique roles from the full unfiltered index for the filter UI
    # (re-use transcripts if no filters active, otherwise do a second pass cheaply)
    all_roles: list[str] = []
    seen_roles: set[str] = set()
    for t in transcripts:
        r = str(t.get("role") or "unknown")
        if r not in seen_roles:
            seen_roles.add(r)
            all_roles.append(r)

    return _TEMPLATES.TemplateResponse(
        request,
        "transcripts.html",
        {
            "transcripts": transcripts,
            "transcripts_dir": transcripts_dir_str,
            "error": error,
            "filter_role": filter_role,
            "filter_status": filter_status,
            "filter_issue": filter_issue,
            "filter_q": filter_q,
            "all_roles": sorted(all_roles),
            "total": len(transcripts),
        },
    )


@router.get("/transcripts/{uuid}", response_class=HTMLResponse)
async def transcript_detail(request: Request, uuid: str) -> HTMLResponse:
    """Full detail view for a single agent conversation."""
    from agentception.readers.transcripts import find_transcript_root, read_transcript_full

    error: str | None = None
    transcript: dict[str, object] | None = None

    try:
        tr_root = await find_transcript_root()
        if tr_root is not None:
            transcript = await read_transcript_full(uuid, tr_root)
            if transcript is None:
                error = f"Transcript {uuid!r} not found in {tr_root}"
        else:
            error = "Transcript directory not found — check CURSOR_PROJECTS_DIR setting."
    except Exception as exc:
        error = str(exc)

    return _TEMPLATES.TemplateResponse(
        request,
        "transcript_detail.html",
        {
            "transcript": transcript,
            "uuid": uuid,
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

    # Mark branches as stale: agent branch with no live worktree checked out.
    live_branches: set[str] = {
        str(wt.get("branch", ""))
        for wt in worktrees
        if wt.get("branch") and not wt.get("is_main")
    }
    for b in branches:
        b["is_stale"] = bool(b.get("is_agent_branch")) and str(b.get("name", "")) not in live_branches

    return _TEMPLATES.TemplateResponse(
        request,
        "worktrees.html",
        {"worktrees": worktrees, "branches": branches, "stash": stash},
    )


# ---------------------------------------------------------------------------
# .cursor/ docs viewer
# ---------------------------------------------------------------------------

_CURSOR_DIR = Path(_settings.repo_dir) / ".cursor"


def _scan_cursor_docs() -> list[dict[str, str]]:
    """Auto-discover all markdown files in .cursor/ sorted alphabetically.

    Returns a list of {slug, label, file} dicts. Label is derived from the
    filename by replacing hyphens and underscores with spaces and title-casing.
    """
    if not _CURSOR_DIR.exists():
        return []
    docs: list[dict[str, str]] = []
    for f in sorted(_CURSOR_DIR.glob("*.md")):
        slug = f.stem
        label = slug.replace("-", " ").replace("_", " ").title()
        docs.append({"slug": slug, "label": label, "file": f.name})
    return docs


def _render_doc(slug: str) -> tuple[str | None, str | None, str | None]:
    """Read and render a doc file.

    Returns (label, content_html, error). ``content_html`` is Markdown
    rendered to safe HTML; ``error`` is set on read failure.
    """
    docs = _scan_cursor_docs()
    doc_meta = next((d for d in docs if d["slug"] == slug), None)
    if doc_meta is None:
        return None, None, f"Unknown doc: {slug}"
    file_path = _CURSOR_DIR / doc_meta["file"]
    try:
        raw = file_path.read_text(encoding="utf-8")
        return doc_meta["label"], _md_to_html(raw), None
    except FileNotFoundError:
        return doc_meta["label"], None, f"File not found: {file_path}"
    except OSError as exc:
        return doc_meta["label"], None, str(exc)


@router.get("/docs", response_class=HTMLResponse)
async def docs_index(request: Request) -> HTMLResponse:
    """Redirect to the first available doc."""
    from fastapi.responses import RedirectResponse

    docs = _scan_cursor_docs()
    if docs:
        return RedirectResponse(url=f"/docs/{docs[0]['slug']}", status_code=302)  # type: ignore[return-value]
    raise HTTPException(status_code=404, detail="No .cursor/ docs found")


@router.get("/docs/{slug}", response_class=HTMLResponse)
async def docs_viewer(request: Request, slug: str) -> HTMLResponse:
    """Full page: sidebar + rendered Markdown content."""
    label, content_html, error = _render_doc(slug)
    if label is None:
        raise HTTPException(status_code=404, detail=f"Unknown doc slug: {slug}")
    return _TEMPLATES.TemplateResponse(
        request,
        "docs.html",
        {
            "slug": slug,
            "label": label,
            "content_html": content_html,
            "error": error,
            "available_docs": [
                {"slug": d["slug"], "label": d["label"]}
                for d in _scan_cursor_docs()
            ],
        },
    )


@router.get("/docs/{slug}/content", response_class=HTMLResponse)
async def docs_content_partial(request: Request, slug: str) -> HTMLResponse:
    """HTMX partial: just the main content panel (no sidebar, no chrome)."""
    label, content_html, error = _render_doc(slug)
    if label is None:
        raise HTTPException(status_code=404, detail=f"Unknown doc slug: {slug}")
    return _TEMPLATES.TemplateResponse(
        request,
        "_doc_content.html",
        {
            "slug": slug,
            "label": label,
            "content_html": content_html,
            "error": error,
        },
    )


# ---------------------------------------------------------------------------
# Native API Reference
# ---------------------------------------------------------------------------

#: Human-readable labels and display order for OpenAPI tags.
_API_TAG_META: dict[str, str] = {
    "ui":           "UI Pages",
    "api":          "REST API",
    "control":      "Control Plane",
    "intelligence": "Intelligence",
    "roles":        "Roles",
    "config":       "Configuration",
    "telemetry":    "Telemetry",
    "templates":    "Templates",
    "sse":          "Server-Sent Events",
    "health":       "Health",
}


def _resolve_ref(schema_root: dict[str, object], ref: str) -> dict[str, object]:
    """Walk a JSON Pointer like '#/components/schemas/Foo' and return the node."""
    parts = ref.lstrip("#/").split("/")
    node: object = schema_root
    for part in parts:
        if isinstance(node, dict):
            node = node.get(part, {})
        else:
            return {}
    return node if isinstance(node, dict) else {}


def _resolve_schema(
    schema_root: dict[str, object],
    schema: dict[str, object],
    depth: int = 0,
) -> dict[str, object]:
    """Recursively resolve $ref and allOf, capped to avoid circular loops."""
    if depth > 5:
        return schema
    if "$ref" in schema:
        resolved = _resolve_ref(schema_root, str(schema["$ref"]))
        return _resolve_schema(schema_root, resolved, depth + 1)
    if "allOf" in schema:
        merged: dict[str, object] = {}
        for sub in schema.get("allOf", []):
            if isinstance(sub, dict):
                merged.update(_resolve_schema(schema_root, sub, depth + 1))
        return merged
    return schema


def _schema_to_fields(
    schema_root: dict[str, object],
    schema: dict[str, object],
    depth: int = 0,
) -> list[dict[str, object]]:
    """Flatten a JSON Schema object into a list of field descriptors."""
    resolved = _resolve_schema(schema_root, schema, depth)
    props: dict[str, object] = {}
    if isinstance(resolved.get("properties"), dict):
        props = resolved["properties"]  # type: ignore[assignment]
    required_set: set[str] = set(resolved.get("required", []))  # type: ignore[arg-type]
    fields: list[dict[str, object]] = []
    for name, prop in props.items():
        if not isinstance(prop, dict):
            continue
        prop = _resolve_schema(schema_root, prop, depth + 1)
        if prop.get("type") == "array":
            items = prop.get("items", {})
            if isinstance(items, dict):
                items = _resolve_schema(schema_root, items, depth + 2)
                type_str: str = f"array[{items.get('type', 'object')}]"
            else:
                type_str = "array"
        elif "anyOf" in prop:
            parts_list = [
                _resolve_schema(schema_root, t, depth + 1).get("type", "")
                for t in prop.get("anyOf", [])
                if isinstance(t, dict)
            ]
            type_str = " | ".join(str(p) for p in parts_list if p and p != "null") or "any"
        else:
            type_str = str(prop.get("type", "any"))
        fields.append({
            "name": name,
            "type": type_str,
            "required": name in required_set,
            "description": str(prop.get("description", "")),
            "default": prop.get("default"),
        })
    return fields


def _build_api_groups(
    schema_root: dict[str, object],
) -> list[dict[str, object]]:
    """Group endpoints by their first tag and return ordered groups.

    Every endpoint dict carries the full set of fields Swagger UI exposes:
    deprecated, operationId, per-response content-type and schema fields.
    """
    paths: dict[str, object] = schema_root.get("paths", {})  # type: ignore[assignment]

    tag_order: list[str] = list(_API_TAG_META)
    buckets: dict[str, list[dict[str, object]]] = {t: [] for t in tag_order}

    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if not isinstance(op, dict):
                continue
            tags: list[str] = op.get("tags", ["other"])  # type: ignore[assignment]
            tag = tags[0] if tags else "other"
            if tag not in buckets:
                buckets[tag] = []

            # ── Request body ───────────────────────────────────────────────
            request_body: dict[str, object] | None = None
            rb = op.get("requestBody")
            if isinstance(rb, dict):
                rb_content = rb.get("content", {})
                if isinstance(rb_content, dict):
                    # Prefer JSON; fall back to first available content type.
                    rb_ct = "application/json" if "application/json" in rb_content else (
                        next(iter(rb_content), "")
                    )
                    rb_ct_data: dict[str, object] = rb_content.get(rb_ct, {})  # type: ignore[assignment]
                    raw_schema = rb_ct_data.get("schema", {}) if isinstance(rb_ct_data, dict) else {}
                    if isinstance(raw_schema, dict):
                        ref_name = str(raw_schema.get("$ref", "")).split("/")[-1]
                        request_body = {
                            "required": bool(rb.get("required", False)),
                            "schema_name": ref_name,
                            "content_type": rb_ct,
                            "fields": _schema_to_fields(schema_root, raw_schema),
                        }

            # ── Responses ──────────────────────────────────────────────────
            responses: list[dict[str, object]] = []
            for code, resp_data in (op.get("responses") or {}).items():
                if not isinstance(resp_data, dict):
                    continue
                resp_content = resp_data.get("content") or {}
                # Pick primary content type (JSON preferred, then html, then first)
                resp_ct = ""
                resp_schema_raw: dict[str, object] = {}
                if isinstance(resp_content, dict) and resp_content:
                    for ct_pref in ("application/json", "text/html", "text/plain"):
                        if ct_pref in resp_content:
                            resp_ct = ct_pref
                            ct_data = resp_content[ct_pref]
                            if isinstance(ct_data, dict):
                                s = ct_data.get("schema", {})
                                resp_schema_raw = s if isinstance(s, dict) else {}
                            break
                    if not resp_ct:
                        resp_ct = next(iter(resp_content))

                ref_name = str(resp_schema_raw.get("$ref", "")).split("/")[-1]
                # Expand schema fields for non-trivial schemas (skip error types)
                skip_expand = {"HTTPValidationError", "ValidationError", ""}
                resp_fields = (
                    _schema_to_fields(schema_root, resp_schema_raw)
                    if ref_name not in skip_expand
                    else []
                )
                responses.append({
                    "code": str(code),
                    "description": str(resp_data.get("description", "")),
                    "schema_name": ref_name,
                    "content_type": resp_ct,
                    "fields": resp_fields,
                })

            buckets[tag].append({
                "method": method.upper(),
                "path": str(path),
                "summary": str(op.get("summary", "")),
                "description": str(op.get("description", "")),
                "operation_id": str(op.get("operationId", "")),
                "deprecated": bool(op.get("deprecated", False)),
                "parameters": op.get("parameters", []),
                "request_body": request_body,
                "responses": responses,
            })

    ordered = [t for t in tag_order if buckets.get(t)]
    extra = [t for t in buckets if t not in tag_order and buckets[t]]
    return [
        {
            "tag": tag,
            "label": _API_TAG_META.get(tag, tag.replace("-", " ").title()),
            "endpoints": buckets[tag],
        }
        for tag in ordered + extra
    ]


def _build_schema_models(schema_root: dict[str, object]) -> list[dict[str, object]]:
    """Return a sorted list of all component schemas with their resolved fields.

    Excludes low-signal FastAPI-generated error types.
    """
    raw: dict[str, object] = (
        schema_root.get("components", {}).get("schemas", {})  # type: ignore[union-attr]
    )
    skip = {"HTTPValidationError", "ValidationError"}
    models: list[dict[str, object]] = []
    for name, s in raw.items():
        if name in skip or not isinstance(s, dict):
            continue
        resolved = _resolve_schema(schema_root, s)
        models.append({
            "name": name,
            "description": str(resolved.get("description", "")),
            "fields": _schema_to_fields(schema_root, resolved),
        })
    return sorted(models, key=lambda m: str(m["name"]))


@router.get("/api", response_class=HTMLResponse)
async def api_reference(request: Request) -> HTMLResponse:
    """Native API reference — renders the OpenAPI schema as a first-party branded page.

    Replaces FastAPI's built-in Swagger UI (which is disabled in app.py).
    The schema is pre-processed in Python ($refs resolved, endpoints grouped by
    tag, response schemas expanded, schema models collected) so the Jinja
    template receives clean structured data with no schema logic inside.
    """
    schema: dict[str, object] = request.app.openapi()
    info: dict[str, object] = schema.get("info", {})  # type: ignore[assignment]
    return _TEMPLATES.TemplateResponse(
        request,
        "api_reference.html",
        {
            "info": info,
            "groups": _build_api_groups(schema),
            "schema_models": _build_schema_models(schema),
        },
    )
