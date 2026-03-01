"""AgentCeption background poller — pipeline state aggregation and SSE broadcast.

This module owns the single shared ``PipelineState`` that the dashboard
displays.  A background task calls ``polling_loop()`` on startup; it wakes
every ``poll_interval_seconds``, calls ``tick()``, and broadcasts the new
state to every connected SSE client via a per-client ``asyncio.Queue``.

Public surface used by API routes:
- ``subscribe()`` / ``unsubscribe()``  — SSE client lifecycle
- ``get_state()``                      — synchronous snapshot for HTTP /state

Public surface used by ``app.py`` lifespan:
- ``polling_loop()``  — the long-running background coroutine
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from pathlib import Path

from agentception.config import settings
from agentception.models import AgentNode, AgentStatus, PipelineState, TaskFile
from agentception.readers.github import (
    get_active_label,
    get_open_issues,
    get_open_prs,
    get_wip_issues,
)
from agentception.readers.worktrees import list_active_worktrees, worktree_last_commit_time

logger = logging.getLogger(__name__)

# Agents whose most-recent commit is older than this threshold are flagged.
_STUCK_THRESHOLD_SECONDS: int = 30 * 60  # 30 minutes

# ---------------------------------------------------------------------------
# Shared state — module-level singletons, mutated only by tick()
# ---------------------------------------------------------------------------

_state: PipelineState | None = None
_subscribers: list[asyncio.Queue[PipelineState]] = []


# ---------------------------------------------------------------------------
# GitHub board aggregation
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class GitHubBoard:
    """Aggregated GitHub data for a single polling tick.

    Fetched in parallel so one slow GitHub API call doesn't block the others.
    All fields come directly from the readers; the poller merges them with
    filesystem data to produce ``PipelineState``.
    """

    active_label: str | None
    open_issues: list[dict[str, object]]
    open_prs: list[dict[str, object]]
    wip_issues: list[dict[str, object]]


async def build_github_board() -> GitHubBoard:
    """Fetch all required GitHub data in parallel and return a ``GitHubBoard``.

    Using ``asyncio.gather`` keeps the wall-clock cost equal to the slowest
    individual request rather than the sum of all requests.
    """
    active_label, open_issues, open_prs, wip_issues = await asyncio.gather(
        get_active_label(),
        get_open_issues(),
        get_open_prs(),
        get_wip_issues(),
    )
    return GitHubBoard(
        active_label=active_label,
        open_issues=open_issues,
        open_prs=open_prs,
        wip_issues=wip_issues,
    )


# ---------------------------------------------------------------------------
# Agent merging — correlate filesystem worktrees with GitHub signals
# ---------------------------------------------------------------------------


async def merge_agents(
    worktrees: list[TaskFile],
    github: GitHubBoard,
) -> list[AgentNode]:
    """Build an ``AgentNode`` list by correlating worktree task files with GitHub.

    Status derivation rules (applied in priority order):
    1. Worktree branch matches an open PR ``headRefName`` → REVIEWING
    2. Worktree issue number appears in ``agent:wip`` issues → IMPLEMENTING
    3. Otherwise → UNKNOWN
    """
    # Index open PRs by branch name for O(1) lookup.
    pr_branches: set[str] = {
        str(pr["headRefName"])
        for pr in github.open_prs
        if isinstance(pr.get("headRefName"), str)
    }

    # Index WIP issue numbers for O(1) lookup.
    wip_issue_numbers: set[int] = set()
    for issue in github.wip_issues:
        num = issue.get("number")
        if isinstance(num, int):
            wip_issue_numbers.add(num)

    nodes: list[AgentNode] = []
    for tf in worktrees:
        branch = tf.branch or ""
        if branch and branch in pr_branches:
            status = AgentStatus.REVIEWING
        elif tf.issue_number is not None and tf.issue_number in wip_issue_numbers:
            status = AgentStatus.IMPLEMENTING
        else:
            status = AgentStatus.UNKNOWN

        node_id = tf.worktree or f"issue-{tf.issue_number}" or "unknown"
        nodes.append(
            AgentNode(
                id=node_id,
                role=tf.role or "unknown",
                status=status,
                issue_number=tf.issue_number,
                branch=tf.branch,
                batch_id=tf.batch_id,
                worktree_path=tf.worktree,
            )
        )

    return nodes


# ---------------------------------------------------------------------------
# Alert detection
# ---------------------------------------------------------------------------


async def detect_alerts(
    worktrees: list[TaskFile],
    github: GitHubBoard,
) -> list[str]:
    """Detect pipeline problems and return human-readable alert strings.

    Three alert classes:
    1. **Stale claim** — an ``agent:wip`` issue has no live worktree.
    2. **Out-of-order PR** — an open PR's labels include an agentception phase
       that no longer matches the currently active phase.
    3. **Stuck agent** — the most-recent commit in a worktree is > 30 min old.
    """
    alerts: list[str] = []
    now = time.time()

    # Fast lookup set: which issue numbers have a live worktree?
    worktree_issue_numbers: set[int] = {
        tf.issue_number for tf in worktrees if tf.issue_number is not None
    }

    # ── Alert 1: agent:wip issue with no matching worktree ─────────────────
    for issue in github.wip_issues:
        num = issue.get("number")
        if isinstance(num, int) and num not in worktree_issue_numbers:
            alerts.append(f"Stale claim on #{num}")

    # ── Alert 2: open PR labelled with a non-active agentception phase ──────
    active = github.active_label
    for pr in github.open_prs:
        pr_labels = pr.get("labels", [])
        if not isinstance(pr_labels, list):
            continue
        for lbl in pr_labels:
            if not isinstance(lbl, dict):
                continue
            label_name = lbl.get("name", "")
            if (
                isinstance(label_name, str)
                and label_name.startswith("agentception/")
                and label_name != active
            ):
                pr_num = pr.get("number")
                if isinstance(pr_num, int):
                    alerts.append(f"Out-of-order PR #{pr_num}")
                break  # one alert per PR is enough

    # ── Alert 3: worktree last commit > 30 min ago (async path) ────────────
    for tf in worktrees:
        if tf.worktree is None:
            continue
        path = Path(tf.worktree)
        if not path.exists():
            continue
        last_commit = await worktree_last_commit_time(path)
        if last_commit > 0.0 and (now - last_commit) > _STUCK_THRESHOLD_SECONDS:
            label = f"issue #{tf.issue_number}" if tf.issue_number else path.name
            alerts.append(f"Possible stuck agent on {label}")

    return alerts


# ---------------------------------------------------------------------------
# Pub/sub — SSE client registry
# ---------------------------------------------------------------------------


def subscribe() -> asyncio.Queue[PipelineState]:
    """Register a new SSE client and return its dedicated queue.

    The caller owns the queue for the duration of the connection.  Call
    ``unsubscribe()`` in a ``finally`` block to prevent queue accumulation.
    """
    q: asyncio.Queue[PipelineState] = asyncio.Queue()
    _subscribers.append(q)
    logger.debug("✅ SSE subscriber added (total=%d)", len(_subscribers))
    return q


def unsubscribe(q: asyncio.Queue[PipelineState]) -> None:
    """Remove a client queue after disconnect.

    Idempotent — calling this for a queue that was already removed is safe.
    """
    try:
        _subscribers.remove(q)
        logger.debug("✅ SSE subscriber removed (total=%d)", len(_subscribers))
    except ValueError:
        pass  # Already removed — benign race on disconnect.


def get_state() -> PipelineState | None:
    """Return the most recently computed ``PipelineState`` (synchronous).

    Returns ``None`` before the first tick completes.  API routes should
    return a 503 or an empty state object when this is ``None``.
    """
    return _state


async def broadcast(state: PipelineState) -> None:
    """Push the new state to every connected SSE subscriber.

    Iterates over a snapshot of the list so that a concurrent ``unsubscribe``
    during iteration does not raise ``RuntimeError``.
    """
    for q in list(_subscribers):
        await q.put(state)
    logger.debug("📡 Broadcast to %d subscriber(s)", len(_subscribers))


# ---------------------------------------------------------------------------
# Core polling functions
# ---------------------------------------------------------------------------


async def tick() -> PipelineState:
    """Execute a single polling cycle: collect → merge → detect → broadcast.

    This is the unit of work for the background loop.  It is also the
    function to call directly in tests to exercise the full data pipeline
    without actually sleeping.

    Returns the newly computed ``PipelineState`` (also stored in ``_state``
    and broadcast to all subscribers).
    """
    global _state

    worktrees = await list_active_worktrees()
    github = await build_github_board()
    agents = await merge_agents(worktrees, github)
    alerts = await detect_alerts(worktrees, github)

    state = PipelineState(
        active_label=github.active_label,
        issues_open=len(github.open_issues),
        prs_open=len(github.open_prs),
        agents=agents,
        alerts=alerts,
        polled_at=time.time(),
    )

    _state = state
    await broadcast(state)
    return state


async def polling_loop() -> None:
    """Run the tick/broadcast cycle on a fixed interval until cancelled.

    Designed to be launched as an ``asyncio.Task`` from the FastAPI lifespan.
    Errors inside a single tick are logged and swallowed so one bad GitHub
    response cannot kill the entire dashboard.
    """
    logger.info(
        "✅ AgentCeption polling loop started (interval=%ds)",
        settings.poll_interval_seconds,
    )
    while True:
        try:
            await asyncio.sleep(settings.poll_interval_seconds)
            await tick()
        except asyncio.CancelledError:
            logger.info("✅ Polling loop stopped cleanly")
            return
        except Exception as exc:
            logger.warning("⚠️  Polling loop error: %s", exc)
