"""Muse Hub object (artifact) route handlers.

Endpoint summary:
  GET /musehub/repos/{repo_id}/objects                         — list artifact metadata
  GET /musehub/repos/{repo_id}/objects/{object_id}/content     — serve raw artifact bytes

Objects are binary artifacts (MIDI, MP3, WebP piano rolls) pushed via the
sync protocol. They are stored on disk; only metadata lives in Postgres.
These endpoints are primarily consumed by the Muse Hub web UI.

All endpoints require a valid JWT Bearer token.
"""
from __future__ import annotations

import logging
import mimetypes
import os

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, require_valid_token
from maestro.db import get_db
from maestro.models.musehub import ObjectMetaListResponse
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter()

# Additional MIME types not always in the system mimetypes database
_EXTRA_MIME: dict[str, str] = {
    ".mid": "audio/midi",
    ".midi": "audio/midi",
    ".mp3": "audio/mpeg",
    ".webp": "image/webp",
}


def _content_type(path: str) -> str:
    """Resolve MIME type from path extension; fall back to octet-stream."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _EXTRA_MIME:
        return _EXTRA_MIME[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


@router.get(
    "/repos/{repo_id}/objects",
    response_model=ObjectMetaListResponse,
    summary="List artifact metadata for a Muse Hub repo",
)
async def list_objects(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> ObjectMetaListResponse:
    """Return metadata (path, size, object_id) for all objects in the repo.

    Binary content is excluded from this response — use the ``/content``
    sub-resource to download individual artifacts. Results are ordered by path.
    Returns 404 if the repo does not exist.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    objects = await musehub_repository.list_objects(db, repo_id)
    return ObjectMetaListResponse(objects=objects)


@router.get(
    "/repos/{repo_id}/objects/{object_id}/content",
    summary="Download raw artifact bytes",
    response_class=FileResponse,
)
async def get_object_content(
    repo_id: str,
    object_id: str,
    db: AsyncSession = Depends(get_db),
    _: TokenClaims = Depends(require_valid_token),
) -> FileResponse:
    """Stream the raw bytes of a stored artifact from disk.

    Content-Type is inferred from the stored ``path`` extension
    (.webp → image/webp, .mid → audio/midi, .mp3 → audio/mpeg).
    Returns 404 if the repo or object is not found, or 410 if the file has
    been removed from disk.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    obj = await musehub_repository.get_object_row(db, repo_id, object_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Object not found")

    if not os.path.exists(obj.disk_path):
        logger.warning("⚠️ Object %s exists in DB but missing from disk: %s", object_id, obj.disk_path)
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Object file has been removed from storage",
        )

    filename = os.path.basename(obj.path)
    media_type = _content_type(obj.path)
    return FileResponse(obj.disk_path, media_type=media_type, filename=filename)
