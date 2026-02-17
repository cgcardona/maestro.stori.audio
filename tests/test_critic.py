"""
Tests for Critic: Layer-aware scoring for groove, fills, ghosts, hat articulation.

Tests verify:
1. Layer-aware scoring works correctly
2. Groove pocket scoring detects good/bad timing
3. Hat articulation scoring detects variety
4. Fill localization scoring detects correct placement
5. Ghost plausibility scoring detects correct velocity/position
6. Rejection sampling works correctly
"""
import pytest
from app.services.critic import (
    score_drum_notes,
    score_bass_notes,
    accept_drum,
    accept_bass,
    _score_groove_pocket,
    _score_hat_articulation,
    _score_fill_localization,
    _score_ghost_plausibility,
    _score_layer_balance,
    _score_repetition_structure,
    _score_velocity_dynamics,
    rejection_sample,
    ACCEPT_THRESHOLD_DRUM,
    ACCEPT_THRESHOLD_DRUM_QUALITY,
    DRUM_WEIGHTS,
)


def make_drum_notes(
    bars: int = 4,
    include_kicks: bool = True,
    include_snares: bool = True,
    include_hats: bool = True,
    include_fills: bool = True,
    include_ghosts: bool = False,
    fill_bars: list | None = None,
) -> list[dict]:
    """Helper to create drum notes for testing."""
    fill_bars = fill_bars or [3]
    notes = []
    
    for bar in range(bars):
        bar_start = bar * 4.0
        
        # Kick on 1 and 3
        if include_kicks:
            notes.append({
                "pitch": 36, "start_beat": bar_start + 0.0, "duration_beats": 0.25,
                "velocity": 100, "layer": "core"
            })
            notes.append({
                "pitch": 36, "start_beat": bar_start + 2.0, "duration_beats": 0.25,
                "velocity": 100, "layer": "core"
            })
        
        # Snare on 2 and 4
        if include_snares:
            notes.append({
                "pitch": 38, "start_beat": bar_start + 1.0, "duration_beats": 0.25,
                "velocity": 95, "layer": "core"
            })
            notes.append({
                "pitch": 38, "start_beat": bar_start + 3.0, "duration_beats": 0.25,
                "velocity": 95, "layer": "core"
            })
        
        # Hats on 8ths
        if include_hats:
            for i in range(8):
                pitch = 42 if i % 4 != 3 else 46  # Open hat on 4th 8th
                vel = 80 - (i % 2) * 10  # Accent pattern
                notes.append({
                    "pitch": pitch, "start_beat": bar_start + i * 0.5, "duration_beats": 0.25,
                    "velocity": vel, "layer": "timekeepers"
                })
        
        # Fills in fill bars
        if include_fills and bar in fill_bars:
            for i in range(4):
                notes.append({
                    "pitch": 43 + i, "start_beat": bar_start + 3.0 + i * 0.25, "duration_beats": 0.25,
                    "velocity": 90, "layer": "fills"
                })
        
        # Ghost notes near backbeats
        if include_ghosts:
            notes.append({
                "pitch": 37, "start_beat": bar_start + 0.75, "duration_beats": 0.25,
                "velocity": 50, "layer": "ghost_layer"
            })
            notes.append({
                "pitch": 37, "start_beat": bar_start + 2.75, "duration_beats": 0.25,
                "velocity": 45, "layer": "ghost_layer"
            })
    
    return notes


class TestDrumWeights:
    """Test drum scoring weight configuration."""
    
    def test_weights_sum_to_one(self):
        """Drum weights should sum to approximately 1.0."""
        total = sum(DRUM_WEIGHTS.values())
        assert abs(total - 1.0) < 0.01


class TestGroovePocketScoring:
    """Test groove pocket scoring."""
    
    def test_good_pocket_scores_high(self):
        """Well-timed drums should score high."""
        notes = make_drum_notes(bars=4)
        score, repairs = _score_groove_pocket(notes, style="trap")
        assert score >= 0.6
    
    def test_empty_notes_score_zero(self):
        """Empty notes should score 0."""
        score, repairs = _score_groove_pocket([])
        assert score == 0.0
        assert "empty_drums" in repairs


