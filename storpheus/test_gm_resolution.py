"""
Tests for GM instrument resolution — the safety net for the production bug
where instruments weren't passed correctly to Orpheus.

Covers: resolve_gm_program, resolve_tmidix_name, _resolve_melodic_index.
"""
from __future__ import annotations

import pytest

from music_service import (
    resolve_gm_program,
    resolve_tmidix_name,
    _resolve_melodic_index,
    _GM_ALIASES,
    _TMIDIX_PATCH_NAMES,
    _DRUM_KEYWORDS,
)


# =============================================================================
# resolve_gm_program
# =============================================================================


class TestResolveGmProgram:
    """Unit tests for resolve_gm_program — alias → GM program number."""

    @pytest.mark.parametrize("role,expected", [
        ("piano", 0),
        ("bright acoustic", 1),
        ("electric piano", 4),
        ("bass", 33),
        ("electric bass", 33),
        ("synth bass", 38),
        ("guitar", 25),
        ("acoustic guitar", 25),
        ("electric guitar", 27),
        ("violin", 40),
        ("cello", 42),
        ("strings", 48),
        ("trumpet", 56),
        ("saxophone", 66),
        ("tenor sax", 66),
        ("alto sax", 65),
        ("flute", 73),
        ("sitar", 104),
        ("organ", 16),
        ("harpsichord", 6),
        ("harp", 46),
        ("choir", 52),
        ("synth lead", 80),
        ("synth pad", 88),
        ("marimba", 12),
        ("xylophone", 13),
        ("vibraphone", 11),
        ("kalimba", 108),
        ("banjo", 105),
        ("harmonica", 22),
        ("accordion", 21),
        ("tuba", 58),
        ("french horn", 60),
        ("oboe", 68),
        ("clarinet", 71),
        ("bassoon", 70),
    ])
    def test_common_instruments_resolve(self, role: str, expected: int) -> None:
        """Common instrument names resolve to the correct GM program."""
        assert resolve_gm_program(role) == expected

    def test_drums_return_none(self) -> None:
        """All drum keywords return None (drums use channel 10, no program)."""
        for keyword in ("drums", "drum", "percussion", "kick", "snare",
                        "hihat", "hi-hat", "808", "cajon", "tabla"):
            assert resolve_gm_program(keyword) is None, f"{keyword} should be None"

    def test_case_insensitive(self) -> None:
        """Resolution is case-insensitive."""
        assert resolve_gm_program("Piano") == resolve_gm_program("piano")
        assert resolve_gm_program("BASS") == resolve_gm_program("bass")
        assert resolve_gm_program("Violin") == resolve_gm_program("violin")

    def test_whitespace_stripped(self) -> None:
        """Leading/trailing whitespace is stripped."""
        assert resolve_gm_program("  piano  ") == 0
        assert resolve_gm_program(" bass\t") == 33

    def test_substring_matching(self) -> None:
        """Substring matching catches partial instrument names."""
        result = resolve_gm_program("grand piano")
        assert result is not None

    def test_unknown_returns_none(self) -> None:
        """Completely unknown instruments return None."""
        assert resolve_gm_program("xyzzy_instrument_404") is None

    def test_every_alias_resolves_to_valid_program(self) -> None:
        """Every alias in the GM table maps to a valid 0-127 program."""
        for alias, program in _GM_ALIASES.items():
            assert 0 <= program <= 127, f"Alias '{alias}' → {program} out of range"

    def test_nearly_all_programs_are_reachable(self) -> None:
        """The vast majority of GM programs have at least one alias."""
        reachable = set(_GM_ALIASES.values())
        missing = [p for p in range(128) if p not in reachable]
        assert len(missing) <= 5, (
            f"Too many unreachable GM programs ({len(missing)}): "
            + ", ".join(f"{p} ({_TMIDIX_PATCH_NAMES[p]})" for p in missing)
        )

    def test_world_instruments_resolve(self) -> None:
        """World instruments that caused the production bug resolve correctly."""
        world = {
            "sitar": 104, "shamisen": 106, "koto": 107, "kalimba": 108,
            "bagpipe": 109, "banjo": 105,
        }
        for role, expected in world.items():
            result = resolve_gm_program(role)
            assert result == expected, f"{role}: expected {expected}, got {result}"


# =============================================================================
# resolve_tmidix_name
# =============================================================================


