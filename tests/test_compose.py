"""Tests for the Cursor-of-DAWs execution layer.

Tests the executor, tool resolution, and UUID generation.
"""
import pytest
import uuid
from unittest.mock import MagicMock

from app.core.executor import execute_plan, ExecutionContext
from app.core.state_store import StateStore, Transaction
from app.core.tracing import TraceContext
from app.core.expansion import ToolCall


def is_valid_uuid(value: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False


class TestExecutionContext:
    """Test ExecutionContext dataclass."""

    def test_empty_context(self):
        """New context should have empty results."""
        store = MagicMock(spec=StateStore)
        transaction = MagicMock(spec=Transaction)
        trace = TraceContext(trace_id="test-trace")
        ctx = ExecutionContext(store=store, transaction=transaction, trace=trace)
        assert ctx.results == []

    def test_add_result(self):
        """Adding a result should append to results."""
        store = MagicMock(spec=StateStore)
        transaction = MagicMock(spec=Transaction)
        trace = TraceContext(trace_id="test-trace")
        ctx = ExecutionContext(store=store, transaction=transaction, trace=trace)
        ctx.add_result("stori_play", success=True, output={"ok": True})
        assert len(ctx.results) == 1
        assert ctx.results[0].tool_name == "stori_play"


class TestExecutePlan:
    """Test execute_plan function."""

    @pytest.mark.anyio
    async def test_simple_passthrough(self):
        """Tier2 tools should pass through to client."""
        calls = [ToolCall("stori_play", {})]
        project_state = {}
        
        events = await execute_plan(calls, project_state)
        
        assert len(events) == 1
        assert events[0]["tool"] == "stori_play"

    @pytest.mark.anyio
    async def test_dedupes_calls(self):
        """Duplicate calls should be deduplicated."""
        calls = [
            ToolCall("stori_play", {}),
            ToolCall("stori_play", {}),
        ]
        project_state = {}
        
        events = await execute_plan(calls, project_state)
        
        assert len(events) == 1

    @pytest.mark.anyio
    async def test_uuid_generation_for_track(self):
        """Track creation should generate trackId if not provided."""
        calls = [ToolCall("stori_add_midi_track", {"name": "Drums"})]
        project_state = {}
        
        events = await execute_plan(calls, project_state)
        
        assert len(events) == 1
        assert "trackId" in events[0]["params"]
        assert is_valid_uuid(events[0]["params"]["trackId"])


class TestTrackNameResolution:
    """Test trackName to trackId resolution."""

    @pytest.mark.anyio
    async def test_resolves_track_name(self):
        """trackName should resolve to trackId from project state."""
        calls = [ToolCall("stori_set_track_volume", {"trackName": "Drums", "volume": 0.8})]
        project_state = {
            "tracks": [
                {"name": "Drums", "id": "abc-123"},
            ]
        }
        
        events = await execute_plan(calls, project_state)
        
        assert len(events) == 1
        assert events[0]["params"]["trackId"] == "abc-123"

    @pytest.mark.anyio
    async def test_case_insensitive_resolution(self):
        """trackName resolution should be case-insensitive."""
        calls = [ToolCall("stori_set_track_volume", {"trackName": "drums", "volume": 0.8})]
        project_state = {
            "tracks": [
                {"name": "Drums", "id": "abc-123"},
            ]
        }
        
        events = await execute_plan(calls, project_state)
        
        assert len(events) == 1
        assert events[0]["params"]["trackId"] == "abc-123"

    @pytest.mark.anyio
    async def test_unresolved_track_name_skips_call(self):
        """Unresolved trackName should skip the call (not crash)."""
        calls = [ToolCall("stori_set_track_volume", {"trackName": "NonExistent", "volume": 0.8})]
        project_state = {"tracks": []}
        
        events = await execute_plan(calls, project_state)
        
        # Call is skipped because trackName couldn't be resolved
        assert len(events) == 0


class TestMultipleCalls:
    """Test execution of multiple tool calls."""

    @pytest.mark.anyio
    async def test_multiple_passthrough(self):
        """Multiple Tier2 calls should all pass through."""
        calls = [
            ToolCall("stori_set_tempo", {"bpm": 120}),
            ToolCall("stori_play", {}),
        ]
        project_state = {}
        
        events = await execute_plan(calls, project_state)
        
        assert len(events) == 2
        assert events[0]["tool"] == "stori_set_tempo"
        assert events[1]["tool"] == "stori_play"

    @pytest.mark.anyio
    async def test_results_accumulate(self):
        """Execution should accumulate all results as events."""
        calls = [
            ToolCall("stori_set_tempo", {"bpm": 120}),
            ToolCall("stori_play", {}),
        ]
        project_state = {}
        
        events = await execute_plan(calls, project_state)
        
        # Both calls should produce events
        assert len(events) >= 2
