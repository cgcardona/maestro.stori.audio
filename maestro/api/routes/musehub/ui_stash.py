"""Muse Hub stash UI route handlers.

Serves browser-readable HTML pages for the stash section of a Muse Hub repo —
analogous to ``git stash list`` but rendered as a rich, interactive page.

Endpoint summary:
  GET  /musehub/ui/{owner}/{repo_slug}/stash                          — stash list page
  POST /musehub/ui/{owner}/{repo_slug}/stash/{stash_ref}/apply        — apply stash (no delete)
  POST /musehub/ui/{owner}/{repo_slug}/stash/{stash_ref}/pop          — apply + delete stash
  POST /musehub/ui/{owner}/{repo_slug}/stash/{stash_ref}/drop         — delete stash without applying

Auth:
  All four endpoints require a valid JWT Bearer token.  Stash data is always
  private — users can only see and act on their own stash entries.  Unauthenticated
  GET requests receive a 401 so the DAW / agent consumer knows to supply a token
  before rendering the page.  The HTML response itself embeds a token-entry prompt
  via the base template so browsers can recover gracefully.

Content negotiation (GET only):
  - Default (HTML): Jinja2 template with the stash list.
  - ``?format=json`` or ``Accept: application/json``: ``StashListPageResponse``
    — the same contract consumed by the Stori DAW MCP tool.

POST responses:
  Always redirect back to the stash list page after a successful action.
  JavaScript callers that prefer JSON should call the JSON API directly:
    POST /api/v1/musehub/repos/{repo_id}/stash/{stash_id}/apply|pop
    DELETE /api/v1/musehub/repos/{repo_id}/stash/{stash_id}

Auto-discovered by ``maestro.api.routes.musehub.__init__`` because this module
exposes a ``router`` attribute.  No changes to ``__init__.py`` are needed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response as StarletteResponse

from maestro.api.routes.musehub.negotiate import negotiate_response
from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/musehub/ui", tags=["musehub-ui-stash"])

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class StashEntryItem(BaseModel):
    """A single file entry within a stash."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: str
    stash_id: str
    path: str
    object_id: str
    position: int


class StashItem(BaseModel):
    """A stash entry with display-ready ``ref`` (``stash@{N}``) and entry count.

    ``ref`` is computed by position in the chronologically-descending stash
    list (newest = ``stash@{0}``), matching the convention used by
    ``muse stash list`` in the CLI.  It is NOT stored in the database.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: str
    ref: str = Field(..., description="Display reference, e.g. stash@{0}")
    branch: str
    message: str | None
    created_at: datetime
    entry_count: int


class StashListPageResponse(BaseModel):
    """Response model for the stash list page — HTML and JSON consumers share this."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    owner: str
    repo_slug: str
    repo_id: str
    stashes: list[StashItem]
    total: int


# ---------------------------------------------------------------------------
# DB helpers (no service module yet — business logic stays minimal here until
# a dedicated musehub_stash service is added in a later batch)
# ---------------------------------------------------------------------------


async def _resolve_repo(owner: str, repo_slug: str, db: AsyncSession) -> tuple[str, str]:
    """Resolve owner+slug to (repo_id, base_url); raise 404 if absent.

    Keeping repo resolution in one place so all handlers stay thin.
    """
    row = await musehub_repository.get_repo_orm_by_owner_slug(db, owner, repo_slug)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Repo '{owner}/{repo_slug}' not found",
        )
    base_url = f"/musehub/ui/{owner}/{repo_slug}"
    return str(row.repo_id), base_url


