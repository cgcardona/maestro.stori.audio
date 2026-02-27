"""Tests for structured prompt → intent routing integration.

Verifies that structured prompts bypass the NL pipeline and route
deterministically via Mode, while non-structured prompts continue to
use the existing pattern/LLM pipeline unchanged.
"""
from __future__ import annotations

import pytest

from app.core.intent import (
    Intent,
    IntentResult,
    SSEState,
    get_intent_result,
)
from app.prompts import MaestroPrompt


# ─── Mode: compose → COMPOSING ──────────────────────────────────────────────


class TestMaestroRouting:
    def test_compose_routes_to_composing(self) -> None:

        prompt = "MAESTRO PROMPT\nMode: compose\nRequest: make a beat"
        result = get_intent_result(prompt)
        assert result.intent == Intent.GENERATE_MUSIC
        assert result.sse_state == SSEState.COMPOSING

    def test_compose_high_confidence(self) -> None:

        prompt = "MAESTRO PROMPT\nMode: compose\nRequest: make a beat"
        result = get_intent_result(prompt)
        assert result.confidence == 0.99

    def test_compose_has_structured_prompt_reason(self) -> None:

        prompt = "MAESTRO PROMPT\nMode: compose\nRequest: make a beat"
        result = get_intent_result(prompt)
        assert "structured_prompt" in result.reasons

    def test_compose_requires_planner(self) -> None:

        prompt = "MAESTRO PROMPT\nMode: compose\nRequest: make a beat"
        result = get_intent_result(prompt)
        assert result.requires_planner is True


# ─── Mode: edit → EDITING ───────────────────────────────────────────────────


class TestEditRouting:
    def test_edit_routes_to_editing(self) -> None:

        prompt = "MAESTRO PROMPT\nMode: edit\nTarget: track:Bass\nRequest: tighten it"
        result = get_intent_result(prompt)
        assert result.sse_state == SSEState.EDITING

    def test_edit_with_vibe_matches_idiom(self) -> None:

        prompt = (
            "MAESTRO PROMPT\nMode: edit\nTarget: track:Bass\n"
            "Vibe:\n- punchier:3\n"
            "Request: make it hit harder"
        )
        result = get_intent_result(prompt)
        assert result.intent == Intent.MIX_DYNAMICS

    def test_edit_with_darker_vibe(self) -> None:

        prompt = (
            "MAESTRO PROMPT\nMode: edit\n"
            "Vibe:\n- darker:2\n"
            "Request: cut the highs"
        )
        result = get_intent_result(prompt)
        assert result.intent == Intent.MIX_TONALITY

    def test_edit_with_wider_vibe(self) -> None:

        prompt = (
            "MAESTRO PROMPT\nMode: edit\n"
            "Vibe:\n- wider:1\n"
            "Request: spread it out"
        )
        result = get_intent_result(prompt)
        assert result.intent == Intent.MIX_SPACE

    def test_edit_with_compressor_constraint(self) -> None:

        prompt = (
            "MAESTRO PROMPT\nMode: edit\nTarget: track:Drums\n"
            "Constraints:\n- compressor: analog\n"
            "Request: add compression"
        )
        result = get_intent_result(prompt)
        assert result.intent == Intent.FX_ADD_INSERT

    def test_edit_default_intent_when_no_vibes(self) -> None:

        prompt = "MAESTRO PROMPT\nMode: edit\nRequest: fix something"
        result = get_intent_result(prompt)
        assert result.sse_state == SSEState.EDITING


# ─── Mode: ask → REASONING ──────────────────────────────────────────────────


class TestAskRouting:
    def test_ask_routes_to_reasoning(self) -> None:

        prompt = "MAESTRO PROMPT\nMode: ask\nRequest: why does reverb cause latency?"
        result = get_intent_result(prompt)
        assert result.intent == Intent.ASK_GENERAL
        assert result.sse_state == SSEState.REASONING

    def test_ask_no_tools(self) -> None:

        prompt = "MAESTRO PROMPT\nMode: ask\nRequest: explain quantization"
        result = get_intent_result(prompt)
        assert len(result.allowed_tool_names) == 0


# ─── Slots carry parsed data ────────────────────────────────────────────────


class TestSlotsPopulation:
    def test_target_in_slots(self) -> None:

        prompt = "MAESTRO PROMPT\nMode: edit\nTarget: track:Lead\nRequest: eq it"
        result = get_intent_result(prompt)
        assert result.slots.target_type == "track"
        assert result.slots.target_name == "Lead"

    def test_request_in_value_str(self) -> None:

        prompt = "MAESTRO PROMPT\nMode: compose\nRequest: lay down some funk"
        result = get_intent_result(prompt)
        assert result.slots.value_str == "lay down some funk"

    def test_parsed_prompt_in_extras(self) -> None:

        prompt = (
            "MAESTRO PROMPT\nMode: compose\n"
            "Style: jazz\nKey: Cm\nTempo: 90\n"
            "Request: smooth groove"
        )
        result = get_intent_result(prompt)
        parsed = result.slots.extras.get("parsed_prompt")
        assert isinstance(parsed, MaestroPrompt)
        assert parsed.style == "jazz"
        assert parsed.key == "Cm"
        assert parsed.tempo == 90


