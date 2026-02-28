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
