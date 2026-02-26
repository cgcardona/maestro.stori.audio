"""Typed SSE event emitter and parser — protocol-enforced serialization.

Every SSE event the backend emits passes through ``emit()``.
Two entry points:

  ``emit(MaestroEvent)``  — serialize a typed event object to SSE wire format.
  ``parse_event(dict)``   — deserialize a wire-format dict back into the
                            correct MaestroEvent subclass (inverse of ``emit``).
                            Use in tests and any consumer that needs typed
                            access to received events.

Handlers construct typed MaestroEvent subclasses directly — raw-dict emission
is forbidden.  The type safety is enforced at construction time by Pydantic
model validation, not at serialization time.

The ``seq`` field defaults to -1 (sentinel); the route-layer ``_with_seq()``
wrapper in maestro.py overwrites it with the monotonic stream counter.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

from app.protocol.events import MaestroEvent
from app.protocol.registry import EVENT_REGISTRY

logger = logging.getLogger(__name__)


class ProtocolSerializationError(Exception):
    """Raised when an event dict fails protocol validation.

    Callers (stream generators) must catch this, emit an ErrorEvent +
    CompleteEvent(success=False), and terminate the stream.
    """


def emit(event: MaestroEvent) -> str:
    """Serialize a MaestroEvent to SSE wire format.

    Returns ``data: {json}\\n\\n``.

    Raises TypeError for non-MaestroEvent arguments.
    Raises ValueError for unregistered event types.
    """
    if not isinstance(event, MaestroEvent):
        raise TypeError(
            f"emit() requires a MaestroEvent, got {type(event).__name__}."
        )

    event_type = event.type
    if event_type not in EVENT_REGISTRY:
        raise ValueError(
            f"Unknown event type '{event_type}'. "
            f"Register it in app/protocol/registry.py."
        )

    data = event.model_dump(by_alias=True, exclude_none=True)
    return f"data: {json.dumps(data, separators=(',', ':'), ensure_ascii=False)}\n\n"


def parse_event(data: Mapping[str, object]) -> MaestroEvent:
    """Deserialize a wire-format dict back into the correct MaestroEvent subclass.

    Dispatches through the registry and validates via the Pydantic model,
    returning the concrete subclass (e.g. ``ErrorEvent``, ``StateEvent``)
    rather than the base ``MaestroEvent``.

    The wire format uses camelCase keys (``by_alias=True``).  ``CamelModel``
    has ``populate_by_name=True`` so both snake_case and camelCase keys are
    accepted during validation.

    Raises ``ProtocolSerializationError`` for unknown or malformed events.
    """
    event_type = data.get("type")
    if not isinstance(event_type, str):
        raise ProtocolSerializationError("Event dict missing 'type' field")

    if event_type not in EVENT_REGISTRY:
        raise ProtocolSerializationError(
            f"Unknown event type '{event_type}'. Cannot deserialize."
        )

    model_class = EVENT_REGISTRY[event_type]
    try:
        return model_class.model_validate(data)
    except Exception as exc:
        raise ProtocolSerializationError(
            f"Event '{event_type}' failed deserialization: {exc}"
        ) from exc
