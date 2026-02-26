"""SSE stream parser — handles raw text/event-stream into typed events."""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from stori_tourdeforce.models import ParsedSSEEvent

logger = logging.getLogger(__name__)


def parse_sse_line(line: str) -> ParsedSSEEvent | None:
    """Parse a single SSE data line into a ParsedSSEEvent.

    SSE format:
        data: {"type": "state", ...}

    Returns None for comments, empty lines, or non-data lines.
    """
    line = line.strip()
    if not line or line.startswith(":"):
        return None

    if not line.startswith("data: "):
        return None

    json_str = line[6:]  # Strip "data: " prefix
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse SSE JSON: %s (line: %s)", e, json_str[:200])
        return ParsedSSEEvent(
            event_type="parse_error",
            data={"raw": json_str, "error": str(e)},
            raw=line,
        )

    event_type = data.get("type", "unknown")
    seq = data.get("seq", -1)

    return ParsedSSEEvent(
        event_type=event_type,
        data=data,
        raw=line,
        seq=seq,
    )


async def parse_sse_stream(
    line_iter: AsyncIterator[str],
) -> AsyncIterator[ParsedSSEEvent]:
    """Parse an async iterator of SSE lines into ParsedSSEEvents."""
    async for line in line_iter:
        event = parse_sse_line(line)
        if event is not None:
            yield event


def extract_tool_calls(events: list[ParsedSSEEvent]) -> list[dict]:
    """Extract all toolCall events from a parsed SSE stream."""
    return [
        {
            "id": e.data.get("id", ""),
            "name": e.data.get("name", ""),
            "params": e.data.get("params", {}),
            "label": e.data.get("label"),
            "phase": e.data.get("phase", "composition"),
            "agentId": e.data.get("agentId"),
            "sectionName": e.data.get("sectionName"),
        }
        for e in events
        if e.event_type == "toolCall"
    ]


def extract_state(events: list[ParsedSSEEvent]) -> dict:
    """Extract the state event (intent classification) from parsed events."""
    for e in events:
        if e.event_type == "state":
            return {
                "state": e.data.get("state", ""),
                "intent": e.data.get("intent", ""),
                "confidence": e.data.get("confidence", 0.0),
                "traceId": e.data.get("traceId", ""),
                "executionMode": e.data.get("executionMode", ""),
            }
    return {}


def extract_complete(events: list[ParsedSSEEvent]) -> dict:
    """Extract the complete event (terminal) from parsed events."""
    for e in events:
        if e.event_type == "complete":
            return e.data
    return {}


def extract_generator_events(events: list[ParsedSSEEvent]) -> list[dict]:
    """Extract generatorStart/generatorComplete pairs for Storpheus instrumentation."""
    generators: list[dict] = []
    starts: dict[str, dict] = {}

    for e in events:
        if e.event_type == "generatorStart":
            key = e.data.get("agentId", "")
            starts[key] = e.data
        elif e.event_type == "generatorComplete":
            key = e.data.get("agentId", "")
            start_data = starts.pop(key, {})
            generators.append({
                "role": e.data.get("role", ""),
                "agent_id": key,
                "note_count": e.data.get("noteCount", 0),
                "duration_ms": e.data.get("durationMs", 0),
                "start_data": start_data,
            })

    return generators
