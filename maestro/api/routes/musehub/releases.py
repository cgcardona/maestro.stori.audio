"""Muse Hub release management route handlers.

Endpoint summary:
  POST /musehub/repos/{repo_id}/releases          — create a release
  GET  /musehub/repos/{repo_id}/releases          — list all releases (newest first)
  GET  /musehub/repos/{repo_id}/releases/{tag}    — get a single release by tag

A release ties a version tag (e.g. "v1.0") to a commit snapshot and carries
Markdown release notes plus structured download package URLs. Tags are unique
per repo — POSTing a duplicate tag returns 409 Conflict.

All endpoints require a valid JWT Bearer token.
No business logic lives here — all persistence is delegated to
``maestro.services.musehub_releases``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import ReleaseCreate, ReleaseListResponse, ReleaseResponse
from maestro.services import musehub_releases
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/repos/{repo_id}/releases",
    response_model=ReleaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a release for a Muse Hub repo",
)
async def create_release(
    repo_id: str,
    body: ReleaseCreate,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> ReleaseResponse:
    """Create a new release tied to an optional commit snapshot.

    Returns 404 if the repo does not exist. Returns 409 if a release with
    the same ``tag`` already exists for this repo.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    try:
        release = await musehub_releases.create_release(
            db,
            repo_id=repo_id,
            tag=body.tag,
            title=body.title,
            body=body.body,
            commit_id=body.commit_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await db.commit()
    return release


@router.get(
    "/repos/{repo_id}/releases",
    response_model=ReleaseListResponse,
    summary="List all releases for a Muse Hub repo",
)
async def list_releases(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> ReleaseListResponse:
    """Return all releases for the repo ordered newest first.

    Returns 404 if the repo does not exist.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    return await musehub_releases.get_release_list_response(db, repo_id)


@router.get(
    "/repos/{repo_id}/releases/{tag}",
    response_model=ReleaseResponse,
    summary="Get a single release by tag",
)
async def get_release(
    repo_id: str,
    tag: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> ReleaseResponse:
    """Return the release identified by ``tag`` for the given repo.

    Returns 404 if the repo or the tag does not exist.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    release = await musehub_releases.get_release_by_tag(db, repo_id, tag)
    if release is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Release '{tag}' not found",
        )
    return release
