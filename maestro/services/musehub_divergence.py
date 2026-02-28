"""Muse Hub Divergence Engine — musical divergence between two remote branches.

Computes per-dimension divergence scores by comparing the commit history on
two branches since their common ancestor (merge base), using commit message
keyword classification to determine which musical dimensions each commit
touches.

Dimensions analysed
-------------------
- ``melodic``    — melody, lead, solo, vocal, tune, note, pitch, riff, arpeggio
- ``harmonic``   — chord, harmony, key, scale, progression, voicing
- ``rhythmic``   — beat, drum, rhythm, groove, percussion, swing, tempo, bpm
- ``structural`` — structure, form, section, bridge, chorus, verse, intro, outro
- ``dynamic``    — mix, master, volume, level, dynamics, eq, compressor, reverb

Score formula (per dimension)
------------------------------
Given the sets of commit messages classified to dimension D on branch A
(``a_dim``) and branch B (``b_dim``) since the merge base:

    score = |symmetric_difference(a_dim, b_dim)| / |union(a_dim, b_dim)|

Score 0.0 = both branches changed exactly the same things in this dimension.
Score 1.0 = no overlap — completely diverged.

Boundary rules
--------------
- Must NOT import StateStore, executor, MCP tools, or handlers.
- May import ``maestro.db.musehub_models``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.db.musehub_models import MusehubCommit

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

#: Keyword patterns used to classify commit messages into musical dimensions.
_DIMENSION_PATTERNS: dict[str, tuple[str, ...]] = {
    "melodic": ("melody", "lead", "solo", "vocal", "tune", "note", "pitch", "riff", "arpeggio"),
    "harmonic": ("chord", "harmony", "harmonic", "key", "scale", "progression", "voicing"),
    "rhythmic": ("beat", "drum", "rhythm", "groove", "perc", "swing", "tempo", "bpm", "quantize"),
    "structural": (
        "struct",
        "form",
        "section",
        "bridge",
        "chorus",
        "verse",
        "intro",
        "outro",
        "arrangement",
        "transition",
    ),
    "dynamic": ("mix", "master", "volume", "level", "dynamic", "eq", "compress", "reverb", "fx"),
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class MuseHubDivergenceLevel(str, Enum):
    """Qualitative label for a per-dimension or overall divergence score.

    Thresholds mirror the CLI divergence engine for consistency.

    - ``NONE`` — score < 0.15
    - ``LOW``  — 0.15 ≤ score < 0.40
    - ``MED``  — 0.40 ≤ score < 0.70
    - ``HIGH`` — score ≥ 0.70
    """

    NONE = "NONE"
    LOW = "LOW"
    MED = "MED"
    HIGH = "HIGH"


@dataclass(frozen=True)
class MuseHubDimensionDivergence:
    """Divergence score and description for a single musical dimension.

    Attributes:
        dimension:        Dimension name (e.g. ``"melodic"``).
        level:            Qualitative divergence level label.
        score:            Normalised divergence score in [0.0, 1.0].
        description:      Human-readable divergence summary.
        branch_a_commits: Number of commits in this dimension on branch A.
        branch_b_commits: Number of commits in this dimension on branch B.
    """

    dimension: str
    level: MuseHubDivergenceLevel
    score: float
    description: str
    branch_a_commits: int
    branch_b_commits: int


@dataclass(frozen=True)
class MuseHubDivergenceResult:
    """Full musical divergence report between two Muse Hub branches.

    Attributes:
        repo_id:         Repository ID.
        branch_a:        Name of the first branch.
        branch_b:        Name of the second branch.
        common_ancestor: Commit ID of the merge base, or ``None`` if disjoint.
        dimensions:      Per-dimension divergence results (always 5 entries).
        overall_score:   Mean of all per-dimension scores in [0.0, 1.0].
    """

    repo_id: str
    branch_a: str
    branch_b: str
    common_ancestor: str | None
    dimensions: tuple[MuseHubDimensionDivergence, ...]
    overall_score: float


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def classify_message(message: str) -> set[str]:
    """Return the set of musical dimensions this commit message touches.

    Matching is case-insensitive and keyword-based.  A single message may map
    to multiple dimensions (e.g. "add jazzy chord melody" → melodic + harmonic).

    Args:
        message: Commit message text.

    Returns:
        Set of dimension names that the message matches.  Empty if unclassified.
    """
    lower = message.lower()
    return {
        dim
        for dim, patterns in _DIMENSION_PATTERNS.items()
        if any(pat in lower for pat in patterns)
    }


def score_to_level(score: float) -> MuseHubDivergenceLevel:
    """Map a numeric divergence score to a qualitative level label.

    Args:
        score: Normalised score in [0.0, 1.0].

    Returns:
        The appropriate :class:`MuseHubDivergenceLevel` enum member.
    """
    if score < 0.15:
        return MuseHubDivergenceLevel.NONE
    if score < 0.40:
        return MuseHubDivergenceLevel.LOW
    if score < 0.70:
        return MuseHubDivergenceLevel.MED
    return MuseHubDivergenceLevel.HIGH


def compute_hub_dimension_divergence(
    dimension: str,
    a_commit_ids: set[str],
    b_commit_ids: set[str],
    a_messages: dict[str, str],
    b_messages: dict[str, str],
) -> MuseHubDimensionDivergence:
    """Compute divergence for a single musical dimension across two commit sets.

    Score = ``|symmetric_diff| / |union|`` over commit IDs classified into
    *dimension*:

    - 0.0 → both branches touched exactly the same commits in this dimension.
    - 1.0 → no overlap — completely diverged.

    Args:
        dimension:     Dimension name (one of :data:`ALL_DIMENSIONS`).
        a_commit_ids:  Commit IDs for branch A since the merge base.
        b_commit_ids:  Commit IDs for branch B since the merge base.
        a_messages:    Mapping of commit_id → message for branch A.
        b_messages:    Mapping of commit_id → message for branch B.

    Returns:
        A :class:`MuseHubDimensionDivergence` with score, level, and summary.
    """

    def _filter_by_dim(ids: set[str], messages: dict[str, str]) -> set[str]:
        return {cid for cid in ids if dimension in classify_message(messages.get(cid, ""))}

    a_dim = _filter_by_dim(a_commit_ids, a_messages)
    b_dim = _filter_by_dim(b_commit_ids, b_messages)

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
    return MuseHubDimensionDivergence(
        dimension=dimension,
        level=level,
        score=round(score, 4),
        description=desc,
        branch_a_commits=len(a_dim),
        branch_b_commits=len(b_dim),
    )


# ---------------------------------------------------------------------------
# Async DB helpers
# ---------------------------------------------------------------------------


async def get_branch_commits(
    session: AsyncSession,
    repo_id: str,
    branch: str,
) -> list[MusehubCommit]:
    """Return all commits on *branch* for *repo_id*, newest first.

    Args:
        session:  Open async DB session.
        repo_id:  Repository identifier.
        branch:   Branch name.

    Returns:
        List of :class:`MusehubCommit` objects ordered by timestamp descending.
    """
    result = await session.execute(
        select(MusehubCommit)
        .where(
            MusehubCommit.repo_id == repo_id,
            MusehubCommit.branch == branch,
        )
        .order_by(MusehubCommit.timestamp.desc())
    )
    return list(result.scalars().all())


def find_common_ancestor(
    a_commits: list[MusehubCommit],
    b_commits: list[MusehubCommit],
) -> str | None:
    """Find the most recent common ancestor commit ID between two branch histories.

    Uses a simple BFS walk through parent_ids starting from each branch's HEAD,
    returning the first commit ID seen on both branches.

    Args:
        a_commits: All commits on branch A (newest first).
        b_commits: All commits on branch B (newest first).

    Returns:
        The commit ID of the most recent common ancestor, or ``None`` if the
        two histories are completely disjoint.
    """
    a_ids = {c.commit_id for c in a_commits}
    b_ids = {c.commit_id for c in b_commits}
    common = a_ids & b_ids
    if not common:
        return None
    all_a = {c.commit_id: c for c in a_commits}
    all_b = {c.commit_id: c for c in b_commits}
    all_commits = {**all_a, **all_b}
    a_ancestry: set[str] = set()
    frontier = list(a_ids)
    while frontier:
        cid = frontier.pop()
        if cid in a_ancestry:
            continue
        a_ancestry.add(cid)
        commit = all_commits.get(cid)
        if commit:
            frontier.extend(commit.parent_ids)
    for commit in sorted(b_commits, key=lambda c: c.timestamp, reverse=True):
        if commit.commit_id in a_ancestry:
            return commit.commit_id
    return None


def get_commits_since(
    all_commits: list[MusehubCommit],
    base_commit_id: str | None,
) -> list[MusehubCommit]:
    """Filter commits to only those introduced after *base_commit_id*.

    When *base_commit_id* is ``None``, all commits are returned (disjoint
    histories — no common ancestor).

    Args:
        all_commits:    All commits for a branch (newest first).
        base_commit_id: Common ancestor commit ID to exclude, or ``None``.

    Returns:
        Commits introduced after the base, in newest-first order.
    """
    if base_commit_id is None:
        return all_commits
    return [c for c in all_commits if c.commit_id != base_commit_id]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def compute_hub_divergence(
    session: AsyncSession,
    *,
    repo_id: str,
    branch_a: str,
    branch_b: str,
) -> MuseHubDivergenceResult:
    """Compute musical divergence between two Muse Hub branches.

    Finds the common ancestor, collects each branch's commits since that point,
    classifies commit messages into musical dimensions, and computes a
    per-dimension Jaccard divergence score.

    Args:
        session:  Open async DB session.
        repo_id:  Repository ID.
        branch_a: Name of the first branch.
        branch_b: Name of the second branch.

    Returns:
        A :class:`MuseHubDivergenceResult` with per-dimension scores and the
        resolved common ancestor commit ID.

    Raises:
        ValueError: If *branch_a* or *branch_b* has no commits in *repo_id*.
    """
    a_all = await get_branch_commits(session, repo_id, branch_a)
    if not a_all:
        raise ValueError(f"Branch '{branch_a}' has no commits in repo '{repo_id}'.")
    b_all = await get_branch_commits(session, repo_id, branch_b)
    if not b_all:
        raise ValueError(f"Branch '{branch_b}' has no commits in repo '{repo_id}'.")

    common_ancestor = find_common_ancestor(a_all, b_all)

    logger.info(
        "✅ musehub divergence: %r vs %r, base=%s",
        branch_a,
        branch_b,
        common_ancestor[:8] if common_ancestor else "none",
    )

    a_since = get_commits_since(a_all, common_ancestor)
    b_since = get_commits_since(b_all, common_ancestor)

    a_ids = {c.commit_id for c in a_since}
    b_ids = {c.commit_id for c in b_since}
    a_msgs = {c.commit_id: c.message for c in a_since}
    b_msgs = {c.commit_id: c.message for c in b_since}

    dimensions = tuple(
        compute_hub_dimension_divergence(dim, a_ids, b_ids, a_msgs, b_msgs)
        for dim in ALL_DIMENSIONS
    )

    overall = (
        round(sum(d.score for d in dimensions) / len(dimensions), 4)
        if dimensions
        else 0.0
    )

    return MuseHubDivergenceResult(
        repo_id=repo_id,
        branch_a=branch_a,
        branch_b=branch_b,
        common_ancestor=common_ancestor,
        dimensions=dimensions,
        overall_score=overall,
    )
