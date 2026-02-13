"""Tests for the Cursor-of-DAWs executor and tool registry."""
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.executor import execute_plan, ExecutionContext
from app.core.state_store import StateStore, clear_all_stores
from app.core.entity_registry import EntityRegistry
from app.core.expansion import ToolCall
from app.core.tracing import create_trace_context, clear_trace_context
from app.core.tools import (
    ALL_TOOLS,
    TIER1_TOOLS,
    TIER2_TOOLS,
    ToolKind,
    ToolTier,
    build_tool_registry,
    get_tool_meta,
)


@pytest.fixture(autouse=True)
def cleanup_stores():
    """Clean up stores before and after each test."""
    clear_all_stores()
    clear_trace_context()
    yield
    clear_all_stores()
    clear_trace_context()


class TestToolCategories:
    """Test tool categorization in the registry."""

    def test_tier1_tools_defined(self):
        """Test that Tier 1 tools exist in the registry."""
        reg = build_tool_registry()
        tier1_names = [n for n, m in reg.items() if m.tier == ToolTier.TIER1]
        assert len(tier1_names) > 0

    def test_tier2_tools_defined(self):
        """Test that Tier 2 tools exist in the registry."""
        reg = build_tool_registry()
        tier2_names = [n for n, m in reg.items() if m.tier == ToolTier.TIER2]
        assert len(tier2_names) > 0

    def test_generator_tools_are_tier1(self):
        """Generator tools should be Tier 1."""
        reg = build_tool_registry()
        generators = [n for n, m in reg.items() if m.kind == ToolKind.GENERATOR]
        for name in generators:
            meta = reg[name]
            assert meta.tier == ToolTier.TIER1, f"{name} should be TIER1"

    def test_primitive_tools_are_tier2(self):
        """Primitive DAW tools should be Tier 2."""
        reg = build_tool_registry()
        primitives = [n for n, m in reg.items() if m.kind == ToolKind.PRIMITIVE]
        for name in primitives:
            meta = reg[name]
            assert meta.tier == ToolTier.TIER2, f"{name} should be TIER2"


class TestToolRegistry:
    """Test tool registry functions."""

    def test_build_tool_registry(self):
        """Registry should contain all tool metadata."""
        reg = build_tool_registry()
        assert len(reg) > 0
        for name, meta in reg.items():
            assert hasattr(meta, "kind")
            assert hasattr(meta, "tier")

    def test_get_tool_meta(self):
        """get_tool_meta should return metadata for known tools."""
        meta = get_tool_meta("stori_play")
        assert meta is not None
        assert meta.kind == ToolKind.PRIMITIVE

    def test_get_tool_meta_unknown(self):
        """get_tool_meta should return None for unknown tools."""
        meta = get_tool_meta("unknown_tool_xyz")
        assert meta is None


class TestExecutionContext:
    """Tests for ExecutionContext dataclass."""

    def test_empty_context(self):
        """New context should have empty results."""
        store = StateStore()
        tx = store.begin_transaction("test")
        trace = create_trace_context()
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        assert ctx.results == []
        store.rollback(tx)

    def test_add_result(self):
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

    def test_multiple_results(self):
        """Multiple results should accumulate."""
        store = StateStore()
        tx = store.begin_transaction("test")
        trace = create_trace_context()
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_result("stori_add_midi_track", success=True, output={"trackId": "t1"})
        ctx.add_result("stori_set_tempo", success=True, output={"bpm": 120})

        assert len(ctx.results) == 2
        store.rollback(tx)

    def test_all_successful_false_when_one_fails(self):
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

    def test_created_entities_mapping(self):
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


class TestExecutePlanBasics:
    """Basic tests for execute_plan function."""

    @pytest.mark.anyio
    async def test_empty_plan(self):
        """Empty plan should return empty events."""
        events = await execute_plan([], {})
        assert events == []

    @pytest.mark.anyio
    async def test_simple_tier2_passthrough(self):
        """Tier 2 tools should pass through to client."""
        calls = [ToolCall("stori_play", {})]
        events = await execute_plan(calls, {})

        assert len(events) == 1
        assert events[0]["tool"] == "stori_play"

    @pytest.mark.anyio
    async def test_params_preserved(self):
        """Tool params should be preserved in output."""
        calls = [ToolCall("stori_set_tempo", {"tempo": 128})]
        events = await execute_plan(calls, {})

        # Note: param key might be "tempo" or "bpm" depending on tool definition
        assert "tempo" in events[0]["params"] or "bpm" in events[0]["params"]


