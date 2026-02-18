"""Tests for Stori structured prompt parser (app/core/prompt_parser.py)."""

import pytest

from app.core.prompt_parser import (
    ParsedPrompt,
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
        "- kick\n"
        "- bass\n"
        "- arp\n"
        "- pad\n"
        "\n"
        "Constraints:\n"
        "- bars: 16\n"
        "- density: medium\n"
        "- instruments: analog kick, sub bass, pluck\n"
        "\n"
        "Vibe:\n"
        "- darker:2\n"
        "- hypnotic:3\n"
        "- wider:1\n"
        "\n"
        "Request:\n"
        "Build an intro groove that evolves every 4 bars and opens into a club-ready loop."
    )

    def test_parses_successfully(self):
        result = parse_prompt(self.PROMPT)
        assert result is not None

    def test_mode(self):
        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.mode == "compose"

    def test_target(self):
        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.target == TargetSpec(kind="project")

    def test_style(self):
        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.style == "melodic techno"

    def test_key(self):
        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.key == "F#m"

    def test_tempo(self):
        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.tempo == 126

    def test_roles(self):
        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.roles == ["kick", "bass", "arp", "pad"]

    def test_constraints(self):
        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.constraints["bars"] == 16
        assert result.constraints["density"] == "medium"
        assert result.constraints["instruments"] == "analog kick, sub bass, pluck"

    def test_vibes(self):
        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.vibes == [
            VibeWeight("darker", 2),
            VibeWeight("hypnotic", 3),
            VibeWeight("wider", 1),
        ]

    def test_request(self):
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
        "- punchier:3\n"
        "- tighter low end:2\n"
        "\n"
        "Constraints:\n"
        "- compressor: analog\n"
        "- eq_focus: 200hz cleanup\n"
        "\n"
        "Request:\n"
        "Tighten the bass and make it hit harder without increasing loudness."
    )

    def test_mode_is_edit(self):
        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.mode == "edit"

    def test_target_track(self):
        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.target is not None
        assert result.target.kind == "track"
        assert result.target.name == "Bass"

    def test_vibes_with_spaces_in_name(self):
        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert VibeWeight("tighter low end", 2) in result.vibes

    def test_constraints(self):
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
        "Request:\n"
        "Why does my groove feel late when I add long reverb tails?"
    )

    def test_mode_is_ask(self):
        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.mode == "ask"

    def test_no_style_or_tempo(self):
        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert result.style is None
        assert result.tempo is None
        assert result.roles == []
        assert result.vibes == []

    def test_request_text(self):
        result = parse_prompt(self.PROMPT)
        assert result is not None
        assert "reverb tails" in result.request


# ─── Minimal prompt (Mode + Request only) ───────────────────────────────────


class TestMinimalPrompt:
    def test_mode_and_request_only(self):
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
    def test_header_case_insensitive(self):
        prompt = "stori prompt\nMode: compose\nRequest: make a beat"
        result = parse_prompt(prompt)
        assert result is not None

    def test_header_mixed_case(self):
        prompt = "Stori Prompt\nMode: compose\nRequest: make a beat"
        result = parse_prompt(prompt)
        assert result is not None

    def test_field_names_case_insensitive(self):
        prompt = "STORI PROMPT\nMODE: compose\nSTYLE: jazz\nREQUEST: play jazz"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.mode == "compose"
        assert result.style == "jazz"

    def test_mode_value_case_insensitive(self):
        prompt = "STORI PROMPT\nMode: COMPOSE\nRequest: make a beat"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.mode == "compose"

    def test_mode_value_mixed_case(self):
        prompt = "STORI PROMPT\nMode: Edit\nRequest: change something"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.mode == "edit"


# ─── Target parsing ─────────────────────────────────────────────────────────


class TestTargetParsing:
    def test_project(self):
        prompt = "STORI PROMPT\nMode: compose\nTarget: project\nRequest: go"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.target == TargetSpec(kind="project")

    def test_selection(self):
        prompt = "STORI PROMPT\nMode: edit\nTarget: selection\nRequest: fix it"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.target == TargetSpec(kind="selection")

    def test_track_with_name(self):
        prompt = "STORI PROMPT\nMode: edit\nTarget: track:Lead Synth\nRequest: eq it"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.target is not None
        assert result.target.kind == "track"
        assert result.target.name == "Lead Synth"

    def test_region_with_name(self):
        prompt = "STORI PROMPT\nMode: edit\nTarget: region:Verse A\nRequest: quantize"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.target is not None
        assert result.target.kind == "region"
        assert result.target.name == "Verse A"


# ─── Tempo parsing ──────────────────────────────────────────────────────────


class TestTempoParsing:
    def test_bare_number(self):
        prompt = "STORI PROMPT\nMode: compose\nTempo: 140\nRequest: go"
        assert parse_prompt(prompt) is not None
        assert parse_prompt(prompt).tempo == 140  # type: ignore[union-attr]

    def test_with_bpm_suffix(self):
        prompt = "STORI PROMPT\nMode: compose\nTempo: 92 bpm\nRequest: go"
        assert parse_prompt(prompt) is not None
        assert parse_prompt(prompt).tempo == 92  # type: ignore[union-attr]

    def test_bpm_no_space(self):
        prompt = "STORI PROMPT\nMode: compose\nTempo: 110bpm\nRequest: go"
        assert parse_prompt(prompt) is not None
        assert parse_prompt(prompt).tempo == 110  # type: ignore[union-attr]


