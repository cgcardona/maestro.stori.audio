"""Dataclass models for executor contexts and results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.state_store import StateStore, Transaction
from app.core.tracing import TraceContext, log_tool_call


@dataclass
class ExecutionResult:
    """Result of a single tool execution."""

    tool_name: str
    success: bool
    output: dict[str, Any]
    error: Optional[str] = None
    entity_created: Optional[str] = None


@dataclass
class ExecutionContext:
    """Context for plan execution with transaction support."""

    store: StateStore
    transaction: Transaction
    trace: TraceContext
    results: list[ExecutionResult] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def add_result(
        self,
        tool_name: str,
        success: bool,
        output: dict[str, Any],
        error: Optional[str] = None,
        entity_created: Optional[str] = None,
    ) -> None:
        self.results.append(ExecutionResult(
            tool_name=tool_name,
            success=success,
            output=output,
            error=error,
            entity_created=entity_created,
        ))
        log_tool_call(self.trace.trace_id, tool_name, output, success, error)

    def add_event(self, event: dict[str, Any]) -> None:
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


@dataclass
class VariationContext:
    """Context for variation mode execution (read-only state, no transaction)."""

    store: StateStore
    trace: TraceContext
    base_notes: dict[str, list[dict]]
    proposed_notes: dict[str, list[dict]]
    track_regions: dict[str, str]
    proposed_cc: dict[str, list[dict]] = field(default_factory=dict)
    proposed_pitch_bends: dict[str, list[dict]] = field(default_factory=dict)
    proposed_aftertouch: dict[str, list[dict]] = field(default_factory=dict)

    def capture_base_notes(self, region_id: str, track_id: str, notes: list[dict]) -> None:
        if region_id not in self.base_notes:
            from app.core.executor.note_utils import _normalize_note
            self.base_notes[region_id] = [_normalize_note(n) for n in notes]
            self.track_regions[region_id] = track_id

    def record_proposed_notes(self, region_id: str, notes: list[dict]) -> None:
        from app.core.executor.note_utils import _normalize_note
        self.proposed_notes[region_id] = [_normalize_note(n) for n in notes]

    def record_proposed_cc(self, region_id: str, cc_events: list[dict]) -> None:
        if cc_events:
            self.proposed_cc.setdefault(region_id, []).extend(cc_events)

    def record_proposed_pitch_bends(self, region_id: str, pitch_bends: list[dict]) -> None:
        if pitch_bends:
            self.proposed_pitch_bends.setdefault(region_id, []).extend(pitch_bends)

    def record_proposed_aftertouch(self, region_id: str, aftertouch: list[dict]) -> None:
        if aftertouch:
            self.proposed_aftertouch.setdefault(region_id, []).extend(aftertouch)


@dataclass
class VariationApplyResult:
    """Result from applying variation phrases."""

    success: bool
    applied_phrase_ids: list[str]
    notes_added: int
    notes_removed: int
    notes_modified: int
    updated_regions: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
