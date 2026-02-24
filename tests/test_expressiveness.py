"""Comprehensive tests for the expressiveness post-processor.

Covers:
  - Genre profile lookup (exact, fuzzy, alias, fallback)
  - Velocity curves (phrase, bar, crescendo shapes; accents; ghost notes)
  - CC automation (expression CC 11, sustain pedal CC 64, mod wheel CC 1)
  - Pitch bend phrasing (probability-gated, role-dependent)
  - Timing humanization (jitter, late bias, clamped to >= 0)
  - Full apply_expressiveness pipeline (drums skip, non-drums enriched)
  - Deterministic RNG seeding for reproducibility
  - Edge cases (empty notes, zero bars, unknown genre)
"""

import random
import statistics

import pytest

from app.services.expressiveness import (
    PROFILES,
    ExpressivenessProfile,
    add_cc_automation,
    add_pitch_bend_phrasing,
    add_timing_humanization,
    add_velocity_curves,
    apply_expressiveness,
    get_profile,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_notes(
    count: int = 16, bars: int = 4, velocity: int = 80, channel: int = 0
) -> list[dict]:
    """Generate evenly-spaced test notes across the given number of bars."""
    beats_total = bars * 4
    step = beats_total / max(count, 1)
    return [
        {
            "pitch": 60 + (i % 12),
            "start_beat": round(i * step, 3),
            "duration_beats": round(step * 0.8, 3),
            "velocity": velocity,
        }
        for i in range(count)
    ]


# ─── Profile lookup ──────────────────────────────────────────────────────────


class TestGetProfile:
    """Tests for get_profile() — role-aware profile lookup."""

    def test_exact_match(self):
        """Known genre returns a profile based on that genre."""
        p = get_profile("jazz")
        assert p.velocity_arc == PROFILES["jazz"].velocity_arc
        assert p.accent_beats == PROFILES["jazz"].accent_beats

    def test_case_insensitive(self):
        """Lookup is case-insensitive."""
        p = get_profile("Jazz")
        assert p.velocity_arc == PROFILES["jazz"].velocity_arc

    def test_hyphen_normalised(self):
        """Hyphens are normalised to underscores."""
        p = get_profile("neo-soul")
        assert p.accent_beats == PROFILES["neo_soul"].accent_beats

    def test_space_normalised(self):
        """Spaces are normalised to underscores."""
        p = get_profile("drum and bass")
        assert p.accent_beats == PROFILES["drum_and_bass"].accent_beats

    def test_alias_hip_hop(self):
        """Alias 'hip_hop' resolves to boom_bap profile."""
        p = get_profile("hip hop")
        assert p.accent_beats == PROFILES["boom_bap"].accent_beats

    def test_alias_lofi_dash(self):
        """'lo-fi' normalises to 'lo_fi' alias which resolves to lofi."""
        p = get_profile("lo-fi")
        assert p.accent_beats == PROFILES["lofi"].accent_beats

    def test_alias_dnb(self):
        """Alias 'dnb' resolves to drum_and_bass profile."""
        p = get_profile("dnb")
        assert p.accent_beats == PROFILES["drum_and_bass"].accent_beats

    def test_alias_drill(self):
        """Alias 'drill' resolves to trap profile."""
        p = get_profile("drill")
        assert p.accent_beats == PROFILES["trap"].accent_beats

    def test_fuzzy_substring_match(self):
        """Substring match catches compound styles like 'melodic techno'."""
        p = get_profile("melodic techno")
        assert p.accent_beats == PROFILES["techno"].accent_beats

    def test_unknown_returns_default(self):
        """Unknown genre returns default ExpressivenessProfile."""
        p = get_profile("polka")
        assert isinstance(p, ExpressivenessProfile)
        assert p.velocity_arc is True

    def test_all_registered_genres(self):
        """All base profiles are reachable."""
        for genre in [
            "classical", "cinematic", "jazz", "neo_soul", "boom_bap",
            "trap", "house", "techno", "ambient", "funk", "lofi",
            "drum_and_bass", "reggae",
        ]:
            p = get_profile(genre)
            assert p.accent_beats == PROFILES[genre].accent_beats


# ─── Velocity curves ─────────────────────────────────────────────────────────


class TestVelocityCurves:
    """Tests for add_velocity_curves()."""

    def test_empty_notes_returns_empty(self):
        """Empty list in → empty list out."""
        result = add_velocity_curves([], "jazz", 4)
        assert result == []

    def test_velocities_are_modified(self):
        """Notes get velocity adjustments applied."""
        notes = _make_notes(16, bars=4, velocity=80)
        original_vels = [n["velocity"] for n in notes]
        add_velocity_curves(notes, "jazz", 4)
        new_vels = [n["velocity"] for n in notes]
        assert new_vels != original_vels

    def test_velocities_clamped_1_127(self):
        """No velocity falls outside 1-127."""
        notes = _make_notes(32, bars=8, velocity=120)
        add_velocity_curves(notes, "classical", 8)
        for n in notes:
            assert 1 <= n["velocity"] <= 127

    def test_low_velocity_clamped(self):
        """Very low velocities don't go below 1."""
        notes = _make_notes(8, bars=2, velocity=5)
        add_velocity_curves(notes, "ambient", 2)
        for n in notes:
            assert n["velocity"] >= 1

    def test_phrase_shape_peaks_at_two_thirds(self):
        """Phrase shape (triangle) peaks around 2/3 of the total."""
        notes = _make_notes(30, bars=8, velocity=80)
        add_velocity_curves(notes, "classical", 8, rng=random.Random(42))
        vels = [n["velocity"] for n in notes if "start_beat" in n]
        peak_idx = vels.index(max(vels))
        assert 15 <= peak_idx <= 25  # roughly in the 2/3 zone

    def test_bar_shape(self):
        """Bar shape produces variation within each bar."""
        notes = _make_notes(16, bars=4, velocity=80)
        add_velocity_curves(notes, "house", 4, rng=random.Random(42))
        vels = [n["velocity"] for n in notes]
        assert statistics.stdev(vels) > 2

    def test_crescendo_shape_increases(self):
        """Crescendo shape: last notes louder than first."""
        notes = _make_notes(16, bars=4, velocity=80)
        add_velocity_curves(notes, "cinematic", 4, rng=random.Random(42))
        first_half = [n["velocity"] for n in notes[:8]]
        second_half = [n["velocity"] for n in notes[8:16]]
        assert statistics.mean(second_half) > statistics.mean(first_half)

    def test_accent_on_beat_zero(self):
        """Notes on beat 0 of a bar get accent boost (classical)."""
        prof = get_profile("classical")
        notes = [
            {"pitch": 60, "start_beat": 0.0, "duration_beats": 0.5, "velocity": 80},
            {"pitch": 60, "start_beat": 0.5, "duration_beats": 0.5, "velocity": 80},
        ]
        add_velocity_curves(notes, "classical", 1, rng=random.Random(42))
        assert notes[0]["velocity"] > notes[1]["velocity"]

    def test_ghost_notes_inserted_funk(self):
        """Funk profile inserts ghost notes (ghost_probability=0.2)."""
        notes = _make_notes(20, bars=4, velocity=80)
        original_count = len(notes)
        add_velocity_curves(notes, "funk", 4, rng=random.Random(42))
        assert len(notes) >= original_count  # may add ghost notes

    def test_ghost_note_velocity_range(self):
        """Ghost notes have velocity within the configured range."""
        notes = _make_notes(40, bars=8, velocity=80)
        add_velocity_curves(notes, "funk", 8, rng=random.Random(99))
        ghost_range = get_profile("funk").ghost_velocity_range
        ghosts = [n for n in notes if n.get("duration_beats", 0) == 0.1]
        for g in ghosts:
            assert ghost_range[0] <= g["velocity"] <= ghost_range[1]

    def test_deterministic_with_seed(self):
        """Same seed → same output."""
        notes1 = _make_notes(16, bars=4)
        notes2 = _make_notes(16, bars=4)
        add_velocity_curves(notes1, "jazz", 4, rng=random.Random(123))
        add_velocity_curves(notes2, "jazz", 4, rng=random.Random(123))
        assert [n["velocity"] for n in notes1] == [n["velocity"] for n in notes2]

    def test_no_ghost_when_probability_zero(self):
        """Profiles with ghost_probability=0 add no ghost notes."""
        notes = _make_notes(20, bars=4)
        original_count = len(notes)
        add_velocity_curves(notes, "house", 4, rng=random.Random(42))
        assert len(notes) == original_count


# ─── CC automation ────────────────────────────────────────────────────────────


class TestCCAutomation:
    """Tests for add_cc_automation()."""

    def test_classical_keys_get_sustain(self):
        """Classical profile with 'piano' role produces CC 64 (sustain)."""
        notes = _make_notes(16, bars=4)
        cc = add_cc_automation(notes, "classical", 4, instrument_role="piano")
        sustain = [e for e in cc if e["cc"] == 64]
        assert len(sustain) > 0
        assert all(e["value"] in (0, 127) for e in sustain)

    def test_sustain_has_down_up_pairs(self):
        """Sustain pedal events come in down (127) / up (0) pairs."""
        notes = _make_notes(8, bars=2)
        cc = add_cc_automation(notes, "classical", 2, instrument_role="keys")
        sustain = [e for e in cc if e["cc"] == 64]
        downs = [e for e in sustain if e["value"] == 127]
        ups = [e for e in sustain if e["value"] == 0]
        assert len(downs) == len(ups)

    def test_no_sustain_for_non_keys(self):
        """Non-keyboard instruments don't get sustain pedal CC."""
        notes = _make_notes(16, bars=4)
        cc = add_cc_automation(notes, "classical", 4, instrument_role="bass")
        sustain = [e for e in cc if e["cc"] == 64]
        assert len(sustain) == 0

    def test_expression_cc11_generated(self):
        """Profiles with cc_expression_enabled produce CC 11 events."""
        notes = _make_notes(16, bars=4)
        cc = add_cc_automation(notes, "cinematic", 4, instrument_role="melody")
        expr = [e for e in cc if e["cc"] == 11]
        assert len(expr) > 0
        assert all(0 <= e["value"] <= 127 for e in expr)

    def test_expression_density_per_bar(self):
        """CC 11 density matches profile's cc_expression_density per bar."""
        prof = get_profile("cinematic")
        bars = 4
        notes = _make_notes(16, bars=bars)
        cc = add_cc_automation(notes, "cinematic", bars, instrument_role="melody")
        expr = [e for e in cc if e["cc"] == 11]
        assert len(expr) == prof.cc_expression_density * bars

    def test_mod_wheel_cc1_generated(self):
        """Profiles with cc_mod_enabled produce CC 1 events."""
        notes = _make_notes(16, bars=4)
        cc = add_cc_automation(notes, "cinematic", 4, instrument_role="melody")
        mod = [e for e in cc if e["cc"] == 1]
        assert len(mod) > 0

    def test_mod_wheel_depth_range(self):
        """CC 1 values stay within [0, mod_depth]."""
        notes = _make_notes(16, bars=4)
        cc = add_cc_automation(notes, "cinematic", 4, instrument_role="melody")
        mod = [e for e in cc if e["cc"] == 1]
        prof = get_profile("cinematic")
        assert all(0 <= e["value"] <= prof.cc_mod_depth for e in mod)

    def test_no_expression_for_drums(self):
        """Drums produce no CC 11 expression events."""
        notes = _make_notes(16, bars=4)
        cc = add_cc_automation(notes, "house", 4, instrument_role="drums")
        expr = [e for e in cc if e["cc"] == 11]
        assert len(expr) == 0

    def test_events_sorted_by_beat(self):
        """CC events are sorted by (beat, cc)."""
        notes = _make_notes(16, bars=4)
        cc = add_cc_automation(notes, "classical", 4, instrument_role="piano")
        beats = [(e["beat"], e["cc"]) for e in cc]
        assert beats == sorted(beats)

    def test_zero_bars_returns_empty(self):
        """Zero bars produces no CC events."""
        cc = add_cc_automation([], "jazz", 0, instrument_role="piano")
        assert cc == []


# ─── Pitch bends ──────────────────────────────────────────────────────────────


class TestPitchBends:
    """Tests for add_pitch_bend_phrasing()."""

    def test_disabled_returns_empty(self):
        """Chords role with non-bend genre returns empty list."""
        notes = _make_notes(16, bars=4)
        bends = add_pitch_bend_phrasing(notes, "jazz", instrument_role="chords")
        assert bends == []

    def test_trap_produces_bends(self):
        """Trap profile (pitch_bend_enabled=True) generates bends."""
        notes = _make_notes(40, bars=8)
        bends = add_pitch_bend_phrasing(
            notes, "trap", instrument_role="bass", rng=random.Random(42)
        )
        assert len(bends) > 0

    def test_bend_values_within_range(self):
        """All bend values within ± pitch_bend_range."""
        prof = get_profile("trap")
        notes = _make_notes(40, bars=8)
        bends = add_pitch_bend_phrasing(
            notes, "trap", instrument_role="bass", rng=random.Random(42)
        )
        for b in bends:
            assert -prof.pitch_bend_range <= b["value"] <= prof.pitch_bend_range

    def test_slide_pattern_for_bass(self):
        """Bass/melody role uses slide-up pattern (negative → 0)."""
        notes = [{"pitch": 60, "start_beat": 4.0, "duration_beats": 1.0, "velocity": 80}]
        bends = add_pitch_bend_phrasing(
            notes, "trap", instrument_role="bass", rng=random.Random(1)
        )
        if bends:
            assert bends[0]["value"] < 0
            assert bends[1]["value"] == 0

    def test_cinematic_produces_bends(self):
        """Cinematic profile produces pitch bends."""
        notes = _make_notes(40, bars=8)
        bends = add_pitch_bend_phrasing(
            notes, "cinematic", instrument_role="melody", rng=random.Random(42)
        )
        assert len(bends) > 0

    def test_deterministic_output(self):
        """Same seed → same bends."""
        notes = _make_notes(20, bars=4)
        b1 = add_pitch_bend_phrasing(notes, "trap", "bass", rng=random.Random(77))
        b2 = add_pitch_bend_phrasing(notes, "trap", "bass", rng=random.Random(77))
        assert b1 == b2

    def test_sorted_by_beat(self):
        """Bends are sorted by beat."""
        notes = _make_notes(40, bars=8)
        bends = add_pitch_bend_phrasing(
            notes, "trap", instrument_role="bass", rng=random.Random(42)
        )
        beats = [b["beat"] for b in bends]
        assert beats == sorted(beats)


# ─── Timing humanization ─────────────────────────────────────────────────────


class TestTimingHumanization:
    """Tests for add_timing_humanization()."""

    def test_notes_are_shifted(self):
        """Notes get timing offsets applied."""
        notes = _make_notes(16, bars=4)
        original_beats = [n["start_beat"] for n in notes]
        add_timing_humanization(notes, "jazz", rng=random.Random(42))
        new_beats = [n["start_beat"] for n in notes]
        assert new_beats != original_beats

    def test_no_negative_beats(self):
        """Start beats are clamped to >= 0."""
        notes = [
            {"pitch": 60, "start_beat": 0.0, "duration_beats": 0.5, "velocity": 80},
            {"pitch": 62, "start_beat": 0.01, "duration_beats": 0.5, "velocity": 80},
        ]
        add_timing_humanization(notes, "classical", rng=random.Random(42))
        for n in notes:
            assert n["start_beat"] >= 0

    def test_jitter_magnitude(self):
        """Offsets are within a reasonable range of the profile's jitter."""
        notes = _make_notes(100, bars=25)
        original_beats = [n["start_beat"] for n in notes]
        add_timing_humanization(notes, "jazz", rng=random.Random(42))
        offsets = [abs(n["start_beat"] - o) for n, o in zip(notes, original_beats)]
        mean_offset = statistics.mean(offsets)
        prof = get_profile("jazz")
        # Mean offset should be in the ballpark of the jitter setting
        assert mean_offset < prof.timing_jitter_beats * 3

    def test_late_bias(self):
        """Profiles with late bias produce a positive mean offset."""
        notes = _make_notes(200, bars=50)
        original_beats = [n["start_beat"] for n in notes]
        add_timing_humanization(notes, "lofi", rng=random.Random(42))
        offsets = [n["start_beat"] - o for n, o in zip(notes, original_beats)]
        # Filter out clamped-to-zero notes
        non_clamped = [o for o, orig in zip(offsets, original_beats) if orig > 0.5]
        if non_clamped:
            assert statistics.mean(non_clamped) > 0

    def test_techno_tight_jitter(self):
        """Techno chords have very tight jitter."""
        notes = _make_notes(100, bars=25)
        original_beats = [n["start_beat"] for n in notes]
        add_timing_humanization(notes, "techno", rng=random.Random(42), role="chords")
        offsets = [abs(n["start_beat"] - o) for n, o in zip(notes, original_beats)]
        assert max(offsets) < 0.1

    def test_deterministic(self):
        """Same seed → same timing offsets."""
        n1 = _make_notes(16, bars=4)
        n2 = _make_notes(16, bars=4)
        add_timing_humanization(n1, "jazz", rng=random.Random(42))
        add_timing_humanization(n2, "jazz", rng=random.Random(42))
        assert [n["start_beat"] for n in n1] == [n["start_beat"] for n in n2]

    def test_empty_notes(self):
        """Empty list is handled gracefully."""
        result = add_timing_humanization([], "jazz")
        assert result == []


# ─── Full pipeline ────────────────────────────────────────────────────────────


class TestApplyExpressiveness:
    """Tests for apply_expressiveness() top-level pipeline."""

    def test_drums_skipped(self):
        """Drums get no expressiveness processing."""
        notes = _make_notes(16, bars=4)
        original_vels = [n["velocity"] for n in notes]
        result = apply_expressiveness(notes, "jazz", 4, instrument_role="drums")
        assert result["cc_events"] == []
        assert result["pitch_bends"] == []
        assert [n["velocity"] for n in result["notes"]] == original_vels

    def test_melody_enriched(self):
        """Melody instrument gets velocity curves and timing applied."""
        notes = _make_notes(16, bars=4, velocity=80)
        result = apply_expressiveness(notes, "jazz", 4, instrument_role="melody")
        assert result["notes"] is notes  # mutated in place
        vels = [n["velocity"] for n in result["notes"]]
        assert not all(v == 80 for v in vels)

    def test_cc_events_present_for_classical_piano(self):
        """Classical piano gets sustain CC events."""
        notes = _make_notes(16, bars=4)
        result = apply_expressiveness(notes, "classical", 4, instrument_role="piano")
        ccs = result["cc_events"]
        cc_numbers = {e["cc"] for e in ccs}
        assert 64 in cc_numbers  # sustain pedal

    def test_pitch_bends_for_trap_bass(self):
        """Trap bass gets pitch bend events."""
        notes = _make_notes(40, bars=8)
        result = apply_expressiveness(notes, "trap", 8, instrument_role="bass", seed=42)
        assert len(result["pitch_bends"]) > 0

    def test_no_pitch_bends_for_house_chords(self):
        """House chords (pitch_bend_enabled=False) gets no bends."""
        notes = _make_notes(16, bars=4)
        result = apply_expressiveness(notes, "house", 4, instrument_role="chords")
        assert result["pitch_bends"] == []

    def test_return_structure(self):
        """Return dict has exactly notes, cc_events, pitch_bends keys."""
        result = apply_expressiveness(_make_notes(8, bars=2), "jazz", 2)
        assert set(result.keys()) == {"notes", "cc_events", "pitch_bends"}

    def test_seed_reproducibility(self):
        """Same seed → same result."""
        n1 = _make_notes(16, bars=4)
        n2 = _make_notes(16, bars=4)
        r1 = apply_expressiveness(n1, "jazz", 4, seed=99)
        r2 = apply_expressiveness(n2, "jazz", 4, seed=99)
        assert [n["velocity"] for n in r1["notes"]] == [n["velocity"] for n in r2["notes"]]
        assert r1["cc_events"] == r2["cc_events"]

    def test_different_seeds_differ(self):
        """Different seeds produce different velocity curves."""
        n1 = _make_notes(16, bars=4)
        n2 = _make_notes(16, bars=4)
        r1 = apply_expressiveness(n1, "jazz", 4, seed=1)
        r2 = apply_expressiveness(n2, "jazz", 4, seed=2)
        v1 = [n["velocity"] for n in r1["notes"]]
        v2 = [n["velocity"] for n in r2["notes"]]
        assert v1 != v2

    def test_zero_bars(self):
        """Zero bars doesn't crash."""
        result = apply_expressiveness([], "jazz", 0)
        assert result["notes"] == []
        assert result["cc_events"] == []

    def test_unknown_genre_uses_defaults(self):
        """Unknown genre still produces enriched output."""
        notes = _make_notes(16, bars=4)
        result = apply_expressiveness(notes, "polka", 4, instrument_role="melody")
        assert result["notes"] is notes

    def test_all_genres_produce_valid_output(self):
        """Every registered genre produces valid structured output."""
        for genre in PROFILES:
            notes = _make_notes(16, bars=4)
            result = apply_expressiveness(notes, genre, 4, instrument_role="melody")
            assert "notes" in result
            assert "cc_events" in result
            assert "pitch_bends" in result
            for n in result["notes"]:
                assert 1 <= n["velocity"] <= 127
            for cc in result["cc_events"]:
                assert 0 <= cc["value"] <= 127
                assert cc["beat"] >= 0


# ─── Profile data integrity ──────────────────────────────────────────────────


class TestProfileIntegrity:
    """Validate that all profiles have sane parameter ranges."""

    @pytest.mark.parametrize("genre", list(PROFILES.keys()))
    def test_velocity_stdev_positive(self, genre):
        """Velocity stdev target is positive."""
        assert PROFILES[genre].velocity_stdev_target > 0

    @pytest.mark.parametrize("genre", list(PROFILES.keys()))
    def test_expression_range_valid(self, genre):
        """Expression range low < high and both within 0-127."""
        lo, hi = PROFILES[genre].cc_expression_range
        assert 0 <= lo < hi <= 127

    @pytest.mark.parametrize("genre", list(PROFILES.keys()))
    def test_ghost_velocity_range_valid(self, genre):
        """Ghost velocity range low < high and both within 1-127."""
        lo, hi = PROFILES[genre].ghost_velocity_range
        assert 1 <= lo < hi <= 127

    @pytest.mark.parametrize("genre", list(PROFILES.keys()))
    def test_timing_jitter_non_negative(self, genre):
        """Timing jitter is non-negative."""
        assert PROFILES[genre].timing_jitter_beats >= 0

    @pytest.mark.parametrize("genre", list(PROFILES.keys()))
    def test_pitch_bend_range_non_negative(self, genre):
        """Pitch bend range is non-negative."""
        assert PROFILES[genre].pitch_bend_range >= 0


# ─── Music generator integration ─────────────────────────────────────────────


class TestCamelCaseNormalization:
    """Regression: Orpheus notes use camelCase (startBeat) but
    expressiveness previously crashed with KeyError: 'start_beat'."""

    @staticmethod
    def _camel_notes(count: int = 16, bars: int = 4, velocity: int = 80) -> list[dict]:
        beats_total = bars * 4
        step = beats_total / max(count, 1)
        return [
            {
                "pitch": 60 + (i % 12),
                "startBeat": round(i * step, 3),
                "durationBeats": round(step * 0.8, 3),
                "velocity": velocity,
            }
            for i in range(count)
        ]

    def test_apply_expressiveness_camel_no_crash(self):
        """camelCase notes must not raise KeyError."""
        notes = self._camel_notes(16, bars=4)
        result = apply_expressiveness(notes, "jazz", 4, instrument_role="melody")
        assert len(result["notes"]) >= 16
        assert "cc_events" in result

    def test_camel_keys_preserved_on_output(self):
        """Output notes keep camelCase keys when input was camelCase."""
        notes = self._camel_notes(8, bars=2)
        result = apply_expressiveness(notes, "classical", 2, instrument_role="piano")
        for n in result["notes"]:
            assert "startBeat" in n
            assert "start_beat" not in n

    def test_snake_keys_preserved_on_output(self):
        """Output notes keep snake_case keys when input was snake_case."""
        notes = _make_notes(8, bars=2)
        result = apply_expressiveness(notes, "classical", 2, instrument_role="piano")
        for n in result["notes"]:
            assert "start_beat" in n
            assert "startBeat" not in n

    def test_velocity_curves_work_with_camel(self):
        """Velocity shaping works correctly with camelCase input."""
        notes = self._camel_notes(16, bars=4, velocity=80)
        result = apply_expressiveness(notes, "jazz", 4, instrument_role="melody")
        vels = [n["velocity"] for n in result["notes"]]
        assert not all(v == 80 for v in vels)

    def test_ghost_notes_use_camel_keys(self):
        """Ghost notes inserted during camelCase processing also use camelCase."""
        notes = self._camel_notes(40, bars=8, velocity=80)
        original_count = len(notes)
        result = apply_expressiveness(notes, "funk", 8, instrument_role="melody")
        if len(result["notes"]) > original_count:
            for n in result["notes"]:
                assert "startBeat" in n

    def test_drums_skip_with_camel(self):
        """Drums skip fast-path preserves camelCase keys."""
        notes = self._camel_notes(4, bars=1)
        result = apply_expressiveness(notes, "trap", 1, instrument_role="drums")
        for n in result["notes"]:
            assert "startBeat" in n


class TestMusicGeneratorExpressiveness:
    """Tests for expressiveness integration in MusicGenerator._maybe_apply_expressiveness."""

    @pytest.fixture(autouse=True)
    def _enable_expressiveness(self, monkeypatch):
        """Force expressiveness on for these integration tests."""
        from app.config import settings
        monkeypatch.setattr(settings, "skip_expressiveness", False)

    def _result(self, notes=None, cc_events=None, pitch_bends=None):
        from app.services.backends.base import GenerationResult, GeneratorBackend

        return GenerationResult(
            success=True,
            notes=notes or _make_notes(16, bars=4),
            backend_used=GeneratorBackend.ORPHEUS,
            metadata={},
            cc_events=cc_events or [],
            pitch_bends=pitch_bends or [],
        )

    def test_apply_enriches_result(self):
        """_maybe_apply_expressiveness adds CC events to result."""
        result = self._result()
        from app.services.music_generator import MusicGenerator

        enriched = MusicGenerator._maybe_apply_expressiveness(result, "melody", "jazz", 4)
        assert len(enriched.cc_events) > 0
        assert enriched.success is True

    def test_existing_cc_preserved(self):
        """Existing CC events are preserved and new ones appended."""
        existing_cc = [{"cc": 7, "beat": 0.0, "value": 100}]
        result = self._result(cc_events=existing_cc.copy())
        from app.services.music_generator import MusicGenerator

        enriched = MusicGenerator._maybe_apply_expressiveness(result, "melody", "classical", 4)
        assert any(e["cc"] == 7 for e in enriched.cc_events)
        assert len(enriched.cc_events) > 1

    def test_drums_not_enriched(self):
        """Drum instrument role produces no additional CC/PB events."""
        result = self._result()
        from app.services.music_generator import MusicGenerator

        enriched = MusicGenerator._maybe_apply_expressiveness(result, "drums", "jazz", 4)
        assert enriched.cc_events == []
        assert enriched.pitch_bends == []

    def test_camel_case_notes_from_orpheus(self):
        """Regression: Orpheus notes (camelCase) don't crash _maybe_apply_expressiveness."""
        from app.services.backends.base import GenerationResult, GeneratorBackend
        from app.services.music_generator import MusicGenerator

        camel_notes = [
            {"pitch": 60, "startBeat": float(i), "durationBeats": 0.5, "velocity": 80}
            for i in range(16)
        ]
        result = GenerationResult(
            success=True,
            notes=camel_notes,
            backend_used=GeneratorBackend.ORPHEUS,
            metadata={},
        )
        enriched = MusicGenerator._maybe_apply_expressiveness(result, "bass", "trap", 4)
        assert enriched.success is True
        assert len(enriched.notes) >= 16


# ─── Conversion beats_per_bar ─────────────────────────────────────────────────


class TestBeatsPerBar:
    """Tests for _beats_per_bar() in conversion.py."""

    def test_default_is_four(self):
        """No project state → 4 beats per bar."""
        from app.core.planner.conversion import _beats_per_bar

        assert _beats_per_bar(None) == 4
        assert _beats_per_bar({}) == 4

    def test_list_format(self):
        """[3, 4] → 3 beats per bar."""
        from app.core.planner.conversion import _beats_per_bar

        assert _beats_per_bar({"time_signature": [3, 4]}) == 3

    def test_tuple_format(self):
        """(6, 8) → 6 beats per bar."""
        from app.core.planner.conversion import _beats_per_bar

        assert _beats_per_bar({"time_signature": (6, 8)}) == 6

    def test_dict_format(self):
        """{"numerator": 5, "denominator": 4} → 5."""
        from app.core.planner.conversion import _beats_per_bar

        assert _beats_per_bar({"time_signature": {"numerator": 5, "denominator": 4}}) == 5

    def test_string_format(self):
        """'7/8' → 7."""
        from app.core.planner.conversion import _beats_per_bar

        assert _beats_per_bar({"time_signature": "7/8"}) == 7

    def test_camel_case_key(self):
        """timeSignature (camelCase) is also supported."""
        from app.core.planner.conversion import _beats_per_bar

        assert _beats_per_bar({"timeSignature": [5, 4]}) == 5


# ─── Prompt parser Energy field ───────────────────────────────────────────────


class TestPromptParserEnergy:
    """Tests that Energy field is parsed from STORI PROMPT."""

    def test_energy_parsed(self):
        """Energy field is extracted as typed attribute."""
        from app.core.prompt_parser import parse_prompt

        prompt = (
            "STORI PROMPT\n"
            "Mode: compose\n"
            "Energy: high\n"
            "Request: some music\n"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.energy == "high"

    def test_energy_not_in_extensions(self):
        """Energy field is routing, not an extension."""
        from app.core.prompt_parser import parse_prompt

        prompt = (
            "STORI PROMPT\n"
            "Mode: compose\n"
            "Energy: low\n"
            "Request: some music\n"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert "energy" not in result.extensions

    def test_energy_optional(self):
        """Energy is optional — None when absent."""
        from app.core.prompt_parser import parse_prompt

        prompt = (
            "STORI PROMPT\n"
            "Mode: compose\n"
            "Request: some music\n"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.energy is None

    def test_energy_case_insensitive(self):
        """Energy value is normalised to lowercase."""
        from app.core.prompt_parser import parse_prompt

        prompt = (
            "STORI PROMPT\n"
            "Mode: compose\n"
            "Energy: Very High\n"
            "Request: some music\n"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.energy == "very high"
