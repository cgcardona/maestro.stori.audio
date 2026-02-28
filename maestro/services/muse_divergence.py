"""Muse Divergence Engine — musical divergence between two CLI branches.

Computes a per-dimension divergence score by comparing the file-level changes
each branch introduced since their common ancestor (merge base).

Dimensions analysed
-------------------
- ``melodic``    — lead/melody/solo/vocal files
- ``harmonic``   — harmony/chord/key/scale files
- ``rhythmic``   — beat/drum/rhythm/groove/percussion files
- ``structural`` — form/section/arrangement/bridge/chorus/verse files
- ``dynamic``    — mix/master/volume/level files

A path is assigned to one or more dimensions by keyword matching on the
lowercase filename.  Paths that do not match any dimension keyword are counted
as unclassified and excluded from individual dimension scores but may
contribute to the ``overall_score``.

Score formula (per dimension)
------------------------------
Given the sets of paths changed on branch A (``a_dim``) and branch B
(``b_dim``) since the merge base for a specific dimension:

    score = |symmetric_difference(a_dim, b_dim)| / |union(a_dim, b_dim)|

Score 0.0 = both branches changed exactly the same files in this dimension.
Score 1.0 = no overlap — completely diverged.

Boundary rules
--------------
- Must NOT import StateStore, executor, MCP tools, or handlers.
- Must NOT import ``muse_merge_base`` (variation-level LCA) — use
  ``merge_engine.find_merge_base`` (commit-level LCA) for CLI branches.
- May import ``muse_cli.{db, merge_engine, models}``.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli.db import get_commit_snapshot_manifest
from maestro.muse_cli.merge_engine import find_merge_base
from maestro.muse_cli.models import MuseCliCommit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALL_DIMENSIONS: tuple[str, ...] = (
    "melodic",
    "harmonic",
    "rhythmic",
    "structural",
    "dynamic",
)

#: Lowercase keyword patterns used to classify file paths into musical dimensions.
_DIMENSION_PATTERNS: dict[str, tuple[str, ...]] = {
    "melodic":    ("melody", "lead", "solo", "vocal"),
    "harmonic":   ("harm", "chord", "key", "scale"),
    "rhythmic":   ("beat", "drum", "rhythm", "groove", "perc"),
    "structural": ("struct", "form", "section", "bridge", "chorus", "verse", "intro", "outro"),
    "dynamic":    ("mix", "master", "volume", "level", "dyn"),
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class DivergenceLevel(str, Enum):
    """Qualitative label for a per-dimension or overall divergence score.

    Thresholds
    ----------
    - ``NONE`` — score < 0.15
    - ``LOW``  — 0.15 ≤ score < 0.40
    - ``MED``  — 0.40 ≤ score < 0.70
    - ``HIGH`` — score ≥ 0.70
    """

    NONE = "none"
    LOW = "low"
    MED = "med"
    HIGH = "high"


@dataclass(frozen=True)
class DimensionDivergence:
    """Divergence score and description for a single musical dimension.

    Attributes:
        dimension:        Dimension name (e.g. ``"melodic"``).
        level:            Qualitative divergence level.
        score:            Normalised divergence score in [0.0, 1.0].
        description:      Human-readable divergence summary.
        branch_a_summary: How many files in this dimension changed on branch A.
        branch_b_summary: How many files in this dimension changed on branch B.
    """

    dimension: str
    level: DivergenceLevel
    score: float
    description: str
    branch_a_summary: str
    branch_b_summary: str


@dataclass(frozen=True)
class MuseDivergenceResult:
    """Full musical divergence report between two CLI branches.

    Attributes:
        branch_a:        Name of the first branch.
        branch_b:        Name of the second branch.
        common_ancestor: Commit ID of the merge base, or ``None`` if disjoint.
        dimensions:      Per-dimension divergence results.
        overall_score:   Mean of all per-dimension scores in [0.0, 1.0].
    """

    branch_a: str
    branch_b: str
    common_ancestor: str | None
    dimensions: tuple[DimensionDivergence, ...]
    overall_score: float


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def classify_path(path: str) -> set[str]:
    """Return the set of dimensions this file path belongs to.

    Matching is case-insensitive and keyword-based.  A single path may belong
    to multiple dimensions (e.g. ``"vocal_melody.mid"`` → ``melodic``).

    Args:
        path: POSIX-style relative file path from a snapshot manifest.

    Returns:
        Set of dimension names that the path matches.  Empty set if unclassified.
    """
    lower = path.lower()
    return {
        dim
        for dim, patterns in _DIMENSION_PATTERNS.items()
        if any(pat in lower for pat in patterns)
    }


def score_to_level(score: float) -> DivergenceLevel:
    """Map a numeric divergence score to a qualitative :class:`DivergenceLevel`.

    Args:
        score: Normalised score in [0.0, 1.0].

    Returns:
        The appropriate :class:`DivergenceLevel` enum member.
    """
    if score < 0.15:
        return DivergenceLevel.NONE
    if score < 0.40:
        return DivergenceLevel.LOW
    if score < 0.70:
        return DivergenceLevel.MED
    return DivergenceLevel.HIGH


def compute_dimension_divergence(
    dimension: str,
    branch_a_changed: set[str],
    branch_b_changed: set[str],
) -> DimensionDivergence:
    """Compute divergence for a single musical dimension.

    Score = ``|symmetric_diff| / |union|`` over paths in *dimension*:

    - 0.0 → both branches changed exactly the same files.
    - 1.0 → no overlap — completely diverged.

    Args:
        dimension:        Dimension name (one of :data:`ALL_DIMENSIONS`).
        branch_a_changed: Paths changed on branch A since the merge base.
        branch_b_changed: Paths changed on branch B since the merge base.

    Returns:
        A :class:`DimensionDivergence` with score, level, and human summary.
    """
    def _filter(paths: set[str]) -> set[str]:
        return {p for p in paths if dimension in classify_path(p)}

    a_dim = _filter(branch_a_changed)
    b_dim = _filter(branch_b_changed)

    union = a_dim | b_dim
    sym_diff = a_dim.symmetric_difference(b_dim)
    total = len(union)

    if total == 0:
        score = 0.0
        desc = f"No {dimension} changes on either branch."
    else:
        score = len(sym_diff) / total
        if score < 0.15:
            desc = f"Both branches made similar {dimension} changes."
        elif score < 0.40:
            desc = f"Minor {dimension} divergence — mostly aligned."
        elif score < 0.70:
            desc = f"Moderate {dimension} divergence — different directions."
        else:
            desc = f"High {dimension} divergence — branches took different creative paths."

    level = score_to_level(score)
    return DimensionDivergence(
        dimension=dimension,
        level=level,
        score=round(score, 4),
        description=desc,
        branch_a_summary=f"{len(a_dim)} {dimension} file(s) changed",
        branch_b_summary=f"{len(b_dim)} {dimension} file(s) changed",
    )


# ---------------------------------------------------------------------------
# Async DB helpers
# ---------------------------------------------------------------------------


async def get_branch_head_commit_id(
    session: AsyncSession,
    repo_id: str,
    branch: str,
) -> str | None:
    """Return the most recent commit ID on *branch* for *repo_id*.

    Args:
        session:  Open async DB session.
        repo_id:  Repository identifier (from ``.muse/repo.json``).
        branch:   Branch name.

    Returns:
        Commit ID string, or ``None`` if the branch has no commits.
    """
    result = await session.execute(
        select(MuseCliCommit.commit_id)
        .where(
            MuseCliCommit.repo_id == repo_id,
            MuseCliCommit.branch == branch,
        )
        .order_by(MuseCliCommit.committed_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def collect_changed_paths_since(
    session: AsyncSession,
    tip_commit_id: str,
    base_commit_id: str | None,
) -> set[str]:
    """Collect all file paths changed from *base_commit_id* to *tip_commit_id*.

    Loads the snapshot manifests at both ends and returns the union of:
    - Paths added (in tip but not base).
    - Paths deleted (in base but not tip).
    - Paths modified (in both but with different ``object_id``).

    When *base_commit_id* is ``None`` (disjoint histories), all paths in
    *tip_commit_id*'s snapshot are returned.

    Args:
        session:        Open async DB session.
        tip_commit_id:  Branch HEAD commit ID.
        base_commit_id: Merge-base commit ID, or ``None``.

    Returns:
        Set of POSIX paths that changed between base and tip.
    """
    tip_manifest = await get_commit_snapshot_manifest(session, tip_commit_id) or {}
    base_manifest: dict[str, str] = {}
    if base_commit_id:
        base_manifest = await get_commit_snapshot_manifest(session, base_commit_id) or {}

    base_paths = set(base_manifest)
    tip_paths = set(tip_manifest)

    changed: set[str] = set()
    changed |= tip_paths - base_paths          # added
    changed |= base_paths - tip_paths          # deleted
    for path in base_paths & tip_paths:
        if base_manifest[path] != tip_manifest[path]:
            changed.add(path)                  # modified

    return changed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def compute_divergence(
    session: AsyncSession,
    *,
    repo_id: str,
    branch_a: str,
    branch_b: str,
    since: str | None = None,
    dimensions: list[str] | None = None,
) -> MuseDivergenceResult:
    """Compute musical divergence between two CLI branches.

    Finds the common ancestor (merge base), collects file changes since the
    base on each branch, and computes a per-dimension divergence score.

    Args:
        session:    Open async DB session.
        repo_id:    Repository ID (from ``.muse/repo.json``).
        branch_a:   First branch name.
        branch_b:   Second branch name.
        since:      Common ancestor commit ID override (auto-detected if ``None``).
        dimensions: Dimensions to analyse (default: all in :data:`ALL_DIMENSIONS`).

    Returns:
        A :class:`MuseDivergenceResult` with per-dimension scores and
        the resolved common ancestor.

    Raises:
        ValueError: If *branch_a* or *branch_b* has no commits.
    """
    dims: list[str] = list(dimensions) if dimensions else list(ALL_DIMENSIONS)

    # ── Resolve branch head commits ──────────────────────────────────────
    a_head = await get_branch_head_commit_id(session, repo_id, branch_a)
    if a_head is None:
        raise ValueError(
            f"Branch '{branch_a}' has no commits in repo '{repo_id}'."
        )
    b_head = await get_branch_head_commit_id(session, repo_id, branch_b)
    if b_head is None:
        raise ValueError(
            f"Branch '{branch_b}' has no commits in repo '{repo_id}'."
        )

    # ── Find or use provided common ancestor ─────────────────────────────
    base_commit_id: str | None = since
    if base_commit_id is None:
        base_commit_id = await find_merge_base(session, a_head, b_head)

    logger.info(
        "✅ muse divergence: %r vs %r, base=%s",
        branch_a,
        branch_b,
        base_commit_id[:8] if base_commit_id else "none",
    )

    # ── Collect changed paths since merge base ───────────────────────────
    a_changed = await collect_changed_paths_since(session, a_head, base_commit_id)
    b_changed = await collect_changed_paths_since(session, b_head, base_commit_id)

    # ── Per-dimension divergence ─────────────────────────────────────────
    divergences = tuple(
        compute_dimension_divergence(dim, a_changed, b_changed)
        for dim in dims
    )

    overall = (
        round(sum(d.score for d in divergences) / len(divergences), 4)
        if divergences
        else 0.0
    )

    return MuseDivergenceResult(
        branch_a=branch_a,
        branch_b=branch_b,
        common_ancestor=base_commit_id,
        dimensions=divergences,
        overall_score=overall,
    )
