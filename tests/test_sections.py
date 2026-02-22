"""Tests for the STORI PROMPT section parser.

Regression suite for the multi-section MIDI generation feature: ensures
parse_sections() correctly detects named sections, assigns beat ranges that
cover the full arrangement, and returns a single full-arrangement section when
no structural keywords are found.
"""

import pytest

from app.core.maestro_agent_teams.sections import parse_sections, _get_section_role_description


ROLES = ["drums", "bass", "chords", "lead"]


# =============================================================================
# Basic detection
# =============================================================================

class TestSectionDetection:
    def test_no_sections_returns_single_full_section(self):
        """A prompt with no section keywords returns one section covering all beats."""
        prompt = "Make a reggaeton track at 96 BPM in Bm, 24 bars"
        sections = parse_sections(prompt, bars=24, roles=ROLES)
        assert len(sections) == 1
        assert sections[0]["name"] == "full"
        assert sections[0]["start_beat"] == 0.0
        assert sections[0]["length_beats"] == 96.0  # 24 bars × 4 beats

    def test_intro_verse_chorus_detected(self):
        """Explicit intro/verse/chorus keywords create 3 sections."""
        prompt = "Reggaeton with an intro, a verse, and a chorus. 32 bars."
        sections = parse_sections(prompt, bars=32, roles=ROLES)
        names = [s["name"] for s in sections]
        assert "intro" in names
        assert "verse" in names
        assert "chorus" in names
        assert len(sections) == 3

    def test_sections_are_ordered_by_appearance_in_prompt(self):
        """Sections appear in the order they are mentioned in the prompt."""
        prompt = "Start with an intro, then the verse, then the chorus, then the outro."
        sections = parse_sections(prompt, bars=32, roles=ROLES)
        names = [s["name"] for s in sections]
        assert names == ["intro", "verse", "chorus", "outro"]

    def test_single_section_keyword_returns_full_section(self):
        """Only one keyword found — not enough for a meaningful split; return full."""
        prompt = "Heavy chorus energy throughout, 16 bars."
        sections = parse_sections(prompt, bars=16, roles=ROLES)
        # Single keyword → single section
        assert len(sections) == 1

    def test_duplicate_keywords_deduplicated(self):
        """Repeated section keywords produce one section per unique name."""
        prompt = "verse, verse, chorus, verse"
        sections = parse_sections(prompt, bars=16, roles=ROLES)
        names = [s["name"] for s in sections]
        assert names.count("verse") == 1
        assert names.count("chorus") == 1


# =============================================================================
# Beat range integrity
# =============================================================================

class TestSectionBeatRanges:
    def test_sections_cover_full_arrangement(self):
        """All section lengths sum exactly to bars × 4 beats."""
        prompt = "intro verse chorus outro — 32 bars"
        bars = 32
        sections = parse_sections(prompt, bars=bars, roles=ROLES)
        total = sum(s["length_beats"] for s in sections)
        assert total == bars * 4, f"Expected {bars * 4} total beats, got {total}"

    def test_sections_are_contiguous(self):
        """Each section starts exactly where the previous one ends."""
        prompt = "intro, build, verse, chorus, outro — 40 bars"
        sections = parse_sections(prompt, bars=40, roles=ROLES)
        for i in range(1, len(sections)):
            prev_end = sections[i - 1]["start_beat"] + sections[i - 1]["length_beats"]
            curr_start = sections[i]["start_beat"]
            assert abs(prev_end - curr_start) < 0.01, (
                f"Gap between {sections[i-1]['name']} and {sections[i]['name']}: "
                f"prev_end={prev_end}, curr_start={curr_start}"
            )

    def test_first_section_starts_at_beat_zero(self):
        """The first section always starts at beat 0."""
        prompt = "intro verse chorus — 24 bars"
        sections = parse_sections(prompt, bars=24, roles=ROLES)
        assert sections[0]["start_beat"] == 0.0

    def test_each_section_has_minimum_one_bar(self):
        """No section should be shorter than 4 beats (1 bar)."""
        prompt = "intro verse chorus bridge outro — 16 bars"
        sections = parse_sections(prompt, bars=16, roles=ROLES)
        for s in sections:
            assert s["length_beats"] >= 4, (
                f"Section '{s['name']}' is shorter than 1 bar: {s['length_beats']} beats"
            )

    def test_single_full_section_covers_all_beats(self):
        """No-section prompt returns exactly one section of bars × 4 beats."""
        for bars in [4, 8, 16, 24, 32]:
            sections = parse_sections("funky track", bars=bars, roles=["drums", "bass"])
            assert len(sections) == 1
            assert sections[0]["length_beats"] == bars * 4


# =============================================================================
# Per-track descriptions
# =============================================================================

