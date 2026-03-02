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


class StaleClaim(BaseModel):
    """A GitHub issue with ``agent:wip`` label but no corresponding local worktree.

    Produced by :func:`~agentception.intelligence.guards.detect_stale_claims`
    and included in :class:`PipelineState` so the dashboard can surface a
    one-click "Clear Label" fix button for each stale claim.
    """

    issue_number: int
    issue_title: str
    worktree_path: str  # expected path that does not exist


class BoardIssue(BaseModel):
    """Lightweight issue summary for the overview board sidebar.

    Populated from ``ac_issues`` (Postgres) by the poller and carried in every
    SSE broadcast so the sidebar updates live without page reloads or HTMX
    polling.  Only fields needed for the board card are included; full issue
    detail is always available on GitHub.
    """

    number: int
    title: str
    state: str = "open"
    labels: list[str] = []
    claimed: bool = False
    phase_label: str | None = None
    last_synced_at: str | None = None


class PipelineState(BaseModel):
    """Snapshot of the entire Maestro pipeline at a point in time.

    Aggregated by the background poller and served to the dashboard UI.
    ``polled_at`` is a UNIX timestamp — compare with ``time.time()`` to know
    how stale the data is.
    ``stale_claims`` provides structured data for the "Clear Label" UI action;
    the same claims also appear as human-readable strings in ``alerts``.
    ``board_issues`` carries the unclaimed issues for the active phase so the
    sidebar updates via SSE without any extra requests.
    """

    active_label: str | None
    issues_open: int
    prs_open: int
    agents: list[AgentNode]
    alerts: list[str] = []
    stale_claims: list[StaleClaim] = []
    board_issues: list[BoardIssue] = []
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
            stale_claims=[],
            board_issues=[],
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


class AbModeConfig(BaseModel):
    """A/B mode configuration for role file experimentation (AC-504).

    When ``enabled`` is true the Engineering VP alternates between two role
    files for the ``target_role`` based on whether the BATCH_ID timestamp
    second is even (variant A) or odd (variant B).  This enables controlled
    experiments where successive batches see different role prompts so outcomes
    can be compared with everything else held constant.

    ``variant_a_file`` and ``variant_b_file`` are paths relative to the
    repository root (e.g. ``.cursor/roles/python-developer.md``).
    """

    enabled: bool = False
    target_role: str | None = None
    variant_a_file: str | None = None
    variant_b_file: str | None = None


class ProjectConfig(BaseModel):
    """A single project entry in ``pipeline-config.json``.

    Each project maps to a distinct GitHub repository and local workspace.
    The ``active_project`` field in :class:`PipelineConfig` selects which
    project the AgentCeption dashboard currently targets.

    ``worktrees_dir`` supports ``~`` expansion (e.g. ``~/.cursor/worktrees/maestro``).
    ``cursor_project_id`` is the Cursor project slug used to locate transcript files.
    """

    name: str
    gh_repo: str
    repo_dir: str
    worktrees_dir: str
    cursor_project_id: str
    active_labels_order: list[str] = []


class PipelineConfig(BaseModel):
    """Validated shape of ``.cursor/pipeline-config.json``.

    This is the single source of truth for pipeline allocation.  The CTO and
    Engineering VP role files read this model at the start of every loop/seed
    cycle.  The ``PUT /api/config`` route validates incoming bodies against
    this schema before persisting them to disk.

    ``projects`` lists all configured projects; ``active_project`` is the name
    of the currently active one.  When ``active_project`` is set, the dashboard
    targets the corresponding project's ``gh_repo``, ``repo_dir``, and
    ``worktrees_dir`` instead of the defaults in :class:`AgentCeptionSettings`.
    """

    max_eng_vps: int
    max_qa_vps: int
    pool_size_per_vp: int
    active_labels_order: list[str]
    ab_mode: AbModeConfig = AbModeConfig()
    projects: list[ProjectConfig] = []
    active_project: str | None = None


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


class SwitchProjectRequest(BaseModel):
    """Request body for ``POST /api/config/switch-project``.

    ``project_name`` must match the ``name`` field of one of the entries in
    ``PipelineConfig.projects``.  If no match is found the endpoint returns
    HTTP 404 rather than silently writing an invalid ``active_project`` value.
    """

    project_name: str


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


