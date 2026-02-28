"""Muse Hub discover/explore API route handlers.

Endpoint summary:
  GET  /api/v1/musehub/discover/repos           — list public repos (no auth required)
  POST /api/v1/musehub/repos/{repo_id}/star     — star a repo (idempotent add, auth required)
  DELETE /api/v1/musehub/repos/{repo_id}/star   — unstar a repo (auth required)

The browse endpoint is intentionally unauthenticated so that anyone can discover
public compositions without creating an account — matching the HuggingFace model
hub philosophy where discovery is a zero-friction entry point.

Star/unstar requires a valid JWT so we can track which user starred which repo.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import ExploreResponse, StarResponse
from maestro.services import musehub_discover

logger = logging.getLogger(__name__)

# Two routers: one public (no auth dependency) and one authed (star/unstar).
# Both are exported and registered separately so the auth contract is explicit.
router = APIRouter()
star_router = APIRouter()

_VALID_SORTS = {"stars", "activity", "commits", "created"}


@router.get(
    "/musehub/discover/repos",
    response_model=ExploreResponse,
    operation_id="listPublicRepos",
    summary="Browse public Muse Hub repos with optional filters and sorting",
)
async def list_public_repos(
    genre: str | None = Query(None, description="Filter by genre tag (e.g. 'jazz', 'lo-fi')"),
    key: str | None = Query(None, description="Filter by key signature (e.g. 'F# minor')"),
    tempo_min: int | None = Query(None, ge=20, le=300, description="Minimum tempo in BPM"),
    tempo_max: int | None = Query(None, ge=20, le=300, description="Maximum tempo in BPM"),
    instrumentation: str | None = Query(
        None, description="Filter by instrument tag (e.g. 'bass', 'drums', 'keys')"
    ),
    sort: str = Query(
        "created",
        description="Sort order: 'stars' | 'activity' | 'commits' | 'created'",
    ),
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(24, ge=1, le=100, description="Results per page"),
    db: AsyncSession = Depends(get_db),
) -> ExploreResponse:
    """Return paginated public repos with optional music-semantic filters.

    No authentication required — public repos are discoverable by anyone.

    Content negotiation: this endpoint always returns JSON. The explore page HTML
    shell (``GET /musehub/ui/explore``) calls this endpoint from the browser.
    """
    if sort not in _VALID_SORTS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid sort '{sort}'. Must be one of: {', '.join(sorted(_VALID_SORTS))}",
        )

    return await musehub_discover.list_public_repos(
        db,
        genre=genre,
        key=key,
        tempo_min=tempo_min,
        tempo_max=tempo_max,
        instrumentation=instrumentation,
        sort=sort,  # type: ignore[arg-type]  # validated above via _VALID_SORTS check
        page=page,
        page_size=page_size,
    )


@star_router.post(
    "/musehub/repos/{repo_id}/star",
    response_model=StarResponse,
    status_code=status.HTTP_200_OK,
    operation_id="starRepo",
    summary="Star a public Muse Hub repo (idempotent)",
)
async def star_repo(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> StarResponse:
    """Add a star to a public repo on behalf of the authenticated user.

    Idempotent — starring an already-starred repo returns the current star count
    without creating a duplicate.  Use DELETE /star to remove a star.

    Raises 404 if the repo does not exist or is not public.
    """
    user_id: str = claims.get("sub") or ""
    try:
        result = await musehub_discover.star_repo(db, repo_id=repo_id, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await db.commit()
    return result


@star_router.delete(
    "/musehub/repos/{repo_id}/star",
    response_model=StarResponse,
    status_code=status.HTTP_200_OK,
    operation_id="unstarRepo",
    summary="Unstar a Muse Hub repo",
)
async def unstar_repo(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> StarResponse:
    """Remove a star from a repo on behalf of the authenticated user.

    Idempotent — unstarring a repo that was never starred is a no-op.
    """
    user_id: str = claims.get("sub") or ""
    result = await musehub_discover.unstar_repo(db, repo_id=repo_id, user_id=user_id)
    await db.commit()
    return result
