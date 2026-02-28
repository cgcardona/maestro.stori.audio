"""Muse Hub user-profile route handlers (JSON API).

Endpoint summary:
  GET  /musehub/users/{username}  — fetch full profile (public, no JWT required)
  POST /musehub/users             — create a profile for the authenticated user
  PUT  /musehub/users/{username}  — update bio/avatar/pinned repos (owner only)

Content negotiation: all endpoints return JSON.  The browser UI fetches from
these endpoints using the client-side JWT stored in localStorage.

The GET endpoint is intentionally unauthenticated so that profile pages are
publicly discoverable without login — matching the behaviour of GitHub profiles.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import Field

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.models.base import CamelModel
from maestro.models.musehub import ProfileResponse, ProfileUpdateRequest
from maestro.services import musehub_profile as profile_svc

logger = logging.getLogger(__name__)

router = APIRouter(tags=["musehub-users"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateProfileBody(CamelModel):
    """Body for POST /api/v1/musehub/users — create a public profile for the authenticated user."""

    username: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9_-]+$",
        description="URL-friendly username (lowercase alphanumeric, hyphens, underscores)",
    )
    bio: str | None = Field(None, max_length=500, description="Short bio (Markdown supported)")
    avatar_url: str | None = Field(None, max_length=2048, description="Avatar image URL")


@router.get(
    "/users/{username}",
    response_model=ProfileResponse,
    summary="Get a Muse Hub user profile (public)",
)
async def get_user_profile(
    username: str,
    db: AsyncSession = Depends(get_db),
) -> ProfileResponse:
    """Return the full profile for a user: bio, avatar, pinned repos, public repos,
    contribution graph, and session credits.

    No JWT required — profiles are publicly accessible.  Returns 404 when the
    username does not match any registered profile.
    """
    profile = await profile_svc.get_full_profile(db, username)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No profile found for username '{username}'",
        )
    logger.info("✅ Served profile for username=%s", username)
    return profile


@router.post(
    "/users",
    response_model=ProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a Muse Hub user profile",
)
async def create_user_profile(
    body: CreateProfileBody,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> ProfileResponse:
    """Create a public profile for the authenticated user.

    The ``username`` must be globally unique and URL-safe.  Returns 409 if the
    username is already taken, or if the caller already has a profile.
    """
    user_id: str = claims.get("sub") or ""
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: no sub")

    existing_by_user = await profile_svc.get_profile_by_user_id(db, user_id)
    if existing_by_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a profile. Use PUT to update it.",
        )

    existing_by_name = await profile_svc.get_profile_by_username(db, body.username)
    if existing_by_name is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' is already taken.",
        )

    await profile_svc.create_profile(
        db,
        user_id=user_id,
        username=body.username,
        bio=body.bio,
        avatar_url=body.avatar_url,
    )
    await db.commit()

    full = await profile_svc.get_full_profile(db, body.username)
    if full is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Profile created but not found")
    logger.info("✅ Created profile username=%s user_id=%s", body.username, user_id)
    return full


@router.put(
    "/users/{username}",
    response_model=ProfileResponse,
    summary="Update a Muse Hub user profile (owner only)",
)
async def update_user_profile(
    username: str,
    body: ProfileUpdateRequest,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims = Depends(require_valid_token),
) -> ProfileResponse:
    """Partially update the authenticated user's profile: bio, avatar_url, pinned_repo_ids.

    Returns 403 if the caller does not own the profile, 404 if the username
    does not exist.
    """
    profile = await profile_svc.get_profile_by_username(db, username)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    caller_id: str = claims.get("sub") or ""
    if profile.user_id != caller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit your own profile.",
        )

    await profile_svc.update_profile(db, profile, body)
    await db.commit()

    full = await profile_svc.get_full_profile(db, username)
    if full is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Profile updated but not found")
    logger.info("✅ Updated profile username=%s", username)
    return full
