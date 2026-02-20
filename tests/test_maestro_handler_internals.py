"""Tests for maestro handler internal functions (_handle_reasoning, _handle_composing, _handle_editing, _stream_llm_response).

Supplements test_maestro_handlers.py with deeper coverage of maestro handler internals
and execution mode policy.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.maestro_handlers import (
    UsageTracker,
    StreamFinalResponse,
    _handle_reasoning,
    _handle_composing,
    _handle_editing,
    _retry_composing_as_editing,
    _stream_llm_response,
    _PlanTracker,
    _PlanStep,
    _build_step_result,
    orchestrate,
)
from app.core.expansion import ToolCall
from app.core.intent import Intent, IntentResult, SSEState
from app.core.llm_client import LLMResponse
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
        store.get_state_id.return_value = "1"
        store.conversation_id = "test-conv-id"
        store.registry = MagicMock()
        store.registry.get_region.return_value = None
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
            patch("app.core.maestro_handlers.run_pipeline", new_callable=AsyncMock, return_value=fake_output),
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

        with patch("app.core.maestro_handlers.run_pipeline", new_callable=AsyncMock, return_value=fake_output):
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

        with patch("app.core.maestro_handlers.run_pipeline", new_callable=AsyncMock, return_value=fake_output):
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
    async def test_editing_emits_content_alongside_tool_calls(self):
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
        all_content = " ".join(c["content"] for c in content_events)
        assert "tempo" in all_content.lower() or "120" in all_content

    @pytest.mark.anyio
    async def test_editing_strips_tool_echo_from_content(self):
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
        all_content = " ".join(c["content"] for c in content_events)
        # Natural language preserved
        assert "Setting the tempo" in all_content
        assert "Done with tempo" in all_content
        # Tool-call syntax stripped
        assert "(tempo=120)" not in all_content

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
    async def test_editing_persists_notes_to_state_store(self):
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
        notes_payload = [
            {"pitch": 60, "startBeat": 0.0, "durationBeats": 1.0, "velocity": 100},
            {"pitch": 62, "startBeat": 1.0, "durationBeats": 0.5, "velocity": 90},
        ]
        response = LLMResponse(content=None, usage={"prompt_tokens": 5, "completion_tokens": 5})
        response.tool_calls = [
            ToolCall(id="tc1", name="stori_add_notes", params={
                "regionId": region_id,
                "notes": notes_payload,
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
    async def test_editing_variation_meta_includes_base_state_id(self):
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
        uuid.UUID(track_id)  # should not raise

    @pytest.mark.anyio
    async def test_synthetic_set_track_icon_emitted_after_add_track_with_gm_program(self):
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
        # GM 33 = Electric Bass Guitar → waveform.path
        assert icon_call["params"]["icon"] == "waveform.path"
        # trackId must match the generated UUID from stori_add_midi_track
        track_call = next(p for p in tc_events if p["name"] == "stori_add_midi_track")
        assert icon_call["params"]["trackId"] == track_call["params"]["trackId"]

    @pytest.mark.anyio
    async def test_synthetic_set_track_icon_uses_drum_icon_for_drum_kit(self):
        """stori_set_track_icon uses music.note.list when drumKitId is set."""
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
        assert icon_calls[0]["params"]["icon"] == "music.note.list"


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
        assert final.response.tool_calls[0].params == {"tempo": 120}


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
        """COMPOSING intent sets execution_mode='variation' internally (requires non-empty project)."""
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
        # Non-empty project so the empty-project override doesn't kick in
        project_ctx = {"id": "p1", "tracks": [{"id": "t1", "name": "Track 1"}]}

        with (
            patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route),
            patch("app.core.maestro_handlers.run_pipeline", new_callable=AsyncMock, return_value=fake_output),
            patch("app.core.maestro_handlers.LLMClient") as m_cls,
            patch("app.core.executor.execute_plan_variation", new_callable=AsyncMock, return_value=fake_variation),
        ):
            m_cls.return_value = _make_llm_mock()
            events = []
            async for e in orchestrate("make a beat", project_context=project_ctx):
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

    def _make_tool_calls(self, specs: list[tuple[str, dict]]) -> list[ToolCall]:
        return [
            ToolCall(id=f"tc{i}", name=name, params=params)
            for i, (name, params) in enumerate(specs)
        ]

    def test_group_setup_tools_into_individual_steps(self):
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

    def test_group_track_with_content(self):
        """Track creation followed by region+notes = one step."""
        tcs = self._make_tool_calls([
            ("stori_add_midi_track", {"name": "Drums", "drumKitId": "TR-808"}),
            ("stori_add_midi_region", {"trackId": "$0.trackId", "startBeat": 0, "durationBeats": 16}),
            ("stori_add_notes", {"regionId": "$1.regionId", "notes": []}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Add drums", {}, False, StateStore(conversation_id="t"))
        assert len(tracker.steps) == 1
        assert "Drums" in tracker.steps[0].label
        assert "content" in tracker.steps[0].label
        assert tracker.steps[0].track_name == "Drums"
        assert tracker.steps[0].detail == "TR-808"

    def test_group_multiple_tracks(self):
        """Multiple track creations should produce separate steps."""
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
        assert len(tracker.steps) == 3  # tempo step + drums + bass
        assert "tempo" in tracker.steps[0].label.lower()
        assert tracker.steps[1].track_name == "Drums"
        assert tracker.steps[2].track_name == "Bass"
        assert tracker.steps[2].detail == "GM 33"

    def test_effects_grouped(self):
        """Effect tools should be grouped together."""
        tcs = self._make_tool_calls([
            ("stori_ensure_bus", {"name": "Reverb"}),
            ("stori_add_insert_effect", {"trackId": "t1", "type": "chorus"}),
            ("stori_add_send", {"trackId": "t1", "busId": "$0.busId"}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Add effects", {}, False, StateStore(conversation_id="t"))
        assert len(tracker.steps) == 1
        assert "effect" in tracker.steps[0].label.lower()
        assert "Reverb bus" in (tracker.steps[0].detail or "")
        assert "chorus" in (tracker.steps[0].detail or "")

    def test_plan_event_shape(self):
        """to_plan_event() must match the SSE wire format."""
        tcs = self._make_tool_calls([
            ("stori_set_tempo", {"tempo": 120}),
            ("stori_add_midi_track", {"name": "Piano", "gmProgram": 0}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Add piano", {}, False, StateStore(conversation_id="t"))
        event = tracker.to_plan_event()
        assert event["type"] == "plan"
        assert "planId" in event
        assert isinstance(event["steps"], list)
        assert len(event["steps"]) == 2
        for step in event["steps"]:
            assert "stepId" in step
            assert "label" in step
            assert step["status"] == "pending"

    def test_title_includes_params(self):
        """Plan title should incorporate key/tempo from tool calls."""
        tcs = self._make_tool_calls([
            ("stori_set_tempo", {"tempo": 72}),
            ("stori_set_key", {"key": "Cm"}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Create a lofi beat", {}, False, StateStore(conversation_id="t"))
        assert "Cm" in tracker.title
        assert "72 BPM" in tracker.title

    def test_title_falls_back_to_project_context(self):
        """Plan title uses project context when tool calls don't set tempo/key."""
        tcs = self._make_tool_calls([
            ("stori_add_midi_track", {"name": "Lead"}),
        ])
        tracker = _PlanTracker()
        tracker.build(tcs, "Add lead", {"tempo": 100, "key": "Am"}, False, StateStore(conversation_id="t"))
        assert "Am" in tracker.title
        assert "100 BPM" in tracker.title

    def test_build_from_prompt_creates_upfront_plan(self):
        """build_from_prompt() generates one step per routing field and role."""
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
        tracker.build_from_prompt(parsed, "STORI PROMPT\nMode: compose\nSection: intro", {})
        labels = [s.label for s in tracker.steps]
        # tempo + key + 3 roles = 5 steps
        assert len(tracker.steps) == 5
        assert any("165" in l for l in labels)
        assert any("Bb" in l for l in labels)
        assert any("Drums" in l for l in labels)
        assert any("Bass" in l for l in labels)
        assert any("Organ" in l for l in labels)

    def test_build_from_prompt_no_roles_adds_placeholder(self):
        """Without roles, build_from_prompt adds a generic placeholder step."""
        from app.core.prompt_parser import ParsedPrompt
        parsed = ParsedPrompt(raw="STORI PROMPT\nMode: compose", mode="compose", request="make something", tempo=120)
        tracker = _PlanTracker()
        tracker.build_from_prompt(parsed, "make something", {})
        labels = [s.label for s in tracker.steps]
        assert any("tempo" in l.lower() for l in labels)
        assert any("generate" in l.lower() or "music" in l.lower() for l in labels)

    def test_step_activation_and_completion(self):
        """activate_step / complete_active_step produce correct events."""
        tracker = _PlanTracker()
        tracker.steps = [
            _PlanStep(step_id="1", label="Setup"),
            _PlanStep(step_id="2", label="Track"),
        ]
        evt = tracker.activate_step("1")
        assert evt == {"type": "planStepUpdate", "stepId": "1", "status": "active"}
        assert tracker._active_step_id == "1"
        assert tracker.steps[0].status == "active"

        tracker.steps[0].result = "Set 72 BPM"
        evt = tracker.complete_active_step()
        assert evt is not None
        assert evt["status"] == "completed"
        assert evt["result"] == "Set 72 BPM"
        assert tracker._active_step_id is None
        assert tracker.steps[0].status == "completed"

    def test_step_for_tool_index(self):
        """step_for_tool_index returns correct step."""
        tracker = _PlanTracker()
        tracker.steps = [
            _PlanStep(step_id="1", label="Setup", tool_indices=[0, 1]),
            _PlanStep(step_id="2", label="Track", tool_indices=[2, 3, 4]),
        ]
        assert tracker.step_for_tool_index(0).step_id == "1"  # type: ignore[union-attr]
        assert tracker.step_for_tool_index(3).step_id == "2"  # type: ignore[union-attr]
        assert tracker.step_for_tool_index(99) is None

    def test_progress_context_formatting(self):
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

    def test_build_step_result(self):
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
    async def test_plan_and_step_updates_emitted_for_multi_tool_editing(self):
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
    async def test_no_plan_for_single_force_stop_tool(self):
        """force_stop_after with a single tool should still emit a plan (minimal)."""
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
        assert "plan" in types
        assert "toolCall" in types
        assert "complete" in types

    @pytest.mark.anyio
    async def test_plan_not_emitted_in_variation_mode(self):
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
            total_changes=0,
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
    async def test_step_order_matches_tool_execution(self):
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
    async def test_orchestration_error_emits_complete_with_success_false(self):
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
