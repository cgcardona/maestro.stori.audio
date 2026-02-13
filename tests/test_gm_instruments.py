"""
Tests for GM (General MIDI) instrument mapping and inference.

Tests cover:
1. GM instrument lookup by program number
2. Fuzzy matching from natural language to GM programs
3. Context-based inference with multiple sources
4. Edge cases and special handling (drums, ambiguous names)
"""

import pytest

from app.core.gm_instruments import (
    GM_INSTRUMENTS,
    GMInstrument,
    get_instrument_by_program,
    get_instrument_name,
    infer_gm_program,
    get_default_program_for_role,
    infer_gm_program_with_context,
    GMInferenceResult,
)


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def common_instruments():
    """Common instrument names for testing."""
    return [
        ("piano", 0),
        ("acoustic guitar", 25),
        ("electric bass", 33),
        ("violin", 40),
        ("trumpet", 56),
        ("flute", 73),
    ]


# =============================================================================
# GM Instrument Lookup Tests
# =============================================================================

class TestGMInstrumentLookup:
    """Tests for basic GM instrument lookup functions."""
    
    def test_all_128_programs_exist(self):
        """All 128 GM programs should be defined."""
        assert len(GM_INSTRUMENTS) == 128
        programs = [inst.program for inst in GM_INSTRUMENTS]
        assert sorted(programs) == list(range(128))
    
    def test_get_instrument_by_program_valid(self):
        """Should return correct instrument for valid program."""
        inst = get_instrument_by_program(0)
        assert inst is not None
        assert inst.name == "Acoustic Grand Piano"
        assert inst.category == "piano"
    
    def test_get_instrument_by_program_invalid(self):
        """Should return None for invalid program."""
        assert get_instrument_by_program(128) is None
        assert get_instrument_by_program(-1) is None
    
    def test_get_instrument_name_valid(self):
        """Should return correct name for valid program."""
        assert get_instrument_name(0) == "Acoustic Grand Piano"
        assert get_instrument_name(33) == "Electric Bass (finger)"
        assert get_instrument_name(80) == "Lead 1 (square)"
    
    def test_get_instrument_name_invalid(self):
        """Should return fallback for invalid program."""
        assert get_instrument_name(200) == "Program 200"
    
    def test_instrument_has_aliases(self):
        """All instruments should have at least one alias."""
        for inst in GM_INSTRUMENTS:
            assert len(inst.aliases) > 0, f"Instrument {inst.name} has no aliases"


# =============================================================================
# Fuzzy Matching Tests
# =============================================================================

class TestFuzzyMatching:
    """Tests for natural language to GM program matching."""
    
    def test_exact_name_match(self):
        """Exact GM name should match correctly."""
        assert infer_gm_program("Acoustic Grand Piano") == 0
        assert infer_gm_program("Electric Bass (finger)") == 33
    
    def test_alias_match(self, common_instruments):
        """Common aliases should match."""
        for alias, expected_program in common_instruments:
            result = infer_gm_program(alias)
            assert result == expected_program, f"'{alias}' should match program {expected_program}"
    
    def test_case_insensitive(self):
        """Matching should be case-insensitive."""
        assert infer_gm_program("PIANO") == 0
        assert infer_gm_program("Piano") == 0
        assert infer_gm_program("piano") == 0
        assert infer_gm_program("ACOUSTIC GUITAR") == 25
    
    def test_partial_match(self):
        """Partial matches should work for substrings."""
        assert infer_gm_program("My Acoustic Guitar Track") == 25
        assert infer_gm_program("Cool Electric Bass Line") == 33
    
    def test_drums_return_none(self):
        """Drums should return None (use channel 10)."""
        assert infer_gm_program("drums") is None
        assert infer_gm_program("Drum Kit") is None
        assert infer_gm_program("Kick and Snare") is None
        assert infer_gm_program("Hi-Hat Pattern") is None
    
    def test_rhodes_matches_electric_piano(self):
        """Rhodes should match Electric Piano 1."""
        assert infer_gm_program("rhodes") == 4
        assert infer_gm_program("Fender Rhodes") == 4
    
    def test_synth_bass_variations(self):
        """Synth bass should match program 38."""
        assert infer_gm_program("synth bass") == 38
        assert infer_gm_program("analog bass") == 38
    
    def test_string_ensemble(self):
        """String ensemble should match program 48."""
        assert infer_gm_program("strings") == 48
        assert infer_gm_program("orchestral strings") == 48
    
    def test_no_match_returns_default(self):
        """No match should return the default value."""
        assert infer_gm_program("xyzzy", default_program=42) == 42
        assert infer_gm_program("random nonsense") is None
    
    def test_empty_string(self):
        """Empty string should return default."""
        assert infer_gm_program("") is None
        assert infer_gm_program("", default_program=0) == 0


