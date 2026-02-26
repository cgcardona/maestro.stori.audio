"""
SSE Broadcaster for Muse/Variations.

Manages per-variation subscriber lists and broadcasts EventEnvelopes
to connected SSE clients. Supports late-join replay from stored events.

Architecture:
    VariationService → publish_event(envelope) → SSEBroadcaster → clients

The broadcaster is transport-specific (SSE). A future WebSocket
broadcaster will consume the same EventEnvelope objects.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from app.variation.core.event_envelope import AnyEnvelope, EventEnvelope

logger = logging.getLogger(__name__)


class SSEBroadcaster:
    """
    Manages SSE subscriptions for variation event streams.

    Each variation has a list of subscriber queues. When an event is
    published, it's pushed to all subscriber queues.
    """

    def __init__(self) -> None:
        # variation_id -> list of subscriber queues
        self._subscribers: dict[str, list[asyncio.Queue[AnyEnvelope | None]]] = {}
        # variation_id -> list of past envelopes (for replay)
        self._history: dict[str, list[AnyEnvelope]] = {}

    async def publish(self, envelope: AnyEnvelope) -> int:
        """
        Publish an event to all subscribers of a variation.

        Also stores the event for late-join replay.
        Returns the number of subscribers that received the event.
        """
        vid = envelope.variation_id

        # Store for replay
        if vid not in self._history:
            self._history[vid] = []
        self._history[vid].append(envelope)

        # Broadcast to subscribers
        subscribers = self._subscribers.get(vid, [])
        delivered = 0
        for queue in subscribers:
            try:
                queue.put_nowait(envelope)
                delivered += 1
            except asyncio.QueueFull:
                logger.warning(
                    f"SSE queue full for variation {vid[:8]}, dropping event"
                )

        logger.debug(
            f"Published {envelope.type} seq={envelope.sequence} "
            f"to {delivered}/{len(subscribers)} subscribers for {vid[:8]}"
        )
        return delivered

    def subscribe(
        self,
        variation_id: str,
        from_sequence: int = 0,
    ) -> asyncio.Queue[AnyEnvelope | None]:
        """
        Subscribe to events for a variation.

        Returns a queue that will receive AnyEnvelope objects.
        A None sentinel signals end-of-stream.

        If from_sequence > 0, queues replay events starting after that sequence.
        """
        queue: asyncio.Queue[AnyEnvelope | None] = asyncio.Queue(maxsize=256)

        if variation_id not in self._subscribers:
            self._subscribers[variation_id] = []
        self._subscribers[variation_id].append(queue)

        # Replay past events if requested
        history = self._history.get(variation_id, [])
        for envelope in history:
            if envelope.sequence > from_sequence:
                try:
                    queue.put_nowait(envelope)
                except asyncio.QueueFull:
                    logger.warning(
                        f"SSE replay queue full for {variation_id[:8]}"
                    )
                    break

        logger.debug(
            f"New SSE subscriber for {variation_id[:8]} "
            f"(from_seq={from_sequence}, replayed={len(history)})"
        )
        return queue

    def unsubscribe(
        self,
        variation_id: str,
        queue: asyncio.Queue[AnyEnvelope | None],
    ) -> None:
        """Remove a subscriber queue."""
        subscribers = self._subscribers.get(variation_id, [])
        if queue in subscribers:
            subscribers.remove(queue)

        # Clean up empty subscriber lists
        if not subscribers and variation_id in self._subscribers:
            del self._subscribers[variation_id]

    async def close_stream(self, variation_id: str) -> None:
        """
        Signal end-of-stream to all subscribers of a variation.

        Sends None sentinel to each queue, then removes all subscribers.
        """
        subscribers = self._subscribers.get(variation_id, [])
        for queue in subscribers:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

        if variation_id in self._subscribers:
            del self._subscribers[variation_id]

    def get_history(
        self,
        variation_id: str,
        from_sequence: int = 0,
    ) -> list[AnyEnvelope]:
        """Get stored events for a variation, optionally from a sequence."""
        history = self._history.get(variation_id, [])
        if from_sequence > 0:
            return [e for e in history if e.sequence > from_sequence]
        return list(history)

    def cleanup(self, variation_id: str) -> None:
        """Remove all data for a variation (after terminal state)."""
        self._subscribers.pop(variation_id, None)
        self._history.pop(variation_id, None)

    def clear(self) -> None:
        """Clear all state (for testing)."""
        self._subscribers.clear()
        self._history.clear()

    @property
    def active_streams(self) -> int:
        """Number of variations with active subscribers."""
        return sum(1 for subs in self._subscribers.values() if subs)


# Singleton instance
_broadcaster: SSEBroadcaster | None = None


def get_sse_broadcaster() -> SSEBroadcaster:
    """Get the singleton SSEBroadcaster instance."""
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = SSEBroadcaster()
    return _broadcaster


def reset_sse_broadcaster() -> None:
    """Reset the singleton (for testing)."""
    global _broadcaster
    if _broadcaster is not None:
        _broadcaster.clear()
    _broadcaster = None
