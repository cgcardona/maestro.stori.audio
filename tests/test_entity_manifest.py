"""
Tests for entity manifest and entity ID echo in maestro_handlers.

Coverage:
  1. _entity_manifest — structure, tracks, regions, buses
  2. _ENTITY_CREATING_TOOLS — correct membership
  3. _ENTITY_ID_ECHO — correct field lists per tool
  4. EntityRegistry CRUD — create, resolve, exists, list
  5. EntityRegistry.sync_from_project_state — full project sync
  6. EntityRegistry.from_dict / to_dict round-trip
  7. get_or_create_bus idempotency
"""

import pytest
from unittest.mock import MagicMock

from app.core.entity_registry import (
    EntityRegistry,
    EntityInfo,
    EntityType,
    create_registry_from_context,
)
from app.core.maestro_handlers import (
    _entity_manifest,
    _ENTITY_CREATING_TOOLS,
    _ENTITY_ID_ECHO,
)


# ===========================================================================
# 1. _entity_manifest structure
# ===========================================================================

class TestEntityManifest:
    """_entity_manifest returns a compact entity listing for the LLM."""

    def _store_with_tracks(self, *track_names: str):
        """Return a mock store whose registry has the given tracks."""
        registry = EntityRegistry()
        track_ids = {}
        for name in track_names:
            tid = registry.create_track(name)
            track_ids[name] = tid
        store = MagicMock()
        store.registry = registry
        return store, registry, track_ids

    def test_empty_store_returns_empty_manifest(self):
        store = MagicMock()
        store.registry = EntityRegistry()
        manifest = _entity_manifest(store)
        assert manifest == {"tracks": [], "buses": []}

    def test_single_track_in_manifest(self):
        store, registry, ids = self._store_with_tracks("Drums")
        manifest = _entity_manifest(store)
        assert len(manifest["tracks"]) == 1
        track = manifest["tracks"][0]
        assert track["name"] == "Drums"
        assert track["trackId"] == ids["Drums"]

    def test_multiple_tracks_all_in_manifest(self):
        store, registry, ids = self._store_with_tracks("Drums", "Bass", "Melody")
        manifest = _entity_manifest(store)
        names = {t["name"] for t in manifest["tracks"]}
        assert names == {"Drums", "Bass", "Melody"}

    def test_regions_nested_under_tracks(self):
        store, registry, ids = self._store_with_tracks("Drums")
        tid = ids["Drums"]
        rid = registry.create_region("Intro", parent_track_id=tid)
        manifest = _entity_manifest(store)
        track_entry = manifest["tracks"][0]
        assert len(track_entry["regions"]) == 1
        region = track_entry["regions"][0]
        assert region["name"] == "Intro"
        assert region["regionId"] == rid

    def test_multiple_regions_on_same_track(self):
        store, registry, ids = self._store_with_tracks("Bass")
        tid = ids["Bass"]
        rid1 = registry.create_region("Intro Bass", parent_track_id=tid)
        rid2 = registry.create_region("Verse Bass", parent_track_id=tid)
        manifest = _entity_manifest(store)
        regions = manifest["tracks"][0]["regions"]
        assert len(regions) == 2
        region_ids = {r["regionId"] for r in regions}
        assert rid1 in region_ids
        assert rid2 in region_ids

    def test_bus_in_manifest(self):
        registry = EntityRegistry()
        bid = registry.create_bus("Reverb")
        store = MagicMock()
        store.registry = registry
        manifest = _entity_manifest(store)
        assert len(manifest["buses"]) == 1
        assert manifest["buses"][0]["name"] == "Reverb"
        assert manifest["buses"][0]["busId"] == bid

    def test_manifest_ids_are_valid_strings(self):
        store, registry, ids = self._store_with_tracks("Drums")
        tid = ids["Drums"]
        registry.create_region("Pattern", parent_track_id=tid)
        manifest = _entity_manifest(store)
        track = manifest["tracks"][0]
        assert isinstance(track["trackId"], str)
        assert len(track["trackId"]) > 8
        assert isinstance(track["regions"][0]["regionId"], str)


# ===========================================================================
# 2. _ENTITY_CREATING_TOOLS membership
# ===========================================================================

