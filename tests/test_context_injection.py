"""
Tests for combined system prompt context injection.

Verifies that structured_prompt_context and sequential_context:
  1. Each produce well-formed content independently
  2. When concatenated (as in run_pipeline / build_execution_plan), they don't
     conflict, duplicate fields, or produce broken output
  3. The combined system prompt correctly gates "use above values directly"
     and "ARRANGEMENT POSITION" blocks

Coverage:
  1. structured_prompt_context alone — all field classes
  2. sequential_context alone — all Position kinds
  3. Combined output — no duplicate sentinels, correct ordering
  4. System prompt assembly (system_prompt_base + composing/editing + context)
  5. No conflicts when both blocks are present vs. absent
  6. Fields absent from ParsedPrompt are not spuriously injected
"""
from __future__ import annotations

from typing import Literal

import pytest

from app.contracts.project_types import ProjectContext
from app.core.prompt_parser import parse_prompt, ParsedPrompt, PositionSpec
from app.core.prompts import (
    structured_prompt_context,
    structured_prompt_routing_context,
    sequential_context,
    system_prompt_base,
    composing_prompt,
    editing_prompt,
    resolve_position,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(prompt: str) -> ParsedPrompt:

    result = parse_prompt(prompt)
    assert result is not None
    return result


def _full_composing_system(parsed: ParsedPrompt, project_state: ProjectContext | None = None) -> str:

    """Assemble the exact system prompt that build_execution_plan injects."""
    project_state = project_state or {}
    sys = system_prompt_base() + "\n" + composing_prompt()
    sys += structured_prompt_context(parsed)
    if parsed.position is not None:
        start_beat = resolve_position(parsed.position, project_state)
        sys += sequential_context(start_beat, parsed.section, pos=parsed.position)
    return sys


def _full_editing_system(parsed: ParsedPrompt, project_state: ProjectContext | None = None) -> str:

    """Assemble the system prompt that run_pipeline injects for EDITING."""
    project_state = project_state or {}
    sys = system_prompt_base() + "\n" + editing_prompt(False)
    sys += structured_prompt_context(parsed)
    if parsed.position is not None:
        start_beat = resolve_position(parsed.position, project_state)
        sys += sequential_context(start_beat, parsed.section, pos=parsed.position)
    return sys


# ===========================================================================
# 1. structured_prompt_context standalone
# ===========================================================================

class TestStructuredPromptContextStandalone:
    """structured_prompt_context emits correct blocks for each field class."""

    def test_sentinel_header_present(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "═══ STORI STRUCTURED INPUT ═══" in ctx

    def test_closing_sentinel_present(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "═════════════════════════════════════" in ctx

    def test_do_not_reinfer_instruction(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "Do not re-infer" in ctx

    def test_mode_always_emitted(self) -> None:

        for mode in ("compose", "edit", "ask"):
            parsed = _parse(f"STORI PROMPT\nMode: {mode}\nRequest: go")
            ctx = structured_prompt_context(parsed)
            assert f"Mode: {mode}" in ctx

    def test_section_emitted_when_set(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nSection: chorus\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "chorus" in ctx

    def test_section_absent_when_not_set(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "Section:" not in ctx

    def test_style_emitted(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nStyle: techno\nRequest: go")
        assert "techno" in structured_prompt_context(parsed)

    def test_key_emitted(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nKey: F#m\nRequest: go")
        assert "F#m" in structured_prompt_context(parsed)

    def test_tempo_emitted(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nTempo: 140\nRequest: go")
        assert "140" in structured_prompt_context(parsed)

    def test_roles_emitted(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRole: kick, bass, arp\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "kick" in ctx and "bass" in ctx and "arp" in ctx

    def test_target_emitted(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: edit\nTarget: track:Bass\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "track" in ctx and "Bass" in ctx

    def test_maestro_dims_in_context(self) -> None:

        parsed = _parse(
            "STORI PROMPT\nMode: compose\n"
            "Harmony:\n  progression: ii-V-I\n"
            "Request: go\n"
        )
        ctx = structured_prompt_context(parsed)
        assert "MAESTRO DIMENSIONS" in ctx
        assert "ii-V-I" in ctx

    def test_no_maestro_dims_when_none(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        assert "MAESTRO DIMENSIONS" not in structured_prompt_context(parsed)

    def test_weighted_vibes_show_weight(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nVibe:\n- darker:3\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "darker" in ctx and "3" in ctx

    def test_constraints_emitted(self) -> None:

        parsed = _parse(
            "STORI PROMPT\nMode: compose\n"
            "Constraints:\n  bars: 16\n  density: high\n"
            "Request: go\n"
        )
        ctx = structured_prompt_context(parsed)
        assert "bars" in ctx or "16" in ctx


# ===========================================================================
# 2. sequential_context standalone
# ===========================================================================

class TestSequentialContextStandalone:
    """sequential_context emits ARRANGEMENT POSITION block for each kind."""

    def _pos(
        self,
        kind: Literal["after", "before", "alongside", "between", "within", "absolute", "last"],
        ref: str | None = None,
        ref2: str | None = None,
        offset: float = 0.0,
        beat: float | None = None,
    ) -> PositionSpec:
        return PositionSpec(kind=kind, ref=ref, ref2=ref2, offset=offset, beat=beat)

    def test_arrangement_position_sentinel(self) -> None:

        ctx = sequential_context(0.0, pos=self._pos("absolute"))
        assert "ARRANGEMENT POSITION" in ctx

    def test_absolute_contains_beat(self) -> None:

        ctx = sequential_context(32.0, pos=self._pos("absolute"))
        assert "32" in ctx

    def test_last_mentions_append(self) -> None:

        ctx = sequential_context(64.0, pos=self._pos("last"))
        lower = ctx.lower()
        assert "append" in lower or "existing" in lower

    def test_after_names_ref(self) -> None:

        ctx = sequential_context(16.0, pos=self._pos("after", ref="intro"))
        assert "intro" in ctx.lower()

    def test_before_names_ref(self) -> None:

        ctx = sequential_context(60.0, pos=self._pos("before", ref="chorus"))
        assert "chorus" in ctx.lower()

    def test_alongside_names_ref(self) -> None:

        ctx = sequential_context(0.0, pos=self._pos("alongside", ref="verse"))
        assert "verse" in ctx.lower()

    def test_between_names_both_refs(self) -> None:

        ctx = sequential_context(8.0, pos=self._pos("between", ref="intro", ref2="chorus"))
        assert "intro" in ctx.lower() and "chorus" in ctx.lower()

    def test_within_names_ref(self) -> None:

        ctx = sequential_context(4.0, pos=self._pos("within", ref="verse"))
        assert "verse" in ctx.lower()

    def test_section_name_appears(self) -> None:

        ctx = sequential_context(16.0, section_name="bridge",
                                 pos=self._pos("after", ref="verse"))
        assert "bridge" in ctx.lower()

    def test_no_position_returns_string(self) -> None:

        ctx = sequential_context(0.0)
        assert isinstance(ctx, str)

    def test_pickup_mentions_lead_in(self) -> None:

        ctx = sequential_context(60.0, pos=self._pos("before", ref="chorus", offset=-4.0))
        lower = ctx.lower()
        assert "lead-in" in lower or "pickup" in lower or "4" in lower


# ===========================================================================
# 3. Combined output — no conflicts
# ===========================================================================

class TestCombinedContextNoDuplicates:
    """When both blocks are concatenated, no fields are duplicated."""

    def _combined(self, prompt_str: str, project: ProjectContext | None = None) -> str:

        parsed = _parse(prompt_str)
        ctx = structured_prompt_context(parsed)
        if parsed.position is not None:
            proj: ProjectContext = project or {}
            beat = resolve_position(parsed.position, proj)
            ctx += sequential_context(beat, parsed.section, pos=parsed.position)
        return ctx

    def test_arrangement_position_appears_exactly_once(self) -> None:

        ctx = self._combined(
            "STORI PROMPT\nMode: compose\nSection: verse\nPosition: after intro\nRequest: go",
            {"tracks": [{"name": "intro", "regions": [
                {"name": "intro", "startBeat": 0, "durationBeats": 16}
            ]}]},
        )
        assert ctx.count("ARRANGEMENT POSITION") == 1

    def test_stori_structured_sentinel_exactly_once(self) -> None:

        ctx = self._combined(
            "STORI PROMPT\nMode: compose\nPosition: at 32\nRequest: go"
        )
        assert ctx.count("═══ STORI STRUCTURED INPUT ═══") == 1

    def test_structured_context_before_sequential_context(self) -> None:

        ctx = self._combined(
            "STORI PROMPT\nMode: compose\nSection: verse\nPosition: after intro\nRequest: go",
            {"tracks": [{"name": "intro", "regions": [
                {"name": "intro", "startBeat": 0, "durationBeats": 16}
            ]}]},
        )
        stori_pos = ctx.index("STORI STRUCTURED INPUT")
        arrangement_pos = ctx.index("ARRANGEMENT POSITION")
        assert stori_pos < arrangement_pos

    def test_mode_field_not_duplicated(self) -> None:

        ctx = self._combined(
            "STORI PROMPT\nMode: compose\nPosition: last\nRequest: go"
        )
        assert ctx.count("Mode: compose") == 1

    def test_no_position_no_arrangement_block(self) -> None:

        ctx = self._combined("STORI PROMPT\nMode: compose\nRequest: go")
        assert "ARRANGEMENT POSITION" not in ctx

    def test_all_maestro_dims_and_position_coexist(self) -> None:

        ctx = self._combined(
            "STORI PROMPT\nMode: compose\nSection: verse\nPosition: after intro\n"
            "Harmony:\n  progression: ii-V-I\n"
            "Request: go",
            {"tracks": [{"name": "intro", "regions": [
                {"name": "intro", "startBeat": 0, "durationBeats": 64}
            ]}]},
        )
        assert "MAESTRO DIMENSIONS" in ctx
        assert "ARRANGEMENT POSITION" in ctx
        assert "ii-V-I" in ctx
        assert "intro" in ctx.lower()


# ===========================================================================
# 4. Full system prompt assembly
# ===========================================================================

class TestFullSystemPromptAssembly:
    """The assembled system prompt contains all required sections in order."""

    def test_composing_system_has_base(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        sys = _full_composing_system(parsed)
        # system_prompt_base() returns the Stori identity section
        assert "Stori" in sys or "maestro" in sys.lower() or "composing" in sys.lower()

    def test_composing_system_has_structured_block(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nStyle: jazz\nRequest: go")
        sys = _full_composing_system(parsed)
        assert "STORI STRUCTURED INPUT" in sys
        assert "jazz" in sys

    def test_composing_system_with_position_has_arrangement_block(self) -> None:

        parsed = _parse(
            "STORI PROMPT\nMode: compose\nSection: verse\nPosition: after intro\nRequest: go"
        )
        project: ProjectContext = {"tracks": [
            {"name": "intro", "regions": [
                {"name": "intro", "startBeat": 0, "durationBeats": 64}
            ]}
        ]}
        sys = _full_composing_system(parsed, project_state=project)
        assert "ARRANGEMENT POSITION" in sys
        assert "64" in sys

    def test_editing_system_has_structured_block(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: edit\nTarget: track:Drums\nRequest: go")
        sys = _full_editing_system(parsed)
        assert "STORI STRUCTURED INPUT" in sys
        assert "edit" in sys

    def test_system_prompt_is_string(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        sys = _full_composing_system(parsed)
        assert isinstance(sys, str)
        assert len(sys) > 100

    def test_do_not_reinfer_present(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nStyle: jazz\nRequest: go")
        sys = _full_composing_system(parsed)
        assert "Do not re-infer" in sys


# ===========================================================================
# 5. Fields absent from ParsedPrompt not spuriously injected
# ===========================================================================

class TestAbsentFieldsNotInjected:
    """Fields that are None/empty in ParsedPrompt must not appear in context."""

    def test_style_absent_when_not_set(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "Style:" not in ctx

    def test_key_absent_when_not_set(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "Key:" not in ctx

    def test_tempo_absent_when_not_set(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "Tempo:" not in ctx

    def test_roles_absent_when_not_set(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "Roles:" not in ctx

    def test_target_absent_when_not_set(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "Target:" not in ctx

    def test_section_absent_when_not_set(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "Section:" not in ctx

    def test_constraints_absent_when_not_set(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "Constraints:" not in ctx

    def test_vibes_absent_when_not_set(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        ctx = structured_prompt_context(parsed)
        assert "Vibes:" not in ctx


# ===========================================================================
# 7. structured_prompt_routing_context — planner-only (no extensions)
# ===========================================================================

class TestStructuredPromptRoutingContext:
    """Routing-only context excludes Maestro dimensions but keeps routing."""

    def test_routing_excludes_maestro_dimensions(self) -> None:

        """The planner should never see Harmony/Melody/etc. extensions."""
        parsed = _parse(
            "STORI PROMPT\nMode: compose\n"
            "Harmony: ii-V-I\nMelody: scalar runs\n"
            "Request: go\n"
        )
        ctx = structured_prompt_routing_context(parsed)
        assert "MAESTRO DIMENSIONS" not in ctx
        assert "ii-V-I" not in ctx
        assert "scalar runs" not in ctx

    def test_routing_includes_mode(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRequest: go")
        ctx = structured_prompt_routing_context(parsed)
        assert "Mode: compose" in ctx

    def test_routing_includes_style(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nStyle: techno\nRequest: go")
        ctx = structured_prompt_routing_context(parsed)
        assert "techno" in ctx

    def test_routing_includes_key(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nKey: Dm\nRequest: go")
        ctx = structured_prompt_routing_context(parsed)
        assert "Dm" in ctx

    def test_routing_includes_tempo(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nTempo: 128\nRequest: go")
        ctx = structured_prompt_routing_context(parsed)
        assert "128" in ctx

    def test_routing_includes_roles(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nRole: drums, bass\nRequest: go")
        ctx = structured_prompt_routing_context(parsed)
        assert "drums" in ctx
        assert "bass" in ctx

    def test_routing_includes_vibes(self) -> None:

        parsed = _parse("STORI PROMPT\nMode: compose\nVibe:\n- dark:2\nRequest: go")
        ctx = structured_prompt_routing_context(parsed)
        assert "dark" in ctx

    def test_routing_includes_constraints(self) -> None:

        parsed = _parse(
            "STORI PROMPT\nMode: compose\n"
            "Constraints:\n  bars: 16\n  density: high\n"
            "Request: go\n"
        )
        ctx = structured_prompt_routing_context(parsed)
        assert "bars" in ctx or "16" in ctx

    def test_routing_shorter_than_full_with_extensions(self) -> None:

        """Routing context must be strictly shorter when extensions are present."""
        parsed = _parse(
            "STORI PROMPT\nMode: compose\nStyle: jazz\n"
            "Harmony: ii-V-I\nMelody: bebop phrases\n"
            "Rhythm: swung 8ths\nDynamics: mp to ff crescendo\n"
            "Request: go\n"
        )
        full = structured_prompt_context(parsed)
        routing = structured_prompt_routing_context(parsed)
        assert len(routing) < len(full)

    def test_full_context_still_includes_extensions(self) -> None:

        """structured_prompt_context unchanged — still has MAESTRO DIMENSIONS."""
        parsed = _parse(
            "STORI PROMPT\nMode: compose\n"
            "Harmony: ii-V-I\n"
            "Request: go\n"
        )
        ctx = structured_prompt_context(parsed)
        assert "MAESTRO DIMENSIONS" in ctx
        assert "ii-V-I" in ctx
