"""Muse Hub listen-page audio scanning service.

Single source of truth for the audio-track scanning and ``TrackListingResponse``
construction logic shared between:

- ``GET /musehub/repos/{repo_id}/listen/{ref}/tracks``  (repos.py)
- ``GET /musehub/ui/{owner}/{repo_slug}/listen/{ref}``  (ui.py)

Why this module exists
----------------------
Prior to extraction, ``_AUDIO_EXTENSIONS``, ``_IMAGE_EXTENSIONS``,
``_FULL_MIX_KEYWORDS``, and the ``AudioTrackEntry`` construction loop were
duplicated between the two handlers.  Any change (e.g. adding ``.aiff`` support)
had to be made in two places, creating a divergence risk.

This service owns all of that logic.  Both handlers are now thin call-through
wrappers around ``build_track_listing()``.
"""
from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession

from maestro.models.musehub import AudioTrackEntry, TrackListingResponse
from maestro.services import musehub_repository

# ---------------------------------------------------------------------------
# Shared constants — single source of truth for all listen-page handlers
# ---------------------------------------------------------------------------

_AUDIO_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".ogg", ".wav", ".m4a", ".flac"})
_IMAGE_EXTENSIONS: frozenset[str] = frozenset({".webp", ".png", ".jpg", ".jpeg"})
_FULL_MIX_KEYWORDS: tuple[str, ...] = ("mix", "full", "master", "bounce")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_audio(path: str) -> bool:
    """Return True when the path extension is a recognised audio format."""
    return os.path.splitext(path)[1].lower() in _AUDIO_EXTENSIONS


def _piano_roll_url(path: str, object_map: dict[str, str], repo_id: str) -> str | None:
    """Return an absolute content URL for a matching piano-roll image, or None.

    Looks for an image file (e.g. ``.webp``) whose basename (without extension)
    matches the audio file's basename — a naming convention produced by the
    Stori DAW when exporting piano-roll snapshots alongside audio stems.
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    for obj_path, obj_id in object_map.items():
        ext = os.path.splitext(obj_path)[1].lower()
        if ext in _IMAGE_EXTENSIONS and os.path.splitext(os.path.basename(obj_path))[0] == stem:
            return f"/api/v1/musehub/repos/{repo_id}/objects/{obj_id}/content"
    return None


# ---------------------------------------------------------------------------
# Public service function
# ---------------------------------------------------------------------------


async def build_track_listing(
    db: AsyncSession,
    repo_id: str,
    ref: str,
) -> TrackListingResponse:
    """Scan a repo's stored objects and build the listen-page track listing.

    Fetches all object metadata for ``repo_id`` from the database, filters to
    audio files, determines a full-mix candidate (preferring files whose
    basename contains a mix/master keyword, falling back to the first audio
    file alphabetically), and constructs one ``AudioTrackEntry`` per audio
    artifact.

    This function performs **no auth or visibility checks** — callers are
    responsible for guarding access before invoking this service.

    Args:
        db: Active async database session.
        repo_id: Internal UUID string for the target repo.
        ref: Commit ref or branch name being listed (threaded through into
            the response for client correlation only — not used for filtering).

    Returns:
        ``TrackListingResponse`` with ``has_renders=False`` when no audio
        artifacts exist, or a fully-populated listing when audio is present.
    """
    objects = await musehub_repository.list_objects(db, repo_id)

    object_map: dict[str, str] = {obj.path: obj.object_id for obj in objects}

    audio_objects = sorted(
        [obj for obj in objects if _is_audio(obj.path)],
        key=lambda o: o.path,
    )

    if not audio_objects:
        return TrackListingResponse(
            repo_id=repo_id,
            ref=ref,
            full_mix_url=None,
            tracks=[],
            has_renders=False,
        )

    def _audio_url(object_id: str) -> str:
        return f"/api/v1/musehub/repos/{repo_id}/objects/{object_id}/content"

    full_mix_obj = next(
        (
            o
            for o in audio_objects
            if any(kw in os.path.basename(o.path).lower() for kw in _FULL_MIX_KEYWORDS)
        ),
        audio_objects[0],
    )

    tracks = [
        AudioTrackEntry(
            name=os.path.splitext(os.path.basename(obj.path))[0],
            path=obj.path,
            object_id=obj.object_id,
            audio_url=_audio_url(obj.object_id),
            piano_roll_url=_piano_roll_url(obj.path, object_map, repo_id),
            size_bytes=obj.size_bytes,
        )
        for obj in audio_objects
    ]

    return TrackListingResponse(
        repo_id=repo_id,
        ref=ref,
        full_mix_url=_audio_url(full_mix_obj.object_id),
        tracks=tracks,
        has_renders=True,
    )
