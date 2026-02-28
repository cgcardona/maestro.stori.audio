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

import logging

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from maestro.db import musehub_models as db
from maestro.models.musehub import (
    BranchResponse,
    CommitResponse,
    GlobalSearchCommitMatch,
    GlobalSearchRepoGroup,
    GlobalSearchResult,
    MuseHubContextCommitInfo,
    MuseHubContextHistoryEntry,
    MuseHubContextMusicalState,
    MuseHubContextResponse,
    ObjectMetaResponse,
    RepoResponse,
)

logger = logging.getLogger(__name__)


def _repo_clone_url(repo_id: str) -> str:
    """Derive a deterministic clone URL from the repo ID.

    The URL format is intentionally simple for MVP; it will be parameterised
    by ``settings.musehub_base_url`` once that setting is introduced.
    """
    return f"/musehub/repos/{repo_id}"


def _to_repo_response(row: db.MusehubRepo) -> RepoResponse:
    return RepoResponse(
        repo_id=row.repo_id,
        name=row.name,
        visibility=row.visibility,
        owner_user_id=row.owner_user_id,
        clone_url=_repo_clone_url(row.repo_id),
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
    visibility: str,
    owner_user_id: str,
) -> RepoResponse:
    """Persist a new remote repo and return its wire representation."""
    repo = db.MusehubRepo(name=name, visibility=visibility, owner_user_id=owner_user_id)
    session.add(repo)
    await session.flush()  # populate default columns before reading
    await session.refresh(repo)
    logger.info("✅ Created Muse Hub repo %s (%s) for user %s", repo.repo_id, name, owner_user_id)
    return _to_repo_response(repo)


async def get_repo(session: AsyncSession, repo_id: str) -> RepoResponse | None:
    """Return repo metadata, or None if not found."""
    result = await session.get(db.MusehubRepo, repo_id)
    if result is None:
        return None
    return _to_repo_response(result)


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

    An audio preview object ID is attached when the repo contains any .mp3
    or .ogg artifact — the first one found by path ordering is used.

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

    # ── 5. Resolve audio preview objects (one per repo, first .mp3/.ogg) ────
    audio_map: dict[str, str] = {}
    for rid in groups_map:
        audio_stmt = (
            select(db.MusehubObject.object_id)
            .where(
                db.MusehubObject.repo_id == rid,
                or_(
                    db.MusehubObject.path.like("%.mp3"),
                    db.MusehubObject.path.like("%.ogg"),
                    db.MusehubObject.path.like("%.wav"),
                ),
            )
            .order_by(db.MusehubObject.path)
            .limit(1)
        )
        audio_row = (await session.execute(audio_stmt)).scalar_one_or_none()
        if audio_row is not None:
            audio_map[rid] = audio_row

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
