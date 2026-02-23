"""Tests for SSE formatting, reasoning sanitization, and BPE buffering."""
import json
import pytest

from app.core.sse_utils import ReasoningBuffer, sse_event, sanitize_reasoning, strip_tool_echoes


class TestSseEvent:
    """Test sse_event formatting."""

    @pytest.mark.anyio
    async def test_formats_as_sse_data_line(self):
        """Output should be data: {...}\\n\\n."""
        result = await sse_event({"type": "status", "message": "hello"})
        assert result.startswith("data: ")
        assert result.endswith("\n\n")
        payload = json.loads(result[6:].strip())
        assert payload["type"] == "status"
        assert payload["message"] == "hello"

    @pytest.mark.anyio
    async def test_handles_nested_structure(self):
        """Nested dicts are validated and serialized through the protocol model."""
        result = await sse_event({
            "type": "toolCall",
            "id": "tc-1",
            "name": "stori_add_track",
            "params": {"trackId": "abc-123"},
        })
        payload = json.loads(result[6:].strip())
        assert payload["params"]["trackId"] == "abc-123"

    @pytest.mark.anyio
    async def test_rejects_empty_dict(self):
        """Empty dict raises because 'type' field is missing."""
        from app.protocol.emitter import ProtocolSerializationError
        with pytest.raises(ProtocolSerializationError, match="missing 'type'"):
            await sse_event({})


class TestSanitizeReasoning:
    """Test sanitize_reasoning strips implementation details."""

    def test_removes_stori_function_calls(self):
        """Function call syntax should be removed."""
        text = "We should stori_add_midi_track(name=\"Drums\") then add notes."
        out = sanitize_reasoning(text)
        assert "stori_add_midi_track" not in out
        assert "add notes" in out or "notes" in out

    def test_removes_standalone_stori_names(self):
        """Standalone stori_* names should be removed."""
        text = "Use stori_set_tempo for the BPM."
        out = sanitize_reasoning(text)
        assert "stori_set_tempo" not in out

    def test_removes_uuids(self):
        """UUIDs should be stripped."""
        text = "Apply to track 550e8400-e29b-41d4-a716-446655440000."
        out = sanitize_reasoning(text)
        assert "550e8400" not in out
        assert "446655440000" not in out

    def test_removes_parameter_assignments(self):
        """Param-style assignments should be removed."""
        text = "trackId = \"abc-123\" startBeat = 0"
        out = sanitize_reasoning(text)
        assert "trackId" not in out or "abc-123" not in out
        assert "startBeat" not in out or "0" not in out

    def test_removes_code_markers(self):
        """Code block markers should be removed."""
        text = "Here is the plan ```json { } ```"
        out = sanitize_reasoning(text)
        assert "```" not in out

    def test_collapses_whitespace(self):
        """Multiple interior spaces should become single space."""
        text = "one   two   three"
        out = sanitize_reasoning(text)
        assert "   " not in out
        assert "one two three" in out

    def test_preserves_musical_reasoning(self):
        """Musical terms and natural language should remain."""
        text = "Use a boom bap style at 90 BPM with a minor key feel."
        out = sanitize_reasoning(text)
        assert "boom bap" in out
        assert "90" in out or "BPM" in out
        assert "minor" in out

    def test_preserves_leading_space_for_bpe(self):
        """Leading space from BPE tokens must be preserved for concatenation."""
        # BPE tokens often start with a space: " user", " has", " asked"
        assert sanitize_reasoning(" user").startswith(" ")
        assert sanitize_reasoning(" has asked me to create").startswith(" ")
        # Concatenation of BPE tokens should produce correct spacing
        chunks = ["The", " user", " has asked me to create", " a song"]
        result = "".join(sanitize_reasoning(c) for c in chunks)
        assert "Theuser" not in result  # No missing space
        assert "The user" in result

    def test_preserves_newlines_in_structured_reasoning(self):
        """Newlines for numbered lists and bullet points must survive."""
        # Numbered list item preceded by newline
        text = " Phish-style song:\n1. Set up project parameters"
        out = sanitize_reasoning(text)
        assert "\n" in out
        assert "1. Set up project parameters" in out

    def test_preserves_standalone_newline_token(self):
        """A token that is just a newline should pass through."""
        assert sanitize_reasoning("\n") == "\n"

    def test_preserves_newline_with_bullet(self):
        """Newline followed by bullet/dash should be preserved."""
        text = "\n- Tempo: 98 BPM"
        out = sanitize_reasoning(text)
        assert out.startswith("\n")
        assert "Tempo" in out

    def test_concatenated_tokens_preserve_structure(self):
        """Simulated reasoning stream with newlines renders with line breaks."""
        chunks = [
            " Phish-style song",
            ":\n",
            "1. Set",
            " up project",
            " parameters",
            ":\n",
            "- Tempo",
            ": 98 BPM",
            "\n",
            "- Key",
            ": D major",
        ]
        result = "".join(sanitize_reasoning(c) for c in chunks)
        assert "song:\n1. Set" in result
        assert "parameters:\n- Tempo" in result
        assert "BPM\n- Key" in result

    def test_empty_string_returns_empty(self):
        """Empty or whitespace-only (spaces/tabs) returns empty."""
        assert sanitize_reasoning("") == ""
        assert sanitize_reasoning("   ") == ""
        assert sanitize_reasoning("\t\t") == ""


