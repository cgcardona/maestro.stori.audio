"""Dataclass models for executor contexts and results."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.contracts.json_types import (
    AftertouchDict,
    AppliedRegionInfo,
    CCEventDict,
    NoteDict,
    PitchBendDict,
    RegionAftertouchMap,
    RegionCCMap,
    RegionNotesMap,
    RegionPitchBendMap,
)
from app.core.state_store import StateStore, Transaction
from app.core.tracing import TraceContext, log_tool_call


@dataclass
class ExecutionResult:
    """Result of a single tool execution."""

    tool_name: str
    success: bool
    output: dict[str, object]
    error: str | None = None
    entity_created: str | None = None


@dataclass
class ExecutionContext:
    """Context for plan execution with transaction support."""

    store: StateStore
    transaction: Transaction
    trace: TraceContext
    results: list[ExecutionResult] = field(default_factory=list)
    events: list[dict[str, object]] = field(default_factory=list)

    def add_result(
        self,
        tool_name: str,
        success: bool,
        output: dict[str, object],
        error: str | None = None,
        entity_created: str | None = None,
    ) -> None:
        self.results.append(ExecutionResult(
            tool_name=tool_name,
            success=success,
            output=output,
            error=error,
            entity_created=entity_created,
        ))
        log_tool_call(self.trace.trace_id, tool_name, output, success, error)

    def add_event(self, event: dict[str, object]) -> None:
        self.events.append(event)

    @property
    def all_successful(self) -> bool:
        return all(r.success for r in self.results)

    @property
    def failed_tools(self) -> list[str]:
        return [r.tool_name for r in self.results if not r.success]

    @property
    def created_entities(self) -> dict[str, str]:
        return {
            r.tool_name: r.entity_created
            for r in self.results
            if r.entity_created
        }


# ---------------------------------------------------------------------------
# Snapshot & variation models
# ---------------------------------------------------------------------------


@dataclass
class SnapshotBundle:
    """Unified snapshot of region musical data.

    Used by both the agent-team path (``capture_*_snapshot``) and
    the single-instrument path (``VariationContext`` incremental capture).
    One type, one shape, everywhere.
    """

    notes: RegionNotesMap = field(default_factory=dict)
    cc: RegionCCMap = field(default_factory=dict)
    pitch_bends: RegionPitchBendMap = field(default_factory=dict)
    aftertouch: RegionAftertouchMap = field(default_factory=dict)
    track_regions: dict[str, str] = field(default_factory=dict)
    region_start_beats: dict[str, float] = field(default_factory=dict)


@dataclass
class VariationExecutionContext:
    """Mutable execution state — lives only inside the executor.

    Holds the StateStore reference needed for entity resolution during
    tool dispatch.  Must NOT cross the Muse boundary.
    """

    store: StateStore
    trace: TraceContext


@dataclass
class VariationContext:
    """Data container for variation computation — no store access.

    Accumulates base and proposed musical data during tool dispatch,
    then passed to ``compute_variation_from_context`` which sees only data.
    """

    trace: TraceContext
    base: SnapshotBundle = field(default_factory=SnapshotBundle)
    proposed: SnapshotBundle = field(default_factory=SnapshotBundle)

    def capture_base_notes(self, region_id: str, track_id: str, notes: list[NoteDict]) -> None:
        if region_id not in self.base.notes:
            from app.core.executor.note_utils import _normalize_note
            self.base.notes[region_id] = [_normalize_note(n) for n in notes]
            self.base.track_regions[region_id] = track_id
            self.proposed.track_regions[region_id] = track_id

    def record_proposed_notes(self, region_id: str, notes: list[NoteDict]) -> None:
        from app.core.executor.note_utils import _normalize_note
        self.proposed.notes[region_id] = [_normalize_note(n) for n in notes]

    def record_proposed_cc(self, region_id: str, cc_events: list[CCEventDict]) -> None:
        if cc_events:
            self.proposed.cc.setdefault(region_id, []).extend(cc_events)

    def record_proposed_pitch_bends(self, region_id: str, pitch_bends: list[PitchBendDict]) -> None:
        if pitch_bends:
            self.proposed.pitch_bends.setdefault(region_id, []).extend(pitch_bends)

    def record_proposed_aftertouch(self, region_id: str, aftertouch: list[AftertouchDict]) -> None:
        if aftertouch:
            self.proposed.aftertouch.setdefault(region_id, []).extend(aftertouch)


@dataclass
class VariationApplyResult:
    """Result from applying variation phrases."""

    success: bool
    applied_phrase_ids: list[str]
    notes_added: int
    notes_removed: int
    notes_modified: int
    updated_regions: list[AppliedRegionInfo] = field(default_factory=list)
    error: str | None = None
