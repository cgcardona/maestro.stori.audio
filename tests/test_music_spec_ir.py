"""Tests for app/core/music_spec_ir.py.

Covers: default_drum_spec, default_bass_spec, default_harmonic_spec,
default_melody_spec, build_full_music_spec, apply_policy_to_music_spec,
_default_chord_palette, QUALITY_PRESETS, GlobalSpec, SectionMapEntry.
"""
from __future__ import annotations

import pytest
from maestro.core.music_spec_ir import (
    DrumSpec,
    DrumLayerSpec,
    DensityTarget,
    DrumConstraints,
    BassSpec,
    BassDensityTarget,
    BassNoteLength,
    HarmonicSpec,
    ChordScheduleEntry,
    MelodySpec,
    MusicSpec,
    GlobalSpec,
    SectionMapEntry,
    default_drum_spec,
    default_bass_spec,
    default_harmonic_spec,
    default_melody_spec,
    build_full_music_spec,
    apply_policy_to_music_spec,
    QUALITY_PRESETS,
)


# ---------------------------------------------------------------------------
# default_drum_spec
# ---------------------------------------------------------------------------


class TestDefaultDrumSpec:

    def test_trap_style(self) -> None:

        spec = default_drum_spec(style="trap", bars=4)
        assert spec.style == "trap"
        assert spec.groove_template == "trap_straight"
        assert "core" in spec.layers
        assert "timekeepers" in spec.layers
        assert "fills" in spec.layers

    def test_boom_bap_style(self) -> None:

        spec = default_drum_spec(style="boom_bap", bars=8)
        assert spec.groove_template == "boom_bap_swing"

    def test_house_style(self) -> None:

        spec = default_drum_spec(style="house", bars=4)
        assert spec.groove_template == "house_four_on_floor"

    def test_triplet_style(self) -> None:

        spec = default_drum_spec(style="trap_triplet", bars=4)
        assert spec.groove_template == "trap_triplet"

    def test_fill_bars_4_bar(self) -> None:

        spec = default_drum_spec(style="trap", bars=4)
        assert spec.constraints.fill_bars == [3]

    def test_fill_bars_8_bar(self) -> None:

        spec = default_drum_spec(style="trap", bars=8)
        assert 3 in spec.constraints.fill_bars
        assert 7 in spec.constraints.fill_bars

    def test_fill_bars_1_bar(self) -> None:

        spec = default_drum_spec(style="trap", bars=1)
        assert spec.constraints.fill_bars == [0]

    def test_salience_weight(self) -> None:

        spec = default_drum_spec()
        assert isinstance(spec.salience_weight, dict)
        assert len(spec.salience_weight) > 0


# ---------------------------------------------------------------------------
# default_bass_spec
# ---------------------------------------------------------------------------


class TestDefaultBassSpec:

    def test_default(self) -> None:

        spec = default_bass_spec()
        assert spec.style == "trap"
        assert spec.register == "low"
        assert spec.root_octave == 2

    def test_custom_style(self) -> None:

        spec = default_bass_spec(style="house")
        assert spec.style == "house"


# ---------------------------------------------------------------------------
# default_harmonic_spec
# ---------------------------------------------------------------------------


class TestDefaultHarmonicSpec:

    def test_c_minor(self) -> None:

        spec = default_harmonic_spec(key="C", scale="natural_minor", bars=8)
        assert len(spec.chord_schedule) == 4  # one chord per 2 bars
        assert spec.chord_palette[0] == "Cm"

    def test_custom_chords(self) -> None:

        spec = default_harmonic_spec(
            key="A", chords=["Am", "F", "C", "G"], bars=8
        )
        assert spec.chord_palette == ["Am", "F", "C", "G"]

    def test_major_key(self) -> None:

        spec = default_harmonic_spec(key="G", scale="major", bars=4)
        assert spec.chord_palette[0] == "G"

    def test_tension_points(self) -> None:

        spec = default_harmonic_spec(bars=16)
        assert 7 in spec.tension_points

    def test_unknown_key_fallback(self) -> None:

        spec = default_harmonic_spec(key="Z", scale="natural_minor", bars=4)
        assert isinstance(spec.chord_palette, list)
        assert len(spec.chord_palette) > 0


# ---------------------------------------------------------------------------
# default_melody_spec
# ---------------------------------------------------------------------------


class TestDefaultMelodySpec:

    def test_default(self) -> None:

        spec = default_melody_spec(bars=16)
        assert spec.motif_length_bars == 2
        assert 4 in spec.phrase_boundaries
        assert 16 in spec.phrase_boundaries

    def test_short(self) -> None:

        spec = default_melody_spec(bars=4)
        assert 4 in spec.phrase_boundaries


# ---------------------------------------------------------------------------
# build_full_music_spec
# ---------------------------------------------------------------------------


