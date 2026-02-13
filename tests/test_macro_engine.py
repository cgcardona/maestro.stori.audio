"""Tests for macro engine (recipe expansion)."""
import pytest

from app.core.macro_engine import expand_macro, MACROS, macro_make_darker
from app.core.expansion import ToolCall


class TestExpandMacro:
    """Test expand_macro."""

    def test_unknown_macro_returns_empty_list(self):
        """Unknown macro id returns no tool calls."""
        assert expand_macro("unknown.macro", {}) == []
        assert expand_macro("mix.louder", {"trackId": "t1"}) == []

    def test_mix_darker_with_track_id(self):
        """mix.darker with trackId expands to EQ + distortion."""
        ctx = {"trackId": "track-123"}
        result = expand_macro("mix.darker", ctx)
        assert len(result) == 2
        assert all(isinstance(tc, ToolCall) for tc in result)
        names = [tc.name for tc in result]
        assert "stori_add_insert_effect" in names
        assert result[0].params.get("type") in ("eq", "distortion")
        assert result[1].params.get("type") in ("eq", "distortion")
        assert result[0].params.get("trackId") == "track-123"
        assert result[1].params.get("trackId") == "track-123"

    def test_mix_darker_without_track_id_returns_empty(self):
        """mix.darker without trackId returns empty (no target)."""
        assert expand_macro("mix.darker", {}) == []
        assert expand_macro("mix.darker", {"regionId": "r1"}) == []


class TestMacroMakeDarker:
    """Test macro_make_darker directly."""

    def test_returns_eq_and_distortion_calls(self):
        """Recipe should include EQ and distortion effects."""
        out = macro_make_darker({"trackId": "t1"})
        assert len(out) == 2
        types = {tc.params.get("type") for tc in out}
        assert "eq" in types
        assert "distortion" in types

    def test_no_track_id_returns_empty(self):
        assert macro_make_darker({}) == []
        assert macro_make_darker({"name": "Drums"}) == []


class TestMacrosRegistry:
    """Test MACROS registry is consistent."""

    def test_mix_darker_registered(self):
        """mix.darker should be in MACROS."""
        assert "mix.darker" in MACROS
        m = MACROS["mix.darker"]
        assert m.id == "mix.darker"
        assert "darker" in m.description.lower()
        assert callable(m.expand)
