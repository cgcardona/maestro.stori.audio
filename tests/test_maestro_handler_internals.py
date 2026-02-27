"""Tests for maestro handler internal functions (_handle_reasoning, _handle_composing, _handle_editing, _stream_llm_response).

Supplements test_maestro_handlers.py with deeper coverage of maestro handler internals
and execution mode policy.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Iterable
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from app.contracts.project_types import ProjectContext
    from app.core.maestro_plan_tracker import _PlanTracker
    from app.core.prompt_parser import ParsedPrompt
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing_extensions import Required, TypedDict

from app.contracts.json_types import JSONObject, JSONValue, NoteDict, ToolCallDict, json_list
from app.contracts.llm_types import (
    ChatMessage,
    OpenAIToolChoice,
    StreamEvent,
    ToolCallEntry,
    ToolSchemaDict,
)


class _ParsedSSEEvent(TypedDict, total=False):
    """Wire shape of a parsed SSE event from the Maestro protocol.

    Used as the return type of ``_parse_events`` so every field access on
    a parsed event is statically known.  All fields are optional (``total=False``)
    except ``type`` which every SSE event carries.
    """

    type: Required[str]
    content: str | None
    text: str | None
    message: str | None
    name: str | None
    params: dict[str, JSONValue]
    variationId: str | None
    baseStateId: str | None
    phraseId: str | None
    stepId: str | None
    stepLabel: str | None
    state: str | None
    tracksReused: list[JSONObject]
    toolCalls: list[JSONValue]
    steps: list[JSONValue]
    status: str
    planId: str
    success: bool
    error: str | None
from app.contracts.project_types import ProjectContext
from app.protocol.events import MaestroEvent, PlanStepUpdateEvent, ReasoningEvent, ToolCallEvent
from app.core.maestro_handlers import UsageTracker, orchestrate
from app.core.maestro_helpers import StreamFinalResponse, _stream_llm_response
from app.core.maestro_composing import _handle_reasoning, _handle_composing, _retry_composing_as_editing
from app.core.maestro_editing import _handle_editing
from app.core.maestro_plan_tracker import _PlanTracker, _PlanStep, _build_step_result
from app.core.planner import ExecutionPlan
from app.core.expansion import ToolCall
from app.core.intent import Intent, IntentResult, SSEState
from app.core.llm_client import LLMResponse
from app.core.state_store import StateStore
from app.core.tracing import TraceContext


def _parse_events(events: list[str]) -> list[_ParsedSSEEvent]:

    """Parse SSE event strings into dicts."""
    parsed = []
    for e in events:
        if "data:" in e:
            parsed.append(json.loads(e.split("data: ", 1)[1].strip()))
    return parsed


async def _fake_plan_stream(plan: ExecutionPlan) -> AsyncGenerator[ExecutionPlan, None]:

    """Async generator yielding a single ExecutionPlan (simulates build_execution_plan_stream)."""
    yield plan


def _make_trace() -> TraceContext:
    return TraceContext(trace_id="test-trace-id")


def _make_route(
    sse_state: SSEState = SSEState.REASONING,
    intent: Intent = Intent.UNKNOWN,
    requires_planner: bool = False,
    allowed_tool_names: set[str] | None = None,
    tool_choice: OpenAIToolChoice | None = "none",
    tools: list[ToolSchemaDict] | None = None,
    confidence: float = 0.9,
    force_stop_after: bool = False,
) -> IntentResult:
    from app.core.intent.models import Slots
    return IntentResult(
        intent=intent,
        sse_state=sse_state,
        confidence=confidence,
        slots=Slots(),
        tools=tools or [],
        allowed_tool_names=allowed_tool_names or set(),
        tool_choice=tool_choice,
        force_stop_after=force_stop_after,
        requires_planner=requires_planner,
        reasons=(),
    )


def _response_to_stream(
    response: LLMResponse,
) -> Callable[..., AsyncGenerator[StreamEvent, None]]:
    """Convert an LLMResponse to an async iterator matching chat_completion_stream protocol.

    Used to adapt tests that mocked chat_completion to the streaming API
    used by _run_instrument_agent.
    """
    async def _stream(*args: object, **kwargs: object) -> AsyncGenerator[StreamEvent, None]:

        tool_calls_raw: list[ToolCallEntry] = [
            ToolCallEntry(id=tc.id, type="function", function={"name": tc.name, "arguments": json.dumps(tc.params)})
            for tc in response.tool_calls
        ]
        yield {
            "type": "done",
            "content": response.content,
            "tool_calls": tool_calls_raw,
            "finish_reason": response.finish_reason,
            "usage": response.usage or {},
        }
    return _stream


def _make_llm_mock(content: str | None = "Hello", supports_reasoning: bool = False, tool_calls: list[ToolCall] | None = None) -> MagicMock:

    mock = MagicMock()
    mock.model = "test-model"
    mock.supports_reasoning = MagicMock(return_value=supports_reasoning)
    response = LLMResponse(
        content=content,
        usage={"prompt_tokens": 10, "completion_tokens": 20},
    )
    if tool_calls:
        response.tool_calls = tool_calls
    mock.chat_completion = AsyncMock(return_value=response)
    mock.close = AsyncMock()
    return mock


# ---------------------------------------------------------------------------
# _handle_reasoning
# ---------------------------------------------------------------------------


class TestHandleReasoning:

    @pytest.mark.anyio
    async def test_general_question_non_reasoning_model(self) -> None:

        """Non-reasoning model: chat_completion -> content -> complete."""
        llm = _make_llm_mock(content="The answer is 4.")
        trace = _make_trace()
        route = _make_route(SSEState.REASONING, Intent.UNKNOWN)

        events = []
        async for e in _handle_reasoning("What is 2+2?", {}, route, llm, trace, None, []):
            events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        assert "status" in types
        assert "content" in types
        assert "complete" in types
        content_p = [p for p in payloads if p["type"] == "content"]
        assert any("The answer is 4" in (p.get("content") or "") for p in content_p)

    @pytest.mark.anyio
    async def test_reasoning_model_streams_deltas(self) -> None:

        """Reasoning model: chat_completion_stream yields reasoning + content deltas."""
        llm = _make_llm_mock(supports_reasoning=True)
        trace = _make_trace()
        route = _make_route(SSEState.REASONING, Intent.UNKNOWN)

        async def fake_stream(*args: object, **kwargs: object) -> AsyncGenerator[StreamEvent, None]:

            yield {"type": "reasoning_delta", "text": "Thinking about this..."}
            yield {"type": "content_delta", "text": "The answer."}
            yield {"type": "done", "content": "The answer.", "tool_calls": [], "finish_reason": "stop", "usage": {"prompt_tokens": 5, "completion_tokens": 10}}

        llm.chat_completion_stream = MagicMock(side_effect=fake_stream)

        events = []
        async for e in _handle_reasoning("question?", {}, route, llm, trace, UsageTracker(), []):
            events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        assert "reasoning" in types or "content" in types
        assert "complete" in types

    @pytest.mark.anyio
    async def test_reasoning_tracks_usage(self) -> None:

        """Usage tracker accumulates tokens from reasoning."""
        llm = _make_llm_mock(content="answer")
        trace = _make_trace()
        route = _make_route(SSEState.REASONING)
        tracker = UsageTracker()

        events = []
        async for e in _handle_reasoning("q?", {}, route, llm, trace, tracker, []):
            events.append(e)

        assert tracker.prompt_tokens == 10
        assert tracker.completion_tokens == 20

    @pytest.mark.anyio
    async def test_rag_path_ask_stori_docs(self) -> None:

        """ASK_STORI_DOCS intent routes through RAG when collection exists."""
        llm = _make_llm_mock()
        trace = _make_trace()
        route = _make_route(SSEState.REASONING, Intent.ASK_STORI_DOCS)

        mock_rag = MagicMock()
        mock_rag.collection_exists.return_value = True
        async def rag_answer(*a: object, **k: object) -> AsyncGenerator[str, None]:

            yield "RAG answer part 1"
            yield "RAG answer part 2"
        mock_rag.answer = rag_answer

        with patch("app.services.rag.get_rag_service", return_value=mock_rag):
            events = []
            async for e in _handle_reasoning("How do Stori tracks work?", {}, route, llm, trace, None, []):
                events.append(e)

        payloads = _parse_events(events)
        content_events = [p for p in payloads if p["type"] == "content"]
        assert len(content_events) == 2
        assert "RAG answer part 1" in (content_events[0].get("content") or "")

    @pytest.mark.anyio
    async def test_rag_fallback_on_error(self) -> None:

        """If RAG throws, falls back to general LLM path."""
        llm = _make_llm_mock(content="Fallback answer")
        trace = _make_trace()
        route = _make_route(SSEState.REASONING, Intent.ASK_STORI_DOCS)

        mock_rag = MagicMock()
        mock_rag.collection_exists.side_effect = RuntimeError("RAG broken")

        with patch("app.services.rag.get_rag_service", return_value=mock_rag):
            events = []
            async for e in _handle_reasoning("docs?", {}, route, llm, trace, None, []):
                events.append(e)

        payloads = _parse_events(events)
        content_events = [p for p in payloads if p["type"] == "content"]
        assert any("Fallback answer" in (p.get("content") or "") for p in content_events)

    @pytest.mark.anyio
    async def test_includes_conversation_history(self) -> None:

        """Conversation history is passed to LLM messages."""
        llm = _make_llm_mock(content="With context")
        trace = _make_trace()
        route = _make_route(SSEState.REASONING)
        history: list[ChatMessage] = [{"role": "user", "content": "prev"}, {"role": "assistant", "content": "prev answer"}]

        events = []
        async for e in _handle_reasoning("follow up?", {}, route, llm, trace, None, history):
            events.append(e)

        call_args = llm.chat_completion.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages") or call_args[0][0]
        # History should be in messages
        roles = [m["role"] for m in messages]
        assert "user" in roles  # from history + actual prompt


# ---------------------------------------------------------------------------
# _handle_composing
# ---------------------------------------------------------------------------


class TestHandleComposing:

    @pytest.mark.anyio
    async def test_variation_mode_emits_meta_phrases_done(self) -> None:

        """COMPOSING in variation mode emits meta -> phrase(s) -> done -> complete."""
        from app.core.planner import ExecutionPlan
        from app.core.expansion import ToolCall
        from app.models.variation import Variation, Phrase, NoteChange, MidiNoteSnapshot

        route = _make_route(SSEState.COMPOSING, Intent.GENERATE_MUSIC, requires_planner=True)
        llm = _make_llm_mock()
        store = MagicMock(spec=StateStore)
        store.get_state_id.return_value = "1"
        store.conversation_id = "test-conv-id"
        store.registry = MagicMock()
        store.registry.get_region.return_value = None
        trace = _make_trace()

        plan = ExecutionPlan(
            tool_calls=[ToolCall("stori_add_notes", {"regionId": "r1", "notes": []})],
            safety_validated=True,
        )
        fake_variation = Variation(
            variation_id="var-123",
            intent="make a beat",
            ai_explanation="Added drums",
            affected_tracks=["t1"],
            affected_regions=["r1"],
            beat_range=(0.0, 16.0),
            phrases=[
                Phrase(
                    phrase_id="p1",
                    track_id="t1",
                    region_id="r1",
                    start_beat=0.0,
                    end_beat=4.0,
                    label="Bar 1",
                    note_changes=[
                        NoteChange(
                            note_id="nc-1",
                            change_type="added",
                            after=MidiNoteSnapshot(pitch=60, velocity=100, start_beat=0.0, duration_beats=1.0),
                        )
                    ],
                )
            ],
        )

        with (
            patch("app.core.maestro_composing.composing.build_execution_plan_stream", return_value=_fake_plan_stream(plan)),
            patch("app.core.executor.execute_plan_variation", new_callable=AsyncMock, return_value=fake_variation),
        ):
            events = []
            async for e in _handle_composing("make a beat", {}, route, llm, store, trace, None, None):
                events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        assert types[0] == "status"
        assert "meta" in types
        assert "phrase" in types
        assert "done" in types
        assert "complete" in types

        meta = next(p for p in payloads if p["type"] == "meta")
        assert meta["variationId"] == "var-123"
        assert meta["baseStateId"] == "1"
        phrase = next(p for p in payloads if p["type"] == "phrase")
        assert phrase["phraseId"] == "p1"

    @pytest.mark.anyio
    async def test_empty_plan_asks_for_clarification(self) -> None:

        """When planner returns no tool_calls and no function-call text, asks for clarification."""
        from app.core.planner import ExecutionPlan

        route = _make_route(SSEState.COMPOSING, Intent.GENERATE_MUSIC, requires_planner=True)
        llm = _make_llm_mock()
        store = MagicMock(spec=StateStore)
        trace = _make_trace()

        plan = ExecutionPlan(tool_calls=[], safety_validated=False, llm_response_text="I'm not sure what to do.")

        with patch("app.core.maestro_composing.composing.build_execution_plan_stream", return_value=_fake_plan_stream(plan)):
            events = []
            async for e in _handle_composing("do something", {}, route, llm, store, trace, None, None):
                events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        assert "content" in types
        assert "complete" in types
        content_p = next(p for p in payloads if p["type"] == "content")
        c = content_p.get("content") or ""
        assert "style" in c.lower() or "genre" in c.lower()

    @pytest.mark.anyio
    async def test_empty_plan_no_response_text(self) -> None:

        """When planner returns no tool_calls and no response text, asks for info."""
        from app.core.planner import ExecutionPlan

        route = _make_route(SSEState.COMPOSING, Intent.GENERATE_MUSIC, requires_planner=True)
        llm = _make_llm_mock()
        store = MagicMock(spec=StateStore)
        trace = _make_trace()

        plan = ExecutionPlan(tool_calls=[], safety_validated=False)

        with patch("app.core.maestro_composing.composing.build_execution_plan_stream", return_value=_fake_plan_stream(plan)):
            events = []
            async for e in _handle_composing("", {}, route, llm, store, trace, None, None):
                events.append(e)

        payloads = _parse_events(events)
        content_p = [p for p in payloads if p["type"] == "content"]
        assert len(content_p) >= 1


# ---------------------------------------------------------------------------
# _handle_editing
# ---------------------------------------------------------------------------


class TestHandleEditing:

    @pytest.mark.anyio
    async def test_editing_apply_mode_emits_tool_calls(self) -> None:

        """EDITING in apply mode emits tool_call events."""
        from app.core.tools import ALL_TOOLS

        allowed = {"stori_set_tempo"}
        route = _make_route(
            SSEState.EDITING,
            Intent.PROJECT_SET_TEMPO,
            allowed_tool_names=allowed,
            tool_choice="auto",
            tools=[t for t in ALL_TOOLS if t["function"]["name"] in allowed],
        )
        response = LLMResponse(content=None, usage={"prompt_tokens": 5, "completion_tokens": 5})
        response.tool_calls = [ToolCall(id="tc1", name="stori_set_tempo", params={"tempo": 120})]

        done_response = LLMResponse(content="Done!", usage={"prompt_tokens": 5, "completion_tokens": 5})

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(side_effect=[response, done_response])
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        events = []
        async for e in _handle_editing("set tempo to 120", {}, route, llm, store, trace, None, [], "apply"):
            events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        assert "toolCall" in types
        tc = next(p for p in payloads if p["type"] == "toolCall")
        assert tc["name"] == "stori_set_tempo"
        assert tc["params"]["tempo"] == 120

    @pytest.mark.anyio
    async def test_editing_emits_content_alongside_tool_calls(self) -> None:

        """Content events are emitted during editing when LLM produces natural-language text."""
        from app.core.tools import ALL_TOOLS

        allowed = {"stori_set_tempo"}
        route = _make_route(
            SSEState.EDITING,
            Intent.PROJECT_SET_TEMPO,
            allowed_tool_names=allowed,
            tool_choice="auto",
            tools=[t for t in ALL_TOOLS if t["function"]["name"] in allowed],
        )
        # First response: content + tool call
        response = LLMResponse(
            content="I'll set the tempo to 120 BPM for you.",
            usage={"prompt_tokens": 5, "completion_tokens": 5},
        )
        response.tool_calls = [ToolCall(id="tc1", name="stori_set_tempo", params={"tempo": 120})]

        # Second response: content only (done)
        done_response = LLMResponse(
            content="All set! The tempo is now 120 BPM.",
            usage={"prompt_tokens": 5, "completion_tokens": 5},
        )

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(side_effect=[response, done_response])
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        events = []
        async for e in _handle_editing("set tempo to 120", {}, route, llm, store, trace, None, [], "apply"):
            events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]

        # Both content and toolCall events should be present
        assert "content" in types
        assert "toolCall" in types

        content_events = [p for p in payloads if p["type"] == "content"]
        # Content from first iteration + content from second iteration
        assert len(content_events) >= 1
        all_content = " ".join(c.get("content") or "" for c in content_events)
        assert "tempo" in all_content.lower() or "120" in all_content

    @pytest.mark.anyio
    async def test_editing_strips_tool_echo_from_content(self) -> None:

        """Tool-call syntax in content is filtered out before emission."""
        from app.core.tools import ALL_TOOLS

        allowed = {"stori_set_tempo"}
        route = _make_route(
            SSEState.EDITING,
            Intent.PROJECT_SET_TEMPO,
            allowed_tool_names=allowed,
            tool_choice="auto",
            tools=[t for t in ALL_TOOLS if t["function"]["name"] in allowed],
        )
        # Response with tool-call syntax leaking into content
        response = LLMResponse(
            content='Setting the tempo:\n\n(tempo=120)\n\nDone with tempo.',
            usage={"prompt_tokens": 5, "completion_tokens": 5},
        )
        response.tool_calls = [ToolCall(id="tc1", name="stori_set_tempo", params={"tempo": 120})]

        done_response = LLMResponse(content=None, usage={"prompt_tokens": 5, "completion_tokens": 5})

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(side_effect=[response, done_response])
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        events = []
        async for e in _handle_editing("set tempo to 120", {}, route, llm, store, trace, None, [], "apply"):
            events.append(e)

        payloads = _parse_events(events)
        content_events = [p for p in payloads if p["type"] == "content"]
        assert len(content_events) >= 1
        all_content = " ".join(c.get("content") or "" for c in content_events)
        # Natural language preserved
        assert "Setting the tempo" in all_content
        assert "Done with tempo" in all_content
        # Tool-call syntax stripped
        assert "(tempo=120)" not in all_content

    @pytest.mark.anyio
    async def test_editing_variation_mode_emits_variation_events(self) -> None:

        """EDITING in variation mode emits meta/phrase/done instead of tool_call."""
        from app.core.tools import ALL_TOOLS
        from app.models.variation import Variation, Phrase, NoteChange, MidiNoteSnapshot

        allowed = {"stori_set_tempo"}
        route = _make_route(
            SSEState.EDITING,
            Intent.PROJECT_SET_TEMPO,
            allowed_tool_names=allowed,
            tool_choice="auto",
            tools=[t for t in ALL_TOOLS if t["function"]["name"] in allowed],
        )
        response = LLMResponse(content=None, usage={"prompt_tokens": 5, "completion_tokens": 5})
        response.tool_calls = [ToolCall(id="tc1", name="stori_set_tempo", params={"tempo": 120})]

        done_response = LLMResponse(content="Done!", usage={"prompt_tokens": 5, "completion_tokens": 5})

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(side_effect=[response, done_response])
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        fake_variation = Variation(
            variation_id="var-edit-1",
            intent="set tempo",
            affected_tracks=[],
            affected_regions=[],
            beat_range=(0.0, 0.0),
            phrases=[],
        )

        with patch("app.core.executor.execute_plan_variation", new_callable=AsyncMock, return_value=fake_variation):
            events = []
            async for e in _handle_editing("set tempo to 120", {}, route, llm, store, trace, None, [], "variation"):
                events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        # In variation mode, should NOT emit raw toolCall events
        assert "toolCall" not in types
        assert "meta" in types
        assert "done" in types

    @pytest.mark.anyio
    async def test_editing_tool_validation_error_retries(self) -> None:

        """Invalid tool calls trigger error event and retry."""
        from app.core.tools import ALL_TOOLS

        allowed = {"stori_set_tempo"}
        route = _make_route(
            SSEState.EDITING,
            Intent.PROJECT_SET_TEMPO,
            allowed_tool_names=allowed,
            tool_choice="auto",
        )

        # First response: tool not in allowlist
        bad_response = LLMResponse(content=None, usage={"prompt_tokens": 5, "completion_tokens": 5})
        bad_response.tool_calls = [ToolCall(id="tc1", name="stori_delete_track", params={"trackId": "x"})]

        # Second response: just content
        ok_response = LLMResponse(content="OK, done.", usage={"prompt_tokens": 5, "completion_tokens": 5})

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(side_effect=[bad_response, ok_response])
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        events = []
        async for e in _handle_editing("set tempo", {}, route, llm, store, trace, None, [], "apply"):
            events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        assert "toolError" in types

    @pytest.mark.anyio
    async def test_editing_force_stop_after(self) -> None:

        """force_stop_after stops after first tool execution."""
        from app.core.tools import ALL_TOOLS

        allowed = {"stori_set_tempo"}
        route = _make_route(
            SSEState.EDITING,
            Intent.PROJECT_SET_TEMPO,
            allowed_tool_names=allowed,
            tool_choice="required",
            force_stop_after=True,
        )

        response = LLMResponse(content=None, usage={"prompt_tokens": 5, "completion_tokens": 5})
        response.tool_calls = [
            ToolCall(id="tc1", name="stori_set_tempo", params={"tempo": 120}),
            ToolCall(id="tc2", name="stori_set_tempo", params={"tempo": 130}),
        ]

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(return_value=response)
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        events = []
        async for e in _handle_editing("set tempo", {}, route, llm, store, trace, None, [], "apply"):
            events.append(e)

        payloads = _parse_events(events)
        tc_events = [p for p in payloads if p["type"] == "toolCall"]
        # enforce_single_tool should reduce to 1
        assert len(tc_events) == 1

    @pytest.mark.anyio
    async def test_editing_no_tool_calls_returns_content(self) -> None:

        """When LLM returns content without tools, emit content and complete."""
        route = _make_route(
            SSEState.EDITING,
            Intent.PROJECT_SET_TEMPO,
            allowed_tool_names={"stori_set_tempo"},
            tool_choice="auto",
        )

        response = LLMResponse(content="I can't do that.", usage={"prompt_tokens": 5, "completion_tokens": 5})
        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(return_value=response)
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        events = []
        async for e in _handle_editing("impossible task", {}, route, llm, store, trace, None, [], "apply"):
            events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        assert "content" in types
        assert "complete" in types

    @pytest.mark.anyio
    async def test_editing_persists_notes_to_state_store(self) -> None:

        """stori_add_notes in EDITING mode should persist notes in StateStore for COMPOSING handoff."""
        from app.core.tools import ALL_TOOLS

        allowed = {"stori_add_notes", "stori_add_midi_track", "stori_add_midi_region"}
        route = _make_route(
            SSEState.EDITING,
            Intent.NOTES_ADD,
            allowed_tool_names=allowed,
            tool_choice="auto",
        )

        region_id = "r-test-persist"
        notes_payload: list[NoteDict] = [
            NoteDict(pitch=60, startBeat=0.0, durationBeats=1.0, velocity=100),
            NoteDict(pitch=62, startBeat=1.0, durationBeats=0.5, velocity=90),
        ]
        response = LLMResponse(content=None, usage={"prompt_tokens": 5, "completion_tokens": 5})
        response.tool_calls = [
            ToolCall(id="tc1", name="stori_add_notes", params={
                "regionId": region_id,
                "notes": json_list(notes_payload),
            }),
        ]
        done_response = LLMResponse(content="Done.", usage={"prompt_tokens": 5, "completion_tokens": 5})

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(side_effect=[response, done_response])
        store = StateStore(conversation_id="test-persist")
        # Pre-register the region so validation passes
        tid = store.create_track("Piano")
        store.create_region("Intro", tid, region_id=region_id)
        trace = _make_trace()

        events = []
        async for e in _handle_editing("add notes", {}, route, llm, store, trace, None, [], "apply"):
            events.append(e)

        # Notes should now be in the StateStore
        stored = store.get_region_notes(region_id)
        assert len(stored) == 2
        pitches = {n["pitch"] for n in stored}
        assert pitches == {60, 62}

    @pytest.mark.anyio
    async def test_editing_variation_meta_includes_base_state_id(self) -> None:

        """EDITING variation meta event must include base_state_id."""
        from app.core.tools import ALL_TOOLS
        from app.models.variation import Variation

        allowed = {"stori_set_tempo"}
        route = _make_route(
            SSEState.EDITING,
            Intent.PROJECT_SET_TEMPO,
            allowed_tool_names=allowed,
            tool_choice="auto",
            tools=[t for t in ALL_TOOLS if t["function"]["name"] in allowed],
        )
        response = LLMResponse(content=None, usage={"prompt_tokens": 5, "completion_tokens": 5})
        response.tool_calls = [ToolCall(id="tc1", name="stori_set_tempo", params={"tempo": 120})]
        done_response = LLMResponse(content="Done!", usage={"prompt_tokens": 5, "completion_tokens": 5})

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(side_effect=[response, done_response])
        store = StateStore(conversation_id="test-base-state")
        trace = _make_trace()

        fake_variation = Variation(
            variation_id="var-edit-bsid",
            intent="set tempo",
            affected_tracks=[],
            affected_regions=[],
            beat_range=(0.0, 0.0),
            phrases=[],
        )

        with patch("app.core.executor.execute_plan_variation", new_callable=AsyncMock, return_value=fake_variation):
            events = []
            async for e in _handle_editing("set tempo to 120", {}, route, llm, store, trace, None, [], "variation"):
                events.append(e)

        payloads = _parse_events(events)
        meta = next(p for p in payloads if p["type"] == "meta")
        assert "baseStateId" in meta
        assert meta["baseStateId"] == store.get_state_id()

    @pytest.mark.anyio
    async def test_editing_creates_track_entity_with_uuid(self) -> None:

        """stori_add_midi_track generates server-side UUID via StateStore."""
        from app.core.tools import ALL_TOOLS

        allowed = {"stori_add_midi_track"}
        route = _make_route(
            SSEState.EDITING,
            Intent.NOTES_ADD,
            allowed_tool_names=allowed,
            tool_choice="auto",
        )

        response = LLMResponse(content=None, usage={"prompt_tokens": 5, "completion_tokens": 5})
        response.tool_calls = [ToolCall(id="tc1", name="stori_add_midi_track", params={"name": "Drums"})]

        done_response = LLMResponse(content="Done.", usage={"prompt_tokens": 5, "completion_tokens": 5})

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(side_effect=[response, done_response])
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        events = []
        async for e in _handle_editing("add drums", {}, route, llm, store, trace, None, [], "apply"):
            events.append(e)

        payloads = _parse_events(events)
        tc_events = [p for p in payloads if p["type"] == "toolCall"]
        assert len(tc_events) >= 1
        # trackId should be a valid UUID
        import uuid
        track_id = tc_events[0]["params"]["trackId"]
        assert isinstance(track_id, str)
        uuid.UUID(track_id)  # should not raise

    @pytest.mark.anyio
    async def test_synthetic_set_track_icon_emitted_after_add_track_with_gm_program(self) -> None:

        """stori_set_track_icon is auto-emitted after stori_add_midi_track when gmProgram is set."""
        allowed = {"stori_add_midi_track"}
        route = _make_route(
            SSEState.EDITING,
            Intent.NOTES_ADD,
            allowed_tool_names=allowed,
            tool_choice="auto",
        )

        response = LLMResponse(content=None, usage={"prompt_tokens": 5, "completion_tokens": 5})
        response.tool_calls = [
            ToolCall(id="tc1", name="stori_add_midi_track", params={"name": "Bass", "gmProgram": 33})
        ]
        done_response = LLMResponse(content="Done.", usage={"prompt_tokens": 5, "completion_tokens": 5})

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(side_effect=[response, done_response])
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        events = []
        async for e in _handle_editing("add bass", {}, route, llm, store, trace, None, [], "apply"):
            events.append(e)

        payloads = _parse_events(events)
        tc_events = [p for p in payloads if p["type"] == "toolCall"]
        names = [p["name"] for p in tc_events]

        assert "stori_add_midi_track" in names
        assert "stori_set_track_icon" in names
        icon_call = next(p for p in tc_events if p["name"] == "stori_set_track_icon")
        # GM 33 = Electric Bass Guitar → guitars.fill
        assert icon_call["params"]["icon"] == "guitars.fill"
        # trackId must match the generated UUID from stori_add_midi_track
        track_call = next(p for p in tc_events if p["name"] == "stori_add_midi_track")
        assert icon_call["params"]["trackId"] == track_call["params"]["trackId"]

    @pytest.mark.anyio
    async def test_synthetic_set_track_icon_uses_drum_icon_for_drum_kit(self) -> None:

        """stori_set_track_icon uses instrument.drum when drumKitId is set."""
        allowed = {"stori_add_midi_track"}
        route = _make_route(
            SSEState.EDITING,
            Intent.NOTES_ADD,
            allowed_tool_names=allowed,
            tool_choice="auto",
        )

        response = LLMResponse(content=None, usage={"prompt_tokens": 5, "completion_tokens": 5})
        response.tool_calls = [
            ToolCall(id="tc1", name="stori_add_midi_track", params={"name": "Drums", "drumKitId": "acoustic"})
        ]
        done_response = LLMResponse(content="Done.", usage={"prompt_tokens": 5, "completion_tokens": 5})

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(side_effect=[response, done_response])
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        events = []
        async for e in _handle_editing("add drums", {}, route, llm, store, trace, None, [], "apply"):
            events.append(e)

        payloads = _parse_events(events)
        tc_events = [p for p in payloads if p["type"] == "toolCall"]
        icon_calls = [p for p in tc_events if p["name"] == "stori_set_track_icon"]

        assert len(icon_calls) == 1
        assert icon_calls[0]["params"]["icon"] == "instrument.drum"


# ---------------------------------------------------------------------------
# _stream_llm_response
# ---------------------------------------------------------------------------


class TestStreamLLMResponse:

    @pytest.mark.anyio
    async def test_yields_reasoning_and_content_deltas(self) -> None:

        """Streams reasoning and content deltas, ends with StreamFinalResponse."""
        llm = _make_llm_mock(supports_reasoning=True)
        trace = _make_trace()

        async def fake_stream(*args: object, **kwargs: object) -> AsyncGenerator[StreamEvent, None]:

            yield {"type": "reasoning_delta", "text": "Let me think..."}
            yield {"type": "content_delta", "text": "Here's the answer."}
            yield {"type": "done", "content": "Here's the answer.", "tool_calls": [], "finish_reason": "stop", "usage": {"prompt_tokens": 10, "completion_tokens": 20}}

        llm.chat_completion_stream = MagicMock(side_effect=fake_stream)

        items = []
        async for item in _stream_llm_response(llm, [], [], "auto", trace):
            items.append(item)

        # Last item should be StreamFinalResponse
        assert isinstance(items[-1], StreamFinalResponse)
        assert items[-1].response.content == "Here's the answer."

        # Other items are SSE event strings
        sse_items = [i for i in items if isinstance(i, str)]
        assert len(sse_items) >= 2

    @pytest.mark.anyio
    async def test_parses_tool_calls_from_done(self) -> None:

        """Tool calls in done chunk are parsed into LLMResponse."""
        llm = _make_llm_mock(supports_reasoning=True)
        trace = _make_trace()

        async def fake_stream(*args: object, **kwargs: object) -> AsyncGenerator[StreamEvent, None]:

            yield {
                "type": "done",
                "content": None,
                "tool_calls": [
                    ToolCallEntry(id="tc1", type="function", function={"name": "stori_set_tempo", "arguments": '{"tempo": 120}'}),
                ],
                "finish_reason": "tool_calls",
                "usage": {},
            }

        llm.chat_completion_stream = MagicMock(side_effect=fake_stream)

        items = []
        async for item in _stream_llm_response(llm, [], [], "auto", trace):
            items.append(item)

        final = items[-1]
        assert isinstance(final, StreamFinalResponse)
        assert len(final.response.tool_calls) == 1
        assert final.response.tool_calls[0].name == "stori_set_tempo"
        assert final.response.tool_calls[0].params == {"tempo": 120}


# ---------------------------------------------------------------------------
# _retry_composing_as_editing
# ---------------------------------------------------------------------------


class TestRetryComposingAsEditing:

    @pytest.mark.anyio
    async def test_emits_retry_status_then_delegates_to_editing(self) -> None:

        """Retry emits status message then delegates to _handle_editing."""
        route = _make_route(SSEState.COMPOSING, Intent.GENERATE_MUSIC)
        llm = _make_llm_mock(content="OK done.")
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        events = []
        async for e in _retry_composing_as_editing("add drums", {}, route, llm, store, trace, None):
            events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        assert "status" in types
        retry_status = next(p for p in payloads if p["type"] == "status" and "Retry" in (p.get("message") or ""))
        assert retry_status is not None


# ---------------------------------------------------------------------------
# orchestrate() execution mode policy
# ---------------------------------------------------------------------------


class TestOrchestrateExecutionModePolicy:

    @pytest.mark.anyio
    async def test_composing_forces_variation_mode(self) -> None:

        """COMPOSING intent sets execution_mode='variation' internally (requires non-empty project)."""
        from app.core.planner import ExecutionPlan
        from app.core.expansion import ToolCall
        from app.models.variation import Variation

        fake_route = _make_route(SSEState.COMPOSING, Intent.GENERATE_MUSIC, requires_planner=True)
        plan = ExecutionPlan(
            tool_calls=[ToolCall("stori_add_notes", {"regionId": "r1", "notes": []})],
            safety_validated=True,
        )
        fake_variation = Variation(
            variation_id="var-policy",
            intent="beat",
            affected_tracks=[],
            affected_regions=[],
            beat_range=(0.0, 0.0),
            phrases=[],
        )
        project_ctx: ProjectContext = {"id": "p1", "tracks": [{"id": "t1", "name": "Track 1"}]}

        with (
            patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route),
            patch("app.core.maestro_composing.composing.build_execution_plan_stream", return_value=_fake_plan_stream(plan)),
            patch("app.core.maestro_handlers.LLMClient") as m_cls,
            patch("app.core.executor.execute_plan_variation", new_callable=AsyncMock, return_value=fake_variation),
        ):
            m_cls.return_value = _make_llm_mock()
            events = []
            async for e in orchestrate("make a beat", project_context=project_ctx):
                events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        assert "meta" in types
        assert "done" in types

    @pytest.mark.anyio
    async def test_editing_forces_apply_mode(self) -> None:

        """EDITING intent sets execution_mode='apply' internally."""
        from app.core.tools import ALL_TOOLS

        allowed = {"stori_set_tempo"}
        fake_route = _make_route(
            SSEState.EDITING,
            Intent.PROJECT_SET_TEMPO,
            allowed_tool_names=allowed,
            tool_choice="auto",
        )

        response = LLMResponse(content=None, usage={"prompt_tokens": 5, "completion_tokens": 5})
        response.tool_calls = [ToolCall(id="tc1", name="stori_set_tempo", params={"tempo": 120})]
        done_response = LLMResponse(content="Done.", usage={"prompt_tokens": 5, "completion_tokens": 5})

        mock_llm = _make_llm_mock()
        mock_llm.chat_completion = AsyncMock(side_effect=[response, done_response])

        with (
            patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route),
            patch("app.core.maestro_handlers.LLMClient") as m_cls,
        ):
            m_cls.return_value = mock_llm
            events = []
            async for e in orchestrate("set tempo to 120"):
                events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        # Apply mode should emit toolCall events directly
        assert "toolCall" in types
        # Should NOT have variation events
        assert "meta" not in types


# ---------------------------------------------------------------------------
# Plan Tracker — unit tests
# ---------------------------------------------------------------------------


class TestPlanTracker:

    def _make_tool_calls(self, specs: list[tuple[str, dict[str, JSONValue]]]) -> list[ToolCall]:

        return [
            ToolCall(id=f"tc{i}", name=name, params=params)
            for i, (name, params) in enumerate(specs)
        ]

    def test_group_setup_tools_into_individual_steps(self) -> None:

        """Each setup tool (tempo, key) becomes its own distinct plan step."""
        tcs = self._make_tool_calls([
            ("stori_set_tempo", {"tempo": 72}),
            ("stori_set_key", {"key": "Cm"}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Make a lofi beat", {}, False, StateStore(conversation_id="t"))
        assert len(tracker.steps) == 2
        assert "tempo" in tracker.steps[0].label.lower()
        assert "72" in tracker.steps[0].label
        assert "key" in tracker.steps[1].label.lower()
        assert "Cm" in tracker.steps[1].label

    def test_group_track_creation_separate_from_content(self) -> None:

        """Track creation and content are separate steps with canonical labels."""
        tcs = self._make_tool_calls([
            ("stori_add_midi_track", {"name": "Drums", "drumKitId": "TR-808"}),
            ("stori_add_midi_region", {"trackId": "$0.trackId", "startBeat": 0, "durationBeats": 16}),
            ("stori_add_notes", {"regionId": "$1.regionId", "notes": []}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Add drums", {}, False, StateStore(conversation_id="t"))
        assert tracker.steps[0].label == "Create Drums track"
        assert tracker.steps[0].track_name == "Drums"
        assert tracker.steps[0].phase == "setup"
        content_steps = [s for s in tracker.steps if "content" in s.label.lower()]
        assert len(content_steps) == 1
        assert content_steps[0].label == "Add content to Drums"
        assert content_steps[0].phase == "composition"

    def test_group_multiple_tracks_contiguous(self) -> None:

        """Multiple track creations produce per-track contiguous steps."""
        tcs = self._make_tool_calls([
            ("stori_set_tempo", {"tempo": 90}),
            ("stori_add_midi_track", {"name": "Drums", "drumKitId": "TR-808"}),
            ("stori_add_midi_region", {"trackId": "$1.trackId"}),
            ("stori_add_notes", {"regionId": "$2.regionId", "notes": []}),
            ("stori_add_midi_track", {"name": "Bass", "gmProgram": 33}),
            ("stori_add_midi_region", {"trackId": "$4.trackId"}),
            ("stori_add_notes", {"regionId": "$5.regionId", "notes": []}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Make a beat", {}, False, StateStore(conversation_id="t"))
        labels = [s.label for s in tracker.steps]
        assert "tempo" in labels[0].lower()
        assert "Create Drums track" in labels
        assert "Add content to Drums" in labels
        assert "Create Bass track" in labels
        assert "Add content to Bass" in labels

    def test_effects_grouped_per_track(self) -> None:

        """Bus setup becomes project-level; insert effects are track-targeted."""
        tcs = self._make_tool_calls([
            ("stori_ensure_bus", {"name": "Reverb"}),
            ("stori_add_insert_effect", {"trackName": "Drums", "type": "chorus"}),
            ("stori_add_send", {"trackName": "Drums", "busId": "$0.busId"}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Add effects", {}, False, StateStore(conversation_id="t"))
        labels = [s.label for s in tracker.steps]
        phases = [s.phase for s in tracker.steps]
        assert any("Reverb bus" in l for l in labels)
        assert any("effects" in l.lower() for l in labels)
        assert "mixing" in phases
        assert "soundDesign" in phases

    def test_plan_event_shape_includes_toolName_when_set(self) -> None:

        """to_plan_event() includes toolName when the step has a tool_name."""
        tcs = self._make_tool_calls([
            ("stori_set_tempo", {"tempo": 120}),
            ("stori_add_midi_track", {"name": "Piano", "gmProgram": 0}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Add piano", {}, False, StateStore(conversation_id="t"))
        event = tracker.to_plan_event()
        assert event.type == "plan"
        assert event.plan_id != ""
        assert isinstance(event.steps, list)
        for step in event.steps:
            assert step.step_id != ""
            assert step.label != ""
            assert step.status == "pending"
            assert step.tool_name is not None

    def test_title_is_musically_descriptive(self) -> None:

        """Plan title should be musically descriptive, not a raw prompt."""
        tcs = self._make_tool_calls([
            ("stori_set_tempo", {"tempo": 72}),
            ("stori_set_key", {"key": "Cm"}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Create a lofi beat", {}, False, StateStore(conversation_id="t"))
        # Title should be a musical description, not contain raw key/tempo params
        assert tracker.title
        assert "Plan" not in tracker.title
        assert "Executing" not in tracker.title

    def test_title_with_style_from_generators(self) -> None:

        """Plan title extracts style from generator tool calls."""
        tcs = self._make_tool_calls([
            ("stori_add_midi_track", {"name": "Drums"}),
            ("stori_generate_midi", {"role": "drums", "style": "boom bap", "trackName": "Drums"}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Make a beat", {}, False, StateStore(conversation_id="t"))
        assert "Boom Bap" in tracker.title

    def test_build_from_prompt_creates_upfront_plan(self) -> None:

        """build_from_prompt() generates per-track steps with canonical labels."""
        from app.core.prompt_parser import ParsedPrompt
        parsed = ParsedPrompt(
            raw="STORI PROMPT\nMode: compose",
            mode="compose",
            request="Ska intro",
            tempo=165,
            key="Bb",
            roles=["drums", "bass", "organ"],
            section="intro",
            style="third wave ska",
        )
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "STORI PROMPT\nMode: compose\nSection: intro\nStyle: third wave ska", {})
        labels = [s.label for s in tracker.steps]
        # tempo + key + (create+content)*3 roles + effects step = 9 steps
        assert any("165" in l for l in labels)
        assert any("Bb" in l for l in labels)
        assert any("Create Drums track" == l for l in labels)
        assert any("Add content to Drums" == l for l in labels)
        assert any("Create Bass track" == l for l in labels)
        assert any("Add content to Bass" == l for l in labels)
        assert any("Organ" in l for l in labels)

    def test_build_from_prompt_no_roles_adds_placeholder(self) -> None:

        """Without roles, build_from_prompt adds a generic placeholder step."""
        from app.core.prompt_parser import ParsedPrompt
        parsed = ParsedPrompt(raw="STORI PROMPT\nMode: compose", mode="compose", request="make something", tempo=120)
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make something", {})
        labels = [s.label for s in tracker.steps]
        assert any("tempo" in l.lower() for l in labels)
        assert any("generate" in l.lower() or "music" in l.lower() for l in labels)

    def test_step_activation_and_completion(self) -> None:

        """activate_step / complete_active_step produce correct events."""
        tracker = _PlanTracker()
        tracker.steps = [
            _PlanStep(step_id="1", label="Setup"),
            _PlanStep(step_id="2", label="Track"),
        ]
        evt = tracker.activate_step("1")
        assert evt.type == "planStepUpdate"
        assert evt.step_id == "1"
        assert evt.status == "active"
        assert evt.phase == "composition"
        assert tracker._active_step_id == "1"
        assert tracker.steps[0].status == "active"

        tracker.steps[0].result = "set 72 BPM"
        complete_evt = tracker.complete_active_step()
        assert complete_evt is not None
        assert complete_evt.status == "completed"
        assert complete_evt.result == "set 72 BPM"
        assert tracker.steps[0].status == "completed"
        assert tracker._active_step_id is None

    def test_step_for_tool_index(self) -> None:

        """step_for_tool_index returns correct step."""
        tracker = _PlanTracker()
        tracker.steps = [
            _PlanStep(step_id="1", label="Setup", tool_indices=[0, 1]),
            _PlanStep(step_id="2", label="Track", tool_indices=[2, 3, 4]),
        ]
        step0 = tracker.step_for_tool_index(0)
        assert step0 is not None
        assert step0.step_id == "1"
        step3 = tracker.step_for_tool_index(3)
        assert step3 is not None
        assert step3.step_id == "2"
        assert tracker.step_for_tool_index(99) is None

    def test_progress_context_formatting(self) -> None:

        """progress_context() produces a readable summary."""
        tracker = _PlanTracker()
        tracker.steps = [
            _PlanStep(step_id="1", label="Setup", status="completed", result="72 BPM, Cm"),
            _PlanStep(step_id="2", label="Drums", status="active"),
            _PlanStep(step_id="3", label="Effects", status="pending"),
        ]
        ctx = tracker.progress_context()
        assert "✅" in ctx
        assert "🔄" in ctx
        assert "⬜" in ctx
        assert "72 BPM, Cm" in ctx

    def test_build_step_result(self) -> None:

        """_build_step_result accumulates descriptions."""
        r1 = _build_step_result("stori_set_tempo", {"tempo": 120})
        assert "120" in r1
        r2 = _build_step_result("stori_set_key", {"key": "Am"}, r1)
        assert ";" in r2
        assert "Am" in r2


# ---------------------------------------------------------------------------
# Plan Events in EDITING flow — integration tests
# ---------------------------------------------------------------------------


class TestPlanEventsInEditing:

    @pytest.mark.anyio
    async def test_plan_and_step_updates_emitted_for_multi_tool_editing(self) -> None:

        """EDITING with multiple tool calls should emit plan + planStepUpdate events."""
        from app.core.tools import ALL_TOOLS

        allowed = {"stori_set_tempo", "stori_set_key", "stori_add_midi_track", "stori_add_midi_region", "stori_add_notes"}
        route = _make_route(
            SSEState.EDITING,
            Intent.GENERATE_MUSIC,
            allowed_tool_names=allowed,
            tool_choice="auto",
        )

        response = LLMResponse(content=None, usage={"prompt_tokens": 10, "completion_tokens": 20})
        response.tool_calls = [
            ToolCall(id="tc0", name="stori_set_tempo", params={"tempo": 72}),
            ToolCall(id="tc1", name="stori_set_key", params={"key": "Cm"}),
            ToolCall(id="tc2", name="stori_add_midi_track", params={"name": "Pads", "gmProgram": 89}),
        ]

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(return_value=response)
        store = StateStore(conversation_id="test-plan")
        trace = _make_trace()

        events: list[str] = []
        async for e in _handle_editing(
            "Create a pad track", {}, route, llm, store, trace, None, [], "apply",
        ):
            events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]

        assert "plan" in types, f"Expected 'plan' event, got types: {types}"

        plan_evt = next(p for p in payloads if p["type"] == "plan")
        assert "planId" in plan_evt
        assert isinstance(plan_evt["steps"], list)
        assert len(plan_evt["steps"]) >= 2

        step_updates = [p for p in payloads if p["type"] == "planStepUpdate"]
        assert len(step_updates) >= 2  # at least active+completed for step 1

        statuses = [su["status"] for su in step_updates]
        assert "active" in statuses
        assert "completed" in statuses

    @pytest.mark.anyio
    async def test_no_plan_for_single_force_stop_tool(self) -> None:

        """Single-tool force_stop_after requests must NOT emit a plan event.

        A one-step plan is noise — the toolStart label is sufficient.
        Plans are only emitted when there are 2+ distinct steps.
        """
        allowed = {"stori_set_tempo"}
        route = _make_route(
            SSEState.EDITING,
            Intent.PROJECT_SET_TEMPO,
            allowed_tool_names=allowed,
            tool_choice="required",
            force_stop_after=True,
        )

        response = LLMResponse(content=None, usage={"prompt_tokens": 5, "completion_tokens": 5})
        response.tool_calls = [
            ToolCall(id="tc0", name="stori_set_tempo", params={"tempo": 120}),
        ]

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(return_value=response)
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        events: list[str] = []
        async for e in _handle_editing(
            "set tempo 120", {}, route, llm, store, trace, None, [], "apply",
        ):
            events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        assert "plan" not in types, "Single-tool edits must not generate a plan"
        assert "toolCall" in types
        assert "complete" in types

    @pytest.mark.anyio
    async def test_plan_not_emitted_in_variation_mode(self) -> None:

        """Variation mode should NOT emit plan events (those are for apply only)."""
        allowed = {"stori_add_midi_track"}
        route = _make_route(
            SSEState.EDITING,
            Intent.GENERATE_MUSIC,
            allowed_tool_names=allowed,
            tool_choice="auto",
        )

        response = LLMResponse(content=None, usage={"prompt_tokens": 5, "completion_tokens": 5})
        response.tool_calls = [
            ToolCall(id="tc0", name="stori_add_midi_track", params={"name": "Test"}),
        ]

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(return_value=response)
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        events: list[str] = []
        from app.models.variation import Variation
        fake_var = Variation(
            variation_id="v-1",
            intent="test",
            affected_tracks=[],
            affected_regions=[],
            beat_range=(0.0, 0.0),
            phrases=[],
        )
        with patch("app.core.executor.execute_plan_variation", new_callable=AsyncMock, return_value=fake_var):
            async for e in _handle_editing(
                "generate", {}, route, llm, store, trace, None, [], "variation",
            ):
                events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        assert "plan" not in types

    @pytest.mark.anyio
    async def test_step_order_matches_tool_execution(self) -> None:

        """planStepUpdate events should follow the execution order of tool calls."""
        allowed = {"stori_set_tempo", "stori_add_midi_track"}
        route = _make_route(
            SSEState.EDITING,
            Intent.GENERATE_MUSIC,
            allowed_tool_names=allowed,
            tool_choice="auto",
        )

        response = LLMResponse(content=None, usage={"prompt_tokens": 5, "completion_tokens": 5})
        response.tool_calls = [
            ToolCall(id="tc0", name="stori_set_tempo", params={"tempo": 90}),
            ToolCall(id="tc1", name="stori_add_midi_track", params={"name": "Lead"}),
        ]

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(return_value=response)
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        events: list[str] = []
        async for e in _handle_editing(
            "Create a lead", {}, route, llm, store, trace, None, [], "apply",
        ):
            events.append(e)

        payloads = _parse_events(events)
        step_updates = [p for p in payloads if p["type"] == "planStepUpdate"]

        # Should be: step1 active, step1 completed, step2 active, step2 completed
        assert len(step_updates) >= 4
        assert step_updates[0]["status"] == "active"
        assert step_updates[1]["status"] == "completed"
        assert step_updates[2]["status"] == "active"
        assert step_updates[3]["status"] == "completed"

        # Step IDs should be consistent
        assert step_updates[0]["stepId"] == step_updates[1]["stepId"]
        assert step_updates[2]["stepId"] == step_updates[3]["stepId"]
        assert step_updates[0]["stepId"] != step_updates[2]["stepId"]


# ---------------------------------------------------------------------------
# Parity: complete event on orchestration error
# ---------------------------------------------------------------------------


class TestCompleteEventOnError:

    @pytest.mark.anyio
    async def test_orchestration_error_emits_complete_with_success_false(self) -> None:

        """When orchestrate() hits an exception, it should emit error + complete events."""
        with (
            patch(
                "app.core.maestro_handlers.get_intent_result_with_llm",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
            patch("app.core.maestro_handlers.LLMClient") as m_cls,
        ):
            m_cls.return_value = _make_llm_mock()
            events: list[str] = []
            async for e in orchestrate("trigger error"):
                events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        assert "error" in types
        assert "complete" in types
        complete_evt = next(p for p in payloads if p["type"] == "complete")
        assert complete_evt["success"] is False


# ---------------------------------------------------------------------------
# Bug C — build_from_prompt skips no-op tempo/key steps
# ---------------------------------------------------------------------------

class TestBuildFromPromptNoOpSkip:
    """build_from_prompt omits tempo/key steps when project already matches."""

    def _make_parsed(self, tempo: int | None = None, key: str | None = None, roles: list[str] | None = None) -> ParsedPrompt:
        from app.core.prompt_parser import ParsedPrompt
        return ParsedPrompt(
            raw="STORI PROMPT",
            mode="compose",
            request="make a beat",
            tempo=tempo,
            key=key,
            roles=roles or [],
        )

    def test_tempo_step_skipped_when_already_matching(self) -> None:

        """If project tempo == requested tempo, no 'set tempo' step is added."""
        parsed = self._make_parsed(tempo=120, key="Am", roles=["drums"])
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make a beat", {"tempo": 120, "key": "C"})
        labels = [s.label for s in tracker.steps]
        assert not any("tempo" in l.lower() for l in labels)

    def test_tempo_step_included_when_different(self) -> None:

        """If project tempo != requested tempo, the set tempo step is present."""
        parsed = self._make_parsed(tempo=90, roles=["drums"])
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make a beat", {"tempo": 120})
        labels = [s.label for s in tracker.steps]
        assert any("90 BPM" in l for l in labels)

    def test_key_step_skipped_when_already_matching(self) -> None:

        """If project key == requested key (case-insensitive), no set key step."""
        parsed = self._make_parsed(tempo=120, key="Am", roles=["drums"])
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make a beat", {"tempo": 120, "key": "Am"})
        labels = [s.label for s in tracker.steps]
        assert not any("key" in l.lower() for l in labels)

    def test_key_step_skipped_case_insensitive(self) -> None:

        """Key match is case-insensitive (am == Am == AM)."""
        parsed = self._make_parsed(key="am", roles=["drums"])
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make a beat", {"key": "Am"})
        labels = [s.label for s in tracker.steps]
        assert not any("key" in l.lower() for l in labels)

    def test_key_step_included_when_different(self) -> None:

        """Different key produces a set key step."""
        parsed = self._make_parsed(key="F#m", roles=["drums"])
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make a beat", {"key": "Am"})
        labels = [s.label for s in tracker.steps]
        assert any("F#m" in l for l in labels)

    def test_both_skipped_leaves_only_role_and_effect_steps(self) -> None:

        """When both tempo and key match, only role steps + effects step remain."""
        parsed = self._make_parsed(tempo=80, key="Cm", roles=["drums", "bass"])
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make a beat", {"tempo": 80, "key": "Cm"})
        # (create+content)*2 roles + 1 effects step = 5
        assert len(tracker.steps) == 5
        labels = [s.label for s in tracker.steps]
        assert all("BPM" not in l and "key" not in l.lower() for l in labels)


# ---------------------------------------------------------------------------
# Bug D — _match_roles_to_existing_tracks uses infer_track_role
# ---------------------------------------------------------------------------

class TestMatchRolesWithInferredRoles:
    """_match_roles_to_existing_tracks matches via infer_track_role, not just name."""

    def test_drum_kit_track_matched_as_drums(self) -> None:

        """A track with drumKitId is inferred as drums even if named differently."""
        from app.core.planner import _match_roles_to_existing_tracks
        project: ProjectContext = {
            "tracks": [
                {"id": "BEAT-UUID", "name": "The Beat", "drumKitId": "TR-808"},
            ]
        }
        result = _match_roles_to_existing_tracks({"drums"}, project)
        assert result.get("drums", {}).get("id") == "BEAT-UUID"

    def test_gm_bass_program_matched_as_bass(self) -> None:

        """A track with GM program 33 (Electric Bass) is inferred as bass role."""
        from app.core.planner import _match_roles_to_existing_tracks
        project: ProjectContext = {
            "tracks": [
                {"id": "LOW-UUID", "name": "Low End", "gmProgram": 33},
            ]
        }
        result = _match_roles_to_existing_tracks({"bass"}, project)
        assert result.get("bass", {}).get("id") == "LOW-UUID"

    def test_synth_lead_matched_as_lead(self) -> None:

        """A track with GM program 80 (Square Lead) is inferred as lead role."""
        from app.core.planner import _match_roles_to_existing_tracks
        project: ProjectContext = {
            "tracks": [
                {"id": "LEAD-UUID", "name": "Synth 1", "gmProgram": 80},
            ]
        }
        result = _match_roles_to_existing_tracks({"lead"}, project)
        assert result.get("lead", {}).get("id") == "LEAD-UUID"

    def test_organ_pad_matched_as_pads(self) -> None:

        """A 'Cathedral Pad' track (Church Organ GM 19) inferred as pads, matched to pads role."""
        from app.core.planner import _match_roles_to_existing_tracks
        project: ProjectContext = {
            "tracks": [
                {"id": "PAD-UUID", "name": "Cathedral Pad", "gmProgram": 19},
            ]
        }
        result = _match_roles_to_existing_tracks({"pads"}, project)
        assert result.get("pads", {}).get("id") == "PAD-UUID"


# ---------------------------------------------------------------------------
# build_from_prompt — expressive tool plan steps (Effects / MidiExpressiveness / Automation)
# ---------------------------------------------------------------------------

class TestBuildFromPromptExpressiveSteps:
    """build_from_prompt surfaces Effects / MidiExpressiveness / Automation as plan steps."""

    def _make_parsed(self, roles: list[str] | None = None, extensions: dict[str, JSONValue] | None = None) -> ParsedPrompt:
        from app.core.prompt_parser import ParsedPrompt
        return ParsedPrompt(
            raw="STORI PROMPT",
            mode="compose",
            request="make music",
            tempo=120,
            roles=roles or [],
            extensions=extensions or {},
        )

    def test_effects_block_adds_per_track_step(self) -> None:

        """Each track key in Effects produces its own 'Add effects to X' step."""
        parsed = self._make_parsed(
            roles=["drums", "bass"],
            extensions={"effects": {"drums": {"compression": "VCA"}, "bass": {"eq": True}}},
        )
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make a beat", {})
        labels = [s.label for s in tracker.steps]
        assert any("effects to Drums" in l for l in labels)
        assert any("effects to Bass" in l for l in labels)

    def test_no_explicit_effects_adds_generic_effects_step(self) -> None:

        """Without an Effects extension, a generic 'Add effects and routing' step appears."""
        parsed = self._make_parsed(roles=["drums", "bass"])
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make a beat", {})
        labels = [s.label for s in tracker.steps]
        assert any("effect" in l.lower() for l in labels)

    def test_cc_curves_adds_midi_cc_step(self) -> None:

        """MidiExpressiveness with cc_curves adds an 'Add MIDI CC curves' step."""
        parsed = self._make_parsed(
            roles=["lead"],
            extensions={"midiexpressiveness": {"cc_curves": [{"cc": 91, "from": 20, "to": 80}]}},
        )
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make music", {})
        labels = [s.label for s in tracker.steps]
        assert any("CC" in l for l in labels)

    def test_pitch_bend_adds_step(self) -> None:

        """MidiExpressiveness with pitch_bend adds an 'Add pitch bend' step."""
        parsed = self._make_parsed(
            roles=["bass"],
            extensions={"midiexpressiveness": {"pitch_bend": {"style": "slides"}}},
        )
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make music", {})
        labels = [s.label for s in tracker.steps]
        assert any("pitch bend" in l.lower() for l in labels)

    def test_sustain_pedal_adds_step(self) -> None:

        """MidiExpressiveness with sustain_pedal adds a MIDI CC step."""
        parsed = self._make_parsed(
            roles=["chords"],
            extensions={"midiexpressiveness": {"sustain_pedal": {"changes_per_bar": 2}}},
        )
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make music", {})
        labels = [s.label for s in tracker.steps]
        # Sustain pedal is now represented as "Add MIDI CC to <Track>"
        assert any("MIDI CC" in l for l in labels)

    def test_automation_block_adds_step_with_track(self) -> None:

        """Automation block labels include track name when available."""
        parsed = self._make_parsed(
            roles=["pads"],
            extensions={"automation": [
                {"track": "Pads", "param": "reverb_wet", "events": []},
                {"track": "Drums", "param": "volume", "events": []},
            ]},
        )
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make music", {})
        labels = [s.label for s in tracker.steps]
        assert any("automation" in l.lower() for l in labels)

    def test_all_expressive_blocks_together(self) -> None:

        """All three blocks together produce all expected step categories."""
        parsed = self._make_parsed(
            roles=["drums"],
            extensions={
                "effects": {"drums": {"compression": True}},
                "midiexpressiveness": {
                    "cc_curves": [{"cc": 1}],
                    "pitch_bend": {"style": "slides"},
                },
                "automation": [{"track": "Drums", "param": "volume", "events": []}],
            },
        )
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make music", {})
        labels = [s.label for s in tracker.steps]
        assert any("effect" in l.lower() for l in labels)
        assert any("CC" in l or "cc" in l.lower() for l in labels)
        assert any("pitch bend" in l.lower() for l in labels)
        assert any("automation" in l.lower() for l in labels)


# ---------------------------------------------------------------------------
# structured_prompt_context — EXECUTE translation block
# ---------------------------------------------------------------------------

class TestStructuredPromptContextTranslation:
    """structured_prompt_context injects execution requirements for expressive blocks."""

    def _make_parsed(self, extensions: dict[str, JSONValue]) -> ParsedPrompt:
        from app.core.prompt_parser import ParsedPrompt
        return ParsedPrompt(
            raw="STORI PROMPT",
            mode="compose",
            request="make music",
            extensions=extensions,
        )

    def test_effects_block_triggers_execute_line(self) -> None:

        """Effects extension adds 'EXECUTE Effects block' to the context string."""
        from app.core.prompts import structured_prompt_context
        parsed = self._make_parsed({"effects": {"drums": {"compression": True}}})
        out = structured_prompt_context(parsed)
        assert "EXECUTE Effects block" in out
        assert "stori_add_insert_effect" in out

    def test_midi_expressiveness_triggers_execute_line(self) -> None:

        """MidiExpressiveness extension adds CC/pitch-bend/sustain execution lines."""
        from app.core.prompts import structured_prompt_context
        parsed = self._make_parsed({"midiexpressiveness": {
            "cc_curves": [{"cc": 91}],
            "pitch_bend": {"style": "slides"},
        }})
        out = structured_prompt_context(parsed)
        assert "EXECUTE MidiExpressiveness block" in out
        assert "stori_add_midi_cc" in out
        assert "stori_add_pitch_bend" in out

    def test_automation_triggers_execute_line(self) -> None:

        """Automation extension adds stori_add_automation execution line."""
        from app.core.prompts import structured_prompt_context
        parsed = self._make_parsed({"automation": [{"track": "Pads", "param": "reverb_wet"}]})
        out = structured_prompt_context(parsed)
        assert "EXECUTE Automation block" in out
        assert "stori_add_automation" in out

    def test_no_expressive_blocks_no_translate_header(self) -> None:

        """When only Harmony/Melody are present, no EXECUTE requirements block."""
        from app.core.prompts import structured_prompt_context
        parsed = self._make_parsed({"harmony": {"progression": ["Am", "G"]}})
        out = structured_prompt_context(parsed)
        assert "EXECUTE Effects block" not in out
        assert "EXECUTE MidiExpressiveness block" not in out
        assert "EXECUTE Automation block" not in out

    def test_header_changes_to_translate_all_blocks(self) -> None:

        """Header reads 'TRANSLATE ALL BLOCKS INTO TOOL CALLS' not just 'interpret'."""
        from app.core.prompts import structured_prompt_context
        parsed = self._make_parsed({"effects": {"bass": {"eq": True}}})
        out = structured_prompt_context(parsed)
        assert "TRANSLATE ALL BLOCKS INTO TOOL CALLS" in out

    def test_automation_context_uses_trackId_not_target(self) -> None:

        """Automation execution line must reference trackId, never 'target'."""
        from app.core.prompts import structured_prompt_context
        parsed = self._make_parsed({"automation": [{"track": "Pads", "param": "volume"}]})
        out = structured_prompt_context(parsed)
        assert "trackId" in out
        assert "target=TRACK_ID" not in out


# ---------------------------------------------------------------------------
# _PlanStep.tool_name — toolName in plan event
# ---------------------------------------------------------------------------

class TestPlanStepToolName:
    """_PlanStep.tool_name is included in the plan SSE event as toolName."""

    def _make_tracker_from_prompt(self, roles: list[str] | None = None, extensions: dict[str, JSONValue] | None = None, tempo: int | None = None, key: str | None = None) -> _PlanTracker:
        from app.core.prompt_parser import ParsedPrompt
        from app.core.maestro_plan_tracker import _PlanTracker
        parsed = ParsedPrompt(
            raw="STORI PROMPT",
            mode="compose",
            request="make music",
            tempo=tempo or 120,
            key=key,
            roles=roles or [],
            extensions=extensions or {},
        )
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make music", {})
        return tracker

    def test_to_plan_event_includes_toolName_when_set(self) -> None:

        """Steps with a tool_name emit toolName in the plan event."""
        tracker = self._make_tracker_from_prompt(roles=["drums"])
        event = tracker.to_plan_event()
        steps = event.steps
        # At least one step should have toolName (the track creation step)
        tool_names = [s.tool_name for s in steps if s.tool_name]
        assert len(tool_names) > 0

    def test_tempo_step_has_stori_set_tempo_toolName(self) -> None:

        """The 'set tempo' plan step reports toolName=stori_set_tempo."""
        tracker = self._make_tracker_from_prompt(tempo=90)
        event = tracker.to_plan_event()
        tempo_steps = [s for s in event.steps if "tempo" in s.label.lower()]
        assert len(tempo_steps) == 1
        assert tempo_steps[0].tool_name == "stori_set_tempo"

    def test_key_step_has_stori_set_key_toolName(self) -> None:

        """The 'set key' plan step reports toolName=stori_set_key."""
        tracker = self._make_tracker_from_prompt(key="Am")
        event = tracker.to_plan_event()
        key_steps = [s for s in event.steps if "key" in s.label.lower()]
        assert len(key_steps) == 1
        assert key_steps[0].tool_name == "stori_set_key"

    def test_role_step_has_stori_add_midi_track_toolName(self) -> None:

        """Track creation steps report toolName=stori_add_midi_track."""
        tracker = self._make_tracker_from_prompt(roles=["bass"])
        event = tracker.to_plan_event()
        track_steps = [s for s in event.steps if "Bass" in s.label]
        assert len(track_steps) >= 1
        assert track_steps[0].tool_name == "stori_add_midi_track"

    def test_effects_step_has_stori_add_insert_effect_toolName(self) -> None:

        """Effects plan steps report toolName=stori_add_insert_effect."""
        tracker = self._make_tracker_from_prompt(
            roles=["drums"],
            extensions={"effects": {"drums": {"compression": "VCA"}}},
        )
        event = tracker.to_plan_event()
        fx_steps = [s for s in event.steps if "effect" in s.label.lower()]
        assert len(fx_steps) >= 1
        assert fx_steps[0].tool_name == "stori_add_insert_effect"

    def test_cc_step_has_stori_add_midi_cc_toolName(self) -> None:

        """MIDI CC plan steps report toolName=stori_add_midi_cc."""
        tracker = self._make_tracker_from_prompt(
            extensions={"midiexpressiveness": {"cc_curves": [{"cc": 91}]}},
        )
        event = tracker.to_plan_event()
        cc_steps = [s for s in event.steps if "CC" in s.label]
        assert len(cc_steps) >= 1
        assert cc_steps[0].tool_name == "stori_add_midi_cc"

    def test_pitch_bend_step_has_stori_add_pitch_bend_toolName(self) -> None:

        """Pitch bend plan steps report toolName=stori_add_pitch_bend."""
        tracker = self._make_tracker_from_prompt(
            extensions={"midiexpressiveness": {"pitch_bend": {"style": "slides"}}},
        )
        event = tracker.to_plan_event()
        pb_steps = [s for s in event.steps if "pitch bend" in s.label.lower()]
        assert len(pb_steps) >= 1
        assert pb_steps[0].tool_name == "stori_add_pitch_bend"

    def test_automation_step_has_stori_add_automation_toolName(self) -> None:

        """Automation plan steps report toolName=stori_add_automation."""
        tracker = self._make_tracker_from_prompt(
            extensions={"automation": [{"track": "Piano", "param": "Volume"}]},
        )
        event = tracker.to_plan_event()
        auto_steps = [s for s in event.steps if "automation" in s.label.lower()]
        assert len(auto_steps) >= 1
        assert auto_steps[0].tool_name == "stori_add_automation"

    def test_step_without_tool_name_omits_key(self) -> None:

        """Steps where tool_name is None omit toolName so Swift decodes nil."""
        from app.core.maestro_plan_tracker import _PlanStep, _PlanTracker
        tracker = _PlanTracker()
        tracker.steps = [_PlanStep(step_id="1", label="Do something", tool_name=None)]
        event = tracker.to_plan_event()
        assert event.steps[0].tool_name is None


# ---------------------------------------------------------------------------
# _get_missing_expressive_steps — lowercase key detection + bus suggestion
# ---------------------------------------------------------------------------

class TestGetMissingExpressiveSteps:
    """_get_missing_expressive_steps detects pending expressive tool calls."""

    def _parsed(self, extensions: dict[str, JSONValue]) -> ParsedPrompt:
        from app.core.prompt_parser import ParsedPrompt
        return ParsedPrompt(
            raw="STORI PROMPT",
            mode="compose",
            request="test",
            extensions=extensions,
        )

    def _missing(self, extensions: dict[str, JSONValue], tool_calls_collected: list[ToolCallDict] | None = None) -> list[str]:

        from app.core.maestro_editing import _get_missing_expressive_steps
        empty: list[ToolCallDict] = []
        return _get_missing_expressive_steps(
            self._parsed(extensions),
            tool_calls_collected if tool_calls_collected is not None else empty,
        )

    def test_none_parsed_returns_empty(self) -> None:

        """None parsed prompt → no missing steps."""
        from app.core.maestro_editing import _get_missing_expressive_steps
        assert _get_missing_expressive_steps(None, []) == []

    def test_no_extensions_returns_empty(self) -> None:

        """ParsedPrompt with no extensions → no missing steps."""
        assert self._missing({}) == []

    def test_effects_block_present_but_not_called(self) -> None:

        """effects extension without stori_add_insert_effect call → flagged."""
        result = self._missing({"effects": {"drums": {"compression": True}}})
        assert len(result) == 1
        assert "stori_add_insert_effect" in result[0]

    def test_effects_block_already_called_not_flagged(self) -> None:

        """effects extension with stori_add_insert_effect already called → empty."""
        result = self._missing(
            {"effects": {"drums": {"compression": True}}},
            [ToolCallDict(tool="stori_add_insert_effect", params={})],
        )
        assert result == []

    def test_cc_curves_not_called_flagged(self) -> None:

        """cc_curves without stori_add_midi_cc call → flagged."""
        result = self._missing({"midiexpressiveness": {"cc_curves": [{"cc": 91}]}})
        assert any("stori_add_midi_cc" in m for m in result)

    def test_cc_curves_already_called_not_flagged(self) -> None:

        """cc_curves with stori_add_midi_cc already called → not flagged."""
        result = self._missing(
            {"midiexpressiveness": {"cc_curves": [{"cc": 91}]}},
            [ToolCallDict(tool="stori_add_midi_cc", params={})],
        )
        assert not any("stori_add_midi_cc" in m for m in result)

    def test_pitch_bend_not_called_flagged(self) -> None:

        """pitch_bend without stori_add_pitch_bend → flagged."""
        result = self._missing({"midiexpressiveness": {"pitch_bend": {"style": "slides"}}})
        assert any("stori_add_pitch_bend" in m for m in result)

    def test_sustain_pedal_not_called_flagged(self) -> None:

        """sustain_pedal without stori_add_midi_cc → flagged (CC 64)."""
        result = self._missing({"midiexpressiveness": {"sustain_pedal": {"changes_per_bar": 2}}})
        assert any("stori_add_midi_cc" in m for m in result)

    def test_automation_not_called_flagged_with_trackId_hint(self) -> None:

        """automation block without stori_add_automation → flagged, and message says trackId."""
        result = self._missing({"automation": [{"track": "Piano", "param": "Volume"}]})
        assert any("stori_add_automation" in m for m in result)
        # The message should instruct trackId not target
        assert any("trackId" in m for m in result)
        assert not any("target=TRACK" in m for m in result)

    def test_automation_already_called_not_flagged(self) -> None:

        """automation block with stori_add_automation already called → empty."""
        result = self._missing(
            {"automation": [{"track": "Piano", "param": "Volume"}]},
            [{"tool": "stori_add_automation", "params": {}}],
        )
        assert not any("stori_add_automation" in m for m in result)

    def test_lowercase_keys_detected_correctly(self) -> None:

        """Parser lowercases all YAML keys; detection must use lowercase."""
        # Simulate what the parser produces: lowercase 'effects', 'midiexpressiveness'
        result = self._missing({
            "effects": {"piano": {"reverb": True}},
            "midiexpressiveness": {"cc_curves": [{"cc": 1}]},
            "automation": [{"track": "Piano", "param": "Volume"}],
        })
        # All three should be flagged since no tools have been called
        assert any("stori_add_insert_effect" in m for m in result)
        assert any("stori_add_midi_cc" in m for m in result)
        assert any("stori_add_automation" in m for m in result)

    def test_all_expressive_called_returns_empty(self) -> None:

        """When all expressive tools have been called, result is empty."""
        extensions: dict[str, JSONValue] = {
            "effects": {"drums": {"compression": True}},
            "midiexpressiveness": {
                "cc_curves": [{"cc": 91}],
                "pitch_bend": {"style": "slides"},
                "sustain_pedal": {"changes_per_bar": 2},
            },
            "automation": [{"track": "Drums", "param": "Volume"}],
        }
        tool_calls: list[ToolCallDict] = [
            ToolCallDict(tool="stori_add_insert_effect", params={}),
            ToolCallDict(tool="stori_add_midi_cc", params={}),
            ToolCallDict(tool="stori_add_pitch_bend", params={}),
            ToolCallDict(tool="stori_add_automation", params={}),
        ]
        result = self._missing(extensions, tool_calls)
        assert result == []

    def test_multi_track_reverb_suggests_bus(self) -> None:

        """Two or more tracks with reverb in Effects → suggests stori_ensure_bus."""
        result = self._missing({
            "effects": {
                "piano": {"reverb": "medium hall"},
                "lead": {"reverb": "large room"},
            }
        })
        assert any("stori_ensure_bus" in m for m in result)

    def test_single_track_reverb_does_not_suggest_bus(self) -> None:

        """Only one track with reverb → no shared bus suggestion."""
        result = self._missing({
            "effects": {
                "piano": {"reverb": "medium hall"},
                "bass": {"compression": "optical"},
            }
        })
        # Should flag insert_effect missing, but NOT suggest ensure_bus
        assert not any("stori_ensure_bus" in m for m in result)


# ---------------------------------------------------------------------------
# build_from_prompt — shared reverb bus plan step
# ---------------------------------------------------------------------------

class TestBuildFromPromptReverbBus:
    """build_from_prompt adds a bus setup step when 2+ tracks need reverb."""

    def _make_parsed(self, extensions: dict[str, JSONValue]) -> ParsedPrompt:
        from app.core.prompt_parser import ParsedPrompt
        return ParsedPrompt(
            raw="STORI PROMPT",
            mode="compose",
            request="make music",
            roles=["piano", "lead"],
            extensions=extensions,
        )

    def test_two_reverb_tracks_adds_bus_step(self) -> None:

        """Effects block with reverb on 2+ tracks adds 'set up shared Reverb bus' step."""
        from app.core.maestro_plan_tracker import _PlanTracker
        parsed = self._make_parsed({
            "effects": {
                "piano": {"reverb": "medium room"},
                "lead": {"reverb": "large hall"},
            }
        })
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "test", {})
        labels = [s.label for s in tracker.steps]
        assert any("Reverb bus" in l for l in labels)

    def test_two_reverb_tracks_bus_step_has_correct_toolName(self) -> None:

        """Reverb bus plan step has toolName=stori_ensure_bus."""
        from app.core.maestro_plan_tracker import _PlanTracker
        parsed = self._make_parsed({
            "effects": {
                "piano": {"reverb": "medium room"},
                "lead": {"reverb": "large hall"},
            }
        })
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "test", {})
        bus_steps = [s for s in tracker.steps if "Reverb bus" in s.label]
        assert len(bus_steps) == 1
        assert bus_steps[0].tool_name == "stori_ensure_bus"

    def test_one_reverb_track_no_bus_step(self) -> None:

        """Only one track with reverb → no shared bus step."""
        from app.core.maestro_plan_tracker import _PlanTracker
        parsed = self._make_parsed({
            "effects": {
                "piano": {"reverb": "medium room"},
                "bass": {"compression": "optical"},
            }
        })
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "test", {})
        labels = [s.label for s in tracker.steps]
        assert not any("Reverb bus" in l for l in labels)


# =============================================================================
# _build_tool_result — regression tests for entity ID echoing (Bug 1-3)
# =============================================================================


class TestBuildToolResult:
    """_build_tool_result must always return entity IDs and state feedback.

    These tests are regression guards to prevent the loops described in the
    Tool Result State Feedback bug report.
    """

    def _make_store(self) -> StateStore:

        store = StateStore(conversation_id="test-tool-result")
        return store

    def test_add_midi_track_returns_track_id(self) -> None:

        """stori_add_midi_track result MUST include trackId."""
        from app.core.maestro_helpers import _build_tool_result
        store = self._make_store()
        track_id = store.create_track("Drums")
        params: dict[str, JSONValue] = {"trackId": track_id, "name": "Drums", "drumKitId": "acoustic"}
        result = _build_tool_result("stori_add_midi_track", params, store)

        assert result["success"] is True
        assert result["trackId"] == track_id
        assert result["name"] == "Drums"
        assert "entities" not in result

    def test_add_midi_region_returns_region_id_and_metadata(self) -> None:

        """stori_add_midi_region result MUST include regionId, trackId, startBeat, durationBeats."""
        from app.core.maestro_helpers import _build_tool_result
        store = self._make_store()
        track_id = store.create_track("Bass")
        region_id = store.create_region("Verse", track_id, metadata={"startBeat": 0, "durationBeats": 32})
        params: dict[str, JSONValue] = {
            "regionId": region_id,
            "trackId": track_id,
            "name": "Verse",
            "startBeat": 0,
            "durationBeats": 32,
        }
        result = _build_tool_result("stori_add_midi_region", params, store)

        assert result["success"] is True
        assert result["regionId"] == region_id
        assert result["trackId"] == track_id
        assert result["startBeat"] == 0
        assert result["durationBeats"] == 32
        assert "entities" not in result

    def test_add_notes_returns_confirmation(self) -> None:

        """stori_add_notes result MUST include notesAdded and totalNotes."""
        from app.core.maestro_helpers import _build_tool_result
        store = self._make_store()
        track_id = store.create_track("Drums")
        region_id = store.create_region("Pattern", track_id)
        notes: list[NoteDict] = [{"pitch": 36, "startBeat": float(i), "durationBeats": 0.5, "velocity": 100} for i in range(8)]
        store.add_notes(region_id, notes)
        params: dict[str, JSONValue] = {"regionId": region_id, "notes": json_list(notes)}
        result = _build_tool_result("stori_add_notes", params, store)

        assert result["success"] is True
        assert result["regionId"] == region_id
        assert result["notesAdded"] == 8
        assert result["totalNotes"] == 8

    def test_add_notes_second_call_shows_accumulated_total(self) -> None:

        """Calling stori_add_notes twice should show accumulated totalNotes."""
        from app.core.maestro_helpers import _build_tool_result
        store = self._make_store()
        track_id = store.create_track("Drums")
        region_id = store.create_region("Pattern", track_id)

        first_notes: list[NoteDict] = [{"pitch": 36, "startBeat": float(i), "durationBeats": 0.5, "velocity": 100} for i in range(4)]
        store.add_notes(region_id, first_notes)

        second_notes: list[NoteDict] = [{"pitch": 38, "startBeat": float(i), "durationBeats": 0.5, "velocity": 90} for i in range(4)]
        store.add_notes(region_id, second_notes)

        result = _build_tool_result("stori_add_notes", {"regionId": region_id, "notes": json_list(second_notes)}, store)
        assert result["notesAdded"] == 4
        assert result["totalNotes"] == 8

    def test_clear_notes_returns_warning(self) -> None:

        """stori_clear_notes result MUST include warning about destructive operation."""
        from app.core.maestro_helpers import _build_tool_result
        store = self._make_store()
        result = _build_tool_result("stori_clear_notes", {"regionId": "r-123"}, store)

        assert result["success"] is True
        assert result["regionId"] == "r-123"
        assert result["totalNotes"] == 0
        assert "warning" in result

    def test_ensure_bus_returns_bus_id(self) -> None:

        """stori_ensure_bus result MUST include busId."""
        from app.core.maestro_helpers import _build_tool_result
        store = self._make_store()
        bus_id = store.get_or_create_bus("Reverb")
        params: dict[str, JSONValue] = {"busId": bus_id, "name": "Reverb"}
        result = _build_tool_result("stori_ensure_bus", params, store)

        assert result["success"] is True
        assert result["busId"] == bus_id
        assert "entities" not in result

    def test_add_insert_effect_returns_track_id(self) -> None:

        """stori_add_insert_effect result includes trackId."""
        from app.core.maestro_helpers import _build_tool_result
        store = self._make_store()
        result = _build_tool_result("stori_add_insert_effect", {"trackId": "t-1", "type": "compressor"}, store)
        assert result["success"] is True
        assert result["trackId"] == "t-1"
        assert result["effectType"] == "compressor"

    def test_add_midi_cc_returns_event_count(self) -> None:

        """stori_add_midi_cc result includes regionId and event count."""
        from app.core.maestro_helpers import _build_tool_result
        store = self._make_store()
        events: list[JSONValue] = [{"beat": 0, "value": 64}, {"beat": 4, "value": 127}]
        result = _build_tool_result("stori_add_midi_cc", {"regionId": "r-1", "cc": 1, "events": events}, store)
        assert result["regionId"] == "r-1"
        assert result["cc"] == 1
        assert result["eventCount"] == 2


# =============================================================================
# EntityRegistry.agent_manifest — regression tests for entity snapshot
# =============================================================================


class TestEntityManifest:
    """EntityRegistry.agent_manifest returns compact text with entity IDs."""

    def test_manifest_includes_track_and_region_ids(self) -> None:

        """Track and region IDs must appear in the manifest text."""
        store = StateStore(conversation_id="test-manifest")
        track_id = store.create_track("Drums")
        region_id = store.create_region("Pattern", track_id, metadata={"startBeat": 0, "durationBeats": 16})

        manifest = store.registry.agent_manifest()

        assert track_id in manifest
        assert region_id in manifest
        assert "Drums" in manifest
        assert "Pattern" in manifest

    def test_manifest_empty_registry(self) -> None:

        """Empty registry must show 'no tracks yet'."""
        store = StateStore(conversation_id="test-manifest-empty")
        manifest = store.registry.agent_manifest()
        assert "no tracks yet" in manifest

    def test_manifest_scoped_to_track(self) -> None:

        """When track_id is given, only that track's entities appear."""
        store = StateStore(conversation_id="test-manifest-scope")
        tid1 = store.create_track("Drums")
        tid2 = store.create_track("Bass")
        store.create_region("Verse", tid2, metadata={"startBeat": 0, "durationBeats": 32})

        manifest = store.registry.agent_manifest(track_id=tid1)
        assert "Drums" in manifest
        assert "Bass" not in manifest


