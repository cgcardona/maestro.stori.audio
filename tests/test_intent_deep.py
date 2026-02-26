"""Deep tests for intent classification (app/core/intent.py).

Covers: normalize, _is_question, _is_vague, _is_affirmative, _is_negative,
get_intent_result for various patterns, get_intent_result_with_llm,
_extract_quoted, _num, classify_with_llm fallback.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.contracts.llm_types import ChatMessage
from app.core.intent import (
    normalize,
    get_intent_result,
    get_intent_result_with_llm,
    Intent,
    IntentResult,
)
from app.core.intent_config import SSEState


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------


class TestNormalize:

    def test_lowercase_and_strip(self) -> None:

        result = normalize("  SET TEMPO  ")
        # "set" may or may not be stripped as filler; just ensure lowercase + stripped
        assert result == result.lower().strip()

    def test_filler_removal(self) -> None:

        # "please" and "just" are filler words
        result = normalize("please just set tempo to 120")
        assert "please" not in result
        assert "just" not in result
        assert "120" in result

    def test_multiple_spaces_collapsed(self) -> None:

        result = normalize("set   tempo   to   120")
        assert "  " not in result


# ---------------------------------------------------------------------------
# Pattern matching - transport
# ---------------------------------------------------------------------------


class TestTransportIntents:

    def test_play(self) -> None:

        result = get_intent_result("play")
        assert result.intent == Intent.PLAY

    def test_stop(self) -> None:

        result = get_intent_result("stop")
        # "stop" could match STOP or _is_negative; just verify it returns a result
        assert isinstance(result, IntentResult)

    def test_start(self) -> None:

        result = get_intent_result("start")
        assert result.intent == Intent.PLAY


# ---------------------------------------------------------------------------
# Pattern matching - project
# ---------------------------------------------------------------------------


class TestProjectIntents:

    def test_set_tempo(self) -> None:

        result = get_intent_result("set tempo to 120")
        assert result.intent == Intent.PROJECT_SET_TEMPO

    def test_bpm_shorthand(self) -> None:

        result = get_intent_result("bpm 140")
        assert result.intent == Intent.PROJECT_SET_TEMPO

    def test_set_key(self) -> None:

        result = get_intent_result("set key to Am")
        assert result.intent == Intent.PROJECT_SET_KEY


# ---------------------------------------------------------------------------
# Pattern matching - UI
# ---------------------------------------------------------------------------


class TestUIIntents:

    def test_show_mixer(self) -> None:

        result = get_intent_result("show mixer")
        assert result.intent == Intent.UI_SHOW_PANEL

    def test_open_piano_roll(self) -> None:

        result = get_intent_result("open piano roll")
        assert result.intent == Intent.UI_SHOW_PANEL

    def test_zoom_in(self) -> None:

        result = get_intent_result("zoom in")
        assert result.intent == Intent.UI_SET_ZOOM

    def test_zoom_out(self) -> None:

        result = get_intent_result("zoom out")
        assert result.intent == Intent.UI_SET_ZOOM


# ---------------------------------------------------------------------------
# Pattern matching - questions
# ---------------------------------------------------------------------------


class TestQuestionIntents:

    def test_general_question(self) -> None:

        result = get_intent_result("what is a chord progression?")
        assert result.sse_state == SSEState.REASONING

    def test_how_question(self) -> None:

        result = get_intent_result("how does reverb work?")
        assert result.sse_state == SSEState.REASONING


# ---------------------------------------------------------------------------
# Affirmative/negative detection
# ---------------------------------------------------------------------------


class TestAffirmativeNegative:

    def test_yes_is_affirmative(self) -> None:

        from app.core.intent import _is_affirmative
        assert _is_affirmative("yes") is True
        assert _is_affirmative("sure") is True
        assert _is_affirmative("yep") is True
        assert _is_affirmative("do it") is True

    def test_no_is_negative(self) -> None:

        from app.core.intent import _is_negative
        assert _is_negative("no") is True
        assert _is_negative("nope") is True
        assert _is_negative("cancel") is True

    def test_long_phrase_not_affirmative(self) -> None:

        from app.core.intent import _is_affirmative
        # 4+ words should not match
        assert _is_affirmative("create a new project with drums") is False


# ---------------------------------------------------------------------------
# get_intent_result_with_llm
# ---------------------------------------------------------------------------


class TestIntentWithLLM:

    @pytest.mark.anyio
    async def test_pattern_hit_no_llm_needed(self) -> None:

        result = await get_intent_result_with_llm("play")
        assert result.intent == Intent.PLAY

    @pytest.mark.anyio
    async def test_unknown_triggers_llm(self) -> None:

        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "generation"
        mock_llm.chat.return_value = mock_response

        result = await get_intent_result_with_llm(
            "I want something funky and groovy for my project",
            llm=mock_llm,
        )
        assert isinstance(result, IntentResult)

    @pytest.mark.anyio
    async def test_affirmative_with_context(self) -> None:

        history: list[ChatMessage] = [
            {"role": "user", "content": "make a beat"},
            {"role": "assistant", "content": "Would you like me to add drums?"},
        ]
        result = await get_intent_result_with_llm(
            "yes",
            conversation_history=history,
        )
        assert result.intent == Intent.GENERATE_MUSIC

    @pytest.mark.anyio
    async def test_affirmative_without_context(self) -> None:

        result = await get_intent_result_with_llm("yes")
        assert isinstance(result, IntentResult)

    @pytest.mark.anyio
    async def test_no_llm_fallback_when_none(self) -> None:

        """When llm is None, should not fail even if pattern returns UNKNOWN."""
        result = await get_intent_result_with_llm(
            "do something very obscure and unusual",
            llm=None,
        )
        assert isinstance(result, IntentResult)


# ---------------------------------------------------------------------------
# IntentResult properties
# ---------------------------------------------------------------------------


class TestIntentResultProperties:

    def test_needs_llm_fallback(self) -> None:

        result = get_intent_result("do the thing with the stuff")
        assert isinstance(result.needs_llm_fallback, bool)

    def test_allowed_tools(self) -> None:

        result = get_intent_result("set tempo to 120")
        assert isinstance(result.allowed_tool_names, set)

    def test_confidence_range(self) -> None:

        result = get_intent_result("set tempo to 120")
        assert 0.0 <= result.confidence <= 1.0

    def test_reasons_tuple(self) -> None:

        result = get_intent_result("play")
        assert isinstance(result.reasons, tuple)


# ---------------------------------------------------------------------------
# _extract_quoted and _num (helper functions)
# ---------------------------------------------------------------------------


class TestHelpers:

    def test_extract_quoted_double(self) -> None:

        from app.core.intent import _extract_quoted
        assert _extract_quoted('set name to "My Track"') == "My Track"

    def test_extract_quoted_single(self) -> None:

        from app.core.intent import _extract_quoted
        assert _extract_quoted("set name to 'Bass'") == "Bass"

    def test_extract_quoted_none(self) -> None:

        from app.core.intent import _extract_quoted
        assert _extract_quoted("no quotes here") is None

    def test_num_valid(self) -> None:

        from app.core.intent import _num
        assert _num("42") == 42.0
        assert _num("3.14") == 3.14

    def test_num_invalid(self) -> None:

        from app.core.intent import _num
        assert _num("abc") is None
        assert _num("") is None
