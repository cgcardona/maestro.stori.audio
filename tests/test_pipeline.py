"""
Tests for the composer pipeline (run_pipeline).

Ensures REASONING, COMPOSING, and EDITING branches return correct PipelineOutput.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.pipeline import run_pipeline, PipelineOutput
from app.core.intent import IntentResult, Intent, SSEState, Slots


@pytest.fixture
def mock_llm():
    """Mock LLM client."""
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(content="Here is your answer."))
    llm.close = AsyncMock()
    return llm


@pytest.mark.asyncio
async def test_run_pipeline_reasoning_returns_llm_response(mock_llm):
    """REASONING route returns PipelineOutput with llm_response, no plan."""
    route = IntentResult(
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
    with patch("app.core.pipeline.get_intent_result", return_value=route):
        out = await run_pipeline("What is reverb?", {}, mock_llm)
    assert isinstance(out, PipelineOutput)
    assert out.route is route
    assert out.llm_response is not None
    assert out.llm_response.content == "Here is your answer."
    assert out.plan is None


@pytest.mark.asyncio
async def test_run_pipeline_composing_returns_plan(mock_llm):
    """COMPOSING route returns PipelineOutput with plan."""
    from app.core.planner import ExecutionPlan
    from app.core.expansion import ToolCall

    route = IntentResult(
        intent=Intent.GENERATE_MUSIC,
        sse_state=SSEState.COMPOSING,
        confidence=0.9,
        slots=Slots(),
        tools=[],
        allowed_tool_names=set(),
        tool_choice="auto",
        force_stop_after=False,
        requires_planner=True,
        reasons=(),
    )
    mock_plan = ExecutionPlan(
        tool_calls=[ToolCall(name="stori_generate_drums", params={})],
        llm_response_text="Generated plan",
    )
    with patch("app.core.pipeline.get_intent_result", return_value=route), \
         patch("app.core.pipeline.build_execution_plan", new_callable=AsyncMock, return_value=mock_plan):
        out = await run_pipeline("Create a beat", {}, mock_llm)
    assert isinstance(out, PipelineOutput)
    assert out.route is route
    assert out.plan is mock_plan
    assert out.plan.tool_calls
    assert out.llm_response is None


@pytest.mark.asyncio
async def test_run_pipeline_editing_returns_llm_response(mock_llm):
    """EDITING route returns PipelineOutput with llm_response (tool allowlist)."""
    route = IntentResult(
        intent=Intent.TRACK_SET_VOLUME,
        sse_state=SSEState.EDITING,
        confidence=0.9,
        slots=Slots(),
        tools=[],
        allowed_tool_names={"stori_set_tempo", "stori_set_volume"},
        tool_choice="auto",
        force_stop_after=False,
        requires_planner=False,
        reasons=(),
    )
    with patch("app.core.pipeline.get_intent_result", return_value=route):
        out = await run_pipeline("Make it louder", {}, mock_llm)
    assert isinstance(out, PipelineOutput)
    assert out.route is route
    assert out.llm_response is not None
    assert out.plan is None
