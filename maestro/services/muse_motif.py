"""Muse Motif Engine — identify, track, and compare recurring melodic motifs.

A *motif* is a short melodic or rhythmic idea — a sequence of pitches and/or
durations — that reappears and transforms throughout a composition. This
module implements the core analysis engine used by ``muse motif find``,
``muse motif track``, and ``muse motif diff``.

Design
------
- Melodic identity is encoded as **interval sequences** (signed semitone
  differences between consecutive pitches) so that transpositions of the same
  motif hash to the same fingerprint.
- Rhythmic identity is encoded as **relative duration ratios** normalised
  against the shortest note in the sequence so that augmented / diminished
  versions are detectable.
- A motif fingerprint is the concatenation of its interval sequence, allowing
  fast set-based matching across commits.
- Transformation detection (inversion, retrograde, augmentation, diminution)
  compares the found fingerprint against the query fingerprint's variants.

Boundary rules
--------------
- Must NOT import StateStore, executor, MCP tools, or route handlers.
- May import ``muse_cli.{db, models}``.
- Pure helpers (fingerprint computation, transformation detection) are
  synchronous and fully testable without a DB.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Primitive types
# ---------------------------------------------------------------------------

#: A melodic motif expressed as a sequence of semitone intervals.
#: e.g. [2, 2, -1, 2] for a 4-note motif with those ascending/descending steps.
IntervalSequence = tuple[int, ...]

#: A rhythmic motif expressed as relative duration ratios (float).
#: e.g. (1.0, 2.0, 1.0) means short–long–short relative durations.
RhythmSequence = tuple[float, ...]


# ---------------------------------------------------------------------------
# Transformation vocabulary
# ---------------------------------------------------------------------------


class MotifTransformation(str, Enum):
    """Detected relationship between a found motif and the query motif.

    - ``EXACT``       — identical interval sequence (possibly transposed).
    - ``INVERSION``   — each interval negated (melodic mirror).
    - ``RETROGRADE``  — interval sequence reversed.
    - ``RETRO_INV``   — retrograde + inversion combined.
    - ``AUGMENTED``   — same intervals; note durations scaled up.
    - ``DIMINISHED``  — same intervals; note durations scaled down.
    - ``APPROXIMATE`` — similar contour but not an exact variant.
    """

    EXACT = "exact"
    INVERSION = "inversion"
    RETROGRADE = "retrograde"
    RETRO_INV = "retro_inv"
    AUGMENTED = "augmented"
    DIMINISHED = "diminished"
    APPROXIMATE = "approximate"


# ---------------------------------------------------------------------------
# Named result types (public API contract)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MotifOccurrence:
    """A single occurrence of a motif within a commit or pattern search.

    Attributes:
        commit_id:       Short commit SHA where the motif was found.
        track:           Track name (e.g. ``"melody"``, ``"bass"``).
        section:         Named section the occurrence falls in (optional).
        start_position:  Position index of the first note of the motif.
        transformation:  Relationship to the query motif.
        pitch_sequence:  Literal pitch values at this occurrence (MIDI note numbers).
        interval_fingerprint: Normalised interval sequence used for matching.
    """

    commit_id: str
    track: str
    section: Optional[str]
    start_position: int
    transformation: MotifTransformation
    pitch_sequence: tuple[int, ...]
    interval_fingerprint: IntervalSequence


@dataclass(frozen=True)
class MotifFindResult:
    """Results from ``muse motif find`` — recurring patterns in a single commit.

    Attributes:
        commit_id:    Short commit SHA analysed.
        branch:       Branch name.
        min_length:   Minimum motif length requested.
        motifs:       Detected recurring motif groups, sorted by occurrence count desc.
        total_found:  Total number of distinct recurring motifs identified.
        source:       ``"stub"`` until full MIDI analysis is wired; ``"live"`` thereafter.
    """

    commit_id: str
    branch: str
    min_length: int
    motifs: tuple[MotifGroup, ...]
    total_found: int
    source: str


@dataclass(frozen=True)
class MotifGroup:
    """A single recurring motif and all its occurrences in the scanned commit.

    Attributes:
        fingerprint:   Normalised interval sequence (the motif's identity).
        count:         Number of times the motif appears.
        occurrences:   All detected occurrences.
        label:         Human-readable contour label (e.g. ``"ascending-step"``,
                       ``"arch"``, ``"descending-leap"``).
    """

    fingerprint: IntervalSequence
    count: int
    occurrences: tuple[MotifOccurrence, ...]
    label: str


@dataclass(frozen=True)
class MotifTrackResult:
    """Results from ``muse motif track`` — appearances of a pattern across history.

    Attributes:
        pattern:      The query pattern (as a space-separated pitch string or
                      interval fingerprint).
        fingerprint:  Normalised interval sequence derived from the pattern.
        occurrences:  All commits where the motif (or a transformation) was found.
        total_commits_scanned: How many commits were searched.
        source:       ``"stub"`` or ``"live"``.
    """

    pattern: str
    fingerprint: IntervalSequence
    occurrences: tuple[MotifOccurrence, ...]
    total_commits_scanned: int
    source: str


@dataclass(frozen=True)
class MotifDiffEntry:
    """One side of a motif diff comparison.

    Attributes:
        commit_id:    Short commit SHA.
        fingerprint:  Interval sequence at this commit.
        label:        Contour label.
        pitch_sequence: Literal pitches (if available).
    """

    commit_id: str
    fingerprint: IntervalSequence
    label: str
    pitch_sequence: tuple[int, ...]


@dataclass(frozen=True)
class MotifDiffResult:
    """Results from ``muse motif diff`` — how a motif transformed between commits.

    Attributes:
        commit_a:       Analysis of the motif at the first commit.
        commit_b:       Analysis of the motif at the second commit.
        transformation: How the motif changed from commit A to commit B.
        description:    Human-readable description of the transformation.
        source:         ``"stub"`` or ``"live"``.
    """

    commit_a: MotifDiffEntry
    commit_b: MotifDiffEntry
    transformation: MotifTransformation
    description: str
    source: str


@dataclass(frozen=True)
class SavedMotif:
    """A named motif stored in ``.muse/motifs/``.

    Attributes:
        name:        User-assigned motif name (e.g. ``"main-theme"``).
        fingerprint: Stored interval fingerprint.
        created_at:  ISO-8601 timestamp when the motif was named.
        description: Optional free-text annotation.
    """

    name: str
    fingerprint: IntervalSequence
    created_at: str
    description: Optional[str]


@dataclass(frozen=True)
class MotifListResult:
    """Results from ``muse motif list`` — all named motifs in the repository.

    Attributes:
        motifs: All saved named motifs.
        source: ``"stub"`` or ``"live"``.
    """

    motifs: tuple[SavedMotif, ...]
    source: str


# ---------------------------------------------------------------------------
# Pure fingerprint helpers
# ---------------------------------------------------------------------------


def pitches_to_intervals(pitches: tuple[int, ...]) -> IntervalSequence:
    """Convert a sequence of MIDI pitch numbers to a signed semitone interval sequence.

    The interval representation is transposition-invariant — the same motif
    at different pitch levels produces identical fingerprints.

    Args:
        pitches: Sequence of MIDI note numbers (0–127), length ≥ 2.

    Returns:
        Tuple of signed semitone differences, length = ``len(pitches) - 1``.
        Returns an empty tuple for inputs shorter than 2 notes.
    """
    if len(pitches) < 2:
        return ()
    return tuple(pitches[i + 1] - pitches[i] for i in range(len(pitches) - 1))


def invert_intervals(intervals: IntervalSequence) -> IntervalSequence:
    """Return the melodic inversion of an interval sequence (negate each step).

    Inversion mirrors the motif around its starting pitch so that what went
    up now goes down by the same interval.

    Args:
        intervals: Normalised interval sequence from :func:`pitches_to_intervals`.

    Returns:
        Interval sequence with each element negated.
    """
    return tuple(-i for i in intervals)


def retrograde_intervals(intervals: IntervalSequence) -> IntervalSequence:
    """Return the retrograde (reverse) of an interval sequence.

    Note: reversing the intervals gives the same pitches played backward,
    which is not the same as reversing the pitch list directly.

    Args:
        intervals: Normalised interval sequence.

    Returns:
        Reversed interval sequence, with each element negated (retrograde
        of pitches is the negation of reversed intervals).
    """
    return tuple(-i for i in reversed(intervals))


def detect_transformation(
    query: IntervalSequence,
    candidate: IntervalSequence,
) -> Optional[MotifTransformation]:
    """Determine the transformation relationship between a query and candidate motif.

    Checks for exact match (transposition), inversion, retrograde, and the
    combined retrograde-inversion.

    Args:
        query:     The reference interval sequence.
        candidate: The interval sequence to test.

    Returns:
        The :class:`MotifTransformation` if a relationship is detected, or
        ``None`` if no recognised transformation applies.
    """
    if candidate == query:
        return MotifTransformation.EXACT
    if candidate == invert_intervals(query):
        return MotifTransformation.INVERSION
    if candidate == retrograde_intervals(query):
        return MotifTransformation.RETROGRADE
    if candidate == invert_intervals(retrograde_intervals(query)):
        return MotifTransformation.RETRO_INV
    return None


def contour_label(intervals: IntervalSequence) -> str:
    """Assign a human-readable contour label to an interval sequence.

    Labels encode the overall melodic direction and whether movement is
    predominantly stepwise (≤2 semitones) or leap-based (>2 semitones).

    Args:
        intervals: Normalised interval sequence. Empty sequences return ``"static"``.

    Returns:
        One of: ``"ascending-step"``, ``"ascending-leap"``,
        ``"descending-step"``, ``"descending-leap"``, ``"arch"``,
        ``"valley"``, ``"oscillating"``, or ``"static"``.
    """
    if not intervals:
        return "static"
    net = sum(intervals)
    max_step = max(abs(i) for i in intervals)
    direction_changes = sum(
        1
        for j in range(len(intervals) - 1)
        if (intervals[j] > 0) != (intervals[j + 1] > 0)
    )
    if direction_changes >= len(intervals) // 2:
        return "oscillating"
    ups = sum(1 for i in intervals if i > 0)
    downs = sum(1 for i in intervals if i < 0)
    if ups > 0 and downs > 0:
        if ups > downs:
            return "arch"
        if downs > ups:
            return "valley"
        # Equal ups and downs: arch if motion starts upward, valley if downward.
        return "arch" if intervals[0] > 0 else "valley"
    stepwise = max_step <= 2
    if net > 0:
        return "ascending-step" if stepwise else "ascending-leap"
    if net < 0:
        return "descending-step" if stepwise else "descending-leap"
    return "static"


def parse_pitch_string(pattern: str) -> tuple[int, ...]:
    """Parse a space-separated pitch-name or MIDI-number string into pitch values.

    Supports:
    - MIDI integers:  ``"60 62 64 67"``
    - Note names:     ``"C D E G"`` (middle octave assumed, sharps as ``C#``/``Cs``)

    Args:
        pattern: Space-separated pitch tokens.

    Returns:
        Tuple of MIDI note numbers (0–127).

    Raises:
        ValueError: If any token cannot be parsed as a MIDI number or note name.
    """
    _NOTE_MAP: dict[str, int] = {
        "C": 60, "C#": 61, "CS": 61, "DB": 61,
        "D": 62, "D#": 63, "DS": 63, "EB": 63,
        "E": 64, "F": 65, "F#": 66, "FS": 66, "GB": 66,
        "G": 67, "G#": 68, "GS": 68, "AB": 68,
        "A": 69, "A#": 70, "AS": 70, "BB": 70,
        "B": 71,
    }
    result: list[int] = []
    for token in pattern.strip().split():
        upper = token.upper().replace("-", "")
        if upper in _NOTE_MAP:
            result.append(_NOTE_MAP[upper])
        else:
            try:
                midi = int(token)
                if not 0 <= midi <= 127:
                    raise ValueError(f"MIDI pitch {midi} out of range [0, 127]")
                result.append(midi)
            except ValueError as exc:
                raise ValueError(f"Cannot parse pitch token {token!r}") from exc
    return tuple(result)


# ---------------------------------------------------------------------------
# Stub data helpers
# ---------------------------------------------------------------------------

_STUB_MOTIFS: list[tuple[IntervalSequence, str, int]] = [
    ((2, 2, -1, 2), "ascending-step", 3),
    ((-2, -2, 1, -2), "descending-step", 2),
    ((4, -2, 3), "arch", 2),
]


def _stub_motif_groups(
    commit_id: str,
    track: Optional[str],
    min_length: int,
) -> tuple[MotifGroup, ...]:
    """Return placeholder MotifGroup entries for stub mode.

    Args:
        commit_id:  Short commit SHA to embed in occurrences.
        track:      Track filter (if provided, used as the occurrence track name).
        min_length: Minimum motif length filter (intervals of length ≥ min_length - 1).

    Returns:
        Tuple of :class:`MotifGroup` objects filtered to min_length.
    """
    groups: list[MotifGroup] = []
    for fp, label, count in _STUB_MOTIFS:
        if len(fp) + 1 < min_length:
            continue
        track_name = track or "melody"
        pitches = _intervals_to_pitches(fp, start=60)
        occurrence = MotifOccurrence(
            commit_id=commit_id,
            track=track_name,
            section=None,
            start_position=0,
            transformation=MotifTransformation.EXACT,
            pitch_sequence=pitches,
            interval_fingerprint=fp,
        )
        groups.append(
            MotifGroup(
                fingerprint=fp,
                count=count,
                occurrences=(occurrence,) * count,
                label=label,
            )
        )
    return tuple(sorted(groups, key=lambda g: g.count, reverse=True))


def _intervals_to_pitches(
    intervals: IntervalSequence,
    start: int = 60,
) -> tuple[int, ...]:
    """Reconstruct a pitch sequence from an interval sequence starting at *start*.

    Args:
        intervals: Signed semitone intervals.
        start:     MIDI pitch of the first note (default: 60 = middle C).

    Returns:
        Tuple of MIDI pitch values.
    """
    pitches: list[int] = [start]
    for step in intervals:
        pitches.append(pitches[-1] + step)
    return tuple(pitches)


# ---------------------------------------------------------------------------
# Public async API (stub implementations — contract-correct)
# ---------------------------------------------------------------------------


async def find_motifs(
    *,
    commit_id: str,
    branch: str,
    min_length: int = 3,
    track: Optional[str] = None,
    section: Optional[str] = None,
    as_json: bool = False,
) -> MotifFindResult:
    """Detect recurring melodic/rhythmic patterns in a single commit.

    Scans the MIDI data at *commit_id* for note sequences that appear more
    than once within the commit, groups them by their transposition-invariant
    fingerprint, and returns them sorted by occurrence count.

    Args:
        commit_id:  Short or full commit SHA to analyse.
        branch:     Branch name (for context in the result).
        min_length: Minimum motif length in notes (default: 3). Shorter
                    motifs tend to be musically trivial.
        track:      Restrict analysis to a single named track, or ``None`` for all.
        section:    Restrict to a named section/region, or ``None`` for all.
        as_json:    Unused here — rendered by the CLI layer.

    Returns:
        A :class:`MotifFindResult` with all detected recurring motifs.
    """
    logger.info("✅ muse motif find: commit=%s min_length=%d", commit_id[:8], min_length)
    groups = _stub_motif_groups(commit_id[:8], track=track, min_length=min_length)
    return MotifFindResult(
        commit_id=commit_id[:8],
        branch=branch,
        min_length=min_length,
        motifs=groups,
        total_found=len(groups),
        source="stub",
    )


async def track_motif(
    *,
    pattern: str,
    commit_ids: list[str],
) -> MotifTrackResult:
    """Search all commits for appearances of a specific motif pattern.

    Parses *pattern* as a sequence of pitch names or MIDI numbers, derives the
    transposition-invariant interval fingerprint, and scans each commit in
    *commit_ids* for exact matches or recognised transformations (inversion,
    retrograde, retrograde-inversion).

    Args:
        pattern:     Space-separated pitch names (e.g. ``"C D E G"``) or MIDI
                     numbers (e.g. ``"60 62 64 67"``).
        commit_ids:  Ordered list of commit SHAs to scan (newest first).

    Returns:
        A :class:`MotifTrackResult` with all occurrences found.

    Raises:
        ValueError: If *pattern* cannot be parsed into a valid pitch sequence.
    """
    pitches = parse_pitch_string(pattern)
    fingerprint = pitches_to_intervals(pitches)
    logger.info(
        "✅ muse motif track: pattern=%r fingerprint=%r commits=%d",
        pattern,
        fingerprint,
        len(commit_ids),
    )

    occurrences: list[MotifOccurrence] = []
    for cid in commit_ids:
        short = cid[:8]
        occ = MotifOccurrence(
            commit_id=short,
            track="melody",
            section=None,
            start_position=0,
            transformation=MotifTransformation.EXACT,
            pitch_sequence=pitches,
            interval_fingerprint=fingerprint,
        )
        occurrences.append(occ)

    return MotifTrackResult(
        pattern=pattern,
        fingerprint=fingerprint,
        occurrences=tuple(occurrences),
        total_commits_scanned=len(commit_ids),
        source="stub",
    )


async def diff_motifs(
    *,
    commit_a_id: str,
    commit_b_id: str,
) -> MotifDiffResult:
    """Show how the dominant motif transformed between two commits.

    Extracts the most prominent motif from each commit and computes the
    transformation relationship between them (exact, inversion, retrograde, etc.).

    Args:
        commit_a_id: Short or full SHA of the first (earlier) commit.
        commit_b_id: Short or full SHA of the second (later) commit.

    Returns:
        A :class:`MotifDiffResult` describing the transformation.
    """
    fp_a: IntervalSequence = (2, 2, -1, 2)
    fp_b: IntervalSequence = (-2, -2, 1, -2)

    transformation = detect_transformation(fp_a, fp_b) or MotifTransformation.APPROXIMATE

    descriptions: dict[MotifTransformation, str] = {
        MotifTransformation.EXACT: "The motif is transposition-equivalent — same shape, different pitch level.",
        MotifTransformation.INVERSION: "The motif was inverted — ascending intervals became descending.",
        MotifTransformation.RETROGRADE: "The motif was played in retrograde — same pitches reversed.",
        MotifTransformation.RETRO_INV: "The motif was retrograde-inverted — reversed and mirrored.",
        MotifTransformation.AUGMENTED: "The motif was augmented — note durations scaled up.",
        MotifTransformation.DIMINISHED: "The motif was diminished — note durations compressed.",
        MotifTransformation.APPROXIMATE: "The motif contour changed significantly between commits.",
    }

    entry_a = MotifDiffEntry(
        commit_id=commit_a_id[:8],
        fingerprint=fp_a,
        label=contour_label(fp_a),
        pitch_sequence=_intervals_to_pitches(fp_a),
    )
    entry_b = MotifDiffEntry(
        commit_id=commit_b_id[:8],
        fingerprint=fp_b,
        label=contour_label(fp_b),
        pitch_sequence=_intervals_to_pitches(fp_b),
    )

    logger.info(
        "✅ muse motif diff: %s → %s, transformation=%s",
        commit_a_id[:8],
        commit_b_id[:8],
        transformation.value,
    )

    return MotifDiffResult(
        commit_a=entry_a,
        commit_b=entry_b,
        transformation=transformation,
        description=descriptions[transformation],
        source="stub",
    )


async def list_motifs(
    *,
    muse_dir_path: str,
) -> MotifListResult:
    """List all named motifs stored in ``.muse/motifs/``.

    Named motifs are user-annotated melodic ideas saved for future recall.
    This command surfaces them in a structured format suitable for both
    human review and agent consumption.

    Args:
        muse_dir_path: Absolute path to the ``.muse/`` directory.

    Returns:
        A :class:`MotifListResult` with all saved named motifs.
    """
    logger.info("✅ muse motif list: scanning %s", muse_dir_path)
    stub_motifs: tuple[SavedMotif, ...] = (
        SavedMotif(
            name="main-theme",
            fingerprint=(2, 2, -1, 2),
            created_at="2026-01-15T10:30:00Z",
            description="The central ascending motif introduced in the opening.",
        ),
        SavedMotif(
            name="bass-riff",
            fingerprint=(-2, -3, 2),
            created_at="2026-01-20T14:15:00Z",
            description="Chromatic bass figure used throughout the bridge.",
        ),
    )
    return MotifListResult(motifs=stub_motifs, source="stub")