# ─── Role parsing ───────────────────────────────────────────────────────────


class TestRoleParsing:
    def test_inline_comma_separated(self):
        prompt = "STORI PROMPT\nMode: compose\nRole: kick, bass, arp\nRequest: go"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.roles == ["kick", "bass", "arp"]

    def test_yaml_style_list(self):
        prompt = (
            "STORI PROMPT\nMode: compose\n"
            "Role:\n- drums\n- bass\n- melody\n"
            "Request: go"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.roles == ["drums", "bass", "melody"]

    def test_single_role_inline(self):
        prompt = "STORI PROMPT\nMode: compose\nRole: bassline\nRequest: go"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.roles == ["bassline"]


# ─── Constraint parsing ─────────────────────────────────────────────────────


class TestConstraintParsing:
    def test_key_value_pairs(self):
        prompt = (
            "STORI PROMPT\nMode: compose\n"
            "Constraints:\n- bars: 8\n- density: sparse\n"
            "Request: go"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.constraints["bars"] == 8
        assert result.constraints["density"] == "sparse"

    def test_bare_flag_items(self):
        prompt = (
            "STORI PROMPT\nMode: compose\n"
            "Constraints:\n- no reverb\n"
            "Request: go"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.constraints["no reverb"] is True

    def test_numeric_coercion(self):
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
    def test_unweighted_vibes(self):
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

    def test_weighted_vibes(self):
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

    def test_inline_comma_separated_vibes(self):
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

    def test_natural_language(self):
        assert parse_prompt("make a boom bap beat") is None

    def test_simple_command(self):
        assert parse_prompt("set tempo to 120") is None

    def test_transport(self):
        assert parse_prompt("play") is None

    def test_question_about_stori_prompt(self):
        assert parse_prompt("Tell me about the STORI PROMPT format") is None

    def test_empty_string(self):
        assert parse_prompt("") is None

    def test_whitespace_only(self):
        assert parse_prompt("   \n\n  ") is None

    def test_header_only_no_fields(self):
        assert parse_prompt("STORI PROMPT") is None

    def test_header_with_no_mode(self):
        assert parse_prompt("STORI PROMPT\nRequest: do something") is None

    def test_header_with_no_request(self):
        assert parse_prompt("STORI PROMPT\nMode: compose") is None

    def test_invalid_mode_value(self):
        assert parse_prompt("STORI PROMPT\nMode: destroy\nRequest: go") is None

    def test_stori_prompt_midsentence(self):
        """'STORI PROMPT' appearing mid-text should not trigger parsing."""
        assert parse_prompt("Hey can you explain STORI PROMPT to me?") is None


# ─── Whitespace tolerance ───────────────────────────────────────────────────


class TestWhitespaceTolerance:
    def test_leading_whitespace(self):
        prompt = "  \n  STORI PROMPT\nMode: compose\nRequest: go"
        result = parse_prompt(prompt)
        assert result is not None

    def test_extra_blank_lines_between_fields(self):
        prompt = (
            "STORI PROMPT\n\n"
            "Mode: compose\n\n"
            "Style: jazz\n\n"
            "Request: make jazz"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.style == "jazz"

    def test_trailing_whitespace_on_values(self):
        prompt = "STORI PROMPT\nMode:  compose  \nRequest:  do it  "
        result = parse_prompt(prompt)
        assert result is not None
        assert result.mode == "compose"
        assert result.request == "do it"


# ─── Multi-line Request ─────────────────────────────────────────────────────


class TestMultiLineRequest:
    def test_multi_line_request_preserved(self):
        prompt = (
            "STORI PROMPT\n"
            "Mode: compose\n"
            "Request:\n"
            "Build an evolving groove.\n"
            "It should slowly open into a main loop.\n"
            "Keep the energy building."
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert "evolving groove" in result.request
        assert "main loop" in result.request
        assert "energy building" in result.request


# ─── Mixed structure + freeform ──────────────────────────────────────────────


class TestMixedStructureAndFreeform:
    """Spec rule: users may mix structure and freeform language."""

    def test_freeform_request_with_inline_hints(self):
        prompt = (
            "STORI PROMPT\n"
            "Mode: compose\n"
            "Request:\n"
            "give me a darker techno intro at 126 bpm in F#m"
        )
        result = parse_prompt(prompt)
        assert result is not None
        assert result.mode == "compose"
        assert result.tempo is None  # Not in a Tempo field
        assert "126 bpm" in result.request  # Preserved in request text


# ─── Raw field preserved ────────────────────────────────────────────────────


class TestRawPreserved:
    def test_raw_contains_original_text(self):
        prompt = "STORI PROMPT\nMode: ask\nRequest: help me"
        result = parse_prompt(prompt)
        assert result is not None
        assert result.raw == prompt
