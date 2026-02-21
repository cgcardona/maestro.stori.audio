"""
Comprehensive tests for app.core.gm_instruments.

This module fires on every track creation — wrong inference = wrong MIDI
instrument on every note. Zero test coverage previously.

Coverage:
  1.  GM_INSTRUMENTS list integrity — 128 entries, programs 0-127, no gaps
  2.  get_instrument_by_program — all 128 programs, out-of-range, None
  3.  get_instrument_name — known programs, unknown, boundary
  4.  _normalize helper — lowercase, punctuation, whitespace
  5.  infer_gm_program — exact alias, fuzzy token, category fallback, drums
  6.  infer_gm_program — specific well-known instruments (regression table)
  7.  get_default_program_for_role — all defined roles
  8.  GMInferenceResult.needs_program_change property
  9.  infer_gm_program_with_context — priority chain, drums detection
 10.  infer_gm_program_with_context — edge cases (empty, all None)
"""

import pytest

from app.core.gm_instruments import (
    DRUM_ICON,
    GM_INSTRUMENTS,
    GMInstrument,
    GMInferenceResult,
    get_instrument_by_program,
    get_instrument_name,
    icon_for_gm_program,
    infer_gm_program,
    infer_gm_program_with_context,
    get_default_program_for_role,
    _normalize,
)


# ===========================================================================
# 1. GM_INSTRUMENTS list integrity
# ===========================================================================

class TestGMInstrumentsList:
    """The GM instrument table must be complete and well-formed."""

    def test_exactly_128_instruments(self):
        assert len(GM_INSTRUMENTS) == 128

    def test_programs_zero_to_127(self):
        programs = sorted(inst.program for inst in GM_INSTRUMENTS)
        assert programs == list(range(128))

    def test_no_duplicate_programs(self):
        programs = [inst.program for inst in GM_INSTRUMENTS]
        assert len(programs) == len(set(programs))

    def test_all_names_non_empty(self):
        for inst in GM_INSTRUMENTS:
            assert inst.name, f"Program {inst.program} has empty name"

    def test_all_categories_non_empty(self):
        for inst in GM_INSTRUMENTS:
            assert inst.category, f"Program {inst.program} has empty category"

    def test_all_have_at_least_one_alias(self):
        for inst in GM_INSTRUMENTS:
            assert len(inst.aliases) >= 1, (
                f"Program {inst.program} ({inst.name}) has no aliases"
            )

    def test_programs_in_order(self):
        for i, inst in enumerate(GM_INSTRUMENTS):
            assert inst.program == i, (
                f"Index {i} has program {inst.program} — list must be sorted 0-127"
            )

    def test_program_0_is_acoustic_grand_piano(self):
        assert GM_INSTRUMENTS[0].program == 0
        assert "Piano" in GM_INSTRUMENTS[0].name

    def test_program_127_is_gunshot(self):
        assert GM_INSTRUMENTS[127].program == 127
        assert "Gunshot" in GM_INSTRUMENTS[127].name


# ===========================================================================
# 2. get_instrument_by_program
# ===========================================================================

class TestGetInstrumentByProgram:
    """Lookup by program number."""

    def test_program_0_returns_piano(self):
        inst = get_instrument_by_program(0)
        assert inst is not None
        assert inst.program == 0

    def test_program_33_returns_electric_bass(self):
        inst = get_instrument_by_program(33)
        assert inst is not None
        assert "Bass" in inst.name

    def test_program_127_returns_gunshot(self):
        inst = get_instrument_by_program(127)
        assert inst is not None
        assert inst.program == 127

    def test_out_of_range_returns_none(self):
        assert get_instrument_by_program(128) is None
        assert get_instrument_by_program(-1) is None
        assert get_instrument_by_program(999) is None

    def test_all_128_programs_are_retrievable(self):
        for p in range(128):
            inst = get_instrument_by_program(p)
            assert inst is not None, f"Program {p} not retrievable"
            assert inst.program == p


# ===========================================================================
# 3. get_instrument_name
# ===========================================================================

class TestGetInstrumentName:
    """get_instrument_name returns official name or fallback."""

    def test_known_program_returns_name(self):
        name = get_instrument_name(0)
        assert "Piano" in name

    def test_program_33_name(self):
        assert "Bass" in get_instrument_name(33)

    def test_unknown_program_returns_fallback(self):
        name = get_instrument_name(999)
        assert "999" in name or "Program" in name

    def test_all_128_return_non_empty_string(self):
        for p in range(128):
            name = get_instrument_name(p)
            assert isinstance(name, str) and len(name) > 0


# ===========================================================================
# 4. _normalize helper
# ===========================================================================

