"""Stori Protocol event models — single source of truth for SSE wire format.

Every SSE event the backend emits is an instance of a StoriEvent subclass.
Raw dicts are forbidden. The emitter validates and serializes through these
models, guaranteeing wire-format consistency.

Wire format rules:
  - All keys are camelCase (via CamelModel alias_generator)
  - Every event has: type, seq (injected by emitter), protocolVersion
  - JSON serialization uses model_dump(by_alias=True, exclude_none=True)

Extra fields policy:
  - Events use extra="forbid" — strict outbound contract.
  - ProjectSnapshot (inbound) uses extra="allow" — see schemas/project.py.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import ConfigDict, Field

from app.models.base import CamelModel
from app.protocol.version import STORI_PROTOCOL_VERSION


class StoriEvent(CamelModel):
    """Base class for all SSE events.

    ``seq`` and ``protocol_version`` are injected by the emitter, not
    by event constructors.  Subclasses only set ``type`` and their
    domain-specific fields.
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    seq: int = -1
    protocol_version: str = STORI_PROTOCOL_VERSION


# ═══════════════════════════════════════════════════════════════════════
# Universal events (all modes)
# ═══════════════════════════════════════════════════════════════════════


class StateEvent(StoriEvent):
    """Intent classification result. Always seq=0."""

    type: Literal["state"] = "state"
    state: Literal["reasoning", "editing", "composing"]
    intent: str
    confidence: float = Field(ge=0.0, le=1.0)
    trace_id: str
    execution_mode: Literal["variation", "apply", "reasoning"] = "apply"


class ReasoningEvent(StoriEvent):
    """Sanitized analysis summary for the user.

    Carries user-safe musical reasoning produced by ReasoningBuffer +
    sanitize_reasoning().  NOT raw chain-of-thought or internal LLM
    traces — those are stripped before emission.
    """

    type: Literal["reasoning"] = "reasoning"
    content: str
    agent_id: Optional[str] = None
    section_name: Optional[str] = None


class ReasoningEndEvent(StoriEvent):
    """Marks end of a reasoning stream for an agent."""

    type: Literal["reasoningEnd"] = "reasoningEnd"
    agent_id: str
    section_name: Optional[str] = None


class ContentEvent(StoriEvent):
    """User-facing text response (incremental)."""

    type: Literal["content"] = "content"
    content: str


class StatusEvent(StoriEvent):
    """Human-readable status message."""

    type: Literal["status"] = "status"
    message: str
    agent_id: Optional[str] = None
    section_name: Optional[str] = None


class ErrorEvent(StoriEvent):
    """Error message (may be followed by CompleteEvent)."""

    type: Literal["error"] = "error"
    message: str
    trace_id: Optional[str] = None
    code: Optional[str] = None


class CompleteEvent(StoriEvent):
    """Stream termination. ALWAYS the final event."""

    type: Literal["complete"] = "complete"
    success: bool
    trace_id: str
    input_tokens: int = 0
    context_window_tokens: int = 0

    # EDITING mode
    tool_calls: Optional[list[dict[str, Any]]] = None
    state_version: Optional[int] = None

    # COMPOSING mode
    variation_id: Optional[str] = None
    phrase_count: Optional[int] = None
    total_changes: Optional[int] = None

    # Error info
    error: Optional[str] = None
    warnings: Optional[list[str]] = None


# ═══════════════════════════════════════════════════════════════════════
# Plan events
# ═══════════════════════════════════════════════════════════════════════


