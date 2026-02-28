"""Muse Hub persistence adapter — single point of DB access for Hub entities.

This module is the ONLY place that touches the musehub_* tables.
Route handlers delegate here; no business logic lives in routes.

Boundary rules:
- Must NOT import state stores, SSE queues, or LLM clients.
- Must NOT import maestro.core.* modules.
- May import ORM models from maestro.db.musehub_models.
- May import Pydantic response models from maestro.models.musehub.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db import musehub_models as db
from maestro.models.musehub import (
    BranchResponse,
    CommitResponse,
    ObjectMetaResponse,
    RepoResponse,
    TimelineCommitEvent,
    TimelineEmotionEvent,
    TimelineResponse,
    TimelineSectionEvent,
    TimelineTrackEvent,
)

logger = logging.getLogger(__name__)


def _repo_clone_url(repo_id: str) -> str:
    """Derive a deterministic clone URL from the repo ID.

    The URL format is intentionally simple for MVP; it will be parameterised
    by ``settings.musehub_base_url`` once that setting is introduced.
    """
    return f"/musehub/repos/{repo_id}"


def _to_repo_response(row: db.MusehubRepo) -> RepoResponse:
    return RepoResponse(
        repo_id=row.repo_id,
        name=row.name,
        visibility=row.visibility,
        owner_user_id=row.owner_user_id,
        clone_url=_repo_clone_url(row.repo_id),
        created_at=row.created_at,
    )


def _to_branch_response(row: db.MusehubBranch) -> BranchResponse:
    return BranchResponse(
        branch_id=row.branch_id,
        name=row.name,
        head_commit_id=row.head_commit_id,
    )


def _to_commit_response(row: db.MusehubCommit) -> CommitResponse:
    return CommitResponse(
        commit_id=row.commit_id,
        branch=row.branch,
        parent_ids=list(row.parent_ids or []),
        message=row.message,
        author=row.author,
        timestamp=row.timestamp,
        snapshot_id=row.snapshot_id,
    )


async def create_repo(
    session: AsyncSession,
    *,
    name: str,
    visibility: str,
    owner_user_id: str,
) -> RepoResponse:
    """Persist a new remote repo and return its wire representation."""
    repo = db.MusehubRepo(name=name, visibility=visibility, owner_user_id=owner_user_id)
    session.add(repo)
    await session.flush()  # populate default columns before reading
    await session.refresh(repo)
    logger.info("✅ Created Muse Hub repo %s (%s) for user %s", repo.repo_id, name, owner_user_id)
    return _to_repo_response(repo)


async def get_repo(session: AsyncSession, repo_id: str) -> RepoResponse | None:
    """Return repo metadata, or None if not found."""
    result = await session.get(db.MusehubRepo, repo_id)
    if result is None:
        return None
    return _to_repo_response(result)


async def list_branches(session: AsyncSession, repo_id: str) -> list[BranchResponse]:
    """Return all branches for a repo, ordered by name."""
    stmt = (
        select(db.MusehubBranch)
        .where(db.MusehubBranch.repo_id == repo_id)
        .order_by(db.MusehubBranch.name)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_branch_response(r) for r in rows]


def _to_object_meta_response(row: db.MusehubObject) -> ObjectMetaResponse:
    return ObjectMetaResponse(
        object_id=row.object_id,
        path=row.path,
        size_bytes=row.size_bytes,
        created_at=row.created_at,
    )


async def get_commit(
    session: AsyncSession, repo_id: str, commit_id: str
) -> CommitResponse | None:
    """Return a single commit by ID, or None if not found in this repo."""
    stmt = (
        select(db.MusehubCommit)
        .where(
            db.MusehubCommit.repo_id == repo_id,
            db.MusehubCommit.commit_id == commit_id,
        )
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return None
    return _to_commit_response(row)


async def list_objects(
    session: AsyncSession, repo_id: str
) -> list[ObjectMetaResponse]:
    """Return all object metadata for a repo (no binary content), ordered by path."""
    stmt = (
        select(db.MusehubObject)
        .where(db.MusehubObject.repo_id == repo_id)
        .order_by(db.MusehubObject.path)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_object_meta_response(r) for r in rows]


async def get_object_row(
    session: AsyncSession, repo_id: str, object_id: str
) -> db.MusehubObject | None:
    """Return the raw ORM object row for content delivery, or None if not found.

    Route handlers use this to stream the file from ``disk_path``.
    """
    stmt = (
        select(db.MusehubObject)
        .where(
            db.MusehubObject.repo_id == repo_id,
            db.MusehubObject.object_id == object_id,
        )
    )
    return (await session.execute(stmt)).scalars().first()


async def get_object_by_path(
    session: AsyncSession, repo_id: str, path: str
) -> db.MusehubObject | None:
    """Return the most-recently-created object matching ``path`` in a repo.

    Used by the raw file endpoint to resolve a human-readable path
    (e.g. ``tracks/bass.mid``) to the stored artifact on disk.  When
    multiple objects share the same path (re-pushed content), the newest
    one wins — consistent with Git's ref semantics where HEAD always
    points at the latest version.

    Args:
        session: Active async DB session.
        repo_id: UUID of the target repo.
        path: Client-supplied relative file path, e.g. ``tracks/bass.mid``.

    Returns:
        The matching ORM row, or ``None`` if no object with that path exists.
    """
    stmt = (
        select(db.MusehubObject)
        .where(
            db.MusehubObject.repo_id == repo_id,
            db.MusehubObject.path == path,
        )
        .order_by(desc(db.MusehubObject.created_at))
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def list_commits(
    session: AsyncSession,
    repo_id: str,
    *,
    branch: str | None = None,
    limit: int = 50,
) -> tuple[list[CommitResponse], int]:
    """Return commits for a repo, newest first, optionally filtered by branch.

    Returns a tuple of (commits, total_count).
    """
    base = select(db.MusehubCommit).where(db.MusehubCommit.repo_id == repo_id)
    if branch:
        base = base.where(db.MusehubCommit.branch == branch)

    total_stmt = select(func.count()).select_from(base.subquery())
    total: int = (await session.execute(total_stmt)).scalar_one()

    rows_stmt = base.order_by(desc(db.MusehubCommit.timestamp)).limit(limit)
    rows = (await session.execute(rows_stmt)).scalars().all()
    return [_to_commit_response(r) for r in rows], total


# ── Section / track keyword heuristics ──────────────────────────────────────

_SECTION_KEYWORDS: list[str] = [
    "intro", "verse", "chorus", "bridge", "outro", "hook",
    "pre-chorus", "prechorus", "breakdown", "drop", "build",
    "refrain", "coda", "tag", "interlude",
]

_TRACK_KEYWORDS: list[str] = [
    "bass", "drums", "keys", "piano", "guitar", "synth", "pad",
    "lead", "vocals", "strings", "brass", "horn",
    "flute", "cello", "violin", "organ", "arp", "percussion",
    "kick", "snare", "hi-hat", "hihat", "clap", "melody",
]

_ADDED_VERBS = re.compile(
    r"\b(add(?:ed)?|new|introduce[ds]?|creat(?:ed)?|record(?:ed)?|layer(?:ed)?)\b",
    re.IGNORECASE,
)
_REMOVED_VERBS = re.compile(
    r"\b(remov(?:e[ds]?)?|delet(?:e[ds]?)?|drop(?:ped)?|cut|mute[ds]?)\b",
    re.IGNORECASE,
)


def _infer_action(message: str) -> str:
    """Return 'added' or 'removed' based on verb presence in the commit message."""
    if _REMOVED_VERBS.search(message):
        return "removed"
    return "added"


def _extract_section_events(row: db.MusehubCommit) -> list[TimelineSectionEvent]:
    """Extract zero or more section-change events from a commit message."""
    msg_lower = row.message.lower()
    events: list[TimelineSectionEvent] = []
    for keyword in _SECTION_KEYWORDS:
        if keyword in msg_lower:
            events.append(
                TimelineSectionEvent(
                    commit_id=row.commit_id,
                    timestamp=row.timestamp,
                    section_name=keyword,
                    action=_infer_action(row.message),
                )
            )
    return events


def _extract_track_events(row: db.MusehubCommit) -> list[TimelineTrackEvent]:
    """Extract zero or more track-change events from a commit message."""
    msg_lower = row.message.lower()
    events: list[TimelineTrackEvent] = []
    for keyword in _TRACK_KEYWORDS:
        if keyword in msg_lower:
            events.append(
                TimelineTrackEvent(
                    commit_id=row.commit_id,
                    timestamp=row.timestamp,
                    track_name=keyword,
                    action=_infer_action(row.message),
                )
            )
    return events


def _derive_emotion(row: db.MusehubCommit) -> TimelineEmotionEvent:
    """Derive a deterministic emotion vector from the commit SHA.

    Uses three non-overlapping byte windows of the SHA hex to produce
    valence, energy, and tension in [0.0, 1.0].  Deterministic so the
    timeline is always reproducible without external ML inference.
    """
    sha = row.commit_id
    # Pad short commit IDs (e.g. test fixtures) so indexing is safe.
    sha = sha.ljust(12, "0")
    valence = int(sha[0:4], 16) / 0xFFFF if all(c in "0123456789abcdefABCDEF" for c in sha[0:4]) else 0.5
    energy = int(sha[4:8], 16) / 0xFFFF if all(c in "0123456789abcdefABCDEF" for c in sha[4:8]) else 0.5
    tension = int(sha[8:12], 16) / 0xFFFF if all(c in "0123456789abcdefABCDEF" for c in sha[8:12]) else 0.5
    return TimelineEmotionEvent(
        commit_id=row.commit_id,
        timestamp=row.timestamp,
        valence=round(valence, 4),
        energy=round(energy, 4),
        tension=round(tension, 4),
    )


async def get_timeline_events(
    session: AsyncSession,
    repo_id: str,
    *,
    limit: int = 200,
) -> TimelineResponse:
    """Return a chronological timeline of musical evolution for a repo.

    Fetches up to ``limit`` commits (oldest-first for temporal rendering) and
    derives four event streams:
    - commits: every commit as a timeline marker
    - emotion: deterministic emotion vectors from commit SHAs
    - sections: section-change markers parsed from commit messages
    - tracks: track add/remove markers parsed from commit messages

    Callers must verify the repo exists before calling this function.
    Returns an empty timeline when the repo has no commits.
    """
    total_stmt = select(func.count()).where(db.MusehubCommit.repo_id == repo_id)
    total: int = (await session.execute(total_stmt)).scalar_one()

    rows_stmt = (
        select(db.MusehubCommit)
        .where(db.MusehubCommit.repo_id == repo_id)
        .order_by(db.MusehubCommit.timestamp)  # oldest-first for temporal rendering
        .limit(limit)
    )
    rows = (await session.execute(rows_stmt)).scalars().all()

    commit_events: list[TimelineCommitEvent] = []
    emotion_events: list[TimelineEmotionEvent] = []
    section_events: list[TimelineSectionEvent] = []
    track_events: list[TimelineTrackEvent] = []

    for row in rows:
        commit_events.append(
            TimelineCommitEvent(
                commit_id=row.commit_id,
                branch=row.branch,
                message=row.message,
                author=row.author,
                timestamp=row.timestamp,
                parent_ids=list(row.parent_ids or []),
            )
        )
        emotion_events.append(_derive_emotion(row))
        section_events.extend(_extract_section_events(row))
        track_events.extend(_extract_track_events(row))

    return TimelineResponse(
        commits=commit_events,
        emotion=emotion_events,
        sections=section_events,
        tracks=track_events,
        total_commits=total,
    )
