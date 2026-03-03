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
from agentception.models import AgentNode, PipelineConfig, PipelineState, SpawnCoordinatorRequest, SpawnCoordinatorResult, SpawnRequest, SpawnResult, SwitchProjectRequest  # noqa: E501
from agentception.poller import get_state
from agentception.readers.active_label_override import clear_pin, get_pin, set_pin
from agentception.readers.github import add_wip_label, close_pr, get_active_label, get_issue, get_issue_body, get_open_issues
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

    Sets ``active_project`` to *body.project_name*, persists the updated
    config, then immediately reloads ``settings`` so the poller targets the
    new repo on its very next tick — no service restart required.

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
        result = await switch_project(body.project_name)
        # Apply the new project's paths immediately — readers pick them up
        # on the very next call without waiting for a service restart.
        from agentception.config import settings
        settings.reload()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _resolve_cognitive_arch(issue_body: str, role: str) -> str:
    """Derive COGNITIVE_ARCH string from issue body and role.

    Format: ``figure:skill1:skill2``.  Mirrors the logic in
    ``parallel-issue-to-pr.md`` so agents spawned via the control plane
    receive the same architectural context as batch-spawned agents.
    """
    body = issue_body.lower()

    if any(k in body for k in ("d3.js", "force-directed", "d3.force", "d3.select")):
        skills = "d3:javascript"
    elif any(k in body for k in ("monaco", "vs/loader", "editor.*cdn")):
        skills = "monaco"
    elif any(k in body for k in ("htmx", "hx-", "sse-connect", "hx-ext")):
        skills = "htmx"
        if any(k in body for k in ("jinja2", ".html", "templateresponse", "extends.*html")):
            skills += ":jinja2"
        if any(k in body for k in ("alpine", "x-data", "x-show")):
            skills += ":alpine"
    elif any(k in body for k in ("jinja2", "templateresponse", "extends.*html")):
        skills = "jinja2"
    elif any(k in body for k in ("postgres", "alembic", "migration", "sqlalchemy")):
        skills = "postgresql:python"
    elif any(k in body for k in ("dockerfile", "from python", "compose.*service")):
        skills = "devops"
    elif any(k in body for k in ("midi", "storpheus", "gm.program", "tmidix")):
        skills = "midi:python"
    elif any(k in body for k in ("llm", "embedding", "rag", "openrouter", "claude")):
        skills = "llm:python"
    elif any(k in body for k in ("apirouter", "fastapi", "depends", "response_model")):
        skills = "fastapi:python"
    else:
        skills = "python"

    if any(k in body for k in ("migration", "alembic", "schema", "db.model", "postgres")):
        figure = "dijkstra"
    elif any(k in body for k in ("sse", "broadcast", "async", "asyncio", "fanout")):
        figure = "shannon"
    elif any(k in body for k in ("overview", "dashboard", "pipeline", "tree")):
        figure = "lovelace"
    elif any(k in body for k in ("api", "endpoint", "route", "contract")):
        figure = "turing"
    else:
        figure = "hopper"

    return f"{figure}:{skills}"


