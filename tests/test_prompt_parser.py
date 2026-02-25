"""Tests for Stori structured prompt parser (app/core/prompt_parser.py)."""
from __future__ import annotations

import pytest

from app.core.prompt_parser import (
    AfterSpec,
    ParsedPrompt,
    PositionSpec,
    TargetSpec,
    VibeWeight,
    parse_prompt,
)


# ─── Full structured prompt ─────────────────────────────────────────────────


class TestFullParse:
    """Spec Example 1: compose with all fields populated."""

    PROMPT = (
        "STORI PROMPT\n"
        "Mode: compose\n"
        "Target: project\n"
        "Style: melodic techno\n"
        "Key: F#m\n"
        "Tempo: 126\n"
        "\n"
        "Role:\n"
        "  - kick\n"
        "  - bass\n"
        "  - arp\n"
        "  - pad\n"
        "\n"
        "Constraints:\n"
        "  bars: 16\n"
        "  density: medium\n"
        "  instruments: analog kick, sub bass, pluck\n"
        "\n"
        "Vibe:\n"
        "  - darker:2\n"
        "  - hypnotic:3\n"
        "  - wider:1\n"
        "\n"
        "Request: |\n"
        "  Build an intro groove that evolves every 4 bars and opens into a club-ready loop.\n"
    )

    def test_parses_successfully(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None

    def test_mode(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.mode == "compose"

    def test_target(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.target == TargetSpec(kind="project")

    def test_style(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.style == "melodic techno"

    def test_key(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.key == "F#m"

    def test_tempo(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.tempo == 126

    def test_roles(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.roles == ["kick", "bass", "arp", "pad"]

    def test_constraints(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.constraints["bars"] == 16
        assert result.constraints["density"] == "medium"
        assert result.constraints["instruments"] == "analog kick, sub bass, pluck"

    def test_vibes(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.vibes == [
            VibeWeight("darker", 2),
            VibeWeight("hypnotic", 3),
            VibeWeight("wider", 1),
        ]

    def test_request(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert "intro groove" in result.request
        assert "club-ready loop" in result.request


# ─── Spec Example 2: Edit Track ─────────────────────────────────────────────


class TestEditTrackParse:
    """Spec Example 2: edit mode targeting a specific track."""

    PROMPT = (
        "STORI PROMPT\n"
        "Mode: edit\n"
        "Target: track:Bass\n"
        "\n"
        "Vibe:\n"
        "  - punchier:3\n"
        "  - tighter low end:2\n"
        "\n"
        "Constraints:\n"
        "  compressor: analog\n"
        "  eq_focus: 200hz cleanup\n"
        "\n"
        "Request: Tighten the bass and make it hit harder without increasing loudness.\n"
    )

    def test_mode_is_edit(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.mode == "edit"

    def test_target_track(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.target is not None
        assert result.target.kind == "track"
        assert result.target.name == "Bass"

    def test_vibes_with_spaces_in_name(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert VibeWeight("tighter low end", 2) in result.vibes

    def test_constraints(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.constraints["compressor"] == "analog"
        assert result.constraints["eq_focus"] == "200hz cleanup"


# ─── Spec Example 3: Ask / Reasoning ────────────────────────────────────────


class TestAskParse:
    """Spec Example 3: ask mode (reasoning only)."""

    PROMPT = (
        "STORI PROMPT\n"
        "Mode: ask\n"
        "Target: project\n"
        "\n"
        "Request: Why does my groove feel late when I add long reverb tails?\n"
    )

    def test_mode_is_ask(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.mode == "ask"

    def test_no_style_or_tempo(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.style is None
        assert result.tempo is None
        assert result.roles == []
        assert result.vibes == []

    def test_request_text(self) -> None:

        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert "reverb tails" in result.request


# ─── Minimal prompt (Mode + Request only) ───────────────────────────────────


class TestMinimalPrompt:
    def test_mode_and_request_only(self) -> None:

        prompt = "STORI PROMPT\nMode: compose\nRequest: make a beat"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.mode == "compose"
        assert result.request == "make a beat"
        assert result.target is None
        assert result.style is None
        assert result.key is None
        assert result.tempo is None
        assert result.roles == []
        assert result.constraints == {}
        assert result.vibes == []


# ─── Case insensitivity ─────────────────────────────────────────────────────


class TestCaseInsensitivity:
    def test_header_case_insensitive(self) -> None:

        prompt = "stori prompt\nMode: compose\nRequest: make a beat"
        result = parse_prompt(prompt)
        assert result is not None

    def test_header_mixed_case(self) -> None:

        prompt = "Stori Prompt\nMode: compose\nRequest: make a beat"
        result = parse_prompt(prompt)
        assert result is not None

    def test_field_names_case_insensitive(self) -> None:

        prompt = "STORI PROMPT\nMODE: compose\nSTYLE: jazz\nREQUEST: play jazz"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.mode == "compose"
        assert result.style == "jazz"

    def test_mode_value_case_insensitive(self) -> None:

        prompt = "STORI PROMPT\nMode: COMPOSE\nRequest: make a beat"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.mode == "compose"

    def test_mode_value_mixed_case(self) -> None:

        prompt = "STORI PROMPT\nMode: Edit\nRequest: change something"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.mode == "edit"


# ─── Target parsing ─────────────────────────────────────────────────────────


class TestTargetParsing:
    def test_project(self) -> None:

        prompt = "STORI PROMPT\nMode: compose\nTarget: project\nRequest: go"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.target == TargetSpec(kind="project")

    def test_selection(self) -> None:

        prompt = "STORI PROMPT\nMode: edit\nTarget: selection\nRequest: fix it"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.target == TargetSpec(kind="selection")

    def test_track_with_name(self) -> None:

        prompt = "STORI PROMPT\nMode: edit\nTarget: track:Lead Synth\nRequest: eq it"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.target is not None
        assert result.target.kind == "track"
        assert result.target.name == "Lead Synth"

    def test_region_with_name(self) -> None:

        prompt = "STORI PROMPT\nMode: edit\nTarget: region:Verse A\nRequest: quantize"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.target is not None
        assert result.target.kind == "region"
        assert result.target.name == "Verse A"


# ─── Tempo parsing ──────────────────────────────────────────────────────────


class TestTempoParsing:
    def test_bare_number(self) -> None:

        prompt = "STORI PROMPT\nMode: compose\nTempo: 140\nRequest: go"
        assert parse_prompt(prompt) is not None
        assert parse_prompt(prompt).tempo == 140  # type: ignore[union-attr]

    def test_with_bpm_suffix(self) -> None:

        prompt = "STORI PROMPT\nMode: compose\nTempo: 92 bpm\nRequest: go"
        assert parse_prompt(prompt) is not None
        assert parse_prompt(prompt).tempo == 92  # type: ignore[union-attr]

    def test_bpm_no_space(self) -> None:

        prompt = "STORI PROMPT\nMode: compose\nTempo: 110bpm\nRequest: go"
        assert parse_prompt(prompt) is not None
        assert parse_prompt(prompt).tempo == 110  # type: ignore[union-attr]


# ─── Role parsing ───────────────────────────────────────────────────────────


class TestRoleParsing:
    def test_inline_comma_separated(self) -> None:

        prompt = "STORI PROMPT\nMode: compose\nRole: kick, bass, arp\nRequest: go"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.roles == ["kick", "bass", "arp"]

    def test_yaml_style_list(self) -> None:

        prompt = (
            "STORI PROMPT\nMode: compose\n"
            "Role:\n- drums\n- bass\n- melody\n"
            "Request: go"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.roles == ["drums", "bass", "melody"]

    def test_single_role_inline(self) -> None:

        prompt = "STORI PROMPT\nMode: compose\nRole: bassline\nRequest: go"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.roles == ["bassline"]


# ─── Constraint parsing ─────────────────────────────────────────────────────


class TestConstraintParsing:
    def test_key_value_pairs(self) -> None:

        prompt = (
            "STORI PROMPT\nMode: compose\n"
            "Constraints:\n- bars: 8\n- density: sparse\n"
            "Request: go"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.constraints["bars"] == 8
        assert result.constraints["density"] == "sparse"

    def test_bare_flag_items(self) -> None:

        prompt = (
            "STORI PROMPT\nMode: compose\n"
            "Constraints:\n- no reverb\n"
            "Request: go"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.constraints["no reverb"] is True

    def test_numeric_coercion(self) -> None:

        prompt = (
            "STORI PROMPT\nMode: compose\n"
            "Constraints:\n- gm_program: 38\n"
            "Request: go"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.constraints["gm_program"] == 38
        assert isinstance(result.constraints["gm_program"], int)


# ─── Vibe parsing ───────────────────────────────────────────────────────────


class TestVibeParsing:
    def test_unweighted_vibes(self) -> None:

        prompt = (
            "STORI PROMPT\nMode: edit\n"
            "Vibe:\n- darker\n- punchier\n"
            "Request: fix it"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.vibes == [
            VibeWeight("darker", 1),
            VibeWeight("punchier", 1),
        ]

    def test_weighted_vibes(self) -> None:

        prompt = (
            "STORI PROMPT\nMode: edit\n"
            "Vibe:\n- darker:2\n- wider:1\n- aggressive:3\n"
            "Request: mix it"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert VibeWeight("darker", 2) in result.vibes
        assert VibeWeight("wider", 1) in result.vibes
        assert VibeWeight("aggressive", 3) in result.vibes

    def test_inline_comma_separated_vibes(self) -> None:

        prompt = (
            "STORI PROMPT\nMode: edit\n"
            "Vibe: darker, wider\n"
            "Request: mix"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.vibes == [
            VibeWeight("darker", 1),
            VibeWeight("wider", 1),
        ]


# ─── Non-structured prompts return None ─────────────────────────────────────


class TestNonStructuredFallthrough:
    """Prompts that are NOT structured prompts must return None."""

    def test_natural_language(self) -> None:

        assert parse_prompt("make a boom bap beat") is None

    def test_simple_command(self) -> None:

        assert parse_prompt("set tempo to 120") is None

    def test_transport(self) -> None:

        assert parse_prompt("play") is None

    def test_question_about_stori_prompt(self) -> None:

        assert parse_prompt("Tell me about the STORI PROMPT format") is None

    def test_empty_string(self) -> None:

        assert parse_prompt("") is None

    def test_whitespace_only(self) -> None:

        assert parse_prompt("   \n\n  ") is None

    def test_header_only_no_fields(self) -> None:

        assert parse_prompt("STORI PROMPT") is None

    def test_header_with_no_mode(self) -> None:

        assert parse_prompt("STORI PROMPT\nRequest: do something") is None

    def test_header_with_no_request(self) -> None:

        """Mode: compose without Request synthesises a default request."""
        result = parse_prompt("STORI PROMPT\nMode: compose")
        assert result is not None
        assert result.mode == "compose"
        assert result.request  # synthesised default

    def test_invalid_mode_value(self) -> None:

        assert parse_prompt("STORI PROMPT\nMode: destroy\nRequest: go") is None

    def test_stori_prompt_midsentence(self) -> None:

        """'STORI PROMPT' appearing mid-text should not trigger parsing."""
        assert parse_prompt("Hey can you explain STORI PROMPT to me?") is None

    def test_invalid_yaml_body_returns_none(self) -> None:

        """Body that is not valid YAML falls through to NL pipeline — no fallback."""
        bad = (
            "STORI PROMPT\n"
            "Mode: compose\n"
            "Request:\n"
            "This line: has a colon that breaks YAML parsing mid-key\n"
            ": and this is clearly wrong\n"
        )
        assert parse_prompt(bad) is None


# ─── Whitespace tolerance ───────────────────────────────────────────────────


class TestWhitespaceTolerance:
    def test_leading_whitespace(self) -> None:

        prompt = "  \n  STORI PROMPT\nMode: compose\nRequest: go"
        result = parse_prompt(prompt)
        assert result is not None

    def test_extra_blank_lines_between_fields(self) -> None:

        prompt = (
            "STORI PROMPT\n\n"
            "Mode: compose\n\n"
            "Style: jazz\n\n"
            "Request: make jazz"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.style == "jazz"

    def test_trailing_whitespace_on_values(self) -> None:

        prompt = "STORI PROMPT\nMode:  compose  \nRequest:  do it  "
        result = parse_prompt(prompt)
        assert result is not None
        assert result.mode == "compose"
        assert result.request == "do it"


# ─── Multi-line Request ─────────────────────────────────────────────────────


class TestMultiLineRequest:
    def test_multi_line_request_block_scalar(self) -> None:

        """Multi-line Request uses YAML block scalar (|)."""
        prompt = (
            "STORI PROMPT\n"
            "Mode: compose\n"
            "Request: |\n"
            "  Build an evolving groove.\n"
            "  It should slowly open into a main loop.\n"
            "  Keep the energy building.\n"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert "evolving groove" in result.request
        assert "main loop" in result.request
        assert "energy building" in result.request


# ─── Mixed structure + freeform ──────────────────────────────────────────────


class TestMixedStructureAndFreeform:
    """Spec rule: users may mix structure and freeform language."""

    def test_freeform_request_inline(self) -> None:

        prompt = (
            "STORI PROMPT\n"
            "Mode: compose\n"
            "Request: give me a darker techno intro at 126 bpm in F#m\n"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.mode == "compose"
        assert result.tempo is None  # Not in a Tempo field
        assert "126 bpm" in result.request  # Preserved in request text


# ─── Raw field preserved ────────────────────────────────────────────────────


class TestRawPreserved:
    def test_raw_contains_original_text(self) -> None:

        prompt = "STORI PROMPT\nMode: ask\nRequest: help me"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.raw == prompt


# ─── YAML body parsing ───────────────────────────────────────────────────────


class TestYamlBodyParsing:
    """The body is parsed as YAML; all routing fields work via YAML."""

    def test_yaml_list_for_role(self) -> None:

        prompt = "STORI PROMPT\nMode: compose\nRole:\n  - drums\n  - bass\nRequest: go"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.roles == ["drums", "bass"]

    def test_yaml_inline_list_for_role(self) -> None:

        prompt = "STORI PROMPT\nMode: compose\nRole: [kick, bass, arp]\nRequest: go"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.roles == ["kick", "bass", "arp"]

    def test_yaml_nested_constraints(self) -> None:

        prompt = (
            "STORI PROMPT\nMode: compose\n"
            "Constraints:\n  bars: 8\n  density: sparse\nRequest: go"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.constraints.get("bars") == 8
        assert result.constraints.get("density") == "sparse"

    def test_yaml_block_scalar_request(self) -> None:

        prompt = (
            "STORI PROMPT\nMode: compose\n"
            "Request: |\n  Line one.\n  Line two.\n"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert "Line one." in result.request
        assert "Line two." in result.request

    def test_yaml_tempo_as_integer(self) -> None:

        prompt = "STORI PROMPT\nMode: compose\nTempo: 75\nRequest: go"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.tempo == 75

    def test_yaml_vibe_x_weight_syntax(self) -> None:

        prompt = "STORI PROMPT\nMode: compose\nVibe: [dusty x3, warm x2]\nRequest: go"
        result = parse_prompt(prompt)
        assert result is not None
        assert any(v.vibe == "dusty" and v.weight == 3 for v in result.vibes)
        assert any(v.vibe == "warm" and v.weight == 2 for v in result.vibes)


# ─── Maestro extensions ──────────────────────────────────────────────────────


class TestMaestroExtensions:
    """Unknown top-level fields land in ParsedPrompt.extensions."""

    _PROMPT = (
        "STORI PROMPT\n"
        "Mode: compose\n"
        "Request: verse groove\n"
        "Harmony:\n"
        "  progression: [Cm7, Abmaj7]\n"
        "  voicing: rootless\n"
        "Melody:\n"
        "  scale: C dorian\n"
        "  register: mid\n"
        "Expression:\n"
        "  narrative: 3am, empty diner\n"
        "  arc: melancholic to hopeful\n"
    )

    def test_extensions_populated(self) -> None:

        result = parse_prompt(self._PROMPT)
        assert result is not None
        assert result.extensions

    def test_harmony_in_extensions(self) -> None:

        result = parse_prompt(self._PROMPT)
        assert result is not None
        assert "harmony" in result.extensions
        harmony = result.extensions["harmony"]
        assert isinstance(harmony, dict)
        assert "progression" in harmony
        assert harmony["voicing"] == "rootless"

    def test_melody_in_extensions(self) -> None:

        result = parse_prompt(self._PROMPT)
        assert result is not None
        assert "melody" in result.extensions
        assert result.extensions["melody"]["scale"] == "C dorian"

    def test_expression_in_extensions(self) -> None:

        result = parse_prompt(self._PROMPT)
        assert result is not None
        assert "expression" in result.extensions
        assert "3am" in result.extensions["expression"]["narrative"]

    def test_routing_fields_not_in_extensions(self) -> None:

        result = parse_prompt(self._PROMPT)
        assert result is not None
        for routing_field in ("mode", "request", "style", "key", "tempo"):
            assert routing_field not in result.extensions

    def test_has_maestro_fields_property(self) -> None:

        result = parse_prompt(self._PROMPT)
        assert result is not None
        assert result.has_maestro_fields is True

    def test_no_maestro_fields_when_empty(self) -> None:

        result = parse_prompt("STORI PROMPT\nMode: ask\nRequest: help")
        assert result is not None
        assert result.has_maestro_fields is False

    def test_full_maestro_prompt_parses(self) -> None:

        """The full Maestro example from the spec must parse cleanly."""
        prompt = (
            "STORI PROMPT\n"
            "Mode: compose\n"
            "Section: verse\n"
            "Position: after intro + 2\n"
            "Style: lofi hip hop\n"
            "Key: Cm\n"
            "Tempo: 75\n"
            "Role: [drums, bass, piano, melody]\n"
            "Constraints:\n"
            "  bars: 8\n"
            "  density: medium-sparse\n"
            "Vibe: [dusty x3, warm x2, laid back]\n"
            "Request: Verse groove with lazy boom bap.\n"
            "Harmony:\n"
            "  progression: [Cm7, Abmaj7, Ebmaj7, Bb7sus4]\n"
            "  voicing: rootless close position\n"
            "Melody:\n"
            "  scale: C dorian\n"
            "  register: mid\n"
            "Rhythm:\n"
            "  feel: behind the beat\n"
            "  swing: 55%\n"
            "Dynamics:\n"
            "  overall: mp throughout\n"
            "Orchestration:\n"
            "  drums:\n"
            "    kit: boom bap\n"
            "    kick: slightly late\n"
            "Effects:\n"
            "  drums:\n"
            "    saturation: tape, subtle\n"
            "Expression:\n"
            "  arc: resignation to acceptance\n"
            "  narrative: Late night, alone. At peace with it.\n"
            "Texture:\n"
            "  density: medium-sparse\n"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.mode == "compose"
        assert result.section == "verse"
        assert result.tempo == 75
        assert result.key == "Cm"
        assert "drums" in result.roles
        assert result.constraints.get("bars") == 8
        assert result.position is not None
        assert result.position.kind == "after"
        # All Maestro dimensions captured
        for dim in ("harmony", "melody", "rhythm", "dynamics", "orchestration", "effects", "expression", "texture"):
            assert dim in result.extensions, f"Missing dimension: {dim}"


# ─── Section field ───────────────────────────────────────────────────────────


class TestSectionField:
    def test_section_parsed_to_lowercase(self) -> None:

        result = parse_prompt("STORI PROMPT\nMode: compose\nSection: Intro\nRequest: go")
        assert result is not None
        assert result.section == "intro"

    def test_section_absent_is_none(self) -> None:

        result = parse_prompt("STORI PROMPT\nMode: compose\nRequest: go")
        assert result is not None
        assert result.section is None

    def test_section_verse(self) -> None:

        result = parse_prompt("STORI PROMPT\nMode: compose\nSection: verse\nRequest: go")
        assert result is not None
        assert result.section == "verse"


# ─── Position: field (canonical) ────────────────────────────────────────────


class TestPositionField:
    """Tests for the full Position: field vocabulary."""

    def _p(self, position_val: str) -> PositionSpec | None:

        result = parse_prompt(f"STORI PROMPT\nMode: compose\nPosition: {position_val}\nRequest: go")
        assert result is not None
        return result.position

    # Absolute
    def test_absolute_integer(self) -> None:

        pos = self._p("32")
        assert pos is not None and pos.kind == "absolute" and pos.beat == 32.0

    def test_absolute_beat_keyword(self) -> None:

        pos = self._p("beat 64")
        assert pos is not None and pos.kind == "absolute" and pos.beat == 64.0

    def test_absolute_at_keyword(self) -> None:

        pos = self._p("at 16")
        assert pos is not None and pos.kind == "absolute" and pos.beat == 16.0

    def test_absolute_bar(self) -> None:

        pos = self._p("at bar 5")
        assert pos is not None and pos.kind == "absolute" and pos.beat == 16.0  # (5-1)*4

    # Last
    def test_last(self) -> None:

        pos = self._p("last")
        assert pos is not None and pos.kind == "last"

    # After
    def test_after_section(self) -> None:

        pos = self._p("after intro")
        assert pos is not None and pos.kind == "after" and pos.ref == "intro"

    def test_after_with_positive_offset(self) -> None:

        pos = self._p("after intro + 2")
        assert pos is not None and pos.kind == "after" and pos.ref == "intro" and pos.offset == 2.0

    # Before
    def test_before_section(self) -> None:

        pos = self._p("before chorus")
        assert pos is not None and pos.kind == "before" and pos.ref == "chorus"

    def test_before_pickup_negative_offset(self) -> None:

        pos = self._p("before chorus - 4")
        assert pos is not None and pos.kind == "before" and pos.ref == "chorus" and pos.offset == -4.0

    # Alongside
    def test_alongside_section(self) -> None:

        pos = self._p("alongside verse")
        assert pos is not None and pos.kind == "alongside" and pos.ref == "verse"

    def test_alongside_with_offset(self) -> None:

        pos = self._p("alongside verse + 8")
        assert pos is not None and pos.kind == "alongside" and pos.ref == "verse" and pos.offset == 8.0

    # Between
    def test_between_two_sections(self) -> None:

        pos = self._p("between intro verse")
        assert pos is not None and pos.kind == "between"
        assert pos.ref == "intro" and pos.ref2 == "verse"

    # Within
    def test_within_section(self) -> None:

        pos = self._p("within verse bar 3")
        assert pos is not None and pos.kind == "within" and pos.ref == "verse"
        assert pos.offset == 8.0  # (3-1)*4

    # Absent
    def test_position_absent_is_none(self) -> None:

        result = parse_prompt("STORI PROMPT\nMode: compose\nRequest: go")
        assert result is not None and result.position is None

    # After: alias still works
    def test_after_alias_maps_to_after_kind(self) -> None:

        result = parse_prompt("STORI PROMPT\nMode: compose\nAfter: intro\nRequest: go")
        assert result is not None
        pos = result.position
        assert pos is not None and pos.kind == "after" and pos.ref == "intro"

    def test_after_alias_last(self) -> None:

        result = parse_prompt("STORI PROMPT\nMode: compose\nAfter: last\nRequest: go")
        assert result is not None
        pos = result.position
        assert pos is not None and pos.kind == "last"

    def test_position_wins_over_after(self) -> None:

        """Position: takes precedence over After: when both are present."""
        result = parse_prompt(
            "STORI PROMPT\nMode: compose\nPosition: alongside verse\nAfter: intro\nRequest: go"
        )
        assert result is not None
        pos = result.position
        assert pos is not None and pos.kind == "alongside"

    # .after property is backwards-compatible
    def test_after_property_alias(self) -> None:

        result = parse_prompt("STORI PROMPT\nMode: compose\nPosition: after chorus\nRequest: go")
        assert result is not None
        assert result.after is result.position


# ─── resolve_position ────────────────────────────────────────────────────────


class TestResolvePosition:
    """Tests for prompts.resolve_position()."""

    _PROJECT = {
        "tracks": [
            {"name": "Intro Drums", "regions": [
                {"startBeat": 0, "durationBeats": 16},
            ]},
            {"name": "Intro Bass", "regions": [
                {"startBeat": 0, "durationBeats": 16},
                {"startBeat": 16, "durationBeats": 4},   # ends at 20
            ]},
            {"name": "Verse Pad", "regions": [
                {"startBeat": 20, "durationBeats": 16},  # ends at 36
            ]},
            {"name": "Chorus Lead", "regions": [
                {"startBeat": 36, "durationBeats": 16},  # ends at 52
            ]},
        ]
    }

    def _resolve(self, pos: PositionSpec) -> float:

        from app.core.prompts import resolve_position
        return resolve_position(pos, self._PROJECT)

    def test_absolute(self) -> None:

        assert self._resolve(PositionSpec(kind="absolute", beat=48.0)) == 48.0

    def test_absolute_with_offset(self) -> None:

        assert self._resolve(PositionSpec(kind="absolute", beat=0.0, offset=4.0)) == 4.0

    def test_last(self) -> None:

        assert self._resolve(PositionSpec(kind="last")) == 52.0

    def test_last_empty_project(self) -> None:

        from app.core.prompts import resolve_position
        assert resolve_position(PositionSpec(kind="last"), {}) == 0.0

    def test_after_intro(self) -> None:

        # Intro ends at beat 20
        assert self._resolve(PositionSpec(kind="after", ref="intro")) == 20.0

    def test_after_intro_with_offset(self) -> None:

        assert self._resolve(PositionSpec(kind="after", ref="intro", offset=2.0)) == 22.0

    def test_before_chorus(self) -> None:

        # Chorus starts at beat 36
        assert self._resolve(PositionSpec(kind="before", ref="chorus")) == 36.0

    def test_before_chorus_pickup(self) -> None:

        # 4-beat pickup into chorus
        assert self._resolve(PositionSpec(kind="before", ref="chorus", offset=-4.0)) == 32.0

    def test_alongside_verse(self) -> None:

        # Verse starts at beat 20
        assert self._resolve(PositionSpec(kind="alongside", ref="verse")) == 20.0

    def test_alongside_verse_late_entry(self) -> None:

        assert self._resolve(PositionSpec(kind="alongside", ref="verse", offset=8.0)) == 28.0

    def test_between_intro_verse(self) -> None:

        # Intro ends at 20, Verse starts at 20 → gap is 0, midpoint = 20
        assert self._resolve(PositionSpec(kind="between", ref="intro", ref2="verse")) == 20.0

    def test_within_verse_bar3(self) -> None:

        # Verse starts at beat 20; bar 3 = +8 beats
        assert self._resolve(PositionSpec(kind="within", ref="verse", offset=8.0)) == 28.0

    def test_unknown_section_falls_back_to_last(self) -> None:

        assert self._resolve(PositionSpec(kind="after", ref="bridge")) == 52.0

    def test_backwards_compat_resolve_after_beat(self) -> None:

        from app.core.prompts import resolve_after_beat
        pos = PositionSpec(kind="after", ref="intro")
        assert resolve_after_beat(pos, self._PROJECT) == 20.0