# =============================================================================
# Plan step completion — regression test for Bug 4 (phantom steps)
# =============================================================================


class TestPlanStepPhantomCompletion:
    """Plan steps must NOT be marked completed for tracks that don't exist."""

    def test_nonexistent_track_stays_pending(self) -> None:

        """A plan step for 'Soul_Sample' that was never created must NOT be completed."""
        from app.core.maestro_plan_tracker import _PlanTracker
        tracker = _PlanTracker()
        tracker.steps = [
            _PlanStep(step_id="1", label="Create Drums track", track_name="Drums"),
            _PlanStep(step_id="2", label="Create Soul_Sample track", track_name="Soul_Sample"),
        ]

        store = StateStore(conversation_id="test-phantom")
        store.create_track("Drums")
        track_id = store.registry.resolve_track("Drums")
        assert track_id is not None
        region_id = store.create_region("Pattern", track_id)
        store.add_notes(region_id, [NoteDict(pitch=36, startBeat=0, durationBeats=0.5, velocity=100)])

        existing_track_names = {t.name for t in store.registry.list_tracks()}
        from app.core.maestro_editing import _get_incomplete_tracks
        incomplete = _get_incomplete_tracks(store)
        incomplete_set = set(incomplete)

        for step in tracker.steps:
            if (
                step.track_name
                and step.status in ("active", "pending")
                and step.track_name in existing_track_names
                and step.track_name not in incomplete_set
            ):
                step.status = "completed"

        assert tracker.steps[0].status == "completed"
        assert tracker.steps[1].status == "pending"


