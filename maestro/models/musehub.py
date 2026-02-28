"""Pydantic v2 request/response models for the Muse Hub API.

All wire-format fields use camelCase via CamelModel.  Python code uses
snake_case throughout; only serialisation to JSON uses camelCase.
"""
from __future__ import annotations

from datetime import datetime
from typing import NotRequired, TypedDict

from pydantic import Field

from maestro.models.base import CamelModel


# ── Sync protocol models ──────────────────────────────────────────────────────


class CommitInput(CamelModel):
    """A single commit record transferred in a push payload."""

    commit_id: str
    parent_ids: list[str] = Field(default_factory=list)
    message: str
    snapshot_id: str | None = None
    timestamp: datetime
    # Optional -- falls back to the JWT ``sub`` when absent
    author: str | None = None


class ObjectInput(CamelModel):
    """A binary object transferred in a push payload.

    Content is base64-encoded.  For MVP, objects up to ~1 MB are fine; larger
    files will require pre-signed URL upload in a future release.
    """

    object_id: str = Field(..., description="Content-addressed ID, e.g. 'sha256:abc...'")
    path: str = Field(..., description="Relative path hint, e.g. 'tracks/jazz_4b.mid'")
    content_b64: str = Field(..., description="Base64-encoded binary content")


class PushRequest(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/push."""

    branch: str
    head_commit_id: str
    commits: list[CommitInput] = Field(default_factory=list)
    objects: list[ObjectInput] = Field(default_factory=list)
    # Set true to allow non-fast-forward updates (overwrites remote head)
    force: bool = False


class PushResponse(CamelModel):
    """Response for POST /musehub/repos/{repo_id}/push."""

    ok: bool
    remote_head: str


class PullRequest(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/pull."""

    branch: str
    # Commit IDs the client already has -- missing ones will be returned
    have_commits: list[str] = Field(default_factory=list)
    # Object IDs the client already has -- missing ones will be returned
    have_objects: list[str] = Field(default_factory=list)


class ObjectResponse(CamelModel):
    """A binary object returned in a pull response."""

    object_id: str
    path: str
    content_b64: str


class PullResponse(CamelModel):
    """Response for POST /musehub/repos/{repo_id}/pull."""

    commits: list[CommitResponse]
    objects: list[ObjectResponse]
    remote_head: str | None


# ── Request models ────────────────────────────────────────────────────────────


class CreateRepoRequest(CamelModel):
    """Body for POST /musehub/repos.

    ``owner`` is the URL-visible username that appears in /{owner}/{slug} paths.
    ``slug`` is auto-generated from ``name`` — lowercase, hyphens, 1–64 chars.
    """

    name: str = Field(..., min_length=1, max_length=255, description="Repo name")
    owner: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9]([a-z0-9\-]{0,62}[a-z0-9])?$",
        description="URL-safe owner username (lowercase alphanumeric + hyphens, no leading/trailing hyphens)",
    )
    visibility: str = Field("private", pattern="^(public|private)$")
    description: str = Field("", description="Short description shown on the explore page")
    tags: list[str] = Field(
        default_factory=list,
        description="Free-form tags -- genre, key, instrumentation (e.g. 'jazz', 'F# minor', 'bass')",
    )
    key_signature: str | None = Field(None, max_length=50, description="Musical key (e.g. 'C major', 'F# minor')")
    tempo_bpm: int | None = Field(None, ge=20, le=300, description="Tempo in BPM")


# ── Response models ───────────────────────────────────────────────────────────


class RepoResponse(CamelModel):
    """Wire representation of a Muse Hub repo.

    ``owner`` and ``slug`` together form the canonical /{owner}/{slug} URL scheme.
    ``repo_id`` is the internal UUID primary key — never exposed in external URLs.
    """

    repo_id: str
    name: str
    owner: str
    slug: str
    visibility: str
    owner_user_id: str
    clone_url: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    key_signature: str | None = None
    tempo_bpm: int | None = None
    created_at: datetime


class BranchResponse(CamelModel):
    """Wire representation of a branch pointer."""

    branch_id: str
    name: str
    head_commit_id: str | None = None


