"""Maestro Protocol event models — single source of truth for SSE wire format.

Every SSE event the backend emits is an instance of a MaestroEvent subclass.
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

from typing import Literal

from pydantic import ConfigDict, Field

from app.contracts.json_types import (
    AftertouchDict,
    CCEnvelopeDict,
    CCEventDict,
    EffectSummaryDict,
    JSONValue,
    NoteChangeDict,
    PitchBendDict,
    ToolCallDict,
    TrackSummaryDict,
)
from app.contracts.pydantic_types import PydanticJson
from app.models.base import CamelModel
from app.protocol.version import MAESTRO_VERSION


class ToolCallWire(CamelModel):
    """Pydantic-safe wire shape for a tool call in SSE events.

    Used in ``CompleteEvent.tool_calls`` and as the ``params`` carrier for
    ``ToolCallEvent`` — wherever a tool call must cross the Pydantic model
    boundary (SSE serialization, API responses).

    **Why not ``ToolCallDict``?**  ``ToolCallDict.params`` is
    ``dict[str, JSONValue]``, a recursive type alias.  Pydantic v2 cannot
    generate a finite JSON Schema for recursive aliases — it raises
    ``RecursionError`` at model class definition time.  ``ToolCallWire``
    replaces ``params`` with ``dict[str, PydanticJson]``, which Pydantic
    resolves correctly via ``PydanticJson.model_rebuild()``.

    **Conversion from internal code:**  All producers (editing handler,
    composing coordinator, agent teams) hold ``list[ToolCallDict]`` internally.
    Convert at the SSE emit boundary using ``from_tool_call_dict()``::

        tool_calls=[ToolCallWire.from_tool_call_dict(tc) for tc in collected]

    **Serialization:** ``CamelModel`` serializes ``tool`` and ``params`` as
    camelCase via ``by_alias=True``.  The ``params`` values round-trip through
    ``PydanticJson`` transparently — the SSE client receives standard JSON.
    """

    tool: str
    params: dict[str, PydanticJson] = Field(default_factory=dict)

    @classmethod
    def from_tool_call_dict(cls, tc: ToolCallDict) -> ToolCallWire:
        """Convert an internal ``ToolCallDict`` to a Pydantic-safe wire model.

        Wraps ``tc["params"]`` (``dict[str, JSONValue]``) into
        ``dict[str, PydanticJson]`` using ``wrap_dict()``.  This is the single
        conversion point for crossing the internal→Pydantic boundary for tool
        call params.
        """
        from app.contracts.pydantic_types import wrap_dict
        return cls(tool=tc["tool"], params=wrap_dict(tc["params"]))


class MaestroEvent(CamelModel):
    """Base class for all SSE events.

    ``seq`` and ``protocol_version`` are injected by the emitter, not
    by event constructors.  Subclasses only set ``type`` and their
    domain-specific fields.
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    seq: int = -1
    protocol_version: str = MAESTRO_VERSION


# ═══════════════════════════════════════════════════════════════════════
# Universal events (all modes)
# ═══════════════════════════════════════════════════════════════════════


class StateEvent(MaestroEvent):
    """Intent classification result. Always seq=0."""

    type: Literal["state"] = "state"
    state: Literal["reasoning", "editing", "composing"]
    intent: str
    confidence: float = Field(ge=0.0, le=1.0)
    trace_id: str
    execution_mode: Literal["variation", "apply", "reasoning"] = "apply"


class ReasoningEvent(MaestroEvent):
    """Sanitized analysis summary for the user.

    Carries user-safe musical reasoning produced by ReasoningBuffer +
    sanitize_reasoning().  NOT raw chain-of-thought or internal LLM
    traces — those are stripped before emission.
    """

    type: Literal["reasoning"] = "reasoning"
    content: str
    agent_id: str | None = None
    section_name: str | None = None


class ReasoningEndEvent(MaestroEvent):
    """Marks end of a reasoning stream for an agent."""

    type: Literal["reasoningEnd"] = "reasoningEnd"
    agent_id: str
    section_name: str | None = None


class ContentEvent(MaestroEvent):
    """User-facing text response (incremental)."""

    type: Literal["content"] = "content"
    content: str


class StatusEvent(MaestroEvent):
    """Human-readable status message."""

    type: Literal["status"] = "status"
    message: str
    agent_id: str | None = None
    section_name: str | None = None


class ErrorEvent(MaestroEvent):
    """Error message (may be followed by CompleteEvent)."""

    type: Literal["error"] = "error"
    message: str
    trace_id: str | None = None
    code: str | None = None


class CompleteEvent(MaestroEvent):
    """Stream termination. ALWAYS the final event."""

    type: Literal["complete"] = "complete"
    success: bool
    trace_id: str
    input_tokens: int = 0
    context_window_tokens: int = 0

    # EDITING mode
    tool_calls: list[ToolCallWire] | None = None
    state_version: int | None = None

    # COMPOSING mode
    variation_id: str | None = None
    phrase_count: int | None = None
    total_changes: int | None = None

    # Error info
    error: str | None = None
    warnings: list[str] | None = None


# ═══════════════════════════════════════════════════════════════════════
# Plan events
# ═══════════════════════════════════════════════════════════════════════


