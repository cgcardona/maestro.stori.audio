"""Muse Hub stash UI route handler.

Endpoint summary:
  GET /musehub/ui/{owner}/{repo_slug}/stash — HTML stash list with pop/apply/drop actions
                                               JSON alternate via content negotiation

The HTML shell returns immediately without requiring a server-side JWT.
Client-side JS reads the JWT from localStorage and fetches stash entries from
``GET /api/v1/musehub/repos/{repo_id}/stash``.

The JSON alternate (``?format=json`` or ``Accept: application/json``) checks an
optional JWT from the ``Authorization`` header and returns the authenticated
user's stash list, or an empty list if no token is provided.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status
from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response as StarletteResponse

from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.api.routes.musehub.stash import StashListResponse, StashResponse, _row_to_stash_response
from maestro.auth.dependencies import TokenClaims, optional_token
from maestro.db import get_db
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui-stash"])

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _base_url(owner: str, repo_slug: str) -> str:
    """Return the canonical UI base URL for a repo."""
    return f"/musehub/ui/{owner}/{repo_slug}"


async def _resolve_repo(
    owner: str, repo_slug: str, db: AsyncSession
) -> tuple[str, str]:
    """Resolve owner+slug to repo_id; raise 404 if not found.

    Returns (repo_id, base_url) so callers can unpack both in one line.
    """
    row = await musehub_repository.get_repo_orm_by_owner_slug(db, owner, repo_slug)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Repo '{owner}/{repo_slug}' not found",
        )
    return str(row.repo_id), _base_url(owner, repo_slug)


async def _list_stash_for_user(
    db: AsyncSession,
    repo_id: str,
    user_id: str,
    page: int,
    page_size: int,
) -> StashListResponse:
    """Query the stash table for the given user and repo, returning a paginated response.

    This mirrors the DB query in ``stash.list_stash`` but is called from the UI
    route so the JSON alternate can return structured data without a second HTTP hop.
    """
    offset = (page - 1) * page_size

    count_result = await db.execute(
        text(
            "SELECT COUNT(*) FROM musehub_stash "
            "WHERE repo_id = :repo_id AND user_id = :user_id"
        ),
        {"repo_id": repo_id, "user_id": user_id},
    )
    total: int = count_result.scalar_one()

    rows_result = await db.execute(
        text(
            "SELECT id, repo_id, user_id, message, branch, created_at "
            "FROM musehub_stash "
            "WHERE repo_id = :repo_id AND user_id = :user_id "
            "ORDER BY created_at DESC "
            "LIMIT :limit OFFSET :offset"
        ),
        {"repo_id": repo_id, "user_id": user_id, "limit": page_size, "offset": offset},
    )
    rows = rows_result.mappings().all()
    items: list[StashResponse] = [_row_to_stash_response(row) for row in rows]
    return StashListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/{owner}/{repo_slug}/stash",
    summary="Muse Hub stash list page",
)
async def stash_list_page(
    request: Request,
    owner: str,
    repo_slug: str,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    format: str | None = Query(None, description="Force response format: 'json' or omit for HTML"),
    db: AsyncSession = Depends(get_db),
    token: TokenClaims | None = Depends(optional_token),
) -> StarletteResponse:
    """Render the stash list page or return structured stash data as JSON.

    Why this route exists: musicians need a browser-accessible view of their
    stash stack — the set of uncommitted working-tree snapshots captured by
    ``muse stash push``.  Each entry shows the stash ref (``stash@{N}``),
    source branch, message, timestamp, and file entry count.  Apply, Pop, and
    Drop actions are available with a confirmation step to prevent accidents.

    HTML (default): HTML shell rendered via Jinja2; client-side JS reads the
    JWT from localStorage and fetches stash entries from
    ``GET /api/v1/musehub/repos/{repo_id}/stash``.  The page prompts for auth
    if no token is found in localStorage.

    JSON (``Accept: application/json`` or ``?format=json``): returns the
    authenticated user's ``StashListResponse``, or an empty list when no JWT is
    provided.  Agents use this to enumerate a user's stash without navigating
    the browser UI.
    """
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)

    json_data: StashListResponse | None = None
    if token is not None:
        user_id = token.get("sub", "")
        if user_id:
            json_data = await _list_stash_for_user(db, repo_id, user_id, page, page_size)

    if json_data is None:
        json_data = StashListResponse(items=[], total=0, page=page, page_size=page_size)

    context: dict[str, object] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_url": base_url,
        "current_page": "stash",
        "breadcrumb_data": [
            {"label": owner, "url": f"/musehub/ui/{owner}"},
            {"label": repo_slug, "url": base_url},
            {"label": "stash", "url": ""},
        ],
    }

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/stash.html",
        context=context,
        templates=templates,
        json_data=json_data,
        format_param=format,
    )
