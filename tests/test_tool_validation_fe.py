"""
Tests for frontend validation constraints.

Validates that backend enforces the same constraints as the Swift frontend
per TOOL_CALL_VALIDATION_REFERENCE.md.
"""
from __future__ import annotations

import pytest
from app.core.tool_validation import validate_tool_call, NAME_LENGTH_LIMITS


class TestNameLengthValidation:
    """Test name length constraints."""
    
    def test_track_name_max_50_chars(self) -> None:

        """Track names must be <= 50 characters."""
        allowed = {"stori_add_midi_track"}
        
        # Valid: exactly 50 chars
        result = validate_tool_call(
            "stori_add_midi_track",
            {"name": "A" * 50},
            allowed
        )
        assert result.valid
        
        # Invalid: 51 chars
        result = validate_tool_call(
            "stori_add_midi_track",
            {"name": "A" * 51},
            allowed
        )
        assert not result.valid
        assert any("50 characters" in str(e) for e in result.errors)
    
    def test_track_name_cannot_be_empty(self) -> None:

        """Track names cannot be empty or whitespace-only."""
        allowed = {"stori_add_midi_track"}
        
        # Empty
        result = validate_tool_call(
            "stori_add_midi_track",
            {"name": ""},
            allowed
        )
        assert not result.valid
        
        # Whitespace only
        result = validate_tool_call(
            "stori_add_midi_track",
            {"name": "   "},
            allowed
        )
        assert not result.valid
    
    def test_region_name_max_50_chars(self) -> None:

        """Region names must be <= 50 characters."""
        allowed = {"stori_add_midi_region"}
        
        # Valid
        result = validate_tool_call(
            "stori_add_midi_region",
            {
                "trackId": "test-id",
                "startBeat": 0,
                "durationBeats": 4,
                "name": "A" * 50
            },
            allowed
        )
        assert result.valid
        
        # Invalid
        result = validate_tool_call(
            "stori_add_midi_region",
            {
                "trackId": "test-id",
                "startBeat": 0,
                "durationBeats": 4,
                "name": "A" * 51
            },
            allowed
        )
        assert not result.valid
    
    def test_bus_name_max_50_chars(self) -> None:

        """Bus names must be <= 50 characters."""
        allowed = {"stori_ensure_bus"}
        
        result = validate_tool_call(
            "stori_ensure_bus",
            {"name": "A" * 51},
            allowed
        )
        assert not result.valid
    
    def test_project_name_max_100_chars(self) -> None:

        """Project names must be <= 100 characters."""
        allowed = {"stori_create_project"}
        
        # Valid: 100 chars
        result = validate_tool_call(
            "stori_create_project",
            {"name": "A" * 100},
            allowed
        )
        assert result.valid
        
        # Invalid: 101 chars
        result = validate_tool_call(
            "stori_create_project",
            {"name": "A" * 101},
            allowed
        )
        assert not result.valid


class TestMidiNoteValidation:
    """Test MIDI note constraints."""
    
    def test_pitch_range_0_127(self) -> None:

        """Pitch must be 0-127."""
        allowed = {"stori_add_notes"}
        
        # Valid
        result = validate_tool_call(
            "stori_add_notes",
            {
                "regionId": "test-id",
                "notes": [
                    {"pitch": 0, "startBeat": 0, "durationBeats": 1, "velocity": 100},
                    {"pitch": 127, "startBeat": 1, "durationBeats": 1, "velocity": 100}
                ]
            },
            allowed
        )
        assert result.valid
        
        # Invalid: -1
        result = validate_tool_call(
            "stori_add_notes",
            {
                "regionId": "test-id",
                "notes": [{"pitch": -1, "startBeat": 0, "durationBeats": 1, "velocity": 100}]
            },
            allowed
        )
        assert not result.valid
        
        # Invalid: 128
        result = validate_tool_call(
            "stori_add_notes",
            {
                "regionId": "test-id",
                "notes": [{"pitch": 128, "startBeat": 0, "durationBeats": 1, "velocity": 100}]
            },
            allowed
        )
        assert not result.valid
    
    def test_velocity_range_1_127(self) -> None:

        """Velocity must be 1-127 (not 0)."""
        allowed = {"stori_add_notes"}
        
        # Valid
        result = validate_tool_call(
            "stori_add_notes",
            {
                "regionId": "test-id",
                "notes": [{"pitch": 60, "startBeat": 0, "durationBeats": 1, "velocity": 1}]
            },
            allowed
        )
        assert result.valid
        
        # Invalid: 0
        result = validate_tool_call(
            "stori_add_notes",
            {
                "regionId": "test-id",
                "notes": [{"pitch": 60, "startBeat": 0, "durationBeats": 1, "velocity": 0}]
            },
            allowed
        )
        assert not result.valid
        
        # Invalid: 128
        result = validate_tool_call(
            "stori_add_notes",
            {
                "regionId": "test-id",
                "notes": [{"pitch": 60, "startBeat": 0, "durationBeats": 1, "velocity": 128}]
            },
            allowed
        )
        assert not result.valid
    
    def test_start_beat_non_negative(self) -> None:

        """StartBeat must be >= 0."""
        allowed = {"stori_add_notes"}
        
        # Valid: 0
        result = validate_tool_call(
            "stori_add_notes",
            {
                "regionId": "test-id",
                "notes": [{"pitch": 60, "startBeat": 0, "durationBeats": 1, "velocity": 100}]
            },
            allowed
        )
        assert result.valid
        
        # Invalid: negative
        result = validate_tool_call(
            "stori_add_notes",
            {
                "regionId": "test-id",
                "notes": [{"pitch": 60, "startBeat": -1, "durationBeats": 1, "velocity": 100}]
            },
            allowed
        )
        assert not result.valid
    
    def test_duration_range_0_01_to_1000(self) -> None:

        """Duration must be 0.01-1000 beats."""
        allowed = {"stori_add_notes"}
        
        # Valid: 0.01
        result = validate_tool_call(
            "stori_add_notes",
            {
                "regionId": "test-id",
                "notes": [{"pitch": 60, "startBeat": 0, "durationBeats": 0.01, "velocity": 100}]
            },
            allowed
        )
        assert result.valid
        
        # Invalid: too small
        result = validate_tool_call(
            "stori_add_notes",
            {
                "regionId": "test-id",
                "notes": [{"pitch": 60, "startBeat": 0, "durationBeats": 0.001, "velocity": 100}]
            },
            allowed
        )
        assert not result.valid
        
        # Invalid: too large
        result = validate_tool_call(
            "stori_add_notes",
            {
                "regionId": "test-id",
                "notes": [{"pitch": 60, "startBeat": 0, "durationBeats": 1001, "velocity": 100}]
            },
            allowed
        )
        assert not result.valid


