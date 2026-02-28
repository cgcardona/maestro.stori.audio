"""Muse Timeline — chronological view of a composition's musical evolution.

Builds a commit-by-commit timeline from oldest to newest, enriching each
commit with music-semantic metadata extracted from associated tags
(emotion:*, section:*, track:*).  This is the "album liner notes" view of
a project's creative arc.

The service queries:
1. ``muse_cli_commits`` — ordered chronologically (oldest-first).
2. ``muse_cli_tags``    — joined to extract emotion, section, and track tags
   per commit.

Emotion tags (``emotion:melancholic``), section tags (``section:chorus``),
and track tags (``track:bass``) are extracted by namespace prefix.  When no
tags exist the corresponding fields default to ``None`` / empty lists.

Result types
------------
- :class:`MuseTimelineEntry`  — a single commit in the timeline.
- :class:`MuseTimelineResult` — the full ordered collection + section/emotion
  summary computed across all entries.

Callers
-------
``maestro.muse_cli.commands.timeline`` is the primary consumer.  Future
agents may call :func:`build_timeline` directly to derive emotion arcs or
section progress maps for generative decisions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.models import MuseCliCommit, MuseCliTag

logger = logging.getLogger(__name__)

_EMOTION_PREFIX = "emotion:"
_SECTION_PREFIX = "section:"
_TRACK_PREFIX = "track:"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MuseTimelineEntry:
    """A single commit in the musical timeline.

    All music-semantic fields are derived from tags attached to the commit.
    Missing tags produce ``None`` (emotion, section) or empty lists (tracks).

    Fields
    ------
    commit_id:      Full SHA-256 commit ID.
    short_id:       First 7 characters for display purposes.
    committed_at:   Commit timestamp (UTC).
    message:        Commit message (the human-authored intent label).
    emotion:        First ``emotion:*`` tag value, stripped of the prefix.
    sections:       All ``section:*`` tag values, stripped of the prefix.
    tracks:         All ``track:*`` tag values, stripped of the prefix.
    activity:       Number of tracks modified — used to compute block width.
    """

    commit_id: str
    short_id: str
    committed_at: datetime
    message: str
    emotion: str | None
    sections: tuple[str, ...]
    tracks: tuple[str, ...]
    activity: int


@dataclass(frozen=True)
class MuseTimelineResult:
    """Full chronological timeline for a single repository branch.

    ``entries`` is oldest-first.  ``emotion_arc`` lists the unique
    emotion values in chronological order of first appearance.
    ``section_order`` lists section names in order of first commit.

    Fields
    ------
    entries:       Ordered timeline entries (oldest → newest).
    branch:        Branch name this timeline was built from.
    emotion_arc:   Ordered sequence of unique emotion labels (oldest first).
    section_order: Ordered sequence of unique section names (oldest first).
    total_commits: Total number of commits in the timeline.
    """

    entries: tuple[MuseTimelineEntry, ...]
    branch: str
    emotion_arc: tuple[str, ...]
    section_order: tuple[str, ...]
    total_commits: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_prefix(tag: str, prefix: str) -> str | None:
    """Return the value after *prefix* if *tag* starts with it, else None."""
    if tag.startswith(prefix):
        return tag[len(prefix):]
    return None


def _group_tags_by_commit(
    tags: list[MuseCliTag],
) -> dict[str, list[str]]:
    """Build a mapping of commit_id → list of tag strings."""
    grouped: dict[str, list[str]] = {}
    for t in tags:
        grouped.setdefault(t.commit_id, []).append(t.tag)
    return grouped


def _make_entry(
    commit: MuseCliCommit,
    tag_strings: list[str],
) -> MuseTimelineEntry:
    """Construct a :class:`MuseTimelineEntry` from a commit row and its tags."""
    emotions: list[str] = []
    sections: list[str] = []
    tracks: list[str] = []

    for tag in tag_strings:
        emotion_val = _extract_prefix(tag, _EMOTION_PREFIX)
        if emotion_val is not None:
            emotions.append(emotion_val)
            continue
        section_val = _extract_prefix(tag, _SECTION_PREFIX)
        if section_val is not None:
            sections.append(section_val)
            continue
        track_val = _extract_prefix(tag, _TRACK_PREFIX)
        if track_val is not None:
            tracks.append(track_val)

    return MuseTimelineEntry(
        commit_id=commit.commit_id,
        short_id=commit.commit_id[:7],
        committed_at=commit.committed_at,
        message=commit.message,
        emotion=emotions[0] if emotions else None,
        sections=tuple(sections),
        tracks=tuple(tracks),
        activity=len(tracks) if tracks else 1,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_timeline(
    session: AsyncSession,
    repo_id: str,
    branch: str,
    head_commit_id: str,
    limit: int = 1000,
) -> MuseTimelineResult:
    """Build a chronological musical timeline for *branch* in *repo_id*.

    Walks the parent chain from *head_commit_id* (oldest-first after
    reversal) then queries associated tags in a single batch to avoid N+1
    round-trips.

    Args:
        session:        Open async SQLAlchemy session.
        repo_id:        Repository scope.
        branch:         Branch name for display in the result.
        head_commit_id: SHA-256 of the branch HEAD commit.
        limit:          Maximum commits to walk (default 1000).

    Returns:
        :class:`MuseTimelineResult` sorted oldest-first.
    """
    # --- Walk the parent chain newest-first (same pattern as muse log) ---
    commits_newest_first: list[MuseCliCommit] = []
    current_id: str | None = head_commit_id
    while current_id and len(commits_newest_first) < limit:
        commit = await session.get(MuseCliCommit, current_id)
        if commit is None:
            logger.warning("⚠️ Timeline: commit %s not found — chain broken", current_id[:8])
            break
        commits_newest_first.append(commit)
        current_id = commit.parent_commit_id

    # Reverse to oldest-first for timeline display.
    commits: list[MuseCliCommit] = list(reversed(commits_newest_first))

    if not commits:
        return MuseTimelineResult(
            entries=(),
            branch=branch,
            emotion_arc=(),
            section_order=(),
            total_commits=0,
        )

    # --- Batch-fetch all tags for the commit set ---
    commit_ids = [c.commit_id for c in commits]
    tag_stmt = select(MuseCliTag).where(
        MuseCliTag.repo_id == repo_id,
        MuseCliTag.commit_id.in_(commit_ids),
    )
    tag_result = await session.execute(tag_stmt)
    all_tags: list[MuseCliTag] = list(tag_result.scalars().all())
    tags_by_commit = _group_tags_by_commit(all_tags)

    # --- Build entries ---
    entries: list[MuseTimelineEntry] = [
        _make_entry(c, tags_by_commit.get(c.commit_id, []))
        for c in commits
    ]

    # --- Derive summaries ---
    emotion_arc: list[str] = []
    seen_emotions: set[str] = set()
    section_order: list[str] = []
    seen_sections: set[str] = set()

    for entry in entries:
        if entry.emotion and entry.emotion not in seen_emotions:
            emotion_arc.append(entry.emotion)
            seen_emotions.add(entry.emotion)
        for sec in entry.sections:
            if sec not in seen_sections:
                section_order.append(sec)
                seen_sections.add(sec)

    logger.info(
        "✅ muse timeline: %d commit(s), %d emotion(s), %d section(s) (repo=%s branch=%s)",
        len(entries),
        len(emotion_arc),
        len(section_order),
        repo_id[:8],
        branch,
    )

    return MuseTimelineResult(
        entries=tuple(entries),
        branch=branch,
        emotion_arc=tuple(emotion_arc),
        section_order=tuple(section_order),
        total_commits=len(entries),
    )
