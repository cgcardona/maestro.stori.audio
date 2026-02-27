"""Tests for LLM client streaming (app/core/llm_client.py chat_completion_stream).

Covers the chat_completion_stream method with mocked HTTP responses.
"""
from __future__ import annotations

from app.contracts.llm_types import OpenAIStreamChunk, ReasoningDetail, StreamDelta, ToolCallDelta, ToolCallFunctionDelta, UsageStats

from collections.abc import AsyncIterator
import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from contextlib import asynccontextmanager

from app.core.llm_client import LLMClient, LLMProvider


class MockAsyncIterator:
    """Mock async line iterator for streaming responses."""
    def __init__(self, lines: list[str]) -> None:
        self._lines = iter(lines)

    def __aiter__(self) -> MockAsyncIterator:
        return self

    async def __anext__(self) -> str:

        try:
            return next(self._lines)
        except StopIteration:
            raise StopAsyncIteration


class MockStreamResponse:
    """Mock streaming response that works as a sync object (not awaited)."""
    def __init__(self, status_code: int, lines: list[str]) -> None:
        self.status_code = status_code
        self._lines = lines

    def aiter_lines(self) -> MockAsyncIterator:
        return MockAsyncIterator(self._lines)

    async def aread(self) -> bytes:

        return b"error"

    def raise_for_status(self) -> None:

        if self.status_code != 200:
            import httpx
            raise httpx.HTTPStatusError(
                str(self.status_code),
                request=MagicMock(),
                response=MagicMock(status_code=self.status_code),
            )


def _sse_line(obj: object) -> str:
    """Build an SSE data line from a dict."""
    return "data: " + json.dumps(obj)


def _choice_delta(
    delta: StreamDelta, finish_reason: str | None = None, usage: UsageStats | None = None
) -> OpenAIStreamChunk:

    """Build a streaming choice object."""
    obj: OpenAIStreamChunk = {"choices": [{"delta": delta, "finish_reason": finish_reason}]}
    if usage:
        obj["usage"] = usage
    return obj


def _make_mock_client(status_code: int, lines: list[str]) -> MagicMock:
    """Create a mock httpx.AsyncClient whose .stream() is an async context manager."""
    mock_response = MockStreamResponse(status_code, lines)
    mock_client = MagicMock()

    @asynccontextmanager
    async def mock_stream(*args: object, **kwargs: object) -> AsyncIterator[MockStreamResponse]:

        yield mock_response

    mock_client.stream = mock_stream
    mock_client.aclose = lambda: None
    return mock_client


class TestChatCompletionStream:

    def _make_client(self, model: str = "test-model") -> LLMClient:

        return LLMClient(
            provider=LLMProvider.OPENROUTER,
            api_key="test-key",
            model=model,
        )

    @pytest.mark.anyio
    async def test_stream_content_deltas(self) -> None:

        """Test streaming content deltas."""
        client = self._make_client()
        lines = [
            _sse_line(_choice_delta({"content": "Hello"})),
            _sse_line(_choice_delta({"content": " world"})),
            _sse_line(_choice_delta({}, finish_reason="stop", usage={"prompt_tokens": 10, "completion_tokens": 5})),
            "data: [DONE]",
        ]

        client._client = _make_mock_client(200, lines)

        events = []
        async for event in client.chat_completion_stream(
            messages=[{"role": "user", "content": "hi"}]
        ):
            events.append(event)

        content_events = [e for e in events if e.get("type") == "content_delta"]
        done_events = [e for e in events if e.get("type") == "done"]
        assert len(content_events) >= 1
        assert len(done_events) == 1

    @pytest.mark.anyio
    async def test_stream_tool_calls(self) -> None:

        """Test streaming tool call accumulation."""
        client = self._make_client()
        tc_args = json.dumps({"tempo": 120})
        tc_initial: StreamDelta = {
            "tool_calls": [
                ToolCallDelta(index=0, id="tc-1", function=ToolCallFunctionDelta(name="stori_set_tempo", arguments=""))
            ]
        }
        tc_update: StreamDelta = {
            "tool_calls": [
                ToolCallDelta(index=0, function=ToolCallFunctionDelta(arguments=tc_args))
            ]
        }
        lines = [
            _sse_line(_choice_delta(tc_initial)),
            _sse_line(_choice_delta(tc_update)),
            _sse_line(_choice_delta({}, finish_reason="tool_calls")),
            "data: [DONE]",
        ]

        client._client = _make_mock_client(200, lines)

        events = []
        async for event in client.chat_completion_stream(
            messages=[{"role": "user", "content": "set tempo"}],
            tools=[{"type": "function", "function": {"name": "stori_set_tempo", "description": "Set project tempo"}}],
        ):
            events.append(event)

        done_event = next(e for e in events if e["type"] == "done")
        assert len(done_event["tool_calls"]) == 1
        assert done_event["tool_calls"][0]["function"]["name"] == "stori_set_tempo"

    @pytest.mark.anyio
    async def test_stream_reasoning_deltas(self) -> None:

        """Test streaming reasoning (thinking) tokens."""
        client = self._make_client(model="anthropic/claude-3.7-sonnet")
        reasoning_detail: list[ReasoningDetail] = [{"type": "reasoning.text", "text": "Thinking..."}]
        lines = [
            _sse_line(_choice_delta({"reasoning_details": reasoning_detail})),
            _sse_line(_choice_delta({"content": "Here is the answer"})),
            _sse_line(_choice_delta({}, finish_reason="stop")),
            "data: [DONE]",
        ]

        client._client = _make_mock_client(200, lines)

        events = []
        async for event in client.chat_completion_stream(
            messages=[{"role": "user", "content": "think about this"}]
        ):
            events.append(event)

        reasoning_events = [e for e in events if e.get("type") == "reasoning_delta"]
        assert len(reasoning_events) >= 1

    @pytest.mark.anyio
    async def test_stream_empty_done(self) -> None:

        """Handle a stream that just emits DONE."""
        client = self._make_client()
        lines = ["data: [DONE]"]

        client._client = _make_mock_client(200, lines)

        events = []
        async for event in client.chat_completion_stream(
            messages=[{"role": "user", "content": "empty"}]
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0]["type"] == "done"

    @pytest.mark.anyio
    async def test_stream_skips_non_data_lines(self) -> None:

        """Lines without data: prefix should be skipped."""
        client = self._make_client()
        lines = [
            "",
            ": keep-alive",
            _sse_line(_choice_delta({"content": "ok"})),
            "data: [DONE]",
        ]

        client._client = _make_mock_client(200, lines)

        events = []
        async for event in client.chat_completion_stream(
            messages=[{"role": "user", "content": "test"}]
        ):
            events.append(event)

        content_events = [e for e in events if e.get("type") == "content_delta"]
        assert len(content_events) == 1