class TestBuildFullMusicSpec:

    def test_all_included(self) -> None:

        spec = build_full_music_spec(
            style="trap", tempo=120, bars=16, key="Cm"
        )
        assert spec.drum_spec is not None
        assert spec.bass_spec is not None
        assert spec.harmonic_spec is not None
        assert spec.melody_spec is not None
        assert spec.global_spec.tempo == 120
        assert spec.global_spec.bars == 16
        assert spec.global_spec.key == "Cm"

    def test_drums_only(self) -> None:

        spec = build_full_music_spec(
            include_drums=True, include_bass=False,
            include_harmony=False, include_melody=False,
        )
        assert spec.drum_spec is not None
        assert spec.bass_spec is None
        assert spec.harmonic_spec is None
        assert spec.melody_spec is None

    def test_section_map_long(self) -> None:

        spec = build_full_music_spec(bars=16)
        assert spec.global_spec.section_map is not None
        assert len(spec.global_spec.section_map) == 3  # intro, main, outro

    def test_section_map_medium(self) -> None:

        spec = build_full_music_spec(bars=8)
        assert spec.global_spec.section_map is not None
        assert len(spec.global_spec.section_map) == 2  # intro, main

    def test_section_map_short(self) -> None:

        spec = build_full_music_spec(bars=4)
        # No section map for short songs
        assert spec.global_spec.section_map is None or len(spec.global_spec.section_map) == 0

    def test_minor_key_scale(self) -> None:

        spec = build_full_music_spec(key="Am")
        assert spec.global_spec.scale == "natural_minor"

    def test_major_key_scale(self) -> None:

        spec = build_full_music_spec(key="C")
        assert spec.global_spec.scale == "major"


# ---------------------------------------------------------------------------
# apply_policy_to_music_spec
# ---------------------------------------------------------------------------


class TestApplyPolicyToMusicSpec:

    def test_high_density(self) -> None:

        spec = build_full_music_spec(style="trap", bars=4)
        assert spec.drum_spec is not None
        original_max = spec.drum_spec.layers["timekeepers"].density_target.max_hits_per_bar
        assert original_max is not None
        result = apply_policy_to_music_spec(spec, density=0.9)
        assert result.drum_spec is not None
        result_max = result.drum_spec.layers["timekeepers"].density_target.max_hits_per_bar
        assert result_max is not None
        assert result_max >= original_max

    def test_low_density(self) -> None:

        spec = build_full_music_spec(style="trap", bars=4)
        assert spec.drum_spec is not None
        original_min = spec.drum_spec.layers["core"].density_target.min_hits_per_bar
        assert original_min is not None
        result = apply_policy_to_music_spec(spec, density=0.1)
        assert result.drum_spec is not None
        result_min = result.drum_spec.layers["core"].density_target.min_hits_per_bar
        assert result_min is not None
        assert result_min <= original_min

    def test_complexity(self) -> None:

        spec = build_full_music_spec(style="trap", bars=4)
        result = apply_policy_to_music_spec(spec, complexity=0.8)
        assert result.drum_spec is not None
        for layer in result.drum_spec.layers.values():
            assert layer.variation_rate > 0

    def test_groove_override(self) -> None:

        spec = build_full_music_spec(style="trap", bars=4)
        result = apply_policy_to_music_spec(spec, groove="boom_bap_swing")
        assert result.drum_spec is not None
        assert result.drum_spec.groove_template == "boom_bap_swing"
        assert result.global_spec.swing == 0.5

    def test_triplet_groove(self) -> None:

        spec = build_full_music_spec(style="trap", bars=4)
        result = apply_policy_to_music_spec(spec, groove="trap_triplet")
        assert result.global_spec.swing == 0.3

    def test_no_drums_does_not_crash(self) -> None:

        spec = build_full_music_spec(include_drums=False)
        result = apply_policy_to_music_spec(spec, density=0.9, complexity=0.5)
        assert result.drum_spec is None


# ---------------------------------------------------------------------------
# QUALITY_PRESETS
# ---------------------------------------------------------------------------


class TestQualityPresets:

    def test_fast(self) -> None:

        assert QUALITY_PRESETS["fast"]["num_candidates"] == 1
        assert QUALITY_PRESETS["fast"]["use_critic"] is False

    def test_balanced(self) -> None:

        assert QUALITY_PRESETS["balanced"]["num_candidates"] == 2
        assert QUALITY_PRESETS["balanced"]["use_critic"] is True

    def test_quality(self) -> None:

        assert QUALITY_PRESETS["quality"]["num_candidates"] == 6
        assert QUALITY_PRESETS["quality"]["use_critic"] is True


# ---------------------------------------------------------------------------
# Dataclass construction
# ---------------------------------------------------------------------------


class TestDataclasses:

    def test_global_spec_defaults(self) -> None:

        gs = GlobalSpec()
        assert gs.tempo == 120
        assert gs.bars == 16
        assert gs.time_signature == (4, 4)

    def test_section_map_entry(self) -> None:

        entry = SectionMapEntry(0, 4, "intro", 0.5)
        assert entry.bar_start == 0
        assert entry.bar_end == 4
        assert entry.section == "intro"
        assert entry.energy == 0.5

    def test_density_target(self) -> None:

        dt = DensityTarget(min_hits_per_bar=4, max_hits_per_bar=8)
        assert dt.min_hits_per_bar == 4

    def test_drum_constraints(self) -> None:

        dc = DrumConstraints(fill_bars=[3, 7])
        assert dc.fill_bars == [3, 7]

    def test_chord_schedule_entry(self) -> None:

        cse = ChordScheduleEntry(bar=0, chord="Cm")
        assert cse.chord == "Cm"