# =============================================================================
# Role-Based Default Tests
# =============================================================================

class TestRoleDefaults:
    """Tests for musical role to GM program mapping."""
    
    def test_drums_role(self):
        """Drums role should return None (channel 10)."""
        assert get_default_program_for_role("drums") is None
        assert get_default_program_for_role("drum") is None
        assert get_default_program_for_role("percussion") is None
    
    def test_bass_role(self):
        """Bass role should return Electric Bass (finger)."""
        assert get_default_program_for_role("bass") == 33
    
    def test_chords_role(self):
        """Chords role should return Electric Piano."""
        assert get_default_program_for_role("chords") == 4
    
    def test_melody_role(self):
        """Melody role should return Synth Lead."""
        assert get_default_program_for_role("melody") == 80
        assert get_default_program_for_role("lead") == 80
    
    def test_pads_role(self):
        """Pads role should return Synth Pad."""
        assert get_default_program_for_role("pads") == 88
        assert get_default_program_for_role("pad") == 88
    
    def test_unknown_role(self):
        """Unknown role should return None."""
        assert get_default_program_for_role("unknown") is None
        assert get_default_program_for_role("") is None


# =============================================================================
# Context-Based Inference Tests
# =============================================================================

class TestContextInference:
    """Tests for multi-source context-based inference."""
    
    def test_instrument_takes_priority(self):
        """Explicit instrument field should take priority."""
        result = infer_gm_program_with_context(
            track_name="My Track",
            instrument="rhodes",
        )
        assert result.program == 4
        assert "Electric Piano" in result.instrument_name
        assert result.confidence == "high"
    
    def test_track_name_used_when_no_instrument(self):
        """Track name should be used when no instrument specified."""
        result = infer_gm_program_with_context(
            track_name="Acoustic Guitar",
            instrument=None,
        )
        assert result.program == 25
        assert result.confidence == "medium"
    
    def test_role_used_as_fallback(self):
        """Role should be used as fallback."""
        result = infer_gm_program_with_context(
            track_name="Track 1",
            instrument=None,
            role="bass",
        )
        assert result.program == 33
        assert result.confidence == "low"
    
    def test_default_to_piano(self):
        """Should default to piano when nothing matches."""
        result = infer_gm_program_with_context(
            track_name="My Random Track",
            instrument=None,
            role=None,
        )
        assert result.program == 0
        assert "Piano" in result.instrument_name
        assert result.confidence == "none"
    
    def test_drums_detected_from_any_source(self):
        """Drums should be detected from any source."""
        # From track name
        result = infer_gm_program_with_context(track_name="Drum Kit")
        assert result.is_drums
        assert result.program is None
        
        # From instrument
        result = infer_gm_program_with_context(
            track_name="Track 1",
            instrument="drums",
        )
        assert result.is_drums
        
        # From role
        result = infer_gm_program_with_context(
            track_name="Track 1",
            role="drums",
        )
        assert result.is_drums
    
    def test_needs_program_change(self):
        """needs_program_change should be correct."""
        # Regular instrument
        result = infer_gm_program_with_context(track_name="Piano")
        assert result.needs_program_change is True
        
        # Drums
        result = infer_gm_program_with_context(track_name="Drums")
        assert result.needs_program_change is False


