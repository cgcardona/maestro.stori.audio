"""Muse Groove-Check Service — rhythmic drift analysis across commits.

Computes per-commit groove scores by measuring note-onset deviation
from the quantization grid, then detects which commits introduced
rhythmic inconsistency relative to their neighbors.

"Groove drift" is the absolute change in average onset deviation between
adjacent commits.  A commit with a large drift delta is the one that
"killed the groove."

This is a stub implementation that demonstrates the correct CLI contract
and result schema.  Full MIDI content analysis will be wired in once
Storpheus exposes a rhythmic quantization introspection route.

Boundary rules:
  - Pure data — no side effects, no external I/O.
  - Must NOT import StateStore, EntityRegistry, or executor modules.
  - Must NOT import LLM handlers or maestro_* pipeline modules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLD = 0.1  # beats — flag commits whose drift_delta exceeds this
DEFAULT_COMMIT_LIMIT = 10  # fallback window when no explicit range is given


# ---------------------------------------------------------------------------
# Result types (stable CLI contract)
# ---------------------------------------------------------------------------


class GrooveStatus(str, Enum):
    """Per-commit groove assessment relative to the configured threshold.

    OK   — drift_delta ≤ threshold; rhythm is consistent with neighbors.
    WARN — drift_delta is between threshold and 2× threshold; mild drift.
    FAIL — drift_delta > 2× threshold; likely culprit for groove regression.
    """

    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CommitGrooveMetrics:
    """Rhythmic groove metrics for a single commit.

    groove_score  — average note-onset deviation from the quantization grid,
                    in beats.  Lower is tighter (closer to the grid).
    drift_delta   — absolute change in groove_score relative to the prior
                    commit in the range.  The first commit always has delta 0.0.
    status        — OK / WARN / FAIL classification against the threshold.
    commit        — short commit ref (8 hex chars or resolved ID).
    track         — track scope used for analysis, or "all".
    section       — section scope used for analysis, or "all".
    midi_files    — number of MIDI snapshots analysed for this commit.
    """

    commit: str
    groove_score: float
    drift_delta: float
    status: GrooveStatus
    track: str = "all"
    section: str = "all"
    midi_files: int = 0


@dataclass(frozen=True)
class GrooveCheckResult:
    """Aggregate result for a `muse groove-check` run.

    commit_range    — the range string that was analysed (e.g. "HEAD~5..HEAD").
    threshold       — drift threshold used for WARN/FAIL classification.
    total_commits   — total commits in the analysis window.
    flagged_commits — number of commits with status WARN or FAIL.
    worst_commit    — commit ref with the highest drift_delta, or empty string.
    entries         — per-commit metrics, oldest-first.
    """

    commit_range: str
    threshold: float
    total_commits: int
    flagged_commits: int
    worst_commit: str
    entries: tuple[CommitGrooveMetrics, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Status classification
# ---------------------------------------------------------------------------


def classify_status(drift_delta: float, threshold: float) -> GrooveStatus:
    """Classify a drift delta against the threshold.

    Args:
        drift_delta: Absolute change in groove_score vs. prior commit.
        threshold:   User-configurable WARN boundary in beats.

    Returns:
        :class:`GrooveStatus` OK, WARN, or FAIL.
    """
    if drift_delta <= threshold:
        return GrooveStatus.OK
    if drift_delta <= threshold * 2:
        return GrooveStatus.WARN
    return GrooveStatus.FAIL


# ---------------------------------------------------------------------------
# Stub data factories
# ---------------------------------------------------------------------------

_STUB_COMMITS: tuple[tuple[str, float, int], ...] = (
    ("a1b2c3d4", 0.04, 3),
    ("e5f6a7b8", 0.05, 3),
    ("c9d0e1f2", 0.06, 3),
    ("a3b4c5d6", 0.09, 3),
    ("e7f8a9b0", 0.15, 3),  # groove degraded here
    ("c1d2e3f4", 0.13, 3),
    ("a5b6c7d8", 0.08, 3),  # recovered
)


def build_stub_entries(
    *,
    threshold: float,
    track: Optional[str],
    section: Optional[str],
    limit: int,
) -> list[CommitGrooveMetrics]:
    """Produce stub CommitGrooveMetrics for a commit window.

    Returns the last ``limit`` entries from the stub table with
    drift_delta and status computed against ``threshold``.

    Args:
        threshold: WARN/FAIL boundary in beats.
        track:     Track filter (stored in metadata; no content effect in stub).
        section:   Section filter (stored in metadata; no content effect in stub).
        limit:     Maximum number of commits to return.

    Returns:
        List of :class:`CommitGrooveMetrics`, oldest-first.
    """
    sample = list(_STUB_COMMITS[-limit:])
    entries: list[CommitGrooveMetrics] = []
    prev_score: Optional[float] = None
    for commit, score, midi_files in sample:
        delta = abs(score - prev_score) if prev_score is not None else 0.0
        status = classify_status(delta, threshold)
        entries.append(
            CommitGrooveMetrics(
                commit=commit,
                groove_score=round(score, 4),
                drift_delta=round(delta, 4),
                status=status,
                track=track or "all",
                section=section or "all",
                midi_files=midi_files,
            )
        )
        prev_score = score
    return entries


def compute_groove_check(
    *,
    commit_range: str,
    threshold: float = DEFAULT_THRESHOLD,
    track: Optional[str] = None,
    section: Optional[str] = None,
    limit: int = DEFAULT_COMMIT_LIMIT,
) -> GrooveCheckResult:
    """Compute groove-check metrics for a commit range.

    Pure function — safe to call from tests without any repository context.
    The stub implementation produces deterministic, musically-realistic results
    using hardcoded representative data.

    Args:
        commit_range: Commit range string used for display (e.g. "HEAD~5..HEAD").
        threshold:    Drift threshold in beats (default 0.1).
        track:        Restrict analysis to a named instrument track.
        section:      Restrict analysis to a named musical section.
        limit:        Maximum number of commits to include (default 10).

    Returns:
        A :class:`GrooveCheckResult` with per-commit metrics and summary fields.
    """
    entries = build_stub_entries(
        threshold=threshold,
        track=track,
        section=section,
        limit=limit,
    )
    flagged = [e for e in entries if e.status != GrooveStatus.OK]
    worst = max(entries, key=lambda e: e.drift_delta, default=None)
    worst_commit = worst.commit if worst and worst.drift_delta > 0 else ""

    result = GrooveCheckResult(
        commit_range=commit_range,
        threshold=threshold,
        total_commits=len(entries),
        flagged_commits=len(flagged),
        worst_commit=worst_commit,
        entries=tuple(entries),
    )

    logger.info(
        "✅ Groove-check: range=%s threshold=%.3f flagged=%d/%d worst=%s",
        commit_range,
        threshold,
        result.flagged_commits,
        result.total_commits,
        result.worst_commit or "none",
    )
    return result
