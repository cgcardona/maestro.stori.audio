"""Tests for entity context builder (LLM prompt injection)."""
from unittest.mock import MagicMock
import pytest

from app.core.entity_context import build_entity_context_for_llm, format_project_context


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


class TestFormatProjectContext:
    """Test format_project_context produces clean, human-readable LLM context."""

    def test_empty_project(self):
        """Empty project (no tracks) produces clear instruction to create from scratch."""
        project = {
            "name": "New Project",
            "tempo": 120,
            "key": "C",
            "timeSignature": "4/4",
            "tracks": [],
        }
        out = format_project_context(project)
        assert "New Project" in out
        assert "120 BPM" in out
        assert "Key: C" in out
        assert "4/4" in out
        assert "empty project" in out.lower()
        assert "create tracks from scratch" in out.lower()

    def test_project_with_tracks_and_regions(self):
        """Tracks with regions include IDs, instruments, and note counts."""
        project = {
            "name": "Bluegrass Jam",
            "tempo": 140,
            "key": "G",
            "timeSignature": "4/4",
            "tracks": [
                {
                    "id": "track-banjo",
                    "name": "Banjo",
                    "drumKitId": None,
                    "gmProgram": 105,
                    "regions": [
                        {
                            "id": "region-intro",
                            "name": "Banjo Intro",
                            "startBeat": 0,
                            "durationBeats": 32,
                            "noteCount": 47,
                        }
                    ],
                },
                {
                    "id": "track-drums",
                    "name": "Drums",
                    "drumKitId": "acoustic",
                    "gmProgram": None,
                    "regions": [],
                },
            ],
        }
        out = format_project_context(project)
        assert "Bluegrass Jam" in out
        assert "140 BPM" in out
        assert "Key: G" in out
        assert "Tracks: 2" in out
        # Track 1 — Banjo with GM instrument name
        assert "Banjo" in out
        assert "trackId=track-banjo" in out
        assert "id=region-intro" in out
        assert "47 notes" in out
        assert "0–32 beats" in out
        # Track 2 — Drums with kit name
        assert "Drums (acoustic)" in out
        assert "trackId=track-drums" in out
        assert "no regions" in out
        # Instruction to use IDs
        assert "Use the track IDs" in out

    def test_track_with_gm_program_only(self):
        """Track with gmProgram but no drumKitId shows instrument name."""
        project = {
            "name": "Test",
            "tempo": 90,
            "key": "Am",
            "tracks": [
                {
                    "id": "t1",
                    "name": "Bass",
                    "drumKitId": None,
                    "gmProgram": 33,
                    "regions": [],
                }
            ],
        }
        out = format_project_context(project)
        assert "Electric Bass (finger)" in out

    def test_track_with_unknown_gm_program(self):
        """Unknown GM program falls back to 'GM #N'."""
        project = {
            "name": "Test",
            "tempo": 90,
            "key": "Am",
            "tracks": [
                {
                    "id": "t1",
                    "name": "Mystery",
                    "drumKitId": None,
                    "gmProgram": 127,
                    "regions": [],
                }
            ],
        }
        out = format_project_context(project)
        assert "GM #127" in out

    def test_time_signature_dict_format(self):
        """Time signature as dict {numerator, denominator} is handled."""
        project = {
            "name": "Waltz",
            "tempo": 100,
            "key": "D",
            "timeSignature": {"numerator": 3, "denominator": 4},
            "tracks": [],
        }
        out = format_project_context(project)
        assert "3/4" in out

    def test_multiple_regions_on_track(self):
        """Multiple regions on a single track are listed with semicolons."""
        project = {
            "name": "Multi-region",
            "tempo": 120,
            "key": "C",
            "tracks": [
                {
                    "id": "t1",
                    "name": "Piano",
                    "gmProgram": 0,
                    "regions": [
                        {
                            "id": "r1",
                            "name": "Verse",
                            "startBeat": 0,
                            "durationBeats": 16,
                            "noteCount": 30,
                        },
                        {
                            "id": "r2",
                            "name": "Chorus",
                            "startBeat": 16,
                            "durationBeats": 16,
                            "noteCount": 0,
                        },
                    ],
                }
            ],
        }
        out = format_project_context(project)
        assert "Verse" in out
        assert "Chorus" in out
        assert "30 notes" in out
        assert "0 notes" in out
        assert "id=r1" in out
        assert "id=r2" in out

    def test_no_raw_json_in_output(self):
        """Output is human-readable, not a JSON dump."""
        project = {
            "name": "Test",
            "tempo": 120,
            "key": "C",
            "tracks": [{"id": "t1", "name": "Bass", "gmProgram": 33, "regions": []}],
        }
        out = format_project_context(project)
        # Should not contain JSON syntax
        assert "{" not in out
        assert "}" not in out
        assert '"id"' not in out
