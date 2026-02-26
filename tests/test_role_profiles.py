"""Tests for heuristic-derived role profiles.

Covers:
  - Module-level loading from heuristics_v2.json
  - RoleProfile field accuracy (medians from 222K compositions)
  - Fuzzy alias lookup (melody→lead, piano→chords, etc.)
  - Unknown-role returns None
  - prompt_block() rendering for LLM injection
  - Orpheus conditioning fields (complexity, density_hint)
  - Role-aware expressiveness profile modulation
"""
from __future__ import annotations

import pytest

from app.data.role_profiles import (
    ROLE_PROFILES,
    RoleProfile,
    get_role_profile,
    _ROLE_ALIASES,
)


class TestRoleProfileLoading:
    """Verify heuristics data loads correctly at module level."""

    def test_expected_roles_loaded(self) -> None:

        assert set(ROLE_PROFILES.keys()) == {"lead", "bass", "chords", "pads", "drums"}

    def test_track_counts_plausible(self) -> None:

        for name, profile in ROLE_PROFILES.items():
            assert profile.track_count > 10_000, (
                f"Role '{name}' has suspiciously low track count: {profile.track_count}"
            )

    def test_lead_has_most_tracks(self) -> None:

        assert ROLE_PROFILES["lead"].track_count > ROLE_PROFILES["bass"].track_count

    def test_profiles_are_frozen(self) -> None:

        with pytest.raises(AttributeError):
            ROLE_PROFILES["lead"].rest_ratio = 0.99  # type: ignore[misc]


class TestRoleProfileFields:
    """Spot-check median values against known heuristics."""

    def test_bass_is_low_register(self) -> None:

        bass = ROLE_PROFILES["bass"]
        assert bass.register_mean_pitch < 50
        assert bass.register_low_ratio > 0.8

    def test_lead_is_mostly_monophonic(self) -> None:

        lead = ROLE_PROFILES["lead"]
        assert lead.pct_monophonic > 0.9

    def test_chords_are_polyphonic(self) -> None:

        chords = ROLE_PROFILES["chords"]
        assert chords.pct_monophonic < 0.7

    def test_drums_high_repeat_ratio(self) -> None:

        drums = ROLE_PROFILES["drums"]
        assert drums.repeat_ratio > 0.7

    def test_velocity_ranges_sensible(self) -> None:

        for name, profile in ROLE_PROFILES.items():
            assert 30 < profile.velocity_mean < 127, f"{name} velocity_mean out of range"
            assert profile.velocity_range >= 0, f"{name} velocity_range negative"

    def test_syncopation_within_zero_one(self) -> None:

        for name, profile in ROLE_PROFILES.items():
            assert 0 <= profile.syncopation_ratio <= 1, f"{name} syncopation out of range"


class TestFuzzyLookup:
    """Test get_role_profile with aliases and edge cases."""

    @pytest.mark.parametrize(
        "alias,expected_role",
        [
            ("melody", "lead"),
            ("Melody", "lead"),
            ("vocal", "lead"),
            ("guitar", "lead"),
            ("violin", "lead"),
            ("piano", "chords"),
            ("keys", "chords"),
            ("organ", "chords"),
            ("pad", "pads"),
            ("strings", "pads"),
            ("drum", "drums"),
            ("percussion", "drums"),
            ("bass", "bass"),
            ("Bass", "bass"),
        ],
    )
    def test_alias_resolution(self, alias: str, expected_role: str) -> None:

        profile = get_role_profile(alias)
        assert profile is not None, f"Alias '{alias}' should resolve"
        assert profile.role == expected_role

    def test_canonical_names(self) -> None:

        for name in ("lead", "bass", "chords", "pads", "drums"):
            assert get_role_profile(name) is not None

    def test_unknown_returns_none(self) -> None:

        assert get_role_profile("theremin") is None
        assert get_role_profile("") is None

    def test_all_aliases_resolve(self) -> None:

        for alias, canon in _ROLE_ALIASES.items():
            if canon == "other":
                continue
            profile = get_role_profile(alias)
            assert profile is not None, f"Alias '{alias}' → '{canon}' should resolve"
            assert profile.role == canon


class TestPromptBlock:
    """Test prompt_block() rendering for LLM system prompts."""

    def test_prompt_block_contains_role_name(self) -> None:

        lead = ROLE_PROFILES["lead"]
        block = lead.prompt_block()
        assert "LEAD" in block
        assert "MUSICAL DNA" in block

    def test_prompt_block_contains_key_sections(self) -> None:

        block = ROLE_PROFILES["bass"].prompt_block()
        for section in ("DENSITY", "PHRASING", "INTERVALS", "ARTICULATION", "DYNAMICS"):
            assert section in block, f"Missing section: {section}"

    def test_prompt_block_contains_track_count(self) -> None:

        pads = ROLE_PROFILES["pads"]
        block = pads.prompt_block()
        assert str(pads.track_count) in block.replace(",", "")

    def test_prompt_block_polyphony_description(self) -> None:

        chords_block = ROLE_PROFILES["chords"].prompt_block()
        assert "mono/polyphonic" in chords_block or "polyphonic" in chords_block.lower()

        lead_block = ROLE_PROFILES["lead"].prompt_block()
        assert "monophonic" in lead_block


class TestOrpheusConditioning:
    """Test derived Orpheus conditioning fields."""

    def test_complexity_in_range(self) -> None:

        for name, profile in ROLE_PROFILES.items():
            assert 0 <= profile.storpheus_complexity <= 1.0, (
                f"{name} storpheus_complexity out of [0, 1]"
            )

    def test_density_hint_valid(self) -> None:

        valid_hints = {"sparse", "moderate", "dense"}
        for name, profile in ROLE_PROFILES.items():
            assert profile.storpheus_density_hint in valid_hints, (
                f"{name} has invalid density_hint: {profile.storpheus_density_hint}"
            )

    def test_drums_low_complexity(self) -> None:

        drums = ROLE_PROFILES["drums"]
        assert drums.storpheus_complexity < 0.2

    def test_chords_moderate_complexity(self) -> None:

        chords = ROLE_PROFILES["chords"]
        assert chords.storpheus_complexity > 0.3


class TestRoleAwareExpressiveness:
    """Test that get_profile with role parameter produces role-modulated results."""

    def test_lead_gets_pitch_bend_enabled(self) -> None:

        from app.services.expressiveness import get_profile
        prof = get_profile("jazz", "lead")
        assert prof.pitch_bend_enabled is True

    def test_bass_gets_late_bias(self) -> None:

        from app.services.expressiveness import get_profile
        prof = get_profile("jazz", "bass")
        assert prof.timing_late_bias > 0

    def test_pads_get_expression_cc(self) -> None:

        from app.services.expressiveness import get_profile
        prof = get_profile("ambient", "pads")
        assert prof.cc_expression_enabled is True

    def test_chords_get_sustain_cc(self) -> None:

        from app.services.expressiveness import get_profile
        prof = get_profile("jazz", "chords")
        assert prof.cc_sustain_enabled is True

    def test_velocity_stdev_matches_heuristic(self) -> None:

        from app.services.expressiveness import get_profile
        rp = get_role_profile("lead")
        assert rp is not None
        prof = get_profile("jazz", "lead")
        assert prof.velocity_stdev_target == max(8.0, rp.velocity_stdev)

    def test_unknown_role_falls_back_to_base(self) -> None:

        from app.services.expressiveness import get_profile
        prof = get_profile("jazz", "theremin")
        assert prof is not None