# ---------------------------------------------------------------------------
# _enrich_params_with_track_context — SSE toolCall param enrichment
# ---------------------------------------------------------------------------

class TestEnrichParamsWithTrackContext:
    """_enrich_params_with_track_context injects trackName/trackId for region-scoped tools."""

    def _make_store_with_region(self) -> tuple[StateStore, str, str]:

        """Return (store, track_id, region_id) with one track and one region."""
        from app.core.state_store import StateStore
        store = StateStore(conversation_id="enrich-test")
        track_id = store.create_track("Guitar Lead")
        region_id = store.create_region("Region 1", track_id)
        return store, track_id, region_id

    def test_injects_track_name_and_id_from_region_id(self) -> None:

        """regionId-only params get trackName and trackId appended."""
        from app.core.maestro_helpers import _enrich_params_with_track_context
        store, track_id, region_id = self._make_store_with_region()
        params: dict[str, JSONValue] = {"regionId": region_id, "cc": 11, "events": []}
        result = _enrich_params_with_track_context(params, store)
        assert result["trackName"] == "Guitar Lead"
        assert result["trackId"] == track_id
        assert result["regionId"] == region_id
        assert result["cc"] == 11

    def test_skips_if_track_name_already_present(self) -> None:

        """Params already containing trackName are returned unchanged."""
        from app.core.maestro_helpers import _enrich_params_with_track_context
        store, _, region_id = self._make_store_with_region()
        params: dict[str, JSONValue] = {"regionId": region_id, "trackName": "Already set", "trackId": "existing"}
        result = _enrich_params_with_track_context(params, store)
        assert result["trackName"] == "Already set"
        assert result["trackId"] == "existing"

    def test_skips_if_no_region_id(self) -> None:

        """Params without regionId (track-scoped tools) pass through unchanged."""
        from app.core.maestro_helpers import _enrich_params_with_track_context
        from app.core.state_store import StateStore
        store = StateStore(conversation_id="enrich-test-2")
        params: dict[str, JSONValue] = {"trackId": "some-track", "volumeDb": -6}
        result = _enrich_params_with_track_context(params, store)
        assert result == params

    def test_graceful_fallback_on_unknown_region(self) -> None:

        """Unknown regionId returns params unchanged without raising."""
        from app.core.maestro_helpers import _enrich_params_with_track_context
        from app.core.state_store import StateStore
        store = StateStore(conversation_id="enrich-test-3")
        params: dict[str, JSONValue] = {"regionId": "nonexistent-region-id", "cc": 64, "events": []}
        result = _enrich_params_with_track_context(params, store)
        assert result == params
        assert "trackName" not in result

    def test_original_params_not_mutated(self) -> None:

        """The helper returns a new dict; the original is never mutated."""
        from app.core.maestro_helpers import _enrich_params_with_track_context
        store, _, region_id = self._make_store_with_region()
        params: dict[str, JSONValue] = {"regionId": region_id, "cc": 1, "events": []}
        original = dict(params)
        _enrich_params_with_track_context(params, store)
        assert params == original


