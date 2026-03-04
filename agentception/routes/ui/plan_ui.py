"""UI routes: Plan page, recent-runs partial, and plan preview endpoint.

Endpoints
---------
POST /api/plan/preview                   — phase preview (no GitHub resources created)
GET  /plan                               — full page
GET  /plan/recent-runs                   — HTMX partial (sidebar refresh)
GET  /api/plan/{run_id}/plan-text        — return original plan text for re-run
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.requests import Request

from agentception.models import PlanRequest, PlanResult
from agentception.readers.phase_planner import plan_phases
from agentception.readers.llm_phase_planner import plan_phases_llm
from ._shared import _TEMPLATES

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Plan page — static data (defined once, passed to Jinja)
# ---------------------------------------------------------------------------

_PLAN_FUNNEL_STAGES = [
    {"icon": "🧠", "label": "Plan",    "desc": "Your raw input"},
    {"icon": "📋", "label": "Analyze", "desc": "Classify items"},
    {"icon": "🗂️", "label": "Phase",   "desc": "Group by dependency"},
    {"icon": "🏷️", "label": "Label",   "desc": "Create GitHub labels"},
    {"icon": "📝", "label": "Issues",  "desc": "File structured tickets"},
    {"icon": "🤖", "label": "Agents",  "desc": "Dispatch to engineers"},
]

_PLAN_SEEDS = [
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

_PLAN_LOADING_MSGS: list[str] = [
    "Analyzing your plan…",
    "Planning phases…",
    "Setting up labels…",
    "Preparing issues…",
    "Dispatching coordinator…",
]


def _parse_task_fields(content: str) -> dict[str, str]:
    """Parse key=value lines from the structured header of a ``.agent-task`` file.

    Only processes lines before the first blank line or ``PLAN_DUMP:`` marker so
    that multi-line plan text is never misinterpreted as a key=value pair.
    """
    fields: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "PLAN_DUMP:":
            break
        if "=" in stripped:
            key, _, val = stripped.partition("=")
            fields[key.strip()] = val.strip()
    return fields


def _count_plan_items(plan_text: str) -> int:
    """Count non-empty lines in a PLAN_DUMP block as a proxy for item count."""
    return sum(1 for ln in plan_text.splitlines() if ln.strip())


async def _build_recent_plans() -> list[dict[str, str]]:
    """Scan the worktrees directory and return metadata for the 6 most recent plan runs.

    Each entry contains: slug, label_prefix, preview, ts, batch_id, item_count.
    ``item_count`` is a line-count heuristic over the PLAN_DUMP block (not a live
    GitHub issue count) so no network call is needed on the hot render path.
    """
    from agentception.config import settings as _cfg

    recent_plans: list[dict[str, str]] = []
    worktrees_dir = _cfg.worktrees_dir
    try:
        if worktrees_dir.exists():
            candidates = sorted(
                (d for d in worktrees_dir.iterdir() if d.is_dir() and d.name.startswith("plan-")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for d in candidates[:6]:
                label_prefix = ""
                preview = ""
                batch_id = d.name
                item_count = "—"
                task_file = d / ".agent-task"
                if task_file.exists():
                    try:
                        content = task_file.read_text(encoding="utf-8")
                        fields = _parse_task_fields(content)
                        label_prefix = fields.get("LABEL_PREFIX", "")
                        batch_id = fields.get("BATCH_ID", d.name)
                        if "PLAN_DUMP:" in content:
                            plan_part = content.split("PLAN_DUMP:", 1)[1].strip()
                            first = next((ln.strip() for ln in plan_part.splitlines() if ln.strip()), "")
                            preview = first[:90]
                            count = _count_plan_items(plan_part)
                            item_count = str(count) if count else "—"
                    except OSError:
                        pass
                ts_raw = d.name[len("plan-"):]
                try:
                    ts_fmt = f"{ts_raw[:4]}-{ts_raw[4:6]}-{ts_raw[6:8]} {ts_raw[9:11]}:{ts_raw[11:13]}"
                except Exception:
                    ts_fmt = ts_raw
                recent_plans.append({
                    "slug": d.name,
                    "label_prefix": label_prefix,
                    "preview": preview,
                    "ts": ts_fmt,
                    "batch_id": batch_id,
                    "item_count": item_count,
                })
    except OSError:
        pass
    return recent_plans


@router.post("/api/plan/preview", response_model=PlanResult)
async def plan_preview(body: PlanRequest) -> PlanResult:
    """Convert free-form plan text into sequenced phase cards.

    Tries the LLM path first (Claude via OpenRouter) when
    ``AC_OPENROUTER_API_KEY`` is configured.  Falls back to the keyword
    heuristic when the key is absent or the LLM call fails, so the page
    always works even without a key.

    Raises
    ------
    HTTP 422
        When ``dump`` is empty or contains no extractable work items.
    """
    from agentception.config import settings as _cfg

    if _cfg.openrouter_api_key:
        try:
            result = await plan_phases_llm(body.dump)
            logger.info(
                "✅ Phase plan (LLM): %d phases for %d chars",
                len(result.phases), len(body.dump),
            )
            return result
        except Exception as exc:
            logger.warning(
                "⚠️ LLM phase planner failed — falling back to heuristic: %s", exc
            )

    # Heuristic fallback — always available, no network required.
    try:
        result = plan_phases(body.dump)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info(
        "✅ Phase plan (heuristic): %d phases for %d chars",
        len(result.phases), len(body.dump),
    )
    return result


@router.get("/plan", response_class=HTMLResponse)
async def plan_page(request: Request) -> HTMLResponse:
    """Plan — convert free-form text into phased GitHub issues."""
    from agentception.config import settings as _cfg

    recent_plans = await _build_recent_plans()
    return _TEMPLATES.TemplateResponse(
        request,
        "plan.html",
        {
            "recent_plans": recent_plans,
            "gh_repo": _cfg.gh_repo,
            "funnel_stages": _PLAN_FUNNEL_STAGES,
            "seeds": _PLAN_SEEDS,
            "loading_msgs": _PLAN_LOADING_MSGS,
        },
    )


@router.get("/plan/recent-runs", response_class=HTMLResponse)
async def plan_recent_runs(request: Request) -> HTMLResponse:
    """HTMX partial — returns the recent-runs sidebar section.

    Triggered by Alpine after a successful plan submit so the sidebar
    updates without a full page reload.
    """
    from agentception.config import settings as _cfg

    recent_plans = await _build_recent_plans()
    return _TEMPLATES.TemplateResponse(
        request,
        "_plan_recent_runs.html",
        {"recent_plans": recent_plans, "gh_repo": _cfg.gh_repo},
    )


@router.get("/api/plan/{run_id}/plan-text")
async def plan_run_text(run_id: str) -> JSONResponse:
    """Return the original PLAN_DUMP text for a given run slug.

    Used by the "Re-run →" button in the sidebar: the JS handler fetches this,
    populates the main textarea, and switches Alpine to the ``input`` step so
    the user can edit and resubmit without copy-pasting.

    Parameters
    ----------
    run_id:
        The directory slug, e.g. ``plan-20260303-164033``.  Must start
        with ``plan-`` and must not contain path traversal characters.

    Raises
    ------
    HTTP 400
        When ``run_id`` contains illegal characters or does not start with
        ``plan-``.
    HTTP 404
        When the worktree directory or ``.agent-task`` file does not exist, or
        the file contains no ``PLAN_DUMP:`` section.
    """
    from agentception.config import settings as _cfg

    if not run_id.startswith("plan-") or "/" in run_id or ".." in run_id:
        raise HTTPException(status_code=400, detail="Invalid run_id format.")

    task_file = _cfg.worktrees_dir / run_id / ".agent-task"
    if not task_file.exists():
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    try:
        content = task_file.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("⚠️ Could not read .agent-task for run %s: %s", run_id, exc)
        raise HTTPException(status_code=404, detail="Could not read task file.") from exc

    if "PLAN_DUMP:" not in content:
        raise HTTPException(status_code=404, detail="No PLAN_DUMP section in task file.")

    plan_text = content.split("PLAN_DUMP:", 1)[1].strip()
    return JSONResponse({"plan_text": plan_text})