def _build_agent_task(
    issue_number: int,
    title: str,
    role: str,
    worktree: Path,
    host_worktree: Path,
    branch: str,
    phase_label: str = "",
    depends_on: str = "none",
    cognitive_arch: str = "hopper:python",
    wave_id: str = "manual",
) -> str:
    """Build the raw text content of a ``.agent-task`` file.

    The format mirrors what the ``parallel-issue-to-pr.md`` coordinator
    script generates so that agents spawned via the control plane receive
    the same context as batch-spawned agents.

    ``worktree`` is the container-side path (written to the file for Docker
    commands).  ``host_worktree`` is the host-side path embedded as
    ``HOST_WORKTREE`` so the Cursor Task launcher can use the correct path
    when opening the worktree as a project root.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    repo = settings.gh_repo
    # ROLE_FILE is metadata only — the kickoff prompt embeds all role content
    # inline.  The path uses the host repo dir so it is human-readable even
    # though agents are instructed not to read it from disk.
    role_file = settings.host_worktrees_dir.parent.parent / "dev" / "tellurstori" / "maestro" / ".cursor" / "roles" / f"{role}.md"
    # Simpler: derive from host_worktree's ancestor (host repo root).
    # host_worktrees_dir is e.g. ~/.cursor/worktrees/maestro
    # host repo is ~/dev/tellurstori/maestro — but that's not in settings.
    # Use a known-good path from settings.repo_dir (container: /repo → host unknown here).
    # The agent is told to ignore ROLE_FILE; use a placeholder that's self-documenting.
    role_file_display = f"<host-repo>/.cursor/roles/{role}.md"
    return (
        f"WORKFLOW=issue-to-pr\n"
        f"GH_REPO={repo}\n"
        f"ISSUE_NUMBER={issue_number}\n"
        f"ISSUE_TITLE={title}\n"
        f"ISSUE_URL=https://github.com/{repo}/issues/{issue_number}\n"
        f"PHASE_LABEL={phase_label}\n"
        f"DEPENDS_ON={depends_on}\n"
        f"BRANCH={branch}\n"
        f"ROLE={role}\n"
        f"ROLE_FILE={role_file_display}\n"
        f"WORKTREE={worktree}\n"
        f"HOST_WORKTREE={host_worktree}\n"
        f"BASE=dev\n"
        f"CLOSES_ISSUES={issue_number}\n"
        f"BATCH_ID={wave_id}\n"
        f"WAVE={wave_id}\n"
        f"COGNITIVE_ARCH={cognitive_arch}\n"
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

    # Lock the worktree immediately so git doesn't auto-prune it when git
    # is run from a context where the container-internal path is not resolvable
    # (e.g. running `git worktree list` from the host sees /worktrees/issue-N
    # as missing since it's bind-mounted from ~/.cursor/worktrees/maestro/).
    lock_proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_dir,
        "worktree", "lock", str(worktree_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await lock_proc.communicate()  # best-effort — non-fatal if it fails

    logger.info(
        "✅ Created worktree %s on branch %s for issue #%d",
        worktree_path,
        branch,
        issue_number,
    )

    # ── 5. Write .agent-task ──────────────────────────────────────────────────
    title_raw = issue.get("title", "")
    title: str = title_raw if isinstance(title_raw, str) else str(title_raw)

    # Fetch issue body for DEPENDS_ON extraction and COGNITIVE_ARCH derivation.
    try:
        issue_body = await get_issue_body(issue_number)
    except Exception:
        issue_body = ""

    # Extract "Depends on #NNN" patterns — comma-separated, or "none" if absent.
    import re as _re
    dep_matches = _re.findall(r"[Dd]epends\s+on\s+#(\d+)", issue_body)
    depends_on = ",".join(dep_matches) if dep_matches else "none"

    # Derive COGNITIVE_ARCH from issue body so the agent gets the right persona.
    cognitive_arch = _resolve_cognitive_arch(issue_body, body.role)

    # Get active phase label for provenance — best-effort; not fatal if absent.
    try:
        phase_label = await get_active_label() or ""
    except Exception:
        phase_label = ""

    # Compute the host-side worktree path for display to the user.
    # host_worktrees_dir is ~/.cursor/worktrees/maestro (set via AC_HOST_WORKTREES_DIR).
    host_worktree_path = settings.host_worktrees_dir / f"issue-{issue_number}"

    agent_task_content = _build_agent_task(
        issue_number=issue_number,
        title=title,
        role=body.role,
        worktree=worktree_path,
        host_worktree=host_worktree_path,
        branch=branch,
        phase_label=phase_label,
        depends_on=depends_on,
        cognitive_arch=cognitive_arch,
    )
    task_file = worktree_path / ".agent-task"
    task_file.write_text(agent_task_content, encoding="utf-8")

    logger.info("✅ Wrote .agent-task to %s", task_file)

    return SpawnResult(
        spawned=issue_number,
        worktree=str(worktree_path),
        host_worktree=str(host_worktree_path),
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


class SweepResult(BaseModel):
    """Result of a stale-state sweep operation."""

    deleted_branches: list[str]
    removed_worktrees: list[str]
    cleared_wip_labels: list[int]
    errors: list[str]


@router.post("/control/sweep", tags=["control"])
async def sweep_stale(dry_run: bool = False) -> SweepResult:
    """Delete all stale agent branches, remove orphan worktrees, and clear stale agent:wip labels.

    A branch is stale when it is an agent branch (``feat/issue-N`` or
    ``feat/brain-dump-*``) with no live git worktree checked out on it.
    A claim is stale when an issue carries ``agent:wip`` but has no matching
    worktree directory.

    Parameters
    ----------
    dry_run:
        When ``True``, return what *would* be deleted without making any changes.
    """
    from agentception.readers.git import list_git_branches, list_git_worktrees
    from agentception.readers.github import clear_wip_label, get_wip_issues
    from agentception.intelligence.guards import detect_stale_claims

    deleted_branches: list[str] = []
    removed_worktrees: list[str] = []
    cleared_wip_labels: list[int] = []
    errors: list[str] = []

    repo_dir = str(settings.repo_dir)

    # ── 1. Stale branches (agent branch with no live worktree) ───────────────
    live_branches: set[str] = {
        str(wt.get("branch", ""))
        for wt in await list_git_worktrees()
        if wt.get("branch") and not wt.get("is_main")
    }

    for branch in await list_git_branches():
        name = str(branch.get("name", "")).strip()
        if not branch.get("is_agent_branch"):
            continue
        if name in live_branches:
            continue  # has a live worktree — not stale
        deleted_branches.append(name)
        if not dry_run:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_dir, "branch", "-D", name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                errors.append(f"branch -D {name}: {stderr.decode().strip()}")
                deleted_branches.pop()

    # ── 2. Prune git's internal worktree references (unlocked stale entries) ─
    if not dry_run:
        prune_proc = await asyncio.create_subprocess_exec(
            "git", "-C", repo_dir, "worktree", "prune",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        prune_out, prune_err = await prune_proc.communicate()
        pruned = (prune_out + prune_err).decode().strip()
        if pruned:
            removed_worktrees.append(f"pruned: {pruned}")

    # ── 3. Stale agent:wip labels (issue claimed but no worktree on disk) ────
    try:
        wip_issues = await get_wip_issues()
        stale_claims = await detect_stale_claims(wip_issues, settings.worktrees_dir)
        for claim in stale_claims:
            cleared_wip_labels.append(claim.issue_number)
            if not dry_run:
                try:
                    await clear_wip_label(claim.issue_number)
                except Exception as exc:
                    errors.append(f"clear wip #{claim.issue_number}: {exc}")
                    cleared_wip_labels.pop()
    except Exception as exc:
        errors.append(f"stale claims check: {exc}")

    action = "Would delete" if dry_run else "Swept"
    logger.info(
        "✅ %s: branches=%s wip_labels=%s errors=%d",
        action, deleted_branches, cleared_wip_labels, len(errors),
    )
    return SweepResult(
        deleted_branches=deleted_branches,
        removed_worktrees=removed_worktrees,
        cleared_wip_labels=cleared_wip_labels,
        errors=errors,
    )


class DeleteWorktreeResult(BaseModel):
    """Result of removing a single worktree."""

    slug: str
    deleted: bool
    pruned: bool
    error: str | None = None


@router.delete("/worktrees/{slug}", tags=["control"])
async def delete_worktree(slug: str) -> DeleteWorktreeResult:
    """Remove a single linked worktree by its slug (directory name).

    Runs ``git worktree remove --force <path>`` followed by
    ``git worktree prune`` to keep git's internal reference list clean.
    The worktree's branch is intentionally left intact so history is
    preserved; use the sweep endpoint to bulk-remove stale branches.
    """
    from agentception.readers.git import list_git_worktrees

    repo_dir = str(settings.repo_dir)
    worktrees = await list_git_worktrees()
    wt = next((w for w in worktrees if str(w.get("slug", "")) == slug), None)
    if wt is None:
        raise HTTPException(status_code=404, detail=f"Worktree '{slug}' not found")
    if wt.get("is_main"):
        raise HTTPException(status_code=400, detail="Cannot delete the main worktree")

    wt_path = str(wt["path"])
    deleted = False
    pruned = False
    error: str | None = None

    # Unlock first — locked worktrees silently resist `remove --force`.
    if wt.get("locked"):
        unlock_proc = await asyncio.create_subprocess_exec(
            "git", "-C", repo_dir, "worktree", "unlock", wt_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await unlock_proc.communicate()

    # Remove the worktree directory (--force handles dirty working trees).
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_dir, "worktree", "remove", "--force", wt_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode == 0:
        deleted = True
        logger.info("✅ Removed worktree %s", wt_path)
    else:
        error = stderr.decode().strip()
        logger.warning("⚠️  Failed to remove worktree %s: %s", wt_path, error)

    # Always attempt a prune pass to clean up git's internal metadata.
    prune_proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo_dir, "worktree", "prune",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await prune_proc.communicate()
    pruned = prune_proc.returncode == 0

    return DeleteWorktreeResult(slug=slug, deleted=deleted, pruned=pruned, error=error)


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


def _build_coordinator_task(
    slug: str,
    brain_dump: str,
    label_prefix: str,
    worktree: Path,
    host_worktree: Path,
    branch: str,
) -> str:
    """Build the ``.agent-task`` content for a brain-dump coordinator worktree.

    The coordinator agent reads ``WORKFLOW=bugs-to-issues`` and follows
    ``parallel-bugs-to-issues.md``: it runs the Phase Planner, creates GitHub
    labels, creates worktrees for each batch, writes sub-agent task files, and
    launches sub-agents.  AgentCeption's only job is to prepare the worktree
    and this file — the Cursor background agent does all LLM work.

    The ``BRAIN_DUMP`` section is appended as a freeform block after the
    structured key=value header so the coordinator can read it verbatim.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    repo = settings.gh_repo
    prefix_line = f"LABEL_PREFIX={label_prefix}\n" if label_prefix else ""
    return (
        f"WORKFLOW=bugs-to-issues\n"
        f"GH_REPO={repo}\n"
        f"ROLE=coordinator\n"
        f"ROLE_FILE=<host-repo>/.cursor/roles/coordinator.md\n"
        f"WORKTREE={worktree}\n"
        f"HOST_WORKTREE={host_worktree}\n"
        f"BASE=dev\n"
        f"BATCH_ID={slug}\n"
        f"WAVE={slug}\n"
        f"COGNITIVE_ARCH=coordinator\n"
        f"{prefix_line}"
        f"CREATED_AT={now}\n"
        f"SPAWN_MODE=chain\n"
        f"SPAWN_SUB_AGENTS=true\n"
        f"ATTEMPT_N=0\n"
        f"REQUIRED_OUTPUT=phase_plan\n"
        f"ON_BLOCK=stop\n"
        f"\nBRAIN_DUMP:\n{brain_dump}\n"
    )


