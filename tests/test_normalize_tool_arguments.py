"""
Unit tests for normalize_tool_arguments (Swift/client compatibility).

Ensures all numeric values are converted to strings and booleans/nested
structures are handled correctly.
"""
import pytest

from app.api.routes.conversations import normalize_tool_arguments


def test_empty_dict_unchanged():
    """Empty arguments returned as-is."""
    assert normalize_tool_arguments({}) == {}


def test_flat_int_and_float_to_string():
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


def test_nested_dict():
    """Nested dicts are normalized recursively."""
    out = normalize_tool_arguments({
        "a": 1,
        "inner": {"b": 2, "c": 0.5},
    })
    assert out is not None
    assert out["a"] == "1"
    assert out["inner"]["b"] == "2"
    assert out["inner"]["c"] == "0.5"


def test_list_of_numbers():
    """List of int/float becomes list of strings."""
    out = normalize_tool_arguments({
        "beats": [0, 4, 8, 12],
        "gains": [0.5, 1.0],
    })
    assert out is not None
    assert out["beats"] == ["0", "4", "8", "12"]
    assert out["gains"] == ["0.5", "1.0"]


def test_list_of_dicts():
    """List of dicts is normalized recursively."""
    out = normalize_tool_arguments({
        "items": [{"x": 1}, {"y": 2.0}],
    })
    assert out is not None
    assert out["items"][0]["x"] == "1"
    assert out["items"][1]["y"] == "2.0"


def test_strings_and_none_unchanged():
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


def test_bool_in_list_unchanged():
    """Booleans in lists are not converted to strings."""
    out = normalize_tool_arguments({
        "flags": [True, False, 1, 0],
    })
    assert out is not None
    assert out["flags"][0] is True
    assert out["flags"][1] is False
    assert out["flags"][2] == "1"
    assert out["flags"][3] == "0"
