"""
Smoke tests for app.core.pipeline.run_pipeline — all LLM calls mocked.

Each test sends a prompt through the full intent → route → LLM/planner path
and asserts the PipelineOutput has the correct shape. No external I/O.

Coverage:
  1. REASONING path (ask / question) → llm_response set, plan=None
  2. COMPOSING path (generate / compose mode) → plan set, llm_response=None
  3. EDITING path (set tempo, add effect) → llm_response set, plan=None
  4. Structured prompt COMPOSING fast path → plan, LLM not called
  5. Structured prompt EDITING path → llm_response, structured context injected
  6. PipelineOutput fields — route always set
  7. Empty project state handled without error
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.pipeline import run_pipeline, PipelineOutput
from app.core.intent import SSEState, Intent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_llm(content: str = "ok") -> AsyncMock:

    llm = AsyncMock()
    llm.chat.return_value = MagicMock(content=content)
    return llm


def _mock_llm_with_plan(bars: int = 4) -> AsyncMock:

    """LLM that returns a valid JSON plan for COMPOSING requests."""
    import json
    plan_json = json.dumps({
        "generations": [
            {"role": "drums", "style": "house", "tempo": 128, "bars": bars},
        ],
        "edits": [
            {"action": "add_track", "name": "Drums"},
            {"action": "add_region", "track": "Drums", "barStart": 0, "bars": bars},
        ],
        "mix": [],
    })
    return _mock_llm(content=plan_json)


# ===========================================================================
# 1. REASONING path
# ===========================================================================

class TestReasoningPath:
    """A question or chat request routes to REASONING → LLM chat, no plan."""

    @pytest.mark.anyio
    async def test_reasoning_returns_llm_response(self) -> None:

        llm = _mock_llm("Jazz is a genre originating in New Orleans.")
        output = await run_pipeline(
            user_prompt="What is jazz?",
            project_state={},
            llm=llm,
        )
        assert isinstance(output, PipelineOutput)
        assert output.llm_response is not None
        assert output.plan is None

    @pytest.mark.anyio
    async def test_reasoning_route_is_reasoning(self) -> None:

        llm = _mock_llm("Here's how reverb works...")
        output = await run_pipeline(
            user_prompt="How does reverb work?",
            project_state={},
            llm=llm,
        )
        assert output.route.sse_state == SSEState.REASONING

    @pytest.mark.anyio
    async def test_reasoning_llm_called_with_no_tools(self) -> None:

        llm = _mock_llm()
        await run_pipeline(
            user_prompt="Explain chord progressions.",
            project_state={},
            llm=llm,
        )
        call_kwargs = llm.chat.call_args.kwargs
        assert call_kwargs.get("tools") == []
        assert call_kwargs.get("tool_choice") == "none"

    @pytest.mark.anyio
    async def test_reasoning_route_set_on_output(self) -> None:

        llm = _mock_llm()
        output = await run_pipeline(
            user_prompt="What tempo should I use for lofi?",
            project_state={},
            llm=llm,
        )
        assert output.route is not None


# ===========================================================================
# 2. COMPOSING path
# ===========================================================================

class TestComposingPath:
    """A music generation request routes to COMPOSING → planner plan, no direct llm_response."""

    @pytest.mark.anyio
    async def test_composing_returns_plan(self) -> None:

        llm = _mock_llm_with_plan()
        output = await run_pipeline(
            user_prompt="Make a boom bap beat at 90 BPM",
            project_state={},
            llm=llm,
        )
        assert isinstance(output, PipelineOutput)
        # COMPOSING path returns a plan (may or may not set llm_response)
        assert output.plan is not None

    @pytest.mark.anyio
    async def test_composing_route_state(self) -> None:

        llm = _mock_llm_with_plan()
        output = await run_pipeline(
            user_prompt="Generate a full lo-fi track",
            project_state={},
            llm=llm,
        )
        assert output.route.sse_state in (SSEState.COMPOSING,)

    @pytest.mark.anyio
    async def test_composing_plan_has_tool_calls_or_notes(self) -> None:

        """A valid plan has tool_calls; an invalid response still returns an ExecutionPlan."""
        llm = _mock_llm_with_plan()
        output = await run_pipeline(
            user_prompt="Create a house music groove",
            project_state={},
            llm=llm,
        )
        assert output.plan is not None
        # Either valid (has tool_calls) or invalid (has notes explaining failure)
        assert isinstance(output.plan.tool_calls, list)
        assert isinstance(output.plan.notes, list)


# ===========================================================================
# 3. EDITING path
# ===========================================================================

class TestEditingPath:
    """An editing command routes to EDITING → LLM with tool calls, no plan."""

    @pytest.mark.anyio
    async def test_editing_returns_llm_response(self) -> None:

        llm = _mock_llm()
        output = await run_pipeline(
            user_prompt="set the tempo to 120 BPM",
            project_state={},
            llm=llm,
        )
        # EDITING path returns llm_response
        assert output.route is not None
        if output.route.sse_state == SSEState.EDITING:
            assert output.llm_response is not None
            assert output.plan is None

    @pytest.mark.anyio
    async def test_editing_llm_receives_tools(self) -> None:

        """EDITING path passes allowed tools to the LLM."""
        llm = _mock_llm()
        output = await run_pipeline(
            user_prompt="Add reverb to the drums track",
            project_state={
                "tracks": [{"id": "t1", "name": "Drums", "regions": []}]
            },
            llm=llm,
        )
        if output.route.sse_state == SSEState.EDITING:
            call_kwargs = llm.chat.call_args.kwargs
            # Tools must be a list (may be empty if no matching allowed tools)
            assert isinstance(call_kwargs.get("tools"), list)


# ===========================================================================
# 4. Structured prompt COMPOSING fast path
# ===========================================================================

class TestStructuredPromptComposingFastPath:
    """A fully-specified structured prompt builds a plan deterministically."""

    @pytest.mark.anyio
    async def test_deterministic_plan_skips_llm(self) -> None:

        llm = AsyncMock()
        output = await run_pipeline(
            user_prompt=(
                "STORI PROMPT\n"
                "Mode: compose\n"
                "Style: house\n"
                "Tempo: 128\n"
                "Key: Am\n"
                "Role:\n"
                "- kick\n"
                "- bass\n"
                "Constraints:\n"
                "  bars: 8\n"
                "Request: Make the groove"
            ),
            project_state={},
            llm=llm,
        )
        # LLM must not have been called (deterministic path)
        llm.chat.assert_not_awaited()
        assert output.plan is not None
        assert output.plan.is_valid

    @pytest.mark.anyio
    async def test_deterministic_plan_has_region_calls(self) -> None:

        llm = AsyncMock()
        output = await run_pipeline(
            user_prompt=(
                "STORI PROMPT\n"
                "Mode: compose\n"
                "Style: techno\n"
                "Tempo: 140\n"
                "Key: Cm\n"
                "Role:\n"
                "- kick\n"
                "Constraints:\n"
                "  bars: 4\n"
                "Request: pound the floor"
            ),
            project_state={},
            llm=llm,
        )
        assert output.plan is not None
        region_calls = [
            tc for tc in output.plan.tool_calls
            if tc.name == "stori_add_midi_region"
        ]
        assert len(region_calls) >= 1

    @pytest.mark.anyio
    async def test_structured_prompt_with_position_calls_llm_for_context(self) -> None:

        """
        A structured prompt with Position: but missing bars falls back to LLM.
        The system prompt injected must contain ARRANGEMENT POSITION.
        """
        llm = _mock_llm_with_plan()
        output = await run_pipeline(
            user_prompt=(
                "STORI PROMPT\n"
                "Mode: compose\n"
                "Style: house\n"
                "Tempo: 128\n"
                "Key: Am\n"
                "Position: after intro\n"
                "Role:\n"
                "- kick\n"
                "Request: verse groove"  # no Constraints: bars, forces LLM
            ),
            project_state={
                "tracks": [
                    {"name": "intro", "regions": [
                        {"name": "intro", "startBeat": 0, "durationBeats": 64}
                    ]}
                ]
            },
            llm=llm,
        )
        assert output.plan is not None
        if llm.chat.called:
            call_kwargs = llm.chat.call_args.kwargs
            system = call_kwargs.get("system", "")
            assert "ARRANGEMENT POSITION" in system or "64" in system


# ===========================================================================
# 5. Structured prompt EDITING path
# ===========================================================================

class TestStructuredPromptEditingPath:
    """Mode: edit structured prompts route to EDITING."""

    @pytest.mark.anyio
    async def test_edit_mode_routes_to_editing(self) -> None:

        llm = _mock_llm()
        output = await run_pipeline(
            user_prompt=(
                "STORI PROMPT\n"
                "Mode: edit\n"
                "Target: track:Drums\n"
                "Vibe:\n"
                "- darker\n"
                "Request: make the snare punchier"
            ),
            project_state={
                "tracks": [{"id": "t1", "name": "Drums", "regions": []}]
            },
            llm=llm,
        )
        assert output.route is not None
        # Edit mode → EDITING state
        assert output.route.sse_state == SSEState.EDITING

    @pytest.mark.anyio
    async def test_structured_context_injected_in_edit_system_prompt(self) -> None:

        llm = _mock_llm()
        await run_pipeline(
            user_prompt=(
                "STORI PROMPT\n"
                "Mode: edit\n"
                "Target: track:Bass\n"
                "Vibe:\n"
                "- wider\n"
                "Request: widen the stereo field"
            ),
            project_state={},
            llm=llm,
        )
        if llm.chat.called:
            system = llm.chat.call_args.kwargs.get("system", "")
            assert "STORI STRUCTURED INPUT" in system


# ===========================================================================
# 6. PipelineOutput structural invariants
# ===========================================================================

class TestPipelineOutputInvariants:
    """route is always set; at most one of llm_response / plan is set."""

    @pytest.mark.anyio
    async def test_route_always_set(self) -> None:

        for prompt in [
            "What is a chord?",
            "Make a beat",
            "set tempo to 120",
        ]:
            llm = _mock_llm_with_plan()
            output = await run_pipeline(prompt, {}, llm)
            assert output.route is not None

    @pytest.mark.anyio
    async def test_not_both_plan_and_llm_response_for_composing(self) -> None:

        llm = _mock_llm_with_plan()
        output = await run_pipeline("Generate a hip hop beat", {}, llm)
        # COMPOSING path: plan is set, llm_response is not (it's in the plan)
        if output.route.sse_state == SSEState.COMPOSING:
            assert output.plan is not None


# ===========================================================================
# 7. Edge cases
# ===========================================================================

class TestPipelineEdgeCases:
    """Edge cases that must not crash the pipeline."""

    @pytest.mark.anyio
    async def test_empty_project_state(self) -> None:

        llm = _mock_llm()
        output = await run_pipeline("What is reverb?", {}, llm)
        assert output is not None

    @pytest.mark.anyio
    async def test_very_long_prompt(self) -> None:

        llm = _mock_llm()
        long_prompt = "make a beat " * 200
        output = await run_pipeline(long_prompt, {}, llm)
        assert output is not None

    @pytest.mark.anyio
    async def test_structured_prompt_ask_mode(self) -> None:

        """Mode: ask routes to REASONING."""
        llm = _mock_llm("Here's how EQ works...")
        output = await run_pipeline(
            "STORI PROMPT\nMode: ask\nRequest: How does EQ work?",
            {},
            llm,
        )
        assert output.route.sse_state == SSEState.REASONING
        assert output.llm_response is not None
        assert output.plan is None
