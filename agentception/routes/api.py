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
from starlette.requests import Request
from pydantic import BaseModel

from agentception.config import settings
from agentception.intelligence.analyzer import IssueAnalysis, analyze_issue
from agentception.intelligence.dag import DependencyDAG, build_dag
from agentception.intelligence.guards import PRViolation, detect_out_of_order_prs
from agentception.models import AgentNode, PipelineConfig, PipelineState, SpawnRequest, SpawnResult, SwitchProjectRequest  # noqa: E501
from agentception.poller import get_state
from agentception.readers.active_label_override import clear_pin, get_pin, set_pin
from agentception.readers.github import add_wip_label, close_pr, get_active_label, get_issue, get_open_issues
from agentception.readers.pipeline_config import read_pipeline_config, switch_project, write_pipeline_config
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


class ActiveLabelRequest(BaseModel):
    label: str


class ActiveLabelStatus(BaseModel):
    label: str | None
    pinned: bool
    pin: str | None


@router.get("/control/active-label", tags=["control"])
async def get_active_label_status() -> ActiveLabelStatus:
    """Return the current active label and whether it is manually pinned.

    ``pinned`` is ``true`` when an operator override is in effect; ``false``
    means the label was determined automatically by scanning open issues.
    """
    pin = get_pin()
    resolved = await get_active_label()
    return ActiveLabelStatus(label=resolved, pinned=pin is not None, pin=pin)


@router.put("/control/active-label", tags=["control"])
async def pin_active_label(body: ActiveLabelRequest) -> ActiveLabelStatus:
    """Manually pin the active phase label, overriding automatic selection.

    The pin is held in memory for the lifetime of the AgentCeption process.
    Restart clears it and returns to auto mode.
    """
    config = await read_pipeline_config()
    if body.label not in config.active_labels_order:
        raise HTTPException(
            status_code=400,
            detail=f"Label '{body.label}' not in active_labels_order. "
                   f"Valid: {config.active_labels_order}",
        )
    set_pin(body.label)
    logger.info("📌 Active label pinned to '%s'", body.label)
    return ActiveLabelStatus(label=body.label, pinned=True, pin=body.label)


@router.delete("/control/active-label", tags=["control"])
async def unpin_active_label() -> ActiveLabelStatus:
    """Clear the manual pin and return to automatic phase selection."""
    clear_pin()
    resolved = await get_active_label()
    logger.info("🔓 Active label pin cleared, auto-resolved to '%s'", resolved)
    return ActiveLabelStatus(label=resolved, pinned=False, pin=None)


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


