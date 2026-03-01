"""Muse Hub stash route handlers.

Endpoint summary:
  GET    /musehub/repos/{repo_id}/stash                          — list stash entries (auth required)
  POST   /musehub/repos/{repo_id}/stash                          — push new stash (auth required)
  GET    /musehub/repos/{repo_id}/stash/{stash_id}               — get stash detail + entries (auth required)
  POST   /musehub/repos/{repo_id}/stash/{stash_id}/pop           — apply + delete stash (auth required)
  POST   /musehub/repos/{repo_id}/stash/{stash_id}/apply         — apply stash without deleting (auth required)
  DELETE /musehub/repos/{repo_id}/stash/{stash_id}               — drop a stash entry (auth required)

Maps to CLI commands: muse stash push, list, show, pop, apply, drop.
Stash entries are scoped per repo+user — users can only see their own stash.
All endpoints require a valid JWT Bearer token; there is no public/anonymous access.
Business logic is kept minimal here; persistence is handled via SQLAlchemy directly
until the musehub_stash service module is introduced in a later batch.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db

if TYPE_CHECKING:
    pass  # ORM models imported at runtime below when available

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic request / response models ────────────────────────────────────────


class StashEntryCreate(BaseModel):
    """A single file entry within a stash push request."""

    path: str = Field(..., description="Repo-relative file path")
    object_id: str = Field(..., description="Object SHA referencing the blob")


class StashPushRequest(BaseModel):
    """Request body for pushing a new stash."""

    message: str | None = Field(None, description="Optional human-readable stash message")
    branch: str = Field(..., description="Branch name the stash was taken from")
    entries: list[StashEntryCreate] = Field(
        default_factory=list,
        description="File entries captured in this stash",
    )


class StashEntryResponse(BaseModel):
    """A single file entry belonging to a stash."""

    id: str
    stash_id: str
    path: str
    object_id: str
    created_at: datetime


class StashResponse(BaseModel):
    """A stash entry with its file entries."""

    id: str
    repo_id: str
    user_id: str
    message: str | None
    branch: str
    created_at: datetime
    entries: list[StashEntryResponse] = Field(default_factory=list)


class StashListResponse(BaseModel):
    """Paginated list of stash entries."""

    items: list[StashResponse]
    total: int
    page: int
    page_size: int


class StashApplyResponse(BaseModel):
    """Response after applying a stash (pop or apply)."""

    stash_id: str
    entries: list[StashEntryResponse]
    deleted: bool = Field(..., description="True when the stash entry was removed (pop), False for apply")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


async def _get_stash_or_404(
    db: AsyncSession,
    repo_id: str,
    stash_id: str,
    user_id: str,
) -> Any:
    """Fetch a stash row scoped to repo+user, raise 404 if absent."""
    result = await db.execute(
        text(
            "SELECT id, repo_id, user_id, message, branch, created_at "
            "FROM musehub_stash "
            "WHERE id = :stash_id AND repo_id = :repo_id AND user_id = :user_id"
        ),
        {"stash_id": stash_id, "repo_id": repo_id, "user_id": user_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stash entry not found")
    return row


async def _get_stash_entries(db: AsyncSession, stash_id: str) -> list[StashEntryResponse]:
    """Return all file entries belonging to ``stash_id``."""
    result = await db.execute(
        text(
            "SELECT id, stash_id, path, object_id, created_at "
            "FROM musehub_stash_entry "
            "WHERE stash_id = :stash_id "
            "ORDER BY path"
        ),
        {"stash_id": stash_id},
    )
    return [
        StashEntryResponse(
            id=str(r["id"]),
            stash_id=str(r["stash_id"]),
            path=r["path"],
            object_id=r["object_id"],
            created_at=r["created_at"],
        )
        for r in result.mappings().all()
    ]


def _row_to_stash_response(row: Any, entries: list[StashEntryResponse] | None = None) -> StashResponse:
    """Convert a DB mapping row to ``StashResponse``."""
    return StashResponse(
        id=str(row["id"]),
        repo_id=str(row["repo_id"]),
        user_id=str(row["user_id"]),
        message=row["message"],
        branch=row["branch"],
        created_at=row["created_at"],
        entries=entries or [],
    )


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get(
    "/repos/{repo_id}/stash",
    response_model=StashListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="listStash",
    summary="List stash entries for a repo (scoped to the authenticated user)",
)
async def list_stash(
    repo_id: str,
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> StashListResponse:
    """Return a paginated list of stash entries belonging to the caller in ``repo_id``.

    Stash entries are private — each user can only see their own stash.
    """
    user_id = token.get("sub", "")
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

    items = [_row_to_stash_response(row) for row in rows]
    return StashListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post(
    "/repos/{repo_id}/stash",
    response_model=StashResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="pushStash",
    summary="Push working-tree changes onto the stash stack",
)
async def push_stash(
    repo_id: str,
    body: StashPushRequest,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> StashResponse:
    """Create a new stash entry containing the provided file entries.

    Corresponds to ``muse stash push``.  The stash is owned by the calling
    user and scoped to ``repo_id``.
    """
    user_id = token.get("sub", "")
    stash_id = str(uuid.uuid4())
    now = _now()

    await db.execute(
        text(
            "INSERT INTO musehub_stash (id, repo_id, user_id, message, branch, created_at) "
            "VALUES (:id, :repo_id, :user_id, :message, :branch, :created_at)"
        ),
        {
            "id": stash_id,
            "repo_id": repo_id,
            "user_id": user_id,
            "message": body.message,
            "branch": body.branch,
            "created_at": now,
        },
    )

    entry_responses: list[StashEntryResponse] = []
    for entry in body.entries:
        entry_id = str(uuid.uuid4())
        await db.execute(
            text(
                "INSERT INTO musehub_stash_entry (id, stash_id, path, object_id, created_at) "
                "VALUES (:id, :stash_id, :path, :object_id, :created_at)"
            ),
            {
                "id": entry_id,
                "stash_id": stash_id,
                "path": entry.path,
                "object_id": entry.object_id,
                "created_at": now,
            },
        )
        entry_responses.append(
            StashEntryResponse(
                id=entry_id,
                stash_id=stash_id,
                path=entry.path,
                object_id=entry.object_id,
                created_at=now,
            )
        )

    await db.commit()
    logger.info("✅ Stash pushed: stash_id=%s repo_id=%s user_id=%s", stash_id, repo_id, user_id)

    return StashResponse(
        id=stash_id,
        repo_id=repo_id,
        user_id=user_id,
        message=body.message,
        branch=body.branch,
        created_at=now,
        entries=entry_responses,
    )


@router.get(
    "/repos/{repo_id}/stash/{stash_id}",
    response_model=StashResponse,
    status_code=status.HTTP_200_OK,
    operation_id="getStash",
    summary="Get a stash entry with its file entries",
)
async def get_stash(
    repo_id: str,
    stash_id: str,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> StashResponse:
    """Return the stash entry identified by ``stash_id`` along with all its file entries.

    Corresponds to ``muse stash show``.  Returns 404 if the stash does not
    belong to the authenticated user in the given repo.
    """
    row = await _get_stash_or_404(db, repo_id, stash_id, token.get("sub", ""))
    entries = await _get_stash_entries(db, stash_id)
    return _row_to_stash_response(row, entries)


@router.post(
    "/repos/{repo_id}/stash/{stash_id}/pop",
    response_model=StashApplyResponse,
    status_code=status.HTTP_200_OK,
    operation_id="popStash",
    summary="Apply stash and delete it (muse stash pop)",
)
async def pop_stash(
    repo_id: str,
    stash_id: str,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> StashApplyResponse:
    """Atomically apply the stash entries and remove the stash.

    Corresponds to ``muse stash pop``.  The stash entry and all its file
    entries are deleted after the entries are returned to the caller.
    Returns 404 if the stash does not belong to the caller.
    """
    await _get_stash_or_404(db, repo_id, stash_id, token.get("sub", ""))
    entries = await _get_stash_entries(db, stash_id)

    # Delete entries first (FK constraint), then the stash header.
    await db.execute(
        text("DELETE FROM musehub_stash_entry WHERE stash_id = :stash_id"),
        {"stash_id": stash_id},
    )
    await db.execute(
        text("DELETE FROM musehub_stash WHERE id = :stash_id"),
        {"stash_id": stash_id},
    )
    await db.commit()
    logger.info("✅ Stash popped: stash_id=%s repo_id=%s user_id=%s", stash_id, repo_id, token.get("sub", ""))

    return StashApplyResponse(stash_id=stash_id, entries=entries, deleted=True)


@router.post(
    "/repos/{repo_id}/stash/{stash_id}/apply",
    response_model=StashApplyResponse,
    status_code=status.HTTP_200_OK,
    operation_id="applyStash",
    summary="Apply stash without removing it (muse stash apply)",
)
async def apply_stash(
    repo_id: str,
    stash_id: str,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> StashApplyResponse:
    """Apply the stash entries without deleting the stash.

    Corresponds to ``muse stash apply``.  The stash entry remains on the
    stack after this call — use ``pop`` to apply and remove in one step.
    Returns 404 if the stash does not belong to the caller.
    """
    await _get_stash_or_404(db, repo_id, stash_id, token.get("sub", ""))
    entries = await _get_stash_entries(db, stash_id)
    logger.info("✅ Stash applied: stash_id=%s repo_id=%s user_id=%s", stash_id, repo_id, token.get("sub", ""))

    return StashApplyResponse(stash_id=stash_id, entries=entries, deleted=False)


@router.delete(
    "/repos/{repo_id}/stash/{stash_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="dropStash",
    summary="Drop (delete) a stash entry without applying it",
)
async def drop_stash(
    repo_id: str,
    stash_id: str,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> None:
    """Permanently delete a stash entry and all its file entries.

    Corresponds to ``muse stash drop``.  The stash contents are discarded
    without being applied.  Returns 404 if the stash does not belong to
    the caller in the given repo.
    """
    await _get_stash_or_404(db, repo_id, stash_id, token.get("sub", ""))

    await db.execute(
        text("DELETE FROM musehub_stash_entry WHERE stash_id = :stash_id"),
        {"stash_id": stash_id},
    )
    await db.execute(
        text("DELETE FROM musehub_stash WHERE id = :stash_id"),
        {"stash_id": stash_id},
    )
    await db.commit()
    logger.info("✅ Stash dropped: stash_id=%s repo_id=%s user_id=%s", stash_id, repo_id, token.get("sub", ""))
