"""
Tests for Groove Engine: style-specific microtiming, velocity, and articulation.

Tests verify:
1. Groove profiles have correct timing characteristics
2. Swing is applied correctly to offbeats
3. Role-based timing offsets are applied
4. Velocity shaping follows accent maps
5. Hat arcs are applied correctly
"""
from __future__ import annotations

import pytest
import random
from app.contracts.json_types import NoteDict
from app.services.groove_engine import (
    GrooveProfile,
    BOOM_BAP,
    TRAP_STRAIGHT,
    TRAP_TRIPLET,
    HOUSE_FOUR_ON_FLOOR,
    TIGHT,
    PUSHED,
    LAID_BACK,
    get_groove_profile,
    get_role_for_pitch,
    is_offbeat_for_grid,
    calculate_swing_offset,
    apply_groove_map,
    extract_kick_onsets,
    extract_snare_onsets,
    extract_hat_grid,
    RhythmSpine,
    GROOVE_PROFILES,
)


class TestGrooveProfiles:
    """Test groove profile definitions."""
    
    def test_boom_bap_profile_exists(self) -> None:

        """Boom bap profile should have correct characteristics."""
        assert BOOM_BAP.name == "boom_bap"
        assert BOOM_BAP.swing_amount > 0.3  # Should have swing
        assert BOOM_BAP.swing_grid == "8th"  # 8th note swing
        
        # Snare should be late, kick should be early
        kick_lo, kick_hi = BOOM_BAP.role_offset_ms["kick"]
        snare_lo, snare_hi = BOOM_BAP.role_offset_ms["snare"]
        assert kick_hi < snare_lo  # Kick max < snare min (kick early, snare late)
    
    def test_trap_straight_is_straight(self) -> None:

        """Trap straight should have no swing."""
        assert TRAP_STRAIGHT.swing_amount == 0.0
        assert TRAP_STRAIGHT.swing_grid == "16th"
    
    def test_trap_triplet_has_some_swing(self) -> None:

        """Trap triplet should have triplet feel."""
        assert TRAP_TRIPLET.swing_amount > 0.2
        assert TRAP_TRIPLET.swing_grid == "16th"
    
    def test_house_has_tight_kick(self) -> None:

        """House/four-on-floor should have tight kick timing."""
        kick_lo, kick_hi = HOUSE_FOUR_ON_FLOOR.role_offset_ms["kick"]
        assert abs(kick_lo) <= 5 and abs(kick_hi) <= 5  # Very tight
    
    def test_all_profiles_have_required_fields(self) -> None:

        """All profiles should have complete configuration."""
        for name, profile in GROOVE_PROFILES.items():
            assert profile.name, f"Profile {name} missing name"
            assert "kick" in profile.role_offset_ms, f"Profile {name} missing kick offset"
            assert "snare" in profile.role_offset_ms, f"Profile {name} missing snare offset"
            assert "hat" in profile.role_offset_ms, f"Profile {name} missing hat offset"
            assert len(profile.accent_map) >= 2, f"Profile {name} needs accent map"
            assert len(profile.hat_arc) == 2, f"Profile {name} needs hat arc"


class TestRoleDetection:
    """Test pitch to role mapping."""
    
    def test_kick_pitches(self) -> None:

        """Kick pitches should map to kick role."""
        assert get_role_for_pitch(36) == "kick"
        assert get_role_for_pitch(35) == "kick"
    
    def test_snare_pitches(self) -> None:

        """Snare pitches should map to snare role."""
        assert get_role_for_pitch(38) == "snare"
        assert get_role_for_pitch(39) == "snare"
        assert get_role_for_pitch(40) == "snare"
    
    def test_hat_pitches(self) -> None:

        """Hat pitches should map to hat role."""
        assert get_role_for_pitch(42) == "hat"
        assert get_role_for_pitch(44) == "hat"
        assert get_role_for_pitch(46) == "hat"
    
    def test_ghost_detection_by_velocity(self) -> None:

        """Low velocity snare should be detected as ghost."""
        # High velocity = snare
        assert get_role_for_pitch(38, velocity=100) == "snare"
        # Low velocity = ghost
        assert get_role_for_pitch(38, velocity=50) == "ghost"
    
    def test_layer_override(self) -> None:

        """Layer parameter should override pitch-based detection."""
        assert get_role_for_pitch(38, velocity=100, layer="ghost_layer") == "ghost"
        assert get_role_for_pitch(42, velocity=100, layer="fills") == "fill"


