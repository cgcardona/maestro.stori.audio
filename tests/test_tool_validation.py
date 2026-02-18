"""
Tests for tool argument validation.

Tool validation ensures:
1. Tools are in the allowlist
2. Arguments match schemas
3. Entity references are valid
4. Values are in expected ranges
"""

import pytest
from app.core.entity_registry import EntityRegistry
from app.core.tool_validation import (
    validate_tool_call,
    validate_tool_call_simple,
    ValidationError,
    ValidationResult,
    validate_tool_calls_batch,
    all_valid,
    collect_errors,
)


class TestAllowlistValidation:
    """Test tool allowlist validation."""
    
    def test_allowed_tool_passes(self):
        """Should pass for allowed tool."""
        result = validate_tool_call(
            tool_name="stori_play",
            params={},
            allowed_tools={"stori_play", "stori_stop"},
        )
        
        assert result.valid
        assert len(result.errors) == 0
    
    def test_disallowed_tool_fails(self):
        """Should fail for tool not in allowlist."""
        result = validate_tool_call(
            tool_name="stori_add_midi_track",
            params={"name": "Drums"},
            allowed_tools={"stori_play", "stori_stop"},
        )
        
        assert not result.valid
        assert any("not allowed" in str(e).lower() for e in result.errors)
    
    def test_generator_tool_blocked_by_allowlist(self):
        """Allowlist is the single source of truth; generator not in allowlist is rejected."""
        result = validate_tool_call(
            tool_name="stori_generate_midi",
            params={"role": "drums"},
            allowed_tools={"stori_set_tempo"},  # Compose pass: no generators in allowlist
        )
        assert not result.valid
        assert any(e.code == "TOOL_NOT_ALLOWED" for e in result.errors)


class TestSchemaValidation:
    """Test tool schema validation."""
    
    def test_missing_required_field(self):
        """Should fail when required field is missing."""
        result = validate_tool_call(
            tool_name="stori_set_tempo",
            params={},  # Missing 'tempo'
            allowed_tools={"stori_set_tempo"},
        )
        
        assert not result.valid
        assert any("tempo" in str(e).lower() for e in result.errors)
    
    def test_valid_params(self):
        """Should pass with valid parameters."""
        result = validate_tool_call(
            tool_name="stori_set_tempo",
            params={"tempo": 120},
            allowed_tools={"stori_set_tempo"},
        )
        
        assert result.valid


