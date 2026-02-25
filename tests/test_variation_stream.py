"""
Tests for the SSE Broadcaster.

Covers event publishing, subscription, replay, late-join,
end-of-stream signaling, and cleanup.
"""
from __future__ import annotations

from collections.abc import Generator
import asyncio
import pytest

from app.variation.core.event_envelope import (
    build_meta_envelope,
    build_phrase_envelope,
    build_done_envelope,
    build_error_envelope,
    EventEnvelope,
)
from app.variation.streaming.sse_broadcaster import (
    SSEBroadcaster,
    get_sse_broadcaster,
    reset_sse_broadcaster,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def broadcaster() -> Generator[SSEBroadcaster, None, None]:
    """Fresh SSE broadcaster for each test."""
    b = SSEBroadcaster()
    yield b
    b.clear()


@pytest.fixture(autouse=True)
def reset_singleton() -> Generator[None, None, None]:
    """Reset singleton between tests."""
    yield
    reset_sse_broadcaster()


def _make_meta(variation_id: str = "v-1", seq: int = 1) -> EventEnvelope:

    """Helper to create a meta envelope."""
    return build_meta_envelope(
        variation_id=variation_id,
        project_id="p-1",
        base_state_id="0",
        intent="test",
        ai_explanation=None,
        affected_tracks=[],
        affected_regions=[],
        note_counts={"added": 0, "removed": 0, "modified": 0},
        sequence=seq,
    )


def _make_phrase(variation_id: str = "v-1", seq: int = 2) -> EventEnvelope:

    """Helper to create a phrase envelope."""
    return build_phrase_envelope(
        variation_id=variation_id,
        project_id="p-1",
        base_state_id="0",
        sequence=seq,
        phrase_data={"phrase_id": f"phrase-{seq}", "note_changes": []},
    )


def _make_done(variation_id: str = "v-1", seq: int = 3) -> EventEnvelope:

    """Helper to create a done envelope."""
    return build_done_envelope(
        variation_id=variation_id,
        project_id="p-1",
        base_state_id="0",
        sequence=seq,
        status="ready",
        phrase_count=1,
    )


# =============================================================================
# Publishing
# =============================================================================


class TestPublishing:
    """Test event publishing."""

    @pytest.mark.asyncio
    async def test_publish_without_subscribers(self, broadcaster: SSEBroadcaster) -> None:

        """Publishing without subscribers still stores in history."""
        envelope = _make_meta()
        delivered = await broadcaster.publish(envelope)

        assert delivered == 0
        assert len(broadcaster.get_history("v-1")) == 1

    @pytest.mark.asyncio
    async def test_publish_to_subscriber(self, broadcaster: SSEBroadcaster) -> None:

        """Publishing delivers to subscriber queue."""
        queue = broadcaster.subscribe("v-1")
        envelope = _make_meta()

        delivered = await broadcaster.publish(envelope)

        assert delivered == 1
        received = queue.get_nowait()
        assert received is not None
        assert received.type == "meta"
        assert received.sequence == 1

    @pytest.mark.asyncio
    async def test_publish_to_multiple_subscribers(self, broadcaster: SSEBroadcaster) -> None:

        """Publishing delivers to all subscribers."""
        q1 = broadcaster.subscribe("v-1")
        q2 = broadcaster.subscribe("v-1")

        envelope = _make_meta()
        delivered = await broadcaster.publish(envelope)

        assert delivered == 2
        r1 = q1.get_nowait()
        r2 = q2.get_nowait()
        assert r1 is not None and r1.type == "meta"
        assert r2 is not None and r2.type == "meta"

    @pytest.mark.asyncio
    async def test_publish_stores_history(self, broadcaster: SSEBroadcaster) -> None:

        """All published events are stored in history."""
        await broadcaster.publish(_make_meta())
        await broadcaster.publish(_make_phrase())
        await broadcaster.publish(_make_done())

        history = broadcaster.get_history("v-1")
        assert len(history) == 3
        assert history[0].type == "meta"
        assert history[1].type == "phrase"
        assert history[2].type == "done"


# =============================================================================
# Subscription and Replay
# =============================================================================


class TestSubscription:
    """Test subscribing and event replay."""

    @pytest.mark.asyncio
    async def test_subscribe_creates_queue(self, broadcaster: SSEBroadcaster) -> None:

        """subscribe() returns a queue."""
        queue = broadcaster.subscribe("v-1")
        assert isinstance(queue, asyncio.Queue)

    @pytest.mark.asyncio
    async def test_late_join_replays_history(self, broadcaster: SSEBroadcaster) -> None:

        """Late-joining subscriber receives all past events."""
        await broadcaster.publish(_make_meta())
        await broadcaster.publish(_make_phrase())

        # Subscribe after events were published
        queue = broadcaster.subscribe("v-1")

        # Should have replayed events
        assert queue.qsize() == 2
        r1 = queue.get_nowait()
        r2 = queue.get_nowait()
        assert r1 is not None and r1.type == "meta"
        assert r2 is not None and r2.type == "phrase"

    @pytest.mark.asyncio
    async def test_late_join_with_from_sequence(self, broadcaster: SSEBroadcaster) -> None:

        """Late-joining with from_sequence only replays newer events."""
        await broadcaster.publish(_make_meta())
        await broadcaster.publish(_make_phrase(seq=2))
        await broadcaster.publish(_make_phrase(seq=3))

        # Subscribe from sequence 1 (skip meta)
        queue = broadcaster.subscribe("v-1", from_sequence=1)

        assert queue.qsize() == 2
        e1 = queue.get_nowait()
        e2 = queue.get_nowait()
        assert e1 is not None and e1.sequence == 2
        assert e2 is not None and e2.sequence == 3

    @pytest.mark.asyncio
    async def test_unsubscribe(self, broadcaster: SSEBroadcaster) -> None:

        """unsubscribe() removes the queue."""
        queue = broadcaster.subscribe("v-1")
        broadcaster.unsubscribe("v-1", queue)

        # Publishing should not deliver to the removed queue
        await broadcaster.publish(_make_meta())
        assert queue.empty()


# =============================================================================
# End-of-Stream
# =============================================================================


class TestEndOfStream:
    """Test stream closing and cleanup."""

    @pytest.mark.asyncio
    async def test_close_stream_sends_sentinel(self, broadcaster: SSEBroadcaster) -> None:

        """close_stream() sends None sentinel to all subscribers."""
        q1 = broadcaster.subscribe("v-1")
        q2 = broadcaster.subscribe("v-1")

        await broadcaster.close_stream("v-1")

        assert q1.get_nowait() is None
        assert q2.get_nowait() is None

    @pytest.mark.asyncio
    async def test_close_stream_removes_subscribers(self, broadcaster: SSEBroadcaster) -> None:

        """close_stream() removes all subscribers for a variation."""
        broadcaster.subscribe("v-1")
        broadcaster.subscribe("v-1")

        await broadcaster.close_stream("v-1")

        # Active streams should be 0
        assert broadcaster.active_streams == 0

    @pytest.mark.asyncio
    async def test_cleanup_removes_all_data(self, broadcaster: SSEBroadcaster) -> None:

        """cleanup() removes both subscribers and history."""
        broadcaster.subscribe("v-1")
        await broadcaster.publish(_make_meta())

        broadcaster.cleanup("v-1")

        assert broadcaster.get_history("v-1") == []
        assert broadcaster.active_streams == 0


# =============================================================================
# History Filtering
# =============================================================================


class TestHistory:
    """Test history retrieval."""

    @pytest.mark.asyncio
    async def test_get_history_full(self, broadcaster: SSEBroadcaster) -> None:

        """get_history() returns all stored events."""
        await broadcaster.publish(_make_meta())
        await broadcaster.publish(_make_phrase())
        await broadcaster.publish(_make_done())

        history = broadcaster.get_history("v-1")
        assert len(history) == 3

    @pytest.mark.asyncio
    async def test_get_history_from_sequence(self, broadcaster: SSEBroadcaster) -> None:

        """get_history(from_sequence) filters correctly."""
        await broadcaster.publish(_make_meta())
        await broadcaster.publish(_make_phrase(seq=2))
        await broadcaster.publish(_make_done(seq=3))

        # Get events after sequence 1
        history = broadcaster.get_history("v-1", from_sequence=1)
        assert len(history) == 2
        assert history[0].sequence == 2
        assert history[1].sequence == 3

    @pytest.mark.asyncio
    async def test_get_history_empty(self, broadcaster: SSEBroadcaster) -> None:

        """get_history() returns empty list for unknown variation."""
        assert broadcaster.get_history("nonexistent") == []


# =============================================================================
# Full Stream Simulation
# =============================================================================


class TestFullStreamSimulation:
    """End-to-end simulation of a variation event stream."""

    @pytest.mark.asyncio
    async def test_complete_stream_lifecycle(self, broadcaster: SSEBroadcaster) -> None:

        """Simulate: subscribe → meta → phrase → phrase → done → close."""
        vid = "v-sim"
        queue = broadcaster.subscribe(vid)

        # Publish full stream
        await broadcaster.publish(build_meta_envelope(
            variation_id=vid, project_id="p", base_state_id="0",
            intent="simulate", ai_explanation=None,
            affected_tracks=["t-1"], affected_regions=["r-1"],
            note_counts={"added": 2, "removed": 0, "modified": 1},
        ))
        await broadcaster.publish(build_phrase_envelope(
            variation_id=vid, project_id="p", base_state_id="0",
            sequence=2, phrase_data={"phrase_id": "p-1"},
        ))
        await broadcaster.publish(build_phrase_envelope(
            variation_id=vid, project_id="p", base_state_id="0",
            sequence=3, phrase_data={"phrase_id": "p-2"},
        ))
        await broadcaster.publish(build_done_envelope(
            variation_id=vid, project_id="p", base_state_id="0",
            sequence=4, status="ready", phrase_count=2,
        ))

        # Close the stream
        await broadcaster.close_stream(vid)

        # Collect all received events
        events = []
        while not queue.empty():
            item = queue.get_nowait()
            if item is None:
                break
            events.append(item)

        # Verify ordering: meta, phrase, phrase, done
        assert len(events) == 4
        assert events[0].type == "meta"
        assert events[0].sequence == 1
        assert events[1].type == "phrase"
        assert events[1].sequence == 2
        assert events[2].type == "phrase"
        assert events[2].sequence == 3
        assert events[3].type == "done"
        assert events[3].sequence == 4
        assert events[3].payload["status"] == "ready"
        assert events[3].payload["phraseCount"] == 2

    @pytest.mark.asyncio
    async def test_error_then_done_stream(self, broadcaster: SSEBroadcaster) -> None:

        """Simulate error flow: meta → error → done(failed)."""
        vid = "v-err"
        queue = broadcaster.subscribe(vid)

        await broadcaster.publish(build_meta_envelope(
            variation_id=vid, project_id="p", base_state_id="0",
            intent="fail", ai_explanation=None,
            affected_tracks=[], affected_regions=[],
            note_counts={"added": 0, "removed": 0, "modified": 0},
        ))
        await broadcaster.publish(build_error_envelope(
            variation_id=vid, project_id="p", base_state_id="0",
            sequence=2, error_message="Generation failed",
        ))
        await broadcaster.publish(build_done_envelope(
            variation_id=vid, project_id="p", base_state_id="0",
            sequence=3, status="failed", phrase_count=0,
        ))

        await broadcaster.close_stream(vid)

        events = []
        while not queue.empty():
            item = queue.get_nowait()
            if item is None:
                break
            events.append(item)

        assert len(events) == 3
        assert events[0].type == "meta"
        assert events[1].type == "error"
        assert events[1].payload["message"] == "Generation failed"
        assert events[2].type == "done"
        assert events[2].payload["status"] == "failed"


# =============================================================================
# Singleton
# =============================================================================


class TestBroadcasterSingleton:
    """Test singleton access."""

    def test_singleton_returns_same(self) -> None:

        """get_sse_broadcaster returns the same instance."""
        b1 = get_sse_broadcaster()
        b2 = get_sse_broadcaster()
        assert b1 is b2

    def test_reset_clears_singleton(self) -> None:

        """reset_sse_broadcaster clears state."""
        b1 = get_sse_broadcaster()
        reset_sse_broadcaster()
        b2 = get_sse_broadcaster()
        assert b1 is not b2
