"""
Tests for the EntityRegistry.

The EntityRegistry is the authoritative source of entity IDs in a project,
eliminating the "fabricated ID" failure mode where LLMs invent entity IDs.
"""
from __future__ import annotations

import pytest

from app.contracts.project_types import ProjectContext
from app.core.entity_registry import (
    EntityRegistry,
    EntityType,
    EntityInfo,
    create_registry_from_context,
)


class TestEntityRegistryBasics:
    """Test basic entity registry operations."""
    
    def test_create_registry(self) -> None:

        """Registry should initialize with empty collections."""
        registry = EntityRegistry()
        
        assert registry.project_id is not None
        assert len(registry.list_tracks()) == 0
        assert len(registry.list_regions()) == 0
        assert len(registry.list_buses()) == 0
    
    def test_create_registry_with_project_id(self) -> None:

        """Registry should accept custom project ID."""
        registry = EntityRegistry(project_id="my-project-123")
        assert registry.project_id == "my-project-123"
    
    def test_create_track(self) -> None:

        """Should create and register a track."""
        registry = EntityRegistry()
        
        track_id = registry.create_track("Drums")
        
        assert track_id is not None
        assert len(track_id) == 36  # UUID format
        assert registry.exists_track(track_id)
        info = registry.get_track(track_id)
        assert info is not None and info.name == "Drums"
    
    def test_create_track_with_custom_id(self) -> None:

        """Should accept custom track ID."""
        registry = EntityRegistry()
        
        track_id = registry.create_track("Drums", track_id="custom-track-id")
        
        assert track_id == "custom-track-id"
        assert registry.exists_track("custom-track-id")
    
    def test_create_region(self) -> None:

        """Should create and register a region with parent track."""
        registry = EntityRegistry()
        
        track_id = registry.create_track("Drums")
        region_id = registry.create_region("Main Pattern", parent_track_id=track_id)
        
        assert region_id is not None
        assert registry.exists_region(region_id)
        
        region = registry.get_region(region_id)
        assert region is not None and region.name == "Main Pattern" and region.parent_id == track_id
    
    def test_create_region_idempotent_same_beat_range(self) -> None:

        """Duplicate stori_add_midi_region at same beat range returns existing ID."""
        registry = EntityRegistry()
        track_id = registry.create_track("Strings")

        first_id = registry.create_region(
            "BUILD", track_id,
            metadata={"startBeat": 0, "durationBeats": 28},
        )
        second_id = registry.create_region(
            "BUILD", track_id,
            metadata={"startBeat": 0, "durationBeats": 28},
        )

        assert first_id == second_id
        assert len(registry.get_track_regions(track_id)) == 1

    def test_create_region_different_beat_range_creates_new(self) -> None:

        """Different beat ranges create distinct regions (not idempotent)."""
        registry = EntityRegistry()
        track_id = registry.create_track("Strings")

        first_id = registry.create_region(
            "VERSE", track_id,
            metadata={"startBeat": 0, "durationBeats": 16},
        )
        second_id = registry.create_region(
            "CHORUS", track_id,
            metadata={"startBeat": 16, "durationBeats": 16},
        )

        assert first_id != second_id
        assert len(registry.get_track_regions(track_id)) == 2

    def test_create_region_without_parent_fails(self) -> None:

        """Should fail to create region without valid parent track."""
        registry = EntityRegistry()
        
        with pytest.raises(ValueError, match="not found"):
            registry.create_region("Pattern", parent_track_id="nonexistent")
    
    def test_create_bus(self) -> None:

        """Should create and register a bus."""
        registry = EntityRegistry()
        
        bus_id = registry.create_bus("Reverb Bus")
        
        assert bus_id is not None
        assert registry.exists_bus(bus_id)
        bus = registry.get_bus(bus_id)
        assert bus is not None and bus.name == "Reverb Bus"

    def test_get_or_create_bus_new(self) -> None:

        """Should create bus if it doesn't exist."""
        registry = EntityRegistry()
        
        bus_id = registry.get_or_create_bus("Delay Bus")
        
        assert registry.exists_bus(bus_id)
        bus = registry.get_bus(bus_id)
        assert bus is not None and bus.name == "Delay Bus"

    def test_get_or_create_bus_existing(self) -> None:

        """Should return existing bus if it exists."""
        registry = EntityRegistry()
        
        bus_id1 = registry.create_bus("Reverb Bus")
        bus_id2 = registry.get_or_create_bus("Reverb Bus")
        
        assert bus_id1 == bus_id2


