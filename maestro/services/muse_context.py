"""Muse Context service — structured musical state document for AI agent consumption.

This is the primary read-side interface between Muse VCS and AI music generation
agents.  ``build_muse_context()`` traverses the commit graph to produce a
self-contained ``MuseContextResult`` describing the current musical state of a
repository at a given commit (or HEAD).

When Maestro receives a "generate a new section" request, it calls this service
to obtain the full musical context, passes it to the LLM, and the LLM generates
music that is harmonically, rhythmically, and structurally coherent with the
existing composition.

Design notes
------------
- **Read-only**: this service never writes to the DB.
- **Deterministic**: for the same commit_id, the output is always identical.
- **Active tracks**: derived from file paths in the snapshot manifest.
- **Musical dimensions** (key, tempo, form, harmony): currently None — these
  require MIDI analysis from the Storpheus service and are not yet integrated.
  The schema is fully defined so agents can handle None gracefully today and
  receive populated values once Storpheus integration lands.
"""
from __future__ import annotations

import json
import logging
import pathlib
from dataclasses import asdict, dataclass, field
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result types — every public function signature is a contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MuseHeadCommitInfo:
    """Metadata for the commit that the context document was built from."""

    commit_id: str
    message: str
    author: str
    committed_at: str  # ISO-8601 UTC


@dataclass(frozen=True)
class MuseSectionDetail:
    """Per-section musical detail surfaced when ``--sections`` is requested.

    ``bars`` is None until MIDI region analysis is integrated.
    """

    tracks: list[str]
    bars: Optional[int] = None


@dataclass(frozen=True)
class MuseHarmonicProfile:
    """Harmonic summary — None fields require Storpheus MIDI analysis."""

    chord_progression: Optional[list[str]] = None
    tension_score: Optional[float] = None
    harmonic_rhythm: Optional[float] = None


@dataclass(frozen=True)
class MuseDynamicProfile:
    """Dynamic (volume/intensity) summary — None fields require MIDI analysis."""

    avg_velocity: Optional[int] = None
    dynamic_arc: Optional[str] = None
    peak_section: Optional[str] = None


@dataclass(frozen=True)
class MuseMelodicProfile:
    """Melodic contour summary — None fields require MIDI analysis."""

    contour: Optional[str] = None
    range_semitones: Optional[int] = None
    motifs_detected: Optional[int] = None


@dataclass(frozen=True)
class MuseTrackDetail:
    """Per-track harmonic and dynamic breakdown (``--tracks`` flag)."""

    track_name: str
    harmonic: MuseHarmonicProfile = field(default_factory=MuseHarmonicProfile)
    dynamic: MuseDynamicProfile = field(default_factory=MuseDynamicProfile)


@dataclass(frozen=True)
class MuseMusicalState:
    """Full musical state of the project at a given commit.

    ``active_tracks`` is populated from the snapshot manifest file names.
    All other fields are None until Storpheus MIDI analysis is integrated.
    Agents should treat None values as unknown and generate accordingly.
    """

    active_tracks: list[str]
    key: Optional[str] = None
    mode: Optional[str] = None
    tempo_bpm: Optional[int] = None
    time_signature: Optional[str] = None
    swing_factor: Optional[float] = None
    form: Optional[str] = None
    emotion: Optional[str] = None
    sections: Optional[dict[str, MuseSectionDetail]] = None
    tracks: Optional[list[MuseTrackDetail]] = None
    harmonic_profile: Optional[MuseHarmonicProfile] = None
    dynamic_profile: Optional[MuseDynamicProfile] = None
    melodic_profile: Optional[MuseMelodicProfile] = None


@dataclass(frozen=True)
class MuseHistoryEntry:
    """A single ancestor commit in the evolutionary history of the composition.

    Produced for each of the N most-recent ancestors when ``--depth`` > 0.
    """

    commit_id: str
    message: str
    author: str
    committed_at: str  # ISO-8601 UTC
    active_tracks: list[str]
    key: Optional[str] = None
    tempo_bpm: Optional[int] = None
    emotion: Optional[str] = None


