"""Muse Hub collaborators management route handlers.

Endpoint summary:
  GET    /musehub/repos/{repo_id}/collaborators                          — list collaborators with permission level (auth required)
  POST   /musehub/repos/{repo_id}/collaborators                          — invite collaborator (auth required, admin+)
  PUT    /musehub/repos/{repo_id}/collaborators/{user_id}/permission     — update permission level (auth required, admin+)
  DELETE /musehub/repos/{repo_id}/collaborators/{user_id}               — remove collaborator (auth required, admin+)

  The read-only access-check endpoint (GET /repos/{repo_id}/collaborators/{username}/permission)
  lives in repos.py — it has a different response shape and 404-on-absence semantics.

Permission hierarchy: owner > admin > write > read

All endpoints require a valid JWT Bearer token. Admin+ permission is required
for mutating operations. The repository owner cannot be removed as a collaborator.
"""
from __future__ import annotations

import logging
import uuid
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.db.musehub_collaborator_models import MusehubCollaborator
from maestro.models.base import CamelModel
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
    Permission.read.value: 1,
    Permission.write.value: 2,
    Permission.admin.value: 3,
    Permission.owner.value: 4,
}


def _has_permission(actor_permission: str, required: Permission) -> bool:
    """Return True if *actor_permission* is at least *required*."""
    actor_rank = _PERMISSION_RANK.get(actor_permission, 0)
    required_rank = _PERMISSION_RANK.get(required.value, 0)
    return actor_rank >= required_rank


# ── Pydantic request / response models ───────────────────────────────────────


class CollaboratorInviteRequest(CamelModel):
    """Body for POST /collaborators — invite a new collaborator."""

    user_id: str = Field(..., min_length=1, max_length=36, description="UUID of the user to invite")
    permission: Permission = Field(Permission.write, description="Initial permission level (read | write | admin)")


class CollaboratorPermissionUpdate(CamelModel):
    """Body for PUT /collaborators/{user_id}/permission — update permission."""

    permission: Permission = Field(..., description="New permission level (read | write | admin)")


class CollaboratorResponse(CamelModel):
    """A single collaborator entry."""

    collaborator_id: str = Field(..., description="Unique collaborator record ID (primary key)")
    repo_id: str = Field(..., description="Repository ID")
    user_id: str = Field(..., description="Collaborator user ID (UUID)")
    permission: str = Field(..., description="Current permission level")
    invited_by: str | None = Field(None, description="User ID of the inviter (null if added programmatically)")


class CollaboratorListResponse(CamelModel):
    """Paginated list of repository collaborators."""

    collaborators: list[CollaboratorResponse]
    total: int = Field(..., description="Total number of collaborators")


# ── Helper ───────────────────────────────────────────────────────────────────


def _orm_to_response(collab: MusehubCollaborator) -> CollaboratorResponse:
    """Convert an ORM MusehubCollaborator row to a CollaboratorResponse."""
    return CollaboratorResponse(
        collaborator_id=str(collab.id),
        repo_id=str(collab.repo_id),
        user_id=str(collab.user_id),
        permission=str(collab.permission),
        invited_by=str(collab.invited_by) if collab.invited_by is not None else None,
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

    result = await db.execute(
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
    """Invite *user_id* as a collaborator with the given *permission* level.

    Requires admin+ permission on the repository. The owner's permission level
    cannot be downgraded via this endpoint — use the dedicated update endpoint.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    actor: str = token.get("sub", "")
    repo_owner: str = str(repo.owner_user_id)

    # Look up the actor's permission on this repo.
    actor_result = await db.execute(
        select(MusehubCollaborator).where(
            MusehubCollaborator.repo_id == repo_id,
            MusehubCollaborator.user_id == actor,
        )
    )
    actor_collab = actor_result.scalar_one_or_none()
    actor_permission: str = str(actor_collab.permission) if actor_collab is not None else ""

    # Repo owner also counts as admin+.
    if actor != repo_owner and not _has_permission(actor_permission, Permission.admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner permission required to invite collaborators",
        )

    # Check for duplicate.
    existing_result = await db.execute(
        select(MusehubCollaborator).where(
            MusehubCollaborator.repo_id == repo_id,
            MusehubCollaborator.user_id == body.user_id,
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User '{body.user_id}' is already a collaborator",
        )

    new_collab = MusehubCollaborator(
        id=str(uuid.uuid4()),
        repo_id=repo_id,
        user_id=body.user_id,
        permission=body.permission.value,
        invited_by=actor,
    )
    db.add(new_collab)
    await db.commit()
    await db.refresh(new_collab)

    logger.info(
        "✅ Collaborator '%s' added to repo '%s' with permission '%s'",
        body.user_id,
        repo_id,
        body.permission.value,
    )
    return _orm_to_response(new_collab)


@router.put(
    "/repos/{repo_id}/collaborators/{user_id}/permission",
    response_model=CollaboratorResponse,
    operation_id="updateCollaboratorPermission",
    summary="Update a collaborator's permission level",
)
async def update_collaborator_permission(
    repo_id: str,
    user_id: str,
    body: CollaboratorPermissionUpdate,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> CollaboratorResponse:
    """Update *user_id*'s permission on *repo_id*.

    Requires admin+ permission. The owner's permission cannot be changed via
    this endpoint — ownership transfer is a separate, deliberate operation.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    actor: str = token.get("sub", "")
    repo_owner: str = str(repo.owner_user_id)

    actor_result = await db.execute(
        select(MusehubCollaborator).where(
            MusehubCollaborator.repo_id == repo_id,
            MusehubCollaborator.user_id == actor,
        )
    )
    actor_collab = actor_result.scalar_one_or_none()
    actor_permission: str = str(actor_collab.permission) if actor_collab is not None else ""

    if actor != repo_owner and not _has_permission(actor_permission, Permission.admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner permission required to update collaborator permissions",
        )

    target_result = await db.execute(
        select(MusehubCollaborator).where(
            MusehubCollaborator.repo_id == repo_id,
            MusehubCollaborator.user_id == user_id,
        )
    )
    target = target_result.scalar_one_or_none()
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
        user_id,
        body.permission.value,
        repo_id,
    )
    return _orm_to_response(target)


@router.delete(
    "/repos/{repo_id}/collaborators/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="removeCollaborator",
    summary="Remove a collaborator from the repository",
)
async def remove_collaborator(
    repo_id: str,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(require_valid_token),
) -> None:
    """Remove *user_id* from the collaborator list of *repo_id*.

    Requires admin+ permission. The repository owner cannot be removed.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    actor: str = token.get("sub", "")
    repo_owner: str = str(repo.owner_user_id)

    actor_result = await db.execute(
        select(MusehubCollaborator).where(
            MusehubCollaborator.repo_id == repo_id,
            MusehubCollaborator.user_id == actor,
        )
    )
    actor_collab = actor_result.scalar_one_or_none()
    actor_permission: str = str(actor_collab.permission) if actor_collab is not None else ""

    if actor != repo_owner and not _has_permission(actor_permission, Permission.admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner permission required to remove collaborators",
        )

    target_result = await db.execute(
        select(MusehubCollaborator).where(
            MusehubCollaborator.repo_id == repo_id,
            MusehubCollaborator.user_id == user_id,
        )
    )
    target = target_result.scalar_one_or_none()
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
            MusehubCollaborator.user_id == user_id,
        )
    )
    await db.commit()

    logger.info(
        "✅ Collaborator '%s' removed from repo '%s' by '%s'",
        user_id,
        repo_id,
        actor,
    )