class TestDeduplication:
    """Test call deduplication."""

    @pytest.mark.anyio
    async def test_identical_calls_deduped(self):
        """Identical calls should be deduplicated."""
        calls = [
            ToolCall("stori_play", {}),
            ToolCall("stori_play", {}),
        ]
        events = await execute_plan(calls, {})
        assert len(events) == 1

    @pytest.mark.anyio
    async def test_different_params_not_deduped(self):
        """Calls with different params should not be deduplicated."""
        calls = [
            ToolCall("stori_set_tempo", {"tempo": 120}),
            ToolCall("stori_set_tempo", {"tempo": 140}),
        ]
        events = await execute_plan(calls, {})
        assert len(events) == 2


class TestUUIDGeneration:
    """Test UUID generation for entity-creating tools."""

    @pytest.mark.anyio
    async def test_track_gets_uuid(self):
        """Track creation should generate trackId."""
        calls = [ToolCall("stori_add_midi_track", {"name": "Drums"})]
        events = await execute_plan(calls, {})

        assert "trackId" in events[0]["params"]
        # Validate it's a proper UUID
        uuid.UUID(events[0]["params"]["trackId"])


class TestTrackNameResolution:
    """Test trackName to trackId resolution."""

    @pytest.mark.anyio
    async def test_resolves_from_project_state(self):
        """trackName should resolve from project state."""
        calls = [ToolCall("stori_set_track_volume", {"trackName": "Drums", "volumeDb": -6})]
        project_state = {"tracks": [{"name": "Drums", "id": "abc-123"}]}

        events = await execute_plan(calls, project_state)

        assert events[0]["params"]["trackId"] == "abc-123"

    @pytest.mark.anyio
    async def test_case_insensitive(self):
        """Resolution should be case-insensitive."""
        calls = [ToolCall("stori_set_track_volume", {"trackName": "DRUMS", "volumeDb": -6})]
        project_state = {"tracks": [{"name": "drums", "id": "xyz-789"}]}

        events = await execute_plan(calls, project_state)

        assert events[0]["params"]["trackId"] == "xyz-789"

    @pytest.mark.anyio
    async def test_unresolved_skips(self):
        """Unresolved trackName should skip the call."""
        calls = [ToolCall("stori_set_track_volume", {"trackName": "NonExistent", "volumeDb": -6})]
        project_state = {"tracks": []}

        events = await execute_plan(calls, project_state)

        assert len(events) == 0


class TestStateStoreIntegration:
    """Test StateStore integration with executor."""

    @pytest.mark.anyio
    async def test_track_created_in_store(self):
        """Track creation should register in StateStore."""
        calls = [ToolCall("stori_add_midi_track", {"name": "Bass"})]
        
        from app.core.state_store import get_or_create_store
        store = get_or_create_store("test-conv-1")
        
        events = await execute_plan(calls, {}, store=store)
        
        # Track should be in the store's registry
        track_id = store.registry.resolve_track("Bass")
        assert track_id is not None
        assert track_id == events[0]["params"]["trackId"]

    @pytest.mark.anyio
    async def test_state_version_increments(self):
        """State version should increment after operations."""
        from app.core.state_store import get_or_create_store
        store = get_or_create_store("test-conv-2")
        
        initial_version = store.version
        
        calls = [ToolCall("stori_add_midi_track", {"name": "Drums"})]
        await execute_plan(calls, {}, store=store)
        
        # Version should have increased
        assert store.version > initial_version


# =============================================================================
# apply_variation_phrases (variation commit)
# =============================================================================

@pytest.mark.asyncio
async def test_apply_variation_phrases_empty_list(cleanup_stores):
    """Applying empty accepted_phrase_ids returns success with zero counts."""
    from app.core.executor import apply_variation_phrases
    from app.models.variation import Variation

    variation = Variation(
        variation_id="v-empty",
        intent="test",
        beat_range=(0.0, 4.0),
        phrases=[],
    )
    project_state = {"projectId": "proj-1", "tracks": [], "regions": {}}
    result = await apply_variation_phrases(
        variation, [], project_state, conversation_id="conv-1"
    )
    assert result.success is True
    assert result.applied_phrase_ids == []
    assert result.notes_added == 0
    assert result.notes_removed == 0
    assert result.notes_modified == 0


