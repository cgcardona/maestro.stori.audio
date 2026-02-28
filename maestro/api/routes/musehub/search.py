"""Muse Hub search route handlers.

Endpoint summary (cross-repo global search):
  GET /musehub/search?q={query}&mode={mode}&page={page}&page_size={page_size}
    — search commit messages across all public repos, results grouped by repo.

Endpoint summary (in-repo search):
  GET /api/v1/musehub/repos/{repo_id}/search — search commits by mode

The global search endpoint requires a valid JWT Bearer token so unauthenticated
callers cannot enumerate commit messages from public repos without identity.

Content negotiation:
  Accept: application/json  (default) — returns JSON.
  The UI page at GET /musehub/ui/search serves the browser-readable shell.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import GlobalSearchResult, SearchResponse
from maestro.services import musehub_repository, musehub_search

logger = logging.getLogger(__name__)

router = APIRouter()

_GLOBAL_VALID_MODES = frozenset({"keyword", "pattern"})
_REPO_VALID_MODES = frozenset({"property", "ask", "keyword", "pattern"})


@router.get(
    "/search",
    response_model=GlobalSearchResult,
    summary="Global cross-repo search across all public Muse Hub repos",
)
async def global_search(
    q: str = Query(..., min_length=1, max_length=500, description="Search query string"),
    mode: str = Query("keyword", description="Search mode: 'keyword' or 'pattern'"),
    page: int = Query(1, ge=1, description="1-based page number for repo-group pagination"),
    page_size: int = Query(10, ge=1, le=50, description="Number of repo groups per page"),
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
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


@router.get(
    "/repos/{repo_id}/search",
    response_model=SearchResponse,
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
    _: TokenClaims = Depends(require_valid_token),
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