class TestEntityResolution:
    """Test entity reference resolution."""
    
    def test_resolve_track_name_to_id(self):
        """Should resolve trackName to trackId."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")
        
        result = validate_tool_call(
            tool_name="stori_set_track_volume",
            params={"trackName": "Drums", "volume": 0.8},
            allowed_tools={"stori_set_track_volume"},
            registry=registry,
        )
        
        assert result.valid
        assert result.resolved_params["trackId"] == track_id
    
    def test_unknown_track_fails(self):
        """Should fail for unknown track reference."""
        registry = EntityRegistry()
        
        result = validate_tool_call(
            tool_name="stori_set_track_volume",
            params={"trackName": "NonExistent", "volume": 0.8},
            allowed_tools={"stori_set_track_volume"},
            registry=registry,
        )
        
        assert not result.valid
        assert any("not found" in str(e).lower() for e in result.errors)
    
    def test_validate_track_id_exists(self):
        """Should validate that trackId exists."""
        registry = EntityRegistry()
        registry.create_track("Drums")
        
        result = validate_tool_call(
            tool_name="stori_set_track_volume",
            params={"trackId": "fake-id-123", "volume": 0.8},
            allowed_tools={"stori_set_track_volume"},
            registry=registry,
        )
        
        assert not result.valid
        assert any("not found" in str(e).lower() for e in result.errors)
    
    def test_valid_track_id_passes(self):
        """Should pass for valid trackId."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")
        
        result = validate_tool_call(
            tool_name="stori_set_track_volume",
            params={"trackId": track_id, "volume": 0.8},
            allowed_tools={"stori_set_track_volume"},
            registry=registry,
        )
        
        assert result.valid
    
    def test_validate_region_id(self):
        """Should validate regionId exists."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")
        region_id = registry.create_region("Pattern", track_id)
        
        # Valid region
        result = validate_tool_call(
            tool_name="stori_add_notes",
            params={"regionId": region_id, "notes": [{"pitch": 36, "startBeat": 0, "durationBeats": 0.5, "velocity": 100}]},
            allowed_tools={"stori_add_notes"},
            registry=registry,
        )
        
        assert result.valid
        
        # Invalid region
        result = validate_tool_call(
            tool_name="stori_add_notes",
            params={"regionId": "fake-region", "notes": []},
            allowed_tools={"stori_add_notes"},
            registry=registry,
        )
        
        assert not result.valid

    def test_track_id_used_as_region_id_gives_targeted_error(self):
        """When LLM passes a trackId as regionId, error message identifies the mistake."""
        registry = EntityRegistry()
        track_id = registry.create_track("Bass")
        region_id = registry.create_region("Bass Line", track_id)

        result = validate_tool_call(
            tool_name="stori_add_notes",
            params={"regionId": track_id, "notes": []},
            allowed_tools={"stori_add_notes"},
            registry=registry,
        )

        assert not result.valid
        msg = result.errors[0].message
        assert "trackId" in msg
        assert "regionId" in msg
        assert f"Bass Line (regionId: {region_id})" in msg

    def test_region_not_found_includes_id_in_suggestions(self):
        """Region not-found suggestions include regionId so the LLM can self-correct."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")
        region_id = registry.create_region("Beat", track_id)

        result = validate_tool_call(
            tool_name="stori_add_notes",
            params={"regionId": "wrong-id", "notes": []},
            allowed_tools={"stori_add_notes"},
            registry=registry,
        )

        assert not result.valid
        msg = result.errors[0].message
        assert region_id in msg

    def test_entity_creating_tool_skips_primary_id_validation(self):
        """stori_add_midi_track should not reject hallucinated trackId."""
        registry = EntityRegistry()
        # LLM hallucinated a trackId for a CREATE operation — should not fail
        result = validate_tool_call(
            tool_name="stori_add_midi_track",
            params={"name": "Drums", "trackId": "fake-hallucinated-uuid"},
            allowed_tools={"stori_add_midi_track"},
            registry=registry,
        )
        # Validation passes — server will replace the ID later
        assert result.valid

    def test_entity_creating_region_skips_regionid_but_validates_trackid(self):
        """stori_add_midi_region skips regionId validation but still validates trackId."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")

        # Hallucinated regionId with valid trackId → should pass
        result = validate_tool_call(
            tool_name="stori_add_midi_region",
            params={
                "trackId": track_id,
                "regionId": "fake-region-id",
                "name": "Pattern",
                "startBeat": 0,
                "durationBeats": 16,
            },
            allowed_tools={"stori_add_midi_region"},
            registry=registry,
        )
        assert result.valid

        # Hallucinated regionId with INVALID trackId → should fail on trackId
        result = validate_tool_call(
            tool_name="stori_add_midi_region",
            params={
                "trackId": "nonexistent-track",
                "regionId": "fake-region-id",
                "name": "Pattern",
                "startBeat": 0,
                "durationBeats": 16,
            },
            allowed_tools={"stori_add_midi_region"},
            registry=registry,
        )
        assert not result.valid
        assert any("trackId" in str(e) for e in result.errors)


class TestValueRangeValidation:
    """Test value range validation."""
    
    def test_volume_in_range(self):
        """Should accept volume in valid linear range 0.0–1.5."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")
        
        result = validate_tool_call(
            tool_name="stori_set_track_volume",
            params={"trackId": track_id, "volume": 1.0},
            allowed_tools={"stori_set_track_volume"},
            registry=registry,
        )
        
        assert result.valid
    
    def test_volume_out_of_range(self):
        """Should reject volume outside 0.0–1.5."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")
        
        result = validate_tool_call(
            tool_name="stori_set_track_volume",
            params={"trackId": track_id, "volume": 3.0},  # Too high
            allowed_tools={"stori_set_track_volume"},
            registry=registry,
        )
        
        assert not result.valid
        assert any("out of range" in str(e).lower() for e in result.errors)
    
    def test_pan_in_range(self):
        """Should accept pan in valid range 0.0–1.0."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")
        
        result = validate_tool_call(
            tool_name="stori_set_track_pan",
            params={"trackId": track_id, "pan": 0.5},
            allowed_tools={"stori_set_track_pan"},
            registry=registry,
        )
        
        assert result.valid
    
    def test_pan_out_of_range(self):
        """Should reject pan outside 0.0–1.0."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")
        
        result = validate_tool_call(
            tool_name="stori_set_track_pan",
            params={"trackId": track_id, "pan": 1.5},  # Too high
            allowed_tools={"stori_set_track_pan"},
            registry=registry,
        )
        
        assert not result.valid


class TestToolSpecificValidation:
    """Test tool-specific validation rules."""
    
    def test_valid_effect_type(self):
        """Should accept valid effect type."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")
        
        result = validate_tool_call(
            tool_name="stori_add_insert_effect",
            params={"trackId": track_id, "type": "compressor"},
            allowed_tools={"stori_add_insert_effect"},
            registry=registry,
        )
        
        assert result.valid
    
    def test_invalid_effect_type(self):
        """Should reject unknown effect type."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")
        
        result = validate_tool_call(
            tool_name="stori_add_insert_effect",
            params={"trackId": track_id, "type": "unknown_effect"},
            allowed_tools={"stori_add_insert_effect"},
            registry=registry,
        )
        
        assert not result.valid
        assert any("effect" in str(e).lower() for e in result.errors)
    
    def test_valid_quantize_grid(self):
        """Should accept valid quantize gridSize (numeric beats)."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")
        region_id = registry.create_region("Pattern", track_id)

        result = validate_tool_call(
            tool_name="stori_quantize_notes",
            params={"regionId": region_id, "gridSize": 0.25},  # 1/16 note
            allowed_tools={"stori_quantize_notes"},
            registry=registry,
        )
        
        assert result.valid
    
    def test_invalid_quantize_grid(self):
        """Should reject invalid quantize gridSize."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")
        region_id = registry.create_region("Pattern", track_id)

        result = validate_tool_call(
            tool_name="stori_quantize_notes",
            params={"regionId": region_id, "gridSize": 0.3},  # Not a valid grid value
            allowed_tools={"stori_quantize_notes"},
            registry=registry,
        )
        
        assert not result.valid
    
    def test_region_start_beat_negative(self):
        """Should reject negative startBeat."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")
        
        result = validate_tool_call(
            tool_name="stori_add_midi_region",
            params={"trackId": track_id, "startBeat": -1, "durationBeats": 16},
            allowed_tools={"stori_add_midi_region"},
            registry=registry,
        )
        
        assert not result.valid
    
    def test_empty_notes_array(self):
        """Should reject empty notes array."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")
        region_id = registry.create_region("Pattern", track_id)
        
        result = validate_tool_call(
            tool_name="stori_add_notes",
            params={"regionId": region_id, "notes": []},
            allowed_tools={"stori_add_notes"},
            registry=registry,
        )
        
        assert not result.valid
        assert any("empty" in str(e).lower() for e in result.errors)


class TestBatchValidation:
    """Test batch validation of multiple tool calls."""
    
    def test_all_valid(self):
        """Should pass when all tools are valid."""
        registry = EntityRegistry()
        track_id = registry.create_track("Drums")
        
        results = validate_tool_calls_batch(
            [
                ("stori_play", {}),
                ("stori_set_track_volume", {"trackId": track_id, "volume": 1.0}),
            ],
            allowed_tools={"stori_play", "stori_set_track_volume"},
            registry=registry,
        )
        
        assert all_valid(results)
        assert len(collect_errors(results)) == 0
    
    def test_some_invalid(self):
        """Should report errors for invalid tools."""
        results = validate_tool_calls_batch(
            [
                ("stori_play", {}),
                ("stori_disallowed", {}),
            ],
            allowed_tools={"stori_play"},
        )
        
        assert not all_valid(results)
        errors = collect_errors(results)
        assert len(errors) == 1
        assert "stori_disallowed" in errors[0]


class TestSimpleValidation:
    """Test simple validation interface."""
    
    def test_simple_valid(self):
        """Simple interface should return (True, '') for valid."""
        valid, error = validate_tool_call_simple(
            "stori_play",
            {},
            {"stori_play"},
        )
        
        assert valid
        assert error == ""
    
    def test_simple_invalid(self):
        """Simple interface should return (False, message) for invalid."""
        valid, error = validate_tool_call_simple(
            "stori_disallowed",
            {},
            {"stori_play"},
        )
        
        assert not valid
        assert "not allowed" in error.lower()


class TestTargetScopeCheck:
    """Target scope advisory warnings from structured prompt Target field."""

    def test_project_scope_never_warns(self):
        """Target: project — everything is in scope."""
        result = validate_tool_call(
            "stori_set_track_volume",
            {"trackId": "abc", "volume": 0.5},
            {"stori_set_track_volume"},
            target_scope=("project", None),
        )
        assert result.warnings == []

    def test_track_scope_matching_name_no_warning(self):
        """Tool call matches the target track — no warning."""
        result = validate_tool_call(
            "stori_add_insert_effect",
            {"trackId": "abc", "trackName": "Bass", "type": "compressor"},
            {"stori_add_insert_effect"},
            target_scope=("track", "Bass"),
        )
        assert result.warnings == []

    def test_track_scope_different_name_warns(self):
        """Tool call references a different track — advisory warning."""
        result = validate_tool_call(
            "stori_add_insert_effect",
            {"trackId": "abc", "trackName": "Drums", "type": "eq"},
            {"stori_add_insert_effect"},
            target_scope=("track", "Bass"),
        )
        assert len(result.warnings) == 1
        assert "track:Bass" in result.warnings[0]
        assert "Drums" in result.warnings[0]
        # Warning only — validation still passes
        assert result.valid

    def test_track_scope_case_insensitive(self):
        """Track name comparison is case-insensitive."""
        result = validate_tool_call(
            "stori_add_insert_effect",
            {"trackId": "abc", "trackName": "bass", "type": "compressor"},
            {"stori_add_insert_effect"},
            target_scope=("track", "Bass"),
        )
        assert result.warnings == []

    def test_no_target_scope_no_warnings(self):
        """Without target_scope, no scope warnings are emitted."""
        result = validate_tool_call(
            "stori_set_track_volume",
            {"trackId": "abc", "volume": 0.5},
            {"stori_set_track_volume"},
            target_scope=None,
        )
        assert result.warnings == []

    def test_region_scope_different_name_warns(self):
        """Tool call references a different region name — warning."""
        result = validate_tool_call(
            "stori_add_midi_region",
            {"trackId": "abc", "name": "Chorus", "startBeat": 0, "durationBeats": 16},
            {"stori_add_midi_region"},
            target_scope=("region", "Verse A"),
        )
        assert len(result.warnings) == 1
        assert "region:Verse A" in result.warnings[0]
        assert result.valid
