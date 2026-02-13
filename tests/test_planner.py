"""Tests for planner (ExecutionPlan, build_execution_plan, preview_plan)."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.planner import (
    ExecutionPlan,
    build_execution_plan,
    build_plan_from_dict,
    preview_plan,
)
from app.core.expansion import ToolCall
from app.core.intent import IntentResult, Intent, SSEState


class TestExecutionPlan:
    """Test ExecutionPlan properties and to_dict."""

    def test_is_valid_true_when_safety_validated_and_has_calls(self):
        plan = ExecutionPlan(
            tool_calls=[ToolCall("stori_play", {})],
            safety_validated=True,
        )
        assert plan.is_valid is True

    def test_is_valid_false_when_not_validated(self):
        plan = ExecutionPlan(
            tool_calls=[ToolCall("stori_play", {})],
            safety_validated=False,
        )
        assert plan.is_valid is False

    def test_is_valid_false_when_no_tool_calls(self):
        plan = ExecutionPlan(tool_calls=[], safety_validated=True)
        assert plan.is_valid is False

    def test_generation_count(self):
        plan = ExecutionPlan(
            tool_calls=[
                ToolCall("stori_add_midi_track", {"name": "Drums"}),
                ToolCall("stori_generate_drums", {"style": "boom_bap"}),
                ToolCall("stori_generate_midi", {"role": "bass"}),
            ],
            safety_validated=True,
        )
        assert plan.generation_count == 2

    def test_edit_count(self):
        plan = ExecutionPlan(
            tool_calls=[
                ToolCall("stori_add_midi_track", {"name": "Drums"}),
                ToolCall("stori_add_midi_region", {"name": "R1", "trackName": "Drums"}),
                ToolCall("stori_play", {}),
            ],
            safety_validated=True,
        )
        assert plan.edit_count == 2

    def test_to_dict(self):
        plan = ExecutionPlan(
            tool_calls=[ToolCall("stori_play", {})],
            notes=["note1"],
            safety_validated=True,
        )
        d = plan.to_dict()
        assert d["tool_calls"] == [{"name": "stori_play", "params": {}}]
        assert d["notes"] == ["note1"]
        assert d["safety_validated"] is True
        assert "validation_errors" in d


class TestBuildExecutionPlan:
    """Test build_execution_plan with mocked LLM."""

    @pytest.mark.asyncio
    async def test_validation_failure_returns_plan_with_notes(self):
        """When LLM returns invalid JSON, plan has validation notes."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=MagicMock(content="No JSON here at all."))
        route = IntentResult(
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
        plan = await build_execution_plan(
            user_prompt="make a beat",
            project_state={},
            route=route,
            llm=llm,
        )
        assert plan.validation_result is not None
        assert plan.validation_result.valid is False
        assert len(plan.notes) > 0
        assert len(plan.tool_calls) == 0

    @pytest.mark.asyncio
    async def test_valid_json_returns_plan_with_tool_calls(self):
        """When LLM returns valid plan JSON, we get tool_calls."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=MagicMock(content="""
        {
            "generations": [{"role": "drums", "style": "boom_bap", "tempo": 90, "bars": 8}],
            "edits": [
                {"action": "add_track", "name": "Drums"},
                {"action": "add_region", "track": "Drums", "barStart": 0, "bars": 8}
            ],
            "mix": []
        }
        """))
        route = IntentResult(
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
        plan = await build_execution_plan(
            user_prompt="make a boom bap beat",
            project_state={},
            route=route,
            llm=llm,
        )
        assert plan.safety_validated is True
        assert len(plan.tool_calls) > 0
        names = [tc.name for tc in plan.tool_calls]
        assert "stori_add_midi_track" in names
        assert "stori_generate_midi" in names or "stori_generate_drums" in names


class TestPreviewPlan:
    """Test preview_plan."""

    @pytest.mark.asyncio
    async def test_preview_returns_summary_dict(self):
        """preview_plan returns valid/total_steps/generations/edits/tool_calls/notes."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=MagicMock(content="""
        {
            "generations": [{"role": "drums", "style": "boom_bap", "tempo": 90, "bars": 8}],
            "edits": [{"action": "add_track", "name": "Drums"}],
            "mix": []
        }
        """))
        route = IntentResult(
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
        preview = await preview_plan(
            user_prompt="drums please",
            project_state={},
            route=route,
            llm=llm,
        )
        assert "valid" in preview
        assert "total_steps" in preview
        assert "generations" in preview
        assert "edits" in preview
        assert "tool_calls" in preview
        assert "notes" in preview
        assert preview["valid"] is True
        assert preview["total_steps"] >= 1
        assert isinstance(preview["tool_calls"], list)
