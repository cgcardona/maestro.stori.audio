"""JSON API routes for the AgentCeption dashboard.

These endpoints are consumed by HTMX fragments, external tools, and tests.
They are intentionally separate from the HTML UI routes so that callers
can choose their preferred serialisation format.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

from agentception.config import settings
from agentception.intelligence.analyzer import IssueAnalysis, analyze_issue
from agentception.intelligence.dag import DependencyDAG, build_dag
from agentception.intelligence.guards import PRViolation, detect_out_of_order_prs
from agentception.models import AgentNode, PipelineConfig, PipelineState, SpawnRequest, SpawnResult
from agentception.poller import get_state
from agentception.readers.github import add_wip_label, close_pr, get_issue
from agentception.readers.pipeline_config import read_pipeline_config, write_pipeline_config
from agentception.readers.transcripts import read_transcript_messages
from agentception.routes.ui import _find_agent
from agentception.telemetry import WaveSummary, aggregate_waves

logger = logging.getLogger(__name__)

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


@router.get("/telemetry/waves", tags=["telemetry"])
async def waves_api() -> list[WaveSummary]:
    """Return a list of WaveSummary objects, one per unique BATCH_ID.

    Scans all active ``.agent-task`` files in the worktrees directory, groups
    them by their ``BATCH_ID`` field, and computes timing from file mtimes.
    Returns an empty list when no worktrees are present or none carry a
    ``BATCH_ID``.  Results are sorted most-recent-first by ``started_at``.
    """
    return await aggregate_waves()


@router.get("/telemetry/cost", tags=["telemetry"])
async def total_cost_api() -> dict[str, float | int]:
    """Return the aggregate token and cost estimate across all historical waves.

    Sums ``estimated_tokens`` and ``estimated_cost_usd`` from every wave
    returned by ``aggregate_waves()``.  The result is a stable summary
    useful for dashboards and budget tracking without iterating wave data
    on the client side.

    Returns ``{"total_tokens": int, "total_cost_usd": float, "wave_count": int}``.
    """
    waves = await aggregate_waves()
    total_tokens = sum(w.estimated_tokens for w in waves)
    total_cost_usd = round(sum(w.estimated_cost_usd for w in waves), 4)
    return {
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost_usd,
        "wave_count": len(waves),
    }


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


@router.get("/config", tags=["config"])
async def get_config() -> PipelineConfig:
    """Return the current pipeline allocation configuration.

    Reads ``.cursor/pipeline-config.json`` from disk on every call so that
    manual edits to the file are reflected immediately without a service restart.
    Falls back to compiled-in defaults when the file does not exist.
    """
    return await read_pipeline_config()


@router.put("/config", tags=["config"])
async def update_config(body: PipelineConfig) -> PipelineConfig:
    """Persist updated pipeline allocation settings to disk.

    Validates the incoming body against :class:`~agentception.models.PipelineConfig`
    before writing, so callers receive a 422 on schema violations rather than
    silently corrupting the config file.

    Returns the saved config so callers can confirm what was written.
    """
    return await write_pipeline_config(body)


def _build_agent_task(
    issue_number: int,
    title: str,
    role: str,
    worktree: Path,
    branch: str,
) -> str:
    """Build the raw text content of a ``.agent-task`` file.

    The format mirrors what the parallel-batch coordinator writes so that
    agents spawned via the control plane behave identically to batch-spawned
    agents.  ``BRANCH`` is included so the engineer kickoff prompt can derive
    the feature branch name without re-computing it.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    repo = settings.gh_repo
    role_file = settings.repo_dir / ".cursor" / "roles" / f"{role}.md"
    return (
        f"WORKFLOW=issue-to-pr\n"
        f"GH_REPO={repo}\n"
        f"ISSUE_NUMBER={issue_number}\n"
        f"ISSUE_TITLE={title}\n"
        f"ISSUE_URL=https://github.com/{repo}/issues/{issue_number}\n"
        f"BRANCH={branch}\n"
        f"ROLE={role}\n"
        f"ROLE_FILE={role_file}\n"
        f"WORKTREE={worktree}\n"
        f"BASE=dev\n"
        f"CLOSES_ISSUES={issue_number}\n"
        f"BATCH_ID=manual\n"
        f"CREATED_AT={now}\n"
        f"SPAWN_MODE=chain\n"
        f"LINKED_PR=none\n"
        f"SPAWN_SUB_AGENTS=false\n"
        f"ATTEMPT_N=0\n"
        f"REQUIRED_OUTPUT=pr_url\n"
        f"ON_BLOCK=stop\n"
    )


@router.get("/dag", tags=["intelligence"])
async def dag_api() -> DependencyDAG:
    """Return the full dependency DAG as JSON.

    Fetches every open issue, parses ``Depends on #N`` declarations, and
    returns a :class:`~agentception.intelligence.dag.DependencyDAG` with
    ``nodes`` (one per open issue) and ``edges`` (one per dependency pair).
    Callers who want an interactive visualisation should use ``GET /dag`` instead.
    """
    return await build_dag()


@router.get("/intelligence/pr-violations", tags=["intelligence"])
async def pr_violations_api() -> list[PRViolation]:
    """Return open PRs that violate active pipeline phase ordering.

    Checks each open PR's ``Closes #N`` reference against the currently active
    ``agentception/*`` label.  A PR is a violation when the issue it closes
    belongs to an earlier (or later) phase than the one currently being worked.

    Returns an empty list when there are no violations or no active label.
    """
    return await detect_out_of_order_prs()


