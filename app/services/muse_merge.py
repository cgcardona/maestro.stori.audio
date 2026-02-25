"""Muse Merge Engine — three-way merge for musical variations.

Produces a ``MergeResult`` by comparing base, left, and right snapshots.
Auto-merges non-conflicting changes; reports conflicts when both sides
modify the same note or controller event.

Boundary rules:
  - Must NOT import StateStore, executor, MCP tools, or handlers.
  - May import muse_repository, muse_replay, muse_checkout, note_matching.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.muse_checkout import CheckoutPlan, build_checkout_plan
from app.services.muse_merge_base import find_merge_base
from app.services.muse_replay import HeadSnapshot, reconstruct_variation_snapshot
from app.services.variation.note_matching import (
    NoteMatch,
    EventMatch,
    match_notes,
    match_cc_events,
    match_pitch_bends,
    match_aftertouch,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MergeConflict:
    """A single unresolvable conflict between left and right."""

    region_id: str
    type: Literal["note", "cc", "pb", "at"]
    description: str


@dataclass(frozen=True)
class MergeResult:
    """Outcome of a three-way merge."""

    has_conflicts: bool
    conflicts: tuple[MergeConflict, ...]
    merged_snapshot: HeadSnapshot | None


@dataclass(frozen=True)
class ThreeWaySnapshot:
    """Snapshots at base, left, and right for a three-way merge."""

    base: HeadSnapshot
    left: HeadSnapshot
    right: HeadSnapshot


@dataclass(frozen=True)
class MergeCheckoutPlan:
    """Result of merge plan building — either a checkout plan or conflicts."""

    is_conflict: bool
    conflicts: tuple[MergeConflict, ...]
    checkout_plan: CheckoutPlan | None


# ---------------------------------------------------------------------------
# Three-way snapshot construction
# ---------------------------------------------------------------------------


async def build_three_way_snapshots(
    session: AsyncSession,
    base_id: str,
    left_id: str,
    right_id: str,
) -> ThreeWaySnapshot | None:
    """Reconstruct snapshots for all three points in a merge.

    Returns None if any of the three variations cannot be reconstructed.
    """
    base = await reconstruct_variation_snapshot(session, base_id)
    left = await reconstruct_variation_snapshot(session, left_id)
    right = await reconstruct_variation_snapshot(session, right_id)

    if base is None or left is None or right is None:
        return None

    return ThreeWaySnapshot(base=base, left=left, right=right)


# ---------------------------------------------------------------------------
# Three-way merge engine
# ---------------------------------------------------------------------------


def build_merge_result(
    *,
    base: HeadSnapshot,
    left: HeadSnapshot,
    right: HeadSnapshot,
) -> MergeResult:
    """Perform a three-way merge of musical state.

    For each region, compares left and right against the common base:
    - Only one side changed → take that side.
    - Neither changed → keep base.
    - Both changed → per-note/event conflict detection.

    Returns a MergeResult with the merged snapshot (if conflict-free)
    or the list of conflicts.
    """
    all_regions = sorted(
        set(base.notes.keys())
        | set(left.notes.keys())
        | set(right.notes.keys())
        | set(base.cc.keys())
        | set(left.cc.keys())
        | set(right.cc.keys())
        | set(base.pitch_bends.keys())
        | set(left.pitch_bends.keys())
        | set(right.pitch_bends.keys())
        | set(base.aftertouch.keys())
        | set(left.aftertouch.keys())
        | set(right.aftertouch.keys())
    )

    conflicts: list[MergeConflict] = []
    merged_notes: dict[str, list[dict[str, Any]]] = {}
    merged_cc: dict[str, list[dict[str, Any]]] = {}
    merged_pb: dict[str, list[dict[str, Any]]] = {}
    merged_at: dict[str, list[dict[str, Any]]] = {}
    merged_track_regions: dict[str, str] = {}
    merged_region_starts: dict[str, float] = {}

    for tr in (base.track_regions, left.track_regions, right.track_regions):
        merged_track_regions.update(tr)
    for rs in (base.region_start_beats, left.region_start_beats, right.region_start_beats):
        merged_region_starts.update(rs)

    for rid in all_regions:
        b_notes = base.notes.get(rid, [])
        l_notes = left.notes.get(rid, [])
        r_notes = right.notes.get(rid, [])

        notes_result, note_conflicts = _merge_note_layer(
            b_notes, l_notes, r_notes, rid,
        )
        merged_notes[rid] = notes_result
        conflicts.extend(note_conflicts)

        b_cc = base.cc.get(rid, [])
        l_cc = left.cc.get(rid, [])
        r_cc = right.cc.get(rid, [])
        cc_result, cc_conflicts = _merge_event_layer(
            b_cc, l_cc, r_cc, rid, "cc", match_cc_events,
        )
        merged_cc[rid] = cc_result
        conflicts.extend(cc_conflicts)

        b_pb = base.pitch_bends.get(rid, [])
        l_pb = left.pitch_bends.get(rid, [])
        r_pb = right.pitch_bends.get(rid, [])
        pb_result, pb_conflicts = _merge_event_layer(
            b_pb, l_pb, r_pb, rid, "pb", match_pitch_bends,
        )
        merged_pb[rid] = pb_result
        conflicts.extend(pb_conflicts)

        b_at = base.aftertouch.get(rid, [])
        l_at = left.aftertouch.get(rid, [])
        r_at = right.aftertouch.get(rid, [])
        at_result, at_conflicts = _merge_event_layer(
            b_at, l_at, r_at, rid, "at", match_aftertouch,
        )
        merged_at[rid] = at_result
        conflicts.extend(at_conflicts)

    conflict_tuple = tuple(conflicts)
    has_conflicts = len(conflict_tuple) > 0

    if has_conflicts:
        return MergeResult(
            has_conflicts=True,
            conflicts=conflict_tuple,
            merged_snapshot=None,
        )

    merged = HeadSnapshot(
        variation_id=f"merge:{left.variation_id[:8]}+{right.variation_id[:8]}",
        notes=merged_notes,
        cc=merged_cc,
        pitch_bends=merged_pb,
        aftertouch=merged_at,
        track_regions=merged_track_regions,
        region_start_beats=merged_region_starts,
    )
    return MergeResult(
        has_conflicts=False,
        conflicts=(),
        merged_snapshot=merged,
    )


# ---------------------------------------------------------------------------
# Merge checkout plan builder
# ---------------------------------------------------------------------------


async def build_merge_checkout_plan(
    session: AsyncSession,
    project_id: str,
    left_id: str,
    right_id: str,
    *,
    working_notes: dict[str, list[dict[str, Any]]] | None = None,
    working_cc: dict[str, list[dict[str, Any]]] | None = None,
    working_pb: dict[str, list[dict[str, Any]]] | None = None,
    working_at: dict[str, list[dict[str, Any]]] | None = None,
) -> MergeCheckoutPlan:
    """Build a complete merge plan: merge-base → three-way diff → checkout plan.

    If conflicts exist, returns them without a checkout plan.
    If conflict-free, builds a CheckoutPlan that would apply the merged
    state to the working tree.
    """
    base_id = await find_merge_base(session, left_id, right_id)
    if base_id is None:
        return MergeCheckoutPlan(
            is_conflict=True,
            conflicts=(MergeConflict(
                region_id="*",
                type="note",
                description="No common ancestor found between the two variations",
            ),),
            checkout_plan=None,
        )

    snapshots = await build_three_way_snapshots(session, base_id, left_id, right_id)
    if snapshots is None:
        return MergeCheckoutPlan(
            is_conflict=True,
            conflicts=(MergeConflict(
                region_id="*",
                type="note",
                description="Cannot reconstruct snapshot for one or more variations",
            ),),
            checkout_plan=None,
        )

    result = build_merge_result(
        base=snapshots.base, left=snapshots.left, right=snapshots.right,
    )

    if result.has_conflicts:
        return MergeCheckoutPlan(
            is_conflict=True,
            conflicts=result.conflicts,
            checkout_plan=None,
        )

    merged = result.merged_snapshot
    assert merged is not None

    plan = build_checkout_plan(
        project_id=project_id,
        target_variation_id=merged.variation_id,
        target_notes=merged.notes,
        target_cc=merged.cc,
        target_pb=merged.pitch_bends,
        target_at=merged.aftertouch,
        working_notes=working_notes or {},
        working_cc=working_cc or {},
        working_pb=working_pb or {},
        working_at=working_at or {},
        track_regions=merged.track_regions,
    )

    return MergeCheckoutPlan(
        is_conflict=False,
        conflicts=(),
        checkout_plan=plan,
    )


# ---------------------------------------------------------------------------
# Per-layer merge helpers (private)
# ---------------------------------------------------------------------------


def _merge_note_layer(
    base: list[dict[str, Any]],
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    region_id: str,
) -> tuple[list[dict[str, Any]], list[MergeConflict]]:
    """Three-way merge for notes in a single region."""
    left_matches = match_notes(base, left)
    right_matches = match_notes(base, right)

    left_by_base: dict[int, NoteMatch] = {}
    for m in left_matches:
        if m.base_index is not None:
            left_by_base[m.base_index] = m

    right_by_base: dict[int, NoteMatch] = {}
    for m in right_matches:
        if m.base_index is not None:
            right_by_base[m.base_index] = m

    conflicts: list[MergeConflict] = []
    merged: list[dict[str, Any]] = []

    for bi, base_note in enumerate(base):
        lm = left_by_base.get(bi)
        rm = right_by_base.get(bi)

        l_removed = lm is not None and lm.is_removed
        l_modified = lm is not None and lm.is_modified
        r_removed = rm is not None and rm.is_removed
        r_modified = rm is not None and rm.is_modified

        if l_modified and r_modified:
            conflicts.append(MergeConflict(
                region_id=region_id, type="note",
                description=f"Both sides modified note at pitch={base_note.get('pitch')} beat={base_note.get('start_beat')}",
            ))
        elif (l_removed and r_modified) or (r_removed and l_modified):
            conflicts.append(MergeConflict(
                region_id=region_id, type="note",
                description=f"One side removed, other modified note at pitch={base_note.get('pitch')} beat={base_note.get('start_beat')}",
            ))
        elif l_removed or r_removed:
            pass
        elif l_modified and lm is not None and lm.proposed_note is not None:
            merged.append(lm.proposed_note)
        elif r_modified and rm is not None and rm.proposed_note is not None:
            merged.append(rm.proposed_note)
        else:
            merged.append(base_note)

    left_additions = [m.proposed_note for m in left_matches if m.is_added and m.proposed_note is not None]
    right_additions = [m.proposed_note for m in right_matches if m.is_added and m.proposed_note is not None]

    addition_conflicts = _check_addition_overlaps(left_additions, right_additions, region_id, "note")
    conflicts.extend(addition_conflicts)

    if not addition_conflicts:
        merged.extend(left_additions)
        merged.extend(right_additions)

    return merged, conflicts


def _merge_event_layer(
    base: list[dict[str, Any]],
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    region_id: str,
    event_type: Literal["cc", "pb", "at"],
    match_fn: Any,
) -> tuple[list[dict[str, Any]], list[MergeConflict]]:
    """Three-way merge for a controller event layer in a single region."""
    left_matches: list[EventMatch] = match_fn(base, left)
    right_matches: list[EventMatch] = match_fn(base, right)

    left_by_base: dict[int, EventMatch] = {}
    right_by_base: dict[int, EventMatch] = {}

    for i, m in enumerate(left_matches):
        if m.base_event is not None and not m.is_added:
            left_by_base[i] = m
    for i, m in enumerate(right_matches):
        if m.base_event is not None and not m.is_added:
            right_by_base[i] = m

    conflicts: list[MergeConflict] = []
    merged: list[dict[str, Any]] = []

    base_indexed = list(enumerate(base))

    for bi, base_ev in base_indexed:
        lm = _find_event_match_for_base(left_matches, base_ev)
        rm = _find_event_match_for_base(right_matches, base_ev)

        l_removed = lm is not None and lm.is_removed
        l_modified = lm is not None and lm.is_modified
        r_removed = rm is not None and rm.is_removed
        r_modified = rm is not None and rm.is_modified

        if l_modified and r_modified:
            conflicts.append(MergeConflict(
                region_id=region_id, type=event_type,
                description=f"Both sides modified {event_type} event at beat={base_ev.get('beat')}",
            ))
        elif (l_removed and r_modified) or (r_removed and l_modified):
            conflicts.append(MergeConflict(
                region_id=region_id, type=event_type,
                description=f"One side removed, other modified {event_type} event at beat={base_ev.get('beat')}",
            ))
        elif l_removed or r_removed:
            pass
        elif l_modified and lm is not None and lm.proposed_event is not None:
            merged.append(lm.proposed_event)
        elif r_modified and rm is not None and rm.proposed_event is not None:
            merged.append(rm.proposed_event)
        else:
            merged.append(base_ev)

    left_additions = [m.proposed_event for m in left_matches if m.is_added and m.proposed_event is not None]
    right_additions = [m.proposed_event for m in right_matches if m.is_added and m.proposed_event is not None]
    merged.extend(left_additions)
    merged.extend(right_additions)

    return merged, conflicts


def _find_event_match_for_base(
    matches: list[EventMatch],
    base_event: dict[str, Any],
) -> EventMatch | None:
    """Find the EventMatch that corresponds to a specific base event."""
    for m in matches:
        if m.base_event is base_event:
            return m
    return None


def _check_addition_overlaps(
    left_adds: list[dict[str, Any]],
    right_adds: list[dict[str, Any]],
    region_id: str,
    conflict_type: Literal["note", "cc", "pb", "at"],
) -> list[MergeConflict]:
    """Detect conflicting additions (same position, different content)."""
    if not left_adds or not right_adds:
        return []

    from app.services.variation.note_matching import _notes_match

    conflicts: list[MergeConflict] = []
    for la in left_adds:
        for ra in right_adds:
            if _notes_match(la, ra):
                if la != ra:
                    conflicts.append(MergeConflict(
                        region_id=region_id,
                        type=conflict_type,
                        description=f"Both sides added conflicting {conflict_type} at pitch={la.get('pitch')} beat={la.get('start_beat')}",
                    ))
    return conflicts