@router.post("/control/spawn-coordinator", tags=["control"])
async def spawn_coordinator(body: SpawnCoordinatorRequest) -> SpawnCoordinatorResult:
    """Seed a coordinator worktree from a free-form brain dump.

    Creates a git worktree and writes an ``.agent-task`` file that tells a
    Cursor background agent to run as coordinator using
    ``parallel-bugs-to-issues.md``.  The agent will:

    1. Run the Phase Planner against the ``BRAIN_DUMP`` field.
    2. Create required GitHub labels (phase-N/*, status/*, priority/*).
    3. Create one sub-worktree per phase-batch.
    4. Write ``.agent-task`` files for each sub-agent.
    5. Launch sub-agents via the Cursor Task tool.

    This endpoint only creates the worktree and task file.  The caller
    (AgentCeption UI) instructs the user to open the returned
    ``host_worktree`` path in Cursor to start the coordinator agent.

    Raises
    ------
    HTTP 409
        When a worktree with the same slug already exists.
    HTTP 422
        When ``brain_dump`` is empty.
    HTTP 500
        When ``git worktree add`` fails.
    """
    brain_dump = body.brain_dump.strip()
    if not brain_dump:
        raise HTTPException(status_code=422, detail="brain_dump must not be empty")

    now_slug = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = f"brain-dump-{now_slug}"
    branch = f"feat/{slug}"
    worktree_path = settings.worktrees_dir / slug
    host_worktree_path = settings.host_worktrees_dir / slug

    if worktree_path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Worktree '{slug}' already exists — wait a second and retry.",
        )

    repo_dir = str(settings.repo_dir)

    # Create the worktree on a fresh branch off origin/dev.
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

    logger.info("✅ Created coordinator worktree %s on branch %s", worktree_path, branch)

    agent_task_content = _build_coordinator_task(
        slug=slug,
        brain_dump=brain_dump,
        label_prefix=body.label_prefix,
        worktree=worktree_path,
        host_worktree=host_worktree_path,
        branch=branch,
    )
    task_file = worktree_path / ".agent-task"
    task_file.write_text(agent_task_content, encoding="utf-8")
    logger.info("✅ Wrote coordinator .agent-task to %s", task_file)

    return SpawnCoordinatorResult(
        slug=slug,
        worktree=str(worktree_path),
        host_worktree=str(host_worktree_path),
        branch=branch,
        agent_task=agent_task_content,
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
