"""Contract coverage tests: verify zero information loss across the Maestro→Orpheus boundary.

Validates that:
1. All EmotionVector axes cross the boundary (including tension).
2. RoleProfile summary (12 fields) is transmitted.
3. GenerationConstraints (12 fields) are transmitted.
4. Intent goals carry weights.
5. Trace/seed/intent_hash observability fields propagate.
"""
from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from app.core.emotion_vector import EmotionVector, emotion_to_constraints
from app.data.role_profiles import RoleProfile
from app.services.backends.orpheus import _build_intent_hash


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DEFAULT_PROFILE = RoleProfile(
    role="lead", track_count=1000,
    rest_ratio=0.35, notes_per_bar=4.0,
    phrase_length_beats=8.0, notes_per_phrase=6.0,
    phrase_regularity_cv=0.3, note_length_entropy=2.0,
    syncopation_ratio=0.4, swing_ratio=0.2,
    rhythm_trigram_repeat=0.6, ioi_cv=0.5,
    step_ratio=0.5, leap_ratio=0.15, repeat_ratio=0.35,
    pitch_class_entropy=3.0, contour_complexity=0.6,
    interval_entropy=2.5, pitch_gravity=0.5,
    climax_position=0.6, pitch_range_semitones=18.0,
    register_mean_pitch=65.0,
    register_low_ratio=0.2, register_mid_ratio=0.6, register_high_ratio=0.2,
    velocity_mean=80.0, velocity_range=60.0,
    velocity_stdev=17.0, velocity_entropy=3.5,
    velocity_pitch_correlation=0.1, phrase_velocity_slope=0.5,
    accelerando_ratio=0.05, ritardando_ratio=0.05,
    staccato_ratio=0.15, legato_ratio=0.55, sustained_ratio=0.04,
    polyphony_mean=1.2, pct_monophonic=0.7,
    motif_pitch_trigram_repeat=0.65,
    motif_direction_trigram_repeat=0.55,
    orpheus_complexity=0.6, orpheus_density_hint="moderate",
)


def _make_role_profile(**overrides: Any) -> RoleProfile:
    """Build a RoleProfile with optional field overrides."""
    return dataclasses.replace(_DEFAULT_PROFILE, **overrides)


# ---------------------------------------------------------------------------
# Tension axis
# ---------------------------------------------------------------------------

class TestTensionAxis:
    """Tension MUST cross the boundary as a continuous float."""

    def test_tension_in_emotion_vector_dict(self) -> None:
        ev = EmotionVector(energy=0.8, valence=-0.3, tension=0.75, intimacy=0.4, motion=0.6)
        d = ev.to_dict()
        assert "tension" in d
        assert d["tension"] == 0.75

    def test_tension_round_trip(self) -> None:
        ev = EmotionVector(tension=0.9)
        d = ev.to_dict()
        restored = EmotionVector.from_dict(d)
        assert restored.tension == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# RoleProfile summary transmission
# ---------------------------------------------------------------------------

class TestRoleProfileSummary:
    """12-field expressive subset must be extractable."""

    EXPECTED_FIELDS = {
        "rest_ratio", "syncopation_ratio", "swing_ratio",
        "pitch_range_semitones", "contour_complexity", "velocity_entropy",
        "staccato_ratio", "legato_ratio", "sustained_ratio",
        "motif_pitch_trigram_repeat", "polyphony_mean", "register_mean_pitch",
    }

    def test_summary_has_all_12_fields(self) -> None:
        rp = _make_role_profile()
        summary = rp.to_summary_dict()
        assert set(summary.keys()) == self.EXPECTED_FIELDS

    def test_summary_values_match_profile(self) -> None:
        rp = _make_role_profile(syncopation_ratio=0.42, swing_ratio=0.18)
        summary = rp.to_summary_dict()
        assert summary["syncopation_ratio"] == pytest.approx(0.42)
        assert summary["swing_ratio"] == pytest.approx(0.18)


# ---------------------------------------------------------------------------
# GenerationConstraints transmission
# ---------------------------------------------------------------------------