class TestResolveTmidixName:
    """Unit tests for resolve_tmidix_name — role → TMIDIX patch string."""

    def test_piano_resolves_to_acoustic_grand(self) -> None:
        assert resolve_tmidix_name("piano") == "Acoustic Grand"

    def test_bass_resolves_to_electric_bass_finger(self) -> None:
        assert resolve_tmidix_name("bass") == "Electric Bass(finger)"

    def test_drums_resolve_to_drums(self) -> None:
        assert resolve_tmidix_name("drums") == "Drums"

    def test_percussion_resolve_to_drums(self) -> None:
        assert resolve_tmidix_name("percussion") == "Drums"

    def test_unknown_returns_none(self) -> None:
        assert resolve_tmidix_name("xyzzy_404") is None

    @pytest.mark.parametrize("role", [
        "piano", "bass", "guitar", "violin", "trumpet", "flute", "sitar",
        "strings", "choir", "organ", "synth lead", "marimba",
    ])
    def test_every_common_role_returns_string(self, role: str) -> None:
        """Common roles return a non-None TMIDIX string."""
        result = resolve_tmidix_name(role)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_tmidix_table_has_128_entries(self) -> None:
        """The TMIDIX patch name table has exactly 128 entries."""
        assert len(_TMIDIX_PATCH_NAMES) == 128

    def test_tmidix_names_are_unique(self) -> None:
        """Each GM program has a distinct TMIDIX name."""
        seen: set[str] = set()
        for name in _TMIDIX_PATCH_NAMES:
            assert name not in seen, f"Duplicate TMIDIX name: {name}"
            seen.add(name)


# =============================================================================
# _resolve_melodic_index
# =============================================================================


class TestResolveMelodicIndex:
    """Unit tests for _resolve_melodic_index — role → channel assignment."""

    def test_bass_family_returns_0(self) -> None:
        """Bass instruments (GM 32-39) → channel index 0."""
        for role in ("bass", "electric bass", "synth bass", "fretless bass"):
            result = _resolve_melodic_index(role)
            assert result == 0, f"{role}: expected 0, got {result}"

    def test_piano_keys_returns_1(self) -> None:
        """Piano/keys/organ (GM 0-7, 16-23) → channel index 1."""
        for role in ("piano", "harpsichord", "organ", "accordion"):
            result = _resolve_melodic_index(role)
            assert result == 1, f"{role}: expected 1, got {result}"

    def test_other_melodic_returns_2(self) -> None:
        """Everything else (guitar, strings, brass, etc.) → channel index 2."""
        for role in ("guitar", "violin", "trumpet", "flute", "sitar"):
            result = _resolve_melodic_index(role)
            assert result == 2, f"{role}: expected 2, got {result}"

    def test_drums_return_none(self) -> None:
        """Drum roles return None (drums go to channel 9)."""
        assert _resolve_melodic_index("drums") is None
        assert _resolve_melodic_index("percussion") is None

    def test_unknown_defaults_to_2(self) -> None:
        """Unknown melodic roles default to channel 2."""
        assert _resolve_melodic_index("xyzzy_unknown") == 2


# =============================================================================
# Compat wrappers
# =============================================================================


# =============================================================================
# Drum keywords
# =============================================================================


class TestDrumKeywords:
    """Validate the drum keyword set is comprehensive and correct."""

    def test_core_drum_keywords_present(self) -> None:
        """Essential drum keywords are in the set."""
        essential = {"drums", "drum", "percussion", "kick", "snare", "hihat"}
        assert essential.issubset(_DRUM_KEYWORDS)

    def test_world_percussion_present(self) -> None:
        """World percussion instruments are recognized as drums."""
        world_perc = {"tabla", "cajon", "djembe", "taiko", "congas", "bongos"}
        assert world_perc.issubset(_DRUM_KEYWORDS)

    def test_drum_machine_keywords(self) -> None:
        """Electronic drum machine references are recognized."""
        assert "808" in _DRUM_KEYWORDS
        assert "909" in _DRUM_KEYWORDS

    def test_no_melodic_instruments_in_drums(self) -> None:
        """Melodic instruments are NOT in the drum set."""
        melodic = {"piano", "bass", "guitar", "violin", "flute", "sitar"}
        assert not melodic.intersection(_DRUM_KEYWORDS)
