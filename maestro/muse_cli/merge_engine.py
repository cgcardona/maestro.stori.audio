"""Muse VCS merge engine — fast-forward and 3-way path-level merge.

Public API
----------
Pure functions (no I/O):

- :func:`diff_snapshots` — paths that changed between two snapshot manifests.
- :func:`detect_conflicts` — paths changed on *both* branches since the base.
- :func:`apply_merge` — build merged manifest for a conflict-free 3-way merge.

Async helpers (require a DB session):

- :func:`find_merge_base` — lowest common ancestor (LCA) of two commits.

Filesystem helpers:

- :func:`read_merge_state` — detect and load an in-progress merge.
- :func:`write_merge_state` — persist conflict state before exiting.

``MERGE_STATE.json`` schema
---------------------------

.. code-block:: json

    {
        "base_commit":    "abc123...",
        "ours_commit":    "def456...",
        "theirs_commit":  "789abc...",
        "conflict_paths": ["beat.mid", "lead.mp3"],
        "other_branch":   "feature/experiment"
    }

``other_branch`` is optional; all other fields are required when conflicts exist.
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

logger = logging.getLogger(__name__)

_MERGE_STATE_FILENAME = "MERGE_STATE.json"


# ---------------------------------------------------------------------------
# MergeState dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MergeState:
    """Describes an in-progress merge with unresolved conflicts.

    Attributes:
        conflict_paths: Relative paths (POSIX) of files with merge conflicts.
        base_commit:    Commit ID of the common ancestor (merge base).
        ours_commit:    Commit ID of HEAD when the merge was initiated.
        theirs_commit:  Commit ID of the branch being merged in.
        other_branch:   Name of the branch being merged in, if recorded.
    """

    conflict_paths: list[str] = field(default_factory=list)
    base_commit: str | None = None
    ours_commit: str | None = None
    theirs_commit: str | None = None
    other_branch: str | None = None


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def read_merge_state(root: pathlib.Path) -> MergeState | None:
    """Return :class:`MergeState` if a merge is in progress, otherwise ``None``.

    Reads ``.muse/MERGE_STATE.json`` from *root*.  Returns ``None`` when the
    file does not exist (no in-progress merge) or when it cannot be parsed.

    Args:
        root: The repository root directory (the directory containing ``.muse/``).

    Returns:
        A :class:`MergeState` instance describing the in-progress merge, or
        ``None`` if no merge is in progress.
    """
    merge_state_path = root / ".muse" / _MERGE_STATE_FILENAME
    if not merge_state_path.exists():
        return None

    try:
        data: dict[str, object] = json.loads(merge_state_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("⚠️ Failed to read %s: %s", _MERGE_STATE_FILENAME, exc)
        return None

    raw_conflicts = data.get("conflict_paths", [])
    conflict_paths: list[str] = (
        [str(c) for c in raw_conflicts] if isinstance(raw_conflicts, list) else []
    )

    def _str_or_none(key: str) -> str | None:
        return str(data[key]) if key in data else None

    return MergeState(
        conflict_paths=conflict_paths,
        base_commit=_str_or_none("base_commit"),
        ours_commit=_str_or_none("ours_commit"),
        theirs_commit=_str_or_none("theirs_commit"),
        other_branch=_str_or_none("other_branch"),
    )


def write_merge_state(
    root: pathlib.Path,
    *,
    base_commit: str,
    ours_commit: str,
    theirs_commit: str,
    conflict_paths: list[str],
    other_branch: str | None = None,
) -> None:
    """Write ``.muse/MERGE_STATE.json`` to signal an in-progress conflicted merge.

    Args:
        root:           Repository root (directory containing ``.muse/``).
        base_commit:    Commit ID of the merge base (LCA).
        ours_commit:    Commit ID of HEAD at merge time.
        theirs_commit:  Commit ID of the branch being merged in.
        conflict_paths: List of POSIX paths with unresolved conflicts.
        other_branch:   Human-readable name of the branch being merged in.
    """
    merge_state_path = root / ".muse" / _MERGE_STATE_FILENAME
    data: dict[str, object] = {
        "base_commit": base_commit,
        "ours_commit": ours_commit,
        "theirs_commit": theirs_commit,
        "conflict_paths": sorted(conflict_paths),
    }
    if other_branch is not None:
        data["other_branch"] = other_branch
    merge_state_path.write_text(json.dumps(data, indent=2))
    logger.info("✅ Wrote MERGE_STATE.json with %d conflict(s)", len(conflict_paths))


def clear_merge_state(root: pathlib.Path) -> None:
    """Remove ``.muse/MERGE_STATE.json`` after a successful merge or resolution."""
    merge_state_path = root / ".muse" / _MERGE_STATE_FILENAME
    if merge_state_path.exists():
        merge_state_path.unlink()
        logger.debug("✅ Cleared MERGE_STATE.json")


def apply_resolution(
    root: pathlib.Path,
    rel_path: str,
    object_id: str,
) -> None:
    """Copy the object identified by *object_id* from the local store to ``muse-work/<rel_path>``.

    Used by ``muse resolve --theirs`` and ``muse merge --abort`` to restore
    a specific version of a file to the working directory without requiring
    the caller to know the internal object store layout.

    Args:
        root:      Repository root (directory containing ``.muse/``).
        rel_path:  POSIX path relative to ``muse-work/``.
        object_id: sha256 hex digest of the desired object content.

    Raises:
        FileNotFoundError: If the object is not present in the local store.
            This means the commit's objects were never fetched locally — the
            caller should report a user-friendly error.
    """
    from maestro.muse_cli.object_store import read_object

    content = read_object(root, object_id)
    if content is None:
        raise FileNotFoundError(
            f"Object {object_id[:8]} for '{rel_path}' not found in local store."
        )
    dest = root / "muse-work" / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    logger.debug("✅ Restored '%s' from object %s", rel_path, object_id[:8])


def is_conflict_resolved(merge_state: MergeState, rel_path: str) -> bool:
    """Return ``True`` if *rel_path* is NOT listed as a conflict in *merge_state*.

    A path is resolved when it no longer appears in ``conflict_paths``.
    Call this before marking a path resolved to detect double-resolve attempts.

    Args:
        merge_state: The current in-progress merge state.
        rel_path:    POSIX path to check (relative to ``muse-work/``).

    Returns:
        ``True`` if the path is already resolved, ``False`` if it still conflicts.
    """
    return rel_path not in merge_state.conflict_paths


# ---------------------------------------------------------------------------
# Pure merge functions (no I/O, no DB)
# ---------------------------------------------------------------------------


def diff_snapshots(
    base_manifest: dict[str, str],
    other_manifest: dict[str, str],
) -> set[str]:
    """Return the set of paths that differ between *base_manifest* and *other_manifest*.

    A path is included when it was:

    - **added** — present in *other* but absent from *base*.
    - **deleted** — present in *base* but absent from *other*.
    - **modified** — present in both but with a different ``object_id``.

    Args:
        base_manifest:  ``{path: object_id}`` for the common ancestor snapshot.
        other_manifest: ``{path: object_id}`` for the branch snapshot.

    Returns:
        Set of POSIX paths that changed.
    """
    base_paths = set(base_manifest.keys())
    other_paths = set(other_manifest.keys())

    added = other_paths - base_paths
    deleted = base_paths - other_paths
    common = base_paths & other_paths
    modified = {p for p in common if base_manifest[p] != other_manifest[p]}

    return added | deleted | modified


def detect_conflicts(
    ours_changed: set[str],
    theirs_changed: set[str],
) -> set[str]:
    """Return paths changed on *both* branches since the merge base.

    A conflict occurs when both ``ours`` and ``theirs`` modified the same path
    independently.  The caller decides how to handle these (write
    ``MERGE_STATE.json`` and exit, or apply one side's version).

    Args:
        ours_changed:   Paths changed on the current branch since the base.
        theirs_changed: Paths changed on the target branch since the base.

    Returns:
        Set of conflicting POSIX paths.
    """
    return ours_changed & theirs_changed


def apply_merge(
    base_manifest: dict[str, str],
    ours_manifest: dict[str, str],
    theirs_manifest: dict[str, str],
    ours_changed: set[str],
    theirs_changed: set[str],
    conflict_paths: set[str],
) -> dict[str, str]:
    """Build the merged snapshot manifest for a *conflict-free* 3-way merge.

    Only non-conflicting changes are applied:

    - Paths changed only on ours → take ours version (or deletion).
    - Paths changed only on theirs → take theirs version (or deletion).
    - Conflict paths → excluded (caller already wrote ``MERGE_STATE.json``).

    Args:
        base_manifest:  ``{path: object_id}`` for the common ancestor.
        ours_manifest:  ``{path: object_id}`` for the current branch HEAD.
        theirs_manifest: ``{path: object_id}`` for the target branch HEAD.
        ours_changed:   Paths changed on the current branch since base.
        theirs_changed: Paths changed on the target branch since base.
        conflict_paths: Paths with conflicts (must be empty for a clean merge).

    Returns:
        Merged ``{path: object_id}`` manifest.
    """
    merged: dict[str, str] = dict(base_manifest)

    # Apply non-conflicting ours changes.
    for path in ours_changed - conflict_paths:
        if path in ours_manifest:
            merged[path] = ours_manifest[path]
        else:
            merged.pop(path, None)

    # Apply non-conflicting theirs changes.
    for path in theirs_changed - conflict_paths:
        if path in theirs_manifest:
            merged[path] = theirs_manifest[path]
        else:
            merged.pop(path, None)

    return merged


# ---------------------------------------------------------------------------
# Async merge helpers (require a DB session)
# ---------------------------------------------------------------------------


async def find_merge_base(
    session: AsyncSession,
    commit_id_a: str,
    commit_id_b: str,
) -> str | None:
    """Find the Lowest Common Ancestor (LCA) of two commits.

    Uses BFS to collect all ancestors of *commit_id_a* (inclusive), then
    walks *commit_id_b*'s ancestor graph (BFS) until the first node found
    in *a*'s ancestor set is reached.

    Supports merge commits with two parents (``parent_commit_id`` and
    ``parent2_commit_id``).

    Args:
        session:      An open async DB session.
        commit_id_a:  First commit ID (e.g., current branch HEAD).
        commit_id_b:  Second commit ID (e.g., target branch HEAD).

    Returns:
        The LCA commit ID, or ``None`` if the commits share no common ancestor
        (disjoint histories).
    """
    from maestro.muse_cli.models import MuseCliCommit

    async def _all_ancestors(start: str) -> set[str]:
        """BFS from *start*, returning all reachable commit IDs (inclusive)."""
        visited: set[str] = set()
        queue: deque[str] = deque([start])
        while queue:
            cid = queue.popleft()
            if cid in visited:
                continue
            visited.add(cid)
            commit: MuseCliCommit | None = await session.get(MuseCliCommit, cid)
            if commit is None:
                continue
            if commit.parent_commit_id:
                queue.append(commit.parent_commit_id)
            if commit.parent2_commit_id:
                queue.append(commit.parent2_commit_id)
        return visited

    a_ancestors = await _all_ancestors(commit_id_a)

    # BFS from B — return the first node that is in A's ancestor set.
    visited_b: set[str] = set()
    queue_b: deque[str] = deque([commit_id_b])
    while queue_b:
        cid = queue_b.popleft()
        if cid in visited_b:
            continue
        visited_b.add(cid)
        if cid in a_ancestors:
            return cid
        commit = await session.get(MuseCliCommit, cid)
        if commit is None:
            continue
        if commit.parent_commit_id:
            queue_b.append(commit.parent_commit_id)
        if commit.parent2_commit_id:
            queue_b.append(commit.parent2_commit_id)

    return None
