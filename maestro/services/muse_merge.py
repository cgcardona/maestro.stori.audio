"""Muse Merge Engine — three-way merge for musical variations.

Produces a ``MergeResult`` by comparing base, left, and right snapshots.
Auto-merges non-conflicting changes; reports conflicts when both sides
modify the same note or controller event.

Consults ``.museattributes`` (via ``muse_attributes``) to resolve per-dimension
merge strategies before running the three-way diff.  When a strategy of
``ours`` or ``theirs`` is configured for a dimension, conflict detection for
that dimension is skipped and the winning side is taken wholesale.

Boundary rules:
  - Must NOT import StateStore, executor, MCP tools, or handlers.
  - May import muse_repository, muse_replay, muse_checkout, note_matching.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from maestro.contracts.json_types import (
    AftertouchDict,
    CCEventDict,
    NoteDict,
    PitchBendDict,
    RegionAftertouchMap,
    RegionCCMap,
    RegionNotesMap,
    RegionPitchBendMap,
)
from maestro.services.muse_attributes import (
    MergeStrategy,
    MuseAttribute,
    load_attributes,
    resolve_strategy,
)
from maestro.services.muse_checkout import CheckoutPlan, build_checkout_plan
from maestro.services.muse_merge_base import find_merge_base
from maestro.services.muse_replay import HeadSnapshot, reconstruct_variation_snapshot
from maestro.services.variation.note_matching import (
    EventMatch,
    NoteMatch,
    match_aftertouch,
    match_cc_events,
    match_notes,
    match_pitch_bends,
)

logger = logging.getLogger(__name__)

# Mirrors the constrained TypeVar in note_matching so _merge_event_layer
# can propagate the concrete event type (CCEventDict, PitchBendDict, or
# AftertouchDict) without overloads or casts.
_EV = TypeVar("_EV", CCEventDict, PitchBendDict, AftertouchDict)


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
    attributes: list[MuseAttribute] | None = None,
) -> MergeResult:
    """Perform a three-way merge of musical state.

    For each region, compares left and right against the common base:
    - Only one side changed → take that side.
    - Neither changed → keep base.
    - Both changed → per-note/event conflict detection.

    When ``attributes`` are provided (from a ``.museattributes`` file), the
    configured strategy for each region's track + dimension is consulted
    before running conflict detection.  ``ours`` takes the left snapshot,
    ``theirs`` takes the right snapshot — both skip conflict detection entirely.
    ``union``, ``auto``, and ``manual`` fall through to normal three-way merge.

    Returns a MergeResult with the merged snapshot (if conflict-free)
    or the list of conflicts.
    """
    attrs: list[MuseAttribute] = attributes or []

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
    merged_notes: RegionNotesMap = {}
    merged_cc: RegionCCMap = {}
    merged_pb: RegionPitchBendMap = {}
    merged_at: RegionAftertouchMap = {}
    merged_track_regions: dict[str, str] = {}
    merged_region_starts: dict[str, float] = {}

    for tr in (base.track_regions, left.track_regions, right.track_regions):
        merged_track_regions.update(tr)
    for rs in (base.region_start_beats, left.region_start_beats, right.region_start_beats):
        merged_region_starts.update(rs)

    for rid in all_regions:
        # Determine the track name for this region so we can look up attributes.
        track_name = merged_track_regions.get(rid, rid)

        # --- notes (melodic/rhythmic dimension) ---
        note_strategy = resolve_strategy(attrs, track_name, "melodic")
        b_notes = base.notes.get(rid, [])
        l_notes = left.notes.get(rid, [])
        r_notes = right.notes.get(rid, [])

        if note_strategy == MergeStrategy.OURS:
            merged_notes[rid] = l_notes
        elif note_strategy == MergeStrategy.THEIRS:
            merged_notes[rid] = r_notes
        else:
            notes_result, note_conflicts = _merge_note_layer(b_notes, l_notes, r_notes, rid)
            merged_notes[rid] = notes_result
            conflicts.extend(note_conflicts)

        # --- CC events (dynamic dimension) ---
        cc_strategy = resolve_strategy(attrs, track_name, "dynamic")
        b_cc = base.cc.get(rid, [])
        l_cc = left.cc.get(rid, [])
        r_cc = right.cc.get(rid, [])

        if cc_strategy == MergeStrategy.OURS:
            merged_cc[rid] = l_cc
        elif cc_strategy == MergeStrategy.THEIRS:
            merged_cc[rid] = r_cc
        else:
            cc_result, cc_conflicts = _merge_event_layer(b_cc, l_cc, r_cc, rid, "cc", match_cc_events)
            merged_cc[rid] = cc_result
            conflicts.extend(cc_conflicts)

        # --- Pitch bends (harmonic dimension) ---
        pb_strategy = resolve_strategy(attrs, track_name, "harmonic")
        b_pb = base.pitch_bends.get(rid, [])
        l_pb = left.pitch_bends.get(rid, [])
        r_pb = right.pitch_bends.get(rid, [])

        if pb_strategy == MergeStrategy.OURS:
            merged_pb[rid] = l_pb
        elif pb_strategy == MergeStrategy.THEIRS:
            merged_pb[rid] = r_pb
        else:
            pb_result, pb_conflicts = _merge_event_layer(b_pb, l_pb, r_pb, rid, "pb", match_pitch_bends)
            merged_pb[rid] = pb_result
            conflicts.extend(pb_conflicts)

        # --- Aftertouch (dynamic dimension, expression layer) ---
        at_strategy = resolve_strategy(attrs, track_name, "dynamic")
        b_at = base.aftertouch.get(rid, [])
        l_at = left.aftertouch.get(rid, [])
        r_at = right.aftertouch.get(rid, [])

        if at_strategy == MergeStrategy.OURS:
            merged_at[rid] = l_at
        elif at_strategy == MergeStrategy.THEIRS:
            merged_at[rid] = r_at
        else:
            at_result, at_conflicts = _merge_event_layer(b_at, l_at, r_at, rid, "at", match_aftertouch)
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
    working_notes: RegionNotesMap | None = None,
    working_cc: RegionCCMap | None = None,
    working_pb: RegionPitchBendMap | None = None,
    working_at: RegionAftertouchMap | None = None,
    repo_path: Path | None = None,
) -> MergeCheckoutPlan:
    """Build a complete merge plan: merge-base → three-way diff → checkout plan.

    Loads ``.museattributes`` from ``repo_path`` (if provided) to apply
    per-track, per-dimension merge strategies before conflict detection.

    If conflicts exist, returns them without a checkout plan.
    If conflict-free, builds a CheckoutPlan that would apply the merged
    state to the working tree.
    """
    attributes = load_attributes(repo_path) if repo_path is not None else []

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
        attributes=attributes,
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
    base: list[NoteDict],
    left: list[NoteDict],
    right: list[NoteDict],
    region_id: str,
) -> tuple[list[NoteDict], list[MergeConflict]]:
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
    merged: list[NoteDict] = []

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
    base: list[_EV],
    left: list[_EV],
    right: list[_EV],
    region_id: str,
    event_type: Literal["cc", "pb", "at"],
    match_fn: Callable[[list[_EV], list[_EV]], list[EventMatch[_EV]]],
) -> tuple[list[_EV], list[MergeConflict]]:
    """Three-way merge for a controller event layer in a single region."""
    left_matches: list[EventMatch[_EV]] = match_fn(base, left)
    right_matches: list[EventMatch[_EV]] = match_fn(base, right)

    conflicts: list[MergeConflict] = []
    merged: list[_EV] = []

    for base_ev in base:
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

    for m in left_matches:
        if m.is_added and m.proposed_event is not None:
            merged.append(m.proposed_event)
    for m in right_matches:
        if m.is_added and m.proposed_event is not None:
            merged.append(m.proposed_event)

    return merged, conflicts


def _find_event_match_for_base(
    matches: list[EventMatch[_EV]],
    base_event: _EV,
) -> EventMatch[_EV] | None:
    """Find the EventMatch that corresponds to a specific base event."""
    for m in matches:
        if m.base_event is base_event:
            return m
    return None


def _check_addition_overlaps(
    left_adds: list[NoteDict],
    right_adds: list[NoteDict],
    region_id: str,
    conflict_type: Literal["note", "cc", "pb", "at"],
) -> list[MergeConflict]:
    """Detect conflicting additions (same position, different content)."""
    if not left_adds or not right_adds:
        return []

    from maestro.services.variation.note_matching import _notes_match

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