@router.post("/intelligence/pr-violations/{pr_number}/close", tags=["intelligence"])
async def close_violating_pr(pr_number: int) -> dict[str, int]:
    """Close a PR identified as an out-of-order violation.

    Posts an automated comment explaining the closure before closing the PR so
    the git history and GitHub timeline both retain the reason.

    Raises
    ------
    HTTP 500
        When the ``gh pr close`` subprocess call fails (e.g. PR already closed).
    """
    try:
        await close_pr(
            pr_number,
            "Closed by AgentCeption: out-of-order PR violation.",
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to close PR #{pr_number}: {exc}",
        ) from exc
    logger.info("✅ Closed violating PR #%d", pr_number)
    return {"closed": pr_number}


@router.post("/control/spawn")
async def spawn_agent(body: SpawnRequest) -> SpawnResult:
    """Manually seed a new engineer agent for an open, unclaimed issue.

    This endpoint bypasses the CTO/VP polling loop for direct human
    intervention — useful when an issue needs immediate attention or when
    the automated batch scheduler hasn't picked it up yet.

    The caller is responsible for launching the Cursor Task pointed at the
    returned ``worktree`` path.  This endpoint only prepares the worktree
    and ``.agent-task`` file; it does NOT start the agent automatically.

    Raises
    ------
    HTTP 404
        When the issue number does not exist on GitHub or the issue is not
        open (already closed or merged).
    HTTP 409
        When the issue already carries an ``agent:wip`` label, indicating
        another agent has already claimed it.
    HTTP 500
        When the git worktree creation fails (e.g. directory already exists).
    """
    issue_number = body.issue_number

    # ── 1. Fetch issue state from GitHub ──────────────────────────────────────
    try:
        issue = await get_issue(issue_number)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Issue #{issue_number} not found on GitHub: {exc}",
        ) from exc

    state = issue.get("state", "")
    if state != "OPEN":
        raise HTTPException(
            status_code=404,
            detail=f"Issue #{issue_number} is not open (state={state!r})",
        )

    # ── 2. Check whether the issue is already claimed ─────────────────────────
    raw_labels = issue.get("labels")
    # narrow from object to list before iterating — get() returns object
    label_names: list[str] = (
        [lbl for lbl in raw_labels if isinstance(lbl, str)]
        if isinstance(raw_labels, list)
        else []
    )
    if "agent:wip" in label_names:
        raise HTTPException(
            status_code=409,
            detail=f"Issue #{issue_number} is already claimed (agent:wip label present)",
        )

    # ── 3. Pre-flight: check for an existing worktree BEFORE claiming ─────────
    # Claiming (add_wip_label) is irreversible in the short term; checking the
    # worktree path first prevents leaving an issue permanently labelled
    # agent:wip with no agent actually working on it.
    branch = f"feat/issue-{issue_number}"
    worktree_path = settings.worktrees_dir / f"issue-{issue_number}"

    if worktree_path.exists():
        raise HTTPException(
            status_code=409,
            detail=(
                f"Worktree directory already exists: {worktree_path}. "
                "Remove it manually and retry."
            ),
        )

    # ── 4. Add agent:wip label ────────────────────────────────────────────────
    await add_wip_label(issue_number)

    repo_dir = str(settings.repo_dir)
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_dir,
        "worktree", "add", "-b", branch,
        str(worktree_path), "origin/dev",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=(
                f"git worktree add failed (exit {proc.returncode}): "
                f"{stderr.decode().strip()!r}"
            ),
        )

    logger.info(
        "✅ Created worktree %s on branch %s for issue #%d",
        worktree_path,
        branch,
        issue_number,
    )

    # ── 5. Write .agent-task ──────────────────────────────────────────────────
    title_raw = issue.get("title", "")
    title: str = title_raw if isinstance(title_raw, str) else str(title_raw)
    agent_task_content = _build_agent_task(
        issue_number=issue_number,
        title=title,
        role=body.role,
        worktree=worktree_path,
        branch=branch,
    )
    task_file = worktree_path / ".agent-task"
    task_file.write_text(agent_task_content, encoding="utf-8")

    logger.info("✅ Wrote .agent-task to %s", task_file)

    return SpawnResult(
        spawned=issue_number,
        worktree=str(worktree_path),
        branch=branch,
        agent_task=agent_task_content,
    )


@router.post("/analyze/issue/{number}", tags=["intelligence"])
async def analyze_issue_api(number: int) -> IssueAnalysis:
    """Parse an issue body and return structured parallelism / role recommendations.

    Fetches the issue body from GitHub via the ``gh`` CLI and applies
    local heuristics to infer dependencies, conflict risk, and the
    recommended engineer role.  No model calls are made — results are
    deterministic for a given issue body.

    This endpoint feeds into the Eng VP ``.agent-task`` generation pipeline:
    the caller can use ``recommended_role``, ``parallelism``, and
    ``recommended_merge_after`` to decide whether and how to schedule an agent.

    Parameters
    ----------
    number:
        GitHub issue number to analyse.

    Raises
    ------
    HTTP 404
        When the GitHub CLI cannot find the issue (non-existent or no access).
    HTTP 500
        When the ``gh`` subprocess exits with a non-zero status for any other
        reason.
    """
    try:
        return await analyze_issue(number)
    except RuntimeError as exc:
        detail = str(exc)
        status = 404 if "not found" in detail.lower() else 500
        raise HTTPException(status_code=status, detail=detail) from exc