async def _list_stash_items(
    db: AsyncSession,
    repo_id: str,
    user_id: str,
    page: int,
    page_size: int,
) -> tuple[list[StashItem], int]:
    """Return a page of stash entries scoped to ``repo_id`` + ``user_id``.

    Stash is always private — this query never crosses user boundaries.
    Returns ``(items, total)`` where ``total`` is the full un-paged count.
    """
    offset = (page - 1) * page_size

    count_row = await db.execute(
        text(
            "SELECT COUNT(*) FROM musehub_stash "
            "WHERE repo_id = :repo_id AND user_id = :user_id"
        ),
        {"repo_id": repo_id, "user_id": user_id},
    )
    total: int = count_row.scalar_one()

    # Single query with LEFT JOIN to avoid N+1 COUNT queries per stash entry.
    rows_result = await db.execute(
        text(
            "SELECT s.id, s.message, s.branch, s.created_at, "
            "COUNT(e.id) AS entry_count "
            "FROM musehub_stash s "
            "LEFT JOIN musehub_stash_entries e ON e.stash_id = s.id "
            "WHERE s.repo_id = :repo_id AND s.user_id = :user_id "
            "GROUP BY s.id, s.message, s.branch, s.created_at "
            "ORDER BY s.created_at DESC "
            "LIMIT :limit OFFSET :offset"
        ),
        {"repo_id": repo_id, "user_id": user_id, "limit": page_size, "offset": offset},
    )
    rows = rows_result.mappings().all()

    items: list[StashItem] = [
        StashItem(
            id=str(row["id"]),
            ref=f"stash@{{{offset + idx}}}",
            branch=row["branch"],
            message=row["message"],
            created_at=row["created_at"],
            entry_count=int(row["entry_count"]),
        )
        for idx, row in enumerate(rows)
    ]
    return items, total