class TestPerTrackDescriptions:
    def test_per_track_descriptions_populated_for_known_roles(self):
        """Section parser generates per-track descriptions for standard instrument roles."""
        prompt = "intro verse chorus — 32 bars"
        sections = parse_sections(prompt, bars=32, roles=["drums", "bass", "chords"])
        for s in sections:
            if s["name"] == "full":
                continue
            per_track = s["per_track_description"]
            assert "drums" in per_track
            assert "bass" in per_track
            assert isinstance(per_track["drums"], str)
            assert len(per_track["drums"]) > 10

    def test_chorus_drums_description_is_high_energy(self):
        """Chorus drum description must convey high energy (not sparse/minimal)."""
        desc = _get_section_role_description("chorus", "drums")
        assert desc  # non-empty
        low_energy_words = {"sparse", "minimal", "silent", "absent"}
        assert not any(w in desc.lower() for w in low_energy_words), (
            f"Chorus drum description should be energetic, got: {desc!r}"
        )

    def test_breakdown_drums_description_is_sparse(self):
        """Breakdown drum description must convey low energy."""
        desc = _get_section_role_description("breakdown", "drums")
        assert desc
        high_energy_words = {"full energy", "all elements", "maximum"}
        assert not any(w in desc.lower() for w in high_energy_words), (
            f"Breakdown drum description should be sparse, got: {desc!r}"
        )

    def test_unknown_role_returns_empty_string(self):
        """An unrecognised role name returns an empty string (no crash)."""
        desc = _get_section_role_description("verse", "theremin")
        assert isinstance(desc, str)  # may be empty, must not raise


# =============================================================================
# Edge cases
# =============================================================================

class TestSectionEdgeCases:
    def test_empty_prompt_returns_full_section(self):
        """Empty prompt string returns a single full-arrangement section."""
        sections = parse_sections("", bars=8, roles=["drums"])
        assert len(sections) == 1
        assert sections[0]["name"] == "full"

    def test_inferred_drop_keyword_detected(self):
        """'drop' is treated as a synonym for chorus."""
        prompt = "build into the drop, 16 bars"
        sections = parse_sections(prompt, bars=16, roles=ROLES)
        names = [s["name"] for s in sections]
        assert "chorus" in names or "build" in names  # at least one detected

    def test_large_bar_count_preserved(self):
        """parse_sections handles large bar counts without overflow or rounding errors."""
        prompt = "intro verse chorus outro"
        sections = parse_sections(prompt, bars=128, roles=["drums", "bass"])
        total = sum(s["length_beats"] for s in sections)
        assert total == 128 * 4

    def test_section_descriptions_field_present(self):
        """Every section has a non-empty 'description' field."""
        prompt = "intro verse chorus outro — 32 bars"
        sections = parse_sections(prompt, bars=32, roles=ROLES)
        for s in sections:
            assert "description" in s
            assert isinstance(s["description"], str)


# =============================================================================
# Extended keyword coverage
# =============================================================================

