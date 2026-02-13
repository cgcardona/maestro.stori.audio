"""
Unit tests for entity ID tracking across conversation turns.

This tests the critical fix for the backend - ensuring that entity IDs
(trackId, regionId, busId) from previous tool calls are properly tracked
and available to the LLM in subsequent turns.
"""
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from app.api.routes.conversations import build_conversation_history_for_llm


def test_build_conversation_history_basic():
    """Test basic conversation history building with user/assistant messages."""
    # Create mock messages
    messages = [
        MagicMock(
            role="user",
            content="Create a drum track",
            tool_calls=None,
        ),
        MagicMock(
            role="assistant",
            content="I'll create a drum track for you.",
            tool_calls=None,
        ),
    ]
    
    history = build_conversation_history_for_llm(messages)
    
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Create a drum track"
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] == "I'll create a drum track for you."


def test_build_conversation_history_with_tool_calls():
    """
    Test conversation history with tool calls - the CRITICAL case for entity ID tracking.
    
    The LLM must see previous tool calls with their parameters (including generated UUIDs)
    so it can reuse those IDs in subsequent tool calls.
    """
    # Create mock messages with tool calls
    messages = [
        # User message
        MagicMock(
            role="user",
            content="Create a drum track",
            tool_calls=None,
        ),
        # Assistant message with tool call (flat storage format)
        MagicMock(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_abc123",
                    "type": "function",
                    "name": "stori_add_midi_track",
                    "arguments": {
                        "name": "Drums",
                        "trackId": "07761e6f-2cca-4ac7-9397-71b3a01953ed"
                    }
                }
            ],
        ),
    ]
    
    history = build_conversation_history_for_llm(messages)
    
    # Should have: user message, assistant message with tool_calls, tool result
    assert len(history) == 3
    
    # User message
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Create a drum track"
    
    # Assistant message with tool calls (converted to OpenAI nested format)
    assert history[1]["role"] == "assistant"
    assert "tool_calls" in history[1]
    assert len(history[1]["tool_calls"]) == 1
    
    tool_call = history[1]["tool_calls"][0]
    assert tool_call["id"] == "call_abc123"
    assert tool_call["type"] == "function"
    assert tool_call["function"]["name"] == "stori_add_midi_track"
    
    # CRITICAL: The trackId must be preserved in the arguments
    import json
    arguments = json.loads(tool_call["function"]["arguments"])
    assert arguments["trackId"] == "07761e6f-2cca-4ac7-9397-71b3a01953ed"
    assert arguments["name"] == "Drums"
    
    # Tool result message
    assert history[2]["role"] == "tool"
    assert history[2]["tool_call_id"] == "call_abc123"
    assert "success" in history[2]["content"]


def test_build_conversation_history_multi_turn_with_entity_reuse():
    """
    Test multi-turn conversation where entity IDs must be tracked and reused.
    
    This simulates the exact scenario from the bug report:
    1. Turn 1: Create track with trackId=X
    2. Turn 2: Add effect using trackId=X (must see X from turn 1)
    """
    track_id = "07761e6f-2cca-4ac7-9397-71b3a01953ed"
    
    messages = [
        # Turn 1: User creates track
        MagicMock(
            role="user",
            content="Create a drum track",
            tool_calls=None,
        ),
        # Turn 1: Assistant creates track with trackId
        MagicMock(
            role="assistant",
            content="",
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "name": "stori_add_midi_track",
                "arguments": {"name": "Drums", "trackId": track_id}
            }],
        ),
        # Turn 2: User asks to add effect
        MagicMock(
            role="user",
            content="Add compressor to drums",
            tool_calls=None,
        ),
        # Turn 2: Assistant adds effect (must use trackId from turn 1)
        MagicMock(
            role="assistant",
            content="",
            tool_calls=[{
                "id": "call_2",
                "type": "function",
                "name": "stori_add_insert_effect",
                "arguments": {"trackId": track_id, "type": "compressor"}
            }],
        ),
    ]
    
    history = build_conversation_history_for_llm(messages)
    
    # Should have: user1, assistant1, tool_result1, user2, assistant2, tool_result2
    assert len(history) == 6
    
    # Verify turn 1 tool call has trackId
    turn1_tool_call = history[1]["tool_calls"][0]
    import json
    turn1_args = json.loads(turn1_tool_call["function"]["arguments"])
    assert turn1_args["trackId"] == track_id
    
    # CRITICAL: Verify turn 2 tool call reuses the SAME trackId
    turn2_tool_call = history[4]["tool_calls"][0]
    turn2_args = json.loads(turn2_tool_call["function"]["arguments"])
    assert turn2_args["trackId"] == track_id  # Same ID!
    assert turn2_args["type"] == "compressor"
    
    # This proves that the LLM has access to the trackId from turn 1
    # and can reuse it in turn 2


def test_build_conversation_history_with_region_ids():
    """
    Test entity ID tracking for regions (regionId).
    
    Flow:
    1. Create track → trackId
    2. Create region on track → regionId
    3. Add notes to region → must use regionId
    """
    track_id = "track-uuid-123"
    region_id = "region-uuid-456"
    
    messages = [
        MagicMock(
            role="user",
            content="Create a drum track and region",
            tool_calls=None,
        ),
        MagicMock(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_track",
                    "type": "function",
                    "name": "stori_add_midi_track",
                    "arguments": {"name": "Drums", "trackId": track_id}
                },
                {
                    "id": "call_region",
                    "type": "function",
                    "name": "stori_add_midi_region",
                    "arguments": {
                        "trackId": track_id,
                        "regionId": region_id,
                        "startBeat": 0,
                        "durationBeats": 16
                    }
                },
            ],
        ),
        MagicMock(
            role="user",
            content="Add some kick drum notes",
            tool_calls=None,
        ),
        MagicMock(
            role="assistant",
            content="",
            tool_calls=[{
                "id": "call_notes",
                "type": "function",
                "name": "stori_add_notes",
                "arguments": {
                    "regionId": region_id,  # Must reuse regionId from previous turn
                    "notes": [{"pitch": 36, "startBeats": 0, "durationBeats": 0.25, "velocity": 100}]
                }
            }],
        ),
    ]
    
    history = build_conversation_history_for_llm(messages)
    
    # Verify regionId is tracked across turns
    import json
    
    # First assistant message has 2 tool calls
    assert len(history[1]["tool_calls"]) == 2
    region_tool_call = history[1]["tool_calls"][1]
    region_args = json.loads(region_tool_call["function"]["arguments"])
    assert region_args["regionId"] == region_id
    
    # Second assistant message reuses regionId
    notes_tool_call = history[5]["tool_calls"][0]  # After user2, assistant2
    notes_args = json.loads(notes_tool_call["function"]["arguments"])
    assert notes_args["regionId"] == region_id  # Same regionId!


def test_build_conversation_history_empty_messages():
    """Test handling of empty message list."""
    history = build_conversation_history_for_llm([])
    assert history == []


def test_build_conversation_history_no_tool_calls():
    """Test conversation with only text, no tool calls."""
    messages = [
        MagicMock(role="user", content="What tempo should I use?", tool_calls=None),
        MagicMock(role="assistant", content="Try 90 BPM for boom bap.", tool_calls=None),
    ]
    
    history = build_conversation_history_for_llm(messages)
    
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"
    # No tool results should be added
    assert all(m["role"] in ["user", "assistant"] for m in history)
