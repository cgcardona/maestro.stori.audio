"""Muse Hub topics/tag browse API route handlers.

Endpoints:
  GET  /musehub/topics                     — list all topics with repo_count, sorted desc
  GET  /musehub/topics/{tag}/repos         — list public repos tagged with this topic
                                             (paginated, sortable by stars/updated)
  POST /musehub/repos/{repo_id}/topics     — set topics for a repo (replaces entire list,
                                             auth required)

Topics are free-form lowercase slugs stored in ``musehub_repos.tags`` as a JSON array.
The browse endpoints are unauthenticated (public repo discovery is zero-friction).
The set-topics endpoint requires a valid JWT and repo ownership.

Slug rules:
  - Characters: ``a-z0-9-`` only (validated before write)
  - Max 20 topics per repo

Agent use case:
  Call ``GET /topics`` to populate a tag cloud or filter bar.  Call
  ``GET /topics/{tag}/repos`` to show the topic detail page.  Call
  ``POST /repos/{repo_id}/topics`` from the repo settings panel (auth required).
"""
from __future__ import annotations

import logging
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Text, desc, func, outerjoin, select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, optional_token, require_valid_token
from maestro.db import get_db
from maestro.db import musehub_models as db
from maestro.models.musehub import ExploreRepoResult, RepoResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Slug validation: lowercase a-z, digits, hyphens only
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
_MAX_TOPICS_PER_REPO = 20
_VALID_SORTS: frozenset[str] = frozenset({"stars", "updated"})


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class TopicItem(BaseModel):
    """A single topic entry returned by GET /topics.

    ``name`` is the exact tag slug (e.g. ``"classical"``).
    ``repo_count`` is the number of public repos that carry this tag.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Topic slug (e.g. 'jazz', 'ambient')")
    repo_count: int = Field(..., ge=0, description="Number of public repos with this topic")


class TopicListResponse(BaseModel):
    """Response for GET /musehub/topics.

    Topics are sorted by ``repo_count`` descending so the most popular genres
    appear first.  Only topics that exist on at least one public repo are
    included — empty topics are not materialised.
    """

    topics: list[TopicItem]


class TopicReposResponse(BaseModel):
    """Paginated repo list for GET /musehub/topics/{tag}/repos.

    ``tag`` echoes the requested topic slug so clients don't need to retain it.
    ``total`` is the pre-pagination count; use it with ``page`` / ``page_size``
    to calculate the number of available pages.
    """

    tag: str
    repos: list[ExploreRepoResult]
    total: int
    page: int
    page_size: int


class SetTopicsRequest(BaseModel):
    """Body for POST /musehub/repos/{repo_id}/topics.

    Replaces the repo's topic list entirely — send ``[]`` to clear all topics.
    Each topic must match ``[a-z0-9-]+``; topics are deduplicated and
    normalised to lowercase before being stored.
    """

    topics: list[str] = Field(
        ...,
        max_length=_MAX_TOPICS_PER_REPO,
        description="Complete list of topic slugs (replaces current list)",
        examples=[["jazz", "piano", "ambient"]],
    )


class SetTopicsResponse(BaseModel):
    """Result of POST /musehub/repos/{repo_id}/topics."""

    repo_id: str
    topics: list[str] = Field(..., description="The stored topic list after update")


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@router.get(
    "/topics",
    response_model=TopicListResponse,
    operation_id="listTopics",
    summary="List all topics with repo counts (sorted by popularity)",
)
async def list_topics(
    db_session: AsyncSession = Depends(get_db),
    _: TokenClaims | None = Depends(optional_token),
) -> TopicListResponse:
    """Return every topic that appears on at least one public repo, with its repo count.

    Topics are aggregated from the ``musehub_repos.tags`` JSON array across all
    public repos.  The result is sorted by ``repo_count`` descending so popular
    genres appear first — suitable for rendering a tag cloud or filter sidebar.

    Only public repos contribute to counts.  Private repo tags are excluded
    entirely — callers cannot infer private repo existence from the response.

    Performance note: aggregation is done in Python over the tags column because
    JSON unnesting syntax differs between PostgreSQL (``json_array_elements_text``)
    and SQLite (used in tests).  At expected MuseHub scale this is negligible;
    a materialised view can be added later without changing the API contract.
    """
    rows = await db_session.execute(
        select(db.MusehubRepo.tags).where(db.MusehubRepo.visibility == "public")
    )
    counts: dict[str, int] = {}
    for (tags,) in rows:
        for tag in tags or []:
            t = str(tag).lower()
            counts[t] = counts.get(t, 0) + 1

    topics = [
        TopicItem(name=name, repo_count=count)
        for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    logger.info("✅ Topic list: %d distinct topics across public repos", len(topics))
    return TopicListResponse(topics=topics)


@router.get(
    "/topics/{tag}/repos",
    response_model=TopicReposResponse,
    operation_id="listReposByTopic",
    summary="List public repos tagged with a topic (paginated)",
)
async def list_repos_by_topic(
    tag: str,
    sort: str = Query(
        "stars",
        description="Sort order: 'stars' (most starred first) | 'updated' (most recently committed first)",
    ),
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(24, ge=1, le=100, description="Results per page"),
    db_session: AsyncSession = Depends(get_db),
    _: TokenClaims | None = Depends(optional_token),
) -> TopicReposResponse:
    """Return a paginated list of public repos tagged with the requested topic slug.

    Tag matching is case-insensitive and exact (not substring).  A repo appears
    in results only if ``tag`` is literally present in its tags array — not if it
    merely contains ``tag`` as a substring of another tag.

    Sort options:
    - ``stars``   — repos with the most MuseHub stars first (trending signal).
    - ``updated`` — repos with the most recent commit first (freshest content).

    Returns an empty ``repos`` list when the topic exists but no public repos
    carry it — this is not a 404.  Returns 422 when ``sort`` is invalid.
    """
    effective_sort: Literal["stars", "updated"] = (
        "stars" if sort not in _VALID_SORTS else sort  # type: ignore[assignment]
    )
    if effective_sort != sort:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid sort '{sort}'. Must be one of: {sorted(_VALID_SORTS)}",
        )

    tag_lower = tag.lower()
    offset = (max(page, 1) - 1) * page_size

    star_count_col = func.count(db.MusehubStar.star_id).label("star_count")
    latest_commit_col = func.max(db.MusehubCommit.timestamp).label("latest_commit")

    base_q = (
        select(db.MusehubRepo, star_count_col, latest_commit_col)
        .select_from(
            outerjoin(
                outerjoin(
                    db.MusehubRepo,
                    db.MusehubStar,
                    db.MusehubRepo.repo_id == db.MusehubStar.repo_id,
                ),
                db.MusehubCommit,
                db.MusehubRepo.repo_id == db.MusehubCommit.repo_id,
            )
        )
        .where(db.MusehubRepo.visibility == "public")
        .where(
            # Cross-engine compat: cast JSON tags to text and check for the tag.
            # We wrap with double-quote delimiters to prevent substring false positives,
            # e.g. "jazz" should not match "jazz-fusion".
            func.cast(db.MusehubRepo.tags, Text).ilike(f'%"{tag_lower}"%')
        )
        .group_by(db.MusehubRepo.repo_id)
    )

    # Count before pagination
    count_q = select(func.count()).select_from(base_q.subquery())
    total: int = (await db_session.execute(count_q)).scalar_one()

    # Apply sort
    if effective_sort == "stars":
        base_q = base_q.order_by(desc("star_count"), desc(db.MusehubRepo.created_at))
    else:  # "updated"
        base_q = base_q.order_by(desc("latest_commit"), desc(db.MusehubRepo.created_at))

    rows = (await db_session.execute(base_q.offset(offset).limit(page_size))).all()

    repos = [
        ExploreRepoResult(
            repo_id=row.MusehubRepo.repo_id,
            name=row.MusehubRepo.name,
            owner=row.MusehubRepo.owner,
            slug=row.MusehubRepo.slug,
            owner_user_id=row.MusehubRepo.owner_user_id,
            description=row.MusehubRepo.description,
            tags=list(row.MusehubRepo.tags or []),
            key_signature=row.MusehubRepo.key_signature,
            tempo_bpm=row.MusehubRepo.tempo_bpm,
            star_count=row.star_count or 0,
            commit_count=0,
            created_at=row.MusehubRepo.created_at,
        )
        for row in rows
    ]

    logger.info(
        "✅ Topic repos: tag=%r sort=%s page=%d → %d/%d repos",
        tag_lower,
        effective_sort,
        page,
        len(repos),
        total,
    )
    return TopicReposResponse(tag=tag_lower, repos=repos, total=total, page=page, page_size=page_size)


@router.post(
    "/repos/{repo_id}/topics",
    response_model=SetTopicsResponse,
    status_code=status.HTTP_200_OK,
    operation_id="setRepoTopics",
    summary="Set topics for a repo (replaces current list, auth required)",
)
async def set_repo_topics(
    repo_id: str,
    body: SetTopicsRequest,
    db_session: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> SetTopicsResponse:
    """Replace the topic list for a repo with the supplied slugs.

    The operation is idempotent — calling it twice with the same list produces
    the same result.  Topics are deduplicated and normalised to lowercase before
    storage.  Invalid slugs (non ``[a-z0-9-]``) cause a 422 error listing the
    offending values — the caller should sanitise on the client side.

    Raises:
        401: Missing or invalid JWT.
        403: Authenticated user is not the repo owner.
        404: Repo not found.
        422: A topic slug contains invalid characters, or more than
             ``_MAX_TOPICS_PER_REPO`` (20) topics were submitted.
    """
    user_id: str = claims.get("sub") or ""

    repo = await db_session.get(db.MusehubRepo, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.owner_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the repo owner may update topics",
        )

    # Normalise and deduplicate
    normalised: list[str] = []
    seen: set[str] = set()
    for raw in body.topics:
        slug = raw.strip().lower()
        if not slug:
            continue
        if not _SLUG_RE.match(slug):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid topic slug {raw!r}: must match [a-z0-9-]+",
            )
        if slug not in seen:
            seen.add(slug)
            normalised.append(slug)

    if len(normalised) > _MAX_TOPICS_PER_REPO:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Too many topics: max {_MAX_TOPICS_PER_REPO}, got {len(normalised)}",
        )

    repo.tags = normalised
    await db_session.commit()

    logger.info("✅ Topics set: repo=%s topics=%s", repo_id, normalised)
    return SetTopicsResponse(repo_id=repo_id, topics=normalised)
