"""Snapshot helpers — capture immutable copies of StateStore region data.

These functions form the boundary between Maestro (which owns StateStore)
and Muse (which must only see frozen snapshots, never live references).

Both ``capture_base_snapshot`` and ``capture_proposed_snapshot`` return a
``SnapshotBundle`` — the single unified type for all snapshot data.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from app.core.executor.models import SnapshotBundle

if TYPE_CHECKING:
    from app.core.state_store import StateStore


def capture_base_snapshot(store: "StateStore") -> SnapshotBundle:
    """Capture an immutable snapshot of all region data before execution.

    Returns a deep copy — callers may hold this across mutations
    without observing side effects.
    """
    return SnapshotBundle(
        notes=deepcopy(store._region_notes),
        cc=deepcopy(store._region_cc),
        pitch_bends=deepcopy(store._region_pitch_bends),
        aftertouch=deepcopy(store._region_aftertouch),
    )


def capture_proposed_snapshot(store: "StateStore") -> SnapshotBundle:
    """Capture an immutable snapshot of all region data after execution.

    Identical implementation to ``capture_base_snapshot``; the name
    distinguishes intent (pre-execution vs post-execution).
    """
    return SnapshotBundle(
        notes=deepcopy(store._region_notes),
        cc=deepcopy(store._region_cc),
        pitch_bends=deepcopy(store._region_pitch_bends),
        aftertouch=deepcopy(store._region_aftertouch),
    )
