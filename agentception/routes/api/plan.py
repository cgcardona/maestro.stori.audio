"""API routes: POST /api/plan/draft and POST /api/plan/launch.

POST /api/plan/draft
--------------------
Input:  PlanDraftRequest(dump: str)  — non-empty, non-whitespace text
Output: PlanDraftResponse            — draft_id (uuid4), task_file path,
                                       output_path, status='pending'

Side effects
------------
1. ``git worktree add /tmp/worktrees/plan-draft-<draft_id>`` is executed
   (awaited; it is fast and must succeed before we write the task file).
2. A ``.agent-task`` file is written to the new worktree using the K=V format
   that is compatible with the future TOML migration (issue #888).

POST /api/plan/launch
---------------------
Input:  PlanLaunchRequest(yaml_text: str)  — YAML-encoded plan manifest
Output: PlanLaunchResponse                 — worktree, branch, agent_task_path,
                                             batch_id from the coordinator spawn

Steps:
1. Parse yaml_text → PlanSpec (422 on YAML error with line/col if available).
2. Validate PlanSpec fields via Pydantic (422 on field errors).
3. Validate DAG: detect cycles in depends_on relationships (422 with description).
4. Call plan_spawn_coordinator(manifest_json) — fire-and-forget style.
5. Return PlanLaunchResponse immediately.

Boundary: zero imports from maestro/, muse/, kly/, or storpheus/.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from agentception.mcp.plan_tools import plan_spawn_coordinator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plan", tags=["plan"])

_WORKTREES_BASE = Path("/tmp/worktrees")


class PlanDraftRequest(BaseModel):
    """Request body for POST /api/plan/draft.

    ``dump`` is the raw brain-dump text submitted by the DAW or a human.
    Empty or whitespace-only strings are rejected at validation time (422).
    """

    dump: str

    @field_validator("dump")
    @classmethod
    def dump_must_not_be_blank(cls, v: str) -> str:
        """Reject empty or whitespace-only dump strings before the handler runs."""
        if not v or not v.strip():
            raise ValueError("dump must not be empty or whitespace-only")
        return v


class PlanDraftResponse(BaseModel):
    """Response returned immediately after dispatching the plan draft.

    The caller should poll or subscribe for progress; this endpoint is
    intentionally fire-and-forget from the HTTP perspective.

    ``output_path`` is the absolute path of the YAML file the Cursor agent will
    write when it completes — not the worktree directory.  The AgentCeption
    poller watches this path and emits a ``task_output_ready`` SSE event when
    the file appears.
    """

    draft_id: str
    task_file: str
    output_path: str
    status: str = "pending"


@router.post("/draft")
async def post_plan_draft(request: PlanDraftRequest) -> PlanDraftResponse:
    """Accept a brain dump, create a git worktree, and write an .agent-task file.

    Steps:
    1. Generate a uuid4 draft_id.
    2. Run ``git worktree add /tmp/worktrees/plan-draft-<draft_id>`` (awaited).
    3. Write a K=V .agent-task to that path so an agent can pick it up.
    4. Return PlanDraftResponse immediately.

    Returns 422 if dump is empty/whitespace (validated by PlanDraftRequest).
    Returns 500 if the git worktree add subprocess fails.
    """
    draft_id = str(uuid.uuid4())
    worktree_path = _WORKTREES_BASE / f"plan-draft-{draft_id}"
    task_file_path = worktree_path / ".agent-task"
    # The output file is where the Cursor agent writes the finished PlanSpec YAML.
    # The AgentCeption poller watches *this file* (not the directory) and emits
    # ``task_output_ready`` when it appears on disk.
    output_file_path = worktree_path / ".plan-output.yaml"

    logger.info("✅ Plan draft %s — creating worktree at %s", draft_id, worktree_path)

    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "add", str(worktree_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode().strip()
        logger.error("❌ git worktree add failed for draft %s: %s", draft_id, err)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create worktree for draft {draft_id}: {err}",
        )

    logger.info("✅ Worktree created — writing .agent-task for draft %s", draft_id)

    # Ensure the directory exists. ``git worktree add`` creates it, but we
    # mkdir with exist_ok=True so that the write is safe even if the caller
    # mocked the subprocess in tests.
    worktree_path.mkdir(parents=True, exist_ok=True)

    task_content = (
        f"WORKFLOW=plan-spec\n"
        f"DRAFT_ID={draft_id}\n"
        # OUTPUT_PATH must be a file path (not the directory) so the AgentCeption
        # poller can watch for the file's appearance and emit task_output_ready.
        f"OUTPUT_PATH={output_file_path}\n"
        f"STATUS=pending\n"
        # Guide the Cursor agent: call plan_get_schema() first to get the PlanSpec
        # TOML schema, then produce valid TOML matching that schema.
        f"mcp_tools_hint=call plan_get_schema() to get the PlanSpec TOML schema first\n"
        f"output_schema=plan_get_schema\n"
        f"plan_draft.dump={request.dump}\n"
    )
    task_file_path.write_text(task_content, encoding="utf-8")

    logger.info("✅ .agent-task written for draft %s", draft_id)

    return PlanDraftResponse(
        draft_id=draft_id,
        task_file=str(task_file_path),
        output_path=str(output_file_path),
        status="pending",
    )


# ---------------------------------------------------------------------------
# POST /api/plan/launch — validate manifest, detect DAG cycles, spawn coordinator
# ---------------------------------------------------------------------------


class PlanIssue(BaseModel):
    """A single issue entry in a PlanLaunchRequest manifest.

    ``number`` is the GitHub issue number; ``depends_on`` lists issue numbers
    that must be merged before this one can start.  Both are integers so the
    DAG is resolved by numeric reference, not by title string matching.
    """

    number: int
    title: str
    depends_on: list[int] = []


class PlanSpec(BaseModel):
    """Top-level manifest parsed from the YAML supplied to POST /api/plan/launch.

    ``batch_id`` is a short human slug for the batch (e.g. ``"plan-p2-20260303"``).
    ``issues`` is the ordered list of work units; each carries its numeric
    dependency set so the DAG can be validated before the coordinator is spawned.
    """

    batch_id: str
    issues: list[PlanIssue]


class PlanLaunchRequest(BaseModel):
    """Request body for POST /api/plan/launch.

    ``yaml_text`` is the raw YAML string of the plan manifest.  It must parse
    into a :class:`PlanSpec`; any YAML syntax error or schema mismatch returns
    422 before the coordinator is contacted.
    """

    yaml_text: str


class PlanLaunchResponse(BaseModel):
    """Response returned after the coordinator worktree has been spawned.

    Fields mirror the return value of
    :func:`agentception.mcp.plan_tools.plan_spawn_coordinator`.
    """

    worktree: str
    branch: str
    agent_task_path: str
    batch_id: str


def _detect_cycle(issues: list[PlanIssue]) -> str | None:
    """Return a human-readable cycle description if the dependency graph has a cycle.

    Uses DFS with a per-path *in-stack* set.  The first back-edge found produces
    a description of the form ``"Cycle detected: N → M → N"``.

    Args:
        issues: List of :class:`PlanIssue` objects to check.

    Returns:
        ``None`` when the DAG is acyclic; a non-empty string when a cycle exists.
    """
    issue_map: dict[int, PlanIssue] = {issue.number: issue for issue in issues}
    visited: set[int] = set()
    in_stack: list[int] = []

    def dfs(node: int) -> str | None:
        if node in in_stack:
            cycle_start = in_stack.index(node)
            cycle_path = in_stack[cycle_start:] + [node]
            return "Cycle detected: " + " → ".join(str(n) for n in cycle_path)
        if node in visited:
            return None
        visited.add(node)
        in_stack.append(node)
        issue = issue_map.get(node)
        if issue is not None:
            for dep in issue.depends_on:
                result = dfs(dep)
                if result is not None:
                    return result
        in_stack.pop()
        return None

    for issue in issues:
        if issue.number not in visited:
            result = dfs(issue.number)
            if result is not None:
                return result
    return None


@router.post("/launch")
async def post_plan_launch(request: PlanLaunchRequest) -> PlanLaunchResponse:
    """Validate a YAML plan manifest, check for DAG cycles, and spawn a coordinator.

    Steps:
    1. Parse ``yaml_text`` → :class:`PlanSpec` (422 on YAML or schema error).
    2. Run cycle detection on the ``depends_on`` graph (422 if cycle found).
    3. Await :func:`agentception.mcp.plan_tools.plan_spawn_coordinator` with
       the manifest serialised as JSON.
    4. Return :class:`PlanLaunchResponse` immediately.

    Returns:
        422 on YAML parse error, field validation error, or detected cycle.
        500 if the coordinator spawn fails unexpectedly.
        200 with :class:`PlanLaunchResponse` on success.
    """
    try:
        raw: object = yaml.safe_load(request.yaml_text)
    except yaml.YAMLError as exc:
        detail = f"YAML parse error: {exc}"
        logger.warning("⚠️ /api/plan/launch — %s", detail)
        raise HTTPException(status_code=422, detail=detail)

    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=422,
            detail=f"Expected a YAML mapping at the top level, got {type(raw).__name__}",
        )

    try:
        spec = PlanSpec.model_validate(raw)
    except Exception as exc:
        logger.warning("⚠️ /api/plan/launch — PlanSpec validation failed: %s", exc)
        raise HTTPException(status_code=422, detail=f"Manifest validation error: {exc}")

    cycle = _detect_cycle(spec.issues)
    if cycle is not None:
        logger.warning("⚠️ /api/plan/launch — DAG cycle: %s", cycle)
        raise HTTPException(status_code=422, detail=cycle)

    manifest_json: str = json.dumps(spec.model_dump())

    try:
        result = await plan_spawn_coordinator(manifest_json)
    except Exception as exc:
        logger.error("❌ /api/plan/launch — coordinator spawn failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Coordinator spawn failed: {exc}",
        )

    if "error" in result:
        raise HTTPException(status_code=422, detail=str(result["error"]))

    logger.info(
        "✅ /api/plan/launch — coordinator spawned; worktree=%s", result.get("worktree")
    )

    return PlanLaunchResponse(
        worktree=str(result["worktree"]),
        branch=str(result["branch"]),
        agent_task_path=str(result["agent_task_path"]),
        batch_id=str(result["batch_id"]),
    )
