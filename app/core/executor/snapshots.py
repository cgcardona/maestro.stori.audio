"""Snapshot helpers — capture immutable copies of StateStore region data.

These functions form the boundary between Maestro (which owns StateStore)
and Muse (which must only see frozen snapshots, never live references).

Canonical snapshot shape
~~~~~~~~~~~~~~~~~~~~~~~~
Both ``capture_base_snapshot`` and ``capture_proposed_snapshot`` return a
``StoreSnapshot`` with this structure::

    {
        "region_notes":       {region_id: [note_dict, ...]},
        "region_cc":          {region_id: [cc_dict, ...]},
        "region_pitch_bends": {region_id: [pb_dict, ...]},
        "region_aftertouch":  {region_id: [at_dict, ...]},
    }

The agent-team path in ``maestro_composing/composing.py`` uses these
snapshots directly.  The single-instrument path accumulates the same
data incrementally inside ``VariationContext`` (see ``executor/models.py``),
which mirrors the shape: ``base_notes``/``proposed_notes`` correspond to
``region_notes``, and ``proposed_cc``/``proposed_pitch_bends``/
``proposed_aftertouch`` correspond to the remaining keys.

Both paths produce the same logical structure; only the collection
mechanism differs.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.state_store import StateStore

RegionSnapshot = dict[str, Any]
StoreSnapshot = dict[str, dict[str, list[RegionSnapshot]]]


def capture_base_snapshot(store: "StateStore") -> StoreSnapshot:
    """Capture an immutable snapshot of all region data before execution.

    Returns a frozen copy — callers may hold this across mutations
    without observing side effects.
    """
    return {
        "region_notes": deepcopy(store._region_notes),
        "region_cc": deepcopy(store._region_cc),
        "region_pitch_bends": deepcopy(store._region_pitch_bends),
        "region_aftertouch": deepcopy(store._region_aftertouch),
    }


def capture_proposed_snapshot(store: "StateStore") -> StoreSnapshot:
    """Capture an immutable snapshot of all region data after execution.

    Identical implementation to ``capture_base_snapshot``; the name
    distinguishes intent (pre-execution vs post-execution).
    """
    return {
        "region_notes": deepcopy(store._region_notes),
        "region_cc": deepcopy(store._region_cc),
        "region_pitch_bends": deepcopy(store._region_pitch_bends),
        "region_aftertouch": deepcopy(store._region_aftertouch),
    }
