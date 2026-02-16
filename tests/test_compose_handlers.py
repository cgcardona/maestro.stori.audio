"""Tests for compose handlers (orchestration, UsageTracker, fallback route)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.compose_handlers import (
    UsageTracker,
    _create_editing_fallback_route,
    orchestrate,
)
from app.core.intent import IntentResult, Intent, SSEState
from app.core.intent_config import _PRIMITIVES_REGION, _PRIMITIVES_TRACK


class TestUsageTracker:
    """Test UsageTracker."""

    def test_init_zero(self):
        t = UsageTracker()
        assert t.prompt_tokens == 0
        assert t.completion_tokens == 0

    def test_add_accumulates(self):
        t = UsageTracker()
        t.add(10, 20)
        assert t.prompt_tokens == 10
        assert t.completion_tokens == 20
        t.add(5, 15)
        assert t.prompt_tokens == 15
        assert t.completion_tokens == 35


class TestCreateEditingFallbackRoute:
    """Test _create_editing_fallback_route."""

    def test_returns_editing_state_with_primitives(self):
        """Fallback route should be EDITING with track+region primitives."""
        route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.8,
            slots={},
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=True,
            reasons=("test",),
        )
        out = _create_editing_fallback_route(route)
        assert out.sse_state == SSEState.EDITING
        assert out.intent == Intent.NOTES_ADD
        assert out.allowed_tool_names == set(_PRIMITIVES_REGION) | set(_PRIMITIVES_TRACK)
        assert out.tool_choice == "auto"
        assert out.force_stop_after is False
        assert out.requires_planner is False
        assert "Fallback" in out.reasons[0]

    def test_preserves_slots(self):
        """Slots from original route are preserved."""
        route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.7,
            slots={"tempo": 90},
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=True,
            reasons=(),
        )
        out = _create_editing_fallback_route(route)
        assert out.slots == {"tempo": 90}


class TestOrchestrateStream:
    """Test orchestrate() yields expected SSE events (mocked intent + LLM)."""

    @pytest.mark.anyio
    async def test_yields_state_then_complete_for_reasoning(self):
        """When intent is REASONING, we get state event and then complete with no tools."""
        fake_route = IntentResult(
            intent=Intent.UNKNOWN,
            sse_state=SSEState.REASONING,
            confidence=0.9,
            slots={},
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=False,
            reasons=(),
        )
        fake_llm_response = MagicMock()
        fake_llm_response.content = "Hello, this is the answer."
        fake_llm_response.usage = {"prompt_tokens": 1, "completion_tokens": 5}
        fake_llm_response.has_tool_calls = False
        fake_llm_response.finish_reason = "stop"
        fake_llm_response.tool_calls = []

        with patch("app.core.compose_handlers.get_intent_result_with_llm", new_callable=AsyncMock) as m_intent:
            m_intent.return_value = fake_route
            with patch("app.core.compose_handlers.LLMClient") as m_llm_cls:
                mock_llm = MagicMock()
                mock_llm.chat_completion = AsyncMock(return_value=fake_llm_response)
                mock_llm.supports_reasoning = MagicMock(return_value=False)
                mock_llm.close = AsyncMock()
                mock_llm.model = "test-model"
                m_llm_cls.return_value = mock_llm

                events = []
                async for event in orchestrate("What is 2+2?"):
                    events.append(event)

                # Should have at least: state, status, content, complete
                assert len(events) >= 3
                # First event should be state
                import json
                first = events[0]
                assert "data:" in first
                payload = json.loads(first.split("data: ", 1)[1].strip())
                assert payload.get("type") == "state"
                assert payload.get("state") == "reasoning"
                # Last should be complete
                last = events[-1]
                last_payload = json.loads(last.split("data: ", 1)[1].strip())
                assert last_payload.get("type") == "complete"
                assert last_payload.get("success") is True
                assert last_payload.get("tool_calls") == []

    @pytest.mark.anyio
    async def test_yields_state_then_complete_for_composing_with_empty_plan(self):
        """When intent is COMPOSING and pipeline returns empty plan, we get state then content then complete."""
        from app.core.pipeline import PipelineOutput
        from app.core.planner import ExecutionPlan

        fake_route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.9,
            slots={},
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=True,
            reasons=(),
        )
        fake_output = PipelineOutput(route=fake_route, plan=ExecutionPlan(tool_calls=[], safety_validated=False))

        with patch("app.core.compose_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.compose_handlers.run_pipeline", new_callable=AsyncMock, return_value=fake_output):
                with patch("app.core.compose_handlers.LLMClient") as m_llm_cls:
                    mock_llm = MagicMock()
                    mock_llm.close = AsyncMock()
                    m_llm_cls.return_value = mock_llm

                    events = []
                    async for event in orchestrate("make something vague"):
                        events.append(event)

                    import json
                    first = json.loads(events[0].split("data: ", 1)[1].strip())
                    assert first.get("type") == "state"
                    assert first.get("state") == "composing"
                    last = json.loads(events[-1].split("data: ", 1)[1].strip())
                    assert last.get("type") == "complete"
                    assert last.get("success") is True

    @pytest.mark.anyio
    async def test_orchestrate_yields_error_event_on_exception(self):
        """When orchestration raises, we yield an error SSE event then close."""
        with patch("app.core.compose_handlers.get_intent_result_with_llm", new_callable=AsyncMock) as m_intent:
            m_intent.side_effect = RuntimeError("intent service down")
            with patch("app.core.compose_handlers.LLMClient") as m_llm_cls:
                mock_llm = MagicMock()
                mock_llm.close = AsyncMock()
                m_llm_cls.return_value = mock_llm
                events = []
                async for event in orchestrate("hello"):
                    events.append(event)
                # Should have state (from intent attempt) then error
                assert len(events) >= 1
                import json
                last = json.loads(events[-1].split("data: ", 1)[1].strip())
                assert last.get("type") == "error"
                assert "intent service down" in last.get("message", "")

    @pytest.mark.anyio
    async def test_reasoning_with_rag_ask_stori_docs(self):
        """When intent is ASK_STORI_DOCS and RAG exists, we stream RAG answer then complete."""
        from app.core.intent import Intent

        fake_route = IntentResult(
            intent=Intent.ASK_STORI_DOCS,
            sse_state=SSEState.REASONING,
            confidence=0.9,
            slots={},
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=False,
            reasons=(),
        )
        mock_rag = MagicMock()
        mock_rag.collection_exists = MagicMock(return_value=True)
        async def fake_answer(*args, **kwargs):
            yield "RAG chunk 1"
            yield "RAG chunk 2"
        mock_rag.answer = fake_answer

        with patch("app.core.compose_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.services.rag.get_rag_service", return_value=mock_rag):
                with patch("app.core.compose_handlers.LLMClient") as m_llm_cls:
                    mock_llm = MagicMock()
                    mock_llm.close = AsyncMock()
                    m_llm_cls.return_value = mock_llm

                    events = []
                    async for event in orchestrate("How do I add a track in Stori?"):
                        events.append(event)

                    import json
                    payloads = [json.loads(e.split("data: ", 1)[1].strip()) for e in events if "data:" in e]
                    types = [p.get("type") for p in payloads]
                    assert "state" in types
                    assert "content" in types
                    assert "complete" in types
                    content_events = [p for p in payloads if p.get("type") == "content"]
                    assert any("RAG chunk" in p.get("content", "") for p in content_events)

    @pytest.mark.anyio
    async def test_reasoning_streaming_path_when_supports_reasoning(self):
        """When model supports reasoning, handler uses chat_completion_stream and yields reasoning + content."""
        fake_route = IntentResult(
            intent=Intent.UNKNOWN,
            sse_state=SSEState.REASONING,
            confidence=0.9,
            slots={},
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=False,
            reasons=(),
        )
        async def stream_chunks(*args, **kwargs):
            yield {"type": "reasoning_delta", "text": "Thinking..."}
            yield {"type": "content_delta", "text": "Answer."}
            yield {"type": "done", "content": "Answer.", "usage": {"prompt_tokens": 1, "completion_tokens": 2}}

        def make_stream(*args, **kwargs):
            return stream_chunks(*args, **kwargs)

        with patch("app.core.compose_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.compose_handlers.LLMClient") as m_llm_cls:
                mock_llm = MagicMock()
                mock_llm.supports_reasoning = MagicMock(return_value=True)
                mock_llm.chat_completion_stream = MagicMock(side_effect=make_stream)
                mock_llm.close = AsyncMock()
                mock_llm.model = "anthropic/claude-3.7-sonnet"
                m_llm_cls.return_value = mock_llm

                events = []
                async for event in orchestrate("What is 2+2?"):
                    events.append(event)

                import json
                payloads = [json.loads(e.split("data: ", 1)[1].strip()) for e in events if "data:" in e]
                types = [p.get("type") for p in payloads]
                assert "state" in types
                assert "complete" in types
                assert any(p.get("type") == "content" and "Answer" in p.get("content", "") for p in payloads)

    @pytest.mark.anyio
    async def test_composing_with_non_empty_plan_apply_mode(self):
        """When COMPOSING and pipeline returns a plan with tool_calls, we stream plan_summary then progress and complete."""
        from app.core.pipeline import PipelineOutput
        from app.core.planner import ExecutionPlan
        from app.core.expansion import ToolCall

        fake_route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.9,
            slots={},
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=True,
            reasons=(),
        )
        plan = ExecutionPlan(
            tool_calls=[ToolCall("stori_set_tempo", {"tempo": 120})],
            safety_validated=True,
        )
        fake_output = PipelineOutput(route=fake_route, plan=plan)

        with patch("app.core.compose_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.compose_handlers.run_pipeline", new_callable=AsyncMock, return_value=fake_output):
                with patch("app.core.compose_handlers.LLMClient") as m_llm_cls:
                    mock_llm = MagicMock()
                    mock_llm.close = AsyncMock()
                    m_llm_cls.return_value = mock_llm

                    events = []
                    async for event in orchestrate("make a beat"):
                        events.append(event)

                    import json
                    payloads = [json.loads(e.split("data: ", 1)[1].strip()) for e in events if "data:" in e]
                    types = [p.get("type") for p in payloads]
                    assert "state" in types
                    assert "plan_summary" in types
                    assert "complete" in types
                    plan_summary = next(p for p in payloads if p.get("type") == "plan_summary")
                    assert plan_summary.get("total_steps") == 1

    @pytest.mark.anyio
    async def test_composing_empty_plan_with_stori_in_response_fallback_to_editing(self):
        """When plan has no tool_calls but llm_response_text contains 'stori_', we retry as EDITING."""
        from app.core.pipeline import PipelineOutput
        from app.core.planner import ExecutionPlan
        from app.core.llm_client import LLMResponse, ToolCallData

        fake_route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.9,
            slots={},
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=True,
            reasons=(),
        )
        # Plan with no tool_calls but LLM-like function call text
        plan = ExecutionPlan(tool_calls=[], safety_validated=False, llm_response_text="stori_add_midi_track(name='Drums')")
        fake_output = PipelineOutput(route=fake_route, plan=plan, llm_response=LLMResponse(content="stori_add_midi_track(name='Drums')"))

        with patch("app.core.compose_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.compose_handlers.run_pipeline", new_callable=AsyncMock, return_value=fake_output):
                with patch("app.core.compose_handlers.LLMClient") as m_llm_cls:
                    mock_llm = MagicMock()
                    mock_llm.supports_reasoning = MagicMock(return_value=False)
                    # EDITING path will ask for tool calls; return one then stop
                    mock_llm.chat_completion = AsyncMock(return_value=LLMResponse(
                        content="Done.",
                        tool_calls=[ToolCallData("stori_add_midi_track", {"name": "Drums"}, "tc1")],
                    ))
                    mock_llm.close = AsyncMock()
                    m_llm_cls.return_value = mock_llm

                    events = []
                    async for event in orchestrate("add drums"):
                        events.append(event)

                    import json
                    payloads = [json.loads(e.split("data: ", 1)[1].strip()) for e in events if "data:" in e]
                    types = [p.get("type") for p in payloads]
                    assert "state" in types
                    # Should see "Retrying with different approach" status
                    assert any(p.get("type") == "status" and "Retrying" in p.get("message", "") for p in payloads)
