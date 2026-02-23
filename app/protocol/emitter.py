"""Typed SSE event emitter — protocol-enforced serialization.

Every SSE event the backend emits passes through this module.
Two entry points:

  ``emit(StoriEvent)``       — for code that constructs typed event objects.
  ``serialize_event(dict)``  — for handler code that builds dicts; validates
                               through the registry model before serialization.

The ``seq`` field defaults to -1 (sentinel); the route-layer ``_with_seq()``
wrapper in maestro.py overwrites it with the monotonic stream counter.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.protocol.events import StoriEvent
from app.protocol.registry import EVENT_REGISTRY

logger = logging.getLogger(__name__)


class ProtocolSerializationError(Exception):
    """Raised when an event dict fails protocol validation.

    Callers (stream generators) must catch this, emit an ErrorEvent +
    CompleteEvent(success=False), and terminate the stream.
    """


def emit(event: StoriEvent) -> str:
    """Serialize a StoriEvent to SSE wire format.

    Returns ``data: {json}\\n\\n``.

    Raises TypeError for non-StoriEvent arguments.
    Raises ValueError for unregistered event types.
    """
    if not isinstance(event, StoriEvent):
        raise TypeError(
            f"emit() requires a StoriEvent, got {type(event).__name__}."
        )

    event_type = event.type
    if event_type not in EVENT_REGISTRY:
        raise ValueError(
            f"Unknown event type '{event_type}'. "
            f"Register it in app/protocol/registry.py."
        )

    data = event.model_dump(by_alias=True, exclude_none=True)
    return f"data: {json.dumps(data, separators=(',', ':'), ensure_ascii=False)}\n\n"


def serialize_event(data: dict[str, Any]) -> str:
    """Validate a handler dict against its registered model and serialize.

    This is the single serialization path for all SSE events.  Handler
    code builds plain dicts; this function enforces the protocol contract
    by validating through the Pydantic model before serialization.

    If validation fails, raises ``ProtocolSerializationError``.
    Raw dict emission is forbidden — there is no production fallback.
    """
    event_type = data.get("type")
    if event_type is None:
        raise ProtocolSerializationError("Event dict missing 'type' field")

    if event_type not in EVENT_REGISTRY:
        raise ProtocolSerializationError(
            f"Unregistered event type '{event_type}'. "
            f"Register it in app/protocol/registry.py."
        )

    model_class = EVENT_REGISTRY[event_type]
    try:
        event = model_class.model_validate(data)
    except Exception as exc:
        raise ProtocolSerializationError(
            f"Event '{event_type}' failed protocol validation: {exc}"
        ) from exc

    validated = event.model_dump(by_alias=True, exclude_none=True)
    return f"data: {json.dumps(validated, separators=(',', ':'), ensure_ascii=False)}\n\n"