class TestSwingCalculation:
    """Test swing offset calculation."""
    
    def test_offbeat_detection_8th(self) -> None:

        """8th note offbeats should be at 0.5, 1.5, 2.5, 3.5."""
        assert is_offbeat_for_grid(0.5, "8th")
        assert is_offbeat_for_grid(1.5, "8th")
        assert is_offbeat_for_grid(2.5, "8th")
        assert is_offbeat_for_grid(3.5, "8th")
        
        # On-beats should not be offbeat
        assert not is_offbeat_for_grid(0.0, "8th")
        assert not is_offbeat_for_grid(1.0, "8th")
        assert not is_offbeat_for_grid(2.0, "8th")
    
    def test_offbeat_detection_16th(self) -> None:

        """16th note offbeats should be at 0.25, 0.75, 1.25, etc."""
        assert is_offbeat_for_grid(0.25, "16th")
        assert is_offbeat_for_grid(0.75, "16th")
        assert is_offbeat_for_grid(1.25, "16th")
        
        # Even 16ths should not be offbeat
        assert not is_offbeat_for_grid(0.0, "16th")
        assert not is_offbeat_for_grid(0.5, "16th")
        assert not is_offbeat_for_grid(1.0, "16th")
    
    def test_swing_only_affects_offbeats(self) -> None:

        """Swing should only affect offbeat positions."""
        # On-beat should have no swing offset
        assert calculate_swing_offset(0.0, BOOM_BAP, 120) == 0.0
        assert calculate_swing_offset(1.0, BOOM_BAP, 120) == 0.0
        
        # Offbeat should have swing offset
        offset = calculate_swing_offset(0.5, BOOM_BAP, 120)
        assert offset > 0  # Should be delayed
    
    def test_no_swing_with_zero_amount(self) -> None:

        """Profile with swing_amount=0 should have no swing offset."""
        assert calculate_swing_offset(0.5, TRAP_STRAIGHT, 120) == 0.0


class TestGrooveApplication:
    """Test apply_groove_map function."""
    
    def test_applies_timing_offsets(self) -> None:

        """Notes should have timing offsets applied."""
        notes: list[NoteDict] = [
            {"pitch": 36, "start_beat": 0.0, "duration_beats": 0.25, "velocity": 100},
            {"pitch": 38, "start_beat": 1.0, "duration_beats": 0.25, "velocity": 100},
        ]
        
        rng = random.Random(42)  # Deterministic
        result = apply_groove_map(notes, tempo=120, style="boom_bap", rng=rng)
        
        # Notes should still exist
        assert len(result) == 2
        
        # Timing should be modified (within reasonable range)
        for n in result:
            assert n["start_beat"] >= -0.1  # Not too early
            assert n["start_beat"] <= 2.0  # Not too late
    
    def test_applies_velocity_shaping(self) -> None:

        """Velocity should be shaped by accent map."""
        notes: list[NoteDict] = [
            {"pitch": 42, "start_beat": i * 0.5, "duration_beats": 0.25, "velocity": 80}
            for i in range(8)
        ]
        
        rng = random.Random(42)
        result = apply_groove_map(notes, tempo=120, style="boom_bap", rng=rng)
        
        # Velocity should vary
        velocities = [n["velocity"] for n in result]
        assert max(velocities) > min(velocities)  # Not all same
        assert all(1 <= v <= 127 for v in velocities)  # In MIDI range
    
    def test_hat_arc_applied(self) -> None:

        """Hat notes should have velocity arc applied."""
        # Create hats at different positions in bar
        notes: list[NoteDict] = [
            {"pitch": 42, "start_beat": i * 0.5, "duration_beats": 0.25, "velocity": 100, "layer": "timekeepers"}
            for i in range(8)
        ]
        
        layer_map = {i: "timekeepers" for i in range(8)}
        rng = random.Random(42)
        result = apply_groove_map(notes, tempo=120, style="boom_bap", layer_map=layer_map, rng=rng)
        
        # Velocities should vary with arc pattern
        velocities = [n["velocity"] for n in result]
        assert len(set(velocities)) > 1  # Not all same
    
    def test_deterministic_with_seed(self) -> None:

        """Same seed should produce same result."""
        notes: list[NoteDict] = [
            {"pitch": 36, "start_beat": 0.0, "duration_beats": 0.25, "velocity": 100},
            {"pitch": 38, "start_beat": 1.0, "duration_beats": 0.25, "velocity": 100},
        ]
        
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        
        result1 = apply_groove_map(notes.copy(), tempo=120, style="trap", rng=rng1)
        result2 = apply_groove_map(notes.copy(), tempo=120, style="trap", rng=rng2)
        
        for n1, n2 in zip(result1, result2):
            assert n1["start_beat"] == n2["start_beat"]
            assert n1["velocity"] == n2["velocity"]
    
    def test_empty_notes(self) -> None:

        """Empty notes list should return empty list."""
        result = apply_groove_map([], tempo=120, style="trap")
        assert result == []