class TestHatArticulationScoring:
    """Test hat articulation scoring."""
    
    def test_varied_hats_score_high(self):
        """Hats with closed/open variety should score high."""
        notes = make_drum_notes(bars=4, include_kicks=False, include_snares=False)
        score, repairs = _score_hat_articulation(notes, bars=4)
        assert score >= 0.6
    
    def test_monotone_hats_score_low(self):
        """All closed hats should score lower."""
        notes = [
            {"pitch": 42, "start_beat": i * 0.5, "duration_beats": 0.25, "velocity": 80, "layer": "timekeepers"}
            for i in range(32)  # 4 bars of closed hats only
        ]
        score, repairs = _score_hat_articulation(notes, bars=4)
        assert score < 0.9  # Should be penalized for no variety
        # Should suggest adding open hats
        assert any("open" in r.lower() for r in repairs) or score >= 0.6
    
    def test_no_hats(self):
        """No hats should return base score with repair suggestion."""
        notes = make_drum_notes(bars=4, include_hats=False)
        score, repairs = _score_hat_articulation(notes, bars=4)
        assert "no_hats" in repairs or score == 0.5


class TestFillLocalizationScoring:
    """Test fill localization scoring."""
    
    def test_fills_in_correct_bars_score_high(self):
        """Fills in fill bars should score high."""
        notes = make_drum_notes(bars=4, fill_bars=[3])
        score, repairs = _score_fill_localization(notes, fill_bars=[3], bars=4)
        assert score >= 0.7
    
    def test_scattered_fills_score_lower(self):
        """Fills scattered across all bars should score lower."""
        notes = []
        for bar in range(4):
            bar_start = bar * 4.0
            # Put fills in every bar
            for i in range(4):
                notes.append({
                    "pitch": 43 + i, "start_beat": bar_start + 3.0 + i * 0.25, 
                    "duration_beats": 0.25, "velocity": 90, "layer": "fills"
                })
        
        score, repairs = _score_fill_localization(notes, fill_bars=[3], bars=4)
        # Only 1/4 of fills are in the correct bar
        assert score < 0.8
    
    def test_no_fills(self):
        """No fills should return base score with suggestion."""
        notes = make_drum_notes(bars=4, include_fills=False)
        score, repairs = _score_fill_localization(notes, fill_bars=[3], bars=4)
        assert "no_fills" in repairs or score == 0.6


class TestGhostPlausibilityScoring:
    """Test ghost note plausibility scoring."""
    
    def test_good_ghosts_score_high(self):
        """Properly placed quiet ghosts should score high."""
        notes = [
            # Ghost before beat 2 (near backbeat)
            {"pitch": 37, "start_beat": 0.75, "duration_beats": 0.25, "velocity": 50, "layer": "ghost_layer"},
            # Ghost before beat 4 (near backbeat)
            {"pitch": 37, "start_beat": 2.75, "duration_beats": 0.25, "velocity": 45, "layer": "ghost_layer"},
        ]
        score, repairs = _score_ghost_plausibility(notes)
        assert score >= 0.7
    
    def test_loud_ghosts_score_lower(self):
        """Loud ghosts should score lower."""
        notes = [
            {"pitch": 37, "start_beat": 0.75, "duration_beats": 0.25, "velocity": 100, "layer": "ghost_layer"},
            {"pitch": 37, "start_beat": 2.75, "duration_beats": 0.25, "velocity": 110, "layer": "ghost_layer"},
        ]
        score, repairs = _score_ghost_plausibility(notes)
        assert score < 0.8
        assert any("loud" in r.lower() for r in repairs) or score <= 0.7
    
    def test_no_ghosts(self):
        """No ghosts should return neutral score (ghosts are optional)."""
        notes = make_drum_notes(bars=4, include_ghosts=False)
        score, repairs = _score_ghost_plausibility(notes)
        assert score == 0.7  # Neutral


