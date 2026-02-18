"""
Extended tests for app.core.tool_validation — gaps not covered by
test_tool_validation.py and test_tool_validation_fe.py.

Covers:
  1.  ValidationError / ValidationResult data model
  2.  _find_closest_match (prefix/suffix, substring, character-overlap, empty)
  3.  _validate_type  (all JSON schema types)
  4.  _validate_value_ranges (all VALUE_RANGES boundaries, notes-array range checks)
  5.  _validate_tool_specific  (notes pitch/velocity/startBeat/durationBeats)
  6.  Bus entity resolution (resolve by name, unknown bus)
  7.  Icon validation (stori_set_track_icon)
  8.  validate_tool_calls_batch helpers (all_valid, collect_errors)
"""

import pytest

from app.core.entity_registry import EntityRegistry
from app.core.tool_validation import (
    ValidationError,
    ValidationResult,
    _find_closest_match,
    _validate_type,
    _validate_value_ranges,
    _validate_tool_specific,
    validate_tool_call,
    validate_tool_calls_batch,
    all_valid,
    collect_errors,
)


# ===========================================================================
# 1. ValidationError / ValidationResult data model
# ===========================================================================

class TestValidationErrorModel:
    def test_str_contains_field_and_message(self):
        err = ValidationError(field="tempo", message="out of range", code="VALUE_OUT_OF_RANGE")
        s = str(err)
        assert "tempo" in s
        assert "out of range" in s

    def test_code_stored(self):
        err = ValidationError(field="f", message="m", code="MY_CODE")
        assert err.code == "MY_CODE"


class TestValidationResultModel:
    def _make_result(self, valid: bool, errors: list[ValidationError]) -> ValidationResult:
        return ValidationResult(
            valid=valid,
            tool_name="stori_play",
            original_params={},
            resolved_params={},
            errors=errors,
            warnings=[],
        )

    def test_error_message_empty_when_no_errors(self):
        result = self._make_result(True, [])
        assert result.error_message == ""

    def test_error_message_combines_errors(self):
        errors = [
            ValidationError(field="a", message="bad a", code="E1"),
            ValidationError(field="b", message="bad b", code="E2"),
        ]
        result = self._make_result(False, errors)
        msg = result.error_message
        assert "a: bad a" in msg
        assert "b: bad b" in msg

    def test_error_message_single_error(self):
        errors = [ValidationError(field="x", message="fail", code="E")]
        result = self._make_result(False, errors)
        assert result.error_message == "x: fail"


# ===========================================================================
# 2. _find_closest_match
# ===========================================================================

class TestFindClosestMatch:
    def test_empty_candidates_returns_none(self):
        assert _find_closest_match("bass", []) is None

    def test_exact_prefix_match(self):
        result = _find_closest_match("bass", ["Bass Track", "Drums", "Piano"])
        assert result == "Bass Track"

    def test_prefix_case_insensitive(self):
        result = _find_closest_match("BASS", ["bass guitar", "Drums"])
        assert result == "bass guitar"

    def test_substring_match(self):
        result = _find_closest_match("guitar", ["Acoustic Guitar Track", "Drums"])
        assert result == "Acoustic Guitar Track"

    def test_query_is_substring_of_candidate(self):
        result = _find_closest_match("drum", ["Drum Loop", "Bass"])
        assert result == "Drum Loop"

    def test_no_match_below_threshold_returns_none(self):
        result = _find_closest_match("xyz", ["abcde", "fghij"])
        # Jaccard similarity on completely disjoint character sets → None
        assert result is None

    def test_single_candidate_prefix(self):
        assert _find_closest_match("ba", ["bass"]) == "bass"

    def test_returns_string_or_none(self):
        result = _find_closest_match("anything", ["maybe"])
        assert result is None or isinstance(result, str)

    def test_character_overlap_high_score(self):
        """'reverb' vs 'Reverb Bus' — character overlap should match."""
        result = _find_closest_match("reverb", ["Reverb Bus", "Delay Bus"])
        assert result == "Reverb Bus"


# ===========================================================================
# 3. _validate_type
# ===========================================================================

class TestValidateType:
    def test_string_valid(self):
        assert _validate_type("name", "hello", "string") is None

    def test_string_invalid(self):
        err = _validate_type("name", 123, "string")
        assert err is not None
        assert err.code == "TYPE_MISMATCH"
        assert "string" in err.message

    def test_integer_valid(self):
        assert _validate_type("tempo", 120, "integer") is None

    def test_integer_invalid_float(self):
        err = _validate_type("tempo", 120.5, "integer")
        assert err is not None
        assert err.code == "TYPE_MISMATCH"

    def test_number_accepts_int(self):
        assert _validate_type("volume", 1, "number") is None

    def test_number_accepts_float(self):
        assert _validate_type("volume", 0.8, "number") is None

    def test_number_invalid(self):
        err = _validate_type("volume", "loud", "number")
        assert err is not None

    def test_boolean_valid(self):
        assert _validate_type("muted", True, "boolean") is None

    def test_boolean_invalid(self):
        err = _validate_type("muted", 1, "boolean")
        assert err is not None

    def test_array_valid(self):
        assert _validate_type("notes", [], "array") is None

    def test_array_invalid(self):
        err = _validate_type("notes", {}, "array")
        assert err is not None

    def test_object_valid(self):
        assert _validate_type("params", {}, "object") is None

    def test_object_invalid(self):
        err = _validate_type("params", [], "object")
        assert err is not None

    def test_unknown_type_passes(self):
        """Unknown JSON schema types are not validated — must not crash."""
        assert _validate_type("x", "anything", "xyzzy") is None

    def test_returns_none_on_match(self):
        result = _validate_type("s", "hello", "string")
        assert result is None

    def test_returns_validation_error_on_mismatch(self):
        result = _validate_type("s", 99, "string")
        assert isinstance(result, ValidationError)


# ===========================================================================
# 4. _validate_value_ranges — boundary conditions
# ===========================================================================

class TestValidateValueRanges:
    """_validate_value_ranges catches out-of-range scalars and per-note ranges."""

    def test_tempo_in_range(self):
        assert _validate_value_ranges({"tempo": 120}) == []

    def test_tempo_below_min(self):
        errors = _validate_value_ranges({"tempo": 29})
        assert any(e.code == "VALUE_OUT_OF_RANGE" for e in errors)

    def test_tempo_above_max(self):
        errors = _validate_value_ranges({"tempo": 301})
        assert any(e.code == "VALUE_OUT_OF_RANGE" for e in errors)

    def test_bars_in_range(self):
        assert _validate_value_ranges({"bars": 8}) == []

    def test_bars_zero(self):
        errors = _validate_value_ranges({"bars": 0})
        assert any(e.code == "VALUE_OUT_OF_RANGE" for e in errors)

    def test_bars_max(self):
        assert _validate_value_ranges({"bars": 64}) == []

    def test_bars_above_max(self):
        errors = _validate_value_ranges({"bars": 65})
        assert any(e.code == "VALUE_OUT_OF_RANGE" for e in errors)

    def test_velocity_in_range(self):
        assert _validate_value_ranges({"velocity": 100}) == []

    def test_velocity_zero(self):
        errors = _validate_value_ranges({"velocity": 0})
        assert any(e.code == "VALUE_OUT_OF_RANGE" for e in errors)

    def test_velocity_above_max(self):
        errors = _validate_value_ranges({"velocity": 128})
        assert any(e.code == "VALUE_OUT_OF_RANGE" for e in errors)

    def test_pitch_in_range(self):
        assert _validate_value_ranges({"pitch": 60}) == []

    def test_pitch_below_min(self):
        errors = _validate_value_ranges({"pitch": -1})
        assert any(e.code == "VALUE_OUT_OF_RANGE" for e in errors)

    def test_pitch_above_max(self):
        errors = _validate_value_ranges({"pitch": 128})
        assert any(e.code == "VALUE_OUT_OF_RANGE" for e in errors)

    def test_notes_array_pitch_out_of_range(self):
        notes = [{"pitch": 200, "velocity": 100}]
        errors = _validate_value_ranges({"notes": notes})
        assert any("notes[0].pitch" in e.field for e in errors)

    def test_notes_array_velocity_out_of_range(self):
        notes = [{"pitch": 60, "velocity": 200}]
        errors = _validate_value_ranges({"notes": notes})
        assert any("notes[0].velocity" in e.field for e in errors)

    def test_notes_array_valid(self):
        notes = [{"pitch": 60, "velocity": 100}]
        assert _validate_value_ranges({"notes": notes}) == []

    def test_notes_array_not_list_no_crash(self):
        """If notes is not a list, range validation skips it."""
        _validate_value_ranges({"notes": "not a list"})  # must not raise

    def test_start_beat_zero_ok(self):
        assert _validate_value_ranges({"startBeat": 0}) == []

    def test_duration_beats_min_boundary(self):
        assert _validate_value_ranges({"durationBeats": 0.01}) == []

    def test_duration_beats_below_min(self):
        errors = _validate_value_ranges({"durationBeats": 0.001})
        assert any(e.code == "VALUE_OUT_OF_RANGE" for e in errors)

    def test_empty_params_no_errors(self):
        assert _validate_value_ranges({}) == []

    def test_non_numeric_value_skipped(self):
        """Non-numeric values in range fields must not crash."""
        _validate_value_ranges({"tempo": "fast"})  # must not raise


# ===========================================================================
# 5. _validate_tool_specific — note array content
# ===========================================================================

class TestValidateToolSpecificNotes:
    """stori_add_notes note-level validation."""

    def _valid_note(self, **overrides) -> dict:
        note = {"pitch": 60, "startBeat": 0, "durationBeats": 0.5, "velocity": 100}
        note.update(overrides)
        return note

    def test_valid_notes_no_errors(self):
        errors = _validate_tool_specific(
            "stori_add_notes",
            {"notes": [self._valid_note()]},
        )
        assert errors == []

    def test_pitch_out_of_range(self):
        errors = _validate_tool_specific(
            "stori_add_notes",
            {"notes": [self._valid_note(pitch=200)]},
        )
        assert any("pitch" in e.field for e in errors)
        assert any(e.code == "INVALID_PITCH" for e in errors)

    def test_pitch_negative(self):
        errors = _validate_tool_specific(
            "stori_add_notes",
            {"notes": [self._valid_note(pitch=-1)]},
        )
        assert any("pitch" in e.field for e in errors)

    def test_velocity_zero_rejected(self):
        errors = _validate_tool_specific(
            "stori_add_notes",
            {"notes": [self._valid_note(velocity=0)]},
        )
        assert any("velocity" in e.field for e in errors)
        assert any(e.code == "INVALID_VELOCITY" for e in errors)

    def test_velocity_128_rejected(self):
        errors = _validate_tool_specific(
            "stori_add_notes",
            {"notes": [self._valid_note(velocity=128)]},
        )
        assert any("velocity" in e.field for e in errors)

    def test_start_beat_negative(self):
        errors = _validate_tool_specific(
            "stori_add_notes",
            {"notes": [self._valid_note(startBeat=-0.5)]},
        )
        assert any("startBeat" in e.field for e in errors)
        assert any(e.code == "INVALID_START" for e in errors)

    def test_duration_too_short(self):
        errors = _validate_tool_specific(
            "stori_add_notes",
            {"notes": [self._valid_note(durationBeats=0.001)]},
        )
        assert any("durationBeats" in e.field for e in errors)
        assert any(e.code == "INVALID_DURATION" for e in errors)

    def test_duration_too_long(self):
        errors = _validate_tool_specific(
            "stori_add_notes",
            {"notes": [self._valid_note(durationBeats=9999)]},
        )
        assert any("durationBeats" in e.field for e in errors)

    def test_multiple_notes_errors_indexed(self):
        """Error fields reference the correct note index."""
        errors = _validate_tool_specific(
            "stori_add_notes",
            {"notes": [self._valid_note(), self._valid_note(pitch=999)]},
        )
        assert any("notes[1]" in e.field for e in errors)
        assert not any("notes[0]" in e.field for e in errors)

    def test_notes_not_a_list(self):
        errors = _validate_tool_specific("stori_add_notes", {"notes": "bad"})
        assert any(e.code == "TYPE_MISMATCH" for e in errors)

    def test_empty_notes_invalid(self):
        errors = _validate_tool_specific("stori_add_notes", {"notes": []})
        assert any(e.code == "INVALID_VALUE" for e in errors)


# ===========================================================================
# 6. Bus entity resolution
# ===========================================================================

