"""Muse Hub persistence adapter — single point of DB access for Hub entities.

This module is the ONLY place that touches the musehub_* tables.
Route handlers delegate here; no business logic lives in routes.

Boundary rules:
- Must NOT import state stores, SSE queues, or LLM clients.
- Must NOT import maestro.core.* modules.
- May import ORM models from maestro.db.musehub_models.
- May import Pydantic response models from maestro.models.musehub.
"""
from __future__ import annotations
from datetime import datetime, timezone

import logging
import re
from collections import deque

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from maestro.db import musehub_models as db
from maestro.models.musehub import (
    SessionListResponse,
    SessionResponse,
    BranchResponse,
    CommitResponse,
    GlobalSearchCommitMatch,
    GlobalSearchRepoGroup,
    GlobalSearchResult,
    DagEdge,
    DagGraphResponse,
    DagNode,
    MuseHubContextCommitInfo,
    MuseHubContextHistoryEntry,
    MuseHubContextMusicalState,
    MuseHubContextResponse,
    ObjectMetaResponse,
    RepoResponse,
    TimelineCommitEvent,
    TimelineEmotionEvent,
    TimelineResponse,
    TimelineSectionEvent,
    TimelineTrackEvent,
    TreeEntryResponse,
    TreeListResponse,
)

logger = logging.getLogger(__name__)


def _generate_slug(name: str) -> str:
    """Derive a URL-safe slug from a human-readable repo name.

    Rules: lowercase, non-alphanumeric chars collapsed to single hyphens,
    leading/trailing hyphens stripped, max 64 chars.  If the result is empty
    (e.g. name was all symbols) we fall back to "repo".
    """
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    slug = slug[:64].strip("-")
    return slug or "repo"


def _repo_clone_url(owner: str, slug: str) -> str:
    """Derive the canonical clone URL from owner and slug.

    Uses the /{owner}/{slug} scheme — no internal UUID exposed in external URLs.
    """
    return f"/{owner}/{slug}"


def _to_repo_response(row: db.MusehubRepo) -> RepoResponse:
    return RepoResponse(
        repo_id=row.repo_id,
        name=row.name,
        owner=row.owner,
        slug=row.slug,
        visibility=row.visibility,
        owner_user_id=row.owner_user_id,
        clone_url=_repo_clone_url(row.owner, row.slug),
        description=row.description,
        tags=list(row.tags or []),
        key_signature=row.key_signature,
        tempo_bpm=row.tempo_bpm,
        created_at=row.created_at,
    )


def _to_branch_response(row: db.MusehubBranch) -> BranchResponse:
    return BranchResponse(
        branch_id=row.branch_id,
        name=row.name,
        head_commit_id=row.head_commit_id,
    )


def _to_commit_response(row: db.MusehubCommit) -> CommitResponse:
    return CommitResponse(
        commit_id=row.commit_id,
        branch=row.branch,
        parent_ids=list(row.parent_ids or []),
        message=row.message,
        author=row.author,
        timestamp=row.timestamp,
        snapshot_id=row.snapshot_id,
    )


async def create_repo(
    session: AsyncSession,
    *,
    name: str,
    owner: str,
    visibility: str,
    owner_user_id: str,
    description: str = "",
    tags: list[str] | None = None,
    key_signature: str | None = None,
    tempo_bpm: int | None = None,
) -> RepoResponse:
    """Persist a new remote repo and return its wire representation.

    ``slug`` is auto-generated from ``name``.  The ``(owner, slug)`` pair must
    be unique — callers should catch ``IntegrityError`` and surface a 409.
    """
    slug = _generate_slug(name)
    repo = db.MusehubRepo(
        name=name,
        owner=owner,
        slug=slug,
        visibility=visibility,
        owner_user_id=owner_user_id,
        description=description,
        tags=tags or [],
        key_signature=key_signature,
        tempo_bpm=tempo_bpm,
    )
    session.add(repo)
    await session.flush()  # populate default columns before reading
    await session.refresh(repo)
    logger.info(
        "✅ Created Muse Hub repo %s (%s/%s) for user %s",
        repo.repo_id, owner, slug, owner_user_id,
    )
    return _to_repo_response(repo)


async def get_repo(session: AsyncSession, repo_id: str) -> RepoResponse | None:
    """Return repo metadata by internal UUID, or None if not found."""
    result = await session.get(db.MusehubRepo, repo_id)
    if result is None:
        return None
    return _to_repo_response(result)