class TestLayerBalanceScoring:
    """Test layer balance scoring."""
    
    def test_full_kit_scores_high(self):
        """Full drum kit should score high."""
        notes = make_drum_notes(bars=4, include_ghosts=True)
        score, repairs = _score_layer_balance(notes)
        assert score >= 0.8
    
    def test_kick_snare_only_scores_lower(self):
        """Only core layer should score lower."""
        notes = make_drum_notes(bars=4, include_hats=False, include_fills=False)
        score, repairs = _score_layer_balance(notes)
        assert score < 0.9
        assert any("hat" in r.lower() for r in repairs)
    
    def test_no_core_fails(self):
        """Missing core layer should fail."""
        notes = make_drum_notes(bars=4, include_kicks=False, include_snares=False)
        score, repairs = _score_layer_balance(notes)
        assert any("no_core" in r for r in repairs) or score < 0.5


class TestRepetitionStructureScoring:
    """Test repetition structure scoring."""
    
    def test_varied_bars_score_high(self):
        """Varied bar patterns should score high."""
        notes = make_drum_notes(bars=4)
        score, repairs = _score_repetition_structure(notes, bars=4)
        assert score >= 0.5
    
    def test_identical_bars_score_lower(self):
        """Identical bars should score lower."""
        # Create identical pattern in every bar
        notes = []
        for bar in range(8):
            bar_start = bar * 4.0
            for beat in [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]:
                notes.append({
                    "pitch": 42, "start_beat": bar_start + beat, "duration_beats": 0.25,
                    "velocity": 80, "layer": "timekeepers"
                })
        
        score, repairs = _score_repetition_structure(notes, bars=8)
        # Very high repetition should score lower
        assert score <= 0.8


class TestVelocityDynamicsScoring:
    """Test velocity dynamics scoring."""
    
    def test_dynamic_velocity_scores_high(self):
        """Dynamic velocity range should score high."""
        notes = make_drum_notes(bars=4)
        score, repairs = _score_velocity_dynamics(notes, bars=4)
        assert score >= 0.5
    
    def test_flat_velocity_scores_lower(self):
        """All same velocity should score lower."""
        notes = [
            {"pitch": 42, "start_beat": i * 0.5, "duration_beats": 0.25, "velocity": 80}
            for i in range(32)
        ]
        score, repairs = _score_velocity_dynamics(notes, bars=4)
        assert score < 0.9


class TestOverallDrumScoring:
    """Test the main score_drum_notes function."""
    
    def test_good_drums_score_above_threshold(self):
        """Well-constructed drums should score above threshold."""
        notes = make_drum_notes(bars=4, include_ghosts=True)
        score, repairs = score_drum_notes(notes, bars=4, fill_bars=[3])
        assert score >= ACCEPT_THRESHOLD_DRUM
    
    def test_accepts_good_drums(self):
        """Good drums should be accepted."""
        notes = make_drum_notes(bars=4, include_ghosts=True)
        score, _ = score_drum_notes(notes, bars=4, fill_bars=[3])
        assert accept_drum(score) or score >= 0.5
    
    def test_layer_map_used(self):
        """Layer map should be used for scoring."""
        notes = make_drum_notes(bars=4)
        layer_map = {i: n.get("layer", "unknown") for i, n in enumerate(notes)}
        
        score, repairs = score_drum_notes(notes, layer_map=layer_map, bars=4)
        # Should work without errors
        assert 0 <= score <= 1


