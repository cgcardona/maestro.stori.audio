"""
Transport-Agnostic Event Envelope (v1 Canonical).

All Muse/Variation events are produced through build_envelope(),
then routed to SSE and/or WebSocket broadcasters.

This guarantees frontend and agents receive identical data regardless
of transport.

Envelope fields:
    type          — meta | phrase | done | error | heartbeat
    sequence      — strictly increasing integer (per variation)
    variation_id  — UUID of the variation
    project_id    — UUID of the project
    base_state_id — baseline version at proposal time
    payload       — event-specific JSON

Ordering rules:
    1. meta must be sequence = 1
    2. phrase must be sequence = 2..N
    3. done must be last, unless error occurs
    4. If error occurs: emit error, then done (status=failed) if possible
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)


EventType = Literal["meta", "phrase", "done", "error", "heartbeat"]


@dataclass(frozen=True)
class EventEnvelope:
    """Immutable, transport-agnostic event envelope for all Muse/Variation events.

    All envelopes are produced through ``build_envelope()`` (or the typed
    ``build_*_envelope`` helpers) and consumed identically by the SSE stream
    and WebSocket broadcaster.

    Attributes:
        type: Event type discriminator — one of ``meta``, ``phrase``, ``done``,
            ``error``, or ``heartbeat``.
        sequence: Strictly increasing integer per variation stream.  ``meta``
            is always sequence 1; subsequent events increment from there.
        variation_id: UUID of the variation this event belongs to.
        project_id: UUID of the project (denormalized for client convenience).
        base_state_id: Variation ID of the baseline at proposal time — allows
            the client to detect mid-stream state changes.
        payload: Event-specific JSON body; schema depends on ``type``.
        timestamp_ms: Unix epoch milliseconds at envelope construction time.
    """

    type: EventType
    sequence: int
    variation_id: str
    project_id: str
    base_state_id: str
    payload: dict[str, object]
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain dict for JSON transport (camelCase keys)."""
        return {
            "type": self.type,
            "sequence": self.sequence,
            "variationId": self.variation_id,
            "projectId": self.project_id,
            "baseStateId": self.base_state_id,
            "payload": self.payload,
            "timestampMs": self.timestamp_ms,
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict())

    def to_sse(self) -> str:
        """Format as a Server-Sent Event string."""
        return f"event: {self.type}\ndata: {self.to_json()}\n\n"


class SequenceCounter:
    """
    Monotonic sequence counter for a single variation's event stream.

    Sequence starts at 1 (for meta) and strictly increases.
    Thread-safe is not required — variations are single-writer.
    """

    def __init__(self) -> None:
        self._value: int = 0

    @property
    def current(self) -> int:
        """Current (last-emitted) sequence value; ``0`` before the first ``next()`` call."""
        return self._value

    def next(self) -> int:
        """Get the next sequence number."""
        self._value += 1
        return self._value

    def reset(self) -> None:
        """Reset counter (only for testing)."""
        self._value = 0


def build_envelope(
    event_type: EventType,
    payload: dict[str, object],
    sequence: int,
    variation_id: str,
    project_id: str = "",
    base_state_id: str = "",
) -> EventEnvelope:
    """
    Build a transport-agnostic event envelope.

    This is the single entry point for creating events.
    All broadcasters consume EventEnvelope objects.
    """
    return EventEnvelope(
        type=event_type,
        sequence=sequence,
        variation_id=variation_id,
        project_id=project_id,
        base_state_id=base_state_id,
        payload=payload,
    )


def build_meta_envelope(
    variation_id: str,
    project_id: str,
    base_state_id: str,
    intent: str,
    ai_explanation: str | None,
    affected_tracks: list[str],
    affected_regions: list[str],
    note_counts: dict[str, int],
    sequence: int = 1,
) -> EventEnvelope:
    """Build a meta envelope (always sequence=1)."""
    return build_envelope(
        event_type="meta",
        payload={
            "intent": intent,
            "aiExplanation": ai_explanation,
            "affectedTracks": affected_tracks,
            "affectedRegions": affected_regions,
            "noteCounts": note_counts,
        },
        sequence=sequence,
        variation_id=variation_id,
        project_id=project_id,
        base_state_id=base_state_id,
    )


def build_phrase_envelope(
    variation_id: str,
    project_id: str,
    base_state_id: str,
    sequence: int,
    phrase_data: dict[str, object],
) -> EventEnvelope:
    """Build a phrase envelope."""
    return build_envelope(
        event_type="phrase",
        payload=phrase_data,
        sequence=sequence,
        variation_id=variation_id,
        project_id=project_id,
        base_state_id=base_state_id,
    )


def build_done_envelope(
    variation_id: str,
    project_id: str,
    base_state_id: str,
    sequence: int,
    status: str = "ready",
    phrase_count: int = 0,
) -> EventEnvelope:
    """Build a done envelope (always last in sequence)."""
    return build_envelope(
        event_type="done",
        payload={
            "status": status,
            "phraseCount": phrase_count,
        },
        sequence=sequence,
        variation_id=variation_id,
        project_id=project_id,
        base_state_id=base_state_id,
    )


def build_error_envelope(
    variation_id: str,
    project_id: str,
    base_state_id: str,
    sequence: int,
    error_message: str,
    error_code: str | None = None,
) -> EventEnvelope:
    """Build an error envelope."""
    return build_envelope(
        event_type="error",
        payload={
            "message": error_message,
            "code": error_code,
        },
        sequence=sequence,
        variation_id=variation_id,
        project_id=project_id,
        base_state_id=base_state_id,
    )
