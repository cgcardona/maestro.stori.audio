"""
Tests for track styling (color and icon assignment).
"""

import pytest
from app.core.track_styling import (
    get_random_track_color,
    infer_track_icon,
    get_track_styling,
    normalize_color,
    color_for_role,
    is_valid_icon,
    allocate_colors,
    NAMED_COLORS,
    NAMED_COLORS_SET,
    PALETTE_ROTATION,
    COMPOSITION_PALETTE,
    DEFAULT_ICON,
)
from app.core.tool_validation.constants import VALID_SF_SYMBOL_ICONS


# ── Color tests ──────────────────────────────────────────────────────


class TestNamedColors:
    """Named-color palette and validation."""

    def test_palette_has_12_named_colors(self):
        assert len(NAMED_COLORS) == 12

    def test_grey_alias_accepted(self):
        assert "grey" in NAMED_COLORS_SET
        assert normalize_color("grey") == "gray"

    def test_normalize_named_color(self):
        assert normalize_color("blue") == "blue"
        assert normalize_color("MINT") == "mint"
        assert normalize_color(" Cyan ") == "cyan"

    def test_normalize_hex_color(self):
        assert normalize_color("#FF6B6B") == "#FF6B6B"
        assert normalize_color("#aabbcc") == "#aabbcc"

    def test_normalize_rejects_invalid(self):
        assert normalize_color("crimson") is None
        assert normalize_color("coral") is None
        assert normalize_color("#FFF") is None
        assert normalize_color("") is None
        assert normalize_color(None) is None

    def test_normalize_rejects_css_names(self):
        assert normalize_color("navy") is None
        assert normalize_color("salmon") is None


class TestColorForRole:
    """Role-based color assignment."""

    def test_drums_get_red(self):
        assert color_for_role("Drums") == "red"
        assert color_for_role("Kick") == "red"

    def test_bass_gets_green(self):
        assert color_for_role("Bass") == "green"
        assert color_for_role("Sub Bass") == "green"

    def test_piano_gets_blue(self):
        assert color_for_role("Piano") == "blue"
        assert color_for_role("Keys") == "blue"

    def test_synth_gets_indigo(self):
        assert color_for_role("Synth Lead") == "indigo"
        assert color_for_role("Rhodes Synth") == "indigo"

    def test_strings_get_purple(self):
        assert color_for_role("Strings") == "purple"
        assert color_for_role("Violin") == "purple"

    def test_vocals_get_pink(self):
        assert color_for_role("Vocals") == "pink"
        assert color_for_role("Choir") == "pink"

    def test_brass_gets_orange(self):
        assert color_for_role("Brass") == "orange"
        assert color_for_role("Trumpet") == "orange"

    def test_guitar_gets_yellow(self):
        assert color_for_role("Guitar") == "yellow"

    def test_woodwind_gets_teal(self):
        assert color_for_role("Flute") == "teal"
        assert color_for_role("Sax") == "teal"

    def test_fx_gets_cyan(self):
        assert color_for_role("FX") == "cyan"
        assert color_for_role("Texture") == "cyan"
        assert color_for_role("Ambient") == "cyan"

    def test_perc_gets_mint(self):
        assert color_for_role("Percussion") == "mint"
        assert color_for_role("Shaker") == "mint"

    def test_utility_gets_gray(self):
        assert color_for_role("Click Track") == "gray"

    def test_unknown_falls_back_to_rotation(self):
        assert color_for_role("Mystery", rotation_index=0) == "blue"
        assert color_for_role("Mystery", rotation_index=1) == "indigo"
        assert color_for_role("Mystery", rotation_index=12) == "blue"


class TestPaletteRotation:
    """Deterministic palette cycling."""

    def test_rotation_order(self):
        expected = [
            "blue", "indigo", "purple", "pink", "red", "orange",
            "yellow", "green", "teal", "cyan", "mint", "gray",
        ]
        assert PALETTE_ROTATION == expected

    def test_rotation_wraps(self):
        assert color_for_role("Unknown", 0) == PALETTE_ROTATION[0]
        assert color_for_role("Unknown", 11) == PALETTE_ROTATION[11]
        assert color_for_role("Unknown", 12) == PALETTE_ROTATION[0]


class TestGetRandomTrackColor:
    """Backwards-compatible get_random_track_color returns a named color."""

    def test_returns_named_color(self):
        color = get_random_track_color()
        assert color in NAMED_COLORS_SET


# ── Icon tests ───────────────────────────────────────────────────────


class TestIsValidIcon:
    """Icon allowlist validation."""

    def test_valid_icons_accepted(self):
        assert is_valid_icon("pianokeys") is True
        assert is_valid_icon("instrument.drum") is True
        assert is_valid_icon("music.note") is True
        assert is_valid_icon("sparkles") is True

    def test_invalid_icons_rejected(self):
        assert is_valid_icon("drum") is False
        assert is_valid_icon("piano") is False
        assert is_valid_icon("beats") is False
        assert is_valid_icon("") is False
        assert is_valid_icon(None) is False

    def test_fe_contract_icons_accepted(self):
        """Icons in the FE curated SF Symbol list must be accepted."""
        assert is_valid_icon("speaker.wave.3") is True
        assert is_valid_icon("wand.and.stars.inverse") is True
        assert is_valid_icon("bolt.circle") is True
        assert is_valid_icon("waveform.slash") is True
        assert is_valid_icon("speaker") is True
        assert is_valid_icon("speaker.wave.3.fill") is True


