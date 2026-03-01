"""Domain models for the AgentCeption dashboard.

These types are the shared contract between the background poller, the API
routes, and the frontend templates. Keep them flat — no nested Pydantic
models that reference external services.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class AgentStatus(str, Enum):
    """Lifecycle state of a single pipeline agent, derived from filesystem + GitHub signals."""

    IMPLEMENTING = "implementing"
    REVIEWING = "reviewing"
    DONE = "done"
    STALE = "stale"
    UNKNOWN = "unknown"


class AgentNode(BaseModel):
    """A single agent in the pipeline tree.

    Represents one Cursor/Claude agent instance that is either actively working
    or has completed its assigned task. Children are spawned sub-agents.
    """

    id: str
    role: str
    status: AgentStatus
    issue_number: int | None = None
    pr_number: int | None = None
    branch: str | None = None
    batch_id: str | None = None
    worktree_path: str | None = None
    transcript_path: str | None = None
    message_count: int = 0
    last_activity_mtime: float = 0.0
    children: list[AgentNode] = []


class PipelineState(BaseModel):
    """Snapshot of the entire Maestro pipeline at a point in time.

    Aggregated by the background poller and served to the dashboard UI.
    ``polled_at`` is a UNIX timestamp — compare with ``time.time()`` to know
    how stale the data is.
    """

    active_label: str | None
    issues_open: int
    prs_open: int
    agents: list[AgentNode]
    alerts: list[str] = []
    polled_at: float


class TaskFile(BaseModel):
    """Parsed content of a ``.agent-task`` file from a worktree.

    Every field maps 1-to-1 with a ``KEY=value`` line in the task file.
    Unknown keys are silently ignored. All fields are optional to ensure
    graceful handling of missing or malformed task files.
    """

    task: str | None = None
    gh_repo: str | None = None
    issue_number: int | None = None
    pr_number: int | None = None
    branch: str | None = None
    worktree: str | None = None
    role: str | None = None
    base: str | None = None
    batch_id: str | None = None
    closes_issues: list[int] = []
    spawn_sub_agents: bool = False
    spawn_mode: str | None = None
    merge_after: str | None = None
    attempt_n: int = 0
    required_output: str | None = None
    on_block: str | None = None
