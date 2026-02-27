"""DAW adapter layer tests — ports, registry, phase mapping, and type contracts.

Covers the ``app.daw.ports`` protocol, ``app.daw.stori`` concrete adapter,
named type aliases (``PhaseSplit``, ``InstrumentGroups``, ``ToolMetaRegistry``,
``ToolCategoryEntry``), and the tool registry invariants.
"""
from __future__ import annotations

import pytest

from maestro.contracts.json_types import JSONValue
from maestro.core.expansion import ToolCall


# ---------------------------------------------------------------------------
# PhaseSplit NamedTuple contract
# ---------------------------------------------------------------------------


class TestPhaseSplit:
    """PhaseSplit NamedTuple exposes named fields and supports destructuring."""

    def _tc(self, name: str, params: dict[str, JSONValue] | None = None) -> ToolCall:
        return ToolCall(name=name, params=params or {})

    def _sample_calls(self) -> list[ToolCall]:
        return [
            self._tc("stori_set_tempo", {"tempo": 120}),
            self._tc("stori_add_midi_track", {"name": "Drums"}),
            self._tc("stori_generate_midi", {"trackName": "Drums", "role": "drums", "style": "funk", "tempo": 120, "bars": 4}),
            self._tc("stori_ensure_bus", {"name": "Reverb"}),
        ]

    def test_named_field_access(self) -> None:
        """PhaseSplit fields are accessible by name."""
        from maestro.daw.stori.phase_map import PhaseSplit, group_into_phases

        result = group_into_phases(self._sample_calls())
        assert isinstance(result, PhaseSplit)
        assert len(result.setup) == 1
        assert result.setup[0].name == "stori_set_tempo"
        assert "drums" in result.instruments
        assert len(result.instruments["drums"]) == 2
        assert result.instrument_order == ["drums"]
        assert len(result.mixing) == 1
        assert result.mixing[0].name == "stori_ensure_bus"

    def test_positional_destructuring(self) -> None:
        """PhaseSplit supports tuple-style positional unpacking."""
        from maestro.daw.stori.phase_map import group_into_phases

        setup, instruments, order, mixing = group_into_phases(self._sample_calls())
        assert len(setup) == 1
        assert "drums" in instruments
        assert order == ["drums"]
        assert len(mixing) == 1

    def test_empty_input_all_fields_empty(self) -> None:
        """Empty tool call list produces PhaseSplit with all empty fields."""
        from maestro.daw.stori.phase_map import PhaseSplit, group_into_phases

        result = group_into_phases([])
        assert isinstance(result, PhaseSplit)
        assert result.setup == []
        assert result.instruments == {}
        assert result.instrument_order == []
        assert result.mixing == []

    def test_is_tuple_subclass(self) -> None:
        """PhaseSplit is a tuple (NamedTuple contract)."""
        from maestro.daw.stori.phase_map import PhaseSplit, group_into_phases

        result = group_into_phases(self._sample_calls())
        assert isinstance(result, tuple)
        assert len(result) == 4

    def test_setup_only(self) -> None:
        """All-setup calls land exclusively in the setup field."""
        from maestro.daw.stori.phase_map import group_into_phases

        calls = [
            self._tc("stori_set_tempo", {"tempo": 90}),
            self._tc("stori_set_key", {"key": "Am"}),
        ]
        result = group_into_phases(calls)
        assert len(result.setup) == 2
        assert result.instruments == {}
        assert result.mixing == []

    def test_mixing_only(self) -> None:
        """All-mixing calls land exclusively in the mixing field."""
        from maestro.daw.stori.phase_map import group_into_phases

        calls = [
            self._tc("stori_ensure_bus", {"name": "Reverb"}),
            self._tc("stori_add_send", {"trackName": "Bass", "busId": "bus-1"}),
            self._tc("stori_set_track_volume", {"trackName": "Bass", "volume": -3}),
            self._tc("stori_set_track_pan", {"trackName": "Bass", "pan": 30}),
            self._tc("stori_mute_track", {"trackName": "Drums", "muted": True}),
            self._tc("stori_solo_track", {"trackName": "Bass", "solo": True}),
        ]
        result = group_into_phases(calls)
        assert result.setup == []
        assert result.instruments == {}
        assert len(result.mixing) == 6

    def test_multiple_instruments_grouped(self) -> None:
        """Multiple instruments produce separate groups with preserved order."""
        from maestro.daw.stori.phase_map import group_into_phases

        calls = [
            self._tc("stori_add_midi_track", {"name": "Strings"}),
            self._tc("stori_add_notes", {"trackName": "Strings", "regionId": "r1", "notes": []}),
            self._tc("stori_add_midi_track", {"name": "Bass"}),
            self._tc("stori_add_notes", {"trackName": "Bass", "regionId": "r2", "notes": []}),
        ]
        result = group_into_phases(calls)
        assert result.instrument_order == ["strings", "bass"]
        assert len(result.instruments["strings"]) == 2
        assert len(result.instruments["bass"]) == 2

    def test_unresolvable_instrument_falls_to_mixing(self) -> None:
        """Calls without a resolvable instrument go to mixing phase."""
        from maestro.daw.stori.phase_map import group_into_phases

        calls = [self._tc("stori_add_insert_effect", {"type": "reverb"})]
        result = group_into_phases(calls)
        assert result.instruments == {}
        assert len(result.mixing) == 1


