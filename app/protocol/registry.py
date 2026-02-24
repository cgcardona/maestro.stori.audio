"""Event registry — canonical mapping of event type strings to model classes.

Invariants:
  - Every event the backend can emit has an entry.
  - Unknown event types cannot be emitted (emitter rejects them).
  - Registry is frozen at import time. No runtime mutation.
"""

from __future__ import annotations

from typing import Type

from app.protocol.events import (
    StoriEvent,
    StateEvent,
    ReasoningEvent,
    ReasoningEndEvent,
    ContentEvent,
    StatusEvent,
    ErrorEvent,
    CompleteEvent,
    PlanEvent,
    PlanStepUpdateEvent,
    ToolStartEvent,
    ToolCallEvent,
    ToolErrorEvent,
    PreflightEvent,
    GeneratorStartEvent,
    GeneratorCompleteEvent,
    AgentCompleteEvent,
    SummaryEvent,
    SummaryFinalEvent,
    MetaEvent,
    PhraseEvent,
    DoneEvent,
    MCPMessageEvent,
    MCPPingEvent,
)

EVENT_REGISTRY: dict[str, Type[StoriEvent]] = {
    "state": StateEvent,
    "reasoning": ReasoningEvent,
    "reasoningEnd": ReasoningEndEvent,
    "content": ContentEvent,
    "status": StatusEvent,
    "error": ErrorEvent,
    "complete": CompleteEvent,
    "plan": PlanEvent,
    "planStepUpdate": PlanStepUpdateEvent,
    "toolStart": ToolStartEvent,
    "toolCall": ToolCallEvent,
    "toolError": ToolErrorEvent,
    "preflight": PreflightEvent,
    "generatorStart": GeneratorStartEvent,
    "generatorComplete": GeneratorCompleteEvent,
    "agentComplete": AgentCompleteEvent,
    "summary": SummaryEvent,
    "summary.final": SummaryFinalEvent,
    "meta": MetaEvent,
    "phrase": PhraseEvent,
    "done": DoneEvent,
    "mcp.message": MCPMessageEvent,
    "mcp.ping": MCPPingEvent,
}

ALL_EVENT_TYPES: frozenset[str] = frozenset(EVENT_REGISTRY.keys())


def get_event_class(event_type: str) -> Type[StoriEvent]:
    """Look up the model class for an event type. Raises KeyError for unknown types."""
    return EVENT_REGISTRY[event_type]


def is_known_event(event_type: str) -> bool:
    return event_type in EVENT_REGISTRY
