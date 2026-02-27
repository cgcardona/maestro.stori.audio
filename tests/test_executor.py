"""Tests for the Cursor-of-DAWs executor and tool registry."""
from __future__ import annotations

from collections.abc import Generator

import pytest

from maestro.contracts.project_types import ProjectContext
from maestro.core.executor import ExecutionContext
from maestro.core.state_store import StateStore, clear_all_stores
from maestro.core.tracing import create_trace_context, clear_trace_context
from maestro.core.tools import (
    ALL_TOOLS,
    TIER1_TOOLS,
    TIER2_TOOLS,
    ToolKind,
    ToolTier,
    build_tool_registry,
    get_tool_meta,
)


@pytest.fixture(autouse=True)
def cleanup_stores() -> Generator[None, None, None]:
    """Clean up stores before and after each test."""
    clear_all_stores()
    clear_trace_context()
    yield
    clear_all_stores()
    clear_trace_context()


class TestToolCategories:
    """Test tool categorization in the registry."""

    def test_tier1_tools_defined(self) -> None:

        """Test that Tier 1 tools exist in the registry."""
        reg = build_tool_registry()
        tier1_names = [n for n, m in reg.items() if m.tier == ToolTier.TIER1]
        assert len(tier1_names) > 0

    def test_tier2_tools_defined(self) -> None:

        """Test that Tier 2 tools exist in the registry."""
        reg = build_tool_registry()
        tier2_names = [n for n, m in reg.items() if m.tier == ToolTier.TIER2]
        assert len(tier2_names) > 0

    def test_generator_tools_are_tier1(self) -> None:

        """Generator tools should be Tier 1."""
        reg = build_tool_registry()
        generators = [n for n, m in reg.items() if m.kind == ToolKind.GENERATOR]
        for name in generators:
            meta = reg[name]
            assert meta.tier == ToolTier.TIER1, f"{name} should be TIER1"

    def test_primitive_tools_are_tier2(self) -> None:

        """Primitive DAW tools should be Tier 2."""
        reg = build_tool_registry()
        primitives = [n for n, m in reg.items() if m.kind == ToolKind.PRIMITIVE]
        for name in primitives:
            meta = reg[name]
            assert meta.tier == ToolTier.TIER2, f"{name} should be TIER2"


class TestToolRegistry:
    """Test tool registry functions."""

    def test_build_tool_registry(self) -> None:

        """Registry should contain all tool metadata."""
        reg = build_tool_registry()
        assert len(reg) > 0
        for name, meta in reg.items():
            assert hasattr(meta, "kind")
            assert hasattr(meta, "tier")

    def test_get_tool_meta(self) -> None:

        """get_tool_meta should return metadata for known tools."""
        meta = get_tool_meta("stori_play")
        assert meta is not None
        assert meta.kind == ToolKind.PRIMITIVE

    def test_get_tool_meta_unknown(self) -> None:

        """get_tool_meta should return None for unknown tools."""
        meta = get_tool_meta("unknown_tool_xyz")
        assert meta is None


class TestExecutionContext:
    """Tests for ExecutionContext dataclass."""

    def test_empty_context(self) -> None:

        """New context should have empty results."""
        store = StateStore()
        tx = store.begin_transaction("test")
        trace = create_trace_context()
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        assert ctx.results == []
        store.rollback(tx)

    def test_add_result(self) -> None:

        """Adding results should append to list."""
        store = StateStore()
        tx = store.begin_transaction("test")
        trace = create_trace_context()
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_result("stori_add_midi_track", success=True, output={"trackId": "uuid-123"})

        assert len(ctx.results) == 1
        assert ctx.results[0].tool_name == "stori_add_midi_track"
        assert ctx.results[0].output["trackId"] == "uuid-123"
        store.rollback(tx)

    def test_multiple_results(self) -> None:

        """Multiple results should accumulate."""
        store = StateStore()
        tx = store.begin_transaction("test")
        trace = create_trace_context()
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_result("stori_add_midi_track", success=True, output={"trackId": "t1"})
        ctx.add_result("stori_set_tempo", success=True, output={"bpm": 120})

        assert len(ctx.results) == 2
        store.rollback(tx)

    def test_all_successful_false_when_one_fails(self) -> None:

        """all_successful is False and failed_tools lists failed tool."""
        store = StateStore()
        tx = store.begin_transaction("test")
        trace = create_trace_context()
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_result("stori_add_midi_track", success=True, output={"trackId": "t1"})
        ctx.add_result("stori_set_tempo", success=False, output={}, error="Connection lost")
        assert ctx.all_successful is False
        assert "stori_set_tempo" in ctx.failed_tools
        store.rollback(tx)

    def test_created_entities_mapping(self) -> None:

        """created_entities returns tool_name -> entity_id for entity-creating results."""
        store = StateStore()
        tx = store.begin_transaction("test")
        trace = create_trace_context()
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_result("stori_add_midi_track", success=True, output={"trackId": "track-uuid-1"}, entity_created="track-uuid-1")
        ctx.add_result("stori_add_midi_region", success=True, output={"regionId": "region-uuid-1"}, entity_created="region-uuid-1")
        ctx.add_result("stori_set_tempo", success=True, output={})  # no entity
        assert ctx.created_entities["stori_add_midi_track"] == "track-uuid-1"
        assert ctx.created_entities["stori_add_midi_region"] == "region-uuid-1"
        assert len(ctx.created_entities) == 2
        store.rollback(tx)




