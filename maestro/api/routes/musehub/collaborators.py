"""Muse Hub collaborators management route handlers.

Endpoint summary:
  GET    /musehub/repos/{repo_id}/collaborators                          — list collaborators with permission level (auth required)
  POST   /musehub/repos/{repo_id}/collaborators                          — invite collaborator (auth required, admin+)
  PUT    /musehub/repos/{repo_id}/collaborators/{username}/permission     — update permission level (auth required, admin+)
  DELETE /musehub/repos/{repo_id}/collaborators/{username}               — remove collaborator (auth required, admin+)
  GET    /musehub/repos/{repo_id}/collaborators/{username}/permission     — check collaborator status and permission level (auth required)

Permission hierarchy: owner > admin > write > read

All endpoints require a valid JWT Bearer token. Admin+ permission is required
for mutating operations. The repository owner cannot be removed as a collaborator.
"""
from __future__ import annotations

import logging
import uuid
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Permission model ──────────────────────────────────────────────────────────


class Permission(str, Enum):
    """Collaborator permission levels in ascending order of authority."""

    read = "read"
    write = "write"
    admin = "admin"
    owner = "owner"


# Permission rank for comparison: higher is more privileged.
_PERMISSION_RANK: dict[str, int] = {
    Permission.read: 1,
    Permission.write: 2,
    Permission.admin: 3,
    Permission.owner: 4,
}


def _has_permission(actor_permission: str, required: Permission) -> bool:
    """Return True if *actor_permission* is at least *required*."""
    actor_rank = _PERMISSION_RANK.get(actor_permission, 0)
    required_rank = _PERMISSION_RANK.get(required.value, 0)
    return actor_rank >= required_rank


# ── Pydantic request / response models ───────────────────────────────────────


class CollaboratorInviteRequest(BaseModel):
    """Body for POST /collaborators — invite a new collaborator."""

    username: str = Field(..., min_length=1, max_length=255, description="Username of the user to invite")
    permission: Permission = Field(Permission.read, description="Initial permission level (read | write | admin)")


class CollaboratorPermissionUpdate(BaseModel):
    """Body for PUT /collaborators/{username}/permission — update permission."""

    permission: Permission = Field(..., description="New permission level (read | write | admin)")


class CollaboratorResponse(BaseModel):
    """A single collaborator entry."""

    collaborator_id: str = Field(..., description="Unique collaborator record ID")
    repo_id: str = Field(..., description="Repository ID")
    username: str = Field(..., description="Collaborator username")
    permission: str = Field(..., description="Current permission level")
    invited_by: str = Field(..., description="Username of the inviter")


class CollaboratorListResponse(BaseModel):
    """Paginated list of repository collaborators."""

    collaborators: list[CollaboratorResponse]
    total: int = Field(..., description="Total number of collaborators")


class CollaboratorPermissionResponse(BaseModel):
    """Response for the permission-check endpoint."""

    username: str
    is_collaborator: bool
    permission: str | None = Field(None, description="Permission level if the user is a collaborator")


# ── Helper — load ORM model at runtime ───────────────────────────────────────


def _require_orm() -> Any:
    """Return the MusehubCollaborator ORM class or raise HTTP 503.

    The ORM model lives in batch-01 (maestro.db.musehub_collaborator_models).
    If that migration is not yet merged, this function raises HTTP 503 rather
    than crashing at import time or returning a bad response.
    """
    try:
        from maestro.db.musehub_collaborator_models import MusehubCollaborator  # noqa: PLC0415
        return MusehubCollaborator
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Collaborator ORM not yet available — batch-01 migration pending",
        )


