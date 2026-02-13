"""Tests for SSE formatting and reasoning sanitization."""
import json
import pytest

from app.core.sse_utils import sse_event, sanitize_reasoning


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
        """Nested dicts should be JSON-serialized."""
        result = await sse_event({"type": "tool_call", "params": {"trackId": "abc-123"}})
        payload = json.loads(result[6:].strip())
        assert payload["params"]["trackId"] == "abc-123"

    @pytest.mark.anyio
    async def test_handles_empty_dict(self):
        """Empty dict is valid."""
        result = await sse_event({})
        assert result == "data: {}\n\n"


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
        """Multiple spaces should become single space."""
        text = "one   two   three"
        out = sanitize_reasoning(text)
        assert "   " not in out
        assert out.strip() == out

    def test_preserves_musical_reasoning(self):
        """Musical terms and natural language should remain."""
        text = "Use a boom bap style at 90 BPM with a minor key feel."
        out = sanitize_reasoning(text)
        assert "boom bap" in out
        assert "90" in out or "BPM" in out
        assert "minor" in out

    def test_empty_string_returns_empty(self):
        """Empty or whitespace-only returns stripped empty."""
        assert sanitize_reasoning("") == ""
        assert sanitize_reasoning("   ") == ""
