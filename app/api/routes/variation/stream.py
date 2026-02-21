"""GET /variation/stream — real SSE stream with envelopes + replay."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse

from app.auth.dependencies import require_valid_token
from app.variation.core.state_machine import is_terminal
from app.variation.storage.variation_store import get_variation_store
from app.variation.streaming.sse_broadcaster import get_sse_broadcaster
from app.api.routes.variation._state import _sse_headers

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/variation/stream")
async def stream_variation(
    variation_id: str,
    from_sequence: int = Query(default=0, ge=0, description="Resume from sequence"),
    token_claims: dict = Depends(require_valid_token),
):
    """
    Stream variation events via SSE with transport-agnostic envelopes.

    Emits EventEnvelope objects as SSE events:
      event: meta|phrase|done|error
      data: {type, sequence, variation_id, project_id, base_state_id, payload, timestamp_ms}

    Supports late-join replay via ?from_sequence=N.
    """
    vstore = get_variation_store()
    record = vstore.get(variation_id)
    if record is None:
        raise HTTPException(status_code=404, detail={
            "error": "Variation not found",
            "variationId": variation_id,
        })

    if is_terminal(record.status):
        async def replay_stream():
            broadcaster = get_sse_broadcaster()
            for envelope in broadcaster.get_history(variation_id, from_sequence):
                yield envelope.to_sse()

        return StreamingResponse(
            replay_stream(),
            media_type="text/event-stream",
            headers=_sse_headers(),
        )

    broadcaster = get_sse_broadcaster()
    queue = broadcaster.subscribe(variation_id, from_sequence=from_sequence)

    async def live_stream():
        try:
            while True:
                try:
                    envelope = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield f"event: heartbeat\ndata: {{}}\n\n"
                    continue

                if envelope is None:
                    break
                yield envelope.to_sse()

                if envelope.type == "done":
                    break
        finally:
            broadcaster.unsubscribe(variation_id, queue)

    return StreamingResponse(
        live_stream(),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )
