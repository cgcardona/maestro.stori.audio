"""
Tests for $N.field variable reference resolution in maestro_handlers.

_resolve_variable_refs lets the LLM reference the ID returned by an earlier
tool call in the same batch using "$N.fieldName" syntax, where N is the
0-based index into prior_results.

Coverage:
  1. Basic substitution — $0.trackId, $1.regionId
  2. Index boundary conditions
  3. Non-variable values are passed through unchanged
  4. Missing field in result → original value preserved
  5. Pattern matching — only "$N.field" strings, not partial matches
  6. Multi-field params — some variable, some literal
  7. Chaining — result of resolved call used by next call
  8. _VAR_REF_RE pattern integrity
"""

import pytest
import re

from app.core.maestro_helpers import _resolve_variable_refs, _VAR_REF_RE


# ===========================================================================
# 1. Basic substitution
# ===========================================================================

class TestBasicSubstitution:
    """$N.field is replaced by the value at that index in prior_results."""

    def test_zero_index_track_id(self):
        prior = [{"trackId": "track-abc-123"}]
        result = _resolve_variable_refs({"trackId": "$0.trackId"}, prior)
        assert result["trackId"] == "track-abc-123"

    def test_first_index_region_id(self):
        prior = [
            {"trackId": "t1"},
            {"regionId": "r1", "trackId": "t1"},
        ]
        result = _resolve_variable_refs({"regionId": "$1.regionId"}, prior)
        assert result["regionId"] == "r1"

    def test_bus_id_substitution(self):
        prior = [{"busId": "bus-xyz"}]
        result = _resolve_variable_refs({"busId": "$0.busId"}, prior)
        assert result["busId"] == "bus-xyz"

    def test_multiple_refs_in_same_params(self):
        prior = [
            {"trackId": "t1"},
            {"regionId": "r1"},
        ]
        params = {"trackId": "$0.trackId", "regionId": "$1.regionId"}
        result = _resolve_variable_refs(params, prior)
        assert result["trackId"] == "t1"
        assert result["regionId"] == "r1"

    def test_second_result_used(self):
        prior = [
            {"trackId": "t-first"},
            {"trackId": "t-second"},
        ]
        result = _resolve_variable_refs({"trackId": "$1.trackId"}, prior)
        assert result["trackId"] == "t-second"


# ===========================================================================
# 2. Index boundary conditions
# ===========================================================================

class TestIndexBoundaryConditions:
    """Out-of-range indices leave the original value intact."""

    def test_index_out_of_range_preserves_value(self):
        prior = [{"trackId": "t1"}]
        result = _resolve_variable_refs({"trackId": "$5.trackId"}, prior)
        assert result["trackId"] == "$5.trackId"  # unchanged

    def test_index_exactly_at_boundary_preserved(self):
        prior = [{"trackId": "t1"}]  # len=1, valid index=0
        result = _resolve_variable_refs({"trackId": "$1.trackId"}, prior)
        assert result["trackId"] == "$1.trackId"

    def test_empty_prior_results_preserves_all(self):
        params = {"trackId": "$0.trackId", "regionId": "$1.regionId"}
        result = _resolve_variable_refs(params, [])
        assert result["trackId"] == "$0.trackId"
        assert result["regionId"] == "$1.regionId"

    def test_negative_index_not_matched_by_regex(self):
        """$-1.field should not match the regex and be passed through."""
        prior = [{"trackId": "t1"}]
        result = _resolve_variable_refs({"trackId": "$-1.trackId"}, prior)
        assert result["trackId"] == "$-1.trackId"


# ===========================================================================
# 3. Non-variable values passed through unchanged
# ===========================================================================

class TestNonVariablePassthrough:
    """Literal values are never modified."""

    def test_literal_string_unchanged(self):
        result = _resolve_variable_refs({"name": "Drums"}, [{"trackId": "t1"}])
        assert result["name"] == "Drums"

    def test_integer_param_unchanged(self):
        result = _resolve_variable_refs({"startBeat": 16}, [{"trackId": "t1"}])
        assert result["startBeat"] == 16

    def test_float_param_unchanged(self):
        result = _resolve_variable_refs({"volume": 0.8}, [{"trackId": "t1"}])
        assert result["volume"] == 0.8

    def test_bool_param_unchanged(self):
        result = _resolve_variable_refs({"muted": True}, [])
        assert result["muted"] is True

    def test_none_value_unchanged(self):
        result = _resolve_variable_refs({"key": None}, [{"trackId": "t1"}])
        assert result["key"] is None

    def test_list_value_unchanged(self):
        notes = [{"pitch": 60, "startBeat": 0, "durationBeats": 1, "velocity": 100}]
        result = _resolve_variable_refs({"notes": notes}, [{"regionId": "r1"}])
        assert result["notes"] == notes

    def test_dict_value_unchanged(self):
        result = _resolve_variable_refs({"constraints": {"bars": 8}}, [])
        assert result["constraints"] == {"bars": 8}

    def test_partial_dollar_sign_not_matched(self):
        """A dollar sign not matching $N.field is left alone."""
        result = _resolve_variable_refs({"budget": "$5.00"}, [{"trackId": "t1"}])
        assert result["budget"] == "$5.00"


# ===========================================================================
# 4. Missing field in result → original value preserved
# ===========================================================================

class TestMissingFieldInResult:
    """If the referenced field doesn't exist in the prior result, keep original."""

    def test_missing_field_preserves_ref_string(self):
        prior = [{"trackId": "t1"}]  # no "regionId"
        result = _resolve_variable_refs({"regionId": "$0.regionId"}, prior)
        assert result["regionId"] == "$0.regionId"

    def test_typo_in_field_name_preserves_ref(self):
        prior = [{"trackId": "t1"}]
        result = _resolve_variable_refs({"trackId": "$0.trackid"}, prior)  # lowercase 'i'
        assert result["trackId"] == "$0.trackid"

    def test_empty_dict_in_prior_preserves_ref(self):
        prior = [{}]
        result = _resolve_variable_refs({"trackId": "$0.trackId"}, prior)
        assert result["trackId"] == "$0.trackId"


# ===========================================================================
# 5. Pattern matching — regex correctness
# ===========================================================================

class TestVariableRefPattern:
    """_VAR_REF_RE must match exactly $N.field and nothing else."""

    @pytest.mark.parametrize("value,should_match", [
        ("$0.trackId",       True),
        ("$1.regionId",      True),
        ("$99.busId",        True),
        ("$0.newRegionId",   True),
        ("$0.trackId extra", False),   # trailing content
        ("prefix $0.trackId", False),  # leading content
        ("$0",               False),   # no field
        ("0.trackId",        False),   # missing $
        ("$.trackId",        False),   # missing index
        ("$-1.trackId",      False),   # negative index
        ("",                 False),
        ("$0.",              False),   # empty field
    ])
    def test_pattern(self, value, should_match):
        match = _VAR_REF_RE.match(value)
        if should_match:
            assert match is not None, f"Expected '{value}' to match"
        else:
            assert match is None, f"Expected '{value}' NOT to match"

    def test_match_extracts_index(self):
        m = _VAR_REF_RE.match("$3.regionId")
        assert m is not None
        assert int(m.group(1)) == 3

    def test_match_extracts_field(self):
        m = _VAR_REF_RE.match("$0.newRegionId")
        assert m is not None
        assert m.group(2) == "newRegionId"


# ===========================================================================
# 6. Multi-field params — mixed variable and literal
# ===========================================================================

class TestMixedParams:
    """Params with both $N.field refs and literals are resolved correctly."""

    def test_mixed_params_only_refs_resolved(self):
        prior = [{"trackId": "t1"}, {"regionId": "r1", "trackId": "t1"}]
        params = {
            "trackId":      "$0.trackId",    # → "t1"
            "regionId":     "$1.regionId",   # → "r1"
            "startBeat":    0,               # literal int
            "durationBeats": 16,             # literal int
            "name":         "My Pattern",    # literal string
        }
        result = _resolve_variable_refs(params, prior)
        assert result["trackId"] == "t1"
        assert result["regionId"] == "r1"
        assert result["startBeat"] == 0
        assert result["durationBeats"] == 16
        assert result["name"] == "My Pattern"

    def test_resolved_result_is_new_dict(self):
        """The original params dict is not mutated."""
        prior = [{"trackId": "t1"}]
        params = {"trackId": "$0.trackId"}
        original_params = params.copy()
        _resolve_variable_refs(params, prior)
        assert params == original_params


# ===========================================================================
# 7. Realistic end-to-end sequence
# ===========================================================================

class TestRealisticSequence:
    """
    Simulates a real LLM tool call batch:
      call 0: stori_add_midi_track  → {trackId: "t-uuid"}
      call 1: stori_add_midi_region → {regionId: "r-uuid", trackId: "t-uuid"}
      call 2: stori_add_notes       → regionId="$1.regionId" resolved from call 1
    """

    def test_add_notes_refs_region_from_prior_call(self):
        prior_results = [
            {"trackId": "t-uuid-001"},                              # call 0 result
            {"regionId": "r-uuid-002", "trackId": "t-uuid-001"},   # call 1 result
        ]
        add_notes_params = {
            "regionId": "$1.regionId",
            "notes": [{"pitch": 36, "startBeat": 0, "durationBeats": 1, "velocity": 100}],
        }
        resolved = _resolve_variable_refs(add_notes_params, prior_results)
        assert resolved["regionId"] == "r-uuid-002"
        assert resolved["notes"] == add_notes_params["notes"]

    def test_set_volume_refs_track_from_first_call(self):
        prior_results = [{"trackId": "t-uuid-001"}]
        set_vol_params = {"trackId": "$0.trackId", "volumeDb": -6}
        resolved = _resolve_variable_refs(set_vol_params, prior_results)
        assert resolved["trackId"] == "t-uuid-001"
        assert resolved["volumeDb"] == -6

    def test_accumulating_results_three_calls(self):
        """Third call can reference either of the first two results."""
        prior_results = [
            {"trackId": "t1"},
            {"regionId": "r1", "trackId": "t1"},
        ]
        # Call 2 refs call 1's regionId AND call 0's trackId
        params = {"regionId": "$1.regionId", "trackId": "$0.trackId"}
        resolved = _resolve_variable_refs(params, prior_results)
        assert resolved["regionId"] == "r1"
        assert resolved["trackId"] == "t1"
