"""Muse Hub user-profile route handlers (JSON API).

Endpoint summary:
  GET  /musehub/users/{username}                — fetch full profile (public, no JWT required)
  GET  /musehub/users/{username}/forks          — list repos forked by this user (public)
  POST /musehub/users                           — create a profile for the authenticated user
  PUT  /musehub/users/{username}                — update bio/avatar/pinned repos (owner only)
  GET  /musehub/users/{username}/followers-list — list followers as user cards (public)
  GET  /musehub/users/{username}/following-list — list following as user cards (public)

Content negotiation: all endpoints return JSON.  The browser UI fetches from
these endpoints using the client-side JWT stored in localStorage.

The GET endpoints are intentionally unauthenticated so that profile pages are
publicly discoverable without login — matching the behaviour of GitHub profiles.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import Field

from maestro.auth.dependencies import TokenClaims, optional_token, require_valid_token
from maestro.db import get_db
from maestro.db.musehub_models import MusehubFollow, MusehubProfile
from maestro.models.base import CamelModel
from maestro.models.musehub import ProfileResponse, ProfileUpdateRequest, UserForksResponse
from maestro.services import musehub_profile as profile_svc
from maestro.services import musehub_repository as repo_svc

logger = logging.getLogger(__name__)

router = APIRouter()


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


class UserCardResponse(CamelModel):
    """Compact user card returned by followers-list and following-list endpoints.

    Designed for rendering avatar circles, linked usernames, and bio previews
    in the Followers / Following tabs on the profile page.
    """

    username: str
    bio: str | None = None
    avatar_url: str | None = None


@router.get(
    "/users/{username}",
    response_model=ProfileResponse,
    operation_id="getUserProfile",
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


@router.get(
    "/users/{username}/forks",
    response_model=UserForksResponse,
    operation_id="getUserForks",
    summary="List repos forked by a user (public)",
)
async def get_user_forks(
    username: str,
    db: AsyncSession = Depends(get_db),
) -> UserForksResponse:
    """Return all repos that ``username`` has forked, with source attribution.

    Joins ``musehub_forks`` (where ``forked_by`` matches the given username)
    with ``musehub_repos`` twice — once for the fork repo metadata and once
    for the source repo's owner/slug so the profile page can render
    "forked from {source_owner}/{source_slug}" under each card.

    No JWT required — the forked tab is publicly visible on profile pages.
    Returns 404 when the username does not exist.
    """
    profile = await profile_svc.get_profile_by_username(db, username)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No profile found for username '{username}'",
        )

    result = await repo_svc.get_user_forks(db, username)
    logger.info("✅ Served %d forks for username=%s", result.total, username)
    return result


@router.post(
    "/users",
    response_model=ProfileResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createUserProfile",
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
    operation_id="updateUserProfile",
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


# ---------------------------------------------------------------------------
# Followers / following lists
# ---------------------------------------------------------------------------


async def _resolve_user_id(db: AsyncSession, username: str) -> str:
    """Return the user_id for a given username, falling back to the username itself.

    MusehubFollow rows created via the API store a user_id (JWT sub) as
    follower_id and a username string as followee_id.  Rows created by the
    seed script store user_ids in both columns.  This helper resolves the
    profile's canonical user_id so list queries can match both conventions.
    """
    row = (await db.execute(
        select(MusehubProfile).where(MusehubProfile.username == username)
    )).scalar_one_or_none()
    return row.user_id if row else username


async def _profile_to_card(db: AsyncSession, id_value: str) -> UserCardResponse | None:
    """Look up a profile by user_id or username and return a UserCardResponse.

    Tries user_id first (covers API-created follows and seed data), then falls
    back to username lookup (covers followee_id = username from the follow API).
    """
    row = (await db.execute(
        select(MusehubProfile).where(
            or_(MusehubProfile.user_id == id_value, MusehubProfile.username == id_value)
        )
    )).scalar_one_or_none()
    if row is None:
        return None
    return UserCardResponse(username=row.username, bio=row.bio, avatar_url=row.avatar_url)


@router.get(
    "/users/{username}/followers-list",
    response_model=list[UserCardResponse],
    operation_id="listFollowerCards",
    summary="List followers as user cards (public)",
)
async def list_followers(
    username: str,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> list[UserCardResponse]:
    """Return user cards for everyone who follows *username*.

    Handles both seed-data rows (user_ids in both columns) and API-created rows
    (user_id in follower_id, username string in followee_id).
    No JWT required — follower lists are publicly accessible.
    """
    profile = await profile_svc.get_profile_by_username(db, username)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No profile found for '{username}'")

    user_id = profile.user_id
    rows = (await db.execute(
        select(MusehubFollow).where(
            or_(MusehubFollow.followee_id == username, MusehubFollow.followee_id == user_id)
        ).limit(limit)
    )).scalars().all()

    cards: list[UserCardResponse] = []
    for row in rows:
        card = await _profile_to_card(db, row.follower_id)
        if card is not None:
            cards.append(card)
    logger.info("✅ Followers list username=%s count=%d", username, len(cards))
    return cards


@router.get(
    "/users/{username}/following-list",
    response_model=list[UserCardResponse],
    operation_id="listFollowingCards",
    summary="List following as user cards (public)",
)
async def list_following(
    username: str,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> list[UserCardResponse]:
    """Return user cards for everyone that *username* follows.

    Handles both seed-data rows (user_ids in both columns) and API-created rows
    (user_id in follower_id, username string in followee_id).
    No JWT required — following lists are publicly accessible.
    """
    profile = await profile_svc.get_profile_by_username(db, username)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No profile found for '{username}'")

    user_id = profile.user_id
    rows = (await db.execute(
        select(MusehubFollow).where(
            or_(MusehubFollow.follower_id == username, MusehubFollow.follower_id == user_id)
        ).limit(limit)
    )).scalars().all()

    cards: list[UserCardResponse] = []
    for row in rows:
        card = await _profile_to_card(db, row.followee_id)
        if card is not None:
            cards.append(card)
    logger.info("✅ Following list username=%s count=%d", username, len(cards))
    return cards