class TestBusEntityResolution:
    def test_valid_bus_id_passes(self):
        registry = EntityRegistry()
        bus_id = registry.create_bus("Reverb")

        result = validate_tool_call(
            "stori_add_send",
            {"trackId": "any", "busId": bus_id},
            {"stori_add_send"},
            registry=registry,
        )
        # trackId might fail (not in registry), but busId should NOT add a bus error
        bus_errors = [e for e in result.errors if e.field == "busId"]
        assert bus_errors == []

    def test_unknown_bus_id_fails(self):
        registry = EntityRegistry()
        result = validate_tool_call(
            "stori_add_send",
            {"trackId": "any", "busId": "fake-bus-id"},
            {"stori_add_send"},
            registry=registry,
        )
        assert any(e.field == "busId" for e in result.errors)
        assert any(e.code == "ENTITY_NOT_FOUND" for e in result.errors)

    def test_bus_entity_creating_tool_skips_bus_id(self):
        """stori_ensure_bus should not validate its own busId."""
        registry = EntityRegistry()
        result = validate_tool_call(
            "stori_ensure_bus",
            {"name": "Reverb", "busId": "fake-bus-id"},
            {"stori_ensure_bus"},
            registry=registry,
        )
        bus_errors = [e for e in result.errors if e.field == "busId"]
        assert bus_errors == []

    def test_bus_name_resolves_to_id(self):
        """If busId is a name string matching a bus name, it should resolve."""
        registry = EntityRegistry()
        bus_id = registry.create_bus("Reverb")

        # resolve_bus may look up by name
        resolved_id = registry.resolve_bus("Reverb")
        assert resolved_id == bus_id


# ===========================================================================
# 7. Icon validation (stori_set_track_icon)
# ===========================================================================

class TestIconValidation:
    def test_valid_icon_passes(self):
        registry = EntityRegistry()
        track_id = registry.create_track("Piano")
        result = validate_tool_call(
            "stori_set_track_icon",
            {"trackId": track_id, "icon": "pianokeys"},
            {"stori_set_track_icon"},
            registry=registry,
        )
        icon_errors = [e for e in result.errors if e.field == "icon"]
        assert icon_errors == []

    def test_invalid_icon_fails(self):
        registry = EntityRegistry()
        track_id = registry.create_track("Piano")
        result = validate_tool_call(
            "stori_set_track_icon",
            {"trackId": track_id, "icon": "not-a-real-sf-symbol"},
            {"stori_set_track_icon"},
            registry=registry,
        )
        assert any(e.field == "icon" for e in result.errors)
        assert any(e.code == "INVALID_ICON" for e in result.errors)

    @pytest.mark.parametrize("icon", [
        "guitars.fill", "pianokeys", "music.note", "waveform",
        "headphones", "sparkles", "metronome",
    ])
    def test_curated_icons_pass(self, icon):
        errors = _validate_tool_specific("stori_set_track_icon", {"icon": icon})
        icon_errors = [e for e in errors if e.field == "icon"]
        assert icon_errors == [], f"Expected '{icon}' to be valid"

    def test_empty_icon_no_error(self):
        """Empty string icon skips validation (optional field)."""
        errors = _validate_tool_specific("stori_set_track_icon", {"icon": ""})
        assert errors == []


# ===========================================================================
# 8. all_valid / collect_errors helpers
# ===========================================================================

class TestBatchHelpers:
    def test_all_valid_true_when_empty(self):
        assert all_valid([]) is True

    def test_all_valid_true(self):
        results = validate_tool_calls_batch(
            [("stori_play", {}), ("stori_stop", {})],
            allowed_tools={"stori_play", "stori_stop"},
        )
        assert all_valid(results)

    def test_all_valid_false_when_any_invalid(self):
        results = validate_tool_calls_batch(
            [("stori_play", {}), ("stori_bad", {})],
            allowed_tools={"stori_play"},
        )
        assert not all_valid(results)

    def test_collect_errors_empty_when_all_valid(self):
        results = validate_tool_calls_batch(
            [("stori_play", {})],
            allowed_tools={"stori_play"},
        )
        assert collect_errors(results) == []

    def test_collect_errors_includes_tool_name(self):
        results = validate_tool_calls_batch(
            [("stori_bad_tool", {})],
            allowed_tools={"stori_play"},
        )
        errors = collect_errors(results)
        assert len(errors) == 1
        assert "stori_bad_tool" in errors[0]

    def test_collect_errors_multiple_bad_tools(self):
        results = validate_tool_calls_batch(
            [("bad_a", {}), ("bad_b", {})],
            allowed_tools=set(),
        )
        errors = collect_errors(results)
        assert len(errors) == 2
