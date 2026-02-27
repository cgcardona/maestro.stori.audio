"""Tests for entity context builder (LLM prompt injection)."""
from __future__ import annotations

from app.contracts.json_types import JSONValue
from unittest.mock import MagicMock

from app.contracts.project_types import ProjectContext
import pytest

from app.core.entity_context import build_entity_context_for_llm, format_project_context, infer_track_role
from app.core.entity_registry import EntityMetadata
from app.core.tool_validation import ValidationResult


def _make_entity(
    id: str,
    name: str,
    parent_id: str | None = None,
    metadata: dict[str, JSONValue] | None = None,
) -> MagicMock:
    e = MagicMock()
    e.id = id
    e.name = name
    e.parent_id = parent_id
    e.metadata = EntityMetadata.from_dict(metadata)
    return e


class TestBuildEntityContextForLlm:
    """Test build_entity_context_for_llm."""

    def test_empty_registry(self) -> None:

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

    def test_with_tracks_and_regions(self) -> None:

        """Tracks and regions appear in context with ids."""
        registry = MagicMock()
        registry.list_tracks.return_value = [
            _make_entity("track-1", "Drums"),
            _make_entity("track-2", "Bass"),
        ]
        registry.list_regions.return_value = [
            _make_entity("region-1", "Verse", parent_id="track-1",
                         metadata={"startBeat": 0, "durationBeats": 16}),
        ]
        registry.list_buses.return_value = [
            _make_entity("bus-1", "Reverb"),
        ]
        store = MagicMock()
        store.registry = registry
        store.get_region_notes = MagicMock(return_value=[{"pitch": 60}])

        out = build_entity_context_for_llm(store)
        assert "Drums" in out
        assert "track-1" in out
        assert "Bass" in out
        assert "Verse" in out
        assert "region-1" in out
        assert "trackId" in out
        assert "Reverb" in out
        assert "bus-1" in out
        assert "noteCount" in out

    def test_regions_include_note_count(self) -> None:

        """Regions in entity context must include noteCount to prevent re-add loops."""
        registry = MagicMock()
        region = _make_entity("r-1", "Pattern", parent_id="t-1",
                              metadata={"startBeat": 0, "durationBeats": 32})
        registry.list_tracks.return_value = [_make_entity("t-1", "Drums")]
        registry.list_regions.return_value = [region]
        registry.list_buses.return_value = []
        store = MagicMock()
        store.registry = registry
        store.get_region_notes = MagicMock(return_value=[
            {"pitch": 36, "start_beat": 0, "duration_beats": 0.5, "velocity": 100},
            {"pitch": 38, "start_beat": 1, "duration_beats": 0.5, "velocity": 90},
        ])

        out = build_entity_context_for_llm(store)
        assert "'noteCount': 2" in out or "noteCount" in out
        store.get_region_notes.assert_called_with("r-1")

    def test_notecount_check_instruction_present(self) -> None:

        """Entity context must include instruction to check noteCount before adding."""
        registry = MagicMock()
        registry.list_tracks.return_value = []
        registry.list_regions.return_value = []
        registry.list_buses.return_value = []
        store = MagicMock()
        store.registry = registry

        out = build_entity_context_for_llm(store)
        assert "noteCount" in out
        assert "re-add" in out.lower() or "do not" in out.lower()

    def test_example_uses_first_track_when_present(self) -> None:

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

    def test_empty_project(self) -> None:

        """Empty project (no tracks) produces clear instruction to create from scratch."""
        project: ProjectContext = {
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

    def test_project_with_tracks_and_regions(self) -> None:

        """Tracks with regions include IDs, instruments, and note counts."""
        project: ProjectContext = {
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

    def test_track_with_gm_program_only(self) -> None:

        """Track with gmProgram but no drumKitId shows instrument name."""
        project: ProjectContext = {
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

    def test_track_with_unknown_gm_program(self) -> None:

        """Unknown GM program falls back to 'GM #N'."""
        project: ProjectContext = {
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

    def test_time_signature_dict_format(self) -> None:

        """Time signature as dict {numerator, denominator} is handled."""
        project: ProjectContext = {
            "name": "Waltz",
            "tempo": 100,
            "key": "D",
            "timeSignature": {"numerator": 3, "denominator": 4},
            "tracks": [],
        }
        out = format_project_context(project)
        assert "3/4" in out

    def test_multiple_regions_on_track(self) -> None:

        """Multiple regions on a single track are listed with semicolons."""
        project: ProjectContext = {
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

    def test_no_raw_json_in_output(self) -> None:

        """Output is human-readable, not a JSON dump."""
        project: ProjectContext = {
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


# =============================================================================
# infer_track_role
# =============================================================================

class TestInferTrackRole:
    """infer_track_role maps track metadata to a musical role."""

    def test_drum_kit_always_drums(self) -> None:

        """A track with drumKitId → role=drums regardless of name or program."""
        assert infer_track_role("My Beat", None, "acoustic") == "drums"
        assert infer_track_role("Piano", 0, "TR-808") == "drums"

    def test_bass_by_name(self) -> None:

        assert infer_track_role("Bass", None, None) == "bass"
        assert infer_track_role("Electric Bass", 33, None) == "bass"

    def test_bass_by_gm_program(self) -> None:

        """GM programs 32-39 are bass family."""
        assert infer_track_role("Track", 33, None) == "bass"
        assert infer_track_role("Track", 38, None) == "bass"

    def test_pads_by_name(self) -> None:

        """'Cathedral Pad' resolves to pads role."""
        assert infer_track_role("Cathedral Pad", 19, None) == "pads"
        assert infer_track_role("Atmosphere", None, None) == "pads"

    def test_pads_by_gm_organ(self) -> None:

        """A generic track name with Organ GM program resolves to pads via GM range.
        Name-keyword match wins over GM range, so use a neutral name."""
        assert infer_track_role("Track", 19, None) == "pads"

    def test_chords_by_name(self) -> None:

        assert infer_track_role("Rhodes", 4, None) == "chords"
        assert infer_track_role("Piano", 0, None) == "chords"

    def test_melody_default(self) -> None:

        """Unknown track falls back to melody."""
        assert infer_track_role("Misc", None, None) == "melody"

    def test_melody_by_gm_strings(self) -> None:

        """GM 40-55 strings/orchestral → melody."""
        assert infer_track_role("Track", 48, None) == "melody"

    def test_lead_by_gm_synth(self) -> None:

        """GM 80-87 synth leads → lead."""
        assert infer_track_role("Track", 80, None) == "lead"


class TestFormatProjectContextRole:
    """format_project_context includes role field for each track."""

    def test_role_shown_for_drum_track(self) -> None:

        project: ProjectContext = {
            "tracks": [{"id": "t1", "name": "Drums", "drumKitId": "acoustic", "regions": []}]
        }
        out = format_project_context(project)
        assert "role=drums" in out

    def test_role_shown_for_bass_track(self) -> None:

        project: ProjectContext = {
            "tracks": [{"id": "t1", "name": "Bass", "gmProgram": 33, "regions": []}]
        }
        out = format_project_context(project)
        assert "role=bass" in out

    def test_cathedral_pad_inferred_as_pads(self) -> None:

        """'Cathedral Pad' track (Church Organ GM 19) should show role=pads."""
        project: ProjectContext = {
            "tracks": [{"id": "t1", "name": "Cathedral Pad", "gmProgram": 19, "regions": []}]
        }
        out = format_project_context(project)
        assert "role=pads" in out

    def test_new_section_rule_shown_when_tracks_exist(self) -> None:

        """NEW SECTION RULE instruction appears when project has tracks."""
        project: ProjectContext = {
            "tracks": [{"id": "t1", "name": "Drums", "drumKitId": "acoustic", "regions": []}]
        }
        out = format_project_context(project)
        assert "NEW SECTION RULE" in out

    def test_new_section_rule_absent_for_empty_project(self) -> None:

        """No NEW SECTION RULE when project has no tracks."""
        project: ProjectContext = {"tracks": []}
        out = format_project_context(project)
        assert "NEW SECTION RULE" not in out


# =============================================================================
# stori_add_notes fake-param validation (Bug A)
# =============================================================================

class TestAddNotesValidation:
    """stori_add_notes rejects fake shorthand params with a clear error."""

    def _validate(self, params: dict[str, JSONValue]) -> ValidationResult:
        from app.core.tool_validation import validate_tool_call
        return validate_tool_call("stori_add_notes", params, {"stori_add_notes"}, registry=None)

    def test_fake_note_count_param_rejected(self) -> None:

        """_noteCount is a known fake param — validation must fail with clear message."""
        result = self._validate({"regionId": "r1", "_noteCount": 192})
        assert not result.valid
        assert "_noteCount" in result.error_message

    def test_beat_range_param_rejected(self) -> None:

        """_beatRange is a known fake param — validation must fail."""
        result = self._validate({"regionId": "r1", "_beatRange": "0-64"})
        assert not result.valid
        assert "_beatRange" in result.error_message or "notes" in result.error_message

    def test_placeholder_param_rejected(self) -> None:

        """_placeholder is a known fake param — validation must fail."""
        result = self._validate({"regionId": "r1", "_placeholder": True})
        assert not result.valid

    def test_empty_notes_array_rejected(self) -> None:

        """An empty notes array must fail with a helpful message."""
        result = self._validate({"regionId": "r1", "notes": []})
        assert not result.valid
        assert "notes" in result.error_message.lower()

    def test_valid_notes_accepted(self) -> None:

        """A proper notes array passes validation."""
        result = self._validate({
            "regionId": "r1",
            "notes": [{"pitch": 60, "startBeat": 0, "durationBeats": 1, "velocity": 80}],
        })
        assert result.valid

    def test_missing_notes_key_rejected(self) -> None:

        """Missing notes key (only regionId provided) fails validation."""
        result = self._validate({"regionId": "r1"})
        assert not result.valid
