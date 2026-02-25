"""
Tests for entity manifest and entity ID echo in maestro_handlers.

Coverage:
  1. EntityRegistry.agent_manifest — structure, tracks, regions
  2. _ENTITY_CREATING_TOOLS — correct membership
  3. _ENTITY_ID_ECHO — correct field lists per tool
  4. EntityRegistry CRUD — create, resolve, exists, list
  5. EntityRegistry.sync_from_project_state — full project sync
  6. EntityRegistry.from_dict / to_dict round-trip
  7. get_or_create_bus idempotency
"""
from __future__ import annotations

import pytest
from typing import Any
from unittest.mock import MagicMock

from app.core.entity_registry import (
    EntityRegistry,
    EntityInfo,
    EntityType,
    create_registry_from_context,
)
from app.core.maestro_helpers import (
    _ENTITY_CREATING_TOOLS,
    _ENTITY_ID_ECHO,
)


# ===========================================================================
# 1. EntityRegistry.agent_manifest — text-based manifest
# ===========================================================================

class TestEntityManifest:
    """EntityRegistry.agent_manifest returns compact text for LLM context."""

    def test_empty_registry_shows_no_tracks(self) -> None:

        reg = EntityRegistry()
        manifest = reg.agent_manifest()
        assert "no tracks yet" in manifest

    def test_single_track_in_manifest(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        manifest = reg.agent_manifest()
        assert "Drums" in manifest
        assert tid in manifest

    def test_multiple_tracks_all_in_manifest(self) -> None:

        reg = EntityRegistry()
        ids = {name: reg.create_track(name) for name in ("Drums", "Bass", "Melody")}
        manifest = reg.agent_manifest()
        for name, tid in ids.items():
            assert name in manifest
            assert tid in manifest

    def test_regions_listed_under_tracks(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        rid = reg.create_region("Intro", parent_track_id=tid,
                                metadata={"startBeat": 0, "durationBeats": 16})
        manifest = reg.agent_manifest()
        assert "Intro" in manifest
        assert rid in manifest
        assert "regionId" in manifest

    def test_multiple_regions_on_same_track(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Bass")
        rid1 = reg.create_region("Intro Bass", parent_track_id=tid)
        rid2 = reg.create_region("Verse Bass", parent_track_id=tid)
        manifest = reg.agent_manifest()
        assert rid1 in manifest
        assert rid2 in manifest

    def test_manifest_ids_are_valid_strings(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        rid = reg.create_region("Pattern", parent_track_id=tid)
        manifest = reg.agent_manifest()
        assert tid in manifest
        assert rid in manifest

    def test_scoped_manifest_shows_only_given_track(self) -> None:

        reg = EntityRegistry()
        tid1 = reg.create_track("Drums")
        tid2 = reg.create_track("Bass")
        reg.create_region("Intro", parent_track_id=tid1)
        reg.create_region("Verse", parent_track_id=tid2)
        manifest = reg.agent_manifest(track_id=tid1)
        assert "Drums" in manifest
        assert tid1 in manifest
        assert "Bass" not in manifest
        assert tid2 not in manifest

    def test_agent_id_scoped_manifest_shows_only_own_entities(self) -> None:

        """agent_manifest(agent_id=...) filters to entities owned by that agent."""
        reg = EntityRegistry()
        tid1 = reg.create_track("Drums", owner_agent_id="agent-drums")
        tid2 = reg.create_track("Bass", owner_agent_id="agent-bass")
        reg.create_region(
            "Drums Intro", parent_track_id=tid1,
            metadata={"startBeat": 0, "durationBeats": 16},
            owner_agent_id="agent-drums",
        )
        reg.create_region(
            "Bass Intro", parent_track_id=tid2,
            metadata={"startBeat": 0, "durationBeats": 16},
            owner_agent_id="agent-bass",
        )
        manifest = reg.agent_manifest(agent_id="agent-drums")
        assert "Drums" in manifest
        assert tid1 in manifest
        assert "Drums Intro" in manifest
        assert "Bass" not in manifest
        assert tid2 not in manifest
        assert "Bass Intro" not in manifest

    def test_agent_id_filters_regions_across_tracks(self) -> None:

        """Regions owned by other agents are excluded even on the same track."""
        reg = EntityRegistry()
        tid = reg.create_track("Shared Track", owner_agent_id="agent-a")
        reg.create_region(
            "Region A", parent_track_id=tid,
            metadata={"startBeat": 0, "durationBeats": 16},
            owner_agent_id="agent-a",
        )
        reg.create_region(
            "Region B", parent_track_id=tid,
            metadata={"startBeat": 16, "durationBeats": 16},
            owner_agent_id="agent-b",
        )
        manifest = reg.agent_manifest(agent_id="agent-a")
        assert "Region A" in manifest
        assert "Region B" not in manifest

    def test_owner_agent_id_persists_through_serialization(self) -> None:

        """owner_agent_id survives to_dict / from_dict round-trip."""
        reg = EntityRegistry()
        tid = reg.create_track("Drums", owner_agent_id="agent-drums")
        reg.create_region(
            "Intro", parent_track_id=tid,
            metadata={"startBeat": 0, "durationBeats": 16},
            owner_agent_id="agent-drums",
        )
        data = reg.to_dict()
        restored = EntityRegistry.from_dict(data)

        track = restored.get_track(tid)
        assert track is not None
        assert track.owner_agent_id == "agent-drums"

        regions = restored.get_track_regions(tid)
        assert len(regions) == 1
        assert regions[0].owner_agent_id == "agent-drums"


# ===========================================================================
# 2. _ENTITY_CREATING_TOOLS membership
# ===========================================================================

class TestEntityCreatingToolsConstant:
    """_ENTITY_CREATING_TOOLS must include all tools that create entities."""

    def test_add_midi_track_in_set(self) -> None:

        assert "stori_add_midi_track" in _ENTITY_CREATING_TOOLS

    def test_add_midi_region_in_set(self) -> None:

        assert "stori_add_midi_region" in _ENTITY_CREATING_TOOLS

    def test_ensure_bus_in_set(self) -> None:

        assert "stori_ensure_bus" in _ENTITY_CREATING_TOOLS

    def test_duplicate_region_in_set(self) -> None:

        assert "stori_duplicate_region" in _ENTITY_CREATING_TOOLS

    def test_non_entity_tools_not_in_set(self) -> None:

        non_entity = {
            "stori_add_notes", "stori_set_tempo", "stori_play",
            "stori_generate_midi", "stori_set_track_volume",
        }
        for tool in non_entity:
            assert tool not in _ENTITY_CREATING_TOOLS, (
                f"{tool} should not be in _ENTITY_CREATING_TOOLS"
            )


# ===========================================================================
# 3. _ENTITY_ID_ECHO field lists
# ===========================================================================

class TestEntityIdEcho:
    """_ENTITY_ID_ECHO maps each entity-creating tool to its echoed ID fields."""

    def test_add_midi_track_echoes_track_id(self) -> None:

        assert "trackId" in _ENTITY_ID_ECHO["stori_add_midi_track"]

    def test_add_midi_region_echoes_region_and_track(self) -> None:

        fields = _ENTITY_ID_ECHO["stori_add_midi_region"]
        assert "regionId" in fields
        assert "trackId" in fields

    def test_ensure_bus_echoes_bus_id(self) -> None:

        assert "busId" in _ENTITY_ID_ECHO["stori_ensure_bus"]

    def test_duplicate_region_echoes_new_and_source_region(self) -> None:

        fields = _ENTITY_ID_ECHO["stori_duplicate_region"]
        assert "newRegionId" in fields
        assert "regionId" in fields

    def test_all_entity_creating_tools_have_echo_entry(self) -> None:

        """Every tool in _ENTITY_CREATING_TOOLS has at least one echo field."""
        for tool in _ENTITY_CREATING_TOOLS:
            assert tool in _ENTITY_ID_ECHO, f"{tool} missing from _ENTITY_ID_ECHO"
            assert len(_ENTITY_ID_ECHO[tool]) >= 1


# ===========================================================================
# 4. EntityRegistry — CRUD
# ===========================================================================

class TestEntityRegistryCRUD:
    """EntityRegistry create, resolve, exists, list operations."""

    def test_create_track_returns_id(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        assert isinstance(tid, str)
        assert len(tid) > 8

    def test_create_track_with_explicit_id(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Bass", track_id="fixed-id-123")
        assert tid == "fixed-id-123"

    def test_resolve_track_by_name(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        assert reg.resolve_track("Drums") == tid

    def test_resolve_track_case_insensitive(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        assert reg.resolve_track("drums") == tid
        assert reg.resolve_track("DRUMS") == tid

    def test_resolve_track_by_id(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        assert reg.resolve_track(tid) == tid

    def test_resolve_track_fuzzy_partial_match(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Kick Drums")
        assert reg.resolve_track("drums") == tid

    def test_resolve_track_unknown_returns_none(self) -> None:

        reg = EntityRegistry()
        assert reg.resolve_track("nonexistent") is None

    def test_exists_track_true(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Bass")
        assert reg.exists_track(tid)

    def test_exists_track_false(self) -> None:

        reg = EntityRegistry()
        assert not reg.exists_track("nonexistent-id")

    def test_create_region_returns_id(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        rid = reg.create_region("Pattern", parent_track_id=tid)
        assert isinstance(rid, str)

    def test_create_region_raises_for_unknown_parent(self) -> None:

        reg = EntityRegistry()
        with pytest.raises(ValueError, match="not found"):
            reg.create_region("Pattern", parent_track_id="bad-id")

    def test_resolve_region_by_name(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Bass")
        rid = reg.create_region("Verse", parent_track_id=tid)
        assert reg.resolve_region("Verse") == rid

    def test_resolve_region_scoped_by_parent_track(self) -> None:

        reg = EntityRegistry()
        tid1 = reg.create_track("Drums")
        tid2 = reg.create_track("Bass")
        rid1 = reg.create_region("Pattern", parent_track_id=tid1)
        rid2 = reg.create_region("Pattern", parent_track_id=tid2)
        assert reg.resolve_region("Pattern", parent_track=tid1) == rid1
        assert reg.resolve_region("Pattern", parent_track=tid2) == rid2

    def test_get_latest_region_for_track(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        rid1 = reg.create_region("First", parent_track_id=tid)
        rid2 = reg.create_region("Second", parent_track_id=tid)
        assert reg.get_latest_region_for_track(tid) == rid2

    def test_create_bus_returns_id(self) -> None:

        reg = EntityRegistry()
        bid = reg.create_bus("Reverb")
        assert isinstance(bid, str)

    def test_resolve_bus_by_name(self) -> None:

        reg = EntityRegistry()
        bid = reg.create_bus("Reverb")
        assert reg.resolve_bus("reverb") == bid

    def test_get_or_create_bus_idempotent(self) -> None:

        reg = EntityRegistry()
        bid1 = reg.get_or_create_bus("Delay")
        bid2 = reg.get_or_create_bus("Delay")
        assert bid1 == bid2

    def test_list_tracks(self) -> None:

        reg = EntityRegistry()
        reg.create_track("Drums")
        reg.create_track("Bass")
        tracks = reg.list_tracks()
        assert len(tracks) == 2
        names = {t.name for t in tracks}
        assert names == {"Drums", "Bass"}

    def test_list_regions(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        reg.create_region("Intro", parent_track_id=tid)
        reg.create_region("Verse", parent_track_id=tid)
        assert len(reg.list_regions()) == 2

    def test_clear_removes_all_entities(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        reg.create_region("P", parent_track_id=tid)
        reg.create_bus("Reverb")
        reg.clear()
        assert reg.list_tracks() == []
        assert reg.list_regions() == []
        assert reg.list_buses() == []


# ===========================================================================
# 5. EntityRegistry.sync_from_project_state
# ===========================================================================

class TestSyncFromProjectState:
    """sync_from_project_state populates registry from DAW project snapshot."""

    def _project(self, **kwargs: Any) -> dict[str, Any]:

        return {
            "tracks": [
                {
                    "id": "t1",
                    "name": "Drums",
                    "regions": [
                        {"id": "r1", "name": "Intro", "startBeat": 0, "durationBeats": 16},
                        {"id": "r2", "name": "Verse", "startBeat": 16, "durationBeats": 16},
                    ],
                },
                {
                    "id": "t2",
                    "name": "Bass",
                    "regions": [
                        {"id": "r3", "name": "Intro Bass", "startBeat": 0, "durationBeats": 16},
                    ],
                },
            ],
            "buses": [
                {"id": "b1", "name": "Reverb"},
            ],
            **kwargs,
        }

    def test_tracks_synced(self) -> None:

        reg = EntityRegistry()
        reg.sync_from_project_state(self._project())
        assert reg.exists_track("t1")
        assert reg.exists_track("t2")

    def test_regions_synced(self) -> None:

        reg = EntityRegistry()
        reg.sync_from_project_state(self._project())
        assert reg.exists_region("r1")
        assert reg.exists_region("r2")
        assert reg.exists_region("r3")

    def test_buses_synced(self) -> None:

        reg = EntityRegistry()
        reg.sync_from_project_state(self._project())
        assert reg.exists_bus("b1")

    def test_resolve_track_after_sync(self) -> None:

        reg = EntityRegistry()
        reg.sync_from_project_state(self._project())
        assert reg.resolve_track("Drums") == "t1"
        assert reg.resolve_track("Bass") == "t2"

    def test_resolve_region_after_sync(self) -> None:

        reg = EntityRegistry()
        reg.sync_from_project_state(self._project())
        assert reg.resolve_region("Intro") == "r1"
        assert reg.resolve_region("Verse") == "r2"

    def test_duplicate_sync_is_idempotent(self) -> None:

        """Syncing the same state twice does not create duplicate entries."""
        reg = EntityRegistry()
        project = self._project()
        reg.sync_from_project_state(project)
        reg.sync_from_project_state(project)
        assert len(reg.list_tracks()) == 2
        assert len(reg.list_regions()) == 3

    def test_empty_project_state_no_error(self) -> None:

        reg = EntityRegistry()
        reg.sync_from_project_state({})
        assert reg.list_tracks() == []

    def test_create_registry_from_context_factory(self) -> None:

        reg = create_registry_from_context(self._project())
        assert reg.exists_track("t1")
        assert reg.exists_region("r1")

    def test_region_parent_linked_correctly(self) -> None:

        reg = EntityRegistry()
        reg.sync_from_project_state(self._project())
        r1 = reg.get_region("r1")
        assert r1 is not None
        assert r1.parent_id == "t1"


# ===========================================================================
# 6. EntityRegistry serialisation round-trip
# ===========================================================================

class TestEntityRegistrySerialisation:
    """to_dict / from_dict must produce an identical registry."""

    def test_round_trip_tracks(self) -> None:

        reg = EntityRegistry(project_id="proj-123")
        reg.create_track("Drums", track_id="t1")
        restored = EntityRegistry.from_dict(reg.to_dict())
        assert restored.exists_track("t1")
        assert restored.resolve_track("Drums") == "t1"

    def test_round_trip_regions(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Bass", track_id="t1")
        reg.create_region("Verse", parent_track_id=tid, region_id="r1")
        restored = EntityRegistry.from_dict(reg.to_dict())
        assert restored.exists_region("r1")
        r = restored.get_region("r1")
        assert r is not None
        assert r.parent_id == "t1"

    def test_round_trip_buses(self) -> None:

        reg = EntityRegistry()
        reg.create_bus("Reverb", bus_id="b1")
        restored = EntityRegistry.from_dict(reg.to_dict())
        assert restored.exists_bus("b1")
        assert restored.resolve_bus("reverb") == "b1"

    def test_round_trip_preserves_project_id(self) -> None:

        reg = EntityRegistry(project_id="my-project")
        restored = EntityRegistry.from_dict(reg.to_dict())
        assert restored.project_id == "my-project"

    def test_round_trip_track_region_link(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Drums", track_id="t1")
        reg.create_region("Pattern", parent_track_id=tid, region_id="r1")
        restored = EntityRegistry.from_dict(reg.to_dict())
        latest = restored.get_latest_region_for_track("t1")
        assert latest == "r1"


# ===========================================================================
# 7. agent_manifest — LLM context injection (regression for P0 truncation bug)
# ===========================================================================

class TestAgentManifest:
    """agent_manifest produces a compact text block with all entity IDs.

    Regression: agents lost regionId when tool results were truncated to '...'
    by the LLM provider. The manifest ensures IDs are always available regardless
    of tool result truncation.
    """

    def test_empty_registry_shows_no_tracks(self) -> None:

        reg = EntityRegistry()
        text = reg.agent_manifest()
        assert "no tracks yet" in text

    def test_track_id_in_manifest(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Drums", track_id="t-drums-123")
        text = reg.agent_manifest()
        assert "t-drums-123" in text
        assert "Drums" in text

    def test_region_id_in_manifest(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Bass", track_id="t-bass")
        rid = reg.create_region(
            "INTRO", parent_track_id=tid, region_id="r-intro",
            metadata={"startBeat": 0, "durationBeats": 16},
        )
        text = reg.agent_manifest()
        assert "r-intro" in text
        assert "INTRO" in text
        assert "beat 0" in text

    def test_scoped_to_single_track(self) -> None:

        """When track_id is given, only that track's entities appear."""
        reg = EntityRegistry()
        t1 = reg.create_track("Drums", track_id="t1")
        t2 = reg.create_track("Bass", track_id="t2")
        reg.create_region("Intro", parent_track_id=t1, region_id="r1",
                          metadata={"startBeat": 0, "durationBeats": 8})
        reg.create_region("Intro", parent_track_id=t2, region_id="r2",
                          metadata={"startBeat": 0, "durationBeats": 8})
        text = reg.agent_manifest(track_id="t1")
        assert "t1" in text
        assert "r1" in text
        assert "t2" not in text
        assert "r2" not in text

    def test_multiple_regions_all_listed(self) -> None:

        reg = EntityRegistry()
        tid = reg.create_track("Keys", track_id="t-keys")
        reg.create_region("INTRO", parent_track_id=tid, region_id="r1",
                          metadata={"startBeat": 0, "durationBeats": 8})
        reg.create_region("GROOVE", parent_track_id=tid, region_id="r2",
                          metadata={"startBeat": 8, "durationBeats": 12})
        reg.create_region("VERSE", parent_track_id=tid, region_id="r3",
                          metadata={"startBeat": 20, "durationBeats": 16})
        text = reg.agent_manifest(track_id="t-keys")
        assert "r1" in text
        assert "r2" in text
        assert "r3" in text
        assert "INTRO" in text
        assert "GROOVE" in text
        assert "VERSE" in text

    def test_manifest_header_present(self) -> None:

        reg = EntityRegistry()
        reg.create_track("Drums")
        text = reg.agent_manifest()
        assert "ENTITY REGISTRY" in text

    def test_gm_program_shown_for_tracks(self) -> None:

        reg = EntityRegistry()
        reg.create_track("Bass", track_id="t1", metadata={"gmProgram": 33})
        text = reg.agent_manifest()
        assert "gm=33" in text

    def test_drum_kit_shown_for_tracks(self) -> None:

        reg = EntityRegistry()
        reg.create_track("Drums", track_id="t1", metadata={"drumKitId": "acoustic"})
        text = reg.agent_manifest()
        assert "drumKit=acoustic" in text


# ===========================================================================
# 8. _compact_tool_result — prevents truncation (regression)
# ===========================================================================

class TestCompactToolResult:
    """_compact_tool_result strips bulky fields while preserving IDs.

    Regression: tool results with 'entities' dict caused provider-side
    truncation, losing regionId needed by downstream generate_midi calls.
    """

    def test_region_id_preserved(self) -> None:

        from app.core.maestro_agent_teams.section_agent import _compact_tool_result
        result = {
            "success": True,
            "regionId": "abc-123",
            "trackId": "def-456",
            "startBeat": 0,
            "durationBeats": 16,
            "name": "INTRO",
            "entities": {"tracks": [{"id": "t1"}], "buses": []},
        }
        compact = _compact_tool_result(result)
        assert compact["regionId"] == "abc-123"
        assert compact["trackId"] == "def-456"
        assert "entities" not in compact

    def test_existing_region_id_preserved(self) -> None:

        from app.core.maestro_agent_teams.section_agent import _compact_tool_result
        result = {
            "success": True,
            "existingRegionId": "existing-rid",
            "skipped": True,
            "entities": {"tracks": []},
        }
        compact = _compact_tool_result(result)
        assert compact["existingRegionId"] == "existing-rid"
        assert compact["skipped"] is True
        assert "entities" not in compact

    def test_error_field_preserved(self) -> None:

        from app.core.maestro_agent_teams.section_agent import _compact_tool_result
        result = {"success": False, "error": "Region overlap"}
        compact = _compact_tool_result(result)
        assert compact["error"] == "Region overlap"