async def get_repo_by_owner_slug(
    session: AsyncSession, owner: str, slug: str
) -> RepoResponse | None:
    """Return repo metadata by owner+slug canonical URL pair, or None if not found.

    This is the primary resolver for all external /{owner}/{slug} routes.
    """
    stmt = select(db.MusehubRepo).where(
        db.MusehubRepo.owner == owner,
        db.MusehubRepo.slug == slug,
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return None
    return _to_repo_response(row)


async def get_repo_orm_by_owner_slug(
    session: AsyncSession, owner: str, slug: str
) -> db.MusehubRepo | None:
    """Return the raw ORM repo row by owner+slug, or None if not found.

    Used internally when the route needs the repo_id for downstream calls.
    """
    stmt = select(db.MusehubRepo).where(
        db.MusehubRepo.owner == owner,
        db.MusehubRepo.slug == slug,
    )
    return (await session.execute(stmt)).scalars().first()


async def list_branches(session: AsyncSession, repo_id: str) -> list[BranchResponse]:
    """Return all branches for a repo, ordered by name."""
    stmt = (
        select(db.MusehubBranch)
        .where(db.MusehubBranch.repo_id == repo_id)
        .order_by(db.MusehubBranch.name)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_branch_response(r) for r in rows]


def _to_object_meta_response(row: db.MusehubObject) -> ObjectMetaResponse:
    return ObjectMetaResponse(
        object_id=row.object_id,
        path=row.path,
        size_bytes=row.size_bytes,
        created_at=row.created_at,
    )


async def get_commit(
    session: AsyncSession, repo_id: str, commit_id: str
) -> CommitResponse | None:
    """Return a single commit by ID, or None if not found in this repo."""
    stmt = (
        select(db.MusehubCommit)
        .where(
            db.MusehubCommit.repo_id == repo_id,
            db.MusehubCommit.commit_id == commit_id,
        )
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return None
    return _to_commit_response(row)


async def list_objects(
    session: AsyncSession, repo_id: str
) -> list[ObjectMetaResponse]:
    """Return all object metadata for a repo (no binary content), ordered by path."""
    stmt = (
        select(db.MusehubObject)
        .where(db.MusehubObject.repo_id == repo_id)
        .order_by(db.MusehubObject.path)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_object_meta_response(r) for r in rows]


async def get_object_row(
    session: AsyncSession, repo_id: str, object_id: str
) -> db.MusehubObject | None:
    """Return the raw ORM object row for content delivery, or None if not found.

    Route handlers use this to stream the file from ``disk_path``.
    """
    stmt = (
        select(db.MusehubObject)
        .where(
            db.MusehubObject.repo_id == repo_id,
            db.MusehubObject.object_id == object_id,
        )
    )
    return (await session.execute(stmt)).scalars().first()


async def get_object_by_path(
    session: AsyncSession, repo_id: str, path: str
) -> db.MusehubObject | None:
    """Return the most-recently-created object matching ``path`` in a repo.

    Used by the raw file endpoint to resolve a human-readable path
    (e.g. ``tracks/bass.mid``) to the stored artifact on disk.  When
    multiple objects share the same path (re-pushed content), the newest
    one wins — consistent with Git's ref semantics where HEAD always
    points at the latest version.

    Args:
        session: Active async DB session.
        repo_id: UUID of the target repo.
        path: Client-supplied relative file path, e.g. ``tracks/bass.mid``.

    Returns:
        The matching ORM row, or ``None`` if no object with that path exists.
    """
    stmt = (
        select(db.MusehubObject)
        .where(
            db.MusehubObject.repo_id == repo_id,
            db.MusehubObject.path == path,
        )
        .order_by(desc(db.MusehubObject.created_at))
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def list_commits(
    session: AsyncSession,
    repo_id: str,
    *,
    branch: str | None = None,
    limit: int = 50,
) -> tuple[list[CommitResponse], int]:
    """Return commits for a repo, newest first, optionally filtered by branch.

    Returns a tuple of (commits, total_count).
    """
    base = select(db.MusehubCommit).where(db.MusehubCommit.repo_id == repo_id)
    if branch:
        base = base.where(db.MusehubCommit.branch == branch)

    total_stmt = select(func.count()).select_from(base.subquery())
    total: int = (await session.execute(total_stmt)).scalar_one()

    rows_stmt = base.order_by(desc(db.MusehubCommit.timestamp)).limit(limit)
    rows = (await session.execute(rows_stmt)).scalars().all()
    return [_to_commit_response(r) for r in rows], total


# ── Section / track keyword heuristics ──────────────────────────────────────

_SECTION_KEYWORDS: list[str] = [
    "intro", "verse", "chorus", "bridge", "outro", "hook",
    "pre-chorus", "prechorus", "breakdown", "drop", "build",
    "refrain", "coda", "tag", "interlude",
]

_TRACK_KEYWORDS: list[str] = [
    "bass", "drums", "keys", "piano", "guitar", "synth", "pad",
    "lead", "vocals", "strings", "brass", "horn",
    "flute", "cello", "violin", "organ", "arp", "percussion",
    "kick", "snare", "hi-hat", "hihat", "clap", "melody",
]

_ADDED_VERBS = re.compile(
    r"\b(add(?:ed)?|new|introduce[ds]?|creat(?:ed)?|record(?:ed)?|layer(?:ed)?)\b",
    re.IGNORECASE,
)
_REMOVED_VERBS = re.compile(
    r"\b(remov(?:e[ds]?)?|delet(?:e[ds]?)?|drop(?:ped)?|cut|mute[ds]?)\b",
    re.IGNORECASE,
)


def _infer_action(message: str) -> str:
    """Return 'added' or 'removed' based on verb presence in the commit message."""
    if _REMOVED_VERBS.search(message):
        return "removed"
    return "added"


def _extract_section_events(row: db.MusehubCommit) -> list[TimelineSectionEvent]:
    """Extract zero or more section-change events from a commit message."""
    msg_lower = row.message.lower()
    events: list[TimelineSectionEvent] = []
    for keyword in _SECTION_KEYWORDS:
        if keyword in msg_lower:
            events.append(
                TimelineSectionEvent(
                    commit_id=row.commit_id,
                    timestamp=row.timestamp,
                    section_name=keyword,
                    action=_infer_action(row.message),
                )
            )
    return events


def _extract_track_events(row: db.MusehubCommit) -> list[TimelineTrackEvent]:
    """Extract zero or more track-change events from a commit message."""
    msg_lower = row.message.lower()
    events: list[TimelineTrackEvent] = []
    for keyword in _TRACK_KEYWORDS:
        if keyword in msg_lower:
            events.append(
                TimelineTrackEvent(
                    commit_id=row.commit_id,
                    timestamp=row.timestamp,
                    track_name=keyword,
                    action=_infer_action(row.message),
                )
            )
    return events


def _derive_emotion(row: db.MusehubCommit) -> TimelineEmotionEvent:
    """Derive a deterministic emotion vector from the commit SHA.

    Uses three non-overlapping byte windows of the SHA hex to produce
    valence, energy, and tension in [0.0, 1.0].  Deterministic so the
    timeline is always reproducible without external ML inference.
    """
    sha = row.commit_id
    # Pad short commit IDs (e.g. test fixtures) so indexing is safe.
    sha = sha.ljust(12, "0")
    valence = int(sha[0:4], 16) / 0xFFFF if all(c in "0123456789abcdefABCDEF" for c in sha[0:4]) else 0.5
    energy = int(sha[4:8], 16) / 0xFFFF if all(c in "0123456789abcdefABCDEF" for c in sha[4:8]) else 0.5
    tension = int(sha[8:12], 16) / 0xFFFF if all(c in "0123456789abcdefABCDEF" for c in sha[8:12]) else 0.5
    return TimelineEmotionEvent(
        commit_id=row.commit_id,
        timestamp=row.timestamp,
        valence=round(valence, 4),
        energy=round(energy, 4),
        tension=round(tension, 4),
    )


async def get_timeline_events(
    session: AsyncSession,
    repo_id: str,
    *,
    limit: int = 200,
) -> TimelineResponse:
    """Return a chronological timeline of musical evolution for a repo.

    Fetches up to ``limit`` commits (oldest-first for temporal rendering) and
    derives four event streams:
    - commits: every commit as a timeline marker
    - emotion: deterministic emotion vectors from commit SHAs
    - sections: section-change markers parsed from commit messages
    - tracks: track add/remove markers parsed from commit messages

    Callers must verify the repo exists before calling this function.
    Returns an empty timeline when the repo has no commits.
    """
    total_stmt = select(func.count()).where(db.MusehubCommit.repo_id == repo_id)
    total: int = (await session.execute(total_stmt)).scalar_one()

    rows_stmt = (
        select(db.MusehubCommit)
        .where(db.MusehubCommit.repo_id == repo_id)
        .order_by(db.MusehubCommit.timestamp)  # oldest-first for temporal rendering
        .limit(limit)
    )
    rows = (await session.execute(rows_stmt)).scalars().all()

    commit_events: list[TimelineCommitEvent] = []
    emotion_events: list[TimelineEmotionEvent] = []
    section_events: list[TimelineSectionEvent] = []
    track_events: list[TimelineTrackEvent] = []

    for row in rows:
        commit_events.append(
            TimelineCommitEvent(
                commit_id=row.commit_id,
                branch=row.branch,
                message=row.message,
                author=row.author,
                timestamp=row.timestamp,
                parent_ids=list(row.parent_ids or []),
            )
        )
        emotion_events.append(_derive_emotion(row))
        section_events.extend(_extract_section_events(row))
        track_events.extend(_extract_track_events(row))

    return TimelineResponse(
        commits=commit_events,
        emotion=emotion_events,
        sections=section_events,
        tracks=track_events,
        total_commits=total,
    )
async def global_search(
    session: AsyncSession,
    *,
    query: str,
    mode: str = "keyword",
    page: int = 1,
    page_size: int = 10,
) -> GlobalSearchResult:
    """Search commit messages across all public Muse Hub repos.

    Only ``visibility='public'`` repos are searched — private repos are never
    exposed regardless of caller identity.  This enforces the public-only
    contract at the persistence layer so no route handler can accidentally
    bypass it.

    ``mode`` controls matching strategy:
    - ``keyword``: OR-match of whitespace-split query terms against message and
      repo name using LIKE (case-insensitive via lower()).
    - ``pattern``: raw SQL LIKE pattern applied to commit message only.

    Results are grouped by repo and paginated by repo-group (``page_size``
    controls how many repo-groups per page).  Within each group, up to 20
    matching commits are returned newest-first.

    An audio preview object ID is attached when the repo contains any .mp3,
    .ogg, or .wav artifact — the first one found by path ordering is used.
    Audio previews are resolved in a single batched query across all matching
    repos (not N per-repo queries) to avoid the N+1 pattern.

    Args:
        session:   Active async DB session.
        query:     Raw search string from the user or agent.
        mode:      "keyword" or "pattern".  Defaults to "keyword".
        page:      1-based page number for repo-group pagination.
        page_size: Number of repo-groups per page (1–50).

    Returns:
        GlobalSearchResult with groups, pagination metadata, and counts.
    """
    # ── 1. Collect all public repos ─────────────────────────────────────────
    public_repos_stmt = (
        select(db.MusehubRepo)
        .where(db.MusehubRepo.visibility == "public")
        .order_by(db.MusehubRepo.created_at)
    )
    public_repo_rows = (await session.execute(public_repos_stmt)).scalars().all()
    total_repos_searched = len(public_repo_rows)

    if not public_repo_rows or not query.strip():
        return GlobalSearchResult(
            query=query,
            mode=mode,
            groups=[],
            total_repos_searched=total_repos_searched,
            page=page,
            page_size=page_size,
        )

    repo_ids = [r.repo_id for r in public_repo_rows]
    repo_map: dict[str, db.MusehubRepo] = {r.repo_id: r for r in public_repo_rows}

    # ── 2. Build commit filter predicate ────────────────────────────────────
    predicate: ColumnElement[bool]
    if mode == "pattern":
        predicate = db.MusehubCommit.message.like(query)
    else:
        # keyword: OR-match each whitespace-split term against message (lower)
        terms = [t for t in query.lower().split() if t]
        if not terms:
            return GlobalSearchResult(
                query=query,
                mode=mode,
                groups=[],
                total_repos_searched=total_repos_searched,
                page=page,
                page_size=page_size,
            )
        term_predicates = [
            or_(
                func.lower(db.MusehubCommit.message).contains(term),
                func.lower(db.MusehubRepo.name).contains(term),
            )
            for term in terms
        ]
        predicate = or_(*term_predicates)

    # ── 3. Query matching commits joined to their repo ───────────────────────
    commits_stmt = (
        select(db.MusehubCommit, db.MusehubRepo)
        .join(db.MusehubRepo, db.MusehubCommit.repo_id == db.MusehubRepo.repo_id)
        .where(
            db.MusehubCommit.repo_id.in_(repo_ids),
            predicate,
        )
        .order_by(desc(db.MusehubCommit.timestamp))
    )
    commit_pairs = (await session.execute(commits_stmt)).all()

    # ── 4. Group commits by repo ─────────────────────────────────────────────
    groups_map: dict[str, list[db.MusehubCommit]] = {}
    for commit_row, _repo_row in commit_pairs:
        groups_map.setdefault(commit_row.repo_id, []).append(commit_row)

    # ── 5. Resolve audio preview objects — single batched query (eliminates N+1) ──
    # Fetch all qualifying audio objects for every matching repo in one round-trip,
    # ordered by (repo_id, path) so we naturally encounter each repo's
    # alphabetically-first audio file first when iterating the result set.
    # Python deduplication (first-seen wins) replicates the previous LIMIT 1
    # per-repo semantics without issuing N separate queries.
    audio_map: dict[str, str] = {}
    if groups_map:
        audio_batch_stmt = (
            select(db.MusehubObject.repo_id, db.MusehubObject.object_id)
            .where(
                db.MusehubObject.repo_id.in_(list(groups_map.keys())),
                or_(
                    db.MusehubObject.path.like("%.mp3"),
                    db.MusehubObject.path.like("%.ogg"),
                    db.MusehubObject.path.like("%.wav"),
                ),
            )
            .order_by(db.MusehubObject.repo_id, db.MusehubObject.path)
        )
        audio_rows = (await session.execute(audio_batch_stmt)).all()
        for audio_row in audio_rows:
            if audio_row.repo_id not in audio_map:
                audio_map[audio_row.repo_id] = audio_row.object_id

    # ── 6. Paginate repo-groups ──────────────────────────────────────────────
    sorted_repo_ids = list(groups_map.keys())
    offset = (page - 1) * page_size
    page_repo_ids = sorted_repo_ids[offset : offset + page_size]

    groups: list[GlobalSearchRepoGroup] = []
    for rid in page_repo_ids:
        repo_row = repo_map[rid]
        all_matches = groups_map[rid]
        audio_oid = audio_map.get(rid)

        commit_matches = [
            GlobalSearchCommitMatch(
                commit_id=c.commit_id,
                message=c.message,
                author=c.author,
                branch=c.branch,
                timestamp=c.timestamp,
                repo_id=rid,
                repo_name=repo_row.name,
                repo_owner=repo_row.owner_user_id,
                repo_visibility=repo_row.visibility,
                audio_object_id=audio_oid,
            )
            for c in all_matches[:20]
        ]
        groups.append(
            GlobalSearchRepoGroup(
                repo_id=rid,
                repo_name=repo_row.name,
                repo_owner=repo_row.owner_user_id,
                repo_slug=repo_row.slug,
                repo_visibility=repo_row.visibility,
                matches=commit_matches,
                total_matches=len(all_matches),
            )
        )

    return GlobalSearchResult(
        query=query,
        mode=mode,
        groups=groups,
        total_repos_searched=total_repos_searched,
        page=page,
        page_size=page_size,
    )
async def list_commits_dag(
    session: AsyncSession,
    repo_id: str,
) -> DagGraphResponse:
    """Return the full commit graph for a repo as a topologically sorted DAG.

    Fetches every commit for the repo (no limit — required for correct DAG
    traversal). Applies Kahn's algorithm to produce a topological ordering
    from oldest ancestor to newest commit, which graph renderers can consume
    directly without additional sorting.

    Edges flow child → parent (source = child, target = parent) following the
    standard directed graph convention where arrows point toward ancestors.

    Branch head commits are identified by querying the branches table. The
    highest-timestamp commit across all branches is designated as HEAD for
    display purposes when no explicit HEAD ref exists.

    Agent use case: call this to reason about the project's branching topology,
    find common ancestors, or identify which branches contain a given commit.
    """
    # Fetch all commits for this repo
    stmt = select(db.MusehubCommit).where(db.MusehubCommit.repo_id == repo_id)
    all_rows = (await session.execute(stmt)).scalars().all()

    if not all_rows:
        return DagGraphResponse(nodes=[], edges=[], head_commit_id=None)

    # Build lookup map
    row_map: dict[str, db.MusehubCommit] = {r.commit_id: r for r in all_rows}

    # Fetch all branches to identify HEAD candidates and branch labels
    branch_stmt = select(db.MusehubBranch).where(db.MusehubBranch.repo_id == repo_id)
    branch_rows = (await session.execute(branch_stmt)).scalars().all()

    # Map commit_id → branch names pointing at it
    branch_label_map: dict[str, list[str]] = {}
    for br in branch_rows:
        if br.head_commit_id and br.head_commit_id in row_map:
            branch_label_map.setdefault(br.head_commit_id, []).append(br.name)

    # Identify HEAD: the branch head with the most recent timestamp, or the
    # most recent commit overall when no branches exist
    head_commit_id: str | None = None
    if branch_rows:
        latest_ts = None
        for br in branch_rows:
            if br.head_commit_id and br.head_commit_id in row_map:
                ts = row_map[br.head_commit_id].timestamp
                if latest_ts is None or ts > latest_ts:
                    latest_ts = ts
                    head_commit_id = br.head_commit_id
    if head_commit_id is None:
        head_commit_id = max(all_rows, key=lambda r: r.timestamp).commit_id

    # Kahn's topological sort (oldest → newest).
    # in_degree[c] = number of c's parents that are present in this repo's commit set.
    # Commits with in_degree == 0 are roots (no parents) — they enter the queue first,
    # producing a parent-before-child ordering (oldest ancestor → newest commit).
    in_degree: dict[str, int] = {r.commit_id: 0 for r in all_rows}
    # children_map[parent_id] = list of commit IDs whose parent_ids contains parent_id
    children_map: dict[str, list[str]] = {r.commit_id: [] for r in all_rows}

    edges: list[DagEdge] = []
    for row in all_rows:
        for parent_id in (row.parent_ids or []):
            if parent_id in row_map:
                edges.append(DagEdge(source=row.commit_id, target=parent_id))
                children_map.setdefault(parent_id, []).append(row.commit_id)
                in_degree[row.commit_id] += 1

    # Kahn's algorithm: start from commits with no parents (roots)
    queue: deque[str] = deque(
        cid for cid, deg in in_degree.items() if deg == 0
    )
    topo_order: list[str] = []

    while queue:
        cid = queue.popleft()
        topo_order.append(cid)
        for child_id in children_map.get(cid, []):
            in_degree[child_id] -= 1
            if in_degree[child_id] == 0:
                queue.append(child_id)

    # Handle cycles or disconnected commits (append remaining in timestamp order)
    remaining = set(row_map.keys()) - set(topo_order)
    if remaining:
        sorted_remaining = sorted(remaining, key=lambda c: row_map[c].timestamp)
        topo_order.extend(sorted_remaining)

    nodes: list[DagNode] = []
    for cid in topo_order:
        row = row_map[cid]
        nodes.append(
            DagNode(
                commit_id=row.commit_id,
                message=row.message,
                author=row.author,
                timestamp=row.timestamp,
                branch=row.branch,
                parent_ids=list(row.parent_ids or []),
                is_head=(row.commit_id == head_commit_id),
                branch_labels=branch_label_map.get(row.commit_id, []),
                tag_labels=[],
            )
        )

    logger.debug("✅ Built DAG for repo %s: %d nodes, %d edges", repo_id, len(nodes), len(edges))
    return DagGraphResponse(nodes=nodes, edges=edges, head_commit_id=head_commit_id)


# ---------------------------------------------------------------------------
# Context document builder
# ---------------------------------------------------------------------------

_MUSIC_FILE_EXTENSIONS = frozenset(
    {".mid", ".midi", ".mp3", ".wav", ".aiff", ".aif", ".flac"}
)

_CONTEXT_HISTORY_DEPTH = 5


def _extract_track_names_from_objects(objects: list[db.MusehubObject]) -> list[str]:
    """Derive human-readable track names from stored object paths.

    Files with recognised music extensions whose stems do not look like raw
    SHA-256 hashes are treated as track names.  The stem is lowercased and
    de-duplicated, matching the convention in ``muse_context._extract_track_names``.
    """
    import pathlib

    tracks: list[str] = []
    for obj in objects:
        p = pathlib.PurePosixPath(obj.path)
        if p.suffix.lower() in _MUSIC_FILE_EXTENSIONS:
            stem = p.stem.lower()
            if len(stem) == 64 and all(c in "0123456789abcdef" for c in stem):
                continue
            tracks.append(stem)
    return sorted(set(tracks))


async def _get_commit_by_id(
    session: AsyncSession, repo_id: str, commit_id: str
) -> db.MusehubCommit | None:
    """Fetch a raw MusehubCommit ORM row by (repo_id, commit_id)."""
    stmt = select(db.MusehubCommit).where(
        db.MusehubCommit.repo_id == repo_id,
        db.MusehubCommit.commit_id == commit_id,
    )
    return (await session.execute(stmt)).scalars().first()


async def _build_hub_history(
    session: AsyncSession,
    repo_id: str,
    start_commit: db.MusehubCommit,
    objects: list[db.MusehubObject],
    depth: int,
) -> list[MuseHubContextHistoryEntry]:
    """Walk the parent chain, returning up to *depth* ancestor entries.

    The *start_commit* (the context target) is NOT included — it is surfaced
    separately as ``head_commit`` in the result.  Entries are newest-first.
    The object list is reused across entries since we have no per-commit object
    index at this layer; active tracks reflect the overall repo's artifact set.
    """
    entries: list[MuseHubContextHistoryEntry] = []
    parent_ids: list[str] = list(start_commit.parent_ids or [])

    while parent_ids and len(entries) < depth:
        parent_id = parent_ids[0]
        commit = await _get_commit_by_id(session, repo_id, parent_id)
        if commit is None:
            logger.warning("⚠️ Hub history chain broken at %s", parent_id[:8])
            break
        entries.append(
            MuseHubContextHistoryEntry(
                commit_id=commit.commit_id,
                message=commit.message,
                author=commit.author,
                timestamp=commit.timestamp,
                active_tracks=_extract_track_names_from_objects(objects),
            )
        )
        parent_ids = list(commit.parent_ids or [])

    return entries


async def get_context_for_commit(
    session: AsyncSession,
    repo_id: str,
    ref: str,
) -> MuseHubContextResponse | None:
    """Build a musical context document for a MuseHub commit.

    Traverses the commit's parent chain (up to 5 ancestors) and derives active
    tracks from the repo's stored objects.  Musical dimensions (key, tempo,
    etc.) are always None until Storpheus MIDI analysis is integrated.

    Args:
        session:  Open async DB session. Read-only — no writes performed.
        repo_id:  Hub repo identifier.
        ref:      Target commit ID.  Must belong to this repo.

    Returns:
        ``MuseHubContextResponse`` ready for JSON serialisation, or None if the
        commit does not exist in this repo.

    The output is deterministic: for the same ``repo_id`` + ``ref``, the result
    is always identical, making it safe to cache.
    """
    commit = await _get_commit_by_id(session, repo_id, ref)
    if commit is None:
        return None

    raw_objects_stmt = select(db.MusehubObject).where(
        db.MusehubObject.repo_id == repo_id
    )
    raw_objects = (await session.execute(raw_objects_stmt)).scalars().all()

    active_tracks = _extract_track_names_from_objects(list(raw_objects))

    head_commit_info = MuseHubContextCommitInfo(
        commit_id=commit.commit_id,
        message=commit.message,
        author=commit.author,
        branch=commit.branch,
        timestamp=commit.timestamp,
    )

    musical_state = MuseHubContextMusicalState(active_tracks=active_tracks)

    history = await _build_hub_history(
        session, repo_id, commit, list(raw_objects), _CONTEXT_HISTORY_DEPTH
    )

    missing: list[str] = []
    if not active_tracks:
        missing.append("no music files found in repo")
    for dim in ("key", "tempo_bpm", "time_signature", "form", "emotion"):
        missing.append(dim)

    suggestions: dict[str, str] = {}
    if not active_tracks:
        suggestions["first_track"] = (
            "Push your first MIDI or audio file to populate the musical state."
        )
    else:
        suggestions["next_section"] = (
            f"Current tracks: {', '.join(active_tracks)}. "
            "Consider adding harmonic or melodic variation to develop the composition."
        )

    logger.info(
        "✅ Muse Hub context built for repo %s commit %s (tracks=%d)",
        repo_id[:8],
        ref[:8],
        len(active_tracks),
    )
    return MuseHubContextResponse(
        repo_id=repo_id,
        current_branch=commit.branch,
        head_commit=head_commit_info,
        musical_state=musical_state,
        history=history,
        missing_elements=missing,
        suggestions=suggestions,
    )


def _to_session_response(s: db.MusehubSession) -> SessionResponse:
    """Compute derived fields and return a SessionResponse."""
    duration: float | None = None
    if s.ended_at is not None:
        # Normalize to offset-naive UTC before subtraction (SQLite strips tz info on round-trip)
        ended = s.ended_at.replace(tzinfo=None) if s.ended_at.tzinfo else s.ended_at
        started = s.started_at.replace(tzinfo=None) if s.started_at.tzinfo else s.started_at
        duration = (ended - started).total_seconds()
    return SessionResponse(
        session_id=s.session_id,
        started_at=s.started_at,
        ended_at=s.ended_at,
        duration_seconds=duration,
        participants=s.participants or [],
        intent=s.intent,
        location=s.location,
        is_active=s.is_active,
        created_at=s.created_at,
    )


async def create_session(
    session: AsyncSession,
    repo_id: str,
    started_at: datetime | None,
    participants: list[str],
    intent: str,
    location: str,
) -> SessionResponse:
    """Create and persist a new recording session."""
    import uuid

    new_session = db.MusehubSession(
        session_id=str(uuid.uuid4()),
        repo_id=repo_id,
        started_at=started_at or datetime.now(timezone.utc),
        participants=participants,
        intent=intent,
        location=location,
        is_active=True,
    )
    session.add(new_session)
    await session.flush()
    return _to_session_response(new_session)


async def stop_session(
    session: AsyncSession,
    repo_id: str,
    session_id: str,
    ended_at: datetime | None,
) -> SessionResponse:
    """Mark a session as ended; idempotent if already stopped."""
    from sqlalchemy import select

    result = await session.execute(
        select(db.MusehubSession).where(
            db.MusehubSession.session_id == session_id,
            db.MusehubSession.repo_id == repo_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"session {session_id} not found")
    if row.is_active:
        row.ended_at = ended_at or datetime.now(timezone.utc)
        row.is_active = False
        await session.flush()
    return _to_session_response(row)


async def list_sessions(
    session: AsyncSession,
    repo_id: str,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[SessionResponse], int]:
    """Return sessions for a repo, newest first, with total count."""
    from sqlalchemy import func, select

    total_result = await session.execute(
        select(func.count(db.MusehubSession.session_id)).where(
            db.MusehubSession.repo_id == repo_id
        )
    )
    total = total_result.scalar_one()

    result = await session.execute(
        select(db.MusehubSession)
        .where(db.MusehubSession.repo_id == repo_id)
        .order_by(db.MusehubSession.is_active.desc(), db.MusehubSession.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = result.scalars().all()
    return [_to_session_response(s) for s in rows], total


async def get_session(
    session: AsyncSession,
    repo_id: str,
    session_id: str,
) -> SessionResponse | None:
    """Fetch a single session by id."""
    from sqlalchemy import select

    result = await session.execute(
        select(db.MusehubSession).where(
            db.MusehubSession.session_id == session_id,
            db.MusehubSession.repo_id == repo_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _to_session_response(row)


async def resolve_ref_for_tree(
    session: AsyncSession, repo_id: str, ref: str
) -> bool:
    """Return True if ref resolves to a known branch or commit in this repo.

    The ref can be:
    - A branch name (e.g. "main", "feature/groove") — validated via the
      musehub_branches table.
    - A commit ID prefix or full SHA — validated via musehub_commits.

    Returns False if the ref is unknown, which the caller should surface as
    a 404. This is a lightweight existence check; callers that need the full
    commit object should call ``get_commit()`` separately.
    """
    branch_stmt = select(db.MusehubBranch).where(
        db.MusehubBranch.repo_id == repo_id,
        db.MusehubBranch.name == ref,
    )
    branch_row = (await session.execute(branch_stmt)).scalars().first()
    if branch_row is not None:
        return True

    commit_stmt = select(db.MusehubCommit).where(
        db.MusehubCommit.repo_id == repo_id,
        db.MusehubCommit.commit_id == ref,
    )
    commit_row = (await session.execute(commit_stmt)).scalars().first()
    return commit_row is not None


async def list_tree(
    session: AsyncSession,
    repo_id: str,
    owner: str,
    repo_slug: str,
    ref: str,
    dir_path: str,
) -> TreeListResponse:
    """Build a directory listing for the tree browser.

    Reconstructs the repo's directory structure from all objects stored under
    ``repo_id``. The ``dir_path`` parameter acts as a path prefix filter:
    - Empty string → list the root directory.
    - "tracks" → list all entries directly under the "tracks/" prefix.

    Entries are grouped: directories first (alphabetically), then files
    (alphabetically). Directory size_bytes is None because computing the
    recursive sum is deferred to client-side rendering.

    Args:
        session:   Active async DB session.
        repo_id:   UUID of the target repo.
        owner:     Repo owner username (for response breadcrumbs).
        repo_slug: Repo slug (for response breadcrumbs).
        ref:       Branch name or commit SHA (for response breadcrumbs).
        dir_path:  Current directory prefix; empty string for repo root.

    Returns:
        TreeListResponse with entries sorted dirs-first, then files.
    """
    all_objects = await list_objects(session, repo_id)

    prefix = (dir_path.strip("/") + "/") if dir_path.strip("/") else ""
    seen_dirs: set[str] = set()
    dirs: list[TreeEntryResponse] = []
    files: list[TreeEntryResponse] = []

    for obj in all_objects:
        path = obj.path.lstrip("/")
        if not path.startswith(prefix):
            continue
        remainder = path[len(prefix):]
        if not remainder:
            continue
        slash_pos = remainder.find("/")
        if slash_pos == -1:
            # Direct file entry under this prefix
            files.append(
                TreeEntryResponse(
                    type="file",
                    name=remainder,
                    path=path,
                    size_bytes=obj.size_bytes,
                )
            )
        else:
            # Directory entry — take only the next path segment
            dir_name = remainder[:slash_pos]
            dir_full_path = prefix + dir_name
            if dir_name not in seen_dirs:
                seen_dirs.add(dir_name)
                dirs.append(
                    TreeEntryResponse(
                        type="dir",
                        name=dir_name,
                        path=dir_full_path,
                        size_bytes=None,
                    )
                )

    dirs.sort(key=lambda e: e.name)
    files.sort(key=lambda e: e.name)

    return TreeListResponse(
        owner=owner,
        repo_slug=repo_slug,
        ref=ref,
        dir_path=dir_path.strip("/"),
        entries=dirs + files,
    )