class TestNormalize:
    """_normalize: lowercase, punctuation removed, whitespace collapsed."""

    def test_lowercase(self):
        assert _normalize("Electric Piano") == "electric piano"

    def test_punctuation_removed(self):
        assert "(" not in _normalize("Lead 1 (square)")
        assert ")" not in _normalize("Lead 1 (square)")

    def test_whitespace_collapsed(self):
        result = _normalize("  electric   bass  ")
        assert "  " not in result
        assert result == result.strip()

    def test_hyphen_treated_as_space(self):
        result = _normalize("hi-hat")
        assert "-" not in result

    def test_empty_string(self):
        assert _normalize("") == ""

    def test_already_normalized(self):
        assert _normalize("electric bass") == "electric bass"


# ===========================================================================
# 5. infer_gm_program — general matching behaviour
# ===========================================================================

class TestInferGMProgram:
    """infer_gm_program matches aliases, tokens, category keywords."""

    def test_empty_string_returns_default(self):
        assert infer_gm_program("") is None
        assert infer_gm_program("", default_program=0) == 0

    def test_drum_keywords_return_none(self):
        for kw in ("Drums", "drum kit", "kick", "snare", "hi-hat", "percussion"):
            result = infer_gm_program(kw)
            assert result is None, f"'{kw}' should return None (drums → channel 10)"

    def test_exact_alias_match(self):
        assert infer_gm_program("rhodes") == 4       # Electric Piano 1
        assert infer_gm_program("harmonica") == 22
        assert infer_gm_program("sitar") == 104

    def test_case_insensitive(self):
        assert infer_gm_program("PIANO") == infer_gm_program("piano")
        assert infer_gm_program("Electric Bass") == infer_gm_program("electric bass")

    def test_category_keyword_bass(self):
        result = infer_gm_program("Deep Bass")
        assert result is not None
        assert 32 <= result <= 39  # Bass family

    def test_category_keyword_piano(self):
        result = infer_gm_program("Studio Piano")
        assert result is not None

    def test_category_keyword_synth(self):
        result = infer_gm_program("Synth Lead 808")
        assert result is not None

    def test_completely_unknown_returns_default(self):
        result = infer_gm_program("xyzzy-instrument-12345")
        assert result is None  # no match, no default

    def test_completely_unknown_with_default_returns_default(self):
        result = infer_gm_program("xyzzy-instrument-12345", default_program=0)
        assert result == 0

    def test_returns_int_or_none(self):
        result = infer_gm_program("Piano")
        assert result is None or isinstance(result, int)


# ===========================================================================
# 6. infer_gm_program — regression table for specific instruments
# ===========================================================================

class TestInferGMProgramRegressionTable:
    """
    High-confidence regression tests: specific text → expected program range.
    These lock down the most frequently used track names.
    """

    @pytest.mark.parametrize("text,expected_program", [
        # Piano family
        ("piano",          0),
        ("acoustic piano", 0),
        ("electric piano", 4),
        ("rhodes",         4),

        # Bass family
        ("electric bass",  33),
        ("bass guitar",    33),
        ("synth bass",     38),
        ("upright bass",   32),

        # Guitar
        ("acoustic guitar", 25),
        ("nylon guitar",    24),

        # Strings
        ("violin",   40),
        ("cello",    42),

        # Brass
        ("trumpet",  56),
        ("trombone", 57),

        # Reed
        ("saxophone", 66),
        ("clarinet",  71),
        ("flute",     73),

        # Organ
        ("hammond",  16),
        ("organ",    16),

        # Synth
        ("pad",      88),
        ("choir",    52),

        # Ethnic
        ("sitar",    104),
        ("banjo",    105),
        ("kalimba",  108),
    ])
    def test_known_instrument(self, text, expected_program):
        result = infer_gm_program(text)
        assert result == expected_program, (
            f"'{text}': expected program {expected_program}, got {result}"
        )

    @pytest.mark.parametrize("drum_text", [
        "Drums", "Kick", "Snare", "Hi-Hat", "drum kit",
        "Percussion", "Beat", "Kit",
    ])
    def test_drum_texts_return_none(self, drum_text):
        assert infer_gm_program(drum_text) is None, (
            f"'{drum_text}' should return None (drums)"
        )


# ===========================================================================
# 7. get_default_program_for_role
# ===========================================================================