class PlanStepSchema(CamelModel):
    """One step in a plan event."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    label: str
    status: Literal["pending", "active", "completed", "failed", "skipped"] = "pending"
    tool_name: Optional[str] = None
    detail: Optional[str] = None
    parallel_group: Optional[str] = None


class PlanEvent(StoriEvent):
    """Structured execution plan."""

    type: Literal["plan"] = "plan"
    plan_id: str
    title: str
    steps: list[PlanStepSchema]


class PlanStepUpdateEvent(StoriEvent):
    """Step lifecycle transition."""

    type: Literal["planStepUpdate"] = "planStepUpdate"
    step_id: str
    status: Literal["active", "completed", "failed", "skipped"]
    phase: Optional[str] = None
    result: Optional[str] = None
    agent_id: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════
# Tool events
# ═══════════════════════════════════════════════════════════════════════


class ToolStartEvent(StoriEvent):
    """Fires before tool execution begins."""

    type: Literal["toolStart"] = "toolStart"
    name: str
    label: str
    phase: Optional[str] = None
    agent_id: Optional[str] = None
    section_name: Optional[str] = None


class ToolCallEvent(StoriEvent):
    """Resolved tool call — FE applies this to DAW state."""

    type: Literal["toolCall"] = "toolCall"
    id: str
    name: str
    params: dict[str, Any]
    label: Optional[str] = None
    phase: Optional[str] = None
    proposal: Optional[bool] = None
    agent_id: Optional[str] = None
    section_name: Optional[str] = None


class ToolErrorEvent(StoriEvent):
    """Non-fatal tool validation or execution error."""

    type: Literal["toolError"] = "toolError"
    name: str
    error: str
    errors: Optional[list[str]] = None
    agent_id: Optional[str] = None
    section_name: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════
# Agent Teams events
# ═══════════════════════════════════════════════════════════════════════


class PreflightEvent(StoriEvent):
    """Pre-allocation hint for latency masking."""

    type: Literal["preflight"] = "preflight"
    step_id: str
    agent_id: str
    agent_role: str
    label: str
    tool_name: str
    parallel_group: Optional[str] = None
    confidence: float = 0.9
    track_color: Optional[str] = None


class GeneratorStartEvent(StoriEvent):
    """Orpheus generation started."""

    type: Literal["generatorStart"] = "generatorStart"
    role: str
    agent_id: str
    style: str
    bars: int
    start_beat: float
    label: str
    section_name: Optional[str] = None


class GeneratorCompleteEvent(StoriEvent):
    """Orpheus generation finished."""

    type: Literal["generatorComplete"] = "generatorComplete"
    role: str
    agent_id: str
    note_count: int
    duration_ms: int
    section_name: Optional[str] = None


class AgentCompleteEvent(StoriEvent):
    """Instrument agent finished all sections."""

    type: Literal["agentComplete"] = "agentComplete"
    agent_id: str
    success: bool


# ═══════════════════════════════════════════════════════════════════════
# Summary events
# ═══════════════════════════════════════════════════════════════════════


class SummaryEvent(StoriEvent):
    """Composition summary (tracks, regions, notes, effects)."""

    type: Literal["summary"] = "summary"
    tracks: list[str]
    regions: int
    notes: int
    effects: int


class SummaryFinalEvent(StoriEvent):
    """Rich composition summary from Agent Teams."""

    type: Literal["summary.final"] = "summary.final"
    trace_id: str
    track_count: int = 0
    tracks_created: list[dict[str, Any]] = Field(default_factory=list)
    tracks_reused: list[dict[str, Any]] = Field(default_factory=list)
    regions_created: int = 0
    notes_generated: int = 0
    effects_added: list[dict[str, Any]] = Field(default_factory=list)
    effect_count: int = 0
    sends_created: int = 0
    cc_envelopes: list[dict[str, Any]] = Field(default_factory=list)
    automation_lanes: int = 0
    text: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════
# Variation (COMPOSING) events
# ═══════════════════════════════════════════════════════════════════════


class NoteChangeSchema(CamelModel):
    """Single note change within a phrase."""

    model_config = ConfigDict(extra="forbid")

    note_id: str
    change_type: Literal["added", "removed", "modified"]
    before: Optional[dict[str, Any]] = None
    after: Optional[dict[str, Any]] = None


class ControllerChangeSchema(CamelModel):
    """Single controller change within a phrase."""

    model_config = ConfigDict(extra="forbid")

    cc_number: int
    change_type: Literal["added", "removed", "modified"]
    before: Optional[dict[str, Any]] = None
    after: Optional[dict[str, Any]] = None


class MetaEvent(StoriEvent):
    """Variation summary (emitted before phrases)."""

    type: Literal["meta"] = "meta"
    variation_id: str
    base_state_id: str
    intent: str
    ai_explanation: Optional[str] = None
    affected_tracks: list[str] = Field(default_factory=list)
    affected_regions: list[str] = Field(default_factory=list)
    note_counts: Optional[dict[str, int]] = None


class PhraseEvent(StoriEvent):
    """One musical phrase in a variation."""

    type: Literal["phrase"] = "phrase"
    phrase_id: str
    track_id: str
    region_id: str
    start_beat: float
    end_beat: float
    label: str
    tags: list[str] = Field(default_factory=list)
    explanation: Optional[str] = None
    note_changes: list[NoteChangeSchema] = Field(default_factory=list)
    controller_changes: list[ControllerChangeSchema] = Field(default_factory=list)


class DoneEvent(StoriEvent):
    """End-of-variation marker."""

    type: Literal["done"] = "done"
    variation_id: str
    phrase_count: int
    status: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════
# Legacy composing events (still emitted by _handle_composing)
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# MCP events
# ═══════════════════════════════════════════════════════════════════════


class MCPMessageEvent(StoriEvent):
    """MCP tool-call message relayed over SSE."""

    type: Literal["mcp.message"] = "mcp.message"
    payload: dict[str, Any] = Field(default_factory=dict)


class MCPPingEvent(StoriEvent):
    """MCP SSE keepalive heartbeat."""

    type: Literal["mcp.ping"] = "mcp.ping"


# ═══════════════════════════════════════════════════════════════════════
# Legacy composing events (still emitted by _handle_composing)
# ═══════════════════════════════════════════════════════════════════════


class PlanSummaryEvent(StoriEvent):
    """Composing-mode plan overview. Superseded by PlanEvent in Agent Teams."""

    type: Literal["planSummary"] = "planSummary"
    total_steps: int
    generations: int
    edits: int


class ProgressEvent(StoriEvent):
    """Composing-mode step progress. Superseded by PlanStepUpdateEvent in Agent Teams."""

    type: Literal["progress"] = "progress"
    current_step: int
    total_steps: int
    message: str
    tool_name: Optional[str] = None
