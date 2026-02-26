"""Muse History Controller — orchestrates checkout, merge, time travel.

Coordinates HEAD movement, snapshot reconstruction, checkout plan
generation, drift safety, merge execution, and commit creation.

This is the internal entry point for undo/redo/merge — no route yet.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.contracts.json_types import (
    AftertouchDict,
    CCEventDict,
    NoteDict,
    PitchBendDict,
    RegionAftertouchMap,
    RegionCCMap,
    RegionNotesMap,
    RegionPitchBendMap,
)

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tracing import TraceContext
from app.models.variation import Variation as DomainVariation
from app.services import muse_repository
from app.services.muse_checkout import build_checkout_plan
from app.services.muse_checkout_executor import (
    CheckoutExecutionResult,
    execute_checkout_plan,
)
from app.services.muse_drift import DriftSeverity, compute_drift_report
from app.services.muse_merge import (
    MergeConflict,
    build_merge_checkout_plan,
)
if TYPE_CHECKING:
    from app.core.state_store import StateStore

from app.services.muse_replay import (
    HeadSnapshot,
    reconstruct_head_snapshot,
    reconstruct_variation_snapshot,
)

logger = logging.getLogger(__name__)


class CheckoutBlockedError(Exception):
    """Raised when checkout is blocked by a dirty working tree."""

    def __init__(self, severity: DriftSeverity, total_changes: int) -> None:
        self.severity = severity
        self.total_changes = total_changes
        super().__init__(
            f"Working tree is {severity.value} ({total_changes} changes). "
            f"Commit or discard changes, or use force=True."
        )


@dataclass(frozen=True)
class CheckoutSummary:
    """Full summary of a checkout operation."""

    project_id: str
    from_variation_id: str | None
    to_variation_id: str
    execution: CheckoutExecutionResult
    head_moved: bool


async def checkout_to_variation(
    *,
    session: AsyncSession,
    project_id: str,
    target_variation_id: str,
    store: StateStore,
    trace: TraceContext,
    force: bool = False,
    emit_sse: bool = True,
) -> CheckoutSummary:
    """Check out to a specific variation — the musical equivalent of ``git checkout``.

    Orchestration flow:
    1. Load current HEAD.
    2. Reconstruct target snapshot.
    3. If not force: run drift detection → block if dirty.
    4. Build checkout plan (target vs working).
    5. Execute checkout plan (mutate StateStore).
    6. Move HEAD pointer.

    Args:
        session: Async DB session.
        project_id: Project identifier.
        target_variation_id: Variation to check out to.
        store: StateStore instance.
        trace: Trace context.
        force: Bypass drift safety check.
        emit_sse: Emit SSE events during execution.

    Raises:
        CheckoutBlockedError: Working tree is dirty and force=False.
        ValueError: Target variation not found.
    """
    current_head = await muse_repository.get_head(session, project_id)
    from_variation_id = current_head.variation_id if current_head else None

    target_snap = await reconstruct_variation_snapshot(session, target_variation_id)
    if target_snap is None:
        raise ValueError(f"Cannot reconstruct snapshot for variation {target_variation_id}")

    # ── Drift safety ──────────────────────────────────────────────
    if not force and current_head is not None:
        head_snap = await reconstruct_head_snapshot(session, project_id)
        if head_snap is not None:
            working_notes = _capture_working_notes(store)
            working_cc = _capture_working_cc(store)
            working_pb = _capture_working_pb(store)
            working_at = _capture_working_at(store)

            drift = compute_drift_report(
                project_id=project_id,
                head_variation_id=head_snap.variation_id,
                head_snapshot_notes=head_snap.notes,
                working_snapshot_notes=working_notes,
                track_regions=head_snap.track_regions,
                head_cc=head_snap.cc,
                working_cc=working_cc,
                head_pb=head_snap.pitch_bends,
                working_pb=working_pb,
                head_at=head_snap.aftertouch,
                working_at=working_at,
            )
            if drift.requires_user_action():
                raise CheckoutBlockedError(drift.severity, drift.total_changes)

    # ── Build checkout plan ───────────────────────────────────────
    working_notes = _capture_working_notes(store)
    working_cc = _capture_working_cc(store)
    working_pb = _capture_working_pb(store)
    working_at = _capture_working_at(store)

    plan = build_checkout_plan(
        project_id=project_id,
        target_variation_id=target_variation_id,
        target_notes=target_snap.notes,
        target_cc=target_snap.cc,
        target_pb=target_snap.pitch_bends,
        target_at=target_snap.aftertouch,
        working_notes=working_notes,
        working_cc=working_cc,
        working_pb=working_pb,
        working_at=working_at,
        track_regions=target_snap.track_regions,
    )

    # ── Execute ───────────────────────────────────────────────────
    result = execute_checkout_plan(
        checkout_plan=plan,
        store=store,
        trace=trace,
        emit_sse=emit_sse,
    )

    # ── Move HEAD ─────────────────────────────────────────────────
    head_moved = False
    if result.failed == 0:
        await muse_repository.move_head(session, project_id, target_variation_id)
        head_moved = True
        logger.info(
            "✅ Checkout complete: %s → %s (%d tool calls)",
            (from_variation_id or "none")[:8],
            target_variation_id[:8],
            result.executed,
        )
    else:
        logger.warning(
            "⚠️ Checkout execution had failures — HEAD not moved (%d/%d failed)",
            result.failed, result.executed + result.failed,
        )

    return CheckoutSummary(
        project_id=project_id,
        from_variation_id=from_variation_id,
        to_variation_id=target_variation_id,
        execution=result,
        head_moved=head_moved,
    )


class MergeConflictError(Exception):
    """Raised when a merge has unresolvable conflicts."""

    def __init__(self, conflicts: tuple[MergeConflict, ...]) -> None:
        self.conflicts = conflicts
        regions = {c.region_id for c in conflicts}
        super().__init__(
            f"Merge has {len(conflicts)} conflict(s) in {len(regions)} region(s). "
            f"Resolve conflicts or use force=True."
        )


@dataclass(frozen=True)
class MergeSummary:
    """Full summary of a merge operation."""

    project_id: str
    left_id: str
    right_id: str
    merge_variation_id: str
    execution: CheckoutExecutionResult
    head_moved: bool


async def merge_variations(
    *,
    session: AsyncSession,
    project_id: str,
    left_id: str,
    right_id: str,
    store: StateStore,
    trace: TraceContext,
    force: bool = False,
    emit_sse: bool = True,
) -> MergeSummary:
    """Merge two variations — the musical equivalent of ``git merge``.

    Orchestration flow:
    1. Compute merge base.
    2. Build three-way merge result.
    3. If conflicts and not force → raise MergeConflictError.
    4. Build checkout plan from merged snapshot.
    5. Execute checkout plan.
    6. Create merge commit (two parents).
    7. Move HEAD.

    Args:
        session: Async DB session.
        project_id: Project identifier.
        left_id: "Ours" — typically the current HEAD.
        right_id: "Theirs" — the branch being merged in.
        store: StateStore instance.
        trace: Trace context.
        force: Bypass conflict check (takes left for conflicts).
        emit_sse: Emit SSE events during execution.

    Raises:
        MergeConflictError: Merge has conflicts and force=False.
    """
    working_notes = _capture_working_notes(store)
    working_cc = _capture_working_cc(store)
    working_pb = _capture_working_pb(store)
    working_at = _capture_working_at(store)

    merge_plan = await build_merge_checkout_plan(
        session, project_id, left_id, right_id,
        working_notes=working_notes,
        working_cc=working_cc,
        working_pb=working_pb,
        working_at=working_at,
    )

    if merge_plan.is_conflict and not force:
        raise MergeConflictError(merge_plan.conflicts)

    if merge_plan.checkout_plan is None:
        raise MergeConflictError(merge_plan.conflicts)

    result = execute_checkout_plan(
        checkout_plan=merge_plan.checkout_plan,
        store=store,
        trace=trace,
        emit_sse=emit_sse,
    )

    merge_vid = str(uuid.uuid4())
    head_moved = False

    if result.failed == 0:
        merge_variation = DomainVariation(
            variation_id=merge_vid,
            intent="merge",
            ai_explanation=f"Merge of {left_id[:8]} and {right_id[:8]}",
            affected_tracks=[],
            affected_regions=[],
            beat_range=(0.0, 0.0),
            phrases=[],
        )
        await muse_repository.save_variation(
            session, merge_variation,
            project_id=project_id,
            base_state_id="merge",
            conversation_id="merge",
            region_metadata={},
            status="committed",
            parent_variation_id=left_id,
            parent2_variation_id=right_id,
        )
        await muse_repository.move_head(session, project_id, merge_vid)
        head_moved = True
        logger.info(
            "✅ Merge complete: %s + %s → %s",
            left_id[:8], right_id[:8], merge_vid[:8],
        )
    else:
        logger.warning(
            "⚠️ Merge execution had failures — commit not created (%d/%d failed)",
            result.failed, result.executed + result.failed,
        )

    return MergeSummary(
        project_id=project_id,
        left_id=left_id,
        right_id=right_id,
        merge_variation_id=merge_vid,
        execution=result,
        head_moved=head_moved,
    )


def _capture_working_notes(store: StateStore) -> RegionNotesMap:
    """Extract notes from all regions in the store."""
    result: RegionNotesMap = {}
    if hasattr(store, "_region_notes"):
        for rid, notes in store._region_notes.items():
            if notes:
                result[rid] = list(notes)
    return result


def _capture_working_cc(store: StateStore) -> RegionCCMap:
    """Extract CC events from all regions in the store."""
    result: RegionCCMap = {}
    if hasattr(store, "_region_cc"):
        for rid, events in store._region_cc.items():
            if events:
                result[rid] = list(events)
    return result


def _capture_working_pb(store: StateStore) -> RegionPitchBendMap:
    """Extract pitch bend events from all regions in the store."""
    result: RegionPitchBendMap = {}
    if hasattr(store, "_region_pitch_bends"):
        for rid, events in store._region_pitch_bends.items():
            if events:
                result[rid] = list(events)
    return result


def _capture_working_at(store: StateStore) -> RegionAftertouchMap:
    """Extract aftertouch events from all regions in the store."""
    result: RegionAftertouchMap = {}
    if hasattr(store, "_region_aftertouch"):
        for rid, events in store._region_aftertouch.items():
            if events:
                result[rid] = list(events)
    return result