class TestOnsetExtraction:
    """Test onset extraction functions."""
    
    def test_extract_kick_onsets(self) -> None:

        """Should extract kick onset times."""
        notes: list[NoteDict] = [
            {"pitch": 36, "start_beat": 0.0, "duration_beats": 0.25, "velocity": 100},
            {"pitch": 42, "start_beat": 0.5, "duration_beats": 0.25, "velocity": 80},
            {"pitch": 36, "start_beat": 2.0, "duration_beats": 0.25, "velocity": 100},
            {"pitch": 38, "start_beat": 1.0, "duration_beats": 0.25, "velocity": 100},
        ]
        
        kicks = extract_kick_onsets(notes)
        assert kicks == [0.0, 2.0]
    
    def test_extract_snare_onsets(self) -> None:

        """Should extract snare onset times."""
        notes: list[NoteDict] = [
            {"pitch": 36, "start_beat": 0.0, "duration_beats": 0.25, "velocity": 100},
            {"pitch": 38, "start_beat": 1.0, "duration_beats": 0.25, "velocity": 100},
            {"pitch": 39, "start_beat": 3.0, "duration_beats": 0.25, "velocity": 100},
        ]
        
        snares = extract_snare_onsets(notes)
        assert snares == [1.0, 3.0]
    
    def test_extract_hat_grid(self) -> None:

        """Should extract hat onset times."""
        notes: list[NoteDict] = [
            {"pitch": 42, "start_beat": i * 0.5, "duration_beats": 0.25, "velocity": 80}
            for i in range(8)
        ]
        
        hats = extract_hat_grid(notes)
        assert len(hats) == 8
        assert hats[0] == 0.0
        assert hats[-1] == 3.5


class TestRhythmSpine:
    """Test RhythmSpine for coupled generation."""
    
    def test_create_from_drum_notes(self) -> None:

        """Should create rhythm spine from drum notes."""
        notes: list[NoteDict] = [
            {"pitch": 36, "start_beat": 0.0, "duration_beats": 0.25, "velocity": 100},
            {"pitch": 36, "start_beat": 2.0, "duration_beats": 0.25, "velocity": 100},
            {"pitch": 38, "start_beat": 1.0, "duration_beats": 0.25, "velocity": 100},
            {"pitch": 38, "start_beat": 3.0, "duration_beats": 0.25, "velocity": 100},
            {"pitch": 42, "start_beat": 0.0, "duration_beats": 0.25, "velocity": 80},
            {"pitch": 42, "start_beat": 0.5, "duration_beats": 0.25, "velocity": 80},
        ]
        
        spine = RhythmSpine.from_drum_notes(notes, tempo=120, bars=1, style="trap")
        
        assert len(spine.kick_onsets) == 2
        assert len(spine.snare_onsets) == 2
        assert len(spine.hat_grid) == 2
        assert spine.tempo == 120
        assert spine.bars == 1
    
    def test_anticipation_slots(self) -> None:

        """Should calculate anticipation slots before kicks."""
        spine = RhythmSpine(
            kick_onsets=[0.0, 2.0, 4.0],
            snare_onsets=[1.0, 3.0],
            hat_grid=[],
            tempo=120,
            bars=2,
        )
        
        slots = spine.get_anticipation_slots(beat_before=0.125)
        
        # First kick at 0.0 can't have anticipation (would be negative)
        # Second kick at 2.0 → anticipation at 1.875
        # Third kick at 4.0 → anticipation at 3.875
        assert 1.875 in slots or round(1.875, 4) in slots
        assert 3.875 in slots or round(3.875, 4) in slots
    
    def test_response_slots(self) -> None:

        """Should calculate response slots after snares."""
        spine = RhythmSpine(
            kick_onsets=[0.0, 2.0],
            snare_onsets=[1.0, 3.0],
            hat_grid=[],
            tempo=120,
            bars=1,
        )
        
        slots = spine.get_response_slots(snare_offset=0.25)
        
        # Response after snare at 1.0 → 1.25
        # Response after snare at 3.0 → 3.25
        assert 1.25 in slots
        assert 3.25 in slots


class TestProfileSelection:
    """Test get_groove_profile function."""
    
    def test_get_by_style(self) -> None:

        """Should get profile by style name."""
        assert get_groove_profile("boom_bap").name == "boom_bap"
        assert get_groove_profile("trap").name == "trap_straight"
        assert get_groove_profile("house").name == "house"
    
    def test_humanize_profile_override(self) -> None:

        """Humanize profile should override style."""
        profile = get_groove_profile("trap", humanize_profile="laid_back")
        assert profile.name == "laid_back"
    
    def test_default_fallback(self) -> None:

        """Unknown style should fall back to trap."""
        profile = get_groove_profile("unknown_style_xyz")
        assert profile.name == "trap_straight"
    
    def test_style_aliases(self) -> None:

        """Style aliases should work."""
        assert get_groove_profile("hip_hop").name == "boom_bap"
        assert get_groove_profile("boom_bap_swing").name == "boom_bap"
        assert get_groove_profile("house_four_on_floor").name == "house"
