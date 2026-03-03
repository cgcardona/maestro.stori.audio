"""UI routes: Brain Dump page and recent-runs partial."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from starlette.requests import Request

from ._shared import _TEMPLATES

logger = logging.getLogger(__name__)

router = APIRouter()

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