class TestEntityCreatingToolsConstant:
    """_ENTITY_CREATING_TOOLS must include all tools that create entities."""

    def test_add_midi_track_in_set(self):
        assert "stori_add_midi_track" in _ENTITY_CREATING_TOOLS

    def test_add_midi_region_in_set(self):
        assert "stori_add_midi_region" in _ENTITY_CREATING_TOOLS

    def test_ensure_bus_in_set(self):
        assert "stori_ensure_bus" in _ENTITY_CREATING_TOOLS

    def test_duplicate_region_in_set(self):
        assert "stori_duplicate_region" in _ENTITY_CREATING_TOOLS

    def test_non_entity_tools_not_in_set(self):
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

    def test_add_midi_track_echoes_track_id(self):
        assert "trackId" in _ENTITY_ID_ECHO["stori_add_midi_track"]

    def test_add_midi_region_echoes_region_and_track(self):
        fields = _ENTITY_ID_ECHO["stori_add_midi_region"]
        assert "regionId" in fields
        assert "trackId" in fields

    def test_ensure_bus_echoes_bus_id(self):
        assert "busId" in _ENTITY_ID_ECHO["stori_ensure_bus"]

    def test_duplicate_region_echoes_new_and_source_region(self):
        fields = _ENTITY_ID_ECHO["stori_duplicate_region"]
        assert "newRegionId" in fields
        assert "regionId" in fields

    def test_all_entity_creating_tools_have_echo_entry(self):
        """Every tool in _ENTITY_CREATING_TOOLS has at least one echo field."""
        for tool in _ENTITY_CREATING_TOOLS:
            assert tool in _ENTITY_ID_ECHO, f"{tool} missing from _ENTITY_ID_ECHO"
            assert len(_ENTITY_ID_ECHO[tool]) >= 1


# ===========================================================================
# 4. EntityRegistry — CRUD
# ===========================================================================