# ─── Mode field overrides NL pattern matching ────────────────────────────────


class TestModeOverridesPatterns:
    """Structured prompts whose Request text would normally match
    a pattern rule must still route via the Mode field."""

    def test_compose_with_tempo_in_request(self) -> None:

        """'set tempo to 120' would match the tempo pattern rule,
        but Mode: compose must win."""
        prompt = (
            "MAESTRO PROMPT\nMode: compose\n"
            "Request: set tempo to 120 and build a beat"
        )
        result = get_intent_result(prompt)
        assert result.intent == Intent.GENERATE_MUSIC
        assert result.sse_state == SSEState.COMPOSING

    def test_ask_with_play_in_request(self) -> None:

        """'play' alone would match the transport rule."""
        prompt = "MAESTRO PROMPT\nMode: ask\nRequest: why does play feel laggy?"
        result = get_intent_result(prompt)
        assert result.intent == Intent.ASK_GENERAL
        assert result.sse_state == SSEState.REASONING

    def test_edit_with_generation_phrase_in_request(self) -> None:

        """'make a beat' would match generation detection."""
        prompt = "MAESTRO PROMPT\nMode: edit\nRequest: make a beat sound punchier"
        result = get_intent_result(prompt)
        assert result.sse_state == SSEState.EDITING


# ─── Non-structured prompts still work unchanged ────────────────────────────


class TestNonStructuredUnchanged:
    """Verify the existing NL pipeline is not affected."""

    def test_natural_language_generation(self) -> None:

        result = get_intent_result("make a boom bap beat")
        assert result.intent == Intent.GENERATE_MUSIC
        assert "structured_prompt" not in result.reasons

    def test_question_routing(self) -> None:

        result = get_intent_result("what is quantization?")
        assert result.sse_state == SSEState.REASONING

    def test_transport_play(self) -> None:

        result = get_intent_result("play")
        assert result.intent == Intent.PLAY

    def test_tempo_command(self) -> None:

        result = get_intent_result("set tempo to 120")
        assert result.intent == Intent.PROJECT_SET_TEMPO

    def test_producer_idiom(self) -> None:

        result = get_intent_result("make it darker")
        assert result.intent == Intent.MIX_TONALITY
        assert "structured_prompt" not in result.reasons


# ─── Spec full examples ─────────────────────────────────────────────────────


class TestSpecExamples:
    """One test per full example from docs/protocol/maestro_prompt_spec.md."""

    def test_example_1_compose_advanced(self) -> None:

        prompt = (
            "MAESTRO PROMPT\n"
            "Mode: compose\n"
            "Target: project\n"
            "Style: melodic techno\n"
            "Key: F#m\n"
            "Tempo: 126\n"
            "\n"
            "Role:\n"
            "- kick\n"
            "- bass\n"
            "- arp\n"
            "- pad\n"
            "\n"
            "Constraints:\n"
            "- bars: 16\n"
            "- density: medium\n"
            "- instruments: analog kick, sub bass, pluck\n"
            "\n"
            "Vibe:\n"
            "- darker:2\n"
            "- hypnotic:3\n"
            "- wider:1\n"
            "\n"
            "Request: Build an intro groove that evolves every 4 bars and opens into a club-ready loop."
        )
        result = get_intent_result(prompt)
        assert result.intent == Intent.GENERATE_MUSIC
        assert result.sse_state == SSEState.COMPOSING
        assert result.confidence == 0.99

        parsed = result.slots.extras["parsed_prompt"]
        assert isinstance(parsed, MaestroPrompt)
        assert parsed.style == "melodic techno"
        assert parsed.key == "F#m"
        assert parsed.tempo == 126
        assert parsed.roles == ["kick", "bass", "arp", "pad"]

    def test_example_2_edit_track(self) -> None:

        prompt = (
            "MAESTRO PROMPT\n"
            "Mode: edit\n"
            "Target: track:Bass\n"
            "\n"
            "Vibe:\n"
            "- punchier:3\n"
            "- tighter low end:2\n"
            "\n"
            "Constraints:\n"
            "- compressor: analog\n"
            "- eq_focus: 200hz cleanup\n"
            "\n"
            "Request: Tighten the bass and make it hit harder without increasing loudness."
        )
        result = get_intent_result(prompt)
        assert result.sse_state == SSEState.EDITING
        assert result.slots.target_type == "track"
        assert result.slots.target_name == "Bass"

    def test_example_3_ask_reasoning(self) -> None:

        prompt = (
            "MAESTRO PROMPT\n"
            "Mode: ask\n"
            "Target: project\n"
            "\n"
            "Request: Why does my groove feel late when I add long reverb tails?"
        )
        result = get_intent_result(prompt)
        assert result.intent == Intent.ASK_GENERAL
        assert result.sse_state == SSEState.REASONING
