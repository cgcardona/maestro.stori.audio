"""GET /variation/stream — SSE stream using Wire Protocol events."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse

from app.auth.dependencies import require_valid_token
from app.auth.tokens import TokenClaims
from app.protocol.emitter import ProtocolSerializationError, emit, parse_event
from app.protocol.events import ErrorEvent, MCPPingEvent
from app.protocol.validation import ProtocolGuard
from app.variation.core.event_envelope import AnyEnvelope
from app.variation.core.state_machine import is_terminal
from app.variation.storage.variation_store import get_variation_store
from app.variation.streaming.sse_broadcaster import get_sse_broadcaster
from app.api.routes.variation._state import _sse_headers

router = APIRouter()
logger = logging.getLogger(__name__)


def _envelope_to_protocol_dict(envelope: AnyEnvelope) -> dict[str, object]:
    """Convert an EventEnvelope to a Wire Protocol event dict."""
    etype = envelope.type
    payload = envelope.payload

    if etype == "meta":
        return {
            "type": "meta",
            "variationId": envelope.variation_id,
            "baseStateId": envelope.base_state_id,
            "intent": payload.get("intent", ""),
            "aiExplanation": payload.get("aiExplanation"),
            "affectedTracks": payload.get("affectedTracks", []),
            "affectedRegions": payload.get("affectedRegions", []),
            "noteCounts": payload.get("noteCounts"),
        }
    elif etype == "phrase":
        return {
            "type": "phrase",
            "phraseId": payload.get("phraseId", payload.get("phrase_id", f"p-{envelope.sequence}")),
            "trackId": payload.get("trackId", payload.get("track_id", "")),
            "regionId": payload.get("regionId", payload.get("region_id", "")),
            "startBeat": payload.get("startBeat", payload.get("start_beat", 0)),
            "endBeat": payload.get("endBeat", payload.get("end_beat", 0)),
            "label": payload.get("label", ""),
            "tags": payload.get("tags", []),
            "explanation": payload.get("explanation"),
            "noteChanges": payload.get("noteChanges", payload.get("note_changes", [])),
            "ccEvents": payload.get("ccEvents", payload.get("cc_events", [])),
            "pitchBends": payload.get("pitchBends", payload.get("pitch_bends", [])),
            "aftertouch": payload.get("aftertouch", []),
        }
    elif etype == "done":
        return {
            "type": "done",
            "variationId": envelope.variation_id,
            "phraseCount": payload.get("phraseCount", payload.get("phrase_count", 0)),
            "status": payload.get("status"),
        }
    elif etype == "error":
        return {
            "type": "error",
            "message": payload.get("message", "Unknown variation error"),
            "code": payload.get("code"),
        }
    else:
        return {
            "type": "error",
            "message": f"Unknown variation envelope type: {etype}",
        }


@router.get("/variation/stream")
async def stream_variation(
    variation_id: str,
    from_sequence: int = Query(default=0, ge=0, description="Resume from sequence"),
    token_claims: TokenClaims = Depends(require_valid_token),
) -> StreamingResponse:
    """
    Stream variation events via SSE using Wire Protocol.

    Emits typed protocol events (meta, phrase, done, error)
    validated through the protocol emitter.

    Supports late-join replay via ?from_sequence=N.
    """
    vstore = get_variation_store()
    record = vstore.get(variation_id)
    if record is None:
        raise HTTPException(status_code=404, detail={
            "error": "Variation not found",
            "variationId": variation_id,
        })

    guard = ProtocolGuard()

    if is_terminal(record.status):
        async def replay_stream() -> AsyncIterator[str]:
            broadcaster = get_sse_broadcaster()
            for envelope in broadcaster.get_history(variation_id, from_sequence):
                try:
                    event_dict = _envelope_to_protocol_dict(envelope)
                    yield emit(parse_event(event_dict))
                except ProtocolSerializationError as exc:
                    logger.error(f"❌ Variation replay protocol error: {exc}")
                    yield emit(ErrorEvent(message="Protocol serialization failure"))
                    return

        return StreamingResponse(
            replay_stream(),
            media_type="text/event-stream",
            headers=_sse_headers(),
        )

    broadcaster = get_sse_broadcaster()
    queue = broadcaster.subscribe(variation_id, from_sequence=from_sequence)

    async def live_stream() -> AsyncIterator[str]:
        try:
            while True:
                try:
                    envelope = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield emit(MCPPingEvent())
                    continue

                if envelope is None:
                    break

                try:
                    event_dict = _envelope_to_protocol_dict(envelope)
                    yield emit(parse_event(event_dict))
                except ProtocolSerializationError as exc:
                    logger.error(f"❌ Variation stream protocol error: {exc}")
                    yield emit(ErrorEvent(message="Protocol serialization failure"))
                    return

                if envelope.type == "done":
                    break
        finally:
            broadcaster.unsubscribe(variation_id, queue)

    return StreamingResponse(
        live_stream(),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )
