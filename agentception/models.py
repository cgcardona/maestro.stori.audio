"""Domain models for the AgentCeption dashboard.

These types are the shared contract between the background poller, the API
routes, and the frontend templates. Keep them flat — no nested Pydantic
models that reference external services.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, field_validator

#: Roles that can be assigned to a spawned agent.
VALID_ROLES: frozenset[str] = frozenset(
    {"python-developer", "database-architect", "pr-reviewer"}
)


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

    @classmethod
    def empty(cls) -> PipelineState:
        """Return a zero-value PipelineState for pre-first-tick callers.

        Routes and the API endpoint use this when ``get_state()`` returns
        ``None`` (i.e. the background poller hasn't completed its first tick).
        Callers should treat ``agents == []`` as "loading", not "no agents."
        """
        import time

        return cls(
            active_label=None,
            issues_open=0,
            prs_open=0,
            agents=[],
            alerts=[],
            polled_at=time.time(),
        )


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


class SpawnRequest(BaseModel):
    """Request body for ``POST /api/control/spawn``.

    Callers provide the issue number they want an agent to tackle and an
    optional role override. The endpoint verifies the issue is open and
    unclaimed before creating the worktree.
    """

    issue_number: int
    role: str = "python-developer"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        """Reject unknown roles early so errors surface at the boundary, not in git."""
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(VALID_ROLES)}, got {v!r}")
        return v


class SpawnResult(BaseModel):
    """Response for a successful ``POST /api/control/spawn``.

    Contains enough information for the user (or a future automation layer)
    to launch a Cursor Task pointed at the new worktree.
    ``agent_task`` is the raw text of the ``.agent-task`` file that was
    written — callers can display it or pass it directly to the Task tool.
    """

    spawned: int
    worktree: str
    branch: str
    agent_task: str


class RoleMeta(BaseModel):
    """Metadata for a managed role or cursor configuration file.

    Used by the Role Studio API (AC-301) to describe each file without
    returning full content — callers fetch content separately via GET /api/roles/{slug}.
    ``last_commit_sha`` and ``last_commit_message`` are empty strings when the
    file has never been committed (e.g. in tests with a temp directory).
    """

    slug: str
    path: str
    line_count: int
    mtime: float
    last_commit_sha: str
    last_commit_message: str


class RoleUpdateRequest(BaseModel):
    """Request body for ``PUT /api/roles/{slug}`` (Role Studio AC-301).

    Wraps the raw ``content`` string so FastAPI can validate and document the
    request body rather than accepting an untyped naked dict.
    """

    content: str


class RoleContent(BaseModel):
    """Response for ``GET /api/roles/{slug}`` — full file content with metadata.

    Returned by the Role Studio reader endpoint so the UI (AC-302/303) has
    both the Markdown source and the git provenance in one round-trip.
    """

    slug: str
    content: str
    meta: RoleMeta


class RoleUpdateResponse(BaseModel):
    """Response for ``PUT /api/roles/{slug}`` — diff and refreshed metadata.

    ``diff`` is the raw output of ``git diff HEAD -- <path>`` immediately after
    writing; an empty string means the written content was identical to what
    was already committed.
    """

    slug: str
    diff: str
    meta: RoleMeta
