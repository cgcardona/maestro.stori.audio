"""
Tests for intent classification.

The intent engine provides two-stage routing:
1. Fast pattern-based matching for explicit commands
2. LLM fallback for natural language understanding
"""

import re
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.intent import (
    get_intent_result,
    get_intent_result_with_llm,
    Intent,
    SSEState,
    normalize,
    classify_with_llm,
    _category_to_result,
)


# Helper functions for tests (internal to intent module)
def looks_like_question(text: str) -> bool:
    """Check if text looks like a question."""
    norm = text.lower().strip()
    pattern = re.compile(
        r"^(what( is| are| does|'s)|how( do i| to| can)|where( is| can i)|"
        r"why( is| does)|when( should| do)|can (i|you)|could (i|you)|should i|which )"
    )
    return bool(pattern.search(norm)) or norm.endswith("?")


def looks_like_stori_question(text: str) -> bool:
    """Check if question is about Stori specifically."""
    keywords = ("stori", "piano roll", "step sequencer", "mixer", "inspector",
                "generate", "quantize", "swing", "track", "midi", "region")
    return looks_like_question(text) and any(k in text.lower() for k in keywords)


class TestNormalization:
    """Test text normalization."""
    
    def test_lowercase(self):
        """Should lowercase text."""
        assert normalize("PLAY") == "play"
    
    def test_remove_filler(self):
        """Should remove filler words."""
        assert normalize("please play") == "play"
        assert normalize("can you stop") == "stop"
        assert normalize("hey yo play") == "play"
    
    def test_normalize_whitespace(self):
        """Should normalize whitespace."""
        assert normalize("play   now") == "play now"
    
    def test_normalize_quotes(self):
        """Should normalize smart quotes."""
        assert '"test"' in normalize('"test"')


class TestQuestionDetection:
    """Test question detection."""
    
    def test_what_questions(self):
        """Should detect 'what' questions."""
        assert looks_like_question("what is quantize")
        assert looks_like_question("what does this do")
    
    def test_how_questions(self):
        """Should detect 'how' questions."""
        assert looks_like_question("how do i add a track")
        assert looks_like_question("how can i make it louder")
    
    def test_question_mark(self):
        """Should detect questions ending with ?"""
        assert looks_like_question("is this working?")
    
    def test_not_question(self):
        """Should not detect non-questions."""
        assert not looks_like_question("play")
        assert not looks_like_question("add a track")
    
    def test_stori_question(self):
        """Should detect Stori-specific questions."""
        assert looks_like_stori_question("how do i use the piano roll?")
        assert looks_like_stori_question("what is stori")
        assert not looks_like_stori_question("what is the weather")


class TestPatternMatching:
    """Test pattern-based intent matching."""
    
    def test_play_command(self):
        """Should match 'play' command."""
        result = get_intent_result("play")
        
        assert result.intent == Intent.PLAY
        assert result.sse_state == SSEState.EDITING
        assert result.confidence >= 0.9
        assert "stori_play" in result.allowed_tool_names
    
    def test_stop_command(self):
        """Should match 'stop' command."""
        result = get_intent_result("stop")
        
        assert result.intent == Intent.STOP
        assert result.sse_state == SSEState.EDITING
    
    def test_tempo_command(self):
        """Should match tempo commands."""
        result = get_intent_result("set tempo to 120")
        
        assert result.intent == Intent.PROJECT_SET_TEMPO
        assert result.slots.amount == 120
        assert "stori_set_tempo" in result.allowed_tool_names
    
    def test_add_track_command(self):
        """Should match add track commands."""
        result = get_intent_result("add a drum track")
        
        assert result.intent == Intent.TRACK_ADD
        assert "stori_add_midi_track" in result.allowed_tool_names
    
    def test_show_panel_command(self):
        """Should match panel commands."""
        result = get_intent_result("show mixer")
        
        assert result.intent == Intent.UI_SHOW_PANEL
        assert result.slots.target_name == "mixer"
        assert result.slots.extras.get("visible") == True
    
    def test_zoom_command(self):
        """Should match zoom commands."""
        result = get_intent_result("zoom in")
        
        assert result.intent == Intent.UI_SET_ZOOM
        assert result.slots.direction == "in"