class TestTrackIcons:
    """Track icon inference from name keywords."""

    def test_drum_track_icon(self):
        assert infer_track_icon("Drums") == "instrument.drum"
        assert infer_track_icon("Jam Drums") == "instrument.drum"
        assert infer_track_icon("Kick") == "instrument.drum"
        assert infer_track_icon("Snare") == "instrument.drum"

    def test_perc_track_icon(self):
        assert infer_track_icon("Perc") == "instrument.drum"
        assert infer_track_icon("Percussion") == "instrument.drum"

    def test_bass_track_icon(self):
        assert infer_track_icon("Bass") == "guitars.fill"
        assert infer_track_icon("Funky Bass") == "guitars.fill"
        assert infer_track_icon("Sub Bass") == "guitars.fill"

    def test_piano_track_icon(self):
        assert infer_track_icon("Piano") == "pianokeys"
        assert infer_track_icon("Keys") == "pianokeys"

    def test_synth_track_icon(self):
        assert infer_track_icon("Synth") == "pianokeys.inverse"
        assert infer_track_icon("Synth Lead") == "pianokeys.inverse"
        assert infer_track_icon("Rhodes") == "pianokeys.inverse"
        assert infer_track_icon("Electric Piano") == "pianokeys.inverse"

    def test_pad_track_icon(self):
        assert infer_track_icon("Pads") == "waveform"
        assert infer_track_icon("Pad") == "waveform"

    def test_guitar_track_icon(self):
        assert infer_track_icon("Guitar") == "guitars.fill"
        assert infer_track_icon("Guitar Solo") == "guitars.fill"
        assert infer_track_icon("Acoustic Guitar") == "guitars"

    def test_vocal_track_icon(self):
        assert infer_track_icon("Vocals") == "music.mic"
        assert infer_track_icon("Lead Vocal") == "music.mic"
        assert infer_track_icon("Voice") == "music.mic"

    def test_chord_track_icon(self):
        assert infer_track_icon("Chords") == "pianokeys"
        assert infer_track_icon("Harmony") == "music.note.list"

    def test_fx_track_icon(self):
        assert infer_track_icon("FX") == "sparkles"
        assert infer_track_icon("Sound Effects") == "sparkles"

    def test_strings_track_icon(self):
        assert infer_track_icon("Strings") == "instrument.violin"
        assert infer_track_icon("Violin") == "instrument.violin"
        assert infer_track_icon("Cello Section") == "instrument.violin"

    def test_brass_track_icon(self):
        assert infer_track_icon("Brass") == "instrument.trumpet"
        assert infer_track_icon("Trumpet") == "instrument.trumpet"
        assert infer_track_icon("French Horn") == "instrument.trumpet"

    def test_reed_track_icon(self):
        assert infer_track_icon("Alto Sax") == "instrument.saxophone"
        assert infer_track_icon("Clarinet") == "instrument.saxophone"

    def test_flute_track_icon(self):
        assert infer_track_icon("Flute") == "instrument.flute"
        assert infer_track_icon("Recorder") == "instrument.flute"

    def test_mallet_track_icon(self):
        assert infer_track_icon("Marimba") == "instrument.xylophone"
        assert infer_track_icon("Xylophone") == "instrument.xylophone"
        assert infer_track_icon("Bells") == "instrument.xylophone"

    def test_organ_track_icon(self):
        assert infer_track_icon("Organ") == "pianokeys"
        assert infer_track_icon("Hammond Organ") == "pianokeys"

    def test_default_icon(self):
        assert infer_track_icon("Unknown Thing") == "music.note"
        assert infer_track_icon("") == "music.note"
        assert DEFAULT_ICON == "music.note"

    def test_case_insensitive(self):
        assert infer_track_icon("DRUMS") == "instrument.drum"
        assert infer_track_icon("PiAnO") == "pianokeys"

    def test_all_inferred_icons_are_valid(self):
        """Every icon returned by infer_track_icon must be in the curated set."""
        from app.core.track_styling import _ICON_KEYWORD_LIST
        all_icons = {icon for _, icon in _ICON_KEYWORD_LIST} | {DEFAULT_ICON}
        invalid = all_icons - VALID_SF_SYMBOL_ICONS
        assert invalid == set(), f"Inferred icons not in curated set: {invalid}"


# ── Combined styling tests ───────────────────────────────────────────


