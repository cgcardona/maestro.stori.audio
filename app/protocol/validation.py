"""Runtime protocol guardrails.

Stateful checks that handler code can call before emitting events.
In development/test, violations log at ERROR level. In production,
violations log at WARNING level. The guard never crashes the stream.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.protocol.registry import EVENT_REGISTRY

logger = logging.getLogger(__name__)


class ProtocolGuard:
    """Stateful guard that tracks event ordering invariants per stream."""

    def __init__(self) -> None:
        self._event_count = 0
        self._has_state = False
        self._has_complete = False
        self._seen_types: list[str] = []

    def check_event(self, event_type: str, data: dict[str, Any]) -> list[str]:
        """Validate an event before emission. Returns list of violations (empty = ok)."""
        violations: list[str] = []

        if event_type not in EVENT_REGISTRY:
            violations.append(f"Unregistered event type: '{event_type}'")

        if self._event_count == 0 and event_type != "state":
            violations.append(
                f"First event must be 'state', got '{event_type}'"
            )

        if self._has_complete:
            violations.append(
                f"Event '{event_type}' emitted after 'complete' (terminal violation)"
            )

        if event_type == "complete" and "success" not in data:
            violations.append("'complete' event missing 'success' field")

        self._event_count += 1
        self._seen_types.append(event_type)
        if event_type == "state":
            self._has_state = True
        if event_type == "complete":
            self._has_complete = True

        for v in violations:
            if settings.debug:
                logger.error(f"❌ Protocol violation: {v}")
            else:
                logger.warning(f"⚠️ Protocol violation: {v}")

        return violations

    @property
    def terminated(self) -> bool:
        """``True`` after a ``complete`` event has been emitted for this stream.

        Any event emitted after ``terminated`` is ``True`` is a protocol
        violation — the guard logs it and adds it to the returned violations list.
        """
        return self._has_complete