class TestGetDefaultProgramForRole:
    """get_default_program_for_role maps musical roles to GM programs."""

    def test_drums_returns_none(self):
        assert get_default_program_for_role("drums") is None

    def test_drum_returns_none(self):
        assert get_default_program_for_role("drum") is None

    def test_percussion_returns_none(self):
        assert get_default_program_for_role("percussion") is None

    def test_bass_returns_electric_bass(self):
        assert get_default_program_for_role("bass") == 33

    def test_chords_returns_rhodes(self):
        assert get_default_program_for_role("chords") == 4

    def test_pads_returns_synth_pad(self):
        assert get_default_program_for_role("pads") == 88

    def test_pad_returns_synth_pad(self):
        assert get_default_program_for_role("pad") == 88

    def test_melody_returns_synth_lead(self):
        assert get_default_program_for_role("melody") == 80

    def test_lead_returns_synth_lead(self):
        assert get_default_program_for_role("lead") == 80

    def test_arp_returns_synth_lead(self):
        assert get_default_program_for_role("arp") == 80

    def test_strings_returns_string_ensemble(self):
        assert get_default_program_for_role("strings") == 48

    def test_fx_returns_atmosphere(self):
        assert get_default_program_for_role("fx") == 99

    def test_sfx_returns_atmosphere(self):
        assert get_default_program_for_role("sfx") == 99

    def test_unknown_role_returns_none(self):
        assert get_default_program_for_role("zither") is None

    def test_case_insensitive(self):
        assert get_default_program_for_role("BASS") == get_default_program_for_role("bass")
        assert get_default_program_for_role("DRUMS") == get_default_program_for_role("drums")

    def test_whitespace_stripped(self):
        assert get_default_program_for_role("  bass  ") == 33


# ===========================================================================
# 8. GMInferenceResult.needs_program_change
# ===========================================================================

class TestGMInferenceResultProperty:
    """needs_program_change is False for drums, True for melodic instruments."""

    def test_drums_does_not_need_program_change(self):
        result = GMInferenceResult(
            program=None, instrument_name="Drums", confidence="high", is_drums=True
        )
        assert not result.needs_program_change

    def test_melodic_needs_program_change(self):
        result = GMInferenceResult(
            program=33, instrument_name="Electric Bass", confidence="high", is_drums=False
        )
        assert result.needs_program_change

    def test_none_program_no_program_change(self):
        result = GMInferenceResult(
            program=None, instrument_name="Unknown", confidence="none", is_drums=False
        )
        assert not result.needs_program_change

    def test_program_zero_needs_program_change(self):
        result = GMInferenceResult(
            program=0, instrument_name="Acoustic Grand Piano", confidence="high", is_drums=False
        )
        assert result.needs_program_change


# ===========================================================================
# 9. infer_gm_program_with_context — priority chain
# ===========================================================================

class TestInferGMProgramWithContext:
    """infer_gm_program_with_context follows instrument > track_name > role priority."""

    def test_drums_from_track_name(self):
        result = infer_gm_program_with_context(track_name="Drums")
        assert result.is_drums
        assert result.program is None
        assert not result.needs_program_change

    def test_drums_from_instrument_field(self):
        result = infer_gm_program_with_context(instrument="drums")
        assert result.is_drums

    def test_drums_from_role(self):
        result = infer_gm_program_with_context(role="drums")
        assert result.is_drums

    def test_drums_from_beat_in_track_name(self):
        result = infer_gm_program_with_context(track_name="Beat Track")
        assert result.is_drums

    def test_drums_from_kick_in_track_name(self):
        result = infer_gm_program_with_context(track_name="Kick Pattern")
        assert result.is_drums

    def test_instrument_field_highest_priority(self):
        """Explicit instrument overrides track name."""
        result = infer_gm_program_with_context(
            track_name="Lead Melody",   # would resolve to synth lead
            instrument="rhodes",         # should win
        )
        assert result.program == 4
        assert result.confidence == "high"

    def test_track_name_over_role(self):
        """Track name overrides role when it matches."""
        result = infer_gm_program_with_context(
            track_name="Electric Bass",
            role="melody",  # would give synth lead
        )
        assert result.program == 33  # bass wins

    def test_role_fallback_when_track_name_unknown(self):
        """Unknown track name falls back to role."""
        result = infer_gm_program_with_context(
            track_name="My Unique Layer Alpha",
            role="bass",
        )
        assert result.program == 33

    def test_all_none_defaults_to_piano(self):
        result = infer_gm_program_with_context()
        assert result.program == 0
        assert result.confidence == "none"
        assert not result.is_drums

    def test_bass_track_name(self):
        result = infer_gm_program_with_context(track_name="Bass")
        assert result.program is not None
        assert 32 <= result.program <= 39

    def test_piano_track_name(self):
        result = infer_gm_program_with_context(track_name="Piano")
        assert result.program == 0

    def test_returns_gm_inference_result(self):
        result = infer_gm_program_with_context(track_name="Piano")
        assert isinstance(result, GMInferenceResult)

    def test_confidence_high_for_instrument_field(self):
        result = infer_gm_program_with_context(instrument="rhodes")
        assert result.confidence == "high"

    def test_confidence_medium_for_track_name(self):
        result = infer_gm_program_with_context(track_name="Electric Bass")
        assert result.confidence == "medium"

    def test_confidence_low_for_role_only(self):
        result = infer_gm_program_with_context(role="bass")
        assert result.confidence == "low"

    def test_confidence_none_for_fallback(self):
        result = infer_gm_program_with_context(
            track_name="xyzzy-unknown-track",
        )
        assert result.confidence == "none"
        assert result.program == 0  # default piano