@router.post("/config/switch-project", tags=["config"])
async def switch_project_endpoint(body: SwitchProjectRequest) -> PipelineConfig:
    """Switch the active project in ``pipeline-config.json``.

    Sets ``active_project`` to *body.project_name* and persists the updated
    config.  The dashboard reloads paths (``gh_repo``, ``repo_dir``,
    ``worktrees_dir``) for the new project on the next service restart.

    Parameters
    ----------
    body.project_name:
        The ``name`` of the project to activate.  Must match an entry in
        ``PipelineConfig.projects``.

    Returns
    -------
    PipelineConfig
        The updated config with ``active_project`` set.

    Raises
    ------
    HTTP 404
        When *project_name* does not match any configured project.
    """
    try:
        return await switch_project(body.project_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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

    # Delete the local branch first if it already exists but has no live worktree.
    # This happens when a worktree was manually deleted without pruning the branch,
    # leaving `git worktree add -b` unable to create it again (exit 255).
    del_proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_dir, "branch", "-D", branch,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await del_proc.communicate()  # ignore errors — branch may not exist, that's fine

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


def _issue_is_claimed_api(iss: dict[str, object]) -> bool:
    """Return True when an issue carries the ``agent:wip`` label."""
    raw = iss.get("labels")
    if not isinstance(raw, list):
        return False
    for lbl in raw:
        if isinstance(lbl, str) and lbl == "agent:wip":
            return True
        if isinstance(lbl, dict) and lbl.get("name") == "agent:wip":
            return True
    return False


class WaveSpawnResult(BaseModel):
    """Result of a batch spawn-wave operation."""

    active_label: str
    spawned: list[SpawnResult]
    skipped: list[dict[str, object]]  # issues skipped because already claimed / worktree exists


@router.post("/control/spawn-wave", tags=["control"])
async def spawn_wave(role: str = "python-developer") -> WaveSpawnResult:
    """Spawn agents for all unclaimed issues in the currently active phase.

    Reads the active phase label from ``pipeline-config.json``, fetches all
    open unclaimed issues carrying that label, and calls the single-spawn
    logic for each one.  Issues that are already claimed or already have a
    worktree are silently skipped (not an error).

    This is the "Start Wave" action available in the Overview dashboard.  The
    caller is still responsible for launching a Cursor Task pointed at each
    returned worktree path — this endpoint only creates the worktrees and
    ``.agent-task`` files.

    Parameters
    ----------
    role:
        Role file slug to assign to every spawned agent.  Defaults to
        ``python-developer``.  Must be a member of ``VALID_ROLES``.

    Raises
    ------
    HTTP 404
        When no active phase label is found in ``pipeline-config.json`` or
        no open issues carry that label.
    HTTP 422
        When ``role`` is not a recognised role slug.
    """
    from agentception.models import VALID_ROLES
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown role {role!r}. Valid roles: {sorted(VALID_ROLES)}",
        )

    active_label = await get_active_label()
    if not active_label:
        raise HTTPException(
            status_code=404,
            detail=(
                "No active phase label found. Check that pipeline-config.json "
                "active_labels_order contains labels with open issues."
            ),
        )

    phase_issues = await get_open_issues(label=active_label)
    unclaimed = [iss for iss in phase_issues if not _issue_is_claimed_api(iss)]

    if not unclaimed:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No unclaimed open issues found for label '{active_label}'. "
                "All issues may already be claimed or there are no open issues."
            ),
        )

    spawned: list[SpawnResult] = []
    skipped: list[dict[str, object]] = []

    for iss in unclaimed:
        issue_num = iss.get("number")
        if not isinstance(issue_num, int):
            continue
        try:
            result = await spawn_agent(SpawnRequest(issue_number=issue_num, role=role))
            spawned.append(result)
            logger.info("✅ Wave spawn: issue #%d → %s", issue_num, result.worktree)
        except HTTPException as exc:
            # 409 = already claimed or worktree exists → skip, not an error.
            if exc.status_code == 409:
                skipped.append({"issue_number": issue_num, "reason": exc.detail})
                logger.info("⏭️  Wave spawn skipped issue #%d: %s", issue_num, exc.detail)
            else:
                # Any other error (404 issue gone, 500 git failure) — skip and log.
                skipped.append({"issue_number": issue_num, "reason": exc.detail})
                logger.warning("⚠️  Wave spawn failed for issue #%d: %s", issue_num, exc.detail)

    logger.info(
        "✅ spawn-wave complete: label=%s spawned=%d skipped=%d",
        active_label, len(spawned), len(skipped),
    )
    return WaveSpawnResult(active_label=active_label, spawned=spawned, skipped=skipped)


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


# ---------------------------------------------------------------------------
# HTMX partials — issue comments, PR CI checks, PR reviews
# ---------------------------------------------------------------------------


@router.get("/issues/{number}/comments")
async def issue_comments_partial(request: Request, number: int) -> object:
    """HTMX partial: render comments for issue #{number}.

    Lazily fetches from GitHub so the issue detail page loads without blocking.
    """
    from fastapi.responses import HTMLResponse
    from fastapi.templating import Jinja2Templates
    from pathlib import Path
    from agentception.readers.github import get_issue_comments
    from agentception.routes.ui import _TEMPLATES

    comments: list[dict[str, object]] = []
    try:
        comments = await get_issue_comments(number)
    except Exception as exc:
        logger.warning("⚠️  get_issue_comments(%d) failed: %s", number, exc)

    return _TEMPLATES.TemplateResponse(
        request,
        "partials/issue_comments.html",
        {"comments": comments},
    )


@router.get("/prs/{number}/checks")
async def pr_checks_partial(request: Request, number: int) -> object:
    """HTMX partial: render CI check statuses for PR #{number}."""
    from agentception.readers.github import get_pr_checks
    from agentception.routes.ui import _TEMPLATES

    checks: list[dict[str, object]] = []
    error: str | None = None
    try:
        checks = await get_pr_checks(number)
    except Exception as exc:
        error = str(exc)
        logger.warning("⚠️  get_pr_checks(%d) failed: %s", number, exc)

    return _TEMPLATES.TemplateResponse(
        request,
        "partials/pr_checks.html",
        {"checks": checks, "error": error},
    )


@router.get("/prs/{number}/reviews")
async def pr_reviews_partial(request: Request, number: int) -> object:
    """HTMX partial: render review decisions for PR #{number}."""
    from agentception.readers.github import get_pr_reviews
    from agentception.routes.ui import _TEMPLATES

    reviews: list[dict[str, object]] = []
    error: str | None = None
    try:
        reviews = await get_pr_reviews(number)
    except Exception as exc:
        error = str(exc)
        logger.warning("⚠️  get_pr_reviews(%d) failed: %s", number, exc)

    return _TEMPLATES.TemplateResponse(
        request,
        "partials/pr_reviews.html",
        {"reviews": reviews, "error": error},
    )
