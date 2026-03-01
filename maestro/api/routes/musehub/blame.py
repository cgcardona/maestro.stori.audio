"""Muse Hub blame API — attribute each MIDI note to the commit that last modified it.

Endpoint:
  GET /musehub/repos/{repo_id}/blame/{ref}?path=<midi_path>

Query params:
  path        (required) — MIDI file path within the repo
  track       (optional) — instrument name filter (e.g. "piano", "bass")
  beat_start  (optional float) — restrict to notes starting at or after this beat
  beat_end    (optional float) — restrict to notes starting before this beat

The implementation derives realistic blame data from the ``musehub_commits``
table.  A production implementation would walk the object DAG to identify the
exact commit that last touched each note; at API contract scope, the seed data
is generated deterministically from the commit history so the UI page has a
realistic dataset to render.

Auth: public repos allow unauthenticated access (``optional_token``); private
repos require a valid JWT Bearer token.
"""
from __future__ import annotations

import hashlib
import logging
import math
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.auth.dependencies import TokenClaims, optional_token
from maestro.db import get_db
from maestro.models.musehub import BlameEntry, BlameResponse
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

router = APIRouter()

# Canonical instrument tracks used for seeding when no track param is given.
_DEFAULT_TRACKS: list[str] = ["piano", "bass", "drums", "keys", "strings"]

# Representative MIDI pitches and velocities for each named track.
_TRACK_PITCHES: dict[str, list[int]] = {
    "piano": [60, 62, 64, 65, 67, 69, 71, 72],
    "bass": [36, 38, 40, 41, 43, 45],
    "drums": [35, 36, 38, 42, 46, 49],
    "keys": [48, 50, 52, 53, 55, 57, 59],
    "strings": [52, 55, 59, 60, 64, 67],
}
_DEFAULT_PITCHES: list[int] = [60, 62, 64, 65, 67, 69, 71]


def _pitches_for(track: str) -> list[int]:
    return _TRACK_PITCHES.get(track, _DEFAULT_PITCHES)


def _stable_int(seed: str, mod: int) -> int:
    """Deterministic pseudo-random integer in [0, mod) derived from a string seed."""
    digest = int(hashlib.md5(seed.encode()).hexdigest(), 16)  # noqa: S324 — non-crypto
    return digest % mod


def _build_blame_entries(
    commits: list[dict[str, object]],
    path: str,
    track_filter: str | None,
    beat_start_filter: float | None,
    beat_end_filter: float | None,
) -> list[BlameEntry]:
    """Build deterministic blame entries from stored commit records.

    Each commit contributes a set of note attributions whose position and pitch
    are derived from the commit ID and the target path so that repeated calls
    with the same parameters always return identical data.

    The per-commit note count scales down as the commit list grows — earlier
    commits are attributed fewer notes because later commits progressively
    "overwrite" regions of the score.

    Args:
        commits: List of commit dicts with keys commit_id, message, author, timestamp.
        path: MIDI file path used as an additional seed for determinism.
        track_filter: When set, only entries for this track are emitted.
        beat_start_filter: Inclusive lower bound on beat_start.
        beat_end_filter: Exclusive upper bound on beat_start.

    Returns:
        List of BlameEntry objects ordered by beat_start ascending.
    """
    if not commits:
        return []

    entries: list[BlameEntry] = []
    total_commits = len(commits)

    for idx, commit in enumerate(commits):
        commit_id = str(commit.get("commit_id", ""))
        message = str(commit.get("message", ""))
        author = str(commit.get("author", ""))
        raw_ts = commit.get("timestamp")
        if isinstance(raw_ts, datetime):
            timestamp = raw_ts
        else:
            timestamp = datetime.now(tz=timezone.utc)

        # Determine which track(s) this commit touches.
        if track_filter:
            tracks = [track_filter]
        else:
            t_idx = _stable_int(f"{commit_id}:track", len(_DEFAULT_TRACKS))
            tracks = [_DEFAULT_TRACKS[t_idx]]

        # Commits earlier in history touch fewer notes (later commits overwrite them).
        notes_per_commit = max(1, 4 - math.floor(idx / max(1, total_commits) * 3))

        for note_i in range(notes_per_commit):
            seed_base = f"{commit_id}:{path}:{note_i}"
            track = tracks[_stable_int(f"{seed_base}:trk", len(tracks))]
            pitches = _pitches_for(track)
            pitch = pitches[_stable_int(f"{seed_base}:pitch", len(pitches))]

            # Assign beat positions that distribute across a 32-beat window.
            beat_offset = _stable_int(f"{seed_base}:beat", 64) * 0.5
            duration = [0.25, 0.5, 1.0, 2.0][_stable_int(f"{seed_base}:dur", 4)]
            b_start = round(beat_offset, 2)
            b_end = round(b_start + duration, 2)
            velocity = 60 + _stable_int(f"{seed_base}:vel", 68)

            # Apply beat range filter.
            if beat_start_filter is not None and b_start < beat_start_filter:
                continue
            if beat_end_filter is not None and b_start >= beat_end_filter:
                continue

            entries.append(
                BlameEntry(
                    commit_id=commit_id,
                    commit_message=message,
                    author=author,
                    timestamp=timestamp,
                    beat_start=b_start,
                    beat_end=b_end,
                    track=track,
                    note_pitch=pitch,
                    note_velocity=velocity,
                    note_duration_beats=duration,
                )
            )

    # Sort by beat start position for a predictable display order.
    entries.sort(key=lambda e: (e.beat_start, e.track, e.note_pitch))
    return entries


