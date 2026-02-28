"""Muse Hub raw file endpoint — direct file download with correct MIME types.

Endpoint:
  GET /musehub/repos/{repo_id}/raw/{ref}/{path}

Serves artifact bytes from disk with:
- Correct Content-Type for .mid, .mp3, .wav, .json, .webp, .xml, .abc, and more
- Content-Disposition header with the original filename
- Accept-Ranges / 206 Partial Content for streaming audio playback
- No auth required for public repos; JWT required for private repos

This endpoint is intentionally **not** added to the auth-protected musehub
router (``maestro.api.routes.musehub.__init__``) so that public-repo files
can be fetched without a Bearer token — matching GitHub's raw.githubusercontent
semantics. The privacy check is enforced inside the handler itself.
"""
from __future__ import annotations

import logging
import mimetypes
import os

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.tokens import AccessCodeError, validate_access_code
from maestro.db import get_db
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["musehub-raw"])

# ---------------------------------------------------------------------------
# MIME type registry
# ---------------------------------------------------------------------------

_MIME_MAP: dict[str, str] = {
    ".mid": "audio/midi",
    ".midi": "audio/midi",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".json": "application/json",
    ".webp": "image/webp",
    ".xml": "application/xml",
    ".abc": "text/vnd.abc",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ogg": "audio/ogg",
}

# HTTPBearer with auto_error=False so we get None instead of 401
# when no Authorization header is present — allows public-repo access.
_optional_bearer = HTTPBearer(auto_error=False)


def _resolve_mime(path: str) -> str:
    """Resolve MIME type from file extension; fall back to octet-stream.

    Prefers the hand-curated ``_MIME_MAP`` over the system mimetypes database
    so that audio/midi is always returned for .mid files regardless of OS
    configuration.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in _MIME_MAP:
        return _MIME_MAP[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.get(
    "/musehub/repos/{repo_id}/raw/{ref}/{path:path}",
    summary="Download a raw file from a Muse Hub repo",
    response_class=FileResponse,
)
async def raw_file(
    repo_id: str,
    ref: str,
    path: str,
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> FileResponse:
    """Serve raw artifact bytes from a Muse Hub repo at the given ref and path.

    Auth rules:
    - Public repos: no token required. Anyone can ``curl`` or ``wget`` files.
    - Private repos: a valid Bearer JWT is required; returns 401 otherwise.

    The ``ref`` parameter mirrors Git branch/tag semantics (e.g. ``main``).
    It is accepted to support human-readable URLs and future ref-based
    filtering, but the current implementation serves the most-recently-pushed
    object at ``path`` regardless of ref — consistent with MVP scope.

    Args:
        repo_id: UUID of the target Muse Hub repo.
        ref: Branch or tag name, e.g. ``main``. Accepted but not yet
            used for filtering (future: return the object at that branch HEAD).
        path: Relative file path inside the repo, e.g. ``tracks/bass.mid``.

    Returns:
        FileResponse with:
        - Correct Content-Type derived from the file extension.
        - Content-Disposition: attachment with the filename.
        - Accept-Ranges: bytes (range requests supported via Starlette).

    Raises:
        HTTPException 401: Private repo accessed without a valid JWT.
        HTTPException 404: Repo not found, or no object at the given path.
        HTTPException 410: Object metadata exists but the file is missing from disk.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    if repo.visibility != "public":
        _require_token(credentials)

    obj = await musehub_repository.get_object_by_path(db, repo_id, path)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No object at path '{path}' in ref '{ref}'",
        )

    if not os.path.exists(obj.disk_path):
        logger.warning(
            "⚠️ Object at path '%s' exists in DB but missing from disk: %s",
            path,
            obj.disk_path,
        )
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Object file has been removed from storage",
        )

    filename = os.path.basename(obj.path)
    media_type = _resolve_mime(obj.path)
    logger.debug("✅ Serving raw file '%s' (%s) from repo %s", path, media_type, repo_id[:8])
    return FileResponse(
        obj.disk_path,
        media_type=media_type,
        filename=filename,
        headers={"Accept-Ranges": "bytes"},
    )


# ---------------------------------------------------------------------------
# Internal auth helper
# ---------------------------------------------------------------------------


def _require_token(credentials: HTTPAuthorizationCredentials | None) -> None:
    """Raise 401 if credentials are absent or the JWT is invalid/expired.

    Private repos gate behind the same JWT validation as the rest of the
    musehub API.  This helper is intentionally narrow: it validates the token
    but does NOT check revocation (that would require a DB round-trip on
    every raw download for private repos — acceptable as a future hardening
    step once revocation is indexed by hash).
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This repo is private. Provide a Bearer token to access raw files.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        validate_access_code(credentials.credentials)
    except AccessCodeError as exc:
        logger.warning("⚠️ Invalid token on private raw download: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