@dataclass(frozen=True)
class MuseContextResult:
    """Complete musical context document for AI agent consumption.

    Returned by ``build_muse_context()``.  Self-contained: an agent receiving
    only this document has everything it needs to generate structurally and
    stylistically coherent music.

    Use ``to_dict()`` before serialising to JSON or YAML.
    """

    repo_id: str
    current_branch: str
    head_commit: MuseHeadCommitInfo
    musical_state: MuseMusicalState
    history: list[MuseHistoryEntry]
    missing_elements: list[str]
    suggestions: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        """Recursively convert to a plain dict suitable for json.dumps / yaml.dump."""
        raw: dict[str, object] = asdict(self)
        return raw


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MUSIC_FILE_EXTENSIONS = frozenset(
    {".mid", ".midi", ".mp3", ".wav", ".aiff", ".aif", ".flac"}
)


def _extract_track_names(manifest: dict[str, str]) -> list[str]:
    """Derive human-readable track names from snapshot manifest file paths.

    Files with recognised music extensions whose stems do not look like raw
    SHA-256 hashes are treated as track names.  The stem is lowercased and
    de-duplicated.

    Example:
        ``{"drums.mid": "abc123", "bass.mid": "def456"}`` → ``["bass", "drums"]``
    """
    tracks: list[str] = []
    for path_str in manifest:
        p = pathlib.PurePosixPath(path_str)
        if p.suffix.lower() in _MUSIC_FILE_EXTENSIONS:
            stem = p.stem.lower()
            # Skip stems that look like raw SHA-256 hashes (64 hex chars)
            if len(stem) == 64 and all(c in "0123456789abcdef" for c in stem):
                continue
            tracks.append(stem)
    return sorted(set(tracks))


async def _load_commit(session: AsyncSession, commit_id: str) -> MuseCliCommit | None:
    """Fetch a single MuseCliCommit by primary key."""
    return await session.get(MuseCliCommit, commit_id)


async def _load_snapshot(
    session: AsyncSession, snapshot_id: str
) -> MuseCliSnapshot | None:
    """Fetch a single MuseCliSnapshot by primary key."""
    return await session.get(MuseCliSnapshot, snapshot_id)


def _read_repo_meta(root: pathlib.Path) -> tuple[str, str]:
    """Return (repo_id, current_branch) from .muse metadata files.

    Reads ``.muse/repo.json`` and ``.muse/HEAD`` synchronously — these are
    tiny JSON/text files that do not warrant async I/O.
    """
    muse_dir = root / ".muse"
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]
    head_ref = (muse_dir / "HEAD").read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref
    return repo_id, branch


def _read_head_commit_id(root: pathlib.Path) -> str | None:
    """Return the HEAD commit ID from the .muse filesystem layout, or None."""
    muse_dir = root / ".muse"
    head_ref = (muse_dir / "HEAD").read_text().strip()
    ref_path = muse_dir / pathlib.Path(head_ref)
    if not ref_path.exists():
        return None
    cid = ref_path.read_text().strip()
    return cid or None


