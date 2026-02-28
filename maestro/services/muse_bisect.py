"""Muse bisect service — binary search over the commit graph for regression hunting.

This service implements the state machine and commit graph traversal logic for
``muse bisect``.  It is the music-domain analogue of ``git bisect``: given a
known-good commit and a known-bad commit, it finds the first commit that
introduced a regression by binary searching the ancestry path.

Typical music QA workflow
-------------------------
1. ``muse bisect start``
2. ``muse bisect good <last_known_good_sha>``
3. ``muse bisect bad <current_broken_sha>``
4. Muse checks out the midpoint commit into muse-work/ for inspection.
5. Producer listens, runs tests, then ``muse bisect good`` or ``muse bisect bad``.
6. Repeat until the culprit commit is identified.
7. ``muse bisect reset`` restores the pre-bisect state.

``muse bisect run <cmd>`` automates steps 4-6: it runs the command after each
checkout and uses the exit code (0 = good, 1 = bad) to advance automatically.

State file schema (BISECT_STATE.json)
--------------------------------------
.. code-block:: json

    {
        "good": "abc123...",
        "bad": "def456...",
        "current": "789abc...",
        "tested": {"commit_id": "good"},
        "pre_bisect_ref": "refs/heads/main",
        "pre_bisect_commit": "abc000..."
    }

``tested`` is a map from commit_id to verdict (``"good"`` or ``"bad"``).
``pre_bisect_ref`` and ``pre_bisect_commit`` record where HEAD was before the
session started so ``muse bisect reset`` can cleanly restore the workspace.
"""
from __future__ import annotations

import json
import logging
import pathlib
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from maestro.muse_cli.models import MuseCliCommit

logger = logging.getLogger(__name__)

_BISECT_STATE_FILENAME = "BISECT_STATE.json"


# ---------------------------------------------------------------------------
# BisectState dataclass
# ---------------------------------------------------------------------------


@dataclass
class BisectState:
    """Mutable snapshot of an in-progress bisect session.

    Attributes:
        good:             Commit ID of the last known-good revision.
        bad:              Commit ID of the first known-bad revision.
        current:          Commit ID currently checked out for testing.
        tested:           Map from commit_id to verdict (``"good"`` or ``"bad"``).
        pre_bisect_ref:   Symbolic ref HEAD pointed at before bisect started
                          (e.g. ``refs/heads/main``).
        pre_bisect_commit: Commit ID HEAD resolved to before bisect started.
    """

    good: str | None = None
    bad: str | None = None
    current: str | None = None
    tested: dict[str, str] = field(default_factory=dict)
    pre_bisect_ref: str = ""
    pre_bisect_commit: str = ""


# ---------------------------------------------------------------------------
# BisectStepResult — result type for a single bisect step
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BisectStepResult:
    """Outcome of advancing the bisect session by one step.

    Returned by :func:`advance_bisect` after marking a commit as good or bad.

    Attributes:
        culprit:    Commit ID of the first bad commit if identified, else ``None``.
        next_commit: Commit ID to check out and test next, or ``None`` when done.
        remaining:  Estimated number of commits still to test (0 when done).
        message:    Human-readable summary of this step for CLI display.
    """

    culprit: str | None
    next_commit: str | None
    remaining: int
    message: str


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def read_bisect_state(root: pathlib.Path) -> BisectState | None:
    """Return :class:`BisectState` if a bisect is in progress, else ``None``.

    Reads ``.muse/BISECT_STATE.json``.  Returns ``None`` when no file exists
    (no active session) or when the file cannot be parsed (treated as absent).

    Args:
        root: Repository root (directory containing ``.muse/``).
    """
    state_path = root / ".muse" / _BISECT_STATE_FILENAME
    if not state_path.exists():
        return None

    try:
        raw: dict[str, object] = json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("⚠️  Failed to read %s: %s", _BISECT_STATE_FILENAME, exc)
        return None

    def _str_or_none(key: str) -> str | None:
        val = raw.get(key)
        return str(val) if val is not None else None

    raw_tested = raw.get("tested", {})
    tested: dict[str, str] = (
        {str(k): str(v) for k, v in raw_tested.items()}
        if isinstance(raw_tested, dict)
        else {}
    )

    return BisectState(
        good=_str_or_none("good"),
        bad=_str_or_none("bad"),
        current=_str_or_none("current"),
        tested=tested,
        pre_bisect_ref=str(raw.get("pre_bisect_ref", "")),
        pre_bisect_commit=str(raw.get("pre_bisect_commit", "")),
    )


def write_bisect_state(root: pathlib.Path, state: BisectState) -> None:
    """Persist *state* to ``.muse/BISECT_STATE.json``.

    Args:
        root:  Repository root (directory containing ``.muse/``).
        state: Current bisect session state to persist.
    """
    state_path = root / ".muse" / _BISECT_STATE_FILENAME
    data: dict[str, object] = {
        "good": state.good,
        "bad": state.bad,
        "current": state.current,
        "tested": state.tested,
        "pre_bisect_ref": state.pre_bisect_ref,
        "pre_bisect_commit": state.pre_bisect_commit,
    }
    state_path.write_text(json.dumps(data, indent=2))
    logger.debug("✅ Wrote BISECT_STATE.json (good=%s bad=%s)", state.good, state.bad)


def clear_bisect_state(root: pathlib.Path) -> None:
    """Remove ``.muse/BISECT_STATE.json`` after reset or culprit identified."""
    state_path = root / ".muse" / _BISECT_STATE_FILENAME
    if state_path.exists():
        state_path.unlink()
        logger.debug("✅ Cleared BISECT_STATE.json")


# ---------------------------------------------------------------------------
# Commit graph helpers (require a DB session)
# ---------------------------------------------------------------------------