class CommitResponse(CamelModel):
    """Wire representation of a pushed commit."""

    commit_id: str
    branch: str
    parent_ids: list[str]
    message: str
    author: str
    timestamp: datetime
    snapshot_id: str | None = None


class BranchListResponse(CamelModel):
    """Paginated list of branches."""

    branches: list[BranchResponse]


class CommitListResponse(CamelModel):
    """Paginated list of commits (newest first)."""

    commits: list[CommitResponse]
    total: int


# ── Issue models ───────────────────────────────────────────────────────────────


class IssueCreate(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/issues."""

    title: str = Field(..., min_length=1, max_length=500, description="Issue title")
    body: str = Field("", description="Issue description (Markdown)")
    labels: list[str] = Field(default_factory=list, description="Free-form label strings")


class IssueResponse(CamelModel):
    """Wire representation of a Muse Hub issue."""

    issue_id: str
    number: int
    title: str
    body: str
    state: str
    labels: list[str]
    created_at: datetime


class IssueListResponse(CamelModel):
    """List of issues for a repo."""

    issues: list[IssueResponse]


# ── Pull request models ────────────────────────────────────────────────────────


class PRCreate(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/pull-requests."""

    title: str = Field(..., min_length=1, max_length=500, description="PR title")
    from_branch: str = Field(..., min_length=1, max_length=255, description="Source branch name")
    to_branch: str = Field(..., min_length=1, max_length=255, description="Target branch name")
    body: str = Field("", description="PR description (Markdown)")


class PRResponse(CamelModel):
    """Wire representation of a Muse Hub pull request."""

    pr_id: str
    title: str
    body: str
    state: str
    from_branch: str
    to_branch: str
    merge_commit_id: str | None = None
    created_at: datetime


class PRListResponse(CamelModel):
    """List of pull requests for a repo."""

    pull_requests: list[PRResponse]


class PRMergeRequest(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/pull-requests/{pr_id}/merge."""

    merge_strategy: str = Field(
        "merge_commit",
        pattern="^(merge_commit)$",
        description="Merge strategy -- only 'merge_commit' is supported at MVP",
    )


class PRMergeResponse(CamelModel):
    """Confirmation that a PR was merged."""

    merged: bool
    merge_commit_id: str


# ── Release models ────────────────────────────────────────────────────────────


class ReleaseCreate(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/releases.

    ``tag`` must be unique per repo (e.g. "v1.0", "v2.3.1").
    ``commit_id`` pins the release to a specific commit snapshot.
    """

    tag: str = Field(..., min_length=1, max_length=100, description="Version tag, e.g. 'v1.0'")
    title: str = Field(..., min_length=1, max_length=500, description="Release title")
    body: str = Field("", description="Release notes (Markdown)")
    commit_id: str | None = Field(None, description="Commit to pin this release to")


class ReleaseDownloadUrls(CamelModel):
    """Structured download package URLs for a release.

    Each field is either a URL string or None if the package is not available.
    ``midi_bundle`` is the full MIDI export (all tracks as a single .mid).
    ``stems`` is a zip of per-track MIDI stems.
    ``mp3`` is the full mix audio render.
    ``musicxml`` is the notation export in MusicXML format.
    ``metadata`` is a JSON file with tempo, key, and arrangement info.
    """

    midi_bundle: str | None = None
    stems: str | None = None
    mp3: str | None = None
    musicxml: str | None = None
    metadata: str | None = None


class ReleaseResponse(CamelModel):
    """Wire representation of a Muse Hub release."""

    release_id: str
    tag: str
    title: str
    body: str
    commit_id: str | None = None
    download_urls: ReleaseDownloadUrls
    created_at: datetime


class ReleaseListResponse(CamelModel):
    """List of releases for a repo (newest first)."""

    releases: list[ReleaseResponse]


# ── Credits models ────────────────────────────────────────────────────────────


class ContributorCredits(CamelModel):
    """Wire representation of a single contributor's credit record.

    Aggregated from commit history -- one record per unique author string.
    Contribution types are inferred from commit message keywords so that an
    agent or a human can understand each collaborator's role at a glance.
    """

    author: str
    session_count: int
    contribution_types: list[str]
    first_active: datetime
    last_active: datetime


class CreditsResponse(CamelModel):
    """Wire representation of the full credits roll for a repo.

    Returned by ``GET /api/v1/musehub/repos/{repo_id}/credits``.
    The ``sort`` field echoes back the sort order applied to the list.
    An empty ``contributors`` list means no commits have been pushed yet.
    """

    repo_id: str
    contributors: list[ContributorCredits]
    sort: str
    total_contributors: int


# ── Object metadata model ─────────────────────────────────────────────────────


class ObjectMetaResponse(CamelModel):
    """Wire representation of a stored artifact -- metadata only, no content bytes.

    Returned by GET /musehub/repos/{repo_id}/objects. Use the ``/content``
    sub-resource to download the raw bytes. The ``path`` field retains the
    client-supplied relative path hint (e.g. "tracks/jazz_4b.mid") and is
    the primary signal for choosing display treatment (.webp → img, .mid /
    .mp3 → audio/download).
    """

    object_id: str
    path: str
    size_bytes: int
    created_at: datetime


class ObjectMetaListResponse(CamelModel):
    """List of artifact metadata for a repo."""

    objects: list[ObjectMetaResponse]


# ── Timeline models ───────────────────────────────────────────────────────────


class TimelineCommitEvent(CamelModel):
    """A commit plotted as a point on the timeline.

    Every pushed commit becomes a commit event regardless of its message content.
    The ``commit_id`` is the canonical identifier for audio-preview lookup and
    deep-linking to the commit detail page.
    """

    event_type: str = "commit"
    commit_id: str
    branch: str
    message: str
    author: str
    timestamp: datetime
    parent_ids: list[str]


class TimelineEmotionEvent(CamelModel):
    """An emotion-vector data point overlaid on the timeline as a line chart.

    Emotion values are derived deterministically from the commit SHA so the
    timeline is always reproducible without external inference. Each field is
    in the range [0.0, 1.0]. Agents use these values to understand how the
    emotional character of the composition shifted over time.
    """

    event_type: str = "emotion"
    commit_id: str
    timestamp: datetime
    valence: float
    energy: float
    tension: float


class TimelineSectionEvent(CamelModel):
    """A detected section change plotted as a marker on the timeline.

    Section names are extracted from commit messages using keyword heuristics
    (e.g. "added chorus", "intro complete", "bridge removed"). The ``action``
    field is either ``"added"`` or ``"removed"``.
    """

    event_type: str = "section"
    commit_id: str
    timestamp: datetime
    section_name: str
    action: str


class TimelineTrackEvent(CamelModel):
    """A detected track addition or removal plotted as a marker on the timeline.

    Track changes are extracted from commit messages using keyword heuristics
    (e.g. "added bass", "removed keys", "new drums track"). The ``action``
    field is either ``"added"`` or ``"removed"``.
    """

    event_type: str = "track"
    commit_id: str
    timestamp: datetime
    track_name: str
    action: str


class TimelineResponse(CamelModel):
    """Chronological timeline of musical evolution for a repo.

    Contains four parallel event streams that the client renders as
    independently toggleable layers:
    - ``commits``: every pushed commit (always present)
    - ``emotion``: emotion-vector data points per commit (always present)
    - ``sections``: section change events derived from commit messages
    - ``tracks``: track add/remove events derived from commit messages

    Agent use case: call this endpoint to understand how a project evolved --
    when sections were introduced, when the emotional character shifted, and
    which instruments were added or removed over time.
    """

    commits: list[TimelineCommitEvent]
    emotion: list[TimelineEmotionEvent]
    sections: list[TimelineSectionEvent]
    tracks: list[TimelineTrackEvent]
    total_commits: int


# ── Divergence visualization models ───────────────────────────────────────────


class DivergenceDimensionResponse(CamelModel):
    """Wire representation of divergence scores for a single musical dimension.

    Mirrors :class:`maestro.services.musehub_divergence.MuseHubDimensionDivergence`
    for JSON serialization.  AI agents consume this to decide which dimension
    of a branch needs creative attention before merging.
    """

    dimension: str
    level: str
    score: float
    description: str
    branch_a_commits: int
    branch_b_commits: int


class DivergenceResponse(CamelModel):
    """Full musical divergence report between two Muse Hub branches.

    Returned by ``GET /musehub/repos/{repo_id}/divergence``.  Contains five
    per-dimension scores (melodic, harmonic, rhythmic, structural, dynamic)
    and an overall score computed as the mean of those five scores.

    The ``overall_score`` is in [0.0, 1.0]; multiply by 100 for a percentage.
    A score of 0.0 means identical, 1.0 means completely diverged.
    """

    repo_id: str
    branch_a: str
    branch_b: str
    common_ancestor: str | None
    dimensions: list[DivergenceDimensionResponse]
    overall_score: float
# ── Explore / Discover models ──────────────────────────────────────────────────


class ExploreRepoResult(CamelModel):
    """A public repo card shown on the explore/discover page.

    Extends RepoResponse with aggregated counts (star_count, commit_count)
    that are computed at query time for efficient pagination and sorting.
    These counts are read-only signals -- they are never persisted directly on
    the repo row to avoid write amplification on every push/star.

    ``owner`` and ``slug`` together form the /{owner}/{slug} canonical URL.
    """

    repo_id: str
    name: str
    owner: str
    slug: str
    owner_user_id: str
    description: str
    tags: list[str]
    key_signature: str | None
    tempo_bpm: int | None
    star_count: int
    commit_count: int
    created_at: datetime


# ── Profile models ────────────────────────────────────────────────────────────


class ProfileUpdateRequest(CamelModel):
    """Body for PUT /api/v1/musehub/users/{username}.

    All fields are optional -- send only the ones to change.
    """

    bio: str | None = Field(None, max_length=500, description="Short bio (Markdown supported)")
    avatar_url: str | None = Field(None, max_length=2048, description="Avatar image URL")
    pinned_repo_ids: list[str] | None = Field(
        None, max_length=6, description="Up to 6 repo_ids to pin on the profile page"
    )


class ProfileRepoSummary(CamelModel):
    """Compact repo summary shown on a user's profile page.

    Includes the last-activity timestamp derived from the most recent commit
    and a stub star_count (always 0 at MVP -- no star mechanism yet).
    ``owner`` and ``slug`` form the /{owner}/{slug} canonical URL for the repo card.
    """

    repo_id: str
    name: str
    owner: str
    slug: str
    visibility: str
    star_count: int
    last_activity_at: datetime | None
    created_at: datetime


class ExploreResponse(CamelModel):
    """Paginated response from GET /api/v1/musehub/discover/repos.

    ``total`` reflects the full filtered result set size -- not just the current
    page -- so clients can render pagination controls without a second query.
    """

    repos: list[ExploreRepoResult]
    total: int
    page: int
    page_size: int


class StarResponse(CamelModel):
    """Confirmation that a star was added or removed."""

    starred: bool
    star_count: int


class ContributionDay(CamelModel):
    """A single day in the contribution heatmap.

    ``date`` is ISO-8601 (YYYY-MM-DD). ``count`` is the number of commits
    authored on that day across all of the user's repos.
    """

    date: str
    count: int


class ProfileResponse(CamelModel):
    """Full wire representation of a Muse Hub user profile.

    Returned by GET /api/v1/musehub/users/{username}.
    ``repos`` contains only public repos when the caller is not the owner.
    ``contribution_graph`` is the last 52 weeks of daily commit activity.
    ``session_credits`` is the total number of commits across all repos
    (a proxy for creative session activity).
    """

    user_id: str
    username: str
    bio: str | None = None
    avatar_url: str | None = None
    pinned_repo_ids: list[str]
    repos: list[ProfileRepoSummary]
    contribution_graph: list[ContributionDay]
    session_credits: int
    created_at: datetime
    updated_at: datetime

# ── Cross-repo search models ───────────────────────────────────────────────────


class GlobalSearchCommitMatch(CamelModel):
    """A single commit that matched the search query in a cross-repo search.

    Consumers display ``repo_id`` / ``repo_name`` as the group header, then
    render ``commit_id``, ``message``, and ``author`` as the match row.
    Audio preview is surfaced via ``audio_object_id`` when an .mp3 or .ogg
    artifact is attached to the same repo.
    """

    commit_id: str
    message: str
    author: str
    branch: str
    timestamp: datetime
    repo_id: str
    repo_name: str
    repo_owner: str
    repo_visibility: str
    audio_object_id: str | None = None
# ── Webhook models ────────────────────────────────────────────────────────────

# Valid event types a subscriber may register for.
WEBHOOK_EVENT_TYPES: frozenset[str] = frozenset(
    [
        "push",
        "pull_request",
        "issue",
        "release",
        "branch",
        "tag",
        "session",
        "analysis",
    ]
)


class WebhookCreate(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/webhooks.

    ``events`` must be a non-empty subset of the valid event-type strings
    (push, pull_request, issue, release, branch, tag, session, analysis).
    ``secret`` is optional; when provided it is used to sign every delivery
    with HMAC-SHA256 in the ``X-MuseHub-Signature`` header.
    """

    url: str = Field(..., min_length=1, max_length=2048, description="HTTPS endpoint to deliver events to")
    events: list[str] = Field(..., min_length=1, description="Event types to subscribe to")
    secret: str = Field("", description="Optional HMAC-SHA256 signing secret")


class WebhookResponse(CamelModel):
    """Wire representation of a registered webhook subscription."""

    webhook_id: str
    repo_id: str
    url: str
    events: list[str]
    active: bool
    created_at: datetime


class WebhookListResponse(CamelModel):
    """List of webhook subscriptions for a repo."""

    webhooks: list[WebhookResponse]


class WebhookDeliveryResponse(CamelModel):
    """Wire representation of a single webhook delivery attempt."""

    delivery_id: str
    webhook_id: str
    event_type: str
    attempt: int
    success: bool
    response_status: int
    response_body: str
    delivered_at: datetime


class WebhookDeliveryListResponse(CamelModel):
    """Paginated list of delivery attempts for a webhook."""

    deliveries: list[WebhookDeliveryResponse]


# ── Webhook event payload TypedDicts ─────────────────────────────────────────
# These typed dicts are used as the payload argument to dispatch_event /
# dispatch_event_background, replacing dict[str, Any] at the service boundary.


class PushEventPayload(TypedDict):
    """Payload emitted when commits are pushed to a MuseHub repo.

    Used with event_type="push".
    """

    repoId: str
    branch: str
    headCommitId: str
    pushedBy: str
    commitCount: int


class IssueEventPayload(TypedDict):
    """Payload emitted when an issue is opened or closed.

    ``action`` is either ``"opened"`` or ``"closed"``.
    Used with event_type="issue".
    """

    repoId: str
    action: str
    issueId: str
    number: int
    title: str
    state: str


class PullRequestEventPayload(TypedDict):
    """Payload emitted when a PR is opened or merged.

    ``action`` is either ``"opened"`` or ``"merged"``.
    ``mergeCommitId`` is only present on the "merged" action.
    Used with event_type="pull_request".
    """

    repoId: str
    action: str
    prId: str
    title: str
    fromBranch: str
    toBranch: str
    state: str
    mergeCommitId: NotRequired[str]


# Union of all typed webhook event payloads.  The dispatcher accepts any of
# these; callers pass the specific TypedDict for their event type.
WebhookEventPayload = PushEventPayload | IssueEventPayload | PullRequestEventPayload

# ── Context models ────────────────────────────────────────────────────────────


class MuseHubContextCommitInfo(CamelModel):
    """Minimal commit metadata included in a MuseHub context document."""

    commit_id: str
    message: str
    author: str
    branch: str
    timestamp: datetime


class GlobalSearchRepoGroup(CamelModel):
    """All matching commits for a single repo, with repo-level metadata.

    Results are grouped by repo so consumers can render a collapsible section
    per repo (name, owner) and paginate within each group.

    ``repo_owner`` + ``repo_slug`` form the canonical /{owner}/{slug} UI URL.
    """

    repo_id: str
    repo_name: str
    repo_owner: str
    repo_slug: str
    repo_visibility: str
    matches: list[GlobalSearchCommitMatch]
    total_matches: int


class GlobalSearchResult(CamelModel):
    """Top-level response for GET /search?q={query}.

    ``groups`` contains one entry per public repo that had at least one
    matching commit.  ``total_repos`` is the count of repos searched, not just
    the repos with matches.  ``page`` / ``page_size`` enable offset pagination
    across groups.
    """

    query: str
    mode: str
    groups: list[GlobalSearchRepoGroup]
    total_repos_searched: int
    page: int
    page_size: int


class MuseHubContextHistoryEntry(CamelModel):
    """A single ancestor commit in the evolutionary history of the composition.

    History is built by walking parent_ids from the target commit.
    Entries are returned newest-first and limited to the last 5 ancestors.
    """

    commit_id: str
    message: str
    author: str
    timestamp: datetime
    active_tracks: list[str]


class MuseHubContextMusicalState(CamelModel):
    """Musical state at the target commit, derived from stored artifact paths.

    ``active_tracks`` is populated from object paths in the repo.
    All analytical fields (key, tempo, etc.) are None until Storpheus MIDI
    analysis is integrated -- agents should treat None as "unknown."
    """

    active_tracks: list[str]
    key: str | None = None
    mode: str | None = None
    tempo_bpm: int | None = None
    time_signature: str | None = None
    form: str | None = None
    emotion: str | None = None


class MuseHubContextResponse(CamelModel):
    """Human-readable and agent-consumable musical context document for a commit.

    Returned by ``GET /api/v1/musehub/repos/{repo_id}/context/{ref}``.

    This is the MuseHub equivalent of ``MuseContextResult`` -- built from
    the remote repo's commit graph and stored objects rather than the local
    ``.muse`` filesystem.  The structure deliberately mirrors ``MuseContextResult``
    so that agents consuming either source see the same schema.

    Fields:
        repo_id:        The hub repo identifier.
        current_branch: Branch name for the target commit.
        head_commit:    Metadata for the resolved commit (ref).
        musical_state:  Active tracks and any available musical dimensions.
        history:        Up to 5 ancestor commits, newest-first.
        missing_elements: Dimensions that could not be determined from stored data.
        suggestions:    Composer-facing hints about what to work on next.
    """

    repo_id: str
    current_branch: str
    head_commit: MuseHubContextCommitInfo
    musical_state: MuseHubContextMusicalState
    history: list[MuseHubContextHistoryEntry]
    missing_elements: list[str]
    suggestions: dict[str, str]


# ── In-repo search models ─────────────────────────────────────────────────────


class SearchCommitMatch(CamelModel):
    """A single commit returned by a search query.

    Carries enough metadata to render a result row and launch an audio preview.
    The ``score`` field is populated by keyword/recall modes (0–1 overlap ratio);
    property and grep modes always return 1.0.
    """

    commit_id: str
    branch: str
    message: str
    author: str
    timestamp: datetime
    score: float = Field(1.0, ge=0.0, le=1.0, description="Match score (0–1); always 1.0 for exact-match modes")
    match_source: str = Field("message", description="Where the match was found: 'message', 'branch', or 'property'")


class SearchResponse(CamelModel):
    """Response envelope for all four in-repo search modes.

    ``mode`` echoes back the requested search mode so clients can render
    mode-appropriate headers.  ``total_scanned`` is the number of commits
    examined before limit was applied; useful for indicating search depth.
    """

    mode: str
    query: str
    matches: list[SearchCommitMatch]
    total_scanned: int
    limit: int


# ── DAG graph models ───────────────────────────────────────────────────────────


class DagNode(CamelModel):
    """A single commit node in the repo's directed acyclic graph.

    Designed for consumption by interactive graph renderers. The ``is_head``
    flag marks the current HEAD commit across all branches. ``branch_labels``
    and ``tag_labels`` list all ref names pointing at this commit.
    """

    commit_id: str
    message: str
    author: str
    timestamp: datetime
    branch: str
    parent_ids: list[str]
    is_head: bool = False
    branch_labels: list[str] = Field(default_factory=list)
    tag_labels: list[str] = Field(default_factory=list)


class DagEdge(CamelModel):
    """A directed edge in the commit DAG.

    ``source`` is the child commit (the one that has the parent).
    ``target`` is the parent commit. This follows standard graph convention:
    edge flows from child → parent (newest to oldest).
    """

    source: str
    target: str


class DagGraphResponse(CamelModel):
    """Topologically sorted commit graph for a Muse Hub repo.

    ``nodes`` are ordered from oldest ancestor to newest commit (Kahn's
    algorithm). ``edges`` enumerate every parent→child relationship.
    Consumers can render this directly as a directed acyclic graph without
    further processing.

    Agent use case: an AI music agent can use this to identify which branches
    diverged from a common ancestor, find merge points, and reason about the
    project's compositional history.
    """

    nodes: list[DagNode]
    edges: list[DagEdge]
    head_commit_id: str | None = None


# ── Session models ─────────────────────────────────────────────────────────────


class SessionCreate(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/sessions.

    Sent by the CLI on ``muse session start`` to register a new session.
    ``started_at`` defaults to the server's current time when absent.
    """

    started_at: datetime | None = None
    participants: list[str] = Field(
        default_factory=list, description="Participant identifiers or display names"
    )
    intent: str = Field("", description="Free-text creative goal for this session")
    location: str = Field("", max_length=255, description="Studio or location label")
    is_active: bool = Field(True, description="True if the session is currently live")


class SessionStop(CamelModel):
    """Body for POST /musehub/repos/{repo_id}/sessions/{session_id}/stop.

    Sent by the CLI on ``muse session stop`` to mark a session as ended.
    """

    ended_at: datetime | None = None


class SessionResponse(CamelModel):
    """Wire representation of a single recording session.

    ``duration_seconds`` is derived from ``started_at`` and ``ended_at``;
    None when the session is still active (``ended_at`` is null).
    ``is_active`` is True while the session is open -- used by the Hub UI to
    render a live indicator.
    ``commits`` is the list of Muse commit IDs associated with this session;
    the UI uses ``len(commits)`` as the commit count badge.
    ``notes`` contains closing markdown notes authored after the session ends.
    """

    session_id: str
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    participants: list[str]
    commits: list[str] = Field(default_factory=list, description="Muse commit IDs recorded during this session")
    notes: str = Field("", description="Closing notes for the session (markdown)")
    intent: str
    location: str
    is_active: bool
    created_at: datetime


class SessionListResponse(CamelModel):
    """Paginated list of sessions for a repo (newest first)."""

    sessions: list[SessionResponse]
    total: int


class SimilarCommitResponse(CamelModel):
    """A single result from a MuseHub semantic similarity search.

    The score is cosine similarity in [0.0, 1.0] -- higher is more similar.
    Results are pre-sorted descending by score.
    """

    commit_id: str = Field(..., description="Commit SHA of the matching commit")
    repo_id: str = Field(..., description="UUID of the repo containing this commit")
    score: float = Field(..., ge=0.0, le=1.0, description="Cosine similarity score")
    branch: str = Field(..., description="Branch the commit lives on")
    author: str = Field(..., description="Commit author identifier")


class SimilarSearchResponse(CamelModel):
    """Response for GET /musehub/search/similar.

    Contains the query commit SHA and a ranked list of musically similar commits.
    Only public repos appear in results -- enforced server-side by Qdrant filter.
    """

    query_commit: str = Field(..., description="The commit SHA used as the search query")
    results: list[SimilarCommitResponse] = Field(
        default_factory=list,
        description="Ranked results, most similar first",
    )
