"""
Tests for the Cursor-of-DAWs intent routing system.

Tests the intent classification, SSE state routing, and tool allowlisting.
"""
from __future__ import annotations

import pytest
from maestro.core.intent import (
    get_intent_result,
    SSEState,
    Intent,
    IntentResult,
    Slots,
    normalize,
)
from maestro.core.intent_config import match_producer_idiom


def looks_like_question(text: str) -> bool:

    """Helper for tests - check if text looks like a question."""
    import re
    norm = text.lower().strip()
    pattern = re.compile(
        r"^(what( is| are| does|'s)|how( do i| to| can)|where( is| can i)|"
        r"why( is| does)|when( should| do)|can (i|you)|could (i|you)|should i|which )"
    )
    return bool(pattern.search(norm)) or norm.endswith("?")


class TestSSEState:
    """Tests for SSEState enum."""

    def test_sse_state_values(self) -> None:

        """SSE states should have correct string values."""
        assert SSEState.REASONING.value == "reasoning"
        assert SSEState.EDITING.value == "editing"
        assert SSEState.COMPOSING.value == "composing"


class TestNormalize:
    """Tests for text normalization."""

    def test_lowercase(self) -> None:

        assert normalize("SET TEMPO") == "set tempo"

    def test_strip_filler(self) -> None:

        assert "please" not in normalize("please set tempo to 120")
        assert "pls" not in normalize("pls set tempo to 120")

    def test_collapse_whitespace(self) -> None:

        assert normalize("set   tempo  to   120") == "set tempo to 120"


class TestLooksLikeQuestion:
    """Tests for question detection."""

    def test_what_questions(self) -> None:

        assert looks_like_question("what is midi") is True
        assert looks_like_question("what are sends") is True

    def test_how_questions(self) -> None:

        assert looks_like_question("how do i record audio") is True
        assert looks_like_question("how to use eq") is True

    def test_question_mark(self) -> None:

        assert looks_like_question("is this a question?") is True

    def test_not_question(self) -> None:

        assert looks_like_question("set tempo to 120") is False
        assert looks_like_question("make a beat") is False


class TestIntentRoutingQuestions:
    """Tests for question routing to REASONING state."""

    def test_what_is_question(self) -> None:

        result = get_intent_result("What is MIDI quantization?")
        assert result.sse_state == SSEState.REASONING
        assert result.intent in (Intent.ASK_GENERAL, Intent.ASK_STORI_DOCS)
        assert result.allowed_tool_names == set()

    def test_how_to_question(self) -> None:

        result = get_intent_result("How do I record audio?")
        assert result.sse_state == SSEState.REASONING
        assert result.intent in (Intent.ASK_GENERAL, Intent.ASK_STORI_DOCS)

    def test_stori_question(self) -> None:

        result = get_intent_result("How do I use the piano roll?")
        assert result.sse_state == SSEState.REASONING
        assert result.intent == Intent.ASK_STORI_DOCS


class TestIntentRoutingEditing:
    """Tests for DAW commands routing to EDITING state."""

    def test_play(self) -> None:

        result = get_intent_result("play")
        assert result.sse_state == SSEState.EDITING
        assert result.intent == Intent.PLAY
        assert "stori_play" in result.allowed_tool_names
        assert result.force_stop_after is True

    def test_stop(self) -> None:

        result = get_intent_result("stop")
        assert result.sse_state == SSEState.EDITING
        assert result.intent == Intent.STOP
        assert "stori_stop" in result.allowed_tool_names

    def test_set_tempo(self) -> None:

        result = get_intent_result("set tempo to 120")
        assert result.sse_state == SSEState.EDITING
        assert result.intent == Intent.PROJECT_SET_TEMPO
        assert result.slots.amount == 120.0
        assert "stori_set_tempo" in result.allowed_tool_names

    def test_tempo_shorthand(self) -> None:

        result = get_intent_result("bpm 95")
        assert result.sse_state == SSEState.EDITING
        assert result.intent == Intent.PROJECT_SET_TEMPO
        assert result.slots.amount == 95.0

    def test_add_track(self) -> None:

        result = get_intent_result("add a new track")
        assert result.sse_state == SSEState.EDITING
        assert result.intent == Intent.TRACK_ADD
        assert "stori_add_midi_track" in result.allowed_tool_names

    def test_zoom_in(self) -> None:

        result = get_intent_result("zoom in")
        assert result.sse_state == SSEState.EDITING
        assert result.intent == Intent.UI_SET_ZOOM
        assert "stori_set_zoom" in result.allowed_tool_names

    def test_show_mixer(self) -> None:

        result = get_intent_result("show the mixer")
        assert result.sse_state == SSEState.EDITING
        assert result.intent == Intent.UI_SHOW_PANEL
        assert "stori_show_panel" in result.allowed_tool_names