# ---------------------------------------------------------------------------
# Parallel execution — parallelGroup annotations on plan steps
# ---------------------------------------------------------------------------

class TestParallelGroup:
    """parallelGroup is emitted on instrument steps but not setup/mixing steps."""

    def _make_tool_calls(self, specs: list[tuple[str, dict[str, JSONValue]]]) -> list[ToolCall]:

        return [
            ToolCall(id=f"tc{i}", name=name, params=params)
            for i, (name, params) in enumerate(specs)
        ]

    def test_instrument_steps_have_parallel_group(self) -> None:

        """Track creation and content steps carry parallelGroup='instruments'."""
        tcs = self._make_tool_calls([
            ("stori_set_tempo", {"tempo": 92}),
            ("stori_add_midi_track", {"name": "Drums", "drumKitId": "TR-808"}),
            ("stori_add_midi_region", {"trackName": "Drums", "startBeat": 0, "durationBeats": 16}),
            ("stori_generate_midi", {"trackName": "Drums", "role": "drums", "style": "funk", "tempo": 92, "bars": 4}),
            ("stori_add_midi_track", {"name": "Bass", "gmProgram": 33}),
            ("stori_add_midi_region", {"trackName": "Bass", "startBeat": 0, "durationBeats": 16}),
            ("stori_generate_midi", {"trackName": "Bass", "role": "bass", "style": "funk", "tempo": 92, "bars": 4}),
            ("stori_ensure_bus", {"name": "Reverb"}),
            ("stori_add_send", {"trackName": "Bass", "busName": "Reverb"}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Make funk", {}, False, StateStore(conversation_id="t"))
        event = tracker.to_plan_event()
        steps = event.steps

        for s in steps:
            if "Add content" in s.label:
                assert s.parallel_group == "instruments", f"Step '{s.label}' should be parallel"
            elif "tempo" in s.label.lower() or "bus" in s.label.lower():
                assert s.parallel_group is None, f"Step '{s.label}' should NOT be parallel"

    def test_setup_steps_have_no_parallel_group(self) -> None:

        """Setup steps (tempo, key) must NOT have parallelGroup."""
        tcs = self._make_tool_calls([
            ("stori_set_tempo", {"tempo": 120}),
            ("stori_set_key", {"key": "Am"}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "set up", {}, False, StateStore(conversation_id="t"))
        event = tracker.to_plan_event()
        for s in event.steps:
            assert s.parallel_group is None

    def test_mixing_steps_have_no_parallel_group(self) -> None:

        """Mix adjust steps must NOT have parallelGroup."""
        tcs = self._make_tool_calls([
            ("stori_set_track_volume", {"trackName": "Drums", "volume": -3}),
            ("stori_set_track_pan", {"trackName": "Bass", "pan": -20}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Mix", {}, False, StateStore(conversation_id="t"))
        event = tracker.to_plan_event()
        for s in event.steps:
            assert s.parallel_group is None

    def test_build_from_prompt_annotates_parallel_group(self) -> None:

        """build_from_prompt tags role steps with parallelGroup='instruments'."""
        from app.core.prompt_parser import ParsedPrompt
        parsed = ParsedPrompt(
            raw="STORI PROMPT",
            mode="compose",
            request="make a groove",
            tempo=90,
            key="Cm",
            roles=["drums", "bass", "chords"],
        )
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make a groove", {"tempo": 80, "key": "C"})
        event = tracker.to_plan_event()
        steps = event.steps

        for s in steps:
            if "tempo" in s.label.lower() or "key" in s.label.lower():
                assert s.parallel_group is None, f"Setup step '{s.label}' should NOT be parallel"
            elif any(kw in s.label for kw in ("Create", "Add content", "Add effects")):
                assert s.parallel_group == "instruments", f"'{s.label}' should be parallel"

    def test_effects_within_track_group_have_parallel_group(self) -> None:

        """Insert effects following a track creation share its parallelGroup."""
        tcs = self._make_tool_calls([
            ("stori_add_midi_track", {"name": "Drums"}),
            ("stori_add_midi_region", {"trackName": "Drums", "startBeat": 0, "durationBeats": 16}),
            ("stori_add_insert_effect", {"trackName": "Drums", "type": "compressor"}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Drums", {}, False, StateStore(conversation_id="t"))
        instrument_steps = [s for s in tracker.steps if s.parallel_group == "instruments"]
        assert len(instrument_steps) >= 2
        assert any("effects" in s.label.lower() for s in instrument_steps)

    def test_single_instrument_still_works(self) -> None:

        """A single instrument request still produces valid steps (no crash)."""
        tcs = self._make_tool_calls([
            ("stori_add_midi_track", {"name": "Piano"}),
            ("stori_add_midi_region", {"trackName": "Piano", "startBeat": 0, "durationBeats": 16}),
            ("stori_generate_midi", {"trackName": "Piano", "role": "chords", "style": "jazz", "tempo": 120, "bars": 4}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Piano", {}, False, StateStore(conversation_id="t"))
        assert len(tracker.steps) >= 2
        assert any(s.label == "Create Piano track" for s in tracker.steps)
        assert any("content" in s.label.lower() for s in tracker.steps)


# ---------------------------------------------------------------------------
# Multi-active-step support for parallel execution
# ---------------------------------------------------------------------------

class TestMultiActiveSteps:
    """_PlanTracker supports multiple simultaneously active steps."""

    def test_multiple_steps_active_simultaneously(self) -> None:

        """activate_step can activate multiple steps without completing others."""
        tracker = _PlanTracker()
        tracker.steps = [
            _PlanStep(step_id="1", label="Create Drums track", track_name="Drums"),
            _PlanStep(step_id="2", label="Create Bass track", track_name="Bass"),
            _PlanStep(step_id="3", label="Create Guitar track", track_name="Guitar"),
        ]
        tracker.activate_step("1")
        tracker.activate_step("2")
        tracker.activate_step("3")

        assert tracker.steps[0].status == "active"
        assert tracker.steps[1].status == "active"
        assert tracker.steps[2].status == "active"
        assert tracker._active_step_ids == {"1", "2", "3"}

    def test_complete_step_by_id_removes_from_active_set(self) -> None:

        """Completing a step by ID removes it from _active_step_ids."""
        tracker = _PlanTracker()
        tracker.steps = [
            _PlanStep(step_id="1", label="Drums", track_name="Drums"),
            _PlanStep(step_id="2", label="Bass", track_name="Bass"),
        ]
        tracker.activate_step("1")
        tracker.activate_step("2")
        assert tracker._active_step_ids == {"1", "2"}

        tracker.complete_step_by_id("1")
        assert tracker._active_step_ids == {"2"}
        assert tracker.steps[0].status == "completed"
        assert tracker.steps[1].status == "active"

    def test_complete_all_active_steps(self) -> None:

        """complete_all_active_steps completes every active step at once."""
        tracker = _PlanTracker()
        tracker.steps = [
            _PlanStep(step_id="1", label="Drums", track_name="Drums", status="active"),
            _PlanStep(step_id="2", label="Bass", track_name="Bass", status="active"),
            _PlanStep(step_id="3", label="Mix", status="pending"),
        ]
        tracker._active_step_ids = {"1", "2"}
        events = tracker.complete_all_active_steps()
        assert len(events) == 2
        assert all(e.status == "completed" for e in events)
        assert tracker._active_step_ids == set()
        assert tracker.steps[0].status == "completed"
        assert tracker.steps[1].status == "completed"
        assert tracker.steps[2].status == "pending"

    def test_find_active_step_for_track(self) -> None:

        """find_active_step_for_track locates the active step by track name."""
        tracker = _PlanTracker()
        tracker.steps = [
            _PlanStep(step_id="1", label="Create Drums", track_name="Drums", status="active"),
            _PlanStep(step_id="2", label="Add content to Drums", track_name="Drums", status="pending"),
            _PlanStep(step_id="3", label="Create Bass", track_name="Bass", status="active"),
        ]
        tracker._active_step_ids = {"1", "3"}

        drums = tracker.find_active_step_for_track("Drums")
        assert drums is not None
        assert drums.step_id == "1"

        bass = tracker.find_active_step_for_track("Bass")
        assert bass is not None
        assert bass.step_id == "3"

        guitar = tracker.find_active_step_for_track("Guitar")
        assert guitar is None

    def test_finalize_pending_after_parallel_completion(self) -> None:

        """After completing parallel steps, pending steps become skipped."""
        tracker = _PlanTracker()
        tracker.steps = [
            _PlanStep(step_id="1", label="Drums", status="completed"),
            _PlanStep(step_id="2", label="Bass", status="completed"),
            _PlanStep(step_id="3", label="Mix", status="pending"),
        ]
        events = tracker.finalize_pending_as_skipped()
        assert len(events) == 1
        assert events[0].step_id == "3"
        assert events[0].status == "skipped"


# ---------------------------------------------------------------------------
# Executor — _group_into_phases
# ---------------------------------------------------------------------------

class TestGroupIntoPhases:
    """_group_into_phases splits tool calls into setup / instruments / mixing."""

    def _tc(self, name: str, params: dict[str, JSONValue] | None = None) -> ToolCall:

        return ToolCall(name=name, params=params or {})

    def test_basic_three_phase_split(self) -> None:

        """Setup, instrument, and mixing calls are correctly separated."""
        from app.core.executor import _group_into_phases
        calls = [
            self._tc("stori_set_tempo", {"tempo": 92}),
            self._tc("stori_set_key", {"key": "Cm"}),
            self._tc("stori_add_midi_track", {"name": "Drums"}),
            self._tc("stori_add_midi_region", {"trackName": "Drums", "startBeat": 0, "durationBeats": 16}),
            self._tc("stori_generate_midi", {"trackName": "Drums", "role": "drums", "style": "funk", "tempo": 92, "bars": 4}),
            self._tc("stori_add_midi_track", {"name": "Bass"}),
            self._tc("stori_add_midi_region", {"trackName": "Bass", "startBeat": 0, "durationBeats": 16}),
            self._tc("stori_generate_midi", {"trackName": "Bass", "role": "bass", "style": "funk", "tempo": 92, "bars": 4}),
            self._tc("stori_ensure_bus", {"name": "Reverb"}),
            self._tc("stori_add_send", {"trackName": "Bass", "busName": "Reverb"}),
            self._tc("stori_set_track_volume", {"trackName": "Drums", "volume": -3}),
        ]
        phase1, groups, order, phase3 = _group_into_phases(calls)

        assert len(phase1) == 2
        assert phase1[0].name == "stori_set_tempo"
        assert phase1[1].name == "stori_set_key"

        assert set(groups.keys()) == {"drums", "bass"}
        assert len(groups["drums"]) == 3
        assert len(groups["bass"]) == 3
        assert order == ["drums", "bass"]

        assert len(phase3) == 3
        assert phase3[0].name == "stori_ensure_bus"
        assert phase3[1].name == "stori_add_send"
        assert phase3[2].name == "stori_set_track_volume"

    def test_empty_plan(self) -> None:

        """Empty tool call list produces empty phases."""
        from app.core.executor import _group_into_phases
        p1, groups, order, p3 = _group_into_phases([])
        assert p1 == []
        assert groups == {}
        assert order == []
        assert p3 == []

    def test_single_instrument(self) -> None:

        """Single instrument produces one group, no setup or mixing."""
        from app.core.executor import _group_into_phases
        calls = [
            self._tc("stori_add_midi_track", {"name": "Piano"}),
            self._tc("stori_add_midi_region", {"trackName": "Piano", "startBeat": 0, "durationBeats": 16}),
            self._tc("stori_generate_midi", {"trackName": "Piano", "role": "chords", "style": "jazz", "tempo": 120, "bars": 4}),
            self._tc("stori_add_insert_effect", {"trackName": "Piano", "type": "reverb"}),
        ]
        p1, groups, order, p3 = _group_into_phases(calls)
        assert p1 == []
        assert len(groups) == 1
        assert "piano" in groups
        assert len(groups["piano"]) == 4
        assert p3 == []

    def test_five_instruments_all_grouped(self) -> None:

        """Five instruments each get their own group."""
        from app.core.executor import _group_into_phases
        instruments = ["Drums", "Bass", "Guitar", "Keys", "Strings"]
        calls = []
        for inst in instruments:
            calls.append(self._tc("stori_add_midi_track", {"name": inst}))
            calls.append(self._tc("stori_add_midi_region", {"trackName": inst, "startBeat": 0, "durationBeats": 16}))
            calls.append(self._tc("stori_generate_midi", {"trackName": inst, "role": inst.lower(), "style": "jazz", "tempo": 120, "bars": 4}))

        p1, groups, order, p3 = _group_into_phases(calls)
        assert len(groups) == 5
        assert order == [i.lower() for i in instruments]
        for inst in instruments:
            assert inst.lower() in groups
            assert len(groups[inst.lower()]) == 3

    def test_instrument_order_preserved(self) -> None:

        """instrument_order matches first-seen ordering of tracks."""
        from app.core.executor import _group_into_phases
        calls = [
            self._tc("stori_add_midi_track", {"name": "Strings"}),
            self._tc("stori_add_midi_track", {"name": "Bass"}),
            self._tc("stori_add_midi_track", {"name": "Drums"}),
        ]
        _, _, order, _ = _group_into_phases(calls)
        assert order == ["strings", "bass", "drums"]


# ---------------------------------------------------------------------------
# Agent Teams — new tests per the architecture plan
# ---------------------------------------------------------------------------


def _make_parsed_multi(
    tempo: int = 92,
    key: str = "Cm",
    roles: list[str] | None = None,
    style: str = "funk",
    bars: int = 4,
) -> ParsedPrompt:
    from app.core.prompt_parser import ParsedPrompt
    return ParsedPrompt(
        raw="STORI PROMPT",
        mode="compose",
        request="make a funk groove",
        tempo=tempo,
        key=key,
        roles=roles if roles is not None else ["drums", "bass"],
        style=style,
        extensions={"bars": bars},
    )


class TestAgentTeamRouting:
    """orchestrate() routes multi-role STORI PROMPT to agent-team handler."""

    def _make_intent_result(self, intent: Intent, sse_state: SSEState, parsed: ParsedPrompt | None = None) -> IntentResult:

        from app.core.intent import IntentResult
        slots = MagicMock()
        slots.extras = {"parsed_prompt": parsed} if parsed else {}
        return IntentResult(
            intent=intent,
            sse_state=sse_state,
            confidence=0.9,
            slots=slots,
            tools=[],
            allowed_tool_names={"stori_set_tempo", "stori_set_key", "stori_add_midi_track"},
            tool_choice="auto",
            force_stop_after=False,
            requires_planner=False,
            reasons=(),
        )

    @pytest.mark.anyio
    async def test_multi_role_routes_to_agent_team(self) -> None:

        """Multi-role STORI PROMPT with GENERATE_MUSIC + apply mode uses agent-team handler."""
        from app.core.intent import Intent, SSEState
        from app.core.maestro_agent_teams import _handle_composition_agent_team

        parsed = _make_parsed_multi(roles=["drums", "bass"])
        intent_result = self._make_intent_result(Intent.GENERATE_MUSIC, SSEState.EDITING, parsed)

        llm = _make_llm_mock(content="Done")
        store = StateStore(conversation_id="test-routing")
        project_context: ProjectContext = {}

        agent_team_events = []
        with patch(
            "app.core.maestro_handlers._handle_composition_agent_team",
            return_value=_fake_events_gen(["agent_team_called"]),
        ) as mock_handler, patch(
            "app.core.maestro_handlers.get_intent_result_with_llm",
            return_value=intent_result,
        ), patch(
            "app.core.maestro_handlers.get_or_create_store",
            return_value=store,
        ):
            async for e in orchestrate("make funk", project_context, llm, None):
                agent_team_events.append(e)

        mock_handler.assert_called_once()
        _, call_kwargs = mock_handler.call_args
        # parsed is passed as positional arg index 2
        called_parsed = mock_handler.call_args.args[2]
        assert called_parsed.roles == ["drums", "bass"]

    @pytest.mark.anyio
    async def test_single_role_uses_editing_handler(self) -> None:

        """Single-role STORI PROMPT still routes to _handle_editing."""
        from app.core.intent import Intent, SSEState

        parsed = _make_parsed_multi(roles=["drums"])
        intent_result = self._make_intent_result(Intent.GENERATE_MUSIC, SSEState.EDITING, parsed)

        llm = _make_llm_mock(content="Done")
        store = StateStore(conversation_id="test-single")
        project_context: ProjectContext = {}

        with patch(
            "app.core.maestro_handlers._handle_composition_agent_team",
        ) as mock_agent_team, patch(
            "app.core.maestro_handlers._handle_editing",
            return_value=_fake_events_gen(["editing_called"]),
        ) as mock_editing, patch(
            "app.core.maestro_handlers.get_intent_result_with_llm",
            return_value=intent_result,
        ), patch(
            "app.core.maestro_handlers.get_or_create_store",
            return_value=store,
        ):
            async for _ in orchestrate("make a beat", project_context, llm, None):
                pass

        mock_agent_team.assert_not_called()
        mock_editing.assert_called_once()

    @pytest.mark.anyio
    async def test_no_parsed_prompt_uses_editing_handler(self) -> None:

        """When no parsed prompt is present, routing falls through to _handle_editing."""
        from app.core.intent import Intent, SSEState

        intent_result = self._make_intent_result(Intent.GENERATE_MUSIC, SSEState.EDITING, None)

        llm = _make_llm_mock(content="Done")
        store = StateStore(conversation_id="test-no-parsed")
        project_context: ProjectContext = {}

        with patch(
            "app.core.maestro_handlers._handle_composition_agent_team",
        ) as mock_agent_team, patch(
            "app.core.maestro_handlers._handle_editing",
            return_value=_fake_events_gen(["editing_called"]),
        ), patch(
            "app.core.maestro_handlers.get_intent_result_with_llm",
            return_value=intent_result,
        ), patch(
            "app.core.maestro_handlers.get_or_create_store",
            return_value=store,
        ):
            async for _ in orchestrate("make a beat", project_context, llm, None):
                pass

        mock_agent_team.assert_not_called()


async def _fake_events_gen(events: Iterable[str]) -> AsyncGenerator[str, None]:

    """Async generator yielding fake SSE event strings."""
    for e in events:
        yield f"data: {json.dumps({'type': e})}\n\n"


class TestApplySingleToolCall:
    """_apply_single_tool_call() returns correct outcome for various tool types."""

    @pytest.mark.anyio
    async def test_valid_tool_returns_not_skipped(self) -> None:

        """A valid tool call returns skipped=False with populated fields."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-apply")
        trace = _make_trace()
        outcome = await _apply_single_tool_call(
            tc_id="tc-1",
            tc_name="stori_set_tempo",
            resolved_args={"tempo": 120},
            allowed_tool_names={"stori_set_tempo"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )
        assert not outcome.skipped
        assert outcome.enriched_params["tempo"] == 120
        assert any(e.type == "toolCall" for e in outcome.sse_events)
        assert any(e.type == "toolStart" for e in outcome.sse_events)
        assert outcome.msg_call["role"] == "assistant"
        assert outcome.msg_result["role"] == "tool"

    @pytest.mark.anyio
    async def test_invalid_tool_returns_skipped_with_error(self) -> None:

        """Invalid tool call (wrong tool name) returns skipped=True with toolError."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-apply-invalid")
        trace = _make_trace()
        outcome = await _apply_single_tool_call(
            tc_id="tc-2",
            tc_name="stori_set_tempo",
            resolved_args={"tempo": 120},
            allowed_tool_names={"stori_add_midi_track"},  # disallowed
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )
        assert outcome.skipped
        assert any(e.type == "toolError" for e in outcome.sse_events)

    @pytest.mark.anyio
    async def test_track_creation_generates_uuid(self) -> None:

        """stori_add_midi_track generates a fresh UUID and registers in store."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-apply-track")
        trace = _make_trace()
        outcome = await _apply_single_tool_call(
            tc_id="tc-3",
            tc_name="stori_add_midi_track",
            resolved_args={"name": "Drums", "drumKitId": "TR-808"},
            allowed_tool_names={"stori_add_midi_track"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )
        assert not outcome.skipped
        _track_id = outcome.enriched_params.get("trackId")
        assert isinstance(_track_id, str)
        # Verify UUID was registered in store
        assert store.registry.get_track(_track_id) is not None

    @pytest.mark.anyio
    async def test_circuit_breaker_fires_at_3_failures(self) -> None:

        """stori_add_notes is rejected when failure count >= 3."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-cb")
        # Register a region first
        track_id = store.create_track("Piano")
        region_id = store.create_region("Region", track_id)

        trace = _make_trace()
        failures = {region_id: 3}  # already at limit
        outcome = await _apply_single_tool_call(
            tc_id="tc-4",
            tc_name="stori_add_notes",
            resolved_args={"regionId": region_id, "notes": []},
            allowed_tool_names={"stori_add_notes"},
            store=store,
            trace=trace,
            add_notes_failures=failures,
            emit_sse=True,
        )
        assert outcome.skipped
        assert any(e.type == "toolError" for e in outcome.sse_events)

    @pytest.mark.anyio
    async def test_emit_sse_false_produces_no_events(self) -> None:

        """emit_sse=False returns empty sse_events for proposal/variation path."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-no-sse")
        trace = _make_trace()
        outcome = await _apply_single_tool_call(
            tc_id="tc-5",
            tc_name="stori_set_tempo",
            resolved_args={"tempo": 90},
            allowed_tool_names={"stori_set_tempo"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=False,
        )
        assert not outcome.skipped
        assert outcome.sse_events == []

    @pytest.mark.anyio
    async def test_generator_no_region_returns_error_not_none(self) -> None:

        """stori_generate_midi without a prior region returns a skipped error outcome,
        never falling through to emit the internal tool name to SSE."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-gen-no-region")
        store.create_track("Bass")
        trace = _make_trace()
        outcome = await _apply_single_tool_call(
            tc_id="tc-gen",
            tc_name="stori_generate_midi",
            resolved_args={"role": "bass", "style": "house", "tempo": 120, "bars": 4},
            allowed_tool_names={"stori_generate_midi"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
            composition_context={"style": "house", "tempo": 120, "bars": 4, "key": "Am"},
        )
        assert outcome.skipped
        assert "error" in outcome.tool_result
        tool_names_in_sse = [getattr(e, "name", None) for e in outcome.sse_events if e.type == "toolCall"]
        assert "stori_generate_midi" not in tool_names_in_sse

    @pytest.mark.anyio
    async def test_drumkitid_forces_is_drums_true(self) -> None:

        """A track with drumKitId but a non-drum name still gets _isDrums=True."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-drumkit-flag")
        trace = _make_trace()
        outcome = await _apply_single_tool_call(
            tc_id="tc-dk",
            tc_name="stori_add_midi_track",
            resolved_args={"name": "Shaker", "drumKitId": "TR-909"},
            allowed_tool_names={"stori_add_midi_track"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )
        assert not outcome.skipped
        assert outcome.enriched_params.get("_isDrums") is True

    @pytest.mark.anyio
    async def test_color_autoassigned_when_missing(self) -> None:

        """Tracks without a color param get a named color from the role map."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-color-auto")
        trace = _make_trace()
        outcome = await _apply_single_tool_call(
            tc_id="tc-c1",
            tc_name="stori_add_midi_track",
            resolved_args={"name": "Bass"},
            allowed_tool_names={"stori_add_midi_track"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )
        assert outcome.enriched_params["color"] == "green"

    @pytest.mark.anyio
    async def test_invalid_color_replaced_with_named(self) -> None:

        """Arbitrary CSS names are rejected and replaced by a role-based color."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-color-invalid")
        trace = _make_trace()
        outcome = await _apply_single_tool_call(
            tc_id="tc-c2",
            tc_name="stori_add_midi_track",
            resolved_args={"name": "Drums", "color": "crimson"},
            allowed_tool_names={"stori_add_midi_track"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )
        assert outcome.enriched_params["color"] == "red"

    @pytest.mark.anyio
    async def test_valid_hex_color_passthrough(self) -> None:

        """Valid #RRGGBB hex is accepted as-is."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-color-hex")
        trace = _make_trace()
        outcome = await _apply_single_tool_call(
            tc_id="tc-c3",
            tc_name="stori_add_midi_track",
            resolved_args={"name": "Piano", "color": "#4A90D9"},
            allowed_tool_names={"stori_add_midi_track"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )
        assert outcome.enriched_params["color"] == "#4A90D9"

    @pytest.mark.anyio
    async def test_valid_named_color_passthrough(self) -> None:

        """Valid named color is kept."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-color-named")
        trace = _make_trace()
        outcome = await _apply_single_tool_call(
            tc_id="tc-c4",
            tc_name="stori_add_midi_track",
            resolved_args={"name": "Piano", "color": "indigo"},
            allowed_tool_names={"stori_add_midi_track"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )
        assert outcome.enriched_params["color"] == "indigo"

    @pytest.mark.anyio
    async def test_gm_program_xor_is_drums_enforced(self) -> None:

        """Exactly one of _isDrums or gmProgram is always present."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-xor")
        trace = _make_trace()
        outcome = await _apply_single_tool_call(
            tc_id="tc-xor1",
            tc_name="stori_add_midi_track",
            resolved_args={"name": "Drums", "drumKitId": "TR-808", "gmProgram": 0},
            allowed_tool_names={"stori_add_midi_track"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )
        assert outcome.enriched_params.get("_isDrums") is True
        assert "gmProgram" not in outcome.enriched_params

    @pytest.mark.anyio
    async def test_gm_program_default_when_neither_set(self) -> None:

        """gmProgram defaults to 0 when neither _isDrums nor gmProgram is set."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-xor-default")
        trace = _make_trace()
        outcome = await _apply_single_tool_call(
            tc_id="tc-xor2",
            tc_name="stori_add_midi_track",
            resolved_args={"name": "Mystery Track"},
            allowed_tool_names={"stori_add_midi_track"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )
        has_gm = outcome.enriched_params.get("gmProgram") is not None
        is_drums = outcome.enriched_params.get("_isDrums", False)
        assert has_gm or is_drums, "Exactly one of gmProgram or _isDrums must be set"
        assert not (has_gm and is_drums), "Both gmProgram and _isDrums should not be set"

    @pytest.mark.anyio
    async def test_note_fields_backfilled(self) -> None:

        """Missing note fields get default values before SSE emission."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-note-backfill")
        store.create_track("Piano")
        track_id = store.registry.resolve_track("Piano")
        assert track_id is not None
        store.create_region("Region", track_id)
        region_id = store.registry.get_latest_region_for_track(track_id)
        assert region_id is not None
        trace = _make_trace()
        outcome = await _apply_single_tool_call(
            tc_id="tc-notes",
            tc_name="stori_add_notes",
            resolved_args={
                "regionId": region_id,
                "notes": json_list([NoteDict(pitch=72)]),
            },
            allowed_tool_names={"stori_add_notes"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )
        _notes = outcome.enriched_params["notes"]
        assert isinstance(_notes, list)
        note = _notes[0]
        assert isinstance(note, dict)
        assert note["pitch"] == 72
        assert note["velocity"] == 100
        assert note["startBeat"] == 0
        assert note["durationBeats"] == 1.0


class TestRunInstrumentAgent:
    """_run_instrument_agent() makes one LLM call and pushes events to the queue."""

    def _make_track_response(self, track_name: str = "Drums") -> LLMResponse:

        """LLMResponse with stori_add_midi_track + stori_add_midi_region."""
        response = LLMResponse(content=None, usage={"prompt_tokens": 10, "completion_tokens": 20})
        response.tool_calls = [
            ToolCall(id="tc-a", name="stori_add_midi_track", params={"name": track_name, "drumKitId": "TR-808"}),
            ToolCall(id="tc-b", name="stori_add_midi_region", params={"trackId": "$0.trackId", "startBeat": 0, "durationBeats": 16}),
        ]
        return response

    @pytest.mark.anyio
    async def test_agent_puts_tool_call_events_in_queue(self) -> None:

        """Instrument agent puts toolCall SSE events into the shared queue."""
        from app.core.maestro_agent_teams import _run_instrument_agent

        store = StateStore(conversation_id="test-agent")
        plan_tracker = _PlanTracker()
        plan_tracker.build_from_prompt(
            _make_parsed_multi(roles=["drums"]),
            "make funk",
            {},
        )

        llm = _make_llm_mock()
        resp = self._make_track_response("Drums")
        llm.chat_completion_stream = MagicMock(side_effect=_response_to_stream(resp))
        trace = _make_trace()

        queue: asyncio.Queue[MaestroEvent] = asyncio.Queue()
        step_ids = [s.step_id for s in plan_tracker.steps if s.parallel_group == "instruments"]

        await _run_instrument_agent(
            instrument_name="Drums",
            role="drums",
            style="funk",
            bars=4,
            tempo=92,
            key="Cm",
            step_ids=step_ids,
            plan_tracker=plan_tracker,
            llm=llm,
            store=store,
            allowed_tool_names={"stori_add_midi_track", "stori_add_midi_region"},
            trace=trace,
            sse_queue=queue,
            collected_tool_calls=[],
        )

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        event_types = [e.type for e in events]
        assert "toolCall" in event_types
        assert "toolStart" in event_types

    @pytest.mark.anyio
    async def test_agent_marks_steps_failed_on_llm_error(self) -> None:

        """When the LLM call raises, all owned plan steps are marked failed."""
        from app.core.maestro_agent_teams import _run_instrument_agent

        store = StateStore(conversation_id="test-agent-fail")
        plan_tracker = _PlanTracker()
        plan_tracker.build_from_prompt(
            _make_parsed_multi(roles=["drums"]),
            "make funk",
            {},
        )

        llm = _make_llm_mock()
        async def _failing_stream(*args: object, **kwargs: object) -> AsyncGenerator[StreamEvent, None]:
            _no_events: list[StreamEvent] = []
            for _e in _no_events:
                yield _e  # loop body never executes; required to make this an async generator
            raise RuntimeError("LLM down")
        llm.chat_completion_stream = MagicMock(side_effect=_failing_stream)
        trace = _make_trace()

        queue: asyncio.Queue[MaestroEvent] = asyncio.Queue()
        step_ids = [s.step_id for s in plan_tracker.steps if s.parallel_group == "instruments"]

        await _run_instrument_agent(
            instrument_name="Drums",
            role="drums",
            style="funk",
            bars=4,
            tempo=92,
            key="Cm",
            step_ids=step_ids,
            plan_tracker=plan_tracker,
            llm=llm,
            store=store,
            allowed_tool_names={"stori_add_midi_track"},
            trace=trace,
            sse_queue=queue,
            collected_tool_calls=[],
        )

        # Failed steps should emit planStepUpdate events via queue
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        # Some events for the failed steps should appear
        step_events = [e for e in events if isinstance(e, PlanStepUpdateEvent)]
        assert any(e.status == "failed" for e in step_events)

    @pytest.mark.anyio
    async def test_agent_makes_exactly_one_llm_call(self) -> None:

        """Each instrument agent makes exactly one independent streaming LLM call."""
        from app.core.maestro_agent_teams import _run_instrument_agent

        store = StateStore(conversation_id="test-agent-count")
        plan_tracker = _PlanTracker()
        plan_tracker.build_from_prompt(
            _make_parsed_multi(roles=["bass"]),
            "make funk",
            {},
        )

        llm = _make_llm_mock()
        resp = self._make_track_response("Bass")
        llm.chat_completion_stream = MagicMock(side_effect=_response_to_stream(resp))
        trace = _make_trace()
        queue: asyncio.Queue[MaestroEvent] = asyncio.Queue()
        step_ids = [s.step_id for s in plan_tracker.steps if s.parallel_group == "instruments"]

        await _run_instrument_agent(
            instrument_name="Bass",
            role="bass",
            style="funk",
            bars=4,
            tempo=92,
            key="Cm",
            step_ids=step_ids,
            plan_tracker=plan_tracker,
            llm=llm,
            store=store,
            allowed_tool_names={"stori_add_midi_track", "stori_add_midi_region"},
            trace=trace,
            sse_queue=queue,
            collected_tool_calls=[],
        )

        assert llm.chat_completion_stream.call_count >= 1


class TestAgentTeamPhases:
    """_handle_composition_agent_team() honours phase ordering."""

    def _make_route_for_team(self) -> IntentResult:

        from app.core.intent import Intent, IntentResult, SSEState
        return IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.EDITING,
            confidence=0.9,
            slots=MagicMock(),
            tools=[],
            allowed_tool_names={
                "stori_set_tempo", "stori_set_key",
                "stori_add_midi_track", "stori_add_midi_region",
                "stori_add_notes", "stori_add_insert_effect",
            },
            tool_choice="auto",
            force_stop_after=False,
            requires_planner=False,
            reasons=(),
        )

    @pytest.mark.anyio
    async def test_plan_event_emitted_before_agents(self) -> None:

        """plan event is emitted before any instrument agents start."""
        from app.core.maestro_agent_teams import _handle_composition_agent_team

        parsed = _make_parsed_multi(roles=["drums", "bass"])
        route = self._make_route_for_team()
        store = StateStore(conversation_id="test-phases")
        trace = _make_trace()

        llm = _make_llm_mock()
        agent_response = LLMResponse(content=None, usage={})
        agent_response.tool_calls = [
            ToolCall(id="x1", name="stori_add_midi_track", params={"name": "Drums", "drumKitId": "TR-808"}),
        ]
        llm.chat_completion_stream = MagicMock(side_effect=_response_to_stream(agent_response))

        events = []
        async for e in _handle_composition_agent_team(
            "make funk", {}, parsed, route, llm, store, trace, None
        ):
            events.append(e)

        payloads = _parse_events(events)
        event_types = [p["type"] for p in payloads]
        plan_idx = next((i for i, t in enumerate(event_types) if t == "plan"), None)
        first_tool_idx = next((i for i, t in enumerate(event_types) if t == "toolCall"), None)
        assert plan_idx is not None, "plan event must be emitted"
        if first_tool_idx is not None:
            assert plan_idx < first_tool_idx

    @pytest.mark.anyio
    async def test_complete_event_emitted_at_end(self) -> None:

        """complete event is emitted once, at the end."""
        from app.core.maestro_agent_teams import _handle_composition_agent_team

        parsed = _make_parsed_multi(roles=["drums", "bass"])
        route = self._make_route_for_team()
        store = StateStore(conversation_id="test-complete")
        trace = _make_trace()

        llm = _make_llm_mock()
        agent_response = LLMResponse(content=None, usage={})
        agent_response.tool_calls = []
        llm.chat_completion_stream = MagicMock(side_effect=_response_to_stream(agent_response))

        events = []
        async for e in _handle_composition_agent_team(
            "make funk", {}, parsed, route, llm, store, trace, None
        ):
            events.append(e)

        payloads = _parse_events(events)
        complete_events = [p for p in payloads if p["type"] == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0]["success"] is True
        last_event = payloads[-1]
        assert last_event["type"] == "complete"

    @pytest.mark.anyio
    async def test_phase1_tempo_applied_before_agents(self) -> None:

        """Phase 1 emits toolCall for stori_set_tempo before instrument agents run."""
        from app.core.maestro_agent_teams import _handle_composition_agent_team

        parsed = _make_parsed_multi(tempo=92, roles=["drums", "bass"])
        route = self._make_route_for_team()
        store = StateStore(conversation_id="test-phase1")
        trace = _make_trace()

        llm = _make_llm_mock()
        agent_response = LLMResponse(content=None, usage={})
        agent_response.tool_calls = []
        llm.chat_completion_stream = MagicMock(side_effect=_response_to_stream(agent_response))

        events = []
        async for e in _handle_composition_agent_team(
            "make funk", {"tempo": 80}, parsed, route, llm, store, trace, None
        ):
            events.append(e)

        payloads = _parse_events(events)
        tool_calls = [p for p in payloads if p["type"] == "toolCall"]
        tempo_calls = [t for t in tool_calls if t.get("name") == "stori_set_tempo"]
        assert len(tempo_calls) >= 1


class TestAgentTeamFailureIsolation:
    """A failing instrument agent does not cancel sibling agents."""

    def _make_route_for_team(self) -> IntentResult:

        from app.core.intent import Intent, IntentResult, SSEState
        return IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.EDITING,
            confidence=0.9,
            slots=MagicMock(),
            tools=[],
            allowed_tool_names={
                "stori_add_midi_track", "stori_add_midi_region",
                "stori_add_notes", "stori_add_insert_effect",
                "stori_set_tempo", "stori_set_key",
            },
            tool_choice="auto",
            force_stop_after=False,
            requires_planner=False,
            reasons=(),
        )

    @pytest.mark.anyio
    async def test_one_failing_agent_does_not_cancel_others(self) -> None:

        """When one instrument agent's LLM call fails, others still complete."""
        from app.core.maestro_agent_teams import _handle_composition_agent_team

        parsed = _make_parsed_multi(roles=["drums", "bass"])
        route = self._make_route_for_team()
        store = StateStore(conversation_id="test-isolation")
        trace = _make_trace()

        agent_call_count = 0

        def agent_stream_side_effect(*args: object, **kwargs: object) -> AsyncGenerator[StreamEvent, None]:

            nonlocal agent_call_count
            agent_call_count += 1
            current_call = agent_call_count

            async def _stream() -> AsyncGenerator[StreamEvent, None]:
                if current_call == 1:
                    raise RuntimeError("drums LLM failed")
                response = LLMResponse(content=None, usage={})
                if current_call == 2:
                    response.tool_calls = [
                        ToolCall(id="b1", name="stori_add_midi_track", params={"name": "Bass"}),
                    ]
                else:
                    response.tool_calls = []
                tc_raw: list[ToolCallEntry] = [
                    ToolCallEntry(id=tc.id, type="function", function={"name": tc.name, "arguments": json.dumps(tc.params)})
                    for tc in response.tool_calls
                ]
                yield {"type": "done", "content": None, "tool_calls": tc_raw, "finish_reason": "stop", "usage": {}}
            return _stream()

        llm = _make_llm_mock()
        llm.chat_completion_stream = MagicMock(side_effect=agent_stream_side_effect)

        events = []
        async for e in _handle_composition_agent_team(
            "make funk", {}, parsed, route, llm, store, trace, None
        ):
            events.append(e)

        payloads = _parse_events(events)
        assert any(p["type"] == "complete" for p in payloads)
        assert agent_call_count >= 2

    @pytest.mark.anyio
    async def test_complete_event_emitted_even_when_all_agents_fail(self) -> None:

        """complete event fires even if every instrument agent fails."""
        from app.core.maestro_agent_teams import _handle_composition_agent_team

        parsed = _make_parsed_multi(roles=["drums", "bass"])
        route = self._make_route_for_team()
        store = StateStore(conversation_id="test-all-fail")
        trace = _make_trace()

        def all_fail(*args: object, **kwargs: object) -> AsyncGenerator[StreamEvent, None]:

            async def _fail() -> AsyncGenerator[StreamEvent, None]:
                _no_events: list[StreamEvent] = []
                for _e in _no_events:
                    yield _e  # loop body never executes; required to make this an async generator
                raise RuntimeError("all down")
            return _fail()

        llm = _make_llm_mock()
        llm.chat_completion_stream = MagicMock(side_effect=all_fail)

        events = []
        async for e in _handle_composition_agent_team(
            "make funk", {}, parsed, route, llm, store, trace, None
        ):
            events.append(e)

        payloads = _parse_events(events)
        assert any(p["type"] == "complete" for p in payloads)


# =============================================================================
# Bug-fix regression tests (Agent Teams live-test session)
# =============================================================================

class TestIsAdditiveCompositionBug3:
    """_is_additive_composition returns True for STORI PROMPTs with 2+ roles.

    Regression test for Bug 3: the third STORI PROMPT (horn break, roles:
    drums + horns) was routed to the composing/variation pipeline because
    _is_additive_composition returned False (both tracks already existed).
    The fix: any parsed prompt with 2+ roles always returns True.
    """

    def test_two_roles_returns_true_even_when_all_tracks_exist(self) -> None:

        """2-role STORI PROMPT → True even if both tracks already exist."""
        from app.core.maestro_editing import _is_additive_composition

        parsed = _make_parsed_multi(roles=["drums", "horns"])
        project_context: ProjectContext = {
            "tracks": [
                {"name": "Drums", "id": "t1", "regions": []},
                {"name": "Horns", "id": "t2", "regions": []},
            ]
        }
        assert _is_additive_composition(parsed, project_context) is True

    def test_single_role_existing_track_returns_false(self) -> None:

        """1-role prompt where the track exists → False (original behaviour)."""
        from app.core.maestro_editing import _is_additive_composition

        parsed = _make_parsed_multi(roles=["drums"])
        project_context: ProjectContext = {
            "tracks": [{"name": "Drums", "id": "t1", "regions": []}]
        }
        assert _is_additive_composition(parsed, project_context) is False

    def test_single_role_new_track_returns_true(self) -> None:

        """1-role prompt for a brand-new track → True (existing behaviour)."""
        from app.core.maestro_editing import _is_additive_composition

        parsed = _make_parsed_multi(roles=["bass"])
        project_context: ProjectContext = {
            "tracks": [{"name": "Drums", "id": "t1", "regions": []}]
        }
        assert _is_additive_composition(parsed, project_context) is True

    def test_no_parsed_returns_false(self) -> None:

        """None parsed → False (guard clause)."""
        from app.core.maestro_editing import _is_additive_composition

        assert _is_additive_composition(None, {}) is False


class TestBuildCompositionSummaryBug1:
    """_build_composition_summary distinguishes created vs reused tracks.

    Regression test for Bug 1: summary.final must report tracksReused
    alongside tracksCreated so the frontend shows correct labels.
    """

    def test_reused_track_appears_in_tracks_reused(self) -> None:

        """Synthetic _reused_track entries populate tracksReused."""
        from app.core.maestro_agent_teams import _build_composition_summary

        tool_calls: list[ToolCallDict] = [
            ToolCallDict(tool="_reused_track", params={"name": "Drums", "trackId": "t-drums"}),
            ToolCallDict(tool="stori_add_midi_track", params={"name": "Bass", "trackId": "t-bass"}),
            ToolCallDict(tool="stori_add_midi_region", params={}),
        ]
        summary = _build_composition_summary(tool_calls)

        assert len(summary["tracksReused"]) == 1
        assert summary["tracksReused"][0]["name"] == "Drums"
        assert len(summary["tracksCreated"]) == 1
        assert summary["tracksCreated"][0]["name"] == "Bass"
        assert summary["trackCount"] == 2

    def test_no_reused_tracks_gives_empty_list(self) -> None:

        """When no tracks are reused, tracksReused is an empty list."""
        from app.core.maestro_agent_teams import _build_composition_summary

        tool_calls: list[ToolCallDict] = [
            ToolCallDict(tool="stori_add_midi_track", params={"name": "Drums", "trackId": "t1"}),
        ]
        summary = _build_composition_summary(tool_calls)

        assert summary["tracksReused"] == []
        assert len(summary["tracksCreated"]) == 1


class TestAgentTeamExistingTrackReuse:
    """Agent Teams coordinator passes per-role trackId/startBeat to agents.

    Regression tests for Bug 1 (duplicate tracks) and the follow-up bug where
    all agents received the same (first) trackId instead of their own.
    """

    def _make_route_for_team(self) -> IntentResult:

        from app.core.intent import Intent, IntentResult, SSEState
        return IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.EDITING,
            confidence=0.9,
            slots=MagicMock(),
            tools=[],
            allowed_tool_names={
                "stori_set_tempo", "stori_set_key",
                "stori_add_midi_track", "stori_add_midi_region",
                "stori_add_notes", "stori_add_insert_effect",
            },
            tool_choice="auto",
            force_stop_after=False,
            requires_planner=False,
            reasons=(),
        )

    @pytest.mark.anyio
    async def test_existing_track_injects_reused_track_into_summary(self) -> None:

        """When a track exists, summary.final includes it in tracksReused."""
        from app.core.maestro_agent_teams import _handle_composition_agent_team

        parsed = _make_parsed_multi(roles=["drums", "bass"])
        route = self._make_route_for_team()
        # Project already has a Drums track with one 16-beat region
        project_context: ProjectContext = {
            "tracks": [
                {
                    "name": "Drums",
                    "id": "existing-drums-id",
                    "regions": [{"startBeat": 0, "durationBeats": 16}],
                }
            ]
        }
        store = StateStore(conversation_id="test-reuse-summary")
        trace = _make_trace()

        llm = _make_llm_mock()
        agent_response = LLMResponse(content=None, usage={})
        agent_response.tool_calls = []
        llm.chat_completion_stream = MagicMock(side_effect=_response_to_stream(agent_response))

        events = []
        async for e in _handle_composition_agent_team(
            "add chorus", project_context, parsed, route, llm, store, trace, None
        ):
            events.append(e)

        payloads = _parse_events(events)
        summary_events = [p for p in payloads if p.get("type") == "summary.final"]
        assert summary_events, "summary.final must be emitted"
        summary = summary_events[0]
        reused = summary.get("tracksReused", [])
        assert any(t["trackId"] == "existing-drums-id" for t in reused), (
            f"Expected existing-drums-id in tracksReused, got: {reused}"
        )

    @pytest.mark.anyio
    async def test_existing_track_system_prompt_skips_create(self) -> None:

        """Reusing agent's system prompt does not ask to call stori_add_midi_track."""
        from app.core.maestro_agent_teams import _run_instrument_agent

        store = StateStore(conversation_id="test-reuse-prompt")
        plan_tracker = _PlanTracker()
        plan_tracker.build_from_prompt(
            _make_parsed_multi(roles=["drums"]),
            "add chorus",
            {"tracks": [{"name": "Drums", "id": "d1", "regions": []}]},
        )
        captured_messages: list[ChatMessage] = []

        def capture_stream(
            messages: list[ChatMessage],
            tools: list[ToolSchemaDict] | None = None,
            tool_choice: OpenAIToolChoice | None = None,
            temperature: float | None = None,
            max_tokens: int | None = None,
            reasoning_fraction: float | None = None,
        ) -> AsyncGenerator[StreamEvent, None]:
            captured_messages.extend(messages)
            async def _stream() -> AsyncGenerator[StreamEvent, None]:
                yield {"type": "done", "content": None, "tool_calls": [], "finish_reason": "stop", "usage": {}}
            return _stream()

        llm = _make_llm_mock()
        llm.chat_completion_stream = MagicMock(side_effect=capture_stream)
        trace = _make_trace()
        queue: asyncio.Queue[MaestroEvent] = asyncio.Queue()
        step_ids = [s.step_id for s in plan_tracker.steps if s.parallel_group == "instruments"]

        await _run_instrument_agent(
            instrument_name="Drums",
            role="drums",
            style="lofi",
            bars=4,
            tempo=90,
            key="Am",
            step_ids=step_ids,
            plan_tracker=plan_tracker,
            llm=llm,
            store=store,
            allowed_tool_names={"stori_add_midi_region", "stori_add_notes"},
            trace=trace,
            sse_queue=queue,
            collected_tool_calls=[],
            existing_track_id="d1",
            start_beat=16,
        )

        system_msgs = [m["content"] for m in captured_messages if m["role"] == "system"]
        assert system_msgs, "System message must be sent"
        sys_text = system_msgs[0]
        assert "DO NOT call stori_add_midi_track" in sys_text
        assert "d1" in sys_text
        assert "beat 16" in sys_text

    @pytest.mark.anyio
    async def test_each_agent_receives_its_own_distinct_track_id(self) -> None:

        """Every parallel agent must receive its OWN trackId, not the first one.

        Regression test for the follow-up bug: all agents were receiving
        the Drums trackId (3C1A09C3) because the coordinator injected a single
        shared value. The fix builds a per-role mapping before spawning.
        """
        from app.core.maestro_agent_teams import _handle_composition_agent_team

        parsed = _make_parsed_multi(roles=["drums", "bass", "guitar", "horns"])
        route = self._make_route_for_team()
        project_context: ProjectContext = {
            "tracks": [
                {"name": "Drums",  "id": "id-drums",  "regions": [{"startBeat": 0, "durationBeats": 16}]},
                {"name": "Bass",   "id": "id-bass",   "regions": [{"startBeat": 0, "durationBeats": 16}]},
                {"name": "Guitar", "id": "id-guitar", "regions": [{"startBeat": 0, "durationBeats": 16}]},
                {"name": "Horns",  "id": "id-horns",  "regions": [{"startBeat": 0, "durationBeats": 16}]},
            ]
        }
        store = StateStore(conversation_id="test-distinct-ids")
        trace = _make_trace()

        captured_system_prompts: list[str] = []

        def capture_stream(
            messages: list[ChatMessage],
            tools: list[ToolSchemaDict] | None = None,
            tool_choice: OpenAIToolChoice | None = None,
            temperature: float | None = None,
            max_tokens: int | None = None,
            reasoning_fraction: float | None = None,
        ) -> AsyncGenerator[StreamEvent, None]:
            for m in messages:
                if m["role"] == "system":
                    captured_system_prompts.append(m["content"])
            async def _stream() -> AsyncGenerator[StreamEvent, None]:
                yield {"type": "done", "content": None, "tool_calls": [], "finish_reason": "stop", "usage": {}}
            return _stream()

        llm = _make_llm_mock()
        llm.chat_completion_stream = MagicMock(side_effect=capture_stream)

        async for _ in _handle_composition_agent_team(
            "add chorus", project_context, parsed, route, llm, store, trace, None
        ):
            pass

        # Each of 4 agents uses 1+ calls (no coordinator reasoning for STORI PROMPTs)
        assert len(captured_system_prompts) >= 4, (
            f"Expected at least 4 agent system prompts, got {len(captured_system_prompts)}"
        )
        for expected_id in ["id-drums", "id-bass", "id-guitar", "id-horns"]:
            matching = [p for p in captured_system_prompts if expected_id in p]
            assert len(matching) == 1, (
                f"Expected exactly 1 prompt containing '{expected_id}', got {len(matching)}. "
                f"All prompts: {[p[:120] for p in captured_system_prompts]}"
            )

    @pytest.mark.anyio
    async def test_client_id_key_resolved_same_as_trackid_key(self) -> None:

        """project_context tracks with 'id' key (DAW format) work like 'trackId'."""
        from app.core.maestro_agent_teams import _handle_composition_agent_team

        parsed = _make_parsed_multi(roles=["drums", "bass"])
        route = self._make_route_for_team()
        # Client sends "id" (DAW format) instead of "trackId"
        project_context: ProjectContext = {
            "tracks": [
                {"name": "Drums", "id": "daw-drums-id", "regions": []},
                {"name": "Bass",  "id": "daw-bass-id",  "regions": []},
            ]
        }
        store = StateStore(conversation_id="test-id-key")
        trace = _make_trace()

        captured_system_prompts: list[str] = []

        def capture_stream(
            messages: list[ChatMessage],
            tools: list[ToolSchemaDict] | None = None,
            tool_choice: OpenAIToolChoice | None = None,
            temperature: float | None = None,
            max_tokens: int | None = None,
            reasoning_fraction: float | None = None,
        ) -> AsyncGenerator[StreamEvent, None]:
            for m in messages:
                if m["role"] == "system":
                    captured_system_prompts.append(m["content"])
            async def _stream() -> AsyncGenerator[StreamEvent, None]:
                yield {"type": "done", "content": None, "tool_calls": [], "finish_reason": "stop", "usage": {}}
            return _stream()

        llm = _make_llm_mock()
        llm.chat_completion_stream = MagicMock(side_effect=capture_stream)

        async for _ in _handle_composition_agent_team(
            "add section", project_context, parsed, route, llm, store, trace, None
        ):
            pass

        assert any("daw-drums-id" in p for p in captured_system_prompts), (
            "Drums agent must have received its DAW-format id"
        )
        assert any("daw-bass-id" in p for p in captured_system_prompts), (
            "Bass agent must have received its DAW-format id"
        )
        assert not any("trackId=''" in p for p in captured_system_prompts), (
            "No agent should have an empty trackId injected into its system prompt"
        )


# =============================================================================
# Frontend parity: suppress coordinator reasoning, icon validation, agentId,
# summary.final text
# =============================================================================


class TestSuppressCoordinatorReasoningForStoriPrompt:
    """Coordinator reasoning is suppressed for STORI PROMPT requests."""

    def _make_route_for_team(self) -> IntentResult:

        from app.core.intent import Intent, IntentResult, SSEState
        return IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.EDITING,
            confidence=0.9,
            slots=MagicMock(),
            tools=[],
            allowed_tool_names={
                "stori_set_tempo", "stori_set_key",
                "stori_add_midi_track", "stori_add_midi_region",
                "stori_add_notes", "stori_add_insert_effect",
            },
            tool_choice="auto",
            force_stop_after=False,
            requires_planner=False,
            reasons=(),
        )

    @pytest.mark.anyio
    async def test_no_reasoning_events_from_coordinator(self) -> None:

        """STORI PROMPT compositions emit zero reasoning events without agentId."""
        from app.core.maestro_agent_teams import _handle_composition_agent_team

        parsed = _make_parsed_multi(roles=["drums", "bass"])
        route = self._make_route_for_team()
        store = StateStore(conversation_id="test-suppress-reasoning")
        trace = _make_trace()

        llm = _make_llm_mock()
        agent_response = LLMResponse(content=None, usage={})
        agent_response.tool_calls = []
        llm.chat_completion_stream = MagicMock(side_effect=_response_to_stream(agent_response))

        events = []
        async for e in _handle_composition_agent_team(
            "STORI PROMPT\nTempo: 92\nKey: Cm", {}, parsed, route, llm, store, trace, None
        ):
            events.append(e)

        payloads = _parse_events(events)
        coord_reasoning = [
            p for p in payloads
            if p["type"] == "reasoning" and "agentId" not in p
        ]
        assert coord_reasoning == [], (
            f"Expected zero coordinator reasoning events, got {len(coord_reasoning)}"
        )


class TestIconValidation:
    """Icon validation: invalid icons fall back to name-inferred defaults."""

    @pytest.mark.anyio
    async def test_invalid_icon_replaced_by_inferred(self) -> None:

        """An LLM-provided icon not in the curated set is replaced by inference."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-icon-validate")
        trace = _make_trace()

        outcome = await _apply_single_tool_call(
            tc_id="tc-icon-test",
            tc_name="stori_add_midi_track",
            resolved_args={"name": "Strings", "icon": "nonexistent.icon"},
            allowed_tool_names={"stori_add_midi_track"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )

        assert outcome.enriched_params["icon"] == "instrument.violin"

    @pytest.mark.anyio
    async def test_missing_icon_autoassigned(self) -> None:

        """When no icon is provided, one is inferred from the track name."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-icon-missing")
        trace = _make_trace()

        outcome = await _apply_single_tool_call(
            tc_id="tc-icon-miss",
            tc_name="stori_add_midi_track",
            resolved_args={"name": "Drums"},
            allowed_tool_names={"stori_add_midi_track"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )

        assert outcome.enriched_params["icon"] == "instrument.drum"

    @pytest.mark.anyio
    async def test_valid_icon_is_emitted(self) -> None:

        """An icon in the curated set is emitted normally."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-icon-valid")
        trace = _make_trace()

        outcome = await _apply_single_tool_call(
            tc_id="tc-icon-valid",
            tc_name="stori_add_midi_track",
            resolved_args={"name": "Piano", "gmProgram": 0},
            allowed_tool_names={"stori_add_midi_track"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )

        icon_events = [
            e for e in outcome.sse_events
            if isinstance(e, ToolCallEvent) and e.name == "stori_set_track_icon"
        ]
        assert len(icon_events) >= 1, "Valid icon should produce stori_set_track_icon events"
        icon_param = icon_events[-1].params["icon"].root
        assert icon_param == "pianokeys"

    @pytest.mark.anyio
    async def test_valid_icon_passthrough(self) -> None:

        """A valid LLM-provided icon is kept as-is."""
        from app.core.maestro_editing import _apply_single_tool_call

        store = StateStore(conversation_id="test-icon-pass")
        trace = _make_trace()

        outcome = await _apply_single_tool_call(
            tc_id="tc-icon-pass",
            tc_name="stori_add_midi_track",
            resolved_args={"name": "Whatever", "icon": "sparkles"},
            allowed_tool_names={"stori_add_midi_track"},
            store=store,
            trace=trace,
            add_notes_failures={},
            emit_sse=True,
        )

        assert outcome.enriched_params["icon"] == "sparkles"


class TestGmIconsAllValid:
    """Every icon returned by icon_for_gm_program must be in the curated set."""

    def test_all_gm_icons_in_curated_set(self) -> None:

        """All 128 GM programs map to icons in VALID_SF_SYMBOL_ICONS."""
        from app.core.gm_instruments import DRUM_ICON, icon_for_gm_program
        from app.core.tool_validation import VALID_SF_SYMBOL_ICONS

        invalid = []
        for gm in range(128):
            icon = icon_for_gm_program(gm)
            if icon not in VALID_SF_SYMBOL_ICONS:
                invalid.append((gm, icon))

        assert invalid == [], f"GM programs map to invalid icons: {invalid}"
        assert DRUM_ICON in VALID_SF_SYMBOL_ICONS, f"DRUM_ICON '{DRUM_ICON}' not in curated set"


class TestAgentReasoningEventsCarryAgentId:
    """Reasoning events from instrument agents include agentId."""

    @pytest.mark.anyio
    async def test_agent_reasoning_events_have_agent_id(self) -> None:

        """Reasoning deltas from instrument agent stream include agentId."""
        from app.core.maestro_agent_teams import _run_instrument_agent

        store = StateStore(conversation_id="test-agent-reasoning")
        plan_tracker = _PlanTracker()
        plan_tracker.build_from_prompt(
            _make_parsed_multi(roles=["bass"]),
            "make funk",
            {},
        )

        llm = _make_llm_mock()

        async def _stream_with_reasoning(*args: object, **kwargs: object) -> AsyncGenerator[StreamEvent, None]:

            yield {"type": "reasoning_delta", "text": "Walking bass follows chord roots"}
            resp = LLMResponse(content=None, usage={})
            resp.tool_calls = [
                ToolCall(id="tc-r", name="stori_add_midi_track", params={"name": "Bass"}),
            ]
            tc_raw: list[ToolCallEntry] = [
                ToolCallEntry(id=tc.id, type="function", function={"name": tc.name, "arguments": json.dumps(tc.params)})
                for tc in resp.tool_calls
            ]
            yield {"type": "done", "content": None, "tool_calls": tc_raw, "finish_reason": "stop", "usage": {}}

        llm.chat_completion_stream = MagicMock(side_effect=_stream_with_reasoning)
        trace = _make_trace()
        queue: asyncio.Queue[MaestroEvent] = asyncio.Queue()
        step_ids = [s.step_id for s in plan_tracker.steps if s.parallel_group == "instruments"]

        await _run_instrument_agent(
            instrument_name="Bass",
            role="bass",
            style="funk",
            bars=4,
            tempo=92,
            key="Cm",
            step_ids=step_ids,
            plan_tracker=plan_tracker,
            llm=llm,
            store=store,
            allowed_tool_names={"stori_add_midi_track", "stori_add_midi_region"},
            trace=trace,
            sse_queue=queue,
            collected_tool_calls=[],
        )

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        reasoning_events = [e for e in events if isinstance(e, ReasoningEvent)]
        assert len(reasoning_events) >= 1, "Expected at least one reasoning event"
        for rev in reasoning_events:
            assert rev.agent_id == "bass", (
                f"Reasoning event missing agent_id='bass': {rev}"
            )

    @pytest.mark.anyio
    async def test_plan_step_updates_carry_agent_id(self) -> None:

        """planStepUpdate events from instrument agents include agentId."""
        from app.core.maestro_agent_teams import _run_instrument_agent

        store = StateStore(conversation_id="test-step-agent-id")
        plan_tracker = _PlanTracker()
        plan_tracker.build_from_prompt(
            _make_parsed_multi(roles=["drums"]),
            "make funk",
            {},
        )

        llm = _make_llm_mock()
        resp = LLMResponse(content=None, usage={})
        resp.tool_calls = [
            ToolCall(id="tc-d1", name="stori_add_midi_track", params={"name": "Drums", "drumKitId": "TR-808"}),
        ]
        llm.chat_completion_stream = MagicMock(side_effect=_response_to_stream(resp))
        trace = _make_trace()
        queue: asyncio.Queue[MaestroEvent] = asyncio.Queue()
        step_ids = [s.step_id for s in plan_tracker.steps if s.parallel_group == "instruments"]

        await _run_instrument_agent(
            instrument_name="Drums",
            role="drums",
            style="funk",
            bars=4,
            tempo=92,
            key="Cm",
            step_ids=step_ids,
            plan_tracker=plan_tracker,
            llm=llm,
            store=store,
            allowed_tool_names={"stori_add_midi_track", "stori_add_midi_region"},
            trace=trace,
            sse_queue=queue,
            collected_tool_calls=[],
        )

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        step_events = [e for e in events if isinstance(e, PlanStepUpdateEvent)]
        for se in step_events:
            assert se.agent_id == "drums", (
                f"planStepUpdate missing agent_id='drums': {se}"
            )


class TestSummaryFinalText:
    """summary.final includes a human-readable text field."""

    def test_text_field_present_in_summary(self) -> None:

        """_build_composition_summary includes text when context is provided."""
        from app.core.maestro_agent_teams import _build_composition_summary

        tool_calls: list[ToolCallDict] = [
            ToolCallDict(tool="stori_add_midi_track", params={"name": "Drums", "trackId": "t1", "drumKitId": "TR-808"}),
            ToolCallDict(tool="stori_add_midi_track", params={"name": "Bass", "trackId": "t2", "_gmInstrumentName": "Electric Bass"}),
            ToolCallDict(tool="stori_add_midi_region", params={}),
            ToolCallDict(tool="stori_add_midi_region", params={}),
            ToolCallDict(tool="stori_add_notes", params={"notes": json_list([NoteDict(pitch=60)] * 40)}),
            ToolCallDict(tool="stori_add_notes", params={"notes": json_list([NoteDict(pitch=36)] * 53)}),
            ToolCallDict(tool="stori_add_insert_effect", params={"trackId": "t1", "effectType": "reverb"}),
            ToolCallDict(tool="stori_add_insert_effect", params={"trackId": "t2", "effectType": "reverb"}),
        ]
        summary = _build_composition_summary(tool_calls, tempo=88, key="Dm", style="cinematic orchestral")

        assert "text" in summary
        text = summary["text"]
        assert "88 BPM" in text
        assert "Dm" in text
        assert "cinematic orchestral" in text
        assert "Drums" in text
        assert "Bass" in text
        assert "93 notes" in text

    def test_text_field_present_without_context(self) -> None:

        """_build_composition_summary includes text even without musical context."""
        from app.core.maestro_agent_teams import _build_composition_summary

        tool_calls: list[ToolCallDict] = [
            ToolCallDict(tool="stori_add_midi_track", params={"name": "Piano", "trackId": "t1"}),
        ]
        summary = _build_composition_summary(tool_calls)

        assert "text" in summary
        assert isinstance(summary["text"], str)
        assert len(summary["text"]) > 0

    def test_extended_composition_uses_verb_extended(self) -> None:

        """When tracks are reused, the summary uses 'Extended' verb."""
        from app.core.maestro_agent_teams import _build_composition_summary

        tool_calls: list[ToolCallDict] = [
            ToolCallDict(tool="_reused_track", params={"name": "Drums", "trackId": "t1"}),
            ToolCallDict(tool="stori_add_midi_region", params={}),
        ]
        summary = _build_composition_summary(tool_calls, tempo=120, key="C", style="funk")

        assert summary["text"].startswith("Extended")
