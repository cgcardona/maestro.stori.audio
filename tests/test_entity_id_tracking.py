"""
Unit tests for entity ID tracking across conversation turns.

This tests the critical fix for the backend - ensuring that entity IDs
(trackId, regionId, busId) from previous tool calls are properly tracked
and available to the LLM in subsequent turns.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from maestro.api.routes.conversations import build_conversation_history_for_llm
from maestro.db.models import ConversationMessage


def _make_message(
    role: str,
    content: str,
    tool_calls: list[dict[str, object]] | None = None,
) -> ConversationMessage:
    """Build an unsaved ConversationMessage for unit testing (no DB required)."""
    return ConversationMessage(
        conversation_id="test-conv-id",
        role=role,
        content=content,
        tool_calls=tool_calls,
    )


def test_build_conversation_history_basic() -> None:
    """Test basic conversation history building with user/assistant messages."""
    messages = [
        _make_message("user", "Create a drum track"),
        _make_message("assistant", "I'll create a drum track for you."),
    ]

    history = build_conversation_history_for_llm(messages)

    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Create a drum track"
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] == "I'll create a drum track for you."


def test_build_conversation_history_with_tool_calls() -> None:
    """
    Test conversation history with tool calls - the CRITICAL case for entity ID tracking.

    The LLM must see previous tool calls with their parameters (including generated UUIDs)
    so it can reuse those IDs in subsequent tool calls.
    """
    messages = [
        _make_message("user", "Create a drum track"),
        _make_message(
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "call_abc123",
                    "type": "function",
                    "name": "stori_add_midi_track",
                    "arguments": {
                        "name": "Drums",
                        "trackId": "07761e6f-2cca-4ac7-9397-71b3a01953ed",
                    },
                }
            ],
        ),
    ]

    history = build_conversation_history_for_llm(messages)

    # Should have: user message, assistant message with tool_calls, tool result
    assert len(history) == 3

    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Create a drum track"

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

    assert history[2]["role"] == "tool"
    assert history[2]["tool_call_id"] == "call_abc123"
    assert "success" in history[2]["content"]


def test_build_conversation_history_multi_turn_with_entity_reuse() -> None:
    """
    Test multi-turn conversation where entity IDs must be tracked and reused.

    This simulates the exact scenario from the bug report:
    1. Turn 1: Create track with trackId=X
    2. Turn 2: Add effect using trackId=X (must see X from turn 1)
    """
    track_id = "07761e6f-2cca-4ac7-9397-71b3a01953ed"

    messages = [
        _make_message("user", "Create a drum track"),
        _make_message(
            "assistant",
            "",
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "name": "stori_add_midi_track",
                "arguments": {"name": "Drums", "trackId": track_id},
            }],
        ),
        _make_message("user", "Add compressor to drums"),
        _make_message(
            "assistant",
            "",
            tool_calls=[{
                "id": "call_2",
                "type": "function",
                "name": "stori_add_insert_effect",
                "arguments": {"trackId": track_id, "type": "compressor"},
            }],
        ),
    ]

    history = build_conversation_history_for_llm(messages)

    # Should have: user1, assistant1, tool_result1, user2, assistant2, tool_result2
    assert len(history) == 6

    import json
    turn1_msg = history[1]
    assert turn1_msg["role"] == "assistant"
    turn1_tool_call = turn1_msg["tool_calls"][0]
    turn1_args = json.loads(turn1_tool_call["function"]["arguments"])
    assert turn1_args["trackId"] == track_id

    # CRITICAL: Verify turn 2 tool call reuses the SAME trackId
    turn2_msg = history[4]
    assert turn2_msg["role"] == "assistant"
    turn2_tool_call = turn2_msg["tool_calls"][0]
    turn2_args = json.loads(turn2_tool_call["function"]["arguments"])
    assert turn2_args["trackId"] == track_id
    assert turn2_args["type"] == "compressor"


def test_build_conversation_history_with_region_ids() -> None:
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
        _make_message("user", "Create a drum track and region"),
        _make_message(
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "call_track",
                    "type": "function",
                    "name": "stori_add_midi_track",
                    "arguments": {"name": "Drums", "trackId": track_id},
                },
                {
                    "id": "call_region",
                    "type": "function",
                    "name": "stori_add_midi_region",
                    "arguments": {
                        "trackId": track_id,
                        "regionId": region_id,
                        "startBeat": 0,
                        "durationBeats": 16,
                    },
                },
            ],
        ),
        _make_message("user", "Add some kick drum notes"),
        _make_message(
            "assistant",
            "",
            tool_calls=[{
                "id": "call_notes",
                "type": "function",
                "name": "stori_add_notes",
                "arguments": {
                    "regionId": region_id,
                    "notes": [{"pitch": 36, "startBeat": 0, "durationBeats": 0.25, "velocity": 100}],
                },
            }],
        ),
    ]

    history = build_conversation_history_for_llm(messages)

    import json

    msg1 = history[1]
    assert msg1["role"] == "assistant"
    assert len(msg1["tool_calls"]) == 2
    region_tool_call = msg1["tool_calls"][1]
    region_args = json.loads(region_tool_call["function"]["arguments"])
    assert region_args["regionId"] == region_id

    msg5 = history[5]
    assert msg5["role"] == "assistant"
    notes_tool_call = msg5["tool_calls"][0]
    notes_args = json.loads(notes_tool_call["function"]["arguments"])
    assert notes_args["regionId"] == region_id


def test_build_conversation_history_empty_messages() -> None:
    """Test handling of empty message list."""
    history = build_conversation_history_for_llm([])
    assert history == []


def test_build_conversation_history_no_tool_calls() -> None:
    """Test conversation with only text, no tool calls."""
    messages = [
        _make_message("user", "What tempo should I use?"),
        _make_message("assistant", "Try 90 BPM for boom bap."),
    ]

    history = build_conversation_history_for_llm(messages)

    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"
    assert all(m["role"] in ["user", "assistant"] for m in history)