@pytest.mark.asyncio
async def test_apply_variation_phrases_invalid_phrase_ids_skipped(cleanup_stores):
    """Invalid phrase IDs are skipped; result still success with zero applied."""
    from app.core.executor import apply_variation_phrases
    from app.models.variation import Variation

    variation = Variation(
        variation_id="v-no-match",
        intent="test",
        beat_range=(0.0, 4.0),
        phrases=[],
    )
    project_state = {"projectId": "proj-1", "tracks": [], "regions": {}}
    result = await apply_variation_phrases(
        variation, ["phrase-unknown-1", "phrase-unknown-2"], project_state
    )
    assert result.success is True
    assert result.applied_phrase_ids == []
    assert result.notes_added == 0


# =============================================================================
# ExecutionContext.add_event
# =============================================================================

class TestExecutionContextAddEvent:
    """ExecutionContext.add_event records events."""

    def test_add_event_appends(self):
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

    def test_all_successful_true_when_all_ok(self):
        store = StateStore()
        tx = store.begin_transaction("test")
        trace = create_trace_context()
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_result("stori_play", True, {})
        ctx.add_result("stori_stop", True, {})
        assert ctx.all_successful is True
        assert ctx.failed_tools == []
        store.rollback(tx)

    def test_all_successful_false_and_failed_tools(self):
        store = StateStore()
        tx = store.begin_transaction("test")
        trace = create_trace_context()
        ctx = ExecutionContext(store=store, transaction=tx, trace=trace)
        ctx.add_result("stori_play", True, {})
        ctx.add_result("stori_add_midi_track", False, {}, error="fail")
        assert ctx.all_successful is False
        assert ctx.failed_tools == ["stori_add_midi_track"]
        store.rollback(tx)

    def test_created_entities_mapping(self):
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


# =============================================================================
# execute_plan: add_region, ensure_bus, set_tempo, set_key, add_notes, add_effect
# =============================================================================

class TestExecutePlanAddRegion:
    """stori_add_midi_region with track from state."""

    @pytest.mark.anyio
    async def test_add_region_with_track_id_from_state(self):
        """Create region when track exists in project state."""
        project_state = {
            "tracks": [{"name": "Drums", "id": "track-1"}],
            "regions": {},
        }
        calls = [
            ToolCall("stori_add_midi_track", {"name": "Drums"}),
            ToolCall(
                "stori_add_midi_region",
                {"name": "Verse", "trackName": "Drums", "startBeat": 0, "durationBeats": 16},
            ),
        ]
        events = await execute_plan(calls, project_state)
        assert len(events) >= 2
        region_ev = next(e for e in events if e.get("tool") == "stori_add_midi_region")
        assert "regionId" in region_ev["params"]

    @pytest.mark.anyio
    async def test_add_region_fails_when_no_track_specified(self):
        """add_midi_region with no track returns error event."""
        calls = [ToolCall("stori_add_midi_region", {"name": "R1", "startBeat": 0, "durationBeats": 8})]
        events = await execute_plan(calls, {})
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert "Track" in events[0].get("error", "")


class TestExecutePlanEnsureBus:
    """stori_ensure_bus creates bus and emits params."""

    @pytest.mark.anyio
    async def test_ensure_bus_emits_bus_id(self):
        calls = [ToolCall("stori_ensure_bus", {"name": "Reverb Bus"})]
        events = await execute_plan(calls, {})
        assert len(events) == 1
        assert events[0]["tool"] == "stori_ensure_bus"
        assert "busId" in events[0]["params"]


class TestExecutePlanSetTempoAndKey:
    """State store updates for set_tempo and set_key_signature."""

    @pytest.mark.anyio
    async def test_set_tempo_emits_params(self):
        calls = [ToolCall("stori_set_tempo", {"tempo": 100})]
        events = await execute_plan(calls, {})
        assert len(events) == 1
        assert events[0]["params"].get("tempo") == 100 or events[0]["params"].get("bpm") == 100

    @pytest.mark.anyio
    async def test_set_key_signature_emits_params(self):
        calls = [ToolCall("stori_set_key_signature", {"key": "Am"})]
        events = await execute_plan(calls, {})
        assert len(events) == 1
        assert "key" in events[0]["params"]