class TestExpandedKeywords:
    """Ensure exhaustive section keyword recognition across genres."""

    def test_jazz_head_solo_detected(self):
        """Jazz 'head' and 'solo' map to verse and solo sections."""
        prompt = "Head statement, then a solo, then the head returns. 32 bars."
        sections = parse_sections(prompt, bars=32, roles=ROLES)
        names = [s["name"] for s in sections]
        assert "verse" in names, f"'head' should map to verse, got {names}"
        assert "solo" in names, f"'solo' should be detected, got {names}"

    def test_refrain_maps_to_chorus(self):
        """'refrain' is a synonym for chorus."""
        prompt = "A verse followed by the refrain, 16 bars."
        sections = parse_sections(prompt, bars=16, roles=ROLES)
        names = [s["name"] for s in sections]
        assert "chorus" in names, f"'refrain' should map to chorus, got {names}"

    def test_coda_maps_to_outro(self):
        """Classical 'coda' maps to outro."""
        prompt = "Exposition, then a development, then a coda. 32 bars."
        sections = parse_sections(prompt, bars=32, roles=ROLES)
        names = [s["name"] for s in sections]
        assert "outro" in names, f"'coda' should map to outro, got {names}"

    def test_interlude_detected(self):
        """'interlude' is detected as its own section type."""
        prompt = "Verse, then an interlude, then the chorus. 24 bars."
        sections = parse_sections(prompt, bars=24, roles=ROLES)
        names = [s["name"] for s in sections]
        assert "interlude" in names, f"'interlude' should be detected, got {names}"

    def test_groove_vamp_detected(self):
        """'vamp' or 'groove section' triggers groove section."""
        prompt = "An intro, then a vamp, then the outro. 24 bars."
        sections = parse_sections(prompt, bars=24, roles=ROLES)
        names = [s["name"] for s in sections]
        assert "groove" in names, f"'vamp' should map to groove, got {names}"

    def test_middle_eight_maps_to_bridge(self):
        """British 'middle eight' maps to bridge."""
        prompt = "Verse, then the middle eight, then the chorus. 24 bars."
        sections = parse_sections(prompt, bars=24, roles=ROLES)
        names = [s["name"] for s in sections]
        assert "bridge" in names, f"'middle eight' should map to bridge, got {names}"

    def test_climax_maps_to_chorus(self):
        """'climax' or 'peak' maps to chorus energy."""
        prompt = "A slow build, then the climax. 16 bars."
        sections = parse_sections(prompt, bars=16, roles=ROLES)
        names = [s["name"] for s in sections]
        assert "chorus" in names, f"'climax' should map to chorus, got {names}"

    def test_riser_maps_to_build(self):
        """'riser' maps to build section."""
        prompt = "Verse groove, then a riser, then the drop. 24 bars."
        sections = parse_sections(prompt, bars=24, roles=ROLES)
        names = [s["name"] for s in sections]
        assert "build" in names, f"'riser' should map to build, got {names}"

    def test_exposition_development_detected(self):
        """Classical form: exposition/development map to verse/bridge."""
        prompt = "Exposition of the theme, development section, then recapitulation. 32 bars."
        sections = parse_sections(prompt, bars=32, roles=ROLES)
        names = [s["name"] for s in sections]
        assert "verse" in names, f"'exposition' should map to verse, got {names}"
        assert "bridge" in names, f"'development' should map to bridge, got {names}"
        assert len(sections) >= 2

    def test_reprise_maps_to_verse(self):
        """'reprise' maps to verse (return of theme)."""
        prompt = "Theme statement, a solo, then the reprise. 24 bars."
        sections = parse_sections(prompt, bars=24, roles=ROLES)
        names = [s["name"] for s in sections]
        assert names.count("verse") >= 1, f"'reprise' should map to verse, got {names}"

    def test_inferred_crescendo_maps_to_build(self):
        """Descriptive 'crescendo' infers a build section."""
        prompt = "A quiet intro, then a long crescendo, then full energy. 24 bars."
        sections = parse_sections(prompt, bars=24, roles=ROLES)
        names = [s["name"] for s in sections]
        assert "build" in names, f"'crescendo' should infer build, got {names}"

    def test_inferred_soloist_maps_to_solo(self):
        """'takes a solo' descriptive language infers solo section."""
        prompt = "The band grooves, then the sax takes a solo, then the outro. 24 bars."
        sections = parse_sections(prompt, bars=24, roles=ROLES)
        names = [s["name"] for s in sections]
        assert "solo" in names, f"'takes a solo' should infer solo, got {names}"

    def test_inferred_fade_away_maps_to_outro(self):
        """'fades away' descriptive language infers outro."""
        prompt = "Full chorus energy, then everything fades away. 16 bars."
        sections = parse_sections(prompt, bars=16, roles=ROLES)
        names = [s["name"] for s in sections]
        assert "outro" in names, f"'fades away' should infer outro, got {names}"

    def test_solo_section_has_role_templates(self):
        """Solo section has per-track descriptions for standard roles."""
        prompt = "Head, then a solo section, then the outro. 24 bars."
        sections = parse_sections(prompt, bars=24, roles=["drums", "bass", "lead"])
        solo_sections = [s for s in sections if s["name"] == "solo"]
        assert len(solo_sections) >= 1
        pt = solo_sections[0]["per_track_description"]
        assert "drums" in pt
        assert "lead" in pt
        assert len(pt["lead"]) > 10

    def test_groove_section_has_role_templates(self):
        """Groove/vamp section has per-track descriptions."""
        prompt = "Intro, then a locked groove, then the outro. 24 bars."
        sections = parse_sections(prompt, bars=24, roles=["drums", "bass"])
        groove_sections = [s for s in sections if s["name"] == "groove"]
        assert len(groove_sections) >= 1
        pt = groove_sections[0]["per_track_description"]
        assert "drums" in pt
        assert "bass" in pt

    def test_ethio_jazz_intro_groove_solo_detected(self):
        """The Ethio-jazz prompt that triggered this fix: intro/groove/solo."""
        prompt = (
            "8-bar intro with vibraphone alone. 8-bar groove where drums enter "
            "with a 6/8 East African shuffle. 8-bar solo — alto sax takes the melody."
        )
        sections = parse_sections(prompt, bars=24, roles=["drums", "bass", "vibraphone", "alto sax"])
        names = [s["name"] for s in sections]
        assert len(sections) >= 3, f"Should detect 3+ sections from intro/groove/solo, got {names}"
        assert "intro" in names
        assert "groove" in names or "verse" in names
        assert "solo" in names
