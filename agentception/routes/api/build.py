"""Build phase API routes.

Three audiences:

1. **The Build UI** — ``POST /api/build/dispatch`` creates a worktree,
   ``.agent-task`` file, and a ``pending_launch`` DB record.

2. **The AgentCeption Coordinator** — ``GET /api/build/pending-launches``
   exposes the launch queue; ``POST /api/build/acknowledge/{run_id}``
   atomically claims a run before the coordinator spawns its Task worker.

3. **Running agents** — ``POST /api/build/report/*`` lets agents push
   structured lifecycle events back to AgentCeption.
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
from agentception.db.persist import acknowledge_agent_run, persist_agent_event, persist_agent_run_dispatch
from agentception.db.queries import get_pending_launches

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
    host_worktree: str
    branch: str
    agent_task_path: str
    batch_id: str
    status: str = "pending_launch"


def _make_batch_id(issue_number: int) -> str:
    """Generate a deterministic-but-unique batch id for this dispatch."""
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:4]
    return f"issue-{issue_number}-{stamp}-{short}"


@router.post("/dispatch", response_model=DispatchResponse)
async def dispatch_agent(req: DispatchRequest) -> DispatchResponse:
    """Create a worktree, ``.agent-task``, and a ``pending_launch`` DB record.

    The worktree is the isolated git checkout the agent will work in.
    The ``.agent-task`` file is the agent's full briefing — role, scope,
    repo, callbacks.  The ``pending_launch`` DB record is what the
    AgentCeption Dispatcher reads via ``build_get_pending_launches`` to know
    what to spawn next.

    Agents are NOT launched here.  The Dispatcher (a Cursor prompt the user
    pastes once per wave) polls the pending queue and spawns the right role —
    which may be a leaf worker, a VP, or a CTO depending on what was selected.

    Raises:
        HTTPException 409: Worktree already exists.
        HTTPException 500: git worktree add or .agent-task write failed.
    """
    run_id = f"issue-{req.issue_number}"
    slug = f"issue-{req.issue_number}"
    branch = f"feat/issue-{req.issue_number}"
    batch_id = _make_batch_id(req.issue_number)
    worktree_path = str(Path(settings.worktrees_dir) / slug)
    host_worktree_path = str(Path(settings.host_worktrees_dir) / slug)

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
        raise HTTPException(status_code=500, detail=f"git worktree add failed: {err}")

    logger.info("✅ dispatch: worktree created at %s", worktree_path)

    ac_url = getattr(settings, "ac_url", "http://localhost:7777")
    role_file = str(Path(settings.repo_dir) / ".cursor" / "roles" / f"{req.role}.md")
    agent_task = (
        f"RUN_ID={run_id}\n"
        f"ISSUE_NUMBER={req.issue_number}\n"
        f"ISSUE_TITLE={req.issue_title}\n"
        f"ROLE={req.role}\n"
        f"ROLE_FILE={role_file}\n"
        f"GH_REPO={req.repo}\n"
        f"BRANCH={branch}\n"
        f"WORKTREE={host_worktree_path}\n"
        f"BATCH_ID={batch_id}\n"
        f"SPAWN_MODE=dispatcher\n"
        f"AC_URL={ac_url}\n"
        f"\n"
        f"# How this works\n"
        f"# ──────────────\n"
        f"# 1. Read your role file at ROLE_FILE to understand your scope and children.\n"
        f"# 2. If you are a leaf worker: read the issue, implement, open PR.\n"
        f"#    If you are a manager: survey GitHub and spawn child agents via Task tool.\n"
        f"# 3. Report progress via MCP tools (preferred) or HTTP:\n"
        f"#      curl -s -X POST {ac_url}/api/build/report/step"
        f' -H "Content-Type: application/json"'
        f" -d '{{\"issue_number\":{req.issue_number},\"step_name\":\"<step>\",\"agent_run_id\":\"{run_id}\"}}'\n"
        f"#      curl -s -X POST {ac_url}/api/build/report/done"
        f' -H "Content-Type: application/json"'
        f" -d '{{\"issue_number\":{req.issue_number},\"pr_url\":\"<url>\",\"agent_run_id\":\"{run_id}\"}}'\n"
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

    # Write pending_launch record — this is what the Dispatcher reads.
    await persist_agent_run_dispatch(
        run_id=run_id,
        issue_number=req.issue_number,
        role=req.role,
        branch=branch,
        worktree_path=worktree_path,
        batch_id=batch_id,
        host_worktree_path=host_worktree_path,
    )

    return DispatchResponse(
        run_id=run_id,
        worktree=worktree_path,
        host_worktree=host_worktree_path,
        branch=branch,
        agent_task_path=agent_task_path,
        batch_id=batch_id,
        status="pending_launch",
    )


# ---------------------------------------------------------------------------
# Pending launches — Dispatcher reads this to know what to spawn
# ---------------------------------------------------------------------------


@router.get("/pending-launches")
async def list_pending_launches() -> dict[str, object]:
    """Return all runs waiting to be claimed by the Dispatcher.

    The AgentCeption Dispatcher calls this once at startup to discover what
    the UI has queued.  Each item includes the run_id, role, issue number,
    and host-side worktree path so the Dispatcher can spawn the right agent
    at the right level of the tree (leaf worker, VP, or CTO).
    """
    launches = await get_pending_launches()
    return {"pending": launches, "count": len(launches)}


@router.post("/acknowledge/{run_id}")
async def acknowledge_launch(run_id: str) -> dict[str, object]:
    """Atomically claim a pending run before spawning its Task agent.

    The Dispatcher calls this immediately before it spawns the Task so the
    run cannot be double-claimed if two Dispatchers run concurrently.
    Transitions the run from ``pending_launch`` → ``implementing``.

    Returns ``{"ok": true}`` on success or ``{"ok": false, "reason": "..."}``
    when the run was not found or already claimed (idempotency guard).
    """
    ok = await acknowledge_agent_run(run_id)
    if not ok:
        return {"ok": False, "reason": f"Run {run_id!r} not found or not in pending_launch state"}
    logger.info("✅ acknowledge_launch: %s claimed", run_id)
    return {"ok": True, "run_id": run_id}


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
