"""
Stream Router — single publish entry point for variation events.

All variation events flow through publish_event(). Internally routes to:
  - SSE broadcaster (v1)
  - WebSocket broadcaster (future — stub ready)

This prevents transport-specific code from leaking into the variation
service or API layer. The service calls publish_event(); transports
are an implementation detail.
"""

from __future__ import annotations

import logging
from maestro.variation.core.event_envelope import AnyEnvelope
from maestro.variation.streaming.sse_broadcaster import get_sse_broadcaster

logger = logging.getLogger(__name__)


async def publish_event(envelope: AnyEnvelope) -> int:
    """
    Publish a variation event to all transports.

    This is the ONLY way variation events should be emitted.
    Returns total number of subscribers that received the event.
    """
    total_delivered = 0

    # SSE transport (v1)
    sse = get_sse_broadcaster()
    delivered = await sse.publish(envelope)
    total_delivered += delivered

    # WebSocket transport (future — hook here)
    # ws = get_ws_broadcaster()
    # delivered = await ws.publish(envelope)
    # total_delivered += delivered

    return total_delivered


async def close_variation_stream(variation_id: str) -> None:
    """
    Signal end-of-stream on all transports for a variation.

    Called after terminal done event is published.
    """
    sse = get_sse_broadcaster()
    await sse.close_stream(variation_id)

    # WebSocket transport (future)
    # ws = get_ws_broadcaster()
    # await ws.close_stream(variation_id)