class TestTrackStyling:
    """Test combined styling."""

    def test_get_track_styling_returns_named_color(self):
        styling = get_track_styling("Drums")
        assert "color" in styling
        assert "icon" in styling
        assert styling["color"] == "red"
        assert styling["icon"] == "instrument.drum"

    def test_styling_varies_by_name(self):
        drum_styling = get_track_styling("Drums")
        bass_styling = get_track_styling("Bass")
        piano_styling = get_track_styling("Piano")

        assert drum_styling["color"] == "red"
        assert bass_styling["color"] == "green"
        assert piano_styling["color"] == "blue"

        assert drum_styling["icon"] == "instrument.drum"
        assert bass_styling["icon"] == "guitars.fill"
        assert piano_styling["icon"] == "pianokeys"

    def test_rotation_index_controls_fallback(self):
        s0 = get_track_styling("Unknown", rotation_index=0)
        s5 = get_track_styling("Unknown", rotation_index=5)
        assert s0["color"] == "blue"
        assert s5["color"] == "orange"


# ── Composition palette and color allocation ─────────────────────────


class TestCompositionPalette:
    """COMPOSITION_PALETTE structure and properties."""

    def test_palette_has_twelve_entries(self):
        """Palette must have exactly 12 high-hue-separation hex colors."""
        assert len(COMPOSITION_PALETTE) == 12

    def test_all_entries_are_valid_hex(self):
        """Every palette entry must be a valid #RRGGBB hex string."""
        import re
        hex_re = re.compile(r"^#[0-9a-fA-F]{6}$")
        for color in COMPOSITION_PALETTE:
            assert hex_re.match(color), f"Not a valid hex color: {color!r}"

    def test_normalize_color_accepts_all_palette_entries(self):
        """normalize_color must pass every COMPOSITION_PALETTE entry through."""
        for color in COMPOSITION_PALETTE:
            assert normalize_color(color) == color, (
                f"normalize_color rejected palette entry {color!r}"
            )

    def test_all_entries_unique(self):
        """No duplicate colors in the composition palette."""
        assert len(COMPOSITION_PALETTE) == len(set(COMPOSITION_PALETTE))


class TestAllocateColors:
    """Coordinator-level color pre-allocation — regression for same-color-all-tracks bug."""

    def test_four_track_project_all_distinct(self):
        """4-track projects (Drums, Bass, Synth Lead, Organ Bubble) get distinct colors."""
        instruments = ["Drums", "Bass", "Synth Lead", "Organ Bubble"]
        result = allocate_colors(instruments)
        colors = list(result.values())
        assert len(colors) == len(set(colors)), (
            f"Duplicate colors assigned to tracks: {result}"
        )

    def test_returns_one_color_per_instrument(self):
        """Result contains exactly one entry per instrument name."""
        instruments = ["Drums", "Bass", "Lead", "Pads"]
        result = allocate_colors(instruments)
        assert set(result.keys()) == set(instruments)

    def test_colors_come_from_composition_palette(self):
        """Every assigned color must come from COMPOSITION_PALETTE."""
        instruments = ["Drums", "Bass", "Keys", "Lead", "Arp", "FX", "Perc", "Choir"]
        result = allocate_colors(instruments)
        for name, color in result.items():
            assert color in COMPOSITION_PALETTE, (
                f"Color {color!r} for {name!r} not in COMPOSITION_PALETTE"
            )

    def test_order_matches_palette_index(self):
        """First instrument gets palette[0], second gets palette[1], etc."""
        instruments = ["A", "B", "C"]
        result = allocate_colors(instruments)
        assert result["A"] == COMPOSITION_PALETTE[0]
        assert result["B"] == COMPOSITION_PALETTE[1]
        assert result["C"] == COMPOSITION_PALETTE[2]

    def test_cycles_after_twelve_tracks(self):
        """Colors wrap after the 12-entry palette is exhausted."""
        instruments = [f"Track{i}" for i in range(14)]
        result = allocate_colors(instruments)
        assert result["Track0"] == COMPOSITION_PALETTE[0]
        assert result["Track12"] == COMPOSITION_PALETTE[0]
        assert result["Track13"] == COMPOSITION_PALETTE[1]

    def test_empty_list_returns_empty_dict(self):
        """Empty instrument list produces an empty mapping."""
        assert allocate_colors([]) == {}

    def test_single_instrument(self):
        """Single instrument gets the first palette color."""
        result = allocate_colors(["Solo Piano"])
        assert result == {"Solo Piano": COMPOSITION_PALETTE[0]}

    def test_regression_no_same_color_adjacent_tracks(self):
        """Adjacent tracks in a 4-track project must never share the same color.

        Regression for: Bass, Synth Lead, Organ Bubble, Drums all receiving
        amber/orange because the LLM hallucinated the same color for every track.
        """
        instruments = ["Drums", "Bass", "Synth Lead", "Organ Bubble"]
        result = allocate_colors(instruments)
        names = list(result.keys())
        for i in range(len(names) - 1):
            assert result[names[i]] != result[names[i + 1]], (
                f"Adjacent tracks '{names[i]}' and '{names[i+1]}' share color "
                f"{result[names[i]]!r} — violates diversity requirement"
            )