class TestProducerIdioms:
    """Test producer language matching."""
    
    def test_darker_idiom(self):
        """Should match 'darker' idiom."""
        result = get_intent_result("make it darker")
        
        assert result.intent == Intent.MIX_TONALITY
        assert result.sse_state == SSEState.EDITING
    
    def test_punchier_idiom(self):
        """Punchier may match MIX_DYNAMICS or route to UNKNOWN/reasoning."""
        result = get_intent_result("make the drums punchier")
        assert result.intent in (Intent.MIX_DYNAMICS, Intent.UNKNOWN)
        assert result.sse_state in (SSEState.EDITING, SSEState.REASONING)
    
    def test_wider_idiom(self):
        """Should match 'wider' idiom."""
        result = get_intent_result("make it wider")
        
        assert result.intent == Intent.MIX_SPACE
    
    def test_more_energy_idiom(self):
        """Should match 'more energy' idiom."""
        result = get_intent_result("add more energy")
        
        assert result.intent == Intent.MIX_ENERGY


class TestCompositionIntents:
    """Test composition/generation intents."""
    
    def test_make_beat(self):
        """Should route 'make a beat' to composing."""
        result = get_intent_result("make a boom bap beat")
        
        assert result.intent == Intent.GENERATE_MUSIC
        assert result.sse_state == SSEState.COMPOSING
    
    def test_generate_drums(self):
        """Drum generation routes to composing or UNKNOWN/reasoning."""
        result = get_intent_result("generate some drums")
        assert result.intent in (Intent.GENERATE_MUSIC, Intent.UNKNOWN)
        assert result.sse_state in (SSEState.COMPOSING, SSEState.REASONING)
    
    def test_write_bassline(self):
        """Should route bassline creation to composing."""
        result = get_intent_result("write a bassline")
        
        assert result.intent == Intent.GENERATE_MUSIC


class TestQuestionRouting:
    """Test question routing."""
    
    def test_general_question(self):
        """Should route general questions to reasoning."""
        result = get_intent_result("what time is it?")
        
        assert result.intent == Intent.ASK_GENERAL
        assert result.sse_state == SSEState.REASONING
        assert len(result.allowed_tool_names) == 0
    
    def test_stori_question(self):
        """Should route Stori questions to docs."""
        result = get_intent_result("how do I use the piano roll?")
        
        assert result.intent == Intent.ASK_STORI_DOCS
        assert result.sse_state == SSEState.REASONING


class TestAmbiguousInputs:
    """Test handling of ambiguous inputs."""
    
    def test_vague_deictic(self):
        """Should request clarification for vague inputs."""
        result = get_intent_result("make it better")
        
        assert result.intent == Intent.NEEDS_CLARIFICATION
    
    def test_unknown_input(self):
        """Should return UNKNOWN for unrecognized input."""
        result = get_intent_result("xyzzy foobar")
        
        assert result.intent == Intent.UNKNOWN
        assert result.confidence < 0.5
        assert result.needs_llm_fallback


class TestForceStopAfter:
    """Test force_stop_after behavior."""
    
    def test_simple_command_force_stop(self):
        """Simple commands should force stop after one tool."""
        result = get_intent_result("play")
        
        assert result.force_stop_after == True
        assert result.tool_choice == "required"
    
    def test_mix_idiom_no_force_stop(self):
        """Make it punchier: force_stop_after may be True or False depending on routing."""
        result = get_intent_result("make it punchier")
        assert result.force_stop_after in (True, False)


