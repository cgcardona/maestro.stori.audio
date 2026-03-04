"""Build phase API routes.

These endpoints serve two audiences:

1. **The Build UI** — ``POST /api/build/dispatch`` creates a worktree and
   ``.agent-task`` file so a human can point Cursor at a specific GitHub issue.

2. **Running agents** — the four ``POST /api/build/report/*`` endpoints let
   agents push structured lifecycle events back to AgentCeption from inside
   their Cursor session.  Agents find the URL via ``AC_URL`` in their
   ``.agent-task`` file.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agentception.config import settings
from agentception.db.persist import persist_agent_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/build", tags=["build"])

# ---------------------------------------------------------------------------
# Dispatch — create a worktree + .agent-task for one issue
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class DispatchRequest(BaseModel):
    """Request body for ``POST /api/build/dispatch``."""

    issue_number: int
    issue_title: str
    role: str
    """Role slug from ``agentception/.cursor/roles/`` (e.g. ``python-developer``)."""
    repo: str
    """``owner/repo`` string (e.g. ``tellurstori/maestro``)."""


class DispatchResponse(BaseModel):
    """Successful dispatch response."""

    run_id: str
    worktree: str
    branch: str
    agent_task_path: str
    batch_id: str


def _make_batch_id(issue_number: int) -> str:
    """Generate a deterministic-but-unique batch id for this dispatch."""
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:4]
    return f"issue-{issue_number}-{stamp}-{short}"


@router.post("/dispatch", response_model=DispatchResponse)
async def dispatch_agent(req: DispatchRequest) -> DispatchResponse:
    """Create a git worktree and ``.agent-task`` file for a single GitHub issue.

    The worktree is created under ``settings.worktrees_dir`` (default
    ``/tmp/worktrees``).  The ``.agent-task`` file embeds everything a Cursor
    agent needs: issue number, role, repo, and the AgentCeption callback URL
    so the agent can call ``POST /api/build/report/*`` to push events.

    Returns the worktree path, branch, and agent-task location so the UI can
    direct the user to open the worktree in Cursor.

    Raises:
        HTTPException 409: When a worktree for this issue already exists.
        HTTPException 500: When ``git worktree add`` fails.
    """
    run_id = f"issue-{req.issue_number}"
    slug = f"issue-{req.issue_number}"
    branch = f"feat/issue-{req.issue_number}"
    batch_id = _make_batch_id(req.issue_number)
    worktree_path = str(Path(settings.worktrees_dir) / slug)

    if Path(worktree_path).exists():
        raise HTTPException(
            status_code=409,
            detail=f"Worktree already exists at {worktree_path}. Remove it before re-dispatching.",
        )

    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "add", worktree_path, "-b", branch,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode().strip()
        logger.error("❌ dispatch: git worktree add failed — %s", err)
        raise HTTPException(
            status_code=500,
            detail=f"git worktree add failed: {err}",
        )

    logger.info("✅ dispatch: worktree created at %s", worktree_path)

    ac_url = getattr(settings, "ac_url", "http://localhost:7777")
    agent_task = (
        f"ISSUE_NUMBER={req.issue_number}\n"
        f"ISSUE_TITLE={req.issue_title}\n"
        f"ROLE={req.role}\n"
        f"GH_REPO={req.repo}\n"
        f"BRANCH={branch}\n"
        f"WORKTREE={worktree_path}\n"
        f"BATCH_ID={batch_id}\n"
        f"SPAWN_MODE=manual\n"
        f"AC_URL={ac_url}\n"
        f"\n"
        f"# Agent instructions\n"
        f"# ─────────────────\n"
        f"# 1. Read the issue: gh issue view {req.issue_number} --repo {req.repo}\n"
        f"# 2. Implement the changes described in the issue.\n"
        f"# 3. Report progress via HTTP callbacks:\n"
        f"#      curl -s -X POST {ac_url}/api/build/report/step"
        f' -H "Content-Type: application/json"'
        f" -d '{{\"issue_number\":{req.issue_number},\"step_name\":\"<step>\",\"agent_run_id\":\"{run_id}\"}}'\n"
        f"#      curl -s -X POST {ac_url}/api/build/report/done"
        f' -H "Content-Type: application/json"'
        f" -d '{{\"issue_number\":{req.issue_number},\"pr_url\":\"<url>\",\"agent_run_id\":\"{run_id}\"}}'\n"
        f"# 4. Open a PR when done.\n"
    )

    agent_task_path = str(Path(worktree_path) / ".agent-task")
    try:
        Path(agent_task_path).write_text(agent_task, encoding="utf-8")
    except Exception as exc:
        cleanup = await asyncio.create_subprocess_exec(
            "git", "worktree", "remove", "--force", worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await cleanup.communicate()
        logger.error("❌ dispatch: .agent-task write failed, worktree cleaned up — %s", exc)
        raise HTTPException(status_code=500, detail=f".agent-task write failed: {exc}") from exc

    logger.info("✅ dispatch: .agent-task written to %s", agent_task_path)

    return DispatchResponse(
        run_id=run_id,
        worktree=worktree_path,
        branch=branch,
        agent_task_path=agent_task_path,
        batch_id=batch_id,
    )


# ---------------------------------------------------------------------------
# Agent callbacks — agents POST to these from inside their worktree
# ---------------------------------------------------------------------------


class StepReport(BaseModel):
    issue_number: int
    step_name: str
    agent_run_id: str | None = None


class BlockerReport(BaseModel):
    issue_number: int
    description: str
    agent_run_id: str | None = None


class DecisionReport(BaseModel):
    issue_number: int
    decision: str
    rationale: str
    agent_run_id: str | None = None


class DoneReport(BaseModel):
    issue_number: int
    pr_url: str
    summary: str = ""
    agent_run_id: str | None = None


@router.post("/report/step")
async def report_step(req: StepReport) -> dict[str, object]:
    """Agent reports starting a named execution step."""
    await persist_agent_event(
        issue_number=req.issue_number,
        event_type="step_start",
        payload={"step": req.step_name},
        agent_run_id=req.agent_run_id,
    )
    logger.info("✅ report_step: issue=%d step=%r", req.issue_number, req.step_name)
    return {"ok": True}


@router.post("/report/blocker")
async def report_blocker(req: BlockerReport) -> dict[str, object]:
    """Agent reports being blocked."""
    await persist_agent_event(
        issue_number=req.issue_number,
        event_type="blocker",
        payload={"description": req.description},
        agent_run_id=req.agent_run_id,
    )
    logger.warning(
        "⚠️ report_blocker: issue=%d — %s", req.issue_number, req.description
    )
    return {"ok": True}


@router.post("/report/decision")
async def report_decision(req: DecisionReport) -> dict[str, object]:
    """Agent records an architectural decision."""
    await persist_agent_event(
        issue_number=req.issue_number,
        event_type="decision",
        payload={"decision": req.decision, "rationale": req.rationale},
        agent_run_id=req.agent_run_id,
    )
    logger.info(
        "✅ report_decision: issue=%d decision=%r", req.issue_number, req.decision
    )
    return {"ok": True}


@router.post("/report/done")
async def report_done(req: DoneReport) -> dict[str, object]:
    """Agent reports completion and links the PR."""
    await persist_agent_event(
        issue_number=req.issue_number,
        event_type="done",
        payload={"pr_url": req.pr_url, "summary": req.summary},
        agent_run_id=req.agent_run_id,
    )
    logger.info(
        "✅ report_done: issue=%d pr_url=%r", req.issue_number, req.pr_url
    )
    return {"ok": True}