class TestEntityRegistryCRUD:
    """EntityRegistry create, resolve, exists, list operations."""

    def test_create_track_returns_id(self):
        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        assert isinstance(tid, str)
        assert len(tid) > 8

    def test_create_track_with_explicit_id(self):
        reg = EntityRegistry()
        tid = reg.create_track("Bass", track_id="fixed-id-123")
        assert tid == "fixed-id-123"

    def test_resolve_track_by_name(self):
        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        assert reg.resolve_track("Drums") == tid

    def test_resolve_track_case_insensitive(self):
        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        assert reg.resolve_track("drums") == tid
        assert reg.resolve_track("DRUMS") == tid

    def test_resolve_track_by_id(self):
        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        assert reg.resolve_track(tid) == tid

    def test_resolve_track_fuzzy_partial_match(self):
        reg = EntityRegistry()
        tid = reg.create_track("Kick Drums")
        assert reg.resolve_track("drums") == tid

    def test_resolve_track_unknown_returns_none(self):
        reg = EntityRegistry()
        assert reg.resolve_track("nonexistent") is None

    def test_exists_track_true(self):
        reg = EntityRegistry()
        tid = reg.create_track("Bass")
        assert reg.exists_track(tid)

    def test_exists_track_false(self):
        reg = EntityRegistry()
        assert not reg.exists_track("nonexistent-id")

    def test_create_region_returns_id(self):
        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        rid = reg.create_region("Pattern", parent_track_id=tid)
        assert isinstance(rid, str)

    def test_create_region_raises_for_unknown_parent(self):
        reg = EntityRegistry()
        with pytest.raises(ValueError, match="not found"):
            reg.create_region("Pattern", parent_track_id="bad-id")

    def test_resolve_region_by_name(self):
        reg = EntityRegistry()
        tid = reg.create_track("Bass")
        rid = reg.create_region("Verse", parent_track_id=tid)
        assert reg.resolve_region("Verse") == rid

    def test_resolve_region_scoped_by_parent_track(self):
        reg = EntityRegistry()
        tid1 = reg.create_track("Drums")
        tid2 = reg.create_track("Bass")
        rid1 = reg.create_region("Pattern", parent_track_id=tid1)
        rid2 = reg.create_region("Pattern", parent_track_id=tid2)
        assert reg.resolve_region("Pattern", parent_track=tid1) == rid1
        assert reg.resolve_region("Pattern", parent_track=tid2) == rid2

    def test_get_latest_region_for_track(self):
        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        rid1 = reg.create_region("First", parent_track_id=tid)
        rid2 = reg.create_region("Second", parent_track_id=tid)
        assert reg.get_latest_region_for_track(tid) == rid2

    def test_create_bus_returns_id(self):
        reg = EntityRegistry()
        bid = reg.create_bus("Reverb")
        assert isinstance(bid, str)

    def test_resolve_bus_by_name(self):
        reg = EntityRegistry()
        bid = reg.create_bus("Reverb")
        assert reg.resolve_bus("reverb") == bid

    def test_get_or_create_bus_idempotent(self):
        reg = EntityRegistry()
        bid1 = reg.get_or_create_bus("Delay")
        bid2 = reg.get_or_create_bus("Delay")
        assert bid1 == bid2

    def test_list_tracks(self):
        reg = EntityRegistry()
        reg.create_track("Drums")
        reg.create_track("Bass")
        tracks = reg.list_tracks()
        assert len(tracks) == 2
        names = {t.name for t in tracks}
        assert names == {"Drums", "Bass"}

    def test_list_regions(self):
        reg = EntityRegistry()
        tid = reg.create_track("Drums")
        reg.create_region("Intro", parent_track_id=tid)
        reg.create_region("Verse", parent_track_id=tid)
        assert len(reg.list_regions()) == 2

    def test_clear_removes_all_entities(self):
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

    def _project(self, **kwargs) -> dict:
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

    def test_tracks_synced(self):
        reg = EntityRegistry()
        reg.sync_from_project_state(self._project())
        assert reg.exists_track("t1")
        assert reg.exists_track("t2")

    def test_regions_synced(self):
        reg = EntityRegistry()
        reg.sync_from_project_state(self._project())
        assert reg.exists_region("r1")
        assert reg.exists_region("r2")
        assert reg.exists_region("r3")

    def test_buses_synced(self):
        reg = EntityRegistry()
        reg.sync_from_project_state(self._project())
        assert reg.exists_bus("b1")

    def test_resolve_track_after_sync(self):
        reg = EntityRegistry()
        reg.sync_from_project_state(self._project())
        assert reg.resolve_track("Drums") == "t1"
        assert reg.resolve_track("Bass") == "t2"

    def test_resolve_region_after_sync(self):
        reg = EntityRegistry()
        reg.sync_from_project_state(self._project())
        assert reg.resolve_region("Intro") == "r1"
        assert reg.resolve_region("Verse") == "r2"

    def test_duplicate_sync_is_idempotent(self):
        """Syncing the same state twice does not create duplicate entries."""
        reg = EntityRegistry()
        project = self._project()
        reg.sync_from_project_state(project)
        reg.sync_from_project_state(project)
        assert len(reg.list_tracks()) == 2
        assert len(reg.list_regions()) == 3

    def test_empty_project_state_no_error(self):
        reg = EntityRegistry()
        reg.sync_from_project_state({})
        assert reg.list_tracks() == []

    def test_create_registry_from_context_factory(self):
        reg = create_registry_from_context(self._project())
        assert reg.exists_track("t1")
        assert reg.exists_region("r1")

    def test_region_parent_linked_correctly(self):
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

    def test_round_trip_tracks(self):
        reg = EntityRegistry(project_id="proj-123")
        reg.create_track("Drums", track_id="t1")
        restored = EntityRegistry.from_dict(reg.to_dict())
        assert restored.exists_track("t1")
        assert restored.resolve_track("Drums") == "t1"

    def test_round_trip_regions(self):
        reg = EntityRegistry()
        tid = reg.create_track("Bass", track_id="t1")
        reg.create_region("Verse", parent_track_id=tid, region_id="r1")
        restored = EntityRegistry.from_dict(reg.to_dict())
        assert restored.exists_region("r1")
        r = restored.get_region("r1")
        assert r is not None
        assert r.parent_id == "t1"

    def test_round_trip_buses(self):
        reg = EntityRegistry()
        reg.create_bus("Reverb", bus_id="b1")
        restored = EntityRegistry.from_dict(reg.to_dict())
        assert restored.exists_bus("b1")
        assert restored.resolve_bus("reverb") == "b1"

    def test_round_trip_preserves_project_id(self):
        reg = EntityRegistry(project_id="my-project")
        restored = EntityRegistry.from_dict(reg.to_dict())
        assert restored.project_id == "my-project"

    def test_round_trip_track_region_link(self):
        reg = EntityRegistry()
        tid = reg.create_track("Drums", track_id="t1")
        reg.create_region("Pattern", parent_track_id=tid, region_id="r1")
        restored = EntityRegistry.from_dict(reg.to_dict())
        latest = restored.get_latest_region_for_track("t1")
        assert latest == "r1"