# =============================================================================
# apply_variation_phrases (variation commit)
# =============================================================================

@pytest.mark.asyncio
async def test_apply_variation_phrases_empty_list(cleanup_stores: None) -> None:

    """Applying empty accepted_phrase_ids returns success with zero counts."""
    from maestro.core.executor import apply_variation_phrases
    from maestro.models.variation import Variation

    variation = Variation(
        variation_id="v-empty",
        intent="test",
        beat_range=(0.0, 4.0),
        phrases=[],
    )
    project_state: ProjectContext = {"id": "proj-1", "tracks": []}
    store = StateStore(conversation_id="conv-1")
    result = await apply_variation_phrases(
        variation, [], project_state, store=store
    )
    assert result.success is True
    assert result.applied_phrase_ids == []
    assert result.notes_added == 0
    assert result.notes_removed == 0
    assert result.notes_modified == 0


@pytest.mark.asyncio
async def test_apply_variation_phrases_invalid_phrase_ids_skipped(cleanup_stores: None) -> None:

    """Invalid phrase IDs are skipped; result still success with zero applied."""
    from maestro.core.executor import apply_variation_phrases
    from maestro.models.variation import Variation

    variation = Variation(
        variation_id="v-no-match",
        intent="test",
        beat_range=(0.0, 4.0),
        phrases=[],
    )
    project_state: ProjectContext = {"id": "proj-1", "tracks": []}
    store = StateStore()
    result = await apply_variation_phrases(
        variation, ["phrase-unknown-1", "phrase-unknown-2"], project_state, store=store
    )
    assert result.success is True
    assert result.applied_phrase_ids == []
    assert result.notes_added == 0


# =============================================================================
# ExecutionContext.add_event
# =============================================================================

class TestExecutionContextAddEvent:
    """ExecutionContext.add_event records events."""

    def test_add_event_appends(self) -> None:

        store = StateStore()
        tx = store.begin_transaction("test")
        trace = create_trace_context()
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_event({"tool": "stori_play", "params": {}})
        ctx.add_event({"tool": "stori_stop", "params": {}})
        assert len(ctx.events) == 2
        assert ctx.events[0]["tool"] == "stori_play"
        assert ctx.events[1]["tool"] == "stori_stop"
        store.rollback(tx)


class TestExecutionContextProperties:
    """ExecutionContext all_successful, failed_tools, created_entities."""

    def test_all_successful_true_when_all_ok(self) -> None:

        store = StateStore()
        tx = store.begin_transaction("test")
        trace = create_trace_context()
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_result("stori_play", True, {})
        ctx.add_result("stori_stop", True, {})
        assert ctx.all_successful is True
        assert ctx.failed_tools == []
        store.rollback(tx)

    def test_all_successful_false_and_failed_tools(self) -> None:

        store = StateStore()
        tx = store.begin_transaction("test")
        trace = create_trace_context()
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_result("stori_play", True, {})
        ctx.add_result("stori_add_midi_track", False, {}, error="fail")
        assert ctx.all_successful is False
        assert ctx.failed_tools == ["stori_add_midi_track"]
        store.rollback(tx)

    def test_created_entities_mapping(self) -> None:

        store = StateStore()
        tx = store.begin_transaction("test")
        trace = create_trace_context()
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_result("stori_add_midi_track", True, {}, entity_created="track-123")
        ctx.add_result("stori_ensure_bus", True, {}, entity_created="bus-456")
        assert ctx.created_entities == {
            "stori_add_midi_track": "track-123",
            "stori_ensure_bus": "bus-456",
        }
        store.rollback(tx)


