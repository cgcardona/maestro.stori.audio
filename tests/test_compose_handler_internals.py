"""Tests for compose handler internal functions (_handle_reasoning, _handle_composing, _handle_editing, _stream_llm_response).

Supplements test_compose_handlers.py with deeper coverage of handler internals
and execution mode policy.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.compose_handlers import (
    UsageTracker,
    StreamFinalResponse,
    _handle_reasoning,
    _handle_composing,
    _handle_editing,
    _retry_composing_as_editing,
    _stream_llm_response,
    orchestrate,
)
from app.core.intent import Intent, IntentResult, SSEState
from app.core.llm_client import LLMResponse, ToolCallData
from app.core.state_store import StateStore
from app.core.tracing import TraceContext


def _parse_events(events: list[str]) -> list[dict]:
    """Parse SSE event strings into dicts."""
    parsed = []
    for e in events:
        if "data:" in e:
            parsed.append(json.loads(e.split("data: ", 1)[1].strip()))
    return parsed


def _make_trace():
    return TraceContext(trace_id="test-trace-id")


def _make_route(sse_state=SSEState.REASONING, intent=Intent.UNKNOWN, **kwargs):
    defaults = dict(
        intent=intent,
        sse_state=sse_state,
        confidence=0.9,
        slots={},
        tools=[],
        allowed_tool_names=set(),
        tool_choice="none",
        force_stop_after=False,
        requires_planner=False,
        reasons=(),
    )
    defaults.update(kwargs)
    return IntentResult(**defaults)


def _make_llm_mock(content="Hello", supports_reasoning=False, tool_calls=None):
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
    async def test_general_question_non_reasoning_model(self):
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
        assert any("The answer is 4" in p.get("content", "") for p in content_p)

    @pytest.mark.anyio
    async def test_reasoning_model_streams_deltas(self):
        """Reasoning model: chat_completion_stream yields reasoning + content deltas."""
        llm = _make_llm_mock(supports_reasoning=True)
        trace = _make_trace()
        route = _make_route(SSEState.REASONING, Intent.UNKNOWN)

        async def fake_stream(*args, **kwargs):
            yield {"type": "reasoning_delta", "text": "Thinking about this..."}
            yield {"type": "content_delta", "text": "The answer."}
            yield {"type": "done", "content": "The answer.", "usage": {"prompt_tokens": 5, "completion_tokens": 10}}

        llm.chat_completion_stream = MagicMock(side_effect=fake_stream)

        events = []
        async for e in _handle_reasoning("question?", {}, route, llm, trace, UsageTracker(), []):
            events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        assert "reasoning" in types or "content" in types
        assert "complete" in types

    @pytest.mark.anyio
    async def test_reasoning_tracks_usage(self):
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
    async def test_rag_path_ask_stori_docs(self):
        """ASK_STORI_DOCS intent routes through RAG when collection exists."""
        llm = _make_llm_mock()
        trace = _make_trace()
        route = _make_route(SSEState.REASONING, Intent.ASK_STORI_DOCS)

        mock_rag = MagicMock()
        mock_rag.collection_exists.return_value = True
        async def rag_answer(*a, **k):
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
        assert "RAG answer part 1" in content_events[0]["content"]

    @pytest.mark.anyio
    async def test_rag_fallback_on_error(self):
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
        assert any("Fallback answer" in p["content"] for p in content_events)

    @pytest.mark.anyio
    async def test_includes_conversation_history(self):
        """Conversation history is passed to LLM messages."""
        llm = _make_llm_mock(content="With context")
        trace = _make_trace()
        route = _make_route(SSEState.REASONING)
        history = [{"role": "user", "content": "prev"}, {"role": "assistant", "content": "prev answer"}]

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
    async def test_variation_mode_emits_meta_phrases_done(self):
        """COMPOSING in variation mode emits meta -> phrase(s) -> done -> complete."""
        from app.core.pipeline import PipelineOutput
        from app.core.planner import ExecutionPlan
        from app.core.expansion import ToolCall
        from app.models.variation import Variation, Phrase, NoteChange, MidiNoteSnapshot

        route = _make_route(SSEState.COMPOSING, Intent.GENERATE_MUSIC, requires_planner=True)
        llm = _make_llm_mock()
        store = MagicMock(spec=StateStore)
        trace = _make_trace()

        plan = ExecutionPlan(
            tool_calls=[ToolCall("stori_add_notes", {"regionId": "r1", "notes": []})],
            safety_validated=True,
        )
        fake_output = PipelineOutput(route=route, plan=plan)
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
            patch("app.core.compose_handlers.run_pipeline", new_callable=AsyncMock, return_value=fake_output),
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
        assert meta["variation_id"] == "var-123"
        phrase = next(p for p in payloads if p["type"] == "phrase")
        assert phrase["phrase_id"] == "p1"

    @pytest.mark.anyio
    async def test_empty_plan_asks_for_clarification(self):
        """When planner returns no tool_calls and no function-call text, asks for clarification."""
        from app.core.pipeline import PipelineOutput
        from app.core.planner import ExecutionPlan

        route = _make_route(SSEState.COMPOSING, Intent.GENERATE_MUSIC, requires_planner=True)
        llm = _make_llm_mock()
        store = MagicMock(spec=StateStore)
        trace = _make_trace()

        plan = ExecutionPlan(tool_calls=[], safety_validated=False, llm_response_text="I'm not sure what to do.")
        fake_output = PipelineOutput(route=route, plan=plan)

        with patch("app.core.compose_handlers.run_pipeline", new_callable=AsyncMock, return_value=fake_output):
            events = []
            async for e in _handle_composing("do something", {}, route, llm, store, trace, None, None):
                events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        assert "content" in types
        assert "complete" in types
        content_p = next(p for p in payloads if p["type"] == "content")
        assert "style" in content_p["content"].lower() or "genre" in content_p["content"].lower()

    @pytest.mark.anyio
    async def test_empty_plan_no_response_text(self):
        """When planner returns no tool_calls and no response text, asks for info."""
        from app.core.pipeline import PipelineOutput
        from app.core.planner import ExecutionPlan

        route = _make_route(SSEState.COMPOSING, Intent.GENERATE_MUSIC, requires_planner=True)
        llm = _make_llm_mock()
        store = MagicMock(spec=StateStore)
        trace = _make_trace()

        plan = ExecutionPlan(tool_calls=[], safety_validated=False)
        fake_output = PipelineOutput(route=route, plan=plan)

        with patch("app.core.compose_handlers.run_pipeline", new_callable=AsyncMock, return_value=fake_output):
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
    async def test_editing_apply_mode_emits_tool_calls(self):
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
        response.tool_calls = [ToolCallData(id="tc1", name="stori_set_tempo", arguments={"tempo": 120})]

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
        assert "tool_call" in types
        tc = next(p for p in payloads if p["type"] == "tool_call")
        assert tc["name"] == "stori_set_tempo"
        assert tc["params"]["tempo"] == 120

    @pytest.mark.anyio
    async def test_editing_variation_mode_emits_variation_events(self):
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
        response.tool_calls = [ToolCallData(id="tc1", name="stori_set_tempo", arguments={"tempo": 120})]

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
        # In variation mode, should NOT emit raw tool_call events
        assert "tool_call" not in types
        assert "meta" in types
        assert "done" in types

    @pytest.mark.anyio
    async def test_editing_tool_validation_error_retries(self):
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
        bad_response.tool_calls = [ToolCallData(id="tc1", name="stori_delete_track", arguments={"trackId": "x"})]

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
        assert "tool_error" in types

    @pytest.mark.anyio
    async def test_editing_force_stop_after(self):
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
            ToolCallData(id="tc1", name="stori_set_tempo", arguments={"tempo": 120}),
            ToolCallData(id="tc2", name="stori_set_tempo", arguments={"tempo": 130}),
        ]

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(return_value=response)
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        events = []
        async for e in _handle_editing("set tempo", {}, route, llm, store, trace, None, [], "apply"):
            events.append(e)

        payloads = _parse_events(events)
        tc_events = [p for p in payloads if p["type"] == "tool_call"]
        # enforce_single_tool should reduce to 1
        assert len(tc_events) == 1

    @pytest.mark.anyio
    async def test_editing_no_tool_calls_returns_content(self):
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
    async def test_editing_creates_track_entity_with_uuid(self):
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
        response.tool_calls = [ToolCallData(id="tc1", name="stori_add_midi_track", arguments={"name": "Drums"})]

        done_response = LLMResponse(content="Done.", usage={"prompt_tokens": 5, "completion_tokens": 5})

        llm = _make_llm_mock()
        llm.chat_completion = AsyncMock(side_effect=[response, done_response])
        store = StateStore(conversation_id="test")
        trace = _make_trace()

        events = []
        async for e in _handle_editing("add drums", {}, route, llm, store, trace, None, [], "apply"):
            events.append(e)

        payloads = _parse_events(events)
        tc_events = [p for p in payloads if p["type"] == "tool_call"]
        assert len(tc_events) >= 1
        # trackId should be a valid UUID
        import uuid
        track_id = tc_events[0]["params"]["trackId"]
        uuid.UUID(track_id)  # should not raise


# ---------------------------------------------------------------------------
# _stream_llm_response
# ---------------------------------------------------------------------------


class TestStreamLLMResponse:

    @pytest.mark.anyio
    async def test_yields_reasoning_and_content_deltas(self):
        """Streams reasoning and content deltas, ends with StreamFinalResponse."""
        llm = _make_llm_mock(supports_reasoning=True)
        trace = _make_trace()

        async def fake_stream(*args, **kwargs):
            yield {"type": "reasoning_delta", "text": "Let me think..."}
            yield {"type": "content_delta", "text": "Here's the answer."}
            yield {"type": "done", "content": "Here's the answer.", "tool_calls": [], "usage": {"prompt_tokens": 10, "completion_tokens": 20}}

        llm.chat_completion_stream = MagicMock(side_effect=fake_stream)

        from app.core.sse_utils import sse_event

        items = []
        async for item in _stream_llm_response(llm, [], [], "auto", trace, lambda data: sse_event(data)):
            items.append(item)

        # Last item should be StreamFinalResponse
        assert isinstance(items[-1], StreamFinalResponse)
        assert items[-1].response.content == "Here's the answer."

        # Other items are SSE event strings
        sse_items = [i for i in items if isinstance(i, str)]
        assert len(sse_items) >= 2

    @pytest.mark.anyio
    async def test_parses_tool_calls_from_done(self):
        """Tool calls in done chunk are parsed into LLMResponse."""
        llm = _make_llm_mock(supports_reasoning=True)
        trace = _make_trace()

        async def fake_stream(*args, **kwargs):
            yield {
                "type": "done",
                "content": None,
                "tool_calls": [
                    {"id": "tc1", "function": {"name": "stori_set_tempo", "arguments": '{"tempo": 120}'}},
                ],
                "usage": {},
            }

        llm.chat_completion_stream = MagicMock(side_effect=fake_stream)

        from app.core.sse_utils import sse_event

        items = []
        async for item in _stream_llm_response(llm, [], [], "auto", trace, lambda data: sse_event(data)):
            items.append(item)

        final = items[-1]
        assert isinstance(final, StreamFinalResponse)
        assert len(final.response.tool_calls) == 1
        assert final.response.tool_calls[0].name == "stori_set_tempo"
        assert final.response.tool_calls[0].arguments == {"tempo": 120}


# ---------------------------------------------------------------------------
# _retry_composing_as_editing
# ---------------------------------------------------------------------------


class TestRetryComposingAsEditing:

    @pytest.mark.anyio
    async def test_emits_retry_status_then_delegates_to_editing(self):
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
        retry_status = next(p for p in payloads if p["type"] == "status" and "Retry" in p.get("message", ""))
        assert retry_status is not None


# ---------------------------------------------------------------------------
# orchestrate() execution mode policy
# ---------------------------------------------------------------------------


class TestOrchestrateExecutionModePolicy:

    @pytest.mark.anyio
    async def test_composing_forces_variation_mode(self):
        """COMPOSING intent sets execution_mode='variation' internally."""
        from app.core.pipeline import PipelineOutput
        from app.core.planner import ExecutionPlan
        from app.core.expansion import ToolCall
        from app.models.variation import Variation

        fake_route = _make_route(SSEState.COMPOSING, Intent.GENERATE_MUSIC, requires_planner=True)
        plan = ExecutionPlan(
            tool_calls=[ToolCall("stori_add_notes", {"regionId": "r1", "notes": []})],
            safety_validated=True,
        )
        fake_output = PipelineOutput(route=fake_route, plan=plan)
        fake_variation = Variation(
            variation_id="var-policy",
            intent="beat",
            affected_tracks=[],
            affected_regions=[],
            beat_range=(0.0, 0.0),
            phrases=[],
        )

        with (
            patch("app.core.compose_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route),
            patch("app.core.compose_handlers.run_pipeline", new_callable=AsyncMock, return_value=fake_output),
            patch("app.core.compose_handlers.LLMClient") as m_cls,
            patch("app.core.executor.execute_plan_variation", new_callable=AsyncMock, return_value=fake_variation),
        ):
            m_cls.return_value = _make_llm_mock()
            events = []
            async for e in orchestrate("make a beat"):
                events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        # Variation mode should emit meta + done
        assert "meta" in types
        assert "done" in types

    @pytest.mark.anyio
    async def test_editing_forces_apply_mode(self):
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
        response.tool_calls = [ToolCallData(id="tc1", name="stori_set_tempo", arguments={"tempo": 120})]
        done_response = LLMResponse(content="Done.", usage={"prompt_tokens": 5, "completion_tokens": 5})

        mock_llm = _make_llm_mock()
        mock_llm.chat_completion = AsyncMock(side_effect=[response, done_response])

        with (
            patch("app.core.compose_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route),
            patch("app.core.compose_handlers.LLMClient") as m_cls,
        ):
            m_cls.return_value = mock_llm
            events = []
            async for e in orchestrate("set tempo to 120"):
                events.append(e)

        payloads = _parse_events(events)
        types = [p["type"] for p in payloads]
        # Apply mode should emit tool_call events directly
        assert "tool_call" in types
        # Should NOT have variation events
        assert "meta" not in types