class RoleDiffRequest(BaseModel):
    """Request body for ``POST /api/roles/{slug}/diff`` (AC-303).

    ``content`` is the proposed file content to diff against the HEAD-committed
    version.  No file is written to disk — this is a pure preview operation.
    Using a POST body avoids URL-length limits for large managed files (e.g.
    PARALLEL_PR_REVIEW.md which exceeds Nginx's default 4 KB URI limit).
    """

    content: str


class RoleDiffResponse(BaseModel):
    """Response for ``POST /api/roles/{slug}/diff`` — diff of proposed vs HEAD.

    ``diff`` is a unified diff string comparing ``content`` against
    the HEAD-committed version.  An empty string means the proposed content is
    identical to the committed file.  No file is written to disk.
    """

    slug: str
    diff: str


class RoleCommitRequest(BaseModel):
    """Request body for ``POST /api/roles/{slug}/commit`` (AC-303).

    ``content`` is written to the managed file and then staged + committed in
    one atomic operation.  The commit message is generated automatically.
    """

    content: str


class RoleCommitResponse(BaseModel):
    """Response for ``POST /api/roles/{slug}/commit`` — resulting commit SHA.

    ``commit_sha`` is the full 40-character SHA of the newly created commit.
    ``message`` is the commit subject line that was used.
    """

    slug: str
    commit_sha: str
    message: str


class RoleVersionEntry(BaseModel):
    """A single entry in a role's version history (AC-503).

    Records the git SHA, human-readable version label, and UNIX timestamp of
    one committed change to the role file.  Entries are ordered chronologically
    (oldest first) inside ``RoleVersionInfo.history``.
    """

    sha: str
    label: str
    timestamp: int


class RoleVersionInfo(BaseModel):
    """Version tracking data for a single role slug (AC-503).

    ``current`` is the label of the most recently recorded version.  ``history``
    is the chronologically ordered list of all version entries (oldest first).
    An empty ``history`` means the slug has never been committed through the
    Role Studio commit endpoint.
    """

    current: str
    history: list[RoleVersionEntry]


class RoleVersionsResponse(BaseModel):
    """Response for ``GET /api/roles/{slug}/versions`` (AC-503).

    Returns structured version history for a single role slug so the Role
    Studio UI can display a timeline of changes and link each version to its
    git commit SHA.
    """

    slug: str
    versions: RoleVersionInfo


# ---------------------------------------------------------------------------
# Template export / import  (AC-602)
# ---------------------------------------------------------------------------


class TemplateExportRequest(BaseModel):
    """Request body for ``POST /api/templates/export``.

    ``name`` and ``version`` are embedded in the manifest inside the archive
    so that importers know what they are applying.
    """

    name: str
    version: str


class TemplateManifest(BaseModel):
    """Metadata record written as ``template-manifest.json`` inside the archive.

    Included in every exported template so importers can surface provenance
    without unpacking the whole tarball.
    """

    name: str
    version: str
    created_at: str
    gh_repo: str
    files: list[str]


class TemplateConflict(BaseModel):
    """A single file that already exists in the target repo's ``.cursor/`` directory.

    Surfaced by the import endpoint before any file is overwritten so the
    caller can decide whether to proceed.
    """

    path: str
    exists: bool


class TemplateImportResult(BaseModel):
    """Response for ``POST /api/templates/import``.

    ``extracted`` lists every file path that was written (relative to the
    target repo root).  ``conflicts`` lists files that already existed — they
    are still overwritten, but the caller is informed so the UI can display
    a warning banner.
    """

    name: str
    version: str
    extracted: list[str]
    conflicts: list[TemplateConflict]


class TemplateListEntry(BaseModel):
    """Summary of one previously exported template stored on disk.

    Represents a single ``.tar.gz`` archive in the templates store directory.
    ``size_bytes`` is the archive size (not uncompressed size).
    """

    filename: str
    name: str
    version: str
    created_at: str
    gh_repo: str
    size_bytes: int
