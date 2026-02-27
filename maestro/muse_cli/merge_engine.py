"""Muse VCS merge-state reader.

Provides :func:`read_merge_state` — a pure filesystem read that detects an
in-progress merge and returns conflict information.  The presence of
``.muse/MERGE_STATE.json`` signals that a three-way merge was started but
has unresolved conflicts that must be fixed before committing.

``MERGE_STATE.json`` schema (all fields optional except ``conflicts``):

.. code-block:: json

    {
        "conflicts":    ["path/to/file1.mid", "path/to/file2.mid"],
        "other_branch": "feature/variation-b",
        "merge_base":   "abc123def456..."
    }
"""
from __future__ import annotations

import json
import logging
import pathlib
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_MERGE_STATE_FILENAME = "MERGE_STATE.json"


@dataclass(frozen=True)
class MergeState:
    """Describes an in-progress merge with unresolved conflicts.

    Attributes:
        conflicts:    Relative paths (POSIX) of files with merge conflicts.
        other_branch: Name of the branch being merged in, if recorded.
        merge_base:   Commit ID of the common ancestor, if recorded.
    """

    conflicts: list[str] = field(default_factory=list)
    other_branch: str | None = None
    merge_base: str | None = None


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

    raw_conflicts = data.get("conflicts", [])
    conflicts: list[str] = (
        [str(c) for c in raw_conflicts] if isinstance(raw_conflicts, list) else []
    )

    other_branch: str | None = (
        str(data["other_branch"]) if "other_branch" in data else None
    )
    merge_base: str | None = (
        str(data["merge_base"]) if "merge_base" in data else None
    )

    return MergeState(
        conflicts=conflicts,
        other_branch=other_branch,
        merge_base=merge_base,
    )
