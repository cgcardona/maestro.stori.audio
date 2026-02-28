"""Muse Hub cross-repo search route handlers.

Endpoint summary:
  GET /musehub/search?q={query}&mode={mode}&page={page}&page_size={page_size}
    — search commit messages across all public repos, results grouped by repo.

The search endpoint requires a valid JWT Bearer token so unauthenticated
callers cannot enumerate commit messages from public repos without identity.

Content negotiation:
  Accept: application/json  (default) — returns GlobalSearchResult JSON.
  The UI page at GET /musehub/ui/search serves the browser-readable shell.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import GlobalSearchResult
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_MODES = frozenset({"keyword", "pattern"})


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
    effective_mode = mode if mode in _VALID_MODES else "keyword"
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