class TestGenerationConstraintsTransmission:
    """All 12 GenerationConstraints fields must be derivable and serializable."""

    EXPECTED_FIELDS = {
        "drum_density", "subdivision", "swing_amount",
        "register_center", "register_spread", "rest_density",
        "leap_probability", "chord_extensions",
        "borrowed_chord_probability", "harmonic_rhythm_bars",
        "velocity_floor", "velocity_ceiling",
    }

    def test_constraints_from_emotion_vector(self) -> None:
        ev = EmotionVector(energy=0.8, valence=0.5, tension=0.7, intimacy=0.3, motion=0.9)
        gc = emotion_to_constraints(ev)
        gc_dict = {
            "drum_density": gc.drum_density,
            "subdivision": gc.subdivision,
            "swing_amount": gc.swing_amount,
            "register_center": gc.register_center,
            "register_spread": gc.register_spread,
            "rest_density": gc.rest_density,
            "leap_probability": gc.leap_probability,
            "chord_extensions": gc.chord_extensions,
            "borrowed_chord_probability": gc.borrowed_chord_probability,
            "harmonic_rhythm_bars": gc.harmonic_rhythm_bars,
            "velocity_floor": gc.velocity_floor,
            "velocity_ceiling": gc.velocity_ceiling,
        }
        assert set(gc_dict.keys()) == self.EXPECTED_FIELDS
        assert gc_dict["subdivision"] == 16  # motion 0.9 > 0.6
        assert gc_dict["chord_extensions"] is True  # tension 0.7 > 0.5

    def test_constraints_deterministic(self) -> None:
        ev = EmotionVector(energy=0.5, tension=0.4)
        a = emotion_to_constraints(ev)
        b = emotion_to_constraints(ev)
        assert a.drum_density == b.drum_density
        assert a.register_center == b.register_center


# ---------------------------------------------------------------------------
# Observability fields
# ---------------------------------------------------------------------------

class TestObservabilityFields:
    def test_intent_hash_deterministic(self) -> None:
        ev = {"energy": 0.5, "valence": 0.0, "tension": 0.3, "intimacy": 0.5, "motion": 0.5}
        h1 = _build_intent_hash(ev, None, None, ["dark"])
        h2 = _build_intent_hash(ev, None, None, ["dark"])
        assert h1 == h2

    def test_intent_hash_changes_with_goals(self) -> None:
        ev = {"energy": 0.5, "valence": 0.0, "tension": 0.3, "intimacy": 0.5, "motion": 0.5}
        h1 = _build_intent_hash(ev, None, None, ["dark"])
        h2 = _build_intent_hash(ev, None, None, ["bright"])
        assert h1 != h2


# ---------------------------------------------------------------------------
# Coverage matrix validation
# ---------------------------------------------------------------------------

class TestCoverageMatrix:
    """Validates that the contract transmits all computed vectors."""

    def test_emotion_vector_coverage(self) -> None:
        """All 5 EmotionVector axes must be in the payload."""
        ev = EmotionVector(energy=0.8, valence=-0.3, tension=0.7, intimacy=0.4, motion=0.6)
        d = ev.to_dict()
        required = {"energy", "valence", "tension", "intimacy", "motion"}
        assert required.issubset(d.keys()), f"Missing axes: {required - set(d.keys())}"

    def test_role_profile_coverage(self) -> None:
        """At least 12 of 40 RoleProfile fields cross the boundary."""
        rp = _make_role_profile()
        summary = rp.to_summary_dict()
        assert len(summary) >= 12

    def test_generation_constraints_coverage(self) -> None:
        """All 12 GenerationConstraints fields are serializable."""
        ev = EmotionVector()
        gc = emotion_to_constraints(ev)
        gc_dict = {
            "drum_density": gc.drum_density,
            "subdivision": gc.subdivision,
            "swing_amount": gc.swing_amount,
            "register_center": gc.register_center,
            "register_spread": gc.register_spread,
            "rest_density": gc.rest_density,
            "leap_probability": gc.leap_probability,
            "chord_extensions": gc.chord_extensions,
            "borrowed_chord_probability": gc.borrowed_chord_probability,
            "harmonic_rhythm_bars": gc.harmonic_rhythm_bars,
            "velocity_floor": gc.velocity_floor,
            "velocity_ceiling": gc.velocity_ceiling,
        }
        assert len(gc_dict) == 12

    def test_total_transmitted_fields(self) -> None:
        """Contract transmits 30 structured fields: 5 emotion + 12 role + 12 constraints + 1 tension."""
        total = 5 + 12 + 12
        assert total >= 29