# ---------------------------------------------------------------------------
# InstrumentGroups type alias
# ---------------------------------------------------------------------------


class TestInstrumentGroups:
    """InstrumentGroups alias keys are always lowercased instrument names."""

    def _tc(self, name: str, params: dict[str, JSONValue] | None = None) -> ToolCall:
        return ToolCall(name=name, params=params or {})

    def test_keys_are_lowercased(self) -> None:
        """Instrument group keys are normalised to lowercase."""
        from maestro.daw.stori.phase_map import group_into_phases

        calls = [
            self._tc("stori_add_midi_track", {"name": "Electric Piano"}),
            self._tc("stori_add_midi_track", {"name": "DRUMS"}),
        ]
        result = group_into_phases(calls)
        assert "electric piano" in result.instruments
        assert "drums" in result.instruments

    def test_duplicate_instrument_appends(self) -> None:
        """Multiple calls for the same instrument accumulate in one group."""
        from maestro.daw.stori.phase_map import group_into_phases

        calls = [
            self._tc("stori_add_midi_track", {"name": "Bass"}),
            self._tc("stori_add_midi_region", {"trackName": "Bass", "startBeat": 0, "durationBeats": 8}),
            self._tc("stori_add_notes", {"trackName": "Bass", "regionId": "r1", "notes": []}),
        ]
        result = group_into_phases(calls)
        assert len(result.instruments["bass"]) == 3


# ---------------------------------------------------------------------------
# ToolMetaRegistry
# ---------------------------------------------------------------------------


class TestToolMetaRegistry:
    """ToolMetaRegistry alias and build_tool_registry() invariants."""

    def test_type_alias_matches_registry(self) -> None:
        """build_tool_registry() returns a ToolMetaRegistry (dict[str, ToolMeta])."""
        from maestro.daw.ports import ToolMetaRegistry
        from maestro.daw.stori.tool_registry import build_tool_registry

        reg = build_tool_registry()
        assert isinstance(reg, dict)
        for k, v in reg.items():
            assert isinstance(k, str)
            assert hasattr(v, "name")
            assert hasattr(v, "tier")
            assert hasattr(v, "kind")

    def test_registry_is_idempotent(self) -> None:
        """Calling build_tool_registry() twice returns the same object."""
        from maestro.daw.stori.tool_registry import build_tool_registry

        first = build_tool_registry()
        second = build_tool_registry()
        assert first is second

    def test_every_mcp_tool_has_meta(self) -> None:
        """Every MCP tool definition has a corresponding ToolMeta entry."""
        from maestro.daw.stori.tool_registry import MCP_TOOLS, build_tool_registry

        reg = build_tool_registry()
        for tool in MCP_TOOLS:
            name = tool["name"]
            assert name in reg, f"MCP tool {name!r} missing from ToolMetaRegistry"

    def test_server_side_daw_partition(self) -> None:
        """SERVER_SIDE_TOOLS and DAW_TOOLS are a complete, disjoint partition."""
        from maestro.daw.stori.tool_registry import MCP_TOOLS, SERVER_SIDE_TOOLS, DAW_TOOLS

        all_names = {t["name"] for t in MCP_TOOLS}
        assert SERVER_SIDE_TOOLS | DAW_TOOLS == all_names
        assert SERVER_SIDE_TOOLS & DAW_TOOLS == set()


# ---------------------------------------------------------------------------
# ToolCategoryEntry
# ---------------------------------------------------------------------------


class TestToolCategoryEntry:
    """ToolCategoryEntry pairs and the TOOL_CATEGORIES derived dict."""

    def test_every_tool_has_a_category(self) -> None:
        """TOOL_CATEGORIES covers every MCP tool."""
        from maestro.daw.stori.tool_registry import MCP_TOOLS, TOOL_CATEGORIES

        for tool in MCP_TOOLS:
            assert tool["name"] in TOOL_CATEGORIES, (
                f"Tool {tool['name']!r} missing from TOOL_CATEGORIES"
            )

    def test_categories_are_known_strings(self) -> None:
        """All category values are one of the expected category names."""
        from maestro.daw.stori.tool_registry import TOOL_CATEGORIES

        known = {"project", "track", "region", "note", "effects", "automation",
                 "midi_control", "generation", "playback", "ui"}
        for tool_name, category in TOOL_CATEGORIES.items():
            assert category in known, (
                f"Tool {tool_name!r} has unknown category {category!r}"
            )


# ---------------------------------------------------------------------------
# StoriDAWAdapter — protocol conformance and integration
# ---------------------------------------------------------------------------