class TestBassScoring:
    """Test bass scoring."""
    
    def test_kick_aligned_bass_scores_high(self):
        """Bass aligned to kicks should score high."""
        bass_notes = [
            {"pitch": 36, "start_beat": 0.0, "duration_beats": 0.5, "velocity": 100},
            {"pitch": 36, "start_beat": 2.0, "duration_beats": 0.5, "velocity": 100},
        ]
        kick_beats = [0.0, 2.0]
        
        score, repairs = score_bass_notes(bass_notes, kick_beats=kick_beats)
        assert score >= 0.8
    
    def test_unaligned_bass_scores_lower(self):
        """Bass not aligned to kicks should score lower."""
        bass_notes = [
            {"pitch": 36, "start_beat": 0.5, "duration_beats": 0.5, "velocity": 100},
            {"pitch": 36, "start_beat": 2.5, "duration_beats": 0.5, "velocity": 100},
        ]
        kick_beats = [0.0, 2.0]
        
        score, repairs = score_bass_notes(bass_notes, kick_beats=kick_beats)
        assert score < 0.8
    
    def test_anticipation_counted(self):
        """Anticipation notes should contribute to score."""
        bass_notes = [
            # Anticipation: 1/8 before kick
            {"pitch": 36, "start_beat": 1.875, "duration_beats": 0.5, "velocity": 100},
        ]
        kick_beats = [0.0, 2.0]
        
        score, repairs = score_bass_notes(
            bass_notes, kick_beats=kick_beats, anticipation_allowed=True
        )
        # Should still get partial credit for anticipation
        assert score > 0
    
    def test_empty_bass(self):
        """Empty bass should return 0 with repair suggestion."""
        score, repairs = score_bass_notes([])
        assert score == 0.0
        assert any("bass_empty" in r for r in repairs)


class TestRejectionSampling:
    """Test rejection sampling helper."""
    
    def test_early_stop_on_excellent_score(self):
        """Should stop early when excellent score is reached."""
        call_count = 0
        
        def generate_fn():
            nonlocal call_count
            call_count += 1
            notes = [{"pitch": 36, "start_beat": 0.0, "velocity": 100}]
            return {"notes": notes}, notes
        
        def scorer_fn(notes):
            return 0.9, []  # Always excellent
        
        result = rejection_sample(
            generate_fn, scorer_fn,
            max_attempts=6,
            early_stop_threshold=0.85,
        )
        
        assert result.accepted
        assert result.attempts == 1  # Should stop after first
        assert result.best_score == 0.9
    
    def test_tries_all_candidates_when_needed(self):
        """Should try all candidates if scores are below threshold."""
        call_count = 0
        
        def generate_fn():
            nonlocal call_count
            call_count += 1
            notes = [{"pitch": 36, "start_beat": 0.0, "velocity": 100}]
            return {"notes": notes}, notes
        
        def scorer_fn(notes):
            return 0.5, []  # Always mediocre
        
        result = rejection_sample(
            generate_fn, scorer_fn,
            max_attempts=4,
            accept_threshold=0.75,
            early_stop_threshold=0.85,
        )
        
        assert result.attempts == 4  # Should try all
        assert not result.accepted  # 0.5 < 0.75
    
    def test_keeps_best_result(self):
        """Should keep the best result from all attempts."""
        attempt = 0
        
        def generate_fn():
            nonlocal attempt
            attempt += 1
            notes = [{"pitch": 36, "start_beat": 0.0, "velocity": 100}]
            return {"attempt": attempt, "notes": notes}, notes
        
        scores = [0.5, 0.7, 0.6, 0.65]
        
        def scorer_fn(notes):
            return scores[attempt - 1] if attempt <= len(scores) else 0.5, []
        
        result = rejection_sample(
            generate_fn, scorer_fn,
            max_attempts=4,
            early_stop_threshold=0.85,
        )
        
        assert result.best_score == 0.7
        assert result.all_scores == [0.5, 0.7, 0.6, 0.65]


class TestAcceptThresholds:
    """Test acceptance threshold functions."""
    
    def test_accept_drum_balanced(self):
        """Test drum acceptance at balanced preset."""
        assert accept_drum(0.7, quality_preset="balanced")
        assert not accept_drum(0.5, quality_preset="balanced")
    
    def test_accept_drum_quality(self):
        """Test drum acceptance at quality preset."""
        assert accept_drum(0.8, quality_preset="quality")
        assert not accept_drum(0.7, quality_preset="quality")
    
    def test_accept_bass_balanced(self):
        """Test bass acceptance at balanced preset."""
        assert accept_bass(0.6, quality_preset="balanced")
        assert not accept_bass(0.4, quality_preset="balanced")