async def get_commits_between(
    session: AsyncSession,
    good_commit_id: str,
    bad_commit_id: str,
) -> list[MuseCliCommit]:
    """Return commits reachable from *bad* but not reachable from *good*.

    Performs two BFS traversals of the Muse commit DAG:
    1. All ancestors of *good_commit_id* (inclusive) → ``good_ancestors``.
    2. All ancestors of *bad_commit_id* (inclusive) → filtered by exclusion.

    Returns only commits that are ancestors of *bad* but not of *good*, sorted
    by ``committed_at`` ascending (oldest first).  These are the commits the
    bisect session needs to search through.

    An empty list means *good* and *bad* are the same commit or there is no
    commit between them — the culprit is *bad* itself.

    Args:
        session:       Open async DB session.
        good_commit_id: Commit ID of the known-good revision.
        bad_commit_id:  Commit ID of the known-bad revision.

    Returns:
        Ordered list of :class:`MuseCliCommit` rows to bisect, oldest first.
    """
    from maestro.muse_cli.models import MuseCliCommit

    async def _ancestors(start: str) -> set[str]:
        """BFS collecting all ancestor commit IDs (inclusive of start)."""
        visited: set[str] = set()
        queue: deque[str] = deque([start])
        while queue:
            cid = queue.popleft()
            if cid in visited:
                continue
            visited.add(cid)
            row: MuseCliCommit | None = await session.get(MuseCliCommit, cid)
            if row is None:
                continue
            if row.parent_commit_id:
                queue.append(row.parent_commit_id)
            if row.parent2_commit_id:
                queue.append(row.parent2_commit_id)
        return visited

    good_set = await _ancestors(good_commit_id)
    bad_ancestors = await _ancestors(bad_commit_id)

    # Commits between good and bad: reachable from bad, not from good,
    # and excluding bad itself (bad is known-bad, not a candidate to test).
    candidate_ids = bad_ancestors - good_set - {bad_commit_id}

    if not candidate_ids:
        return []

    # Load and sort by committed_at ascending.
    rows: list[MuseCliCommit] = []
    for cid in candidate_ids:
        row = await session.get(MuseCliCommit, cid)
        if row is not None:
            rows.append(row)

    rows.sort(key=lambda r: r.committed_at)
    return rows


def pick_midpoint(commits: list[MuseCliCommit]) -> MuseCliCommit | None:
    """Return the midpoint commit for binary search.

    Selects ``commits[(len(commits) - 1) // 2]`` — the lower-middle element
    for even-length lists, middle for odd-length.  Returns ``None`` on empty.

    Args:
        commits: Ordered list of candidate commits (oldest first).
    """
    if not commits:
        return None
    mid_idx = (len(commits) - 1) // 2
    return commits[mid_idx]


async def advance_bisect(
    session: AsyncSession,
    root: pathlib.Path,
    commit_id: str,
    verdict: str,
) -> BisectStepResult:
    """Record a verdict for *commit_id* and advance the bisect session.

    Updates the ``good`` or ``bad`` bound based on the verdict, computes the
    remaining candidate range, and selects the next midpoint to test.

    When the candidate range collapses to zero the culprit is identified: it is
    the ``bad`` bound (the earliest commit we marked bad, or the first bad commit
    reachable from bad that is not reachable from good).

    Args:
        session:   Open async DB session.
        root:      Repository root.
        commit_id: Commit being marked.
        verdict:   Either ``"good"`` or ``"bad"``.

    Returns:
        :class:`BisectStepResult` describing the outcome.

    Raises:
        ValueError: If *verdict* is not ``"good"`` or ``"bad"``.
        RuntimeError: If no bisect is in progress.
    """
    if verdict not in ("good", "bad"):
        raise ValueError(f"verdict must be 'good' or 'bad', got {verdict!r}")

    state = read_bisect_state(root)
    if state is None:
        raise RuntimeError("No bisect session in progress. Run 'muse bisect start' first.")

    # Record the verdict.
    state.tested[commit_id] = verdict

    if verdict == "good":
        state.good = commit_id
    else:
        state.bad = commit_id

    # If either bound is still unset we cannot advance yet.
    if state.good is None or state.bad is None:
        write_bisect_state(root, state)
        missing = "bad" if state.bad is None else "good"
        return BisectStepResult(
            culprit=None,
            next_commit=None,
            remaining=0,
            message=(
                f"✅ Marked {commit_id[:8]} as {verdict}. "
                f"Now mark a {missing} commit to begin bisecting."
            ),
        )

    # Recompute remaining candidates.
    candidates = await get_commits_between(session, state.good, state.bad)

    if not candidates:
        # No commits left to test — bad is the culprit.
        culprit = state.bad
        state.current = None
        write_bisect_state(root, state)
        return BisectStepResult(
            culprit=culprit,
            next_commit=None,
            remaining=0,
            message=(
                f"🎯 Bisect complete! First bad commit: {culprit[:8]}\n"
                "Run 'muse bisect reset' to restore your workspace."
            ),
        )

    next_commit = pick_midpoint(candidates)
    assert next_commit is not None  # candidates is non-empty
    state.current = next_commit.commit_id
    write_bisect_state(root, state)

    remaining = len(candidates)
    import math
    steps = math.ceil(math.log2(remaining + 1)) if remaining > 0 else 0

    return BisectStepResult(
        culprit=None,
        next_commit=next_commit.commit_id,
        remaining=remaining,
        message=(
            f"✅ Marked {commit_id[:8]} as {verdict}. "
            f"Checking out {next_commit.commit_id[:8]} "
            f"(~{steps} step(s) remaining, {remaining} commit(s) in range)"
        ),
    )