class TestReasoningBuffer:
    """Test BPE token buffering for reasoning display."""

    def test_buffers_sub_word_tokens(self):
        """Sub-word BPE pieces should be merged before emission."""
        buf = ReasoningBuffer()
        assert buf.add("T") is None  # buffered
        assert buf.add("rey") is None  # still buffering (no word boundary)
        # Next token starts with space → flush previous buffer
        result = buf.add(" Anast")
        assert result is not None
        assert "Trey" in result

    def test_emits_at_space_boundary(self):
        """A token starting with space triggers emission of previous buffer."""
        buf = ReasoningBuffer()
        buf.add("hello")
        result = buf.add(" world")
        assert result == "hello"
        # "world" is in the buffer, flush it
        flushed = buf.flush()
        assert flushed is not None
        assert "world" in flushed

    def test_emits_at_newline_boundary(self):
        """A token starting with newline triggers emission."""
        buf = ReasoningBuffer()
        buf.add("parameters")
        result = buf.add("\n- Tempo")
        assert result == "parameters"
        flushed = buf.flush()
        assert flushed is not None
        assert "\n" in flushed
        assert "Tempo" in flushed

    def test_flush_returns_remaining(self):
        """Flush returns whatever is left in the buffer."""
        buf = ReasoningBuffer()
        buf.add("hello")
        buf.add("world")
        result = buf.flush()
        assert result == "helloworld"

    def test_flush_empty_returns_none(self):
        """Flush on empty buffer returns None."""
        buf = ReasoningBuffer()
        assert buf.flush() is None

    def test_add_empty_returns_none(self):
        """Adding empty string returns None."""
        buf = ReasoningBuffer()
        assert buf.add("") is None

    def test_sanitizes_on_emit(self):
        """Buffered text is sanitized (stori_* names removed) on emission."""
        buf = ReasoningBuffer()
        buf.add("use stori_add_midi_track")
        result = buf.add(" for drums")
        assert result is not None
        assert "stori_add_midi_track" not in result

    def test_full_bpe_sequence_reconstructs_correctly(self):
        """Simulated BPE stream for '(Trey Anastasio)' reconstructs without artifacts."""
        buf = ReasoningBuffer()
        chunks = ["(", "T", "rey", " Anast", "asio", ")"]
        emitted: list[str] = []
        for c in chunks:
            result = buf.add(c)
            if result:
                emitted.append(result)
        flushed = buf.flush()
        if flushed:
            emitted.append(flushed)
        full = "".join(emitted)
        assert "(Trey" in full
        assert "Anastasio)" in full
        assert "T rey" not in full  # No spurious space inside "Trey"

    def test_split_word_around_reconstructs(self):
        """BPE split of 'around' into 'aroun' + 'd' reconstructs correctly."""
        buf = ReasoningBuffer()
        chunks = [" aroun", "d", " 100", "-120"]
        emitted: list[str] = []
        for c in chunks:
            result = buf.add(c)
            if result:
                emitted.append(result)
        flushed = buf.flush()
        if flushed:
            emitted.append(flushed)
        full = "".join(emitted)
        assert "around" in full
        assert "aroun d" not in full

    def test_preserves_newlines_through_buffer(self):
        """Newlines in BPE tokens survive buffering and sanitization."""
        buf = ReasoningBuffer()
        chunks = ["song", ":\n", "1.", " Set"]
        emitted: list[str] = []
        for c in chunks:
            result = buf.add(c)
            if result:
                emitted.append(result)
        flushed = buf.flush()
        if flushed:
            emitted.append(flushed)
        full = "".join(emitted)
        assert "\n" in full
        assert "1." in full

    def test_safety_flush_on_long_buffer(self):
        """Buffer flushes when it exceeds the safety limit."""
        buf = ReasoningBuffer()
        # Feed a long string without word boundaries
        long_text = "a" * 250
        buf.add(long_text)
        # Next token (even without leading space) should trigger flush
        result = buf.add("x")
        assert result is not None
        assert len(result) >= 200


