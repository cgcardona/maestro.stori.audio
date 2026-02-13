"""Tests for entity context builder (LLM prompt injection)."""
from unittest.mock import MagicMock
import pytest

from app.core.entity_context import build_entity_context_for_llm


def _make_entity(id: str, name: str, parent_id: str | None = None):
    e = MagicMock()
    e.id = id
    e.name = name
    e.parent_id = parent_id
    return e


class TestBuildEntityContextForLlm:
    """Test build_entity_context_for_llm."""

    def test_empty_registry(self):
        """No tracks/regions/buses produces (none) and example placeholder."""
        registry = MagicMock()
        registry.list_tracks.return_value = []
        registry.list_regions.return_value = []
        registry.list_buses.return_value = []
        store = MagicMock()
        store.registry = registry

        out = build_entity_context_for_llm(store)
        assert "Tracks: (none)" in out
        assert "Regions: (none)" in out
        assert "Buses: (none)" in out
        assert "Available entities" in out
        assert "trackId" in out
        assert "trackName" in out
        assert "abc-123" in out
        assert "My Track" in out

    def test_with_tracks_and_regions(self):
        """Tracks and regions appear in context with ids."""
        registry = MagicMock()
        registry.list_tracks.return_value = [
            _make_entity("track-1", "Drums"),
            _make_entity("track-2", "Bass"),
        ]
        registry.list_regions.return_value = [
            _make_entity("region-1", "Verse", parent_id="track-1"),
        ]
        registry.list_buses.return_value = [
            _make_entity("bus-1", "Reverb"),
        ]
        store = MagicMock()
        store.registry = registry

        out = build_entity_context_for_llm(store)
        assert "Drums" in out
        assert "track-1" in out
        assert "Bass" in out
        assert "Verse" in out
        assert "region-1" in out
        assert "trackId" in out
        assert "Reverb" in out
        assert "bus-1" in out

    def test_example_uses_first_track_when_present(self):
        """Example at end uses first track id and name."""
        registry = MagicMock()
        registry.list_tracks.return_value = [_make_entity("my-id-99", "Piano")]
        registry.list_regions.return_value = []
        registry.list_buses.return_value = []
        store = MagicMock()
        store.registry = registry

        out = build_entity_context_for_llm(store)
        assert "my-id-99" in out
        assert "Piano" in out
        assert "stori_add_midi_region" in out