class TestExecutePlanAddNotesAndEffect:
    """stori_add_notes and stori_add_insert_effect."""

    @pytest.mark.anyio
    async def test_add_notes_emits_when_region_and_notes_provided(self):
        """add_notes with regionId and notes emits event."""
        project_state = {
            "tracks": [{"name": "Piano", "id": "t1"}],
            "regions": [{"name": "R1", "id": "r1", "trackId": "t1"}],
        }
        calls = [
            ToolCall("stori_add_midi_track", {"name": "Piano"}),
            ToolCall("stori_add_midi_region", {"name": "R1", "trackName": "Piano", "startBeat": 0, "durationBeats": 16}),
            ToolCall(
                "stori_add_notes",
                {"regionId": None, "trackName": "Piano", "notes": [{"pitch": 60, "velocity": 100, "startBeat": 0, "durationBeats": 0.5}]},
            ),
        ]
        events = await execute_plan(calls, project_state)
        add_notes_events = [e for e in events if e.get("tool") == "stori_add_notes"]
        assert len(add_notes_events) >= 1

    @pytest.mark.anyio
    async def test_add_insert_effect_emits_with_track_and_type(self):
        project_state = {"tracks": [{"name": "Drums", "id": "t1"}]}
        calls = [
            ToolCall("stori_add_midi_track", {"name": "Drums"}),
            ToolCall("stori_add_insert_effect", {"trackId": None, "trackName": "Drums", "type": "reverb"}),
        ]
        events = await execute_plan(calls, project_state)
        effect_ev = next(e for e in events if e.get("tool") == "stori_add_insert_effect")
        assert effect_ev["params"].get("type") == "reverb"


# =============================================================================
# execute_plan: generator tool with mocked music_generator
# =============================================================================

class TestExecutePlanGenerator:
    """Generator tools (stori_generate_*) with mocked get_music_generator."""

    @pytest.mark.anyio
    async def test_generator_success_emits_add_notes(self):
        """When music_generator.generate succeeds, executor emits add_notes event."""
        from app.services.backends.base import GenerationResult, GeneratorBackend
        from app.core.executor import execute_plan

        async def fake_generate(**kwargs):
            return GenerationResult(
                success=True,
                notes=[{"pitch": 60, "velocity": 80, "startBeat": 0, "durationBeats": 0.5}],
                backend_used=GeneratorBackend.ORPHEUS,
                metadata={},
            )

        mock_mg = MagicMock()
        mock_mg.generate = AsyncMock(side_effect=fake_generate)

        project_state = {
            "tracks": [{"name": "Drums", "id": "track-1"}],
            "regions": [{"id": "region-1", "trackId": "track-1"}],
        }
        with patch("app.core.executor.get_music_generator", return_value=mock_mg):
            calls = [
                ToolCall("stori_add_midi_track", {"name": "Drums"}),
                ToolCall("stori_add_midi_region", {"name": "Verse", "trackName": "Drums", "startBeat": 0, "durationBeats": 16}),
                ToolCall("stori_generate_drums", {"role": "drums", "style": "boom_bap", "tempo": 90, "bars": 4, "trackName": "Drums"}),
            ]
            events = await execute_plan(calls, project_state)
            add_notes = [e for e in events if e.get("tool") == "stori_add_notes"]
            assert len(add_notes) >= 1
            assert "notes" in add_notes[0]["params"]

    @pytest.mark.anyio
    async def test_generator_failure_adds_error_result(self):
        """When music_generator.generate returns success=False, no add_notes event."""
        from app.services.backends.base import GenerationResult, GeneratorBackend
        from app.core.executor import execute_plan

        async def fake_fail(**kwargs):
            return GenerationResult(success=False, notes=[], backend_used=GeneratorBackend.ORPHEUS, metadata={}, error="Orpheus down")

        mock_mg = MagicMock()
        mock_mg.generate = AsyncMock(side_effect=fake_fail)

        project_state = {"tracks": [{"name": "Drums", "id": "t1"}], "regions": [{"id": "r1", "trackId": "t1"}]}
        with patch("app.core.executor.get_music_generator", return_value=mock_mg):
            calls = [
                ToolCall("stori_add_midi_track", {"name": "Drums"}),
                ToolCall("stori_add_midi_region", {"name": "R", "trackName": "Drums", "startBeat": 0, "durationBeats": 16}),
                ToolCall("stori_generate_drums", {"role": "drums", "style": "boom_bap", "tempo": 90, "bars": 4, "trackName": "Drums"}),
            ]
            events = await execute_plan(calls, project_state)
            add_notes = [e for e in events if e.get("tool") == "stori_add_notes"]
            assert len(add_notes) == 0
            errors = [e for e in events if e.get("type") == "error"]
            assert len(errors) == 0  # we don't append error event for generator failure, we just add_result
            assert len(events) >= 2  # track + region

    @pytest.mark.anyio
    async def test_generator_track_not_found(self):
        """When trackName is not in store, generator adds error result."""
        from app.services.backends.base import GenerationResult, GeneratorBackend
        from app.core.executor import execute_plan

        async def fake_ok(**kwargs):
            return GenerationResult(success=True, notes=[], backend_used=GeneratorBackend.ORPHEUS, metadata={})

        mock_mg = MagicMock()
        mock_mg.generate = AsyncMock(side_effect=fake_ok)
        # No track "Drums" in state
        project_state = {"tracks": [], "regions": []}
        with patch("app.core.executor.get_music_generator", return_value=mock_mg):
            calls = [ToolCall("stori_generate_drums", {"role": "drums", "style": "boom_bap", "tempo": 90, "bars": 4, "trackName": "Drums"})]
            events = await execute_plan(calls, project_state)
            add_notes = [e for e in events if e.get("tool") == "stori_add_notes"]
            assert len(add_notes) == 0

    @pytest.mark.anyio
    async def test_generator_no_region_for_track(self):
        """When track exists but has no region, generator adds error result."""
        from app.services.backends.base import GenerationResult, GeneratorBackend
        from app.core.executor import execute_plan

        async def fake_ok(**kwargs):
            return GenerationResult(success=True, notes=[{"pitch": 60}], backend_used=GeneratorBackend.ORPHEUS, metadata={})

        mock_mg = MagicMock()
        mock_mg.generate = AsyncMock(side_effect=fake_ok)
        # Track exists but no regions
        project_state = {"tracks": [{"name": "Drums", "id": "t1"}], "regions": []}
        with patch("app.core.executor.get_music_generator", return_value=mock_mg):
            calls = [
                ToolCall("stori_add_midi_track", {"name": "Drums"}),
                ToolCall("stori_generate_drums", {"role": "drums", "style": "boom_bap", "tempo": 90, "bars": 4, "trackName": "Drums"}),
            ]
            events = await execute_plan(calls, project_state)
            add_notes = [e for e in events if e.get("tool") == "stori_add_notes"]
            assert len(add_notes) == 0

    @pytest.mark.anyio
    async def test_generator_exception_adds_error_result(self):
        """When music_generator.generate raises, executor records failure."""
        from app.core.executor import execute_plan

        mock_mg = MagicMock()
        mock_mg.generate = AsyncMock(side_effect=RuntimeError("backend down"))

        project_state = {"tracks": [{"name": "Drums", "id": "t1"}], "regions": [{"id": "r1", "trackId": "t1"}]}
        with patch("app.core.executor.get_music_generator", return_value=mock_mg):
            calls = [
                ToolCall("stori_add_midi_track", {"name": "Drums"}),
                ToolCall("stori_add_midi_region", {"name": "R", "trackName": "Drums", "startBeat": 0, "durationBeats": 16}),
                ToolCall("stori_generate_drums", {"role": "drums", "style": "boom_bap", "tempo": 90, "bars": 4, "trackName": "Drums"}),
            ]
            events = await execute_plan(calls, project_state)
            add_notes = [e for e in events if e.get("tool") == "stori_add_notes"]
            assert len(add_notes) == 0


