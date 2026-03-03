"""API route: POST /api/plan/draft — accept a brain dump and dispatch to Cursor.

Contract
--------
Input:  PlanDraftRequest(dump: str)  — non-empty, non-whitespace text
Output: PlanDraftResponse            — draft_id (uuid4), task_file path,
                                       output_path, status='pending'

Side effects
------------
1. ``git worktree add /tmp/worktrees/plan-draft-<draft_id>`` is executed
   (awaited; it is fast and must succeed before we write the task file).
2. A ``.agent-task`` file is written to the new worktree using the K=V format
   that is compatible with the future TOML migration (issue #888).

Boundary: zero imports from maestro/, muse/, kly/, or storpheus/.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

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
        f"OUTPUT_PATH={worktree_path}\n"
        f"STATUS=pending\n"
        f"plan_draft.dump={request.dump}\n"
    )
    task_file_path.write_text(task_content, encoding="utf-8")

    logger.info("✅ .agent-task written for draft %s", draft_id)

    return PlanDraftResponse(
        draft_id=draft_id,
        task_file=str(task_file_path),
        output_path=str(worktree_path),
        status="pending",
    )