class TestStripToolEchoes:
    """Test strip_tool_echoes removes leaked tool-call syntax from content."""

    def test_empty_string_returns_empty(self):
        """Empty input returns empty string."""
        assert strip_tool_echoes("") == ""

    def test_plain_text_unchanged(self):
        """Normal natural-language text passes through untouched."""
        text = "I'll create a funky reggae bass line in E minor."
        assert strip_tool_echoes(text) == text

    def test_strips_single_keyword_arg(self):
        """Single parenthesized keyword arg is removed."""
        text = 'Let\'s set the key:\n\n(key="G major")\n\nNow the tempo.'
        result = strip_tool_echoes(text)
        assert '(key="G major")' not in result
        assert "Let's set the key:" in result
        assert "Now the tempo." in result

    def test_strips_continuation_keyword_arg(self):
        """Continuation keyword arg (leading comma) is removed."""
        text = 'Adding tracks:\n\n(, instrument="Drum Kit")\n\nDone.'
        result = strip_tool_echoes(text)
        assert '(, instrument="Drum Kit")' not in result
        assert "Adding tracks:" in result
        assert "Done." in result

    def test_strips_bare_parens(self):
        """Bare parenthesized commas like (,, ) are removed."""
        text = "Setting up:\n\n(,, )\n\nReady."
        result = strip_tool_echoes(text)
        assert "(,, )" not in result
        assert "Setting up:" in result
        assert "Ready." in result

    def test_strips_empty_parens(self):
        """Empty parens () are removed."""
        text = "Calling:\n\n()\n\nDone."
        result = strip_tool_echoes(text)
        assert "()" not in result

    def test_strips_multiline_tool_echo(self):
        """Multi-line tool echo (opening paren to closing paren) is removed."""
        text = (
            "Adding notes:\n\n"
            "(, notes=\n"
            " # Many notes here representing keyboard playing\n"
            ")\n\n"
            "Moving on."
        )
        result = strip_tool_echoes(text)
        assert "notes=" not in result
        assert "Many notes" not in result
        assert "Adding notes:" in result
        assert "Moving on." in result

    def test_strips_standalone_closing_paren(self):
        """Standalone closing paren on its own line is removed."""
        text = "Hello\n)\nWorld"
        result = strip_tool_echoes(text)
        assert "\n)" not in result
        assert "Hello" in result
        assert "World" in result

    def test_preserves_parenthetical_natural_language(self):
        """Normal parenthetical text (no = sign) is preserved."""
        text = "The song (in E minor) has a walking bass."
        assert strip_tool_echoes(text) == text

    def test_collapses_excess_newlines(self):
        """After stripping, consecutive blank lines are collapsed to at most one."""
        text = 'Line one.\n\n(key="value")\n\n\n\nLine two.'
        result = strip_tool_echoes(text)
        assert "\n\n\n" not in result
        assert "Line one." in result
        assert "Line two." in result

    def test_full_realistic_stream(self):
        """Realistic interleaved content from the original bug report is cleaned."""
        text = (
            "Let's start by setting the tempo and key signature:\n\n"
            '(key="G major")\n\n'
            "Now, I'll add the missing tracks:\n\n"
            '(, instrument="Drum Kit")\n\n'
            "For the keyboard track, I'll add a region:\n\n"
            "(,, )\n\n"
            "Now I'll add some Phish-style keyboard notes:\n\n"
            "(, notes=\n"
            " # Many notes here representing Phish-style keyboard playing\n"
            ")\n\n"
            "For the drums:\n\n"
            "(, notes=\n"
            " # Many notes representing Fishman-style drumming\n"
            ")"
        )
        result = strip_tool_echoes(text)
        # All tool echoes removed
        assert '(key="G major")' not in result
        assert "(, instrument=" not in result
        assert "(,, )" not in result
        assert "notes=" not in result
        assert "# Many notes" not in result
        # Natural language preserved
        assert "setting the tempo and key signature" in result
        assert "add the missing tracks" in result
        assert "keyboard track" in result
        assert "Phish-style keyboard notes" in result
        assert "For the drums" in result

    def test_none_like_empty(self):
        """None-ish empty whitespace returns empty."""
        assert strip_tool_echoes("   ") == ""
        assert strip_tool_echoes("\n\n\n") == ""
