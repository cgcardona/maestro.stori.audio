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
from typing import Literal, Union

from typing_extensions import TypedDict

from app.contracts.json_types import (
    AftertouchDict,
    CCEventDict,
    NoteChangeDict,
    NoteChangeEntryDict,
    PitchBendDict,
)
from app.models.variation import MidiNoteSnapshot, NoteChange, Phrase

logger = logging.getLogger(__name__)


EventType = Literal["meta", "phrase", "done", "error", "heartbeat"]


# ── Per-event payload shapes ───────────────────────────────────────────────────


class MetaPayload(TypedDict, total=False):
    """Payload for ``type="meta"`` envelopes (always sequence=1).

    Describes which tracks and regions a variation will affect, and carries
    the user's intent and the AI explanation.
    """

    intent: str
    aiExplanation: str | None  # noqa: N815
    affectedTracks: list[str]  # noqa: N815
    affectedRegions: list[str]  # noqa: N815
    noteCounts: dict[str, int]  # noqa: N815


class PhrasePayload(TypedDict, total=False):
    """Payload for ``type="phrase"`` envelopes.

    One generated MIDI phrase.  Both camelCase (wire) and snake_case (internal)
    key forms are accepted — consumers should prefer camelCase.
    """

    phraseId: str  # noqa: N815
    phrase_id: str  # snake_case fallback
    trackId: str  # noqa: N815
    track_id: str  # snake_case fallback
    regionId: str  # noqa: N815
    region_id: str  # snake_case fallback
    startBeat: float  # noqa: N815
    start_beat: float  # snake_case fallback
    endBeat: float  # noqa: N815
    end_beat: float  # snake_case fallback
    label: str
    tags: list[str]
    explanation: str | None
    noteChanges: list[NoteChangeEntryDict]  # noqa: N815
    note_changes: list[NoteChangeEntryDict]  # snake_case fallback
    ccEvents: list[CCEventDict]  # noqa: N815
    cc_events: list[CCEventDict]  # snake_case fallback
    pitchBends: list[PitchBendDict]  # noqa: N815
    pitch_bends: list[PitchBendDict]  # snake_case fallback
    aftertouch: list[AftertouchDict]


class DonePayload(TypedDict, total=False):
    """Payload for ``type="done"`` envelopes (always last in a variation stream)."""

    status: str
    phraseCount: int  # noqa: N815
    phrase_count: int  # snake_case fallback


class ErrorPayload(TypedDict, total=False):
    """Payload for ``type="error"`` envelopes."""

    message: str
    code: str | None


EnvelopePayload = Union[MetaPayload, PhrasePayload, DonePayload, ErrorPayload]
"""Union of all typed envelope payload shapes.

``EventEnvelope.payload`` holds exactly one of these depending on ``EventEnvelope.type``.
Consumers that need structural access should narrow on ``envelope.type`` first.
"""


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
    payload: EnvelopePayload
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
    payload: EnvelopePayload,
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
    meta: MetaPayload = {
        "intent": intent,
        "aiExplanation": ai_explanation,
        "affectedTracks": affected_tracks,
        "affectedRegions": affected_regions,
        "noteCounts": note_counts,
    }
    return build_envelope(
        event_type="meta",
        payload=meta,
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
    phrase_data: PhrasePayload,
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
    done: DonePayload = {
        "status": status,
        "phraseCount": phrase_count,
    }
    return build_envelope(
        event_type="done",
        payload=done,
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
    error: ErrorPayload = {
        "message": error_message,
        "code": error_code,
    }
    return build_envelope(
        event_type="error",
        payload=error,
        sequence=sequence,
        variation_id=variation_id,
        project_id=project_id,
        base_state_id=base_state_id,
    )


# ── Phrase serialization helpers ───────────────────────────────────────────────
#
# These live here (not in propose.py / storage.py) so any module that builds
# a PhrasePayload can share the same serialization logic without creating
# a layer-crossing import.


def _snapshot_to_note_dict(snap: MidiNoteSnapshot) -> NoteChangeDict:
    """Convert a ``MidiNoteSnapshot`` to its camelCase wire ``NoteChangeDict``.

    Uses explicit field access so mypy can verify the output shape rather
    than relying on ``model_dump(by_alias=True)``'s ``dict[str, Any]``.
    """
    return NoteChangeDict(
        pitch=snap.pitch,
        startBeat=snap.start_beat,
        durationBeats=snap.duration_beats,
        velocity=snap.velocity,
        channel=snap.channel,
    )


def note_change_to_wire(nc: NoteChange) -> NoteChangeEntryDict:
    """Serialize a ``NoteChange`` domain model to its ``PhrasePayload`` wire entry.

    This is the single serialization point for note changes — explicit field
    extraction means mypy can verify the result type against
    ``NoteChangeEntryDict`` without relying on ``model_dump``'s ``dict[str, Any]``.
    """
    return NoteChangeEntryDict(
        noteId=nc.note_id,
        changeType=nc.change_type,
        before=_snapshot_to_note_dict(nc.before) if nc.before is not None else None,
        after=_snapshot_to_note_dict(nc.after) if nc.after is not None else None,
    )


def build_phrase_payload(phrase: Phrase) -> PhrasePayload:
    """Build a ``PhrasePayload`` from a ``Phrase`` domain model.

    Single serialization point shared by the SSE streaming path
    (``propose.py``) and the background storage path
    (``maestro_composing/storage.py``).  Both paths call this function so
    the two wire representations are guaranteed to be identical.
    """
    return PhrasePayload(
        phraseId=phrase.phrase_id,
        trackId=phrase.track_id,
        regionId=phrase.region_id,
        startBeat=phrase.start_beat,
        endBeat=phrase.end_beat,
        label=phrase.label,
        tags=phrase.tags,
        explanation=phrase.explanation,
        noteChanges=[note_change_to_wire(nc) for nc in phrase.note_changes],
        ccEvents=list(phrase.cc_events),
        pitchBends=list(phrase.pitch_bends),
        aftertouch=list(phrase.aftertouch),
    )
