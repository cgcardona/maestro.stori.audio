"""Wave aggregation layer for the AgentCeption telemetry pipeline.

Groups all ``.agent-task`` files by their ``BATCH_ID`` prefix and builds
``WaveSummary`` objects from filesystem signals.  File mtimes serve as proxy
timestamps because agents write the task file at worktree creation time and
update it on state changes — no separate log file is required.

Consumed by ``GET /api/telemetry/waves`` and future timeline UI components.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import BaseModel

from agentception.config import settings
from agentception.models import AgentNode, AgentStatus, TaskFile
from agentception.readers.worktrees import list_active_worktrees

logger = logging.getLogger(__name__)

# Placeholder token / cost constants — refined once real usage data is available.
# These are intentionally conservative estimates based on typical Sonnet runs.
_TOKENS_PER_AGENT = 80_000
_COST_PER_TOKEN_USD = 0.000_003  # ~$3 per 1M input tokens (Sonnet 3.5 blended rate)


class WaveSummary(BaseModel):
    """Aggregated telemetry for one VP/Engineering batch wave.

    A *wave* is the set of all agents that share the same ``BATCH_ID`` prefix
    (e.g. ``eng-20260301T203044Z-4161``).  ``started_at`` / ``ended_at`` are
    UNIX timestamps derived from ``.agent-task`` file mtimes — they are
    approximations, not wall-clock measurements.

    ``ended_at`` is ``None`` when at least one worktree in the batch is still
    active (i.e. its worktree directory still exists on disk).
    """

    batch_id: str
    started_at: float
    ended_at: float | None
    issues_worked: list[int]
    prs_opened: int
    prs_merged: int
    estimated_tokens: int
    estimated_cost_usd: float
    agents: list[AgentNode]


async def aggregate_waves() -> list[WaveSummary]:
    """Scan all worktree ``.agent-task`` files, group by BATCH_ID, compute timing.

    Reads the current set of active worktrees (live filesystem state), then
    augments with *completed* worktrees by scanning the worktrees directory for
    task files whose directories have been removed (past agents self-destruct
    their worktrees after opening a PR).

    Because completed worktrees are deleted, this function can only observe the
    *currently active* agents plus any worktree task files the OS retains.  For
    historical data beyond the current session, a persistent store would be
    needed — that is out of scope for this issue.

    Returns a list of WaveSummary objects, one per unique BATCH_ID, sorted by
    ``started_at`` descending (most recent wave first).
    """
    task_files = await list_active_worktrees()
    return _build_wave_summaries(task_files, settings.worktrees_dir)


async def compute_wave_timing(worktrees: list[TaskFile]) -> tuple[float, float | None]:
    """Return ``(started_at, ended_at)`` from ``.agent-task`` file mtimes.

    ``started_at`` is the mtime of the *earliest* task file in the group.
    ``ended_at`` is the mtime of the *latest* task file, or ``None`` if any
    worktree path in the group still exists on disk (agent still active).

    Both values are UNIX timestamps (seconds since epoch).  Returns ``(0.0,
    None)`` when the list is empty.
    """
    if not worktrees:
        return 0.0, None

    mtimes: list[float] = []
    any_still_active = False

    for tf in worktrees:
        worktree_path = Path(tf.worktree) if tf.worktree else None
        if worktree_path is None:
            continue

        task_file = worktree_path / ".agent-task"
        try:
            mtime = await _get_mtime(task_file)
            mtimes.append(mtime)
        except OSError:
            logger.debug("⚠️  Cannot stat %s — skipping", task_file)

        # If the worktree directory itself still exists, the agent is active.
        if worktree_path.exists():
            any_still_active = True

    if not mtimes:
        return 0.0, None

    started_at = min(mtimes)
    ended_at = None if any_still_active else max(mtimes)
    return started_at, ended_at


# ── Private helpers ────────────────────────────────────────────────────────────


def _build_wave_summaries(
    task_files: list[TaskFile],
    worktrees_dir: Path,
) -> list[WaveSummary]:
    """Group TaskFile objects by BATCH_ID and produce WaveSummary objects.

    Uses file mtimes synchronously (via ``os.stat``) because this is called
    from the non-async poller path.  For async callers, prefer
    ``compute_wave_timing``.
    """
    # Group by BATCH_ID — skip task files with no batch_id.
    groups: dict[str, list[TaskFile]] = {}
    for tf in task_files:
        bid = tf.batch_id
        if not bid:
            continue
        groups.setdefault(bid, []).append(tf)

    summaries: list[WaveSummary] = []
    for batch_id, members in groups.items():
        mtimes: list[float] = []
        any_still_active = False
        issues_worked: list[int] = []
        prs_opened = 0

        for tf in members:
            worktree_path = Path(tf.worktree) if tf.worktree else None

            # Collect issue numbers worked in this wave.
            for iss in tf.closes_issues:
                if iss not in issues_worked:
                    issues_worked.append(iss)

            # Count PRs opened by checking pr_number field.
            if tf.pr_number is not None:
                prs_opened += 1

            if worktree_path is None:
                continue

            # File mtime as proxy timestamp.
            task_file = worktree_path / ".agent-task"
            mtime = _stat_mtime(task_file)
            if mtime is not None:
                mtimes.append(mtime)

            # Active = worktree directory still present.
            if worktree_path.exists():
                any_still_active = True

        started_at = min(mtimes) if mtimes else 0.0
        ended_at = None if any_still_active else (max(mtimes) if mtimes else None)

        agent_count = len(members)
        estimated_tokens = agent_count * _TOKENS_PER_AGENT
        estimated_cost_usd = estimated_tokens * _COST_PER_TOKEN_USD

        # Build minimal AgentNode stubs from TaskFile data.
        agents = [_task_file_to_agent_node(tf) for tf in members]

        summaries.append(
            WaveSummary(
                batch_id=batch_id,
                started_at=started_at,
                ended_at=ended_at,
                issues_worked=sorted(issues_worked),
                prs_opened=prs_opened,
                prs_merged=0,  # Requires GitHub API — deferred to a follow-up.
                estimated_tokens=estimated_tokens,
                estimated_cost_usd=round(estimated_cost_usd, 4),
                agents=agents,
            )
        )

    # Most recent wave first.
    summaries.sort(key=lambda s: s.started_at, reverse=True)
    return summaries


def _stat_mtime(path: Path) -> float | None:
    """Return file mtime as a float, or None on OS error."""
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


async def _get_mtime(path: Path) -> float:
    """Async wrapper around ``os.stat`` for mtime — raises OSError on failure."""
    import asyncio

    loop = asyncio.get_running_loop()
    stat_result = await loop.run_in_executor(None, os.stat, path)
    return stat_result.st_mtime


def _task_file_to_agent_node(tf: TaskFile) -> AgentNode:
    """Convert a TaskFile to a minimal AgentNode for the WaveSummary agents list.

    Only the fields available from a task file are populated.  Fields that
    require live GitHub state (e.g. actual PR status) are left at their
    defaults and can be enriched by the poller in a future iteration.
    """
    agent_id = tf.worktree or f"agent-{tf.issue_number or 'unknown'}"
    worktree_path = Path(tf.worktree) if tf.worktree else None
    is_active = worktree_path.exists() if worktree_path else False

    return AgentNode(
        id=agent_id,
        role=tf.role or "unknown",
        status=AgentStatus.IMPLEMENTING if is_active else AgentStatus.DONE,
        issue_number=tf.issue_number,
        pr_number=tf.pr_number,
        branch=tf.branch,
        batch_id=tf.batch_id,
        worktree_path=tf.worktree,
    )
