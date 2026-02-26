"""
Unit tests for normalize_tool_arguments (Swift/client compatibility).

Ensures all numeric values are converted to strings and booleans/nested
structures are handled correctly.
"""
from __future__ import annotations

import pytest

from app.api.routes.conversations import normalize_tool_arguments


def test_empty_dict_unchanged() -> None:
    """Empty arguments returned as-is."""
    assert normalize_tool_arguments({}) == {}


def test_flat_int_and_float_to_string() -> None:
    """Integers and floats become strings; booleans unchanged."""
    out = normalize_tool_arguments({
        "gmProgram": 38,
        "tempo": 140,
        "volume": 0.8,
        "enabled": True,
    })
    assert out is not None
    assert out["gmProgram"] == "38"
    assert out["tempo"] == "140"
    assert out["volume"] == "0.8"
    assert out["enabled"] is True
    assert isinstance(out["gmProgram"], str)
    assert isinstance(out["enabled"], bool)


def test_nested_dict() -> None:
    """Nested dicts are normalized recursively."""
    out = normalize_tool_arguments({
        "a": 1,
        "inner": {"b": 2, "c": 0.5},
    })
    assert out is not None
    assert out["a"] == "1"
    inner = out["inner"]
    assert isinstance(inner, dict)
    assert inner["b"] == "2"
    assert inner["c"] == "0.5"


def test_list_of_numbers() -> None:
    """list of int/float becomes list of strings."""
    out = normalize_tool_arguments({
        "beats": [0, 4, 8, 12],
        "gains": [0.5, 1.0],
    })
    assert out is not None
    beats = out["beats"]
    assert isinstance(beats, list)
    assert beats == ["0", "4", "8", "12"]
    gains = out["gains"]
    assert isinstance(gains, list)
    assert gains == ["0.5", "1.0"]


def test_list_of_dicts() -> None:
    """list of dicts is normalized recursively."""
    out = normalize_tool_arguments({
        "items": [{"x": 1}, {"y": 2.0}],
    })
    assert out is not None
    items = out["items"]
    assert isinstance(items, list)
    item0 = items[0]
    assert isinstance(item0, dict)
    assert item0["x"] == "1"
    item1 = items[1]
    assert isinstance(item1, dict)
    assert item1["y"] == "2.0"


def test_strings_and_none_unchanged() -> None:
    """Strings and None pass through unchanged."""
    out = normalize_tool_arguments({
        "name": "Bass",
        "trackId": "track-1",
        "optional": None,
    })
    assert out is not None
    assert out["name"] == "Bass"
    assert out["trackId"] == "track-1"
    assert out["optional"] is None


def test_bool_in_list_unchanged() -> None:
    """Booleans in lists are not converted to strings."""
    out = normalize_tool_arguments({
        "flags": [True, False, 1, 0],
    })
    assert out is not None
    flags = out["flags"]
    assert isinstance(flags, list)
    assert flags[0] is True
    assert flags[1] is False
    assert flags[2] == "1"
    assert flags[3] == "0"
