
"""MuseHub search route handlers.

Endpoints:
  GET /musehub/search/similar?commit={sha}&limit=10
    — Cross-repo vector similarity search. Returns the N most musically
      similar public commits to the given SHA using Qdrant cosine distance.

  GET /musehub/repos/{repo_id}/search?q={q}&mode={mode}
    — In-repo commit search with four modes:
        property  — filter by musical properties (harmony, rhythm, etc.)
        ask       — natural-language query (keyword extraction + overlap scoring)
        keyword   — keyword/phrase overlap scored search
        pattern   — substring pattern match against message and branch name

Authentication: JWT Bearer token required (inherited from musehub router).

"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, optional_token, require_valid_token
from maestro.config import settings
from maestro.db import get_db

from maestro.db import musehub_models as db
from maestro.models.musehub import GlobalSearchResult, SearchResponse, SimilarCommitResponse, SimilarSearchResponse

from maestro.services import musehub_repository, musehub_search
from maestro.services.musehub_embeddings import compute_embedding
from maestro.services.musehub_qdrant import MusehubQdrantClient

logger = logging.getLogger(__name__)

router = APIRouter()


# Module-level singleton — one client per process, collection ensured on first use.
_qdrant_client: MusehubQdrantClient | None = None

_VALID_MODES = frozenset({"property", "ask", "keyword", "pattern"})

_GLOBAL_VALID_MODES = frozenset({"keyword", "pattern"})
_REPO_VALID_MODES = frozenset({"property", "ask", "keyword", "pattern"})


@router.get(
    "/search",
    response_model=GlobalSearchResult,
    operation_id="globalSearch",
    summary="Global cross-repo search across all public Muse Hub repos",
)
async def global_search(
    q: str = Query(..., min_length=1, max_length=500, description="Search query string"),
    mode: str = Query("keyword", description="Search mode: 'keyword' or 'pattern'"),
    page: int = Query(1, ge=1, description="1-based page number for repo-group pagination"),
    page_size: int = Query(10, ge=1, le=50, description="Number of repo groups per page"),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims | None = Depends(optional_token),
) -> GlobalSearchResult:
    """Search commit messages across all public Muse Hub repos.

    Results are grouped by repo — each group contains up to 20 matching
    commits ordered newest-first with repo-level metadata (name, owner).

    Only ``visibility='public'`` repos are searched.  Private repos are
    excluded at the persistence layer regardless of caller identity.

    Pagination applies to repo-groups: ``page=1&page_size=10`` returns the
    first 10 repos that had at least one match.

    Supported search modes:
    - ``keyword``: OR-match whitespace-split terms against commit messages and
      repo names (case-insensitive).
    - ``pattern``: raw SQL LIKE pattern applied to commit messages only.
      Use ``%`` as wildcard (e.g. ``q=%minor%``).

    Content negotiation: this endpoint always returns JSON.  The companion
    HTML page at ``GET /musehub/ui/search`` renders the browser UI shell.
    """
    effective_mode = mode if mode in _GLOBAL_VALID_MODES else "keyword"
    if effective_mode != mode:
        logger.warning("⚠️ Unknown search mode %r — falling back to 'keyword'", mode)

    result = await musehub_repository.global_search(
        db,
        query=q,
        mode=effective_mode,
        page=page,
        page_size=page_size,
    )
    logger.info(
        "✅ Global search q=%r mode=%s page=%d → %d repo groups",
        q,
        effective_mode,
        page,
        len(result.groups),
    )
    return result



def _get_qdrant_client() -> MusehubQdrantClient:
    """Return the process-level Qdrant client, creating it on first call."""
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = MusehubQdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
        )
        _qdrant_client.ensure_collection()
    return _qdrant_client


@router.get(
    "/search/similar",
    response_model=SimilarSearchResponse,
    operation_id="searchSimilar",
    summary="Find musically similar commits across public repos",
)
async def search_similar(
    commit: str = Query(..., description="Commit SHA to use as the similarity query"),
    limit: int = Query(10, ge=1, le=50, description="Maximum number of results"),
    db_session: AsyncSession = Depends(get_db),
    _: TokenClaims | None = Depends(optional_token),
) -> SimilarSearchResponse:
    """Return the N most musically similar public commits to the given commit SHA.

    Resolves the query commit from Postgres to obtain its message (which encodes
    musical metadata), computes its embedding, then queries Qdrant for the closest
    vectors. Only commits from public repos appear in results — visibility is
    enforced server-side by Qdrant payload filtering.

    Raises:
        404: If the commit SHA is not found in the Muse Hub.
        503: If Qdrant is unavailable.
    """
    stmt = select(db.MusehubCommit).where(db.MusehubCommit.commit_id == commit)
    row = (await db_session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Commit '{commit}' not found in Muse Hub.",
        )

    query_vector = compute_embedding(row.message)

    try:
        client = _get_qdrant_client()
        raw_results = await asyncio.to_thread(
            client.search_similar,
            query_vector=query_vector,
            limit=limit,
            public_only=True,
            exclude_commit_id=commit,
        )
    except Exception as exc:
        logger.error("❌ Qdrant search failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Similarity search is temporarily unavailable.",
        ) from exc

    results = [
        SimilarCommitResponse(
            commit_id=r.commit_id,
            repo_id=r.repo_id,
            score=r.score,
            branch=r.branch,
            author=r.author,
        )
        for r in raw_results
    ]

    logger.info(
        "✅ Similarity search for commit=%s returned %d results",
        commit,
        len(results),
    )
    return SimilarSearchResponse(query_commit=commit, results=results)


@router.get(
    "/repos/{repo_id}/search",
    response_model=SearchResponse,
    operation_id="searchRepo",
    summary="Search Muse repo commits",
)
async def search_repo(
    repo_id: str,
    q: str = Query("", description="Search query — interpreted by the selected mode"),
    mode: str = Query("keyword", description="Search mode: property | ask | keyword | pattern"),
    harmony: str | None = Query(None, description="[property mode] Harmony filter"),
    rhythm: str | None = Query(None, description="[property mode] Rhythm filter"),
    melody: str | None = Query(None, description="[property mode] Melody filter"),
    structure: str | None = Query(None, description="[property mode] Structure filter"),
    dynamic: str | None = Query(None, description="[property mode] Dynamics filter"),
    emotion: str | None = Query(None, description="[property mode] Emotion filter"),
    since: datetime | None = Query(None, description="Only include commits on or after this ISO datetime"),
    until: datetime | None = Query(None, description="Only include commits on or before this ISO datetime"),
    limit: int = Query(20, ge=1, le=200, description="Maximum results to return"),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> SearchResponse:
    """Search commit history using one of four musical search modes.

    The ``mode`` parameter selects the search algorithm:

    - **property** — filter commits by musical properties using AND logic.
      Supply any of ``harmony``, ``rhythm``, ``melody``, ``structure``,
      ``dynamic``, ``emotion`` query params.  Accepts ``key=low-high`` range
      syntax (e.g. ``rhythm=tempo=120-130``).

    - **ask** — treat ``q`` as a natural-language question.  Stop-words are
      stripped; remaining keywords are scored by overlap coefficient.

    - **keyword** — score commits by keyword overlap against ``q``.
      Useful for exact term search (e.g. ``q=Fmin_jazz_bassline``).

    - **pattern** — case-insensitive substring match of ``q`` against commit
      messages and branch names.  No scoring; matched rows returned newest-first.

    Returns 404 if the repo does not exist.  Returns an empty ``matches`` list
    when no commits satisfy the criteria (not a 404).
    """
    if mode not in _REPO_VALID_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid mode '{mode}'. Must be one of: {sorted(_REPO_VALID_MODES)}",
        )

    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if mode == "property":
        return await musehub_search.search_by_property(
            db,
            repo_id=repo_id,
            harmony=harmony,
            rhythm=rhythm,
            melody=melody,
            structure=structure,
            dynamic=dynamic,
            emotion=emotion,
            since=since,
            until=until,
            limit=limit,
        )

    if mode == "ask":
        return await musehub_search.search_by_ask(
            db,
            repo_id=repo_id,
            question=q,
            since=since,
            until=until,
            limit=limit,
        )

    if mode == "keyword":
        return await musehub_search.search_by_keyword(
            db,
            repo_id=repo_id,
            keyword=q,
            since=since,
            until=until,
            limit=limit,
        )

    return await musehub_search.search_by_pattern(
        db,
        repo_id=repo_id,
        pattern=q,
        since=since,
        until=until,
        limit=limit,
    )