class TestEntityResolution:
    """Test entity name → ID resolution."""
    
    def test_resolve_track_by_id(self) -> None:

        """Should resolve exact ID match."""
        registry = EntityRegistry()
        
        track_id = registry.create_track("Drums")
        
        assert registry.resolve_track(track_id) == track_id
    
    def test_resolve_track_by_name(self) -> None:

        """Should resolve by name (case-insensitive)."""
        registry = EntityRegistry()
        
        track_id = registry.create_track("Drums")
        
        assert registry.resolve_track("Drums") == track_id
        assert registry.resolve_track("drums") == track_id
        assert registry.resolve_track("DRUMS") == track_id
    
    def test_resolve_track_partial_match(self) -> None:

        """Should resolve partial name matches."""
        registry = EntityRegistry()
        
        track_id = registry.create_track("Drums Track")
        
        # Partial match should work
        assert registry.resolve_track("drums") == track_id
    
    def test_resolve_track_exact_mode_no_fuzzy(self) -> None:

        """With exact=True, should NOT do fuzzy matching."""
        registry = EntityRegistry()
        
        track_id = registry.create_track("Phish Drums")
        
        # Exact mode: "Drums" should NOT match "Phish Drums"
        assert registry.resolve_track("Drums", exact=True) is None
        
        # But exact name should still match (case-insensitive)
        assert registry.resolve_track("Phish Drums", exact=True) == track_id
        assert registry.resolve_track("phish drums", exact=True) == track_id
    
    def test_similar_track_names_not_confused(self) -> None:

        """Different tracks with similar names should have different IDs."""
        registry = EntityRegistry()
        
        # Create two tracks with similar but distinct names
        track1_id = registry.create_track("Phish Drums")
        track2_id = registry.create_track("Drums")
        
        # They should have different IDs
        assert track1_id != track2_id
        
        # Exact matching should distinguish them
        assert registry.resolve_track("Phish Drums", exact=True) == track1_id
        assert registry.resolve_track("Drums", exact=True) == track2_id
        
        # Fuzzy matching finds first match (but this is for lookups, not creation)
        # The important thing is that creation uses exact matching
    
    def test_resolve_track_not_found(self) -> None:

        """Should return None for unknown tracks."""
        registry = EntityRegistry()
        
        assert registry.resolve_track("nonexistent") is None
    
    def test_resolve_region_by_id(self) -> None:

        """Should resolve region by ID."""
        registry = EntityRegistry()
        
        track_id = registry.create_track("Drums")
        region_id = registry.create_region("Pattern A", track_id)
        
        assert registry.resolve_region(region_id) == region_id
    
    def test_resolve_region_by_name(self) -> None:

        """Should resolve region by name."""
        registry = EntityRegistry()
        
        track_id = registry.create_track("Drums")
        region_id = registry.create_region("Pattern A", track_id)
        
        assert registry.resolve_region("Pattern A") == region_id
        assert registry.resolve_region("pattern a") == region_id
    
    def test_resolve_region_with_parent_scope(self) -> None:

        """Should scope region resolution to parent track."""
        registry = EntityRegistry()
        
        track1_id = registry.create_track("Drums")
        track2_id = registry.create_track("Bass")
        region1_id = registry.create_region("Pattern", track1_id)
        region2_id = registry.create_region("Pattern", track2_id)
        
        # Without parent scope, gets first match
        resolved = registry.resolve_region("Pattern")
        assert resolved in (region1_id, region2_id)
        
        # With parent scope, gets correct region
        assert registry.resolve_region("Pattern", parent_track=track1_id) == region1_id
        assert registry.resolve_region("Pattern", parent_track="Drums") == region1_id
    
    def test_resolve_bus(self) -> None:

        """Should resolve bus by name or ID."""
        registry = EntityRegistry()
        
        bus_id = registry.create_bus("Reverb")
        
        assert registry.resolve_bus(bus_id) == bus_id
        assert registry.resolve_bus("Reverb") == bus_id
        assert registry.resolve_bus("reverb") == bus_id


