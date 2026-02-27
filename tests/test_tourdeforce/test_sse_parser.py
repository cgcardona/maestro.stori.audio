"""Tests for SSE parsing logic."""

from __future__ import annotations

import pytest

from tourdeforce.sse_parser import (
    extract_complete,
    extract_generator_events,
    extract_state,
    extract_tool_calls,
    parse_sse_line,
)
from tourdeforce.models import ParsedSSEEvent


class TestParseSSELine:

    def test_data_line_parses(self) -> None:

        line = 'data: {"type": "state", "state": "composing", "intent": "compose.generate_music", "confidence": 0.95, "traceId": "t_abc", "executionMode": "variation"}'
        event = parse_sse_line(line)
        assert event is not None
        assert event.event_type == "state"
        assert event.data["state"] == "composing"
        assert event.data["intent"] == "compose.generate_music"

    def test_empty_line_returns_none(self) -> None:

        assert parse_sse_line("") is None
        assert parse_sse_line("   ") is None

    def test_comment_returns_none(self) -> None:

        assert parse_sse_line(": keepalive") is None

    def test_non_data_line_returns_none(self) -> None:

        assert parse_sse_line("event: message") is None
        assert parse_sse_line("id: 42") is None

    def test_invalid_json_returns_parse_error(self) -> None:

        event = parse_sse_line("data: {invalid json}")
        assert event is not None
        assert event.event_type == "parse_error"

    def test_tool_call_event(self) -> None:

        line = 'data: {"type": "toolCall", "id": "tc_1", "name": "addMidiTrack", "params": {"name": "Bass"}, "seq": 5}'
        event = parse_sse_line(line)
        assert event is not None
        assert event.event_type == "toolCall"
        assert event.data["name"] == "addMidiTrack"
        assert event.seq == 5

    def test_complete_event(self) -> None:

        line = 'data: {"type": "complete", "success": true, "traceId": "t_xyz", "inputTokens": 500}'
        event = parse_sse_line(line)
        assert event is not None
        assert event.event_type == "complete"
        assert event.data["success"] is True

    def test_generator_start_event(self) -> None:

        line = 'data: {"type": "generatorStart", "role": "bass", "agentId": "a_bass", "style": "boom_bap", "bars": 4, "startBeat": 0, "label": "Bass"}'
        event = parse_sse_line(line)
        assert event is not None
        assert event.event_type == "generatorStart"
        assert event.data["role"] == "bass"

    def test_error_event(self) -> None:

        line = 'data: {"type": "error", "message": "something broke", "code": "INTERNAL"}'
        event = parse_sse_line(line)
        assert event is not None
        assert event.event_type == "error"
        assert event.data["message"] == "something broke"

    def test_plan_event(self) -> None:

        line = 'data: {"type": "plan", "planId": "pl_1", "title": "Compose", "steps": [], "seq": 1}'
        event = parse_sse_line(line)
        assert event is not None
        assert event.event_type == "plan"
        assert event.data["planId"] == "pl_1"


class TestExtractors:

    @pytest.fixture
    def sample_events(self) -> list[ParsedSSEEvent]:

        return [
            ParsedSSEEvent(event_type="state", data={"state": "composing", "intent": "compose.generate_music", "confidence": 0.9, "traceId": "t1", "executionMode": "variation"}, raw="", seq=0),
            ParsedSSEEvent(event_type="toolCall", data={"id": "tc1", "name": "addMidiTrack", "params": {"name": "Drums"}, "phase": "composition"}, raw="", seq=3),
            ParsedSSEEvent(event_type="toolCall", data={"id": "tc2", "name": "addNotes", "params": {"notes": [{"pitch": 60}]}, "phase": "composition"}, raw="", seq=4),
            ParsedSSEEvent(event_type="generatorStart", data={"role": "drums", "agentId": "a1", "style": "boom_bap", "bars": 4, "startBeat": 0, "label": "Drums"}, raw="", seq=5),
            ParsedSSEEvent(event_type="generatorComplete", data={"role": "drums", "agentId": "a1", "noteCount": 32, "durationMs": 1500}, raw="", seq=6),
            ParsedSSEEvent(event_type="complete", data={"type": "complete", "success": True, "traceId": "t1", "inputTokens": 500}, raw="", seq=10),
        ]

    def test_extract_state(self, sample_events: list[ParsedSSEEvent]) -> None:

        state = extract_state(sample_events)
        assert state["state"] == "composing"
        assert state["intent"] == "compose.generate_music"
        assert state["executionMode"] == "variation"

    def test_extract_tool_calls(self, sample_events: list[ParsedSSEEvent]) -> None:

        tools = extract_tool_calls(sample_events)
        assert len(tools) == 2
        assert tools[0]["name"] == "addMidiTrack"
        assert tools[1]["name"] == "addNotes"

    def test_extract_complete(self, sample_events: list[ParsedSSEEvent]) -> None:

        complete = extract_complete(sample_events)
        assert complete["success"] is True
        assert complete["traceId"] == "t1"

    def test_extract_generator_events(self, sample_events: list[ParsedSSEEvent]) -> None:

        generators = extract_generator_events(sample_events)
        assert len(generators) == 1
        assert generators[0]["role"] == "drums"
        assert generators[0]["note_count"] == 32
        assert generators[0]["duration_ms"] == 1500

    def test_extract_from_empty(self) -> None:

        assert extract_state([]) == {}
        assert extract_complete([]) == {}
        assert extract_tool_calls([]) == []
        assert extract_generator_events([]) == []

    def test_multiple_tool_calls_order_preserved(self) -> None:

        events = [
            ParsedSSEEvent(event_type="toolCall", data={"id": "a", "name": "createProject", "params": {}}, raw="", seq=1),
            ParsedSSEEvent(event_type="toolCall", data={"id": "b", "name": "addMidiTrack", "params": {}}, raw="", seq=2),
            ParsedSSEEvent(event_type="toolCall", data={"id": "c", "name": "addMidiRegion", "params": {}}, raw="", seq=3),
            ParsedSSEEvent(event_type="toolCall", data={"id": "d", "name": "addNotes", "params": {}}, raw="", seq=4),
        ]
        tools = extract_tool_calls(events)
        assert [t["name"] for t in tools] == ["createProject", "addMidiTrack", "addMidiRegion", "addNotes"]