class PlanStepSchema(CamelModel):
    """One step in a plan event."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    label: str
    status: Literal["pending", "active", "completed", "failed", "skipped"] = "pending"
    tool_name: str | None = None
    detail: str | None = None
    parallel_group: str | None = None
    phase: str = "composition"


class PlanEvent(MaestroEvent):
    """Structured execution plan."""

    type: Literal["plan"] = "plan"
    plan_id: str
    title: str
    steps: list[PlanStepSchema]


class PlanStepUpdateEvent(MaestroEvent):
    """Step lifecycle transition."""

    type: Literal["planStepUpdate"] = "planStepUpdate"
    step_id: str
    status: Literal["active", "completed", "failed", "skipped"]
    phase: str = "composition"
    result: str | None = None
    agent_id: str | None = None


# ═══════════════════════════════════════════════════════════════════════
# Tool events
# ═══════════════════════════════════════════════════════════════════════


class ToolStartEvent(MaestroEvent):
    """Fires before tool execution begins."""

    type: Literal["toolStart"] = "toolStart"
    name: str
    label: str
    phase: str = "composition"
    agent_id: str | None = None
    section_name: str | None = None


class ToolCallEvent(MaestroEvent):
    """Resolved tool call — FE applies this to DAW state."""

    type: Literal["toolCall"] = "toolCall"
    id: str
    name: str
    params: dict[str, PydanticJson]
    label: str | None = None
    phase: str = "composition"
    proposal: bool | None = None
    agent_id: str | None = None
    section_name: str | None = None


class ToolErrorEvent(MaestroEvent):
    """Non-fatal tool validation or execution error."""

    type: Literal["toolError"] = "toolError"
    name: str
    error: str
    errors: list[str] | None = None
    agent_id: str | None = None
    section_name: str | None = None


# ═══════════════════════════════════════════════════════════════════════
# Agent Teams events
# ═══════════════════════════════════════════════════════════════════════


class PreflightEvent(MaestroEvent):
    """Pre-allocation hint for latency masking."""

    type: Literal["preflight"] = "preflight"
    step_id: str
    agent_id: str
    agent_role: str
    label: str
    tool_name: str | None = None
    parallel_group: str | None = None
    confidence: float = 0.9
    track_color: str | None = None


class GeneratorStartEvent(MaestroEvent):
    """Orpheus generation started."""

    type: Literal["generatorStart"] = "generatorStart"
    role: str
    agent_id: str
    style: str
    bars: int
    start_beat: float
    label: str
    section_name: str | None = None


class GeneratorCompleteEvent(MaestroEvent):
    """Orpheus generation finished."""

    type: Literal["generatorComplete"] = "generatorComplete"
    role: str
    agent_id: str
    note_count: int
    duration_ms: int
    section_name: str | None = None


class AgentCompleteEvent(MaestroEvent):
    """Instrument agent finished all sections."""

    type: Literal["agentComplete"] = "agentComplete"
    agent_id: str
    success: bool


# ═══════════════════════════════════════════════════════════════════════
# Summary events
# ═══════════════════════════════════════════════════════════════════════


class SummaryEvent(MaestroEvent):
    """Composition summary (tracks, regions, notes, effects)."""

    type: Literal["summary"] = "summary"
    tracks: list[str]
    regions: int
    notes: int
    effects: int


class SummaryFinalEvent(MaestroEvent):
    """Rich composition summary from Agent Teams."""

    type: Literal["summary.final"] = "summary.final"
    trace_id: str
    track_count: int = 0
    tracks_created: list[TrackSummaryDict] = Field(default_factory=list)
    tracks_reused: list[TrackSummaryDict] = Field(default_factory=list)
    regions_created: int = 0
    notes_generated: int = 0
    effects_added: list[EffectSummaryDict] = Field(default_factory=list)
    effect_count: int = 0
    sends_created: int = 0
    cc_envelopes: list[CCEnvelopeDict] = Field(default_factory=list)
    automation_lanes: int = 0
    text: str | None = None


# ═══════════════════════════════════════════════════════════════════════
# Variation (COMPOSING) events
# ═══════════════════════════════════════════════════════════════════════


class NoteChangeSchema(CamelModel):
    """Single note change within a phrase."""

    model_config = ConfigDict(extra="forbid")

    note_id: str
    change_type: Literal["added", "removed", "modified"]
    before: NoteChangeDict | None = None
    after: NoteChangeDict | None = None


class MetaEvent(MaestroEvent):
    """Variation summary (emitted before phrases)."""

    type: Literal["meta"] = "meta"
    variation_id: str
    base_state_id: str
    intent: str
    ai_explanation: str | None = None
    affected_tracks: list[str] = Field(default_factory=list)
    affected_regions: list[str] = Field(default_factory=list)
    note_counts: dict[str, int] | None = None


class PhraseEvent(MaestroEvent):
    """One musical phrase in a variation."""

    type: Literal["phrase"] = "phrase"
    phrase_id: str
    track_id: str
    region_id: str
    start_beat: float
    end_beat: float
    label: str
    tags: list[str] = Field(default_factory=list)
    explanation: str | None = None
    note_changes: list[NoteChangeSchema] = Field(default_factory=list)
    cc_events: list[CCEventDict] = Field(default_factory=list)
    pitch_bends: list[PitchBendDict] = Field(default_factory=list)
    aftertouch: list[AftertouchDict] = Field(default_factory=list)


class DoneEvent(MaestroEvent):
    """End-of-variation marker."""

    type: Literal["done"] = "done"
    variation_id: str
    phrase_count: int
    status: str | None = None


# ═══════════════════════════════════════════════════════════════════════
# Legacy composing events (still emitted by _handle_composing)
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# MCP events
# ═══════════════════════════════════════════════════════════════════════


class MCPMessageEvent(MaestroEvent):
    """MCP tool-call message relayed over SSE."""

    type: Literal["mcp.message"] = "mcp.message"
    payload: dict[str, object] = Field(default_factory=dict)


class MCPPingEvent(MaestroEvent):
    """MCP SSE keepalive heartbeat."""

    type: Literal["mcp.ping"] = "mcp.ping"


