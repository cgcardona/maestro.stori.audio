"""Muse Hub in-repo search route handlers.

Endpoint summary:
  GET /api/v1/musehub/repos/{repo_id}/search — search commits by mode

Search modes (``?mode=`` query parameter):
  ``property``  — filter by musical properties (harmony, rhythm, melody, etc.)
  ``ask``       — natural-language query (keyword extraction + overlap scoring)
  ``keyword``   — keyword/phrase overlap scored search
  ``pattern``   — substring pattern match against message and branch name

All modes accept optional ``since`` / ``until`` date-range filters and a
``limit`` cap (default 20, max 200).  All modes return the same JSON shape
(:class:`~maestro.models.musehub.SearchResponse`) so the UI can use a
single result renderer.

Content negotiation: this endpoint always returns JSON. The HTML search page
at ``GET /musehub/ui/{repo_id}/search`` fetches this endpoint client-side.

Authentication: JWT Bearer token required (inherited from musehub router).
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import SearchResponse
from maestro.services import musehub_repository, musehub_search

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_MODES = frozenset({"property", "ask", "keyword", "pattern"})


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
    if mode not in _VALID_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid mode '{mode}'. Must be one of: {sorted(_VALID_MODES)}",
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

    # mode == "pattern"
    return await musehub_search.search_by_pattern(
        db,
        repo_id=repo_id,
        pattern=q,
        since=since,
        until=until,
        limit=limit,
    )
