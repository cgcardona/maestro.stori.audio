"""
Tests for app.core.expansion — ToolCall and dedupe_tool_calls.

This module is the execution primitive used by the planner and executor.
Zero prior coverage.
"""
from __future__ import annotations

import json

import pytest

from app.core.expansion import ToolCall, dedupe_tool_calls


# ===========================================================================
# ToolCall dataclass
# ===========================================================================

class TestToolCallToDict:
    """ToolCall.to_dict serialises name and params."""

    def test_empty_params(self) -> None:

        tc = ToolCall(name="stori_play", params={})
        assert tc.to_dict() == {"name": "stori_play", "params": {}}

    def test_with_params(self) -> None:

        tc = ToolCall(name="stori_set_tempo", params={"tempo": 120})
        d = tc.to_dict()
        assert d["name"] == "stori_set_tempo"
        assert d["params"] == {"tempo": 120}

    def test_nested_params(self) -> None:

        tc = ToolCall(
            name="stori_add_notes",
            params={"regionId": "r1", "notes": [{"pitch": 60, "startBeat": 0}]},
        )
        d = tc.to_dict()
        params = d["params"]
        assert isinstance(params, dict)
        notes = params["notes"]
        assert isinstance(notes, list)
        first = notes[0]
        assert isinstance(first, dict)
        assert first["pitch"] == 60

    def test_returns_dict(self) -> None:

        tc = ToolCall(name="stori_stop", params={})
        assert isinstance(tc.to_dict(), dict)


class TestToolCallFingerprint:
    """ToolCall.fingerprint() is a deterministic 16-char hex digest."""

    def test_returns_string(self) -> None:

        tc = ToolCall(name="stori_play", params={})
        assert isinstance(tc.fingerprint(), str)

    def test_length_is_16(self) -> None:

        tc = ToolCall(name="stori_play", params={})
        assert len(tc.fingerprint()) == 16

    def test_deterministic(self) -> None:

        tc1 = ToolCall(name="stori_set_tempo", params={"tempo": 128})
        tc2 = ToolCall(name="stori_set_tempo", params={"tempo": 128})
        assert tc1.fingerprint() == tc2.fingerprint()

    def test_different_name_different_fingerprint(self) -> None:

        tc1 = ToolCall(name="stori_play", params={})
        tc2 = ToolCall(name="stori_stop", params={})
        assert tc1.fingerprint() != tc2.fingerprint()

    def test_different_params_different_fingerprint(self) -> None:

        tc1 = ToolCall(name="stori_set_tempo", params={"tempo": 120})
        tc2 = ToolCall(name="stori_set_tempo", params={"tempo": 140})
        assert tc1.fingerprint() != tc2.fingerprint()

    def test_param_order_does_not_matter(self) -> None:

        """Fingerprint is key-order-stable (sort_keys=True)."""
        tc1 = ToolCall(name="stori_add_notes", params={"a": 1, "b": 2})
        tc2 = ToolCall(name="stori_add_notes", params={"b": 2, "a": 1})
        assert tc1.fingerprint() == tc2.fingerprint()

    def test_hex_chars_only(self) -> None:

        tc = ToolCall(name="stori_play", params={"x": "value"})
        assert all(c in "0123456789abcdef" for c in tc.fingerprint())


class TestToolCallFrozen:
    """ToolCall is a frozen dataclass — immutable after creation."""

    def test_cannot_reassign_name(self) -> None:

        tc = ToolCall(name="stori_play", params={})
        with pytest.raises((TypeError, AttributeError)):
            setattr(tc, "name", "stori_stop")

    def test_equality(self) -> None:

        tc1 = ToolCall(name="stori_play", params={"x": 1})
        tc2 = ToolCall(name="stori_play", params={"x": 1})
        assert tc1 == tc2

    def test_inequality_by_params(self) -> None:

        tc1 = ToolCall(name="stori_play", params={"x": 1})
        tc2 = ToolCall(name="stori_play", params={"x": 2})
        assert tc1 != tc2


# ===========================================================================
# dedupe_tool_calls
# ===========================================================================

class TestDedupeToolCalls:
    """dedupe_tool_calls removes exact duplicates, preserves insertion order."""

    def test_empty_list(self) -> None:

        assert dedupe_tool_calls([]) == []

    def test_single_call_preserved(self) -> None:

        tc = ToolCall(name="stori_play", params={})
        assert dedupe_tool_calls([tc]) == [tc]

    def test_all_unique_preserved(self) -> None:

        calls = [
            ToolCall(name="stori_play", params={}),
            ToolCall(name="stori_stop", params={}),
            ToolCall(name="stori_set_tempo", params={"tempo": 120}),
        ]
        result = dedupe_tool_calls(calls)
        assert len(result) == 3

    def test_duplicate_removed(self) -> None:

        tc = ToolCall(name="stori_play", params={})
        result = dedupe_tool_calls([tc, tc])
        assert len(result) == 1
        assert result[0] == tc

    def test_multiple_duplicates_only_first_kept(self) -> None:

        tc = ToolCall(name="stori_set_tempo", params={"tempo": 120})
        result = dedupe_tool_calls([tc, tc, tc])
        assert len(result) == 1

    def test_preserves_insertion_order(self) -> None:

        tc_a = ToolCall(name="stori_play", params={})
        tc_b = ToolCall(name="stori_stop", params={})
        tc_c = ToolCall(name="stori_play", params={})  # duplicate of a
        result = dedupe_tool_calls([tc_a, tc_b, tc_c])
        assert result[0] == tc_a
        assert result[1] == tc_b
        assert len(result) == 2

    def test_different_params_not_deduped(self) -> None:

        tc1 = ToolCall(name="stori_set_tempo", params={"tempo": 120})
        tc2 = ToolCall(name="stori_set_tempo", params={"tempo": 140})
        result = dedupe_tool_calls([tc1, tc2])
        assert len(result) == 2

    def test_large_list_of_duplicates(self) -> None:

        tc = ToolCall(name="stori_play", params={})
        result = dedupe_tool_calls([tc] * 100)
        assert len(result) == 1

    def test_mixed_duplicates_and_uniques(self) -> None:

        tc_play = ToolCall(name="stori_play", params={})
        tc_stop = ToolCall(name="stori_stop", params={})
        tc_120 = ToolCall(name="stori_set_tempo", params={"tempo": 120})
        tc_140 = ToolCall(name="stori_set_tempo", params={"tempo": 140})

        calls = [tc_play, tc_stop, tc_play, tc_120, tc_140, tc_stop]
        result = dedupe_tool_calls(calls)
        assert len(result) == 4
        assert result[0] == tc_play
        assert result[1] == tc_stop