def _orm_to_response(collab: Any) -> CollaboratorResponse:
    """Convert an ORM MusehubCollaborator row to a CollaboratorResponse."""
    return CollaboratorResponse(
        collaborator_id=str(collab.collaborator_id),
        repo_id=str(collab.repo_id),
        username=str(collab.username),
        permission=str(collab.permission),
        invited_by=str(collab.invited_by),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "/repos/{repo_id}/collaborators",
    response_model=CollaboratorListResponse,
    operation_id="listCollaborators",
    summary="List collaborators with their permission levels",
)
async def list_collaborators(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> CollaboratorListResponse:
    """Return all collaborators for *repo_id*.

    Any authenticated user may call this endpoint; being a collaborator is not
    required to view the collaborator list (useful for pending-invite UX).
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    MusehubCollaborator: Any = _require_orm()

    result: Any = await db.execute(
        select(MusehubCollaborator).where(MusehubCollaborator.repo_id == repo_id)
    )
    rows = result.scalars().all()
    items = [_orm_to_response(r) for r in rows]
    return CollaboratorListResponse(collaborators=items, total=len(items))


@router.post(
    "/repos/{repo_id}/collaborators",
    response_model=CollaboratorResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="inviteCollaborator",
    summary="Invite a collaborator to the repository",
)
async def invite_collaborator(
    repo_id: str,
    body: CollaboratorInviteRequest,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> CollaboratorResponse:
    """Invite *username* as a collaborator with the given *permission* level.

    Requires admin+ permission on the repository. The owner's permission level
    cannot be downgraded via this endpoint — use the dedicated update endpoint.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    MusehubCollaborator: Any = _require_orm()

    actor: str = token.get("sub", "")
    repo_owner: str = str(getattr(repo, "owner_id", ""))

    # Look up the actor's permission on this repo.
    actor_result: Any = await db.execute(
        select(MusehubCollaborator).where(
            MusehubCollaborator.repo_id == repo_id,
            MusehubCollaborator.username == actor,
        )
    )
    actor_collab: Any = actor_result.scalar_one_or_none()
    actor_permission: str = str(actor_collab.permission) if actor_collab is not None else ""

    # Repo owner also counts as admin+.
    if actor != repo_owner and not _has_permission(actor_permission, Permission.admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner permission required to invite collaborators",
        )

    # Check for duplicate.
    existing_result: Any = await db.execute(
        select(MusehubCollaborator).where(
            MusehubCollaborator.repo_id == repo_id,
            MusehubCollaborator.username == body.username,
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User '{body.username}' is already a collaborator",
        )

    new_collab: Any = MusehubCollaborator(
        collaborator_id=str(uuid.uuid4()),
        repo_id=repo_id,
        username=body.username,
        permission=body.permission.value,
        invited_by=actor,
    )
    db.add(new_collab)
    await db.commit()
    await db.refresh(new_collab)

    logger.info(
        "✅ Collaborator '%s' added to repo '%s' with permission '%s'",
        body.username,
        repo_id,
        body.permission,
    )
    return _orm_to_response(new_collab)


@router.put(
    "/repos/{repo_id}/collaborators/{username}/permission",
    response_model=CollaboratorResponse,
    operation_id="updateCollaboratorPermission",
    summary="Update a collaborator's permission level",
)
async def update_collaborator_permission(
    repo_id: str,
    username: str,
    body: CollaboratorPermissionUpdate,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> CollaboratorResponse:
    """Update *username*'s permission on *repo_id*.

    Requires admin+ permission. The owner's permission cannot be changed via
    this endpoint — ownership transfer is a separate, deliberate operation.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    MusehubCollaborator: Any = _require_orm()

    actor: str = token.get("sub", "")
    repo_owner: str = str(getattr(repo, "owner_id", ""))

    actor_result: Any = await db.execute(
        select(MusehubCollaborator).where(
            MusehubCollaborator.repo_id == repo_id,
            MusehubCollaborator.username == actor,
        )
    )
    actor_collab: Any = actor_result.scalar_one_or_none()
    actor_permission: str = str(actor_collab.permission) if actor_collab is not None else ""

    if actor != repo_owner and not _has_permission(actor_permission, Permission.admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner permission required to update collaborator permissions",
        )

    target_result: Any = await db.execute(
        select(MusehubCollaborator).where(
            MusehubCollaborator.repo_id == repo_id,
            MusehubCollaborator.username == username,
        )
    )
    target: Any = target_result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collaborator not found")

    if str(target.permission) == Permission.owner.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner permission cannot be changed via this endpoint",
        )

    target.permission = body.permission.value
    await db.commit()
    await db.refresh(target)

    logger.info(
        "✅ Collaborator '%s' permission updated to '%s' on repo '%s'",
        username,
        body.permission,
        repo_id,
    )
    return _orm_to_response(target)


@router.delete(
    "/repos/{repo_id}/collaborators/{username}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="removeCollaborator",
    summary="Remove a collaborator from the repository",
)
async def remove_collaborator(
    repo_id: str,
    username: str,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> None:
    """Remove *username* from the collaborator list of *repo_id*.

    Requires admin+ permission. The repository owner cannot be removed.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    MusehubCollaborator: Any = _require_orm()

    actor: str = token.get("sub", "")
    repo_owner: str = str(getattr(repo, "owner_id", ""))

    actor_result: Any = await db.execute(
        select(MusehubCollaborator).where(
            MusehubCollaborator.repo_id == repo_id,
            MusehubCollaborator.username == actor,
        )
    )
    actor_collab: Any = actor_result.scalar_one_or_none()
    actor_permission: str = str(actor_collab.permission) if actor_collab is not None else ""

    if actor != repo_owner and not _has_permission(actor_permission, Permission.admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner permission required to remove collaborators",
        )

    target_result: Any = await db.execute(
        select(MusehubCollaborator).where(
            MusehubCollaborator.repo_id == repo_id,
            MusehubCollaborator.username == username,
        )
    )
    target: Any = target_result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collaborator not found")

    if str(target.permission) == Permission.owner.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner cannot be removed as a collaborator",
        )

    await db.execute(
        delete(MusehubCollaborator).where(
            MusehubCollaborator.repo_id == repo_id,
            MusehubCollaborator.username == username,
        )
    )
    await db.commit()

    logger.info(
        "✅ Collaborator '%s' removed from repo '%s' by '%s'",
        username,
        repo_id,
        actor,
    )


@router.get(
    "/repos/{repo_id}/collaborators/{username}/permission",
    response_model=CollaboratorPermissionResponse,
    operation_id="checkCollaboratorPermission",
    summary="Check if a user is a collaborator and their permission level",
)
async def check_collaborator_permission(
    repo_id: str,
    username: str,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> CollaboratorPermissionResponse:
    """Return collaborator status and permission level for *username* on *repo_id*.

    Returns ``is_collaborator: false`` (with ``permission: null``) if the user
    is not currently a collaborator rather than raising 404, so callers can
    safely use this as a presence check without special-casing the error.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    MusehubCollaborator: Any = _require_orm()

    result: Any = await db.execute(
        select(MusehubCollaborator).where(
            MusehubCollaborator.repo_id == repo_id,
            MusehubCollaborator.username == username,
        )
    )
    collab: Any = result.scalar_one_or_none()

    if collab is None:
        return CollaboratorPermissionResponse(username=username, is_collaborator=False, permission=None)

    return CollaboratorPermissionResponse(
        username=username,
        is_collaborator=True,
        permission=str(collab.permission),
    )