class TestEffectTypeValidation:
    """Test effect type constraints."""
    
    def test_valid_effect_types(self) -> None:

        """Only specific effect types are allowed."""
        allowed = {"stori_add_insert_effect"}
        
        valid_types = [
            "reverb", "delay", "compressor", "eq", "distortion", "filter",
            "chorus", "modulation", "overdrive", "phaser", "flanger", "tremolo"
        ]
        
        for effect_type in valid_types:
            result = validate_tool_call(
                "stori_add_insert_effect",
                {"trackId": "test-id", "type": effect_type},
                allowed
            )
            assert result.valid, f"{effect_type} should be valid"
    
    def test_invalid_effect_type(self) -> None:

        """Invalid effect types are rejected."""
        allowed = {"stori_add_insert_effect"}
        
        result = validate_tool_call(
            "stori_add_insert_effect",
            {"trackId": "test-id", "type": "invalid_effect"},
            allowed
        )
        assert not result.valid
        assert any("Unknown effect type" in str(e) for e in result.errors)


class TestTrackIconValidation:
    """Test track icon constraints."""
    
    def test_valid_track_icons(self) -> None:

        """Only SF Symbols from curated list are allowed."""
        allowed = {"stori_set_track_icon"}
        
        valid_icons = [
            "waveform.path",
            "pianokeys",
            "guitars.fill",
            "pianokeys.inverse",
            "music.mic.circle.fill",
            "music.note.list",
        ]
        
        for icon in valid_icons:
            result = validate_tool_call(
                "stori_set_track_icon",
                {"trackId": "test-id", "icon": icon},
                allowed
            )
            assert result.valid, f"{icon} should be valid"
    
    def test_invalid_track_icon(self) -> None:

        """Invalid icons are rejected."""
        allowed = {"stori_set_track_icon"}
        
        result = validate_tool_call(
            "stori_set_track_icon",
            {"trackId": "test-id", "icon": "invalid.icon"},
            allowed
        )
        assert not result.valid
        assert any("Invalid icon" in str(e) for e in result.errors)


class TestRegionValidation:
    """Test region constraints."""
    
    def test_start_beat_non_negative(self) -> None:

        """Region startBeat must be >= 0."""
        allowed = {"stori_add_midi_region"}
        
        # Valid
        result = validate_tool_call(
            "stori_add_midi_region",
            {"trackId": "test-id", "startBeat": 0, "durationBeats": 4},
            allowed
        )
        assert result.valid
        
        # Invalid
        result = validate_tool_call(
            "stori_add_midi_region",
            {"trackId": "test-id", "startBeat": -1, "durationBeats": 4},
            allowed
        )
        assert not result.valid
    
    def test_duration_minimum_0_01(self) -> None:

        """Region duration must be at least 0.01 beats."""
        allowed = {"stori_add_midi_region"}
        
        # Valid
        result = validate_tool_call(
            "stori_add_midi_region",
            {"trackId": "test-id", "startBeat": 0, "durationBeats": 0.01},
            allowed
        )
        assert result.valid
        
        # Invalid
        result = validate_tool_call(
            "stori_add_midi_region",
            {"trackId": "test-id", "startBeat": 0, "durationBeats": 0},
            allowed
        )
        assert not result.valid