# =============================================================================
# execute_plan_streaming
# =============================================================================

class TestExecutePlanStreaming:
    """execute_plan_streaming yields events incrementally."""

    @pytest.mark.anyio
    async def test_streaming_yields_tool_call_events(self):
        from app.core.executor import execute_plan_streaming

        calls = [ToolCall("stori_play", {}), ToolCall("stori_stop", {})]
        collected = []
        async for event in execute_plan_streaming(calls, {}):
            collected.append(event)
        assert len(collected) >= 2
        tool_names = [e.get("name") for e in collected if e.get("type") == "tool_call"]
        assert "stori_play" in tool_names
        assert "stori_stop" in tool_names

    @pytest.mark.anyio
    async def test_streaming_yields_progress_and_complete(self):
        from app.core.executor import execute_plan_streaming

        calls = [ToolCall("stori_set_tempo", {"tempo": 120})]
        collected = []
        async for event in execute_plan_streaming(calls, {}):
            collected.append(event)
        types = [e.get("type") for e in collected]
        assert "plan_progress" in types
        assert "tool_call" in types
        assert "plan_complete" in types

    @pytest.mark.anyio
    async def test_streaming_empty_calls_yields_plan_complete_only(self):
        from app.core.executor import execute_plan_streaming

        collected = []
        async for event in execute_plan_streaming([], {}):
            collected.append(event)
        assert len(collected) == 1
        assert collected[0]["type"] == "plan_complete"
        assert collected[0]["success"] is True
        assert collected[0]["total_events"] == 0