# =============================================================================
# Edge Cases and Special Handling
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and special handling."""
    
    def test_guitar_variations(self):
        """Different guitar types should match correctly."""
        # Acoustic (defaults to steel)
        assert infer_gm_program("acoustic guitar") == 25
        
        # Nylon/classical
        assert infer_gm_program("nylon guitar") == 24
        assert infer_gm_program("classical guitar") == 24
        
        # Electric variations
        assert infer_gm_program("jazz guitar") == 26
        assert infer_gm_program("clean electric") == 27  # "clean electric" matches clean electric
        assert infer_gm_program("distortion guitar") == 30
    
    def test_bass_variations(self):
        """Different bass types should match correctly."""
        # Electric finger (default)
        assert infer_gm_program("bass") == 33
        assert infer_gm_program("electric bass") == 33
        
        # Acoustic
        assert infer_gm_program("upright bass") == 32
        assert infer_gm_program("double bass") == 32
        
        # Slap
        assert infer_gm_program("slap bass") == 36
        
        # Fretless
        assert infer_gm_program("fretless bass") == 35
    
    def test_sax_variations(self):
        """Different sax types should match correctly."""
        assert infer_gm_program("soprano sax") == 64
        assert infer_gm_program("alto sax") == 65
        assert infer_gm_program("tenor sax") == 66
        assert infer_gm_program("sax") == 66  # Default to tenor
        assert infer_gm_program("baritone sax") == 67
    
    def test_synth_variations(self):
        """Synth leads and pads should match correctly."""
        assert infer_gm_program("synth lead") == 80
        assert infer_gm_program("synth pad") == 88
        assert infer_gm_program("pad") == 88
        assert infer_gm_program("lead") == 80
    
    def test_organ_variations(self):
        """Organ types should match correctly."""
        assert infer_gm_program("organ") == 16
        assert infer_gm_program("hammond") == 16
        assert infer_gm_program("b3") == 16
        assert infer_gm_program("church organ") == 19
    
    def test_punctuation_ignored(self):
        """Punctuation should not affect matching."""
        assert infer_gm_program("acoustic-guitar") == 25
        assert infer_gm_program("electric_bass") == 33
        assert infer_gm_program("piano!") == 0
    
    def test_extra_words_allowed(self):
        """Extra words in track name should still match."""
        assert infer_gm_program("My Cool Piano Track") == 0
        assert infer_gm_program("Lead 1 - Melody") == 80
        assert infer_gm_program("Bass - Main") == 33


# =============================================================================
# Integration-Style Tests
# =============================================================================

class TestIntegration:
    """Integration-style tests simulating real usage."""
    
    def test_typical_track_names(self):
        """Typical track names from DAW sessions should work."""
        test_cases = [
            ("Drums", None, True),  # Drums - no program
            ("Bass", 33, False),
            ("Piano", 0, False),
            ("Lead Synth", 80, False),
            ("Pad", 88, False),
            ("Strings", 48, False),
            ("Acoustic Guitar", 25, False),
            ("Electric Guitar", 27, False),  # Default clean electric
            ("Rhodes", 4, False),
            ("Wurlitzer", None, False),  # Might not match exactly
            ("Brass Section", 61, False),
            ("Choir", 52, False),
            ("Flute Solo", 73, False),
        ]
        
        for track_name, expected_program, is_drums in test_cases:
            result = infer_gm_program_with_context(track_name=track_name)
            
            if is_drums:
                assert result.is_drums, f"'{track_name}' should be drums"
            elif expected_program is not None:
                assert result.program == expected_program, \
                    f"'{track_name}' should be program {expected_program}, got {result.program}"
    
    def test_composing_workflow(self):
        """Simulate the composing workflow with roles."""
        roles = ["drums", "bass", "chords", "melody"]
        expected = [None, 33, 4, 80]
        
        for role, expected_program in zip(roles, expected):
            result = infer_gm_program_with_context(
                track_name=role.capitalize(),
                role=role,
            )
            
            if expected_program is None:
                assert result.is_drums
            else:
                assert result.program == expected_program
