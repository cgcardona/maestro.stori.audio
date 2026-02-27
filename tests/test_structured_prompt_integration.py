"""
Integration tests for structured prompts through planner, prompts, and weighted vibes.

Covers:
- Deterministic plan building from fully specified structured prompts
- Structured prompt context formatting
- Weighted vibe matching
- Partial structured prompts falling back to LLM
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from maestro.core.intent import Intent, IntentResult, Slots, SSEState
from maestro.core.intent.models import SlotsExtrasDict
from maestro.core.intent_config import IdiomMatch, match_weighted_vibes
from maestro.core.planner import ExecutionPlan, build_execution_plan
from maestro.prompts import MaestroPrompt, TargetSpec, VibeWeight
from maestro.core.prompts import structured_prompt_context


# ─── Deterministic plan building ────────────────────────────────────────────


class TestDeterministicPlan:
    """When all required fields are present, planner skips LLM."""

    @pytest.mark.asyncio
    async def test_full_structured_prompt_produces_deterministic_plan(self) -> None:

        """Style + tempo + roles + bars → deterministic plan, no LLM call."""
        parsed = MaestroPrompt(
            raw="MAESTRO PROMPT...",
            mode="compose",
            request="build a groove",
            style="melodic techno",
            key="F#m",
            tempo=126,
            roles=["drums", "bass", "arp", "pads"],
            constraints={"bars": 16, "density": "medium"},
        )

        llm = AsyncMock()
        route = _make_composing_route(parsed)

        plan = await build_execution_plan(
            user_prompt="build a groove",
            project_state={},
            route=route,
            llm=llm,
            parsed=parsed,
        )

        # LLM should NOT have been called
        llm.chat.assert_not_called()

        assert plan.is_valid
        assert plan.safety_validated
        assert len(plan.tool_calls) > 0

        names = [tc.name for tc in plan.tool_calls]
        assert "stori_generate_midi" in names or any(
            n.startswith("stori_generate") for n in names
        )
        assert "deterministic_plan" in plan.notes[0]

    @pytest.mark.asyncio
    async def test_partial_structured_prompt_falls_back_to_llm(self) -> None:

        """Missing bars → can't build deterministic plan → uses LLM."""
        parsed = MaestroPrompt(
            raw="MAESTRO PROMPT...",
            mode="compose",
            request="build a groove",
            style="melodic techno",
            tempo=126,
            roles=["drums", "bass"],
            constraints={},  # no bars
        )

        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=MagicMock(content='{"generations": [{"role": "drums", "style": "melodic techno", "tempo": 126, "bars": 8}], "edits": [], "mix": []}'))
        route = _make_composing_route(parsed)

        plan = await build_execution_plan(
            user_prompt="build a groove",
            project_state={},
            route=route,
            llm=llm,
            parsed=parsed,
        )

        # LLM should have been called (fallback)
        llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_parsed_prompt_uses_llm(self) -> None:

        """Without parsed prompt, normal LLM-based planning."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=MagicMock(content='{"generations": [{"role": "drums", "style": "boom_bap", "tempo": 90, "bars": 8}], "edits": [], "mix": []}'))
        route = _make_composing_route()

        plan = await build_execution_plan(
            user_prompt="make a boom bap beat",
            project_state={},
            route=route,
            llm=llm,
            parsed=None,
        )

        llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_deterministic_plan_respects_key(self) -> None:

        """Key from parsed prompt should be in the generation steps."""
        parsed = MaestroPrompt(
            raw="...",
            mode="compose",
            request="go",
            style="jazz",
            key="Cm",
            tempo=120,
            roles=["bass"],
            constraints={"bars": 8},
        )

        llm = AsyncMock()
        route = _make_composing_route(parsed)

        plan = await build_execution_plan(
            user_prompt="go",
            project_state={},
            route=route,
            llm=llm,
            parsed=parsed,
        )

        llm.chat.assert_not_called()
        assert plan.is_valid

    @pytest.mark.asyncio
    async def test_constraints_passed_through(self) -> None:

        """Non-bars constraints should appear in the plan."""
        parsed = MaestroPrompt(
            raw="...",
            mode="compose",
            request="go",
            style="house",
            tempo=128,
            roles=["drums"],
            constraints={"bars": 8, "density": "sparse"},
        )

        llm = AsyncMock()
        route = _make_composing_route(parsed)

        plan = await build_execution_plan(
            user_prompt="go",
            project_state={},
            route=route,
            llm=llm,
            parsed=parsed,
        )

        assert plan.is_valid
        assert plan.safety_validated


# ─── Structured prompt context ──────────────────────────────────────────────


class TestStructuredPromptContext:
    """Test the LLM system prompt injection from parsed fields."""

    def test_full_context_output(self) -> None:

        parsed = MaestroPrompt(
            raw="...",
            mode="compose",
            request="go",
            target=TargetSpec(kind="project"),
            style="melodic techno",
            key="F#m",
            tempo=126,
            roles=["kick", "bass", "arp"],
            constraints={"bars": 16, "density": "medium"},
            vibes=[VibeWeight("darker", 2), VibeWeight("hypnotic", 3)],
        )
        ctx = structured_prompt_context(parsed)

        assert "STRUCTURED INPUT" in ctx
        assert "Mode: compose" in ctx
        assert "Style: melodic techno" in ctx
        assert "Key: F#m" in ctx
        assert "Tempo: 126 BPM" in ctx
        assert "kick" in ctx
        assert "bass" in ctx
        assert "arp" in ctx
        assert "bars=16" in ctx
        assert "darker (weight 2)" in ctx
        assert "hypnotic (weight 3)" in ctx
        assert "Do not re-infer" in ctx

    def test_minimal_context_output(self) -> None:

        parsed = MaestroPrompt(
            raw="...",
            mode="ask",
            request="why?",
        )
        ctx = structured_prompt_context(parsed)

        assert "Mode: ask" in ctx
        assert "Style:" not in ctx
        assert "Tempo:" not in ctx
        assert "Roles:" not in ctx

    def test_target_with_name(self) -> None:

        parsed = MaestroPrompt(
            raw="...",
            mode="edit",
            request="eq it",
            target=TargetSpec(kind="track", name="Bass"),
        )
        ctx = structured_prompt_context(parsed)

        assert "Target: track:Bass" in ctx

    def test_unweighted_vibe_no_weight_label(self) -> None:

        parsed = MaestroPrompt(
            raw="...",
            mode="edit",
            request="fix",
            vibes=[VibeWeight("darker", 1)],
        )
        ctx = structured_prompt_context(parsed)
        assert "darker" in ctx
        assert "(weight 1)" not in ctx


# ─── Weighted vibe matching ─────────────────────────────────────────────────


class TestWeightedVibes:
    """Test match_weighted_vibes from intent_config."""

    def test_single_vibe_match(self) -> None:

        matches = match_weighted_vibes([("darker", 2)])
        assert len(matches) == 1
        assert matches[0].intent == Intent.MIX_TONALITY
        assert matches[0].weight == 2
        assert matches[0].phrase == "darker"

    def test_multiple_vibes_sorted_by_weight(self) -> None:

        matches = match_weighted_vibes([
            ("wider", 1),
            ("punchier", 3),
            ("darker", 2),
        ])
        assert len(matches) == 3
        assert matches[0].weight == 3  # punchier
        assert matches[1].weight == 2  # darker
        assert matches[2].weight == 1  # wider

    def test_unknown_vibe_skipped(self) -> None:

        matches = match_weighted_vibes([
            ("darker", 2),
            ("totally_unknown_vibe", 5),
        ])
        assert len(matches) == 1
        assert matches[0].phrase == "darker"

    def test_all_unknown_returns_empty(self) -> None:

        matches = match_weighted_vibes([("zzz_invalid", 1)])
        assert matches == []

    def test_unweighted_defaults_to_1(self) -> None:

        matches = match_weighted_vibes([("brighter", 1)])
        assert len(matches) == 1
        assert matches[0].weight == 1
        assert matches[0].intent == Intent.MIX_TONALITY

    def test_weight_field_on_idiom_match(self) -> None:

        m = IdiomMatch(
            intent=Intent.MIX_DYNAMICS,
            phrase="punchier",
            direction="increase",
            target="attack",
            weight=3,
        )
        assert m.weight == 3

    def test_idiom_match_default_weight(self) -> None:

        m = IdiomMatch(
            intent=Intent.MIX_DYNAMICS,
            phrase="punchier",
            direction="increase",
        )
        assert m.weight == 1


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_composing_route(parsed: MaestroPrompt | None = None) -> IntentResult:

    """Build a minimal COMPOSING IntentResult for testing."""
    extras: SlotsExtrasDict = {}
    if parsed:
        extras["parsed_prompt"] = parsed
    return IntentResult(
        intent=Intent.GENERATE_MUSIC,
        sse_state=SSEState.COMPOSING,
        confidence=0.99,
        slots=Slots(extras=extras),
        tools=[],
        allowed_tool_names=set(),
        tool_choice="none",
        force_stop_after=False,
        requires_planner=True,
        reasons=("structured_prompt",) if parsed else ("test",),
    )