class TestTrackRegionRelationships:
    """Test track → region relationships."""
    
    def test_get_track_regions(self) -> None:

        """Should get all regions for a track."""
        registry = EntityRegistry()
        
        track_id = registry.create_track("Drums")
        region1 = registry.create_region("Pattern A", track_id)
        region2 = registry.create_region("Pattern B", track_id)
        region3 = registry.create_region("Fill", track_id)
        
        regions = registry.get_track_regions(track_id)
        
        assert len(regions) == 3
        assert {r.id for r in regions} == {region1, region2, region3}
    
    def test_get_latest_region_for_track(self) -> None:

        """Should get most recently created region."""
        registry = EntityRegistry()
        
        track_id = registry.create_track("Drums")
        registry.create_region("First", track_id)
        registry.create_region("Second", track_id)
        latest_id = registry.create_region("Latest", track_id)
        
        assert registry.get_latest_region_for_track(track_id) == latest_id
    
    def test_get_latest_region_empty(self) -> None:

        """Should return None if track has no regions."""
        registry = EntityRegistry()
        
        track_id = registry.create_track("Drums")
        
        assert registry.get_latest_region_for_track(track_id) is None


class TestProjectStateSync:
    """Test synchronization with client project state."""
    
    def test_sync_tracks_from_project_state(self) -> None:

        """Should sync tracks from project state."""
        registry = EntityRegistry()
        
        project_state: ProjectContext = {
            "tracks": [
                {"id": "track-1", "name": "Drums"},
                {"id": "track-2", "name": "Bass"},
            ]
        }

        registry.sync_from_project_state(project_state)
        
        assert registry.exists_track("track-1")
        assert registry.exists_track("track-2")
        assert registry.resolve_track("Drums") == "track-1"
        assert registry.resolve_track("Bass") == "track-2"
    
    def test_sync_regions_from_project_state(self) -> None:

        """Should sync regions from project state."""
        registry = EntityRegistry()
        
        project_state: ProjectContext = {
            "tracks": [
                {
                    "id": "track-1",
                    "name": "Drums",
                    "regions": [
                        {"id": "region-1", "name": "Pattern A"},
                        {"id": "region-2", "name": "Fill"},
                    ]
                }
            ]
        }

        registry.sync_from_project_state(project_state)
        
        assert registry.exists_region("region-1")
        assert registry.exists_region("region-2")
        r1 = registry.get_region("region-1")
        assert r1 is not None and r1.parent_id == "track-1"
    
    def test_sync_buses_from_project_state(self) -> None:

        """Should sync buses from project state."""
        registry = EntityRegistry()
        
        project_state: ProjectContext = {
            "tracks": [],
            "buses": [
                {"id": "bus-1", "name": "Reverb"},
                {"id": "bus-2", "name": "Delay"},
            ]
        }

        registry.sync_from_project_state(project_state)
        
        assert registry.exists_bus("bus-1")
        assert registry.exists_bus("bus-2")
    
    def test_create_registry_from_context(self) -> None:

        """Convenience function should create and sync."""
        project_state: ProjectContext = {
            "tracks": [
                {"id": "track-drums", "name": "Drums"},
            ]
        }

        registry = create_registry_from_context(project_state)
        
        assert registry.exists_track("track-drums")


class TestSerialization:
    """Test registry serialization/deserialization."""
    
    def test_to_dict(self) -> None:

        """Should serialize registry to dict."""
        registry = EntityRegistry(project_id="test-project")
        
        track_id = registry.create_track("Drums")
        region_id = registry.create_region("Pattern", track_id)
        bus_id = registry.create_bus("Reverb")
        
        data = registry.to_dict()
        
        assert data["project_id"] == "test-project"
        tracks = data["tracks"]
        regions = data["regions"]
        buses = data["buses"]
        assert isinstance(tracks, dict)
        assert isinstance(regions, dict)
        assert isinstance(buses, dict)
        assert track_id in tracks
        assert region_id in regions
        assert bus_id in buses
    
    def test_from_dict(self) -> None:

        """Should deserialize registry from dict."""
        original = EntityRegistry(project_id="test-project")
        
        track_id = original.create_track("Drums")
        region_id = original.create_region("Pattern", track_id)
        bus_id = original.create_bus("Reverb")
        
        data = original.to_dict()
        restored = EntityRegistry.from_dict(data)
        
        assert restored.project_id == "test-project"
        assert restored.exists_track(track_id)
        assert restored.exists_region(region_id)
        assert restored.exists_bus(bus_id)
        assert restored.resolve_track("Drums") == track_id