class TestStoriDAWAdapter:
    """StoriDAWAdapter satisfies DAWAdapter and wires all components."""

    def test_protocol_conformance(self) -> None:
        """StoriDAWAdapter is a runtime-checkable DAWAdapter."""
        from maestro.daw.ports import DAWAdapter
        from maestro.daw.stori.adapter import StoriDAWAdapter

        adapter = StoriDAWAdapter()
        assert isinstance(adapter, DAWAdapter)

    def test_registry_is_frozen(self) -> None:
        """ToolRegistry returned by the adapter is immutable."""
        from maestro.daw.stori.adapter import StoriDAWAdapter

        adapter = StoriDAWAdapter()
        with pytest.raises(AttributeError):
            adapter.registry.mcp_tools = []  # type: ignore[misc]

    def test_registry_has_mcp_and_llm_tools(self) -> None:
        """Registry exposes both MCP wire defs and LLM schemas."""
        from maestro.daw.stori.adapter import StoriDAWAdapter

        reg = StoriDAWAdapter().registry
        assert len(reg.mcp_tools) >= 34
        assert len(reg.tool_schemas) >= 32

    def test_registry_tool_meta_populated(self) -> None:
        """Registry tool_meta dict is populated with ToolMeta entries."""
        from maestro.daw.stori.adapter import StoriDAWAdapter

        reg = StoriDAWAdapter().registry
        assert len(reg.tool_meta) >= 29

    def test_validate_tool_call_valid(self) -> None:
        """Valid tool call passes validation."""
        from maestro.daw.stori.adapter import StoriDAWAdapter

        adapter = StoriDAWAdapter()
        result = adapter.validate_tool_call(
            "stori_set_tempo",
            {"tempo": 120},
            {"stori_set_tempo"},
        )
        assert result.valid

    def test_validate_tool_call_not_in_allowed_set(self) -> None:
        """Tool not in allowed_tools set fails validation."""
        from maestro.daw.stori.adapter import StoriDAWAdapter

        adapter = StoriDAWAdapter()
        result = adapter.validate_tool_call(
            "stori_set_tempo",
            {"tempo": 120},
            {"stori_add_notes"},
        )
        assert not result.valid

    def test_phase_for_tool_all_phases(self) -> None:
        """phase_for_tool covers setup, instrument, and mixing."""
        from maestro.daw.stori.adapter import StoriDAWAdapter

        adapter = StoriDAWAdapter()
        assert adapter.phase_for_tool("stori_set_tempo") == "setup"
        assert adapter.phase_for_tool("stori_set_key") == "setup"
        assert adapter.phase_for_tool("stori_add_midi_track") == "instrument"
        assert adapter.phase_for_tool("stori_add_notes") == "instrument"
        assert adapter.phase_for_tool("stori_generate_midi") == "instrument"
        assert adapter.phase_for_tool("stori_ensure_bus") == "mixing"
        assert adapter.phase_for_tool("stori_add_send") == "mixing"
        assert adapter.phase_for_tool("stori_set_track_volume") == "mixing"

    def test_singleton_get_daw_adapter(self) -> None:
        """get_daw_adapter returns the same singleton instance."""
        from maestro.daw.stori.adapter import get_daw_adapter

        a = get_daw_adapter()
        b = get_daw_adapter()
        assert a is b


# ---------------------------------------------------------------------------
# ToolRegistry — field-level invariants
# ---------------------------------------------------------------------------


class TestToolRegistryFields:
    """ToolRegistry dataclass field invariants."""

    def test_server_side_and_daw_disjoint(self) -> None:
        """server_side_tools and daw_tools don't overlap."""
        from maestro.daw.stori.adapter import StoriDAWAdapter

        reg = StoriDAWAdapter().registry
        assert reg.server_side_tools & reg.daw_tools == frozenset()

    def test_all_mcp_tools_classified(self) -> None:
        """Every MCP tool is either server-side or DAW-side."""
        from maestro.daw.stori.adapter import StoriDAWAdapter

        reg = StoriDAWAdapter().registry
        mcp_names = {t["name"] for t in reg.mcp_tools}
        classified = reg.server_side_tools | reg.daw_tools
        assert mcp_names == classified

    def test_categories_cover_mcp_tools(self) -> None:
        """Every MCP tool has a category entry."""
        from maestro.daw.stori.adapter import StoriDAWAdapter

        reg = StoriDAWAdapter().registry
        for tool in reg.mcp_tools:
            assert tool["name"] in reg.categories


# ---------------------------------------------------------------------------
# Re-exports — verify public API surface
# ---------------------------------------------------------------------------


class TestReExports:
    """Named types are re-exported through the executor package."""

    def test_phase_split_importable_from_executor(self) -> None:
        """PhaseSplit is importable from maestro.core.executor."""
        from maestro.core.executor import PhaseSplit
        assert PhaseSplit is not None

    def test_instrument_groups_importable_from_executor(self) -> None:
        """InstrumentGroups is importable from maestro.core.executor."""
        from maestro.core.executor import InstrumentGroups
        assert InstrumentGroups is not None

    def test_tool_meta_registry_importable_from_ports(self) -> None:
        """ToolMetaRegistry is importable from maestro.daw.ports."""
        from maestro.daw.ports import ToolMetaRegistry
        assert ToolMetaRegistry is not None
