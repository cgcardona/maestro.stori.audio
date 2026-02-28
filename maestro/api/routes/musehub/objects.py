"""Muse Hub object (artifact) route handlers.

Endpoint summary:
  GET /musehub/repos/{repo_id}/objects                             — list artifact metadata
  GET /musehub/repos/{repo_id}/objects/{object_id}/content         — serve raw artifact bytes
  GET /musehub/repos/{repo_id}/objects/{object_id}/parse-midi      — MIDI-to-JSON parsing endpoint
  GET /musehub/repos/{repo_id}/export/{ref}?format=midi&...        — download export package

Objects are binary artifacts (MIDI, MP3, WebP piano rolls) pushed via the
sync protocol. They are stored on disk; only metadata lives in Postgres.
These endpoints are primarily consumed by the Muse Hub web UI.

The export endpoint packages stored artifacts at a given commit ref into a
single downloadable file (or ZIP archive for multi-track exports).

The parse-midi endpoint converts a stored MIDI artifact into a structured
JSON representation (MidiParseResult) consumed by the Canvas-based piano roll
renderer in the browser.

All endpoints require a valid JWT Bearer token.
"""
from __future__ import annotations

import logging
import mimetypes
import os
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, optional_token, require_valid_token
from maestro.db import get_db
from maestro.db.musehub_models import MusehubDownloadEvent
from maestro.models.musehub import ObjectMetaListResponse, TreeListResponse
from maestro.services import musehub_repository
from maestro.services.musehub_exporter import ExportFormat, export_repo_at_ref
from maestro.services.musehub_midi_parser import parse_midi_bytes

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
    operation_id="listObjects",
    summary="List artifact metadata for a Muse Hub repo",
)
async def list_objects(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> ObjectMetaListResponse:
    """Return metadata (path, size, object_id) for all objects in the repo.

    Binary content is excluded from this response — use the ``/content``
    sub-resource to download individual artifacts. Results are ordered by path.
    Returns 404 if the repo does not exist.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    objects = await musehub_repository.list_objects(db, repo_id)
    return ObjectMetaListResponse(objects=objects)


@router.get(
    "/repos/{repo_id}/objects/{object_id}/content",
    operation_id="getObjectContent",
    summary="Download raw artifact bytes",
    response_class=FileResponse,
)
async def get_object_content(
    repo_id: str,
    object_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
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
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
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


@router.get(
    "/repos/{repo_id}/objects/{object_id}/parse-midi",
    operation_id="parseMidi",
    summary="Parse a stored MIDI artifact into structured note data",
    responses={
        200: {"description": "MidiParseResult JSON — tracks, tempo, time signature, notes"},
        404: {"description": "Repo or object not found, or object is not a MIDI file"},
        410: {"description": "Object file has been removed from storage"},
        422: {"description": "Object is not a valid MIDI file"},
    },
)
async def parse_midi_object(
    repo_id: str,
    object_id: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> JSONResponse:
    """Parse a stored MIDI artifact and return a structured MidiParseResult.

    Reads the binary MIDI file from disk, delegates to
    :func:`maestro.services.musehub_midi_parser.parse_midi_bytes`, and returns
    the result as JSON.  The response shape is:

    .. code-block:: json

        {
          "tracks": [
            {
              "track_id": 0,
              "channel": 0,
              "name": "Piano",
              "notes": [
                {"pitch": 60, "start_beat": 0.0, "duration_beats": 1.0,
                 "velocity": 80, "track_id": 0, "channel": 0}
              ]
            }
          ],
          "tempo_bpm": 120.0,
          "time_signature": "4/4",
          "total_beats": 32.0
        }

    The ``notes`` array is sorted by ``start_beat``.  All timing is expressed
    in quarter-note beats, independent of playback tempo, so the client piano
    roll renderer can display musical time without needing to convert ticks.

    Returns 404 if the repo or object is not found.
    Returns 422 if the artifact bytes cannot be parsed as a Standard MIDI File.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    obj = await musehub_repository.get_object_row(db, repo_id, object_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Object not found")

    ext = os.path.splitext(obj.path)[1].lower()
    if ext not in {".mid", ".midi"}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Object '{obj.path}' is not a MIDI file",
        )

    if not os.path.exists(obj.disk_path):
        logger.warning("⚠️ MIDI object %s missing from disk: %s", object_id, obj.disk_path)
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Object file has been removed from storage",
        )

    try:
        with open(obj.disk_path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        logger.error("❌ Could not read MIDI file %s: %s", obj.disk_path, exc)
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Could not read object from storage",
        ) from exc

    try:
        result = parse_midi_bytes(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    logger.info(
        "✅ MIDI parsed: repo=%s obj=%s tracks=%d beats=%.1f",
        repo_id,
        object_id,
        len(result["tracks"]),
        result["total_beats"],
    )
    return JSONResponse(content=result)


@router.get(
    "/repos/{repo_id}/export/{ref}",
    operation_id="exportArtifacts",
    summary="Download an export package of artifacts at a commit ref",
    responses={
        200: {"description": "Artifact or ZIP archive ready for download"},
        404: {"description": "Repo or ref not found"},
        422: {"description": "Invalid export format"},
    },
)
async def export_artifacts(
    repo_id: str,
    ref: str,
    format: Annotated[  # noqa: A002
        ExportFormat,
        Query(description="Export format: midi, json, musicxml, abc, wav, mp3"),
    ] = ExportFormat.midi,
    split_tracks: Annotated[
        bool,
        Query(
            alias="splitTracks",
            description="Bundle all matching artifacts into a ZIP archive",
        ),
    ] = False,
    sections: Annotated[
        str | None,
        Query(
            description="Comma-separated section names to include (e.g. 'verse,chorus')",
        ),
    ] = None,
    db: AsyncSession = Depends(get_db),
    _claims: TokenClaims = Depends(require_valid_token),
) -> Response:
    """Return a downloadable export of stored artifacts at the given commit ref.

    ``ref`` can be a full commit ID or a branch name. The endpoint resolves
    the ref to a known commit, filters objects by format and optional section
    names, then returns either the raw file (single artifact, split_tracks=False)
    or a ZIP archive (multiple artifacts or split_tracks=True).

    Content-Disposition header is set to ``attachment`` with a meaningful
    filename derived from the repo ID, ref, and format.

    Returns 404 if the repo does not exist or the ref cannot be resolved.
    Returns 422 if the format query param is not one of the accepted values.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")

    section_list: list[str] | None = (
        [s.strip() for s in sections.split(",") if s.strip()] if sections else None
    )

    result = await export_repo_at_ref(
        db,
        repo_id=repo_id,
        ref=ref,
        format=format,
        split_tracks=split_tracks,
        sections=section_list,
    )

    if result == "ref_not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ref '{ref}' not found in repo",
        )
    if result == "no_matching_objects":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {format.value} artifacts found at ref '{ref}'",
        )

    logger.info(
        "✅ Export delivered: repo=%s ref=%s format=%s size=%d bytes",
        repo_id,
        ref,
        format.value,
        len(result.content),
    )
    # Record download event for analytics
    caller = _claims.get("sub") if _claims else None
    dl = MusehubDownloadEvent(
        dl_id=str(uuid.uuid4()),
        repo_id=repo_id,
        ref=ref,
        downloader_id=caller,
        created_at=datetime.now(tz=timezone.utc),
    )
    db.add(dl)
    try:
        await db.commit()
    except Exception:
        await db.rollback()  # analytics failure must not block the download

    return Response(
        content=result.content,
        media_type=result.content_type,
        headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
    )


@router.get(
    "/repos/{repo_id}/tree/{ref}",
    response_model=TreeListResponse,
    summary="List root directory of a Muse repo at a given ref",
)
async def list_tree_root(
    repo_id: str,
    ref: str,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> TreeListResponse:
    """Return a directory listing of the repo root at the given ref.

    Reconstructs the tree from all stored musehub_objects for the repo.
    The ``ref`` parameter is validated against known branches and commit IDs —
    an unknown ref returns 404 so clients get meaningful feedback.

    Query params ``owner`` and ``repo_slug`` are passed through verbatim for
    breadcrumb generation in the response; they are not validated here (the
    repo_id is the authoritative key).

    Returns entries sorted directories-first, then files, both alphabetically.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    ref_valid = await musehub_repository.resolve_ref_for_tree(db, repo_id, ref)
    if not ref_valid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ref '{ref}' not found in repo",
        )
    return await musehub_repository.list_tree(db, repo_id, owner, repo_slug, ref, "")


@router.get(
    "/repos/{repo_id}/tree/{ref}/{path:path}",
    response_model=TreeListResponse,
    summary="List a subdirectory of a Muse repo at a given ref",
)
async def list_tree_subdir(
    repo_id: str,
    ref: str,
    path: str,
    owner: str,
    repo_slug: str,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> TreeListResponse:
    """Return a directory listing of ``path`` inside the repo at the given ref.

    Behaves identically to ``list_tree_root`` but scoped to the subdirectory
    identified by ``path`` (e.g. "tracks", "tracks/stems").  Returns 404 when
    the ref is unknown or the path has no objects underneath it.
    """
    repo = await musehub_repository.get_repo(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repo not found")
    if repo.visibility != "public" and claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access private repos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    ref_valid = await musehub_repository.resolve_ref_for_tree(db, repo_id, ref)
    if not ref_valid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ref '{ref}' not found in repo",
        )
    return await musehub_repository.list_tree(db, repo_id, owner, repo_slug, ref, path)
