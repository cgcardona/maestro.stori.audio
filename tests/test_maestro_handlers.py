"""Tests for maestro handlers (orchestration, UsageTracker, fallback route)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.maestro_handlers import (
    UsageTracker,
    _create_editing_composition_route,
    _create_editing_fallback_route,
    _get_incomplete_tracks,
    _project_needs_structure,
    orchestrate,
)
from app.core.intent import IntentResult, Intent, Slots, SSEState
from app.core.intent_config import (
    _PRIMITIVES_FX,
    _PRIMITIVES_MIXING,
    _PRIMITIVES_REGION,
    _PRIMITIVES_TRACK,
)

# Project context with existing tracks — keeps COMPOSING route active
# (empty projects override COMPOSING → EDITING).
_NON_EMPTY_PROJECT = {
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

    def test_preserves_slots(self):
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

    def test_empty_context_needs_structure(self):
        """Empty project context (no tracks key) needs structure."""
        assert _project_needs_structure({}) is True

    def test_empty_tracks_needs_structure(self):
        """Project with empty tracks list needs structure."""
        assert _project_needs_structure({"tracks": []}) is True

    def test_project_with_tracks_does_not_need_structure(self):
        """Project with at least one track does not need structure."""
        ctx = {"tracks": [{"id": "t1", "name": "Drums"}]}
        assert _project_needs_structure(ctx) is False

    def test_project_with_multiple_tracks(self):
        """Project with multiple tracks does not need structure."""
        ctx = {"tracks": [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}]}
        assert _project_needs_structure(ctx) is False


class TestGetIncompleteTracks:
    """Test _get_incomplete_tracks helper."""

    def _make_store(self):
        from app.core.state_store import StateStore
        return StateStore(project_id="test")

    def test_track_without_region_is_incomplete(self):
        """A track with no regions should be detected as incomplete."""
        store = self._make_store()
        store.create_track("Guitar")
        result = _get_incomplete_tracks(store)
        assert "Guitar" in result

    def test_track_with_region_but_no_notes_is_incomplete(self):
        """A track that has a region but no stori_add_notes call is incomplete."""
        store = self._make_store()
        tid = store.create_track("Piano")
        store.create_region("Intro", tid)
        # No stori_add_notes in tool_calls_collected
        result = _get_incomplete_tracks(store, tool_calls_collected=[])
        assert "Piano" in result

    def test_track_with_region_and_notes_is_complete(self):
        """A track whose region received stori_add_notes is complete."""
        store = self._make_store()
        tid = store.create_track("Bass")
        rid = store.create_region("Groove", tid)
        tc = [{"tool": "stori_add_notes", "params": {"regionId": rid, "notes": []}}]
        result = _get_incomplete_tracks(store, tool_calls_collected=tc)
        assert "Bass" not in result

    def test_mixed_complete_and_incomplete(self):
        """Only incomplete tracks are returned."""
        store = self._make_store()
        tid1 = store.create_track("Guitar")
        rid1 = store.create_region("Riff", tid1)
        tid2 = store.create_track("Drums")
        # Drums has no region at all
        tc = [{"tool": "stori_add_notes", "params": {"regionId": rid1, "notes": []}}]
        result = _get_incomplete_tracks(store, tool_calls_collected=tc)
        assert "Guitar" not in result
        assert "Drums" in result

    def test_no_tool_calls_treats_all_regions_as_noteless(self):
        """Without tool_calls_collected, tracks with regions are still incomplete (no notes)."""
        store = self._make_store()
        tid = store.create_track("Keys")
        store.create_region("Pad", tid)
        result = _get_incomplete_tracks(store)
        assert "Keys" in result

    def test_notes_from_prior_iteration_count_as_complete(self):
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

    def test_returns_editing_state(self):
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

    def test_includes_all_structural_tools(self):
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
        assert "stori_set_key_signature" in out.allowed_tool_names
        # Should be a superset of track + region primitives
        assert set(_PRIMITIVES_TRACK).issubset(out.allowed_tool_names)
        assert set(_PRIMITIVES_REGION).issubset(out.allowed_tool_names)
        assert set(_PRIMITIVES_FX).issubset(out.allowed_tool_names)

    def test_preserves_slots_and_confidence(self):
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
    async def test_yields_state_then_complete_for_reasoning(self):
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
                assert last_payload.get("toolCalls") == []

    @pytest.mark.anyio
    async def test_yields_state_then_complete_for_composing_with_empty_plan(self):
        """When intent is COMPOSING on a non-empty project and pipeline returns empty plan, we get state then content then complete."""
        from app.core.pipeline import PipelineOutput
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
        fake_output = PipelineOutput(route=fake_route, plan=ExecutionPlan(tool_calls=[], safety_validated=False))

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_handlers.run_pipeline", new_callable=AsyncMock, return_value=fake_output):
                with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                    mock_llm = MagicMock()
                    mock_llm.close = AsyncMock()
                    m_llm_cls.return_value = mock_llm

                    events = []
                    # Pass non-empty project so COMPOSING route is preserved
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
    async def test_orchestrate_yields_error_event_on_exception(self):
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
    async def test_reasoning_with_rag_ask_stori_docs(self):
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
        async def fake_answer(*args, **kwargs):
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
    async def test_reasoning_streaming_path_when_supports_reasoning(self):
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
        async def stream_chunks(*args, **kwargs):
            yield {"type": "reasoning_delta", "text": "Thinking..."}
            yield {"type": "content_delta", "text": "Answer."}
            yield {"type": "done", "content": "Answer.", "usage": {"prompt_tokens": 1, "completion_tokens": 2}}

        def make_stream(*args, **kwargs):
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
    async def test_composing_with_non_empty_plan_apply_mode(self):
        """When COMPOSING on a non-empty project and pipeline returns a plan with tool_calls, we stream plan_summary then progress and complete."""
        from app.core.pipeline import PipelineOutput
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
        fake_output = PipelineOutput(route=fake_route, plan=plan)

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_handlers.run_pipeline", new_callable=AsyncMock, return_value=fake_output):
                with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                    mock_llm = MagicMock()
                    mock_llm.close = AsyncMock()
                    m_llm_cls.return_value = mock_llm

                    events = []
                    # Pass non-empty project so COMPOSING route is preserved
                    async for event in orchestrate("make a beat", project_context=_NON_EMPTY_PROJECT):
                        events.append(event)

                    import json
                    payloads = [json.loads(e.split("data: ", 1)[1].strip()) for e in events if "data:" in e]
                    types = [p.get("type") for p in payloads]
                    assert "state" in types
                    assert "planSummary" in types
                    assert "complete" in types
                    plan_summary = next(p for p in payloads if p.get("type") == "planSummary")
                    assert plan_summary.get("totalSteps") == 1

    @pytest.mark.anyio
    async def test_composing_empty_plan_with_stori_in_response_fallback_to_editing(self):
        """When plan has no tool_calls but llm_response_text contains 'stori_', we retry as EDITING."""
        from app.core.pipeline import PipelineOutput
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
        plan = ExecutionPlan(tool_calls=[], safety_validated=False, llm_response_text="stori_add_midi_track(name='Drums')")
        fake_output = PipelineOutput(route=fake_route, plan=plan, llm_response=LLMResponse(content="stori_add_midi_track(name='Drums')"))

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_handlers.run_pipeline", new_callable=AsyncMock, return_value=fake_output):
                with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                    mock_llm = MagicMock()
                    mock_llm.supports_reasoning = MagicMock(return_value=False)
                    # EDITING path will ask for tool calls; return one then stop
                    mock_llm.chat_completion = AsyncMock(return_value=LLMResponse(
                        content="Done.",
                        tool_calls=[ToolCall("stori_add_midi_track", {"name": "Drums"}, "tc1")],
                    ))
                    mock_llm.close = AsyncMock()
                    m_llm_cls.return_value = mock_llm

                    events = []
                    # Pass non-empty project so COMPOSING route is preserved
                    # (otherwise empty project override would skip the planner entirely)
                    async for event in orchestrate("add drums", project_context=_NON_EMPTY_PROJECT):
                        events.append(event)

                    import json
                    payloads = [json.loads(e.split("data: ", 1)[1].strip()) for e in events if "data:" in e]
                    types = [p.get("type") for p in payloads]
                    assert "state" in types
                    # Should see "Retrying with different approach" status
                    assert any(p.get("type") == "status" and "Retrying" in p.get("message", "") for p in payloads)

    @pytest.mark.anyio
    async def test_empty_project_overrides_composing_to_editing(self):
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
    async def test_non_empty_project_stays_on_composing(self):
        """When COMPOSING intent hits a project with tracks, it stays on COMPOSING path (variation review)."""
        from app.core.pipeline import PipelineOutput
        from app.core.planner import ExecutionPlan
        from app.core.expansion import ToolCall as PlanToolCall

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
        # Empty plan to keep test simple
        plan = ExecutionPlan(tool_calls=[], safety_validated=False)
        fake_output = PipelineOutput(route=fake_route, plan=plan)

        with patch("app.core.maestro_handlers.get_intent_result_with_llm", new_callable=AsyncMock, return_value=fake_route):
            with patch("app.core.maestro_handlers.run_pipeline", new_callable=AsyncMock, return_value=fake_output):
                with patch("app.core.maestro_handlers.LLMClient") as m_llm_cls:
                    mock_llm = MagicMock()
                    mock_llm.close = AsyncMock()
                    m_llm_cls.return_value = mock_llm

                    events = []
                    # Non-empty project — has tracks, so COMPOSING stays
                    async for event in orchestrate(
                        "make the bass line funkier",
                        project_context=_NON_EMPTY_PROJECT,
                    ):
                        events.append(event)

                    import json
                    payloads = [json.loads(e.split("data: ", 1)[1].strip()) for e in events if "data:" in e]

                    # State should be "composing" for non-empty project
                    state_event = next(p for p in payloads if p.get("type") == "state")
                    assert state_event.get("state") == "composing", (
                        f"Expected 'composing' for non-empty project, got '{state_event.get('state')}'"
                    )

    @pytest.mark.anyio
    async def test_orchestrate_accepts_quality_preset_param(self):
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