async def _build_history(
    session: AsyncSession,
    start_commit: MuseCliCommit,
    depth: int,
) -> list[MuseHistoryEntry]:
    """Walk the parent chain, returning up to *depth* ancestor entries.

    The *start_commit* (HEAD) is NOT included — it is surfaced separately as
    ``head_commit`` in the result.  Entries are returned newest-first.
    """
    entries: list[MuseHistoryEntry] = []
    current_id: str | None = start_commit.parent_commit_id

    while current_id and len(entries) < depth:
        commit = await _load_commit(session, current_id)
        if commit is None:
            logger.warning("⚠️ History chain broken at %s", current_id[:8])
            break

        tracks: list[str] = []
        snapshot = await _load_snapshot(session, commit.snapshot_id)
        if snapshot is not None and snapshot.manifest:
            tracks = _extract_track_names(snapshot.manifest)

        entries.append(
            MuseHistoryEntry(
                commit_id=commit.commit_id,
                message=commit.message,
                author=commit.author,
                committed_at=commit.committed_at.isoformat(),
                active_tracks=tracks,
            )
        )
        current_id = commit.parent_commit_id

    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_muse_context(
    session: AsyncSession,
    *,
    root: pathlib.Path,
    commit_id: str | None = None,
    depth: int = 5,
    include_sections: bool = False,
    include_tracks: bool = False,
    include_history: bool = False,
) -> MuseContextResult:
    """Build a complete musical context document for AI agent consumption.

    Traverses the commit graph starting from *commit_id* (or HEAD when None)
    and returns a self-contained ``MuseContextResult``.

    The output is deterministic: for the same ``commit_id`` and flags, the
    output is always identical, making it safe to cache and reproduce.

    Args:
        session:          Open async DB session. Read-only — no writes performed.
        root:             Repository root (the directory containing ``.muse/``).
        commit_id:        Target commit ID, or None to use HEAD.
        depth:            Number of ancestor commits to include in ``history``.
                          Pass 0 to omit history entirely.
        include_sections: When True, expand section-level detail in
                          ``musical_state.sections``.  Sections are currently
                          stubbed (one "main" section) until MIDI region
                          metadata is integrated.
        include_tracks:   When True, add per-track harmonic/dynamic detail in
                          ``musical_state.tracks``.
        include_history:  Reserved for future use — will annotate each history
                          entry with dimensional deltas once Storpheus MIDI
                          analysis is integrated.

    Returns:
        MuseContextResult — serialise with ``.to_dict()`` before JSON/YAML output.

    Raises:
        ValueError:  If *commit_id* is provided but not found in the DB.
        RuntimeError: If the repository has no commits yet and commit_id is None.
    """
    repo_id, branch = _read_repo_meta(root)

    # Resolve the target commit
    if commit_id is None:
        resolved_id = _read_head_commit_id(root)
        if not resolved_id:
            raise RuntimeError(
                "Repository has no commits yet. Run `muse commit` first."
            )
    else:
        resolved_id = commit_id

    head_commit_row = await _load_commit(session, resolved_id)
    if head_commit_row is None:
        raise ValueError(f"Commit {resolved_id!r} not found in DB.")

    # Derive active tracks from the snapshot manifest
    snapshot = await _load_snapshot(session, head_commit_row.snapshot_id)
    manifest: dict[str, str] = snapshot.manifest if snapshot is not None else {}
    active_tracks = _extract_track_names(manifest)

    # Optional sections expansion
    sections: dict[str, MuseSectionDetail] | None = None
    if include_sections:
        sections = {"main": MuseSectionDetail(tracks=active_tracks)}

    # Optional per-track detail
    track_details: list[MuseTrackDetail] | None = None
    if include_tracks and active_tracks:
        track_details = [
            MuseTrackDetail(
                track_name=t,
                harmonic=MuseHarmonicProfile(),
                dynamic=MuseDynamicProfile(),
            )
            for t in active_tracks
        ]

    musical_state = MuseMusicalState(
        active_tracks=active_tracks,
        sections=sections,
        tracks=track_details,
    )

    head_commit_info = MuseHeadCommitInfo(
        commit_id=head_commit_row.commit_id,
        message=head_commit_row.message,
        author=head_commit_row.author,
        committed_at=head_commit_row.committed_at.isoformat(),
    )

    history = await _build_history(session, head_commit_row, depth=depth)

    logger.info(
        "✅ Muse context built for commit %s (depth=%d, tracks=%d)",
        resolved_id[:8],
        depth,
        len(active_tracks),
    )

    return MuseContextResult(
        repo_id=repo_id,
        current_branch=branch,
        head_commit=head_commit_info,
        musical_state=musical_state,
        history=history,
        missing_elements=[],
        suggestions={},
    )
