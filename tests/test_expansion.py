"""Tests for expansion module (ToolCall, dedupe)."""
import pytest

from app.core.expansion import ToolCall, dedupe_tool_calls


class TestToolCall:
    """Test ToolCall dataclass."""

    def test_to_dict(self):
        """to_dict returns name and params."""
        tc = ToolCall("stori_play", {})
        d = tc.to_dict()
        assert d["name"] == "stori_play"
        assert d["params"] == {}

    def test_to_dict_with_params(self):
        """Params are included as-is."""
        tc = ToolCall("stori_set_tempo", {"tempo": 120})
        d = tc.to_dict()
        assert d["params"]["tempo"] == 120

    def test_fingerprint_deterministic(self):
        """Same name+params produce same fingerprint."""
        tc = ToolCall("stori_add_midi_track", {"name": "Drums"})
        fp1 = tc.fingerprint()
        fp2 = tc.fingerprint()
        assert fp1 == fp2
        assert len(fp1) == 16
        assert all(c in "0123456789abcdef" for c in fp1)

    def test_fingerprint_different_for_different_params(self):
        """Different params produce different fingerprint."""
        tc1 = ToolCall("stori_play", {})
        tc2 = ToolCall("stori_play", {"foo": 1})
        assert tc1.fingerprint() != tc2.fingerprint()

    def test_fingerprint_same_for_same_params_key_order_insensitive(self):
        """JSON key order should not change fingerprint (sort_keys in impl)."""
        tc1 = ToolCall("x", {"a": 1, "b": 2})
        tc2 = ToolCall("x", {"b": 2, "a": 1})
        assert tc1.fingerprint() == tc2.fingerprint()

    def test_frozen(self):
        """ToolCall should be immutable (dataclass frozen=True)."""
        tc = ToolCall("stori_play", {"k": "v"})
        with pytest.raises(AttributeError):
            tc.name = "other"
        with pytest.raises(AttributeError):
            tc.params = {}


class TestDedupeToolCalls:
    """Test dedupe_tool_calls."""

    def test_empty_list(self):
        assert dedupe_tool_calls([]) == []

    def test_single_call_unchanged(self):
        calls = [ToolCall("stori_play", {})]
        assert dedupe_tool_calls(calls) == calls

    def test_duplicate_removed(self):
        calls = [
            ToolCall("stori_play", {}),
            ToolCall("stori_play", {}),
        ]
        out = dedupe_tool_calls(calls)
        assert len(out) == 1
        assert out[0].name == "stori_play"

    def test_duplicate_with_same_params_removed(self):
        calls = [
            ToolCall("stori_set_tempo", {"tempo": 120}),
            ToolCall("stori_set_tempo", {"tempo": 120}),
        ]
        out = dedupe_tool_calls(calls)
        assert len(out) == 1

    def test_different_calls_preserved(self):
        calls = [
            ToolCall("stori_play", {}),
            ToolCall("stori_stop", {}),
            ToolCall("stori_set_tempo", {"tempo": 90}),
        ]
        out = dedupe_tool_calls(calls)
        assert len(out) == 3
        assert [c.name for c in out] == ["stori_play", "stori_stop", "stori_set_tempo"]

    def test_first_occurrence_kept(self):
        """When duplicates exist, first occurrence is kept."""
        a = ToolCall("stori_play", {})
        b = ToolCall("stori_play", {})
        calls = [a, b]
        out = dedupe_tool_calls(calls)
        assert len(out) == 1
        assert out[0] is a
