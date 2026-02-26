"""Tests for maestro handlers (orchestration, UsageTracker, fallback route)."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from app.core.planner import ExecutionPlan
    from app.core.prompt_parser import ParsedPrompt
    from app.core.state_store import StateStore

from app.contracts.json_types import NoteDict, ToolCallDict
from app.contracts.project_types import ProjectContext
from app.core.maestro_handlers import UsageTracker, orchestrate
from app.models.variation import Variation
from app.core.maestro_editing import (
    _create_editing_composition_route,
    _get_incomplete_tracks,
    _project_needs_structure,
)
from app.core.maestro_composing import _create_editing_fallback_route
from app.core.intent import IntentResult, Intent, Slots, SSEState
from app.core.intent_config import (
    _PRIMITIVES_FX,
    _PRIMITIVES_MIXING,
    _PRIMITIVES_REGION,
    _PRIMITIVES_TRACK,
)


async def _fake_plan_stream(plan: ExecutionPlan) -> AsyncGenerator[ExecutionPlan, None]:
    """Async generator yielding a single ExecutionPlan (simulates build_execution_plan_stream)."""
    yield plan

# Project context with existing tracks — keeps COMPOSING route active
# (empty projects override COMPOSING → EDITING).
_NON_EMPTY_PROJECT: ProjectContext = {
    "id": "test-project",
    "tracks": [
        {
            "id": "existing-track-1",
            "name": "Track 1",
            "regions": [{"id": "existing-region-1", "name": "Region 1"}],
        }
    ],
}


class TestUsageTracker:
    """Test UsageTracker."""

    def test_init_zero(self) -> None:

        t = UsageTracker()
        assert t.prompt_tokens == 0
        assert t.completion_tokens == 0
        assert t.last_input_tokens == 0

    def test_add_accumulates(self) -> None:

        t = UsageTracker()
        t.add(10, 20)
        assert t.prompt_tokens == 10
        assert t.completion_tokens == 20
        t.add(5, 15)
        assert t.prompt_tokens == 15
        assert t.completion_tokens == 35

    def test_last_input_tokens_tracks_most_recent_call(self) -> None:

        """last_input_tokens is overwritten each call, not accumulated."""
        t = UsageTracker()
        t.add(100, 50)
        assert t.last_input_tokens == 100
        # Second call (larger context) overwrites the first
        t.add(250, 80)
        assert t.last_input_tokens == 250
        assert t.prompt_tokens == 350  # accumulated, unchanged

    def test_last_input_tokens_reflects_growing_context(self) -> None:

        """Each iteration of an agentic loop sends more context; last call wins."""
        t = UsageTracker()
        for tokens in [1000, 1500, 2100]:
            t.add(tokens, 200)
        assert t.last_input_tokens == 2100


class TestGetContextWindowTokens:
    """Test get_context_window_tokens helper in config."""

    def test_known_models_return_200k(self) -> None:

        """Both supported Claude models return 200 000."""
        from app.config import get_context_window_tokens
        assert get_context_window_tokens("anthropic/claude-sonnet-4.6") == 200_000
        assert get_context_window_tokens("anthropic/claude-opus-4.6") == 200_000

    def test_unknown_model_returns_zero(self) -> None:

        """Unknown models return 0 so the frontend keeps its previous ring value."""
        from app.config import get_context_window_tokens
        assert get_context_window_tokens("openai/gpt-4o") == 0
        assert get_context_window_tokens("unknown/model") == 0
        assert get_context_window_tokens("") == 0


class TestCreateEditingFallbackRoute:
    """Test _create_editing_fallback_route."""

    def test_returns_editing_state_with_primitives(self) -> None:

        """Fallback route should be EDITING with track+region primitives."""
        route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.8,
            slots=Slots(),
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

    def test_preserves_slots(self) -> None:

        """Slots from original route are preserved."""
        route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.7,
            slots=Slots(extras={"tempo": 90}),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=True,
            reasons=(),
        )
        out = _create_editing_fallback_route(route)
        assert out.slots.extras.get("tempo") == 90


class TestProjectNeedsStructure:
    """Test _project_needs_structure helper."""

    def test_empty_context_needs_structure(self) -> None:

        """Empty project context (no tracks key) needs structure."""
        assert _project_needs_structure({}) is True

    def test_empty_tracks_needs_structure(self) -> None:

        """Project with empty tracks list needs structure."""
        assert _project_needs_structure({"tracks": []}) is True

    def test_project_with_tracks_does_not_need_structure(self) -> None:

        """Project with at least one track does not need structure."""
        ctx: ProjectContext = {"tracks": [{"id": "t1", "name": "Drums"}]}
        assert _project_needs_structure(ctx) is False

    def test_project_with_multiple_tracks(self) -> None:

        """Project with multiple tracks does not need structure."""
        ctx: ProjectContext = {"tracks": [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}]}
        assert _project_needs_structure(ctx) is False


class TestGetIncompleteTracks:
    """Test _get_incomplete_tracks helper."""

    def _make_store(self) -> StateStore:
        from app.core.state_store import StateStore
        return StateStore(project_id="test")

    def test_track_without_region_is_incomplete(self) -> None:

        """A track with no regions should be detected as incomplete."""
        store = self._make_store()
        store.create_track("Guitar")
        result = _get_incomplete_tracks(store)
        assert "Guitar" in result

    def test_track_with_region_but_no_notes_is_incomplete(self) -> None:

        """A track that has a region but no stori_add_notes call is incomplete."""
        store = self._make_store()
        tid = store.create_track("Piano")
        store.create_region("Intro", tid)
        # No stori_add_notes in tool_calls_collected
        result = _get_incomplete_tracks(store, tool_calls_collected=[])
        assert "Piano" in result

    def test_track_with_region_and_notes_is_complete(self) -> None:

        """A track whose region received stori_add_notes is complete."""
        store = self._make_store()
        tid = store.create_track("Bass")
        rid = store.create_region("Groove", tid)
        tc: list[ToolCallDict] = [ToolCallDict(tool="stori_add_notes", params={"regionId": rid, "notes": []})]
        result = _get_incomplete_tracks(store, tool_calls_collected=tc)
        assert "Bass" not in result

    def test_mixed_complete_and_incomplete(self) -> None:

        """Only incomplete tracks are returned."""
        store = self._make_store()
        tid1 = store.create_track("Guitar")
        rid1 = store.create_region("Riff", tid1)
        tid2 = store.create_track("Drums")
        # Drums has no region at all
        tc: list[ToolCallDict] = [ToolCallDict(tool="stori_add_notes", params={"regionId": rid1, "notes": []})]
        result = _get_incomplete_tracks(store, tool_calls_collected=tc)
        assert "Guitar" not in result
        assert "Drums" in result

    def test_no_tool_calls_treats_all_regions_as_noteless(self) -> None:

        """Without tool_calls_collected, tracks with regions are still incomplete (no notes)."""
        store = self._make_store()
        tid = store.create_track("Keys")
        store.create_region("Pad", tid)
        result = _get_incomplete_tracks(store)
        assert "Keys" in result

    def test_notes_from_prior_iteration_count_as_complete(self) -> None:

        """Regression: notes persisted to StateStore in a prior iteration must
        satisfy the completeness check even when tool_calls_collected is empty.
        Without this, the continuation prompt falsely tells the model a region
        still needs notes, causing it to call stori_clear_notes and destroy
        valid content before re-adding.
        """
        store = self._make_store()
        tid = store.create_track("Pads")
        rid = store.create_region("Intro Pads", tid)
        # Simulate notes persisted from iteration 1
        store.add_notes(rid, [{"pitch": 60, "startBeat": 0, "durationBeats": 4, "velocity": 80}])
        # Iteration 2: no new stori_add_notes calls in this batch
        result = _get_incomplete_tracks(store, tool_calls_collected=[])
        assert "Pads" not in result, (
            "Track with StateStore notes should not appear in incomplete list"
        )


class TestCreateEditingCompositionRoute:
    """Test _create_editing_composition_route helper."""

    def test_returns_editing_state(self) -> None:

        """Composition route override should produce EDITING state."""
        route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.85,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=True,
            requires_planner=True,
            reasons=("generation_phrase",),
        )
        out = _create_editing_composition_route(route)
        assert out.sse_state == SSEState.EDITING
        assert out.intent == Intent.GENERATE_MUSIC  # preserves original intent
        assert out.force_stop_after is False
        assert out.requires_planner is False
        assert out.tool_choice == "auto"
        assert "empty_project_override" in out.reasons

    def test_includes_all_structural_tools(self) -> None:

        """Composition route should include track, region, FX, and mixing primitives."""
        route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.85,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=True,
            requires_planner=True,
            reasons=(),
        )
        out = _create_editing_composition_route(route)
        # Must include structural primitives
        assert "stori_add_midi_track" in out.allowed_tool_names
        assert "stori_set_midi_program" in out.allowed_tool_names
        assert "stori_add_midi_region" in out.allowed_tool_names
        assert "stori_add_notes" in out.allowed_tool_names
        assert "stori_add_insert_effect" in out.allowed_tool_names
        assert "stori_set_tempo" in out.allowed_tool_names
        assert "stori_set_key" in out.allowed_tool_names
        # Should be a superset of track + region primitives
        assert set(_PRIMITIVES_TRACK).issubset(out.allowed_tool_names)
        assert set(_PRIMITIVES_REGION).issubset(out.allowed_tool_names)
        assert set(_PRIMITIVES_FX).issubset(out.allowed_tool_names)

    def test_preserves_slots_and_confidence(self) -> None:

        """Slots and confidence from original route are preserved."""
        route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.92,
            slots=Slots(extras={"style": "phish"}),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=True,
            requires_planner=True,
            reasons=(),
        )
        out = _create_editing_composition_route(route)
        assert out.confidence == 0.92
        assert out.slots.extras.get("style") == "phish"


class TestOrchestrateStream:
    """Test orchestrate() yields expected SSE events (mocked intent + LLM)."""

    @pytest.mark.anyio
    async def test_yields_state_then_complete_for_reasoning(self) -> None:

        """When intent is REASONING, we get state event and then complete with no tools."""
        fake_route = IntentResult(
            intent=Intent.UNKNOWN,
            sse_state=SSEState.REASONING,
            confidence=0.9,
            slots=Slots(),
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

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock) as m_intent:
            m_intent.return_value = fake_route
            with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
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
                # REASONING path executes no tools; toolCalls is omitted from the wire payload.
                assert last_payload.get("toolCalls") is None

    @pytest.mark.anyio
    async def test_complete_event_includes_context_window_fields(self) -> None:

        """complete event always contains inputTokens and contextWindowTokens."""
        fake_route = IntentResult(
            intent=Intent.ASK_GENERAL,
            sse_state=SSEState.REASONING,
            confidence=0.9,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=False,
            reasons=(),
        )
        fake_llm_response = MagicMock()
        fake_llm_response.content = "Four."
        fake_llm_response.usage = {"prompt_tokens": 42000, "completion_tokens": 10}
        fake_llm_response.has_tool_calls = False
        fake_llm_response.finish_reason = "stop"
        fake_llm_response.tool_calls = []

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                mock_llm = MagicMock()
                mock_llm.chat_completion = AsyncMock(return_value=fake_llm_response)
                mock_llm.supports_reasoning = MagicMock(return_value=False)
                mock_llm.close = AsyncMock()
                mock_llm.model = "anthropic/claude-sonnet-4.6"
                m_llm_cls.return_value = mock_llm

                tracker = UsageTracker()
                events = []
                async for event in orchestrate("What is 2+2?", usage_tracker=tracker):
                    events.append(event)

                import json
                complete = json.loads(events[-1].split("data: ", 1)[1].strip())
                assert complete["type"] == "complete"
                assert complete["inputTokens"] == 42000
                assert complete["contextWindowTokens"] == 200_000

    @pytest.mark.anyio
    async def test_complete_event_context_window_zero_for_unknown_model(self) -> None:

        """contextWindowTokens is 0 for unrecognised models."""
        fake_route = IntentResult(
            intent=Intent.ASK_GENERAL,
            sse_state=SSEState.REASONING,
            confidence=0.9,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=False,
            reasons=(),
        )
        fake_llm_response = MagicMock()
        fake_llm_response.content = "Four."
        fake_llm_response.usage = {"prompt_tokens": 5000, "completion_tokens": 10}
        fake_llm_response.has_tool_calls = False
        fake_llm_response.finish_reason = "stop"
        fake_llm_response.tool_calls = []

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                mock_llm = MagicMock()
                mock_llm.chat_completion = AsyncMock(return_value=fake_llm_response)
                mock_llm.supports_reasoning = MagicMock(return_value=False)
                mock_llm.close = AsyncMock()
                mock_llm.model = "unknown/model-x"
                m_llm_cls.return_value = mock_llm

                tracker = UsageTracker()
                events = []
                async for event in orchestrate("hi", usage_tracker=tracker):
                    events.append(event)

                import json
                complete = json.loads(events[-1].split("data: ", 1)[1].strip())
                assert complete["type"] == "complete"
                assert complete["contextWindowTokens"] == 0

    @pytest.mark.anyio
    async def test_yields_state_then_complete_for_composing_with_empty_plan(self) -> None:

        """When intent is COMPOSING on a non-empty project and pipeline returns empty plan, we get state then content then complete."""
        from app.core.planner import ExecutionPlan

        fake_route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.9,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=True,
            reasons=(),
        )
        empty_plan = ExecutionPlan(tool_calls=[], safety_validated=False)

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_composing.composing.build_execution_plan_stream", return_value=_fake_plan_stream(empty_plan)):
                with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                    mock_llm = MagicMock()
                    mock_llm.close = AsyncMock()
                    m_llm_cls.return_value = mock_llm

                    events = []
                    async for event in orchestrate("make something vague", project_context=_NON_EMPTY_PROJECT):
                        events.append(event)

                    import json
                    first = json.loads(events[0].split("data: ", 1)[1].strip())
                    assert first.get("type") == "state"
                    assert first.get("state") == "composing"
                    last = json.loads(events[-1].split("data: ", 1)[1].strip())
                    assert last.get("type") == "complete"
                    assert last.get("success") is True

    @pytest.mark.anyio
    async def test_orchestrate_yields_error_event_on_exception(self) -> None:

        """When orchestration raises, we yield error then complete(success=false)."""
        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock) as m_intent:
            m_intent.side_effect = RuntimeError("intent service down")
            with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                mock_llm = MagicMock()
                mock_llm.close = AsyncMock()
                m_llm_cls.return_value = mock_llm
                events = []
                async for event in orchestrate("hello"):
                    events.append(event)
                assert len(events) >= 2
                import json
                payloads = [
                    json.loads(e.split("data: ", 1)[1].strip())
                    for e in events if "data:" in e
                ]
                types = [p["type"] for p in payloads]
                assert "error" in types
                err_evt = next(p for p in payloads if p["type"] == "error")
                assert "intent service down" in err_evt.get("message", "")
                # complete must be the final event (spec requirement)
                assert payloads[-1]["type"] == "complete"
                assert payloads[-1]["success"] is False

    @pytest.mark.anyio
    async def test_reasoning_with_rag_ask_stori_docs(self) -> None:

        """When intent is ASK_STORI_DOCS and RAG exists, we stream RAG answer then complete."""
        from app.core.intent import Intent

        fake_route = IntentResult(
            intent=Intent.ASK_STORI_DOCS,
            sse_state=SSEState.REASONING,
            confidence=0.9,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=False,
            reasons=(),
        )
        mock_rag = MagicMock()
        mock_rag.collection_exists = MagicMock(return_value=True)
        async def fake_answer(*args: object, **kwargs: object) -> AsyncGenerator[str, None]:
            yield "RAG chunk 1"
            yield "RAG chunk 2"
        mock_rag.answer = fake_answer

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.services.rag.get_rag_service", return_value=mock_rag):
                with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
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
    async def test_reasoning_streaming_path_when_supports_reasoning(self) -> None:

        """When model supports reasoning, handler uses chat_completion_stream and yields reasoning + content."""
        fake_route = IntentResult(
            intent=Intent.UNKNOWN,
            sse_state=SSEState.REASONING,
            confidence=0.9,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=False,
            reasons=(),
        )
        async def stream_chunks(
            *args: object, **kwargs: object
        ) -> AsyncGenerator[dict[str, object], None]:
            yield {"type": "reasoning_delta", "text": "Thinking..."}
            yield {"type": "content_delta", "text": "Answer."}
            yield {"type": "done", "content": "Answer.", "usage": {"prompt_tokens": 1, "completion_tokens": 2}}

        def make_stream(
            *args: object, **kwargs: Any
        ) -> AsyncGenerator[dict[str, object], None]:
            return stream_chunks(*args, **kwargs)

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
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
    async def test_composing_with_non_empty_plan_apply_mode(self) -> None:

        """When COMPOSING on a non-empty project and pipeline returns a plan with tool_calls, we stream plan then complete."""
        from app.core.planner import ExecutionPlan
        from app.core.expansion import ToolCall

        fake_route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.9,
            slots=Slots(),
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

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_composing.composing.build_execution_plan_stream", return_value=_fake_plan_stream(plan)):
                with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                    mock_llm = MagicMock()
                    mock_llm.close = AsyncMock()
                    m_llm_cls.return_value = mock_llm

                    events = []
                    async for event in orchestrate("make a beat", project_context=_NON_EMPTY_PROJECT):
                        events.append(event)

                    import json
                    payloads = [json.loads(e.split("data: ", 1)[1].strip()) for e in events if "data:" in e]
                    types = [p.get("type") for p in payloads]
                    assert "state" in types
                    assert "plan" in types
                    assert "complete" in types
                    assert "planSummary" not in types

    @pytest.mark.anyio
    async def test_composing_empty_plan_with_stori_in_response_fallback_to_editing(self) -> None:

        """When plan has no tool_calls but llm_response_text contains 'stori_', we retry as EDITING."""
        from app.core.planner import ExecutionPlan
        from app.core.expansion import ToolCall
        from app.core.llm_client import LLMResponse

        fake_route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.9,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=True,
            reasons=(),
        )
        # Plan with no tool_calls but LLM-like function call text
        empty_plan = ExecutionPlan(
            tool_calls=[], safety_validated=False,
            llm_response_text="stori_add_midi_track(name='Drums')",
        )

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_composing.composing.build_execution_plan_stream", return_value=_fake_plan_stream(empty_plan)):
                with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                    mock_llm = MagicMock()
                    mock_llm.supports_reasoning = MagicMock(return_value=False)
                    mock_llm.chat_completion = AsyncMock(return_value=LLMResponse(
                        content="Done.",
                        tool_calls=[ToolCall("stori_add_midi_track", {"name": "Drums"}, "tc1")],
                    ))
                    mock_llm.close = AsyncMock()
                    m_llm_cls.return_value = mock_llm

                    events = []
                    async for event in orchestrate("add drums", project_context=_NON_EMPTY_PROJECT):
                        events.append(event)

                    import json
                    payloads = [json.loads(e.split("data: ", 1)[1].strip()) for e in events if "data:" in e]
                    types = [p.get("type") for p in payloads]
                    assert "state" in types
                    assert any(p.get("type") == "status" and "Retrying" in p.get("message", "") for p in payloads)

    @pytest.mark.anyio
    async def test_empty_project_overrides_composing_to_editing(self) -> None:

        """When COMPOSING intent hits an empty project, orchestrate overrides to EDITING with tool_call events."""
        from app.core.expansion import ToolCall
        from app.core.llm_client import LLMResponse

        fake_route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.85,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=True,
            requires_planner=True,
            reasons=("generation_phrase",),
        )

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                mock_llm = MagicMock()
                mock_llm.supports_reasoning = MagicMock(return_value=False)
                # LLM returns a tool call to add a track
                mock_llm.chat_completion = AsyncMock(return_value=LLMResponse(
                    content="Creating your song!",
                    tool_calls=[ToolCall("stori_add_midi_track", {"name": "Drums"}, "tc1")],
                ))
                mock_llm.close = AsyncMock()
                m_llm_cls.return_value = mock_llm

                events = []
                # Empty project context — no tracks
                async for event in orchestrate(
                    "Create a new song in the style of Phish",
                    project_context={"id": "empty-project", "tracks": []},
                ):
                    events.append(event)

                import json
                payloads = [json.loads(e.split("data: ", 1)[1].strip()) for e in events if "data:" in e]
                types = [p.get("type") for p in payloads]

                # State event should be "editing", not "composing"
                state_event = next(p for p in payloads if p.get("type") == "state")
                assert state_event.get("state") == "editing", (
                    f"Expected 'editing' for empty project, got '{state_event.get('state')}'"
                )
                assert state_event.get("intent") == "compose.generate_music"

                # Should have toolCall events (not meta/phrase/done)
                assert "toolCall" in types, "Expected toolCall events for empty project"
                assert "meta" not in types, "Should NOT have variation meta events"
                assert "phrase" not in types, "Should NOT have variation phrase events"

                # Tool call should be stori_add_midi_track
                tool_calls = [p for p in payloads if p.get("type") == "toolCall"]
                assert tool_calls[0].get("name") == "stori_add_midi_track"

                # Should end with complete
                assert "complete" in types

    @pytest.mark.anyio
    async def test_non_empty_project_stays_on_composing(self) -> None:

        """When COMPOSING intent hits a project with tracks, it stays on COMPOSING path (variation review)."""
        from app.core.planner import ExecutionPlan

        fake_route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.85,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=True,
            requires_planner=True,
            reasons=("generation_phrase",),
        )
        empty_plan = ExecutionPlan(tool_calls=[], safety_validated=False)

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_composing.composing.build_execution_plan_stream", return_value=_fake_plan_stream(empty_plan)):
                with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                    mock_llm = MagicMock()
                    mock_llm.close = AsyncMock()
                    m_llm_cls.return_value = mock_llm

                    events = []
                    async for event in orchestrate(
                        "make the bass line funkier",
                        project_context=_NON_EMPTY_PROJECT,
                    ):
                        events.append(event)

                    import json
                    payloads = [json.loads(e.split("data: ", 1)[1].strip()) for e in events if "data:" in e]

                    state_event = next(p for p in payloads if p.get("type") == "state")
                    assert state_event.get("state") == "composing", (
                        f"Expected 'composing' for non-empty project, got '{state_event.get('state')}'"
                    )

    @pytest.mark.anyio
    async def test_orchestrate_accepts_quality_preset_param(self) -> None:

        """quality_preset parameter is accepted by orchestrate without TypeError."""
        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock) as m_intent:
            m_intent.side_effect = RuntimeError("abort early")
            with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                mock_llm = MagicMock()
                mock_llm.close = AsyncMock()
                m_llm_cls.return_value = mock_llm
                events = []
                async for event in orchestrate("compose something", quality_preset="fast"):
                    events.append(event)
                # We expect an error+complete pair — just confirming no TypeError
                import json
                payloads = [
                    json.loads(e.split("data: ", 1)[1].strip())
                    for e in events if "data:" in e
                ]
                types = [p["type"] for p in payloads]
                assert "complete" in types


class TestComposingUnifiedSSE:
    """Tests for the unified SSE UX across all three phases."""

    @pytest.mark.anyio
    async def test_composing_emits_reasoning_events(self) -> None:

        """Phase 1: COMPOSING path emits reasoning events from the streaming planner."""
        from app.core.planner import ExecutionPlan
        from app.core.expansion import ToolCall
        from app.protocol.emitter import emit
        from app.protocol.events import ReasoningEvent

        fake_route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.9,
            slots=Slots(),
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

        async def _stream_with_reasoning(
            *args: object, **kwargs: object
        ) -> AsyncGenerator[str | ExecutionPlan, None]:
            yield emit(ReasoningEvent(content="Planning the beat..."))
            yield plan

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_composing.composing.build_execution_plan_stream", return_value=_stream_with_reasoning()):
                with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                    mock_llm = MagicMock()
                    mock_llm.close = AsyncMock()
                    m_llm_cls.return_value = mock_llm

                    events = []
                    async for event in orchestrate("make a beat", project_context=_NON_EMPTY_PROJECT):
                        events.append(event)

                    import json
                    payloads = [json.loads(e.split("data: ", 1)[1].strip()) for e in events if "data:" in e]
                    types = [p.get("type") for p in payloads]

                    assert "reasoning" in types, f"Expected 'reasoning' in {types}"
                    reasoning_ev = next(p for p in payloads if p["type"] == "reasoning")
                    assert "Planning" in reasoning_ev["content"]

    @pytest.mark.anyio
    async def test_composing_emits_plan_event(self) -> None:

        """COMPOSING path emits a 'plan' event with steps."""
        from app.core.planner import ExecutionPlan
        from app.core.expansion import ToolCall

        fake_route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.9,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=True,
            reasons=(),
        )
        plan = ExecutionPlan(
            tool_calls=[
                ToolCall("stori_add_midi_track", {"name": "Drums"}),
                ToolCall("stori_add_midi_region", {"name": "Drums", "trackName": "Drums", "startBeat": 0, "durationBeats": 16}),
                ToolCall("stori_generate_midi", {"role": "drums", "style": "house", "tempo": 128, "bars": 4}),
            ],
            safety_validated=True,
        )

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_composing.composing.build_execution_plan_stream", return_value=_fake_plan_stream(plan)):
                with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                    mock_llm = MagicMock()
                    mock_llm.close = AsyncMock()
                    m_llm_cls.return_value = mock_llm

                    events = []
                    async for event in orchestrate("make a house beat", project_context=_NON_EMPTY_PROJECT):
                        events.append(event)

                    import json
                    payloads = [json.loads(e.split("data: ", 1)[1].strip()) for e in events if "data:" in e]
                    types = [p.get("type") for p in payloads]

                    assert "plan" in types, f"Expected 'plan' in {types}"
                    plan_ev = next(p for p in payloads if p["type"] == "plan")
                    assert "steps" in plan_ev
                    assert len(plan_ev["steps"]) >= 1
                    assert "planSummary" not in types

    @pytest.mark.anyio
    async def test_composing_emits_proposal_tool_calls(self) -> None:

        """COMPOSING path emits proposal toolCalls (phase 1), then real execution toolCalls (phase 2)."""
        from app.core.planner import ExecutionPlan
        from app.core.expansion import ToolCall

        fake_route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.9,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=True,
            reasons=(),
        )
        plan = ExecutionPlan(
            tool_calls=[ToolCall("stori_set_tempo", {"tempo": 120}, id="tc-1")],
            safety_validated=True,
        )

        async def _mock_execute(**kwargs: Any) -> Variation:
            pre_cb = kwargs.get("pre_tool_callback")
            post_cb = kwargs.get("post_tool_callback")
            prog_cb = kwargs.get("progress_callback")
            if pre_cb:
                await pre_cb("stori_set_tempo", {"tempo": 120})
            if post_cb:
                await post_cb("stori_set_tempo", {"tempo": 120})
            if prog_cb:
                await prog_cb(1, 1, "stori_set_tempo", {"tempo": 120})
            from app.models.variation import Variation
            return Variation(
                variation_id="var-1",
                intent="test",
                affected_tracks=[],
                affected_regions=[],
                beat_range=(0.0, 0.0),
                phrases=[],
            )

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_composing.composing.build_execution_plan_stream", return_value=_fake_plan_stream(plan)):
                with patch("app.core.executor.execute_plan_variation", side_effect=_mock_execute):
                    with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                        mock_llm = MagicMock()
                        mock_llm.close = AsyncMock()
                        m_llm_cls.return_value = mock_llm

                        events = []
                        async for event in orchestrate("set tempo", project_context=_NON_EMPTY_PROJECT):
                            events.append(event)

                        import json
                        payloads = [json.loads(e.split("data: ", 1)[1].strip()) for e in events if "data:" in e]
                        types = [p.get("type") for p in payloads]

                        # Phase 1: proposal toolCall (id:"", proposal:true)
                        assert "toolCall" in types, f"Expected 'toolCall' in {types}"
                        proposal_calls = [p for p in payloads if p["type"] == "toolCall" and p.get("proposal") is True]
                        assert len(proposal_calls) >= 1, "Must have at least one proposal toolCall"
                        assert proposal_calls[0].get("id") == "", "Proposal toolCall must have empty id"

                        # Phase 2: execution toolStart + toolCall (proposal:false)
                        assert "toolStart" in types, f"Expected 'toolStart' in {types}"
                        execution_calls = [p for p in payloads if p["type"] == "toolCall" and p.get("proposal") is False]
                        assert len(execution_calls) >= 1, "Must have at least one execution toolCall"
                        assert execution_calls[0].get("id") != "", "Execution toolCall must have a real UUID"

    @pytest.mark.anyio
    async def test_composing_plan_step_updates(self) -> None:

        """COMPOSING execution phase emits planStepUpdate active/complete events (not during proposal phase)."""
        from app.core.planner import ExecutionPlan
        from app.core.expansion import ToolCall

        fake_route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.9,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=False,
            requires_planner=True,
            reasons=(),
        )
        plan = ExecutionPlan(
            tool_calls=[
                ToolCall("stori_set_tempo", {"tempo": 120}, id="tc-1"),
                ToolCall("stori_set_key", {"key": "Am"}, id="tc-2"),
            ],
            safety_validated=True,
        )

        call_idx = 0

        async def _mock_execute(**kwargs: Any) -> Variation:
            nonlocal call_idx
            pre_cb = kwargs.get("pre_tool_callback")
            post_cb = kwargs.get("post_tool_callback")
            prog_cb = kwargs.get("progress_callback")
            for tc in plan.tool_calls:
                call_idx += 1
                if pre_cb:
                    await pre_cb(tc.name, tc.params)
                if post_cb:
                    await post_cb(tc.name, tc.params)
                if prog_cb:
                    await prog_cb(call_idx, len(plan.tool_calls), tc.name, tc.params)
            from app.models.variation import Variation
            return Variation(
                variation_id="var-2",
                intent="test",
                affected_tracks=[],
                affected_regions=[],
                beat_range=(0.0, 0.0),
                phrases=[],
            )

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_composing.composing.build_execution_plan_stream", return_value=_fake_plan_stream(plan)):
                with patch("app.core.executor.execute_plan_variation", side_effect=_mock_execute):
                    with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                        mock_llm = MagicMock()
                        mock_llm.close = AsyncMock()
                        m_llm_cls.return_value = mock_llm

                        events = []
                        async for event in orchestrate("set up project", project_context=_NON_EMPTY_PROJECT):
                            events.append(event)

                        import json
                        payloads = [json.loads(e.split("data: ", 1)[1].strip()) for e in events if "data:" in e]
                        types = [p.get("type") for p in payloads]

                        assert "planStepUpdate" in types, f"Expected 'planStepUpdate' in {types}"
                        step_updates = [p for p in payloads if p["type"] == "planStepUpdate"]
                        statuses = [u.get("status") for u in step_updates]
                        assert "active" in statuses

                        # planStepUpdate must NOT appear before the first execution toolCall
                        # (i.e., it must not fire during the proposal phase)
                        proposal_indices = [i for i, p in enumerate(payloads) if p["type"] == "toolCall" and p.get("proposal") is True]
                        step_indices = [i for i, p in enumerate(payloads) if p["type"] == "planStepUpdate"]
                        if proposal_indices and step_indices:
                            assert step_indices[0] > proposal_indices[-1], \
                                "planStepUpdate must not fire during proposal phase"
                        assert "completed" in statuses


class TestAgentTeamsVariationRouting:
    """Verify that Mode: compose routes through Agent Teams + Variation."""

    def _make_parsed_prompt(self, roles: list[str]) -> ParsedPrompt:
        from app.core.prompt_parser import ParsedPrompt
        return ParsedPrompt(
            raw="STORI PROMPT\nMode: compose",
            mode="compose",
            request="make a beat",
            style="house",
            tempo=120,
            key="Am",
            roles=roles,
        )

    def _make_composing_route(self, parsed: ParsedPrompt) -> IntentResult:

        return IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.9,
            slots=Slots(extras={"parsed_prompt": parsed}),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="auto",
            force_stop_after=False,
            requires_planner=True,
            reasons=("stori_prompt",),
        )

    @pytest.mark.anyio
    async def test_explicit_compose_multi_role_routes_to_agent_teams_variation(self) -> None:

        """Mode: compose with 3 roles routes to _handle_composing_with_agent_teams."""
        parsed = self._make_parsed_prompt(["drums", "bass", "keys"])
        fake_route = self._make_composing_route(parsed)

        async def _fake_at_gen(
            *args: object, **kwargs: object
        ) -> AsyncGenerator[str, None]:
            yield 'data: {"type": "status", "message": "test"}\n\n'
            yield 'data: {"type": "complete", "success": true}\n\n'

        mock_llm = MagicMock()
        mock_llm.close = AsyncMock()

        with (
            patch("app.core.maestro_handlers.get_intent_result_with_llm",
                  new_callable=AsyncMock, return_value=fake_route),
            patch("app.core.maestro_handlers.LLMClient", return_value=mock_llm),
            patch("app.core.maestro_handlers._handle_composing_with_agent_teams",
                  side_effect=_fake_at_gen) as mock_at,
        ):
            events = []
            async for event in orchestrate(
                "STORI PROMPT\nMode: compose",
                project_context=_NON_EMPTY_PROJECT,
            ):
                events.append(event)

            mock_at.assert_called_once()
            call_args = mock_at.call_args
            assert call_args[0][2] is parsed

    @pytest.mark.anyio
    async def test_single_instrument_compose_routes_to_agent_teams_variation(self) -> None:

        """Mode: compose with 1 role also routes to Agent Teams + Variation."""
        parsed = self._make_parsed_prompt(["melody"])
        fake_route = self._make_composing_route(parsed)

        async def _fake_at_gen(
            *args: object, **kwargs: object
        ) -> AsyncGenerator[str, None]:
            yield 'data: {"type": "complete", "success": true}\n\n'

        mock_llm = MagicMock()
        mock_llm.close = AsyncMock()

        with (
            patch("app.core.maestro_handlers.get_intent_result_with_llm",
                  new_callable=AsyncMock, return_value=fake_route),
            patch("app.core.maestro_handlers.LLMClient", return_value=mock_llm),
            patch("app.core.maestro_handlers._handle_composing_with_agent_teams",
                  side_effect=_fake_at_gen) as mock_at,
        ):
            events = []
            async for event in orchestrate(
                "STORI PROMPT\nMode: compose",
                project_context=_NON_EMPTY_PROJECT,
            ):
                events.append(event)

            mock_at.assert_called_once()

    @pytest.mark.anyio
    async def test_compose_without_parsed_prompt_uses_standard_composing(self) -> None:

        """When no parsed prompt (freeform compose), standard _handle_composing is used."""
        from app.core.planner import ExecutionPlan

        fake_route = IntentResult(
            intent=Intent.GENERATE_MUSIC,
            sse_state=SSEState.COMPOSING,
            confidence=0.85,
            slots=Slots(),
            tools=[],
            allowed_tool_names=set(),
            tool_choice="none",
            force_stop_after=True,
            requires_planner=True,
            reasons=("generation_phrase",),
        )
        empty_plan = ExecutionPlan(tool_calls=[], safety_validated=False)

        mock_llm = MagicMock()
        mock_llm.close = AsyncMock()

        with (
            patch("app.core.maestro_handlers.get_intent_result_with_llm",
                  new_callable=AsyncMock, return_value=fake_route),
            patch("app.core.maestro_composing.composing.build_execution_plan_stream",
                  return_value=_fake_plan_stream(empty_plan)),
            patch("app.core.maestro_handlers.LLMClient", return_value=mock_llm),
            patch("app.core.maestro_handlers._handle_composing_with_agent_teams") as mock_at,
        ):
            events = []
            async for event in orchestrate(
                "make the bass funkier",
                project_context=_NON_EMPTY_PROJECT,
            ):
                events.append(event)

            mock_at.assert_not_called()

    @pytest.mark.anyio
    async def test_agent_teams_variation_emits_variation_events(self) -> None:

        """The wrapper intercepts Agent Teams complete and emits meta/phrase/done/complete."""
        import json
        from app.core.maestro_composing.composing import (
            _handle_composing_with_agent_teams,
        )
        from app.core.prompt_parser import ParsedPrompt
        from app.core.state_store import StateStore
        from app.core.tracing import create_trace_context
        from app.models.variation import Variation, Phrase, NoteChange, MidiNoteSnapshot

        parsed = self._make_parsed_prompt(["drums", "bass"])
        fake_route = self._make_composing_route(parsed)

        store = StateStore(conversation_id="test", project_id="test-proj")
        trace = create_trace_context()

        tid = store.create_track("Drums")
        rid = store.create_region(
            name="Drums Region",
            parent_track_id=tid,
            metadata={"startBeat": 0, "durationBeats": 16},
        )
        _notes: list[NoteDict] = [
            {"pitch": 36, "start_beat": 0.0, "duration_beats": 1.0, "velocity": 100},
            {"pitch": 38, "start_beat": 4.0, "duration_beats": 1.0, "velocity": 90},
        ]
        store.add_notes(rid, _notes)

        _test_variation = Variation(
            variation_id="test-var-id",
            intent="test",
            ai_explanation="Test variation",
            affected_tracks=[tid],
            affected_regions=[rid],
            beat_range=(0.0, 16.0),
            phrases=[
                Phrase(
                    phrase_id="phrase-1",
                    track_id=tid,
                    region_id=rid,
                    start_beat=0.0,
                    end_beat=16.0,
                    label="Drums",
                    tags=["drums"],
                    explanation="drum pattern",
                    note_changes=[
                        NoteChange(
                            note_id="n1",
                            change_type="added",
                            after=MidiNoteSnapshot(
                                pitch=36,
                                start_beat=0.0,
                                duration_beats=1.0,
                                velocity=100,
                            ),
                        ),
                    ],
                    cc_events=[],
                    pitch_bends=[],
                    aftertouch=[],
                ),
            ],
        )

        async def _fake_agent_teams(
            *args: object, **kwargs: object
        ) -> AsyncGenerator[str, None]:
            yield 'data: {"type": "status", "message": "Preparing..."}\n\n'
            yield 'data: {"type": "reasoning", "content": "Thinking about drums", "agentId": "drums"}\n\n'
            yield 'data: {"type": "summary", "tracks": ["Drums"], "regions": 1, "notes": 2}\n\n'
            yield 'data: {"type": "complete", "success": true, "stateVersion": 1}\n\n'

        mock_vs = MagicMock()
        mock_vs.compute_multi_region_variation = MagicMock(return_value=_test_variation)
        mock_vs.compute_variation = MagicMock(return_value=_test_variation)

        with (
            patch("app.core.maestro_agent_teams._handle_composition_agent_team",
                  side_effect=_fake_agent_teams),
            patch("app.core.maestro_editing._create_editing_composition_route",
                  return_value=fake_route),
            patch("app.services.variation.get_variation_service",
                  return_value=mock_vs),
            patch("app.core.maestro_composing.storage._store_variation"),
        ):
            events = []
            async for event in _handle_composing_with_agent_teams(
                prompt="test prompt",
                project_context=_NON_EMPTY_PROJECT,
                parsed=parsed,
                route=fake_route,
                llm=MagicMock(),
                store=store,
                trace=trace,
                usage_tracker=None,
            ):
                events.append(event)

        payloads = []
        for e in events:
            if e.startswith("data:"):
                payloads.append(json.loads(e.split("data: ", 1)[1].strip()))
        types = [p.get("type") for p in payloads]

        # Agent Teams events pass through (except complete)
        assert "status" in types
        assert "reasoning" in types
        assert "summary" in types

        # Variation events are appended
        assert "meta" in types
        assert "phrase" in types
        assert "done" in types
        assert "complete" in types

        # The complete event has variation info
        complete_evt = next(p for p in payloads if p["type"] == "complete")
        assert complete_evt["variationId"] == "test-var-id"
        assert complete_evt["phraseCount"] == 1
        assert complete_evt["success"] is True

        # The meta event has variation metadata
        meta_evt = next(p for p in payloads if p["type"] == "meta")
        assert meta_evt["variationId"] == "test-var-id"
        assert "noteCounts" in meta_evt

        # Ordering: reasoning before meta, meta before phrase, phrase before done
        reasoning_idx = types.index("reasoning")
        meta_idx = types.index("meta")
        phrase_idx = types.index("phrase")
        done_idx = types.index("done")
        complete_idx = types.index("complete")
        assert reasoning_idx < meta_idx < phrase_idx < done_idx < complete_idx