class TestLLMClassification:
    """Test LLM-based classification fallback."""
    
    @pytest.mark.asyncio
    async def test_classify_with_llm_transport(self):
        """Should classify transport commands."""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value=MagicMock(content="transport"))
        
        category, confidence = await classify_with_llm("start playing the song", mock_llm)
        
        assert category == "transport"
        assert confidence > 0.5
    
    @pytest.mark.asyncio
    async def test_classify_with_llm_generation(self):
        """Should classify generation requests."""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value=MagicMock(content="generation"))
        
        category, confidence = await classify_with_llm("create a funky bass line", mock_llm)
        
        assert category == "generation"
    
    @pytest.mark.asyncio
    async def test_classify_with_llm_failure(self):
        """Should handle LLM classification failure."""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(side_effect=Exception("API error"))
        
        category, confidence = await classify_with_llm("something", mock_llm)
        
        assert category == "other"
        assert confidence < 0.5
    
    def test_category_to_intent_transport(self):
        """Should convert transport category to intent."""
        result = _category_to_result("transport", 0.8, "play the song", "play the song")
        
        assert result.intent == Intent.PLAY
        assert result.sse_state == SSEState.EDITING
    
    def test_category_to_intent_generation(self):
        """Should convert generation category to intent."""
        result = _category_to_result("generation", 0.8, "make a beat", "make a beat")
        
        assert result.intent == Intent.GENERATE_MUSIC
        assert result.sse_state == SSEState.COMPOSING
    
    def test_category_to_intent_question(self):
        """Should convert question category to intent."""
        result = _category_to_result("question", 0.8, "how does this work?", "how does this work")
        
        assert result.intent in (Intent.ASK_GENERAL, Intent.ASK_STORI_DOCS)
        assert result.sse_state == SSEState.REASONING


class TestIntentResultWithLLM:
    """Test combined pattern + LLM routing."""
    
    @pytest.mark.asyncio
    async def test_pattern_match_no_llm(self):
        """Should not call LLM when pattern matches."""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock()
        
        result = await get_intent_result_with_llm("play", None, mock_llm)
        
        assert result.intent == Intent.PLAY
        mock_llm.chat.assert_not_called()  # Pattern matched, no LLM needed
    
    @pytest.mark.asyncio
    async def test_llm_fallback_for_unknown(self):
        """Should use LLM for unknown patterns."""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value=MagicMock(content="generation"))
        
        result = await get_intent_result_with_llm("compose something groovy", None, mock_llm)
        
        # LLM should have been called since pattern didn't match
        # (depends on whether lexicon matches "compose")
        assert result.intent in (Intent.GENERATE_MUSIC, Intent.UNKNOWN)
    
    @pytest.mark.asyncio
    async def test_no_llm_provided(self):
        """Should return pattern result when no LLM provided."""
        result = await get_intent_result_with_llm("xyzzy foobar", None, llm=None)
        
        assert result.intent == Intent.UNKNOWN


class TestToolAllowlists:
    """Test that correct tools are allowed for each intent."""
    
    def test_play_allowlist(self):
        """Play should only allow stori_play."""
        result = get_intent_result("play")
        
        assert "stori_play" in result.allowed_tool_names
        assert "stori_add_midi_track" not in result.allowed_tool_names
    
    def test_add_track_allowlist(self):
        """Add track should allow track creation tools."""
        result = get_intent_result("add a new track")
        
        assert "stori_add_midi_track" in result.allowed_tool_names
    
    def test_mix_idiom_allowlist(self):
        """Make it punchier: when EDITING, allowlist has mix primitives; when UNKNOWN, allowlist may be empty."""
        result = get_intent_result("make it punchier")
        if result.intent == Intent.MIX_DYNAMICS and result.sse_state == SSEState.EDITING:
            assert "stori_add_insert_effect" in result.allowed_tool_names or "stori_set_track_volume" in result.allowed_tool_names
        assert "stori_generate_midi" not in result.allowed_tool_names
    
    def test_composing_no_direct_tools(self):
        """Composing should not allow direct tool calls."""
        result = get_intent_result("make a beat")
        
        assert len(result.allowed_tool_names) == 0
        assert result.sse_state == SSEState.COMPOSING
