"""Muse Drift Detection Engine — 'git status' for music.

Compares a HEAD snapshot (from persisted variation history) against
a working snapshot (live StateStore capture) to produce a deterministic
DriftReport describing what changed since the last commit.

Diffs notes AND controller data (CC, pitch bends, aftertouch).

Pure data — no side effects, no mutations.

Boundary rules:
  - Must NOT import StateStore, EntityRegistry, or get_or_create_store.
  - Must NOT import executor modules or app.core.executor.*.
  - Must NOT import LLM handlers or maestro_* modules.
  - May import note_matching from VariationService (pure diff logic).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from typing_extensions import TypedDict

from app.contracts.json_types import (
    AftertouchDict,
    CCEventDict,
    NoteDict,
    PitchBendDict,
    RegionMetadataDB,
)
from app.services.variation.note_matching import (
    match_notes,
    match_cc_events,
    match_pitch_bends,
    match_aftertouch,
)

logger = logging.getLogger(__name__)

MAX_SAMPLE_CHANGES = 5


class SampleChange(TypedDict, total=False):
    """A single note change captured as a human-readable diff sample.

    ``type`` is always present; ``note``/``before``/``after`` depend on type.
    """

    type: Literal["added", "removed", "modified"]
    note: NoteDict | None
    before: NoteDict | None
    after: NoteDict | None


class DriftSeverity(str, Enum):
    """How much the working tree has diverged from HEAD."""

    CLEAN = "clean"
    DIRTY = "dirty"
    DIVERGED = "diverged"


@dataclass(frozen=True)
class RegionDriftSummary:
    """Per-region drift summary with note + controller change counts."""

    region_id: str
    track_id: str
    # Notes
    added: int = 0
    removed: int = 0
    modified: int = 0
    # CC
    cc_added: int = 0
    cc_removed: int = 0
    cc_modified: int = 0
    # Pitch bends
    pb_added: int = 0
    pb_removed: int = 0
    pb_modified: int = 0
    # Aftertouch
    at_added: int = 0
    at_removed: int = 0
    at_modified: int = 0

    sample_changes: tuple[SampleChange, ...] = ()
    head_fingerprint: str = ""
    working_fingerprint: str = ""

    @property
    def is_clean(self) -> bool:
        return (
            self.added == 0 and self.removed == 0 and self.modified == 0
            and self.cc_added == 0 and self.cc_removed == 0 and self.cc_modified == 0
            and self.pb_added == 0 and self.pb_removed == 0 and self.pb_modified == 0
            and self.at_added == 0 and self.at_removed == 0 and self.at_modified == 0
        )


@dataclass(frozen=True)
class DriftReport:
    """Deterministic report of working-tree vs HEAD divergence.

    Covers notes and all controller data (CC, pitch bends, aftertouch).
    """

    project_id: str
    head_variation_id: str
    severity: DriftSeverity
    is_clean: bool
    changed_regions: tuple[str, ...] = ()
    added_regions: tuple[str, ...] = ()
    deleted_regions: tuple[str, ...] = ()
    region_summaries: dict[str, RegionDriftSummary] = field(default_factory=dict)

    @property
    def total_changes(self) -> int:
        return sum(
            s.added + s.removed + s.modified
            + s.cc_added + s.cc_removed + s.cc_modified
            + s.pb_added + s.pb_removed + s.pb_modified
            + s.at_added + s.at_removed + s.at_modified
            for s in self.region_summaries.values()
        )

    def requires_user_action(self) -> bool:
        """Whether this drift state should block a commit."""
        return self.severity != DriftSeverity.CLEAN


@dataclass(frozen=True)
class CommitConflictPayload:
    """Lightweight conflict summary returned in 409 responses.

    Derived from DriftReport — excludes bulky sample_changes and
    full region_summaries to keep the payload small.
    """

    project_id: str
    head_variation_id: str
    severity: str
    changed_regions: tuple[str, ...]
    added_regions: tuple[str, ...]
    deleted_regions: tuple[str, ...]
    total_changes: int
    fingerprint_delta: dict[str, tuple[str, str]]

    @classmethod
    def from_drift_report(cls, report: DriftReport) -> "CommitConflictPayload":
        fp_delta: dict[str, tuple[str, str]] = {}
        for rid, summary in report.region_summaries.items():
            if not summary.is_clean:
                fp_delta[rid] = (summary.head_fingerprint, summary.working_fingerprint)
        return cls(
            project_id=report.project_id,
            head_variation_id=report.head_variation_id,
            severity=report.severity.value,
            changed_regions=report.changed_regions,
            added_regions=report.added_regions,
            deleted_regions=report.deleted_regions,
            total_changes=report.total_changes,
            fingerprint_delta=fp_delta,
        )


def _fingerprint(events: Sequence[Mapping[str, object]]) -> str:
    """Stable hash of a note or event list for cache-friendly comparison."""
    canonical = sorted(
        events,
        key=lambda e: (
            e.get("pitch", 0),
            e.get("cc", 0),
            e.get("start_beat", e.get("beat", 0.0)),
            e.get("value", 0),
        ),
    )
    raw = json.dumps(canonical, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _combined_fingerprint(
    notes: Sequence[Mapping[str, object]],
    cc: Sequence[Mapping[str, object]],
    pb: Sequence[Mapping[str, object]],
    at: Sequence[Mapping[str, object]],
) -> str:
    """Composite fingerprint across all data types for a region."""
    combined = json.dumps({
        "n": _fingerprint(notes),
        "c": _fingerprint(cc),
        "p": _fingerprint(pb),
        "a": _fingerprint(at),
    }, sort_keys=True)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def compute_drift_report(
    *,
    project_id: str,
    head_variation_id: str,
    head_snapshot_notes: dict[str, list[NoteDict]],
    working_snapshot_notes: dict[str, list[NoteDict]],
    track_regions: dict[str, str],
    head_cc: dict[str, list[CCEventDict]] | None = None,
    working_cc: dict[str, list[CCEventDict]] | None = None,
    head_pb: dict[str, list[PitchBendDict]] | None = None,
    working_pb: dict[str, list[PitchBendDict]] | None = None,
    head_at: dict[str, list[AftertouchDict]] | None = None,
    working_at: dict[str, list[AftertouchDict]] | None = None,
    region_metadata: dict[str, RegionMetadataDB] | None = None,
) -> DriftReport:
    """Compare HEAD snapshot against working snapshot — notes + controllers.

    Pure function — no database access, no StateStore.  Uses matching
    functions from the VariationService note-matching module.

    Args:
        project_id: Project identifier.
        head_variation_id: The HEAD variation being compared against.
        head_snapshot_notes: Notes per region from HEAD (reconstructed).
        working_snapshot_notes: Notes per region from working tree (live).
        track_regions: Mapping of region_id to track_id.
        head_cc / working_cc: CC events per region.
        head_pb / working_pb: Pitch bend events per region.
        head_at / working_at: Aftertouch events per region.
        region_metadata: Optional region metadata for additional context.
    """
    _head_cc = head_cc or {}
    _working_cc = working_cc or {}
    _head_pb = head_pb or {}
    _working_pb = working_pb or {}
    _head_at = head_at or {}
    _working_at = working_at or {}

    all_head_rids = (
        set(head_snapshot_notes) | set(_head_cc) | set(_head_pb) | set(_head_at)
    )
    all_working_rids = (
        set(working_snapshot_notes) | set(_working_cc) | set(_working_pb) | set(_working_at)
    )

    added_regions = sorted(all_working_rids - all_head_rids)
    deleted_regions = sorted(all_head_rids - all_working_rids)
    common_regions = all_head_rids & all_working_rids

    changed_regions: list[str] = []
    region_summaries: dict[str, RegionDriftSummary] = {}

    # ── Common regions: diff notes + controllers ──────────────────────
    for rid in sorted(common_regions):
        track_id = track_regions.get(rid, "unknown")
        h_notes = head_snapshot_notes.get(rid, [])
        w_notes = working_snapshot_notes.get(rid, [])
        h_cc = _head_cc.get(rid, [])
        w_cc = _working_cc.get(rid, [])
        h_pb = _head_pb.get(rid, [])
        w_pb = _working_pb.get(rid, [])
        h_at = _head_at.get(rid, [])
        w_at = _working_at.get(rid, [])

        head_fp = _combined_fingerprint(h_notes, h_cc, h_pb, h_at)
        working_fp = _combined_fingerprint(w_notes, w_cc, w_pb, w_at)

        if head_fp == working_fp:
            region_summaries[rid] = RegionDriftSummary(
                region_id=rid, track_id=track_id,
                head_fingerprint=head_fp, working_fingerprint=working_fp,
            )
            continue

        # Notes
        note_matches = match_notes(h_notes, w_notes)
        n_adds = sum(1 for m in note_matches if m.is_added)
        n_rems = sum(1 for m in note_matches if m.is_removed)
        n_mods = sum(1 for m in note_matches if m.is_modified)

        # CC
        cc_matches = match_cc_events(h_cc, w_cc)
        cc_adds = sum(1 for m in cc_matches if m.is_added)
        cc_rems = sum(1 for m in cc_matches if m.is_removed)
        cc_mods = sum(1 for m in cc_matches if m.is_modified)

        # Pitch bends
        pb_matches = match_pitch_bends(h_pb, w_pb)
        pb_adds = sum(1 for m in pb_matches if m.is_added)
        pb_rems = sum(1 for m in pb_matches if m.is_removed)
        pb_mods = sum(1 for m in pb_matches if m.is_modified)

        # Aftertouch
        at_matches = match_aftertouch(h_at, w_at)
        at_adds = sum(1 for m in at_matches if m.is_added)
        at_rems = sum(1 for m in at_matches if m.is_removed)
        at_mods = sum(1 for m in at_matches if m.is_modified)

        has_changes = (
            n_adds + n_rems + n_mods
            + cc_adds + cc_rems + cc_mods
            + pb_adds + pb_rems + pb_mods
            + at_adds + at_rems + at_mods
        ) > 0

        # Build capped sample_changes from note matches only
        samples: list[SampleChange] = []
        for m in note_matches:
            if len(samples) >= MAX_SAMPLE_CHANGES:
                break
            if m.is_added:
                samples.append(SampleChange(type="added", note=m.proposed_note))
            elif m.is_removed:
                samples.append(SampleChange(type="removed", note=m.base_note))
            elif m.is_modified:
                samples.append(SampleChange(type="modified", before=m.base_note, after=m.proposed_note))

        if has_changes:
            changed_regions.append(rid)

        region_summaries[rid] = RegionDriftSummary(
            region_id=rid, track_id=track_id,
            added=n_adds, removed=n_rems, modified=n_mods,
            cc_added=cc_adds, cc_removed=cc_rems, cc_modified=cc_mods,
            pb_added=pb_adds, pb_removed=pb_rems, pb_modified=pb_mods,
            at_added=at_adds, at_removed=at_rems, at_modified=at_mods,
            sample_changes=tuple(samples),
            head_fingerprint=head_fp, working_fingerprint=working_fp,
        )

    # ── Added regions (in working but not head) ───────────────────────
    for rid in added_regions:
        track_id = track_regions.get(rid, "unknown")
        w_notes = working_snapshot_notes.get(rid, [])
        w_cc = _working_cc.get(rid, [])
        w_pb = _working_pb.get(rid, [])
        w_at = _working_at.get(rid, [])
        region_summaries[rid] = RegionDriftSummary(
            region_id=rid, track_id=track_id,
            added=len(w_notes),
            cc_added=len(w_cc), pb_added=len(w_pb), at_added=len(w_at),
            working_fingerprint=_combined_fingerprint(w_notes, w_cc, w_pb, w_at),
        )

    # ── Deleted regions (in head but not working) ─────────────────────
    for rid in deleted_regions:
        track_id = track_regions.get(rid, "unknown")
        h_notes = head_snapshot_notes.get(rid, [])
        h_cc = _head_cc.get(rid, [])
        h_pb = _head_pb.get(rid, [])
        h_at = _head_at.get(rid, [])
        region_summaries[rid] = RegionDriftSummary(
            region_id=rid, track_id=track_id,
            removed=len(h_notes),
            cc_removed=len(h_cc), pb_removed=len(h_pb), at_removed=len(h_at),
            head_fingerprint=_combined_fingerprint(h_notes, h_cc, h_pb, h_at),
        )

    is_clean = not changed_regions and not added_regions and not deleted_regions
    severity = DriftSeverity.CLEAN if is_clean else DriftSeverity.DIRTY

    logger.info(
        "✅ Drift report: %s (%d changed, %d added, %d deleted regions)",
        severity.value, len(changed_regions), len(added_regions), len(deleted_regions),
    )

    return DriftReport(
        project_id=project_id,
        head_variation_id=head_variation_id,
        severity=severity,
        is_clean=is_clean,
        changed_regions=tuple(changed_regions),
        added_regions=tuple(added_regions),
        deleted_regions=tuple(deleted_regions),
        region_summaries=region_summaries,
    )