# ===========================================================================
# 10. infer_gm_program_with_context — edge cases
# ===========================================================================

class TestInferGMProgramWithContextEdgeCases:
    """Edge cases that must not crash."""

    def test_empty_track_name(self):
        result = infer_gm_program_with_context(track_name="")
        assert isinstance(result, GMInferenceResult)

    def test_whitespace_track_name(self):
        result = infer_gm_program_with_context(track_name="   ")
        assert isinstance(result, GMInferenceResult)

    def test_none_track_name(self):
        result = infer_gm_program_with_context(track_name=None)
        assert isinstance(result, GMInferenceResult)

    def test_all_args_empty_strings(self):
        result = infer_gm_program_with_context(track_name="", instrument="", role="")
        assert isinstance(result, GMInferenceResult)

    def test_instrument_name_is_string(self):
        result = infer_gm_program_with_context(track_name="Piano")
        assert isinstance(result.instrument_name, str)
        assert len(result.instrument_name) > 0

    def test_drums_confidence_is_high(self):
        result = infer_gm_program_with_context(track_name="Drums")
        assert result.confidence == "high"

    def test_descriptive_track_name_with_drums_keyword(self):
        result = infer_gm_program_with_context(track_name="Hip Hop Drum Kit")
        assert result.is_drums

    def test_descriptive_track_name_with_bass_keyword(self):
        result = infer_gm_program_with_context(track_name="Funky Bass Line")
        assert result.program is not None
        assert 32 <= result.program <= 39
        assert not result.is_drums

    @pytest.mark.parametrize("track_name,expected_is_drums", [
        ("Drums",          True),
        ("Kick",           True),
        ("Snare Hits",     True),
        ("Hi-Hat Pattern", True),
        ("Beat Machine",   True),
        ("Drum Kit",       True),
        ("Bass",           False),
        ("Piano",          False),
        ("Lead Synth",     False),
        ("Strings",        False),
    ])
    def test_drums_detection_parametrized(self, track_name, expected_is_drums):
        result = infer_gm_program_with_context(track_name=track_name)
        assert result.is_drums == expected_is_drums, (
            f"'{track_name}': expected is_drums={expected_is_drums}, "
            f"got is_drums={result.is_drums}"
        )


# ===========================================================================
# icon_for_gm_program
# ===========================================================================

class TestIconForGMProgram:
    """icon_for_gm_program maps every GM program to the correct SF Symbol."""

    @pytest.mark.parametrize("program,expected", [
        # One representative from each of the 16 GM categories
        (0,   "pianokeys"),                # Piano
        (8,   "instrument.xylophone"),     # Chromatic Percussion
        (16,  "music.note.house.fill"),    # Organ
        (24,  "guitars.fill"),             # Guitar
        (32,  "waveform.path"),            # Bass
        (40,  "instrument.violin"),        # Strings
        (48,  "instrument.violin"),        # Ensemble / Strings
        (56,  "instrument.trumpet"),       # Brass
        (64,  "instrument.saxophone"),     # Reed
        (72,  "instrument.flute"),         # Pipe
        (80,  "waveform"),                 # Synth Lead
        (88,  "waveform.circle.fill"),     # Synth Pad
        (96,  "sparkles"),                 # Synth Effects
        (104, "globe"),                    # Ethnic
        (112, "instrument.drum"),          # Percussive
        (120, "speaker.wave.3"),           # Sound Effects
        # Boundary values
        (7,   "pianokeys"),
        (15,  "instrument.xylophone"),
        (127, "speaker.wave.3"),
    ])
    def test_category_boundaries(self, program, expected):
        assert icon_for_gm_program(program) == expected

    def test_all_128_programs_return_non_empty_string(self):
        for p in range(128):
            icon = icon_for_gm_program(p)
            assert isinstance(icon, str) and icon, f"Program {p} returned empty icon"

    def test_out_of_range_returns_fallback(self):
        assert icon_for_gm_program(200) == "pianokeys"
        assert icon_for_gm_program(-1) == "pianokeys"

    def test_drum_icon_constant(self):
        assert DRUM_ICON == "instrument.drum"