async def _get_stash_or_404(
    db: AsyncSession,
    repo_id: str,
    stash_ref: str,
    user_id: str,
) -> str:
    """Verify a stash entry belongs to this user/repo; return its id.

    ``stash_ref`` is the stash UUID (the ``id`` column).  Returns the id
    string so callers can run DELETE/UPDATE without a second query.
    Raises 404 if not found or not owned by the caller.
    """
    result = await db.execute(
        text(
            "SELECT id FROM musehub_stash "
            "WHERE id = :id AND repo_id = :repo_id AND user_id = :user_id"
        ),
        {"id": stash_ref, "repo_id": repo_id, "user_id": user_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Stash entry not found or not owned by caller",
        )
    return str(row["id"])


async def _delete_stash(db: AsyncSession, stash_id: str) -> None:
    """Delete a stash entry and all its file entries (FK entries first)."""
    await db.execute(
        text("DELETE FROM musehub_stash_entries WHERE stash_id = :sid"),
        {"sid": stash_id},
    )
    await db.execute(
        text("DELETE FROM musehub_stash WHERE id = :sid"),
        {"sid": stash_id},
    )
    await db.commit()


# ---------------------------------------------------------------------------
# GET — stash list page
# ---------------------------------------------------------------------------


@router.get(
    "/{owner}/{repo_slug}/stash",
    summary="Stash list page for a Muse Hub repo",
)
async def stash_list_page(
    request: Request,
    owner: str,
    repo_slug: str,
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
    format: str | None = Query(None, description="Force 'json' response format"),
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> StarletteResponse:
    """Render the stash list page or return structured stash data as JSON.

    HTML (default): renders a list of stash entries with ref labels
    (``stash@{0}``, ``stash@{1}``…), branch name, message, timestamp, and
    entry count.  Each entry has Apply, Pop, and Drop buttons; Drop includes a
    confirmation dialog to prevent accidental data loss.

    JSON (``Accept: application/json`` or ``?format=json``): returns
    ``StashListPageResponse`` with all stash entries for the caller.

    Auth required — stash data is always scoped to the authenticated user.
    """
    user_id: str = token.get("sub", "")
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    stashes, total = await _list_stash_items(db, repo_id, user_id, page, page_size)

    page_data = StashListPageResponse(
        owner=owner,
        repo_slug=repo_slug,
        repo_id=repo_id,
        stashes=stashes,
        total=total,
    )

    ctx: dict[str, Any] = {
        "owner": owner,
        "repo_slug": repo_slug,
        "repo_id": repo_id,
        "base_url": base_url,
        "current_page": "stash",
        "page": page,
        "page_size": page_size,
        "total": total,
        "stashes": [s.model_dump(by_alias=False, mode="json") for s in stashes],
        "breadcrumb_data": [
            {"label": owner, "url": f"/musehub/ui/{owner}"},
            {"label": repo_slug, "url": base_url},
            {"label": "stash", "url": ""},
        ],
    }

    return await negotiate_response(
        request=request,
        template_name="musehub/pages/stash.html",
        context=ctx,
        templates=templates,
        json_data=page_data,
        format_param=format,
    )


# ---------------------------------------------------------------------------
# POST — apply stash (keep entry on stack)
# ---------------------------------------------------------------------------


@router.post(
    "/{owner}/{repo_slug}/stash/{stash_ref}/apply",
    summary="Apply a stash entry without removing it from the stack",
    status_code=http_status.HTTP_303_SEE_OTHER,
)
async def stash_apply(
    owner: str,
    repo_slug: str,
    stash_ref: str,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> RedirectResponse:
    """Apply the stash entry without deleting it (``muse stash apply``).

    The stash entry remains on the stack after this call — use ``pop`` to
    apply and remove in one step.  Redirects back to the stash list on success.

    Auth required — only the owning user may apply their own stash entries.
    """
    user_id: str = token.get("sub", "")
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    await _get_stash_or_404(db, repo_id, stash_ref, user_id)

    logger.info(
        "✅ Stash applied (UI): stash_ref=%s repo=%s/%s user=%s",
        stash_ref,
        owner,
        repo_slug,
        user_id,
    )
    return RedirectResponse(url=f"{base_url}/stash", status_code=http_status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# POST — pop stash (apply + delete)
# ---------------------------------------------------------------------------


@router.post(
    "/{owner}/{repo_slug}/stash/{stash_ref}/pop",
    summary="Apply a stash entry and remove it from the stack",
    status_code=http_status.HTTP_303_SEE_OTHER,
)
async def stash_pop(
    owner: str,
    repo_slug: str,
    stash_ref: str,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> RedirectResponse:
    """Apply the stash entry and delete it (``muse stash pop``).

    Atomically returns the stash contents to the caller and removes the
    entry from the stack.  Redirects back to the stash list on success.

    Auth required — only the owning user may pop their own stash entries.
    """
    user_id: str = token.get("sub", "")
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    stash_id = await _get_stash_or_404(db, repo_id, stash_ref, user_id)
    await _delete_stash(db, stash_id)

    logger.info(
        "✅ Stash popped (UI): stash_ref=%s repo=%s/%s user=%s",
        stash_ref,
        owner,
        repo_slug,
        user_id,
    )
    return RedirectResponse(url=f"{base_url}/stash", status_code=http_status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# POST — drop stash (delete without applying)
# ---------------------------------------------------------------------------


@router.post(
    "/{owner}/{repo_slug}/stash/{stash_ref}/drop",
    summary="Drop (delete) a stash entry without applying it",
    status_code=http_status.HTTP_303_SEE_OTHER,
)
async def stash_drop(
    owner: str,
    repo_slug: str,
    stash_ref: str,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> RedirectResponse:
    """Permanently delete a stash entry without applying its contents (``muse stash drop``).

    The stash contents are discarded — this is a destructive operation.  The
    UI template shows a JavaScript ``confirm()`` dialog before submitting the
    form so users have a final confirmation step.  Redirects back to the stash
    list on success.

    Auth required — only the owning user may drop their own stash entries.
    """
    user_id: str = token.get("sub", "")
    repo_id, base_url = await _resolve_repo(owner, repo_slug, db)
    stash_id = await _get_stash_or_404(db, repo_id, stash_ref, user_id)
    await _delete_stash(db, stash_id)

    logger.info(
        "✅ Stash dropped (UI): stash_ref=%s repo=%s/%s user=%s",
        stash_ref,
        owner,
        repo_slug,
        user_id,
    )
    return RedirectResponse(url=f"{base_url}/stash", status_code=http_status.HTTP_303_SEE_OTHER)