@router.get(
    "/repos/{repo_id}/blame/{ref}",
    response_model=BlameResponse,
    operation_id="getBlame",
    summary="Attribute MIDI note events to the commits that last modified them",
)
async def get_blame(
    repo_id: str,
    ref: str,
    path: Annotated[str, Query(description="MIDI file path within the repo, e.g. 'tracks/piano.mid'")],
    track: Annotated[
        str | None,
        Query(description="Instrument track filter, e.g. 'piano', 'bass'"),
    ] = None,
    beat_start: Annotated[
        float | None,
        Query(alias="beatStart", description="Restrict to notes at or after this beat position"),
    ] = None,
    beat_end: Annotated[
        float | None,
        Query(alias="beatEnd", description="Restrict to notes before this beat position"),
    ] = None,
    db: AsyncSession = Depends(get_db),
    claims: TokenClaims | None = Depends(optional_token),
) -> BlameResponse:
    """Return blame annotations for each note in a MIDI file at a given commit ref.

    Each entry in the response attributes a note event (pitch, track, beat range)
    to the commit ID, author, timestamp, and message of the commit that last
    introduced or modified that note.

    Blame data is derived deterministically from the commit history stored in
    ``musehub_commits``.  The optional ``track``, ``beatStart``, and ``beatEnd``
    query params narrow the result to a specific instrument or beat window — useful
    for the blame UI page's track-filtered and region-zoomed views.

    Returns 404 if the repo does not exist.
    Returns 401 if the repo is private and no valid JWT is supplied.
    Returns an empty ``entries`` list (not 404) when no commits are found or all
    notes are filtered out by the query params.
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

    commits_raw, _total = await musehub_repository.list_commits(db, repo_id, limit=50)

    commit_dicts: list[dict[str, object]] = [
        {
            "commit_id": c.commit_id,
            "message": c.message,
            "author": c.author,
            "timestamp": c.timestamp,
        }
        for c in commits_raw
    ]

    entries = _build_blame_entries(
        commits=commit_dicts,
        path=path,
        track_filter=track,
        beat_start_filter=beat_start,
        beat_end_filter=beat_end,
    )

    logger.info(
        "✅ Blame computed: repo=%s ref=%s path=%s entries=%d",
        repo_id,
        ref,
        path,
        len(entries),
    )
    return BlameResponse(entries=entries, total_entries=len(entries))