class TestIntentRoutingComposing:
    """Tests for music generation routing to COMPOSING state."""

    def test_make_a_beat(self) -> None:

        result = get_intent_result("make a beat")
        assert result.sse_state == SSEState.COMPOSING
        assert result.intent == Intent.GENERATE_MUSIC
        # Composing uses planner, no direct tool allowlist
        assert result.allowed_tool_names == set()

    def test_boom_bap(self) -> None:

        result = get_intent_result("make a boom bap beat")
        assert result.sse_state == SSEState.COMPOSING
        assert result.intent == Intent.GENERATE_MUSIC

    def test_generate_drums(self) -> None:

        """Drum generation routes to composing or reasoning (LLM fallback)."""
        result = get_intent_result("generate drums")
        assert result.sse_state in (SSEState.COMPOSING, SSEState.REASONING)
        assert result.intent in (Intent.GENERATE_MUSIC, Intent.UNKNOWN)

    def test_write_bassline(self) -> None:

        result = get_intent_result("write a bassline")
        assert result.sse_state == SSEState.COMPOSING
        assert result.intent == Intent.GENERATE_MUSIC


class TestIntentRoutingIdioms:
    """Tests for producer idiom routing."""

    def test_make_it_darker(self) -> None:

        result = get_intent_result("make it darker")
        assert result.sse_state == SSEState.EDITING
        assert result.intent == Intent.MIX_TONALITY
        # Producer idioms allow limited primitives
        assert len(result.allowed_tool_names) > 0

    def test_more_punch(self) -> None:

        result = get_intent_result("add more punch")
        assert result.sse_state == SSEState.EDITING
        assert result.intent == Intent.MIX_DYNAMICS

    def test_wider(self) -> None:

        result = get_intent_result("make it wider")
        assert result.sse_state == SSEState.EDITING
        assert result.intent == Intent.MIX_SPACE

    def test_more_energy(self) -> None:

        result = get_intent_result("add more energy")
        assert result.sse_state == SSEState.EDITING
        assert result.intent == Intent.MIX_ENERGY


class TestVaguePrompts:
    """Tests for vague prompts requiring clarification."""

    def test_vague_deictics(self) -> None:

        # Prompts with vague deictics like "that", "this" should need clarification
        result = get_intent_result("fix that please")
        assert result.intent == Intent.NEEDS_CLARIFICATION
        assert result.sse_state == SSEState.REASONING

    def test_vague_that(self) -> None:

        result = get_intent_result("fix that")
        assert result.intent == Intent.NEEDS_CLARIFICATION

    def test_vague_this(self) -> None:

        """Short deictics get clarification or UNKNOWN (reasoning)."""
        result = get_intent_result("change this")
        assert result.intent in (Intent.NEEDS_CLARIFICATION, Intent.UNKNOWN)
        assert result.sse_state == SSEState.REASONING


class TestToolGating:
    """Tests for tool allowlist behavior."""

    def test_questions_have_no_tools(self) -> None:

        result = get_intent_result("What is EQ?")
        assert result.allowed_tool_names == set()
        assert result.sse_state == SSEState.REASONING

    def test_play_has_single_tool(self) -> None:

        result = get_intent_result("play")
        assert result.allowed_tool_names == {"stori_play"}
        assert result.force_stop_after is True

    def test_composing_has_no_direct_tools(self) -> None:

        result = get_intent_result("create a trap beat")
        assert result.allowed_tool_names == set()
        assert result.sse_state == SSEState.COMPOSING


class TestIntentResultStructure:
    """Tests for IntentResult dataclass."""

    def test_result_has_all_fields(self) -> None:

        result = get_intent_result("play")
        assert isinstance(result, IntentResult)
        assert isinstance(result.intent, Intent)
        assert isinstance(result.sse_state, SSEState)
        assert isinstance(result.confidence, float)
        assert isinstance(result.slots, Slots)
        assert isinstance(result.tools, list)
        assert isinstance(result.allowed_tool_names, set)
        assert result.tool_choice is not None
        assert isinstance(result.force_stop_after, bool)

    def test_slots_extraction(self) -> None:

        result = get_intent_result("set tempo to 128")
        assert result.slots.amount == 128.0
        assert result.slots.amount_unit == "bpm"


class TestForceStopAfter:
    """Tests for force_stop_after behavior."""

    def test_single_action_commands(self) -> None:

        """Single-action commands should force stop."""
        result = get_intent_result("play")
        assert result.force_stop_after is True

        result = get_intent_result("set tempo to 120")
        assert result.force_stop_after is True

    def test_producer_idioms_allow_continuation(self) -> None:

        """Producer idioms may require multiple operations."""
        result = get_intent_result("make it warmer")
        assert result.force_stop_after is False
