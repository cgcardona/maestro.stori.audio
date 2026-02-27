"""
Tests for app.core.planner — all LLM calls mocked.

Coverage:
  1. _try_deterministic_plan — field requirements, start_beat propagation
  2. _schema_to_tool_calls — execution order, region_start_offset
  3. build_execution_plan — mocked LLM happy path, validation failure, empty plan
  4. build_plan_from_dict — direct dict → ExecutionPlan conversion
  5. Position: → startBeat regression — offset applied in deterministic path
  6. ExecutionPlan properties — is_valid, generation_count, edit_count
"""
from __future__ import annotations

import json
import typing
from collections.abc import AsyncGenerator

from app.contracts.json_types import JSONObject
from app.core.plan_schemas.plan_json_types import (
    EditStepDict,
    GenerationStepDict,
    PlanJsonDict,
)
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.contracts.project_types import ProjectContext
from app.core.planner import (
    ExecutionPlan,
    _try_deterministic_plan,
    _schema_to_tool_calls,
    _match_roles_to_existing_tracks,
    build_execution_plan,
    build_execution_plan_stream,
    build_plan_from_dict,
)
from app.prompts import parse_prompt, MaestroPrompt, PositionSpec
from app.core.intent import IntentResult, Intent, SSEState
from app.core.expansion import ToolCall
from app.core.plan_schemas import ExecutionPlanSchema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_route(intent: Intent = Intent.GENERATE_MUSIC) -> IntentResult:

    from app.core.intent import Slots
    return IntentResult(
        intent=intent,
        sse_state=SSEState.COMPOSING,
        confidence=0.99,
        slots=Slots(value_str="", extras={}),
        tools=[],
        allowed_tool_names=set(),
        tool_choice=None,
        force_stop_after=False,
    )


def _minimal_parsed(
    style: str = "house",
    tempo: int = 128,
    key: str = "Am",
    roles: list[str] | None = None,
    bars: int = 8,
    position: PositionSpec | None = None,
) -> MaestroPrompt:
    """Build a MaestroPrompt directly for deterministic-plan tests."""
    return MaestroPrompt(
        mode="compose",
        request="make a beat",
        style=style,
        tempo=tempo,
        key=key,
        roles=["kick", "bass"] if roles is None else roles,
        constraints={"bars": bars},
        position=position,
        raw="MAESTRO PROMPT\n...",
    )


def _llm_with_response(json_body: PlanJsonDict) -> AsyncMock:
    """Return a mock LLM that yields the given plan JSON as its chat response."""
    llm = AsyncMock()
    response_text = json.dumps(json_body)
    llm.chat.return_value = MagicMock(content=response_text)
    return llm


def _valid_plan_json(bars: int = 8, tempo: int = 128) -> PlanJsonDict:
    """Return a minimal valid plan JSON fixture."""
    drums: GenerationStepDict = {"role": "drums", "style": "house", "tempo": tempo, "bars": bars}
    bass: GenerationStepDict = {"role": "bass", "style": "house", "tempo": tempo, "bars": bars, "key": "Am"}

    add_drums: EditStepDict = {"action": "add_track", "name": "Drums"}
    add_bass: EditStepDict = {"action": "add_track", "name": "Bass"}
    add_drums_region: EditStepDict = {"action": "add_region", "track": "Drums", "barStart": 0, "bars": bars}
    add_bass_region: EditStepDict = {"action": "add_region", "track": "Bass", "barStart": 0, "bars": bars}

    return {
        "generations": [drums, bass],
        "edits": [add_drums, add_bass, add_drums_region, add_bass_region],
        "mix": [],
    }


# ===========================================================================
# 0. _match_roles_to_existing_tracks
# ===========================================================================

class TestMatchRolesToExistingTracks:
    """_match_roles_to_existing_tracks maps roles to project tracks."""

    def test_name_match_drums_and_bass(self) -> None:

        project: ProjectContext = {
            "tracks": [
                {"id": "D-UUID", "name": "Drums"},
                {"id": "B-UUID", "name": "Bass"},
            ]
        }
        result = _match_roles_to_existing_tracks({"drums", "bass"}, project)
        assert result["drums"]["id"] == "D-UUID"
        assert result["bass"]["id"] == "B-UUID"

    def test_instrument_keyword_melody_to_organ(self) -> None:

        project: ProjectContext = {
            "tracks": [
                {"id": "D-UUID", "name": "Drums"},
                {"id": "O-UUID", "name": "Organ", "gmProgram": 16},
            ]
        }
        result = _match_roles_to_existing_tracks({"drums", "melody"}, project)
        assert result["drums"]["id"] == "D-UUID"
        assert "melody" in result
        assert result["melody"]["id"] == "O-UUID"

    def test_no_match_returns_empty(self) -> None:

        project: ProjectContext = {"tracks": [{"id": "X", "name": "FX"}]}
        result = _match_roles_to_existing_tracks({"drums"}, project)
        assert "drums" not in result

    def test_empty_tracks(self) -> None:

        project: ProjectContext = {"tracks": []}
        result = _match_roles_to_existing_tracks({"drums"}, project)
        assert result == {}

    def test_no_double_claim(self) -> None:

        """A single track should not be claimed by multiple roles."""
        project: ProjectContext = {
            "tracks": [
                {"id": "P-UUID", "name": "Piano"},
            ]
        }
        result = _match_roles_to_existing_tracks({"melody", "chords"}, project)
        claimed = [r for r in result if result[r]["id"] == "P-UUID"]
        assert len(claimed) <= 1


# ===========================================================================
# 1. _try_deterministic_plan
# ===========================================================================

class TestTryDeterministicPlan:
    """_try_deterministic_plan builds a plan without the LLM when all fields are present."""

    def test_returns_plan_when_all_fields_present(self) -> None:

        parsed = _minimal_parsed()
        plan = _try_deterministic_plan(parsed)
        assert plan is not None
        assert isinstance(plan, ExecutionPlan)

    def test_returns_none_when_style_missing(self) -> None:

        parsed = _minimal_parsed()
        parsed.style = None
        assert _try_deterministic_plan(parsed) is None

    def test_returns_none_when_tempo_missing(self) -> None:

        parsed = _minimal_parsed()
        parsed.tempo = None
        assert _try_deterministic_plan(parsed) is None

    def test_returns_none_when_roles_empty(self) -> None:

        parsed = _minimal_parsed(roles=[])
        assert _try_deterministic_plan(parsed) is None

    def test_returns_none_when_bars_missing_from_constraints(self) -> None:

        parsed = _minimal_parsed()
        parsed.constraints = {}
        assert _try_deterministic_plan(parsed) is None

    def test_returns_none_when_bars_is_zero(self) -> None:

        parsed = _minimal_parsed(bars=0)
        assert _try_deterministic_plan(parsed) is None

    def test_plan_has_generate_calls_for_each_role(self) -> None:

        parsed = _minimal_parsed(roles=["kick", "bass", "melody"])
        plan = _try_deterministic_plan(parsed)
        assert plan is not None
        gen_calls = [tc for tc in plan.tool_calls if tc.name == "stori_generate_midi"]
        assert len(gen_calls) == 3

    def test_plan_is_safety_validated(self) -> None:

        plan = _try_deterministic_plan(_minimal_parsed())
        assert plan is not None
        assert plan.safety_validated is True

    def test_deterministic_plan_note_mentions_structured_prompt(self) -> None:

        plan = _try_deterministic_plan(_minimal_parsed())
        assert plan is not None
        assert any("structured prompt" in note.lower() or "deterministic" in note.lower()
                   for note in plan.notes)

    def test_start_beat_zero_by_default(self) -> None:

        parsed = _minimal_parsed()
        plan = _try_deterministic_plan(parsed, start_beat=0.0)
        assert plan is not None
        region_calls = [tc for tc in plan.tool_calls if tc.name == "stori_add_midi_region"]
        for call in region_calls:
            assert call.params.get("startBeat", 0) == 0

    def test_start_beat_offset_applied_to_regions(self) -> None:

        """Regression: Position: after intro should shift all region startBeats by 64."""
        parsed = _minimal_parsed()
        plan = _try_deterministic_plan(parsed, start_beat=64.0)
        assert plan is not None
        region_calls = [tc for tc in plan.tool_calls if tc.name == "stori_add_midi_region"]
        assert len(region_calls) > 0
        for call in region_calls:
            _start = call.params.get("startBeat", 0)
            start = _start if isinstance(_start, (int, float)) else 0
            assert start >= 64.0, (
                f"startBeat={start} should be >=64 when start_beat=64 is passed. "
                "Position offset was not applied."
            )

    def test_start_beat_offset_in_plan_notes(self) -> None:

        """Non-zero start_beat is recorded in plan notes for traceability."""
        plan = _try_deterministic_plan(_minimal_parsed(), start_beat=32.0)
        assert plan is not None
        notes_text = " ".join(plan.notes)
        assert "32" in notes_text or "position_offset" in notes_text

    def test_tempo_from_parsed_in_generate_calls(self) -> None:

        parsed = _minimal_parsed(tempo=140)
        plan = _try_deterministic_plan(parsed)
        assert plan is not None
        for tc in plan.tool_calls:
            if tc.name == "stori_generate_midi":
                assert tc.params.get("tempo") == 140

    def test_style_from_parsed_in_generate_calls(self) -> None:

        parsed = _minimal_parsed(style="jazz")
        plan = _try_deterministic_plan(parsed)
        assert plan is not None
        for tc in plan.tool_calls:
            if tc.name == "stori_generate_midi":
                assert tc.params.get("style") == "jazz"


# ===========================================================================
# 2. _schema_to_tool_calls
# ===========================================================================

class TestSchemaToToolCalls:
    """_schema_to_tool_calls converts a validated schema into ToolCall ordering."""

    def _make_schema(self, bars: int = 8, tempo: int = 128) -> "ExecutionPlanSchema":

        from app.core.plan_schemas import ExecutionPlanSchema, GenerationStep, EditStep
        return ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="house", tempo=tempo, bars=bars),
                GenerationStep(role="bass",  style="house", tempo=tempo, bars=bars),
            ],
            edits=[
                EditStep(action="add_track", name="Drums"),
                EditStep(action="add_track", name="Bass"),
                EditStep(action="add_region", track="Drums", barStart=0, bars=bars),
                EditStep(action="add_region", track="Bass",  barStart=0, bars=bars),
            ],
            mix=[],
        )

    def test_tracks_before_regions_before_generators(self) -> None:

        """Execution order: add_track → add_region → generate_midi."""
        schema = self._make_schema()
        calls = _schema_to_tool_calls(schema)
        names = [tc.name for tc in calls]
        # Find first occurrence of each type
        first_track = next(i for i, n in enumerate(names) if n == "stori_add_midi_track")
        first_region = next(i for i, n in enumerate(names) if n == "stori_add_midi_region")
        first_gen = next(i for i, n in enumerate(names) if n == "stori_generate_midi")
        assert first_track < first_region < first_gen

    def test_region_start_offset_zero(self) -> None:

        schema = self._make_schema()
        calls = _schema_to_tool_calls(schema, region_start_offset=0.0)
        for tc in calls:
            if tc.name == "stori_add_midi_region":
                assert tc.params["startBeat"] == 0.0

    def test_region_start_offset_applied(self) -> None:

        """All new regions are shifted by region_start_offset beats."""
        schema = self._make_schema(bars=8)
        calls = _schema_to_tool_calls(schema, region_start_offset=64.0)
        for tc in calls:
            if tc.name == "stori_add_midi_region":
                _sb = tc.params["startBeat"]
                sb = _sb if isinstance(_sb, (int, float)) else 0
                assert sb >= 64.0, f"startBeat {_sb} not offset by 64"

    def test_generates_two_track_calls(self) -> None:

        schema = self._make_schema()
        calls = _schema_to_tool_calls(schema)
        track_calls = [tc for tc in calls if tc.name == "stori_add_midi_track"]
        assert len(track_calls) == 2

    def test_generates_two_region_calls(self) -> None:

        schema = self._make_schema()
        calls = _schema_to_tool_calls(schema)
        region_calls = [tc for tc in calls if tc.name == "stori_add_midi_region"]
        assert len(region_calls) == 2

    def test_generates_two_generation_calls(self) -> None:

        schema = self._make_schema()
        calls = _schema_to_tool_calls(schema)
        gen_calls = [tc for tc in calls if tc.name == "stori_generate_midi"]
        assert len(gen_calls) == 2

    def test_region_duration_from_bars(self) -> None:

        """8 bars × 4 beats/bar = 32 durationBeats."""
        schema = self._make_schema(bars=8)
        calls = _schema_to_tool_calls(schema)
        for tc in calls:
            if tc.name == "stori_add_midi_region":
                assert tc.params["durationBeats"] == 32

    def test_generate_calls_include_role(self) -> None:

        schema = self._make_schema()
        calls = _schema_to_tool_calls(schema)
        roles_generated = {
            tc.params["role"]
            for tc in calls
            if tc.name == "stori_generate_midi"
        }
        assert "drums" in roles_generated
        assert "bass" in roles_generated

    def test_insert_effects_after_generator_per_track(self) -> None:

        """Insert effects come after the generator within the same track group."""
        from app.core.plan_schemas import ExecutionPlanSchema, GenerationStep, EditStep, MixStep
        schema = ExecutionPlanSchema(
            generations=[GenerationStep(role="drums", style="house", tempo=128, bars=4)],
            edits=[
                EditStep(action="add_track", name="Drums"),
                EditStep(action="add_region", track="Drums", barStart=0, bars=4),
            ],
            mix=[MixStep(action="add_insert", track="Drums", type="compressor")],
        )
        calls = _schema_to_tool_calls(schema)
        names = [tc.name for tc in calls]
        last_effect = max(i for i, n in enumerate(names) if n == "stori_add_insert_effect")
        first_gen = next(i for i, n in enumerate(names) if n == "stori_generate_midi")
        assert last_effect > first_gen

    # -- Bug 1: Skip track creation for existing tracks -------------------------

    def test_skips_add_track_for_existing_tracks(self) -> None:

        """When project_state has Drums and Bass, stori_add_midi_track calls are skipped."""
        schema = self._make_schema()
        project_state: ProjectContext = {
            "tracks": [
                {"id": "DRUMS-UUID", "name": "Drums"},
                {"id": "BASS-UUID", "name": "Bass"},
            ]
        }
        calls = _schema_to_tool_calls(schema, project_state=project_state)
        track_calls = [tc for tc in calls if tc.name == "stori_add_midi_track"]
        assert len(track_calls) == 0, "Should not create tracks that already exist"

    def test_skips_color_and_icon_for_existing_tracks(self) -> None:

        """Existing tracks should not get stori_set_track_color or stori_set_track_icon."""
        schema = self._make_schema()
        project_state: ProjectContext = {
            "tracks": [
                {"id": "DRUMS-UUID", "name": "Drums"},
                {"id": "BASS-UUID", "name": "Bass"},
            ]
        }
        calls = _schema_to_tool_calls(schema, project_state=project_state)
        color_calls = [tc for tc in calls if tc.name == "stori_set_track_color"]
        icon_calls = [tc for tc in calls if tc.name == "stori_set_track_icon"]
        assert len(color_calls) == 0, "Should not set color for existing tracks"
        assert len(icon_calls) == 0, "Should not set icon for existing tracks"

    def test_creates_track_when_not_in_project(self) -> None:

        """Tracks not in project_state should still be created."""
        schema = self._make_schema()
        project_state: ProjectContext = {"tracks": [{"id": "DRUMS-UUID", "name": "Drums"}]}
        calls = _schema_to_tool_calls(schema, project_state=project_state)
        track_calls = [tc for tc in calls if tc.name == "stori_add_midi_track"]
        assert len(track_calls) == 1
        assert track_calls[0].params["name"] == "Bass"

    # -- Bug 2: Existing track UUIDs propagated to regions/generators -----------

    def test_existing_track_uuid_in_region_calls(self) -> None:

        """Region calls should carry the existing track's UUID as trackId."""
        schema = self._make_schema()
        project_state: ProjectContext = {
            "tracks": [
                {"id": "DRUMS-UUID-123", "name": "Drums"},
                {"id": "BASS-UUID-456", "name": "Bass"},
            ]
        }
        calls = _schema_to_tool_calls(schema, project_state=project_state)
        region_calls = [tc for tc in calls if tc.name == "stori_add_midi_region"]
        assert len(region_calls) == 2
        region_track_ids = {tc.params.get("trackId") for tc in region_calls}
        assert "DRUMS-UUID-123" in region_track_ids
        assert "BASS-UUID-456" in region_track_ids

    def test_existing_track_uuid_in_generator_calls(self) -> None:

        """Generator calls should carry the existing track's UUID as trackId."""
        schema = self._make_schema()
        project_state: ProjectContext = {
            "tracks": [
                {"id": "DRUMS-UUID-123", "name": "Drums"},
                {"id": "BASS-UUID-456", "name": "Bass"},
            ]
        }
        calls = _schema_to_tool_calls(schema, project_state=project_state)
        gen_calls = [tc for tc in calls if tc.name == "stori_generate_midi"]
        assert len(gen_calls) == 2
        gen_track_ids = {tc.params.get("trackId") for tc in gen_calls}
        assert "DRUMS-UUID-123" in gen_track_ids
        assert "BASS-UUID-456" in gen_track_ids

    # -- Bug 3: Melody role mapped to existing Organ track ----------------------

    def test_melody_role_maps_to_existing_organ_track(self) -> None:

        """When project has an Organ track and plan generates 'melody', use Organ."""
        from app.core.plan_schemas import ExecutionPlanSchema, GenerationStep, EditStep
        schema = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="house", tempo=128, bars=8),
                GenerationStep(role="bass", style="house", tempo=128, bars=8),
                GenerationStep(role="melody", style="house", tempo=128, bars=8, key="Am"),
            ],
            edits=[
                EditStep(action="add_track", name="Drums"),
                EditStep(action="add_track", name="Bass"),
                EditStep(action="add_track", name="Melody"),
                EditStep(action="add_region", track="Drums", barStart=0, bars=8),
                EditStep(action="add_region", track="Bass", barStart=0, bars=8),
                EditStep(action="add_region", track="Melody", barStart=0, bars=8),
            ],
            mix=[],
        )
        project_state: ProjectContext = {
            "tracks": [
                {"id": "DRUMS-UUID", "name": "Drums"},
                {"id": "BASS-UUID", "name": "Bass"},
                {"id": "ORGAN-UUID", "name": "Organ", "gmProgram": 16},
            ]
        }
        calls = _schema_to_tool_calls(schema, project_state=project_state)
        # Should NOT create a Melody track (Organ exists for melody role)
        track_calls = [tc for tc in calls if tc.name == "stori_add_midi_track"]
        track_names = [tc.params["name"] for tc in track_calls]
        assert "Melody" not in track_names, "Should not create Melody track when Organ exists"

        # Generator for melody should target the Organ track
        gen_calls = [tc for tc in calls if tc.name == "stori_generate_midi" and tc.params["role"] == "melody"]
        assert len(gen_calls) == 1
        assert gen_calls[0].params["trackName"] == "Organ"
        assert gen_calls[0].params.get("trackId") == "ORGAN-UUID"


# ===========================================================================
# 3. build_execution_plan — mocked LLM
# ===========================================================================

class TestBuildExecutionPlanMocked:
    """build_execution_plan with a mocked LLM client."""

    @pytest.mark.anyio
    async def test_happy_path_returns_valid_plan(self) -> None:

        llm = _llm_with_response(_valid_plan_json())
        plan = await build_execution_plan(
            user_prompt="make a house beat",
            project_state={},
            route=_make_route(),
            llm=llm,
        )
        assert isinstance(plan, ExecutionPlan)
        assert plan.is_valid
        assert len(plan.tool_calls) > 0

    @pytest.mark.anyio
    async def test_llm_called_without_tools(self) -> None:

        """Planner sends tools=[] to avoid the LLM invoking functions."""
        llm = _llm_with_response(_valid_plan_json())
        await build_execution_plan(
            user_prompt="make a beat",
            project_state={},
            route=_make_route(),
            llm=llm,
        )
        call_kwargs = llm.chat.call_args.kwargs
        assert call_kwargs.get("tools") == []
        assert call_kwargs.get("tool_choice") == "none"

    @pytest.mark.anyio
    async def test_invalid_json_returns_invalid_plan(self) -> None:

        llm = AsyncMock()
        llm.chat.return_value = MagicMock(content="not json at all { garbage")
        plan = await build_execution_plan(
            user_prompt="make a beat",
            project_state={},
            route=_make_route(),
            llm=llm,
        )
        assert isinstance(plan, ExecutionPlan)
        assert not plan.is_valid
        assert any("validation" in n.lower() or "plan" in n.lower() for n in plan.notes)

    @pytest.mark.anyio
    async def test_empty_plan_returns_invalid(self) -> None:

        """A plan with no generations, edits, or mix is considered empty."""
        llm = _llm_with_response({"generations": [], "edits": [], "mix": []})
        plan = await build_execution_plan(
            user_prompt="make a beat",
            project_state={},
            route=_make_route(),
            llm=llm,
        )
        assert not plan.is_valid

    @pytest.mark.anyio
    async def test_structured_prompt_takes_deterministic_path(self) -> None:

        """A fully-specified structured prompt skips the LLM entirely."""
        parsed = _minimal_parsed()
        llm = AsyncMock()
        plan = await build_execution_plan(
            user_prompt="MAESTRO PROMPT\nMode: compose\n...",
            project_state={},
            route=_make_route(),
            llm=llm,
            parsed=parsed,
        )
        # LLM should NOT have been called
        llm.chat.assert_not_awaited()
        assert plan is not None
        assert plan.is_valid

    @pytest.mark.anyio
    async def test_partial_structured_prompt_calls_llm(self) -> None:

        """A structured prompt missing bars falls back to the LLM."""
        parsed = _minimal_parsed()
        parsed.constraints = {}  # missing bars → deterministic path impossible
        llm = _llm_with_response(_valid_plan_json())
        plan = await build_execution_plan(
            user_prompt="MAESTRO PROMPT\nMode: compose\n...",
            project_state={},
            route=_make_route(),
            llm=llm,
            parsed=parsed,
        )
        llm.chat.assert_awaited_once()
        assert plan is not None

    @pytest.mark.anyio
    async def test_position_resolved_before_llm_call(self) -> None:

        """Position: after intro is resolved to a beat and injected into system prompt."""
        parsed = _minimal_parsed()
        parsed.position = PositionSpec(kind="after", ref="intro")
        parsed.constraints = {}  # force LLM path
        project_with_intro: ProjectContext = {
            "tracks": [
                {"name": "intro", "regions": [
                    {"name": "intro", "startBeat": 0, "durationBeats": 64}
                ]}
            ]
        }
        llm = _llm_with_response(_valid_plan_json())
        await build_execution_plan(
            user_prompt="verse",
            project_state=project_with_intro,
            route=_make_route(),
            llm=llm,
            parsed=parsed,
        )
        # System prompt should contain beat offset information
        call_kwargs = llm.chat.call_args.kwargs
        system_prompt = call_kwargs.get("system", "")
        assert "64" in system_prompt or "ARRANGEMENT POSITION" in system_prompt

    @pytest.mark.anyio
    async def test_llm_response_stored_in_plan(self) -> None:

        """LLM response text is preserved in the plan for debugging."""
        plan_json = _valid_plan_json()
        llm = _llm_with_response(plan_json)
        plan = await build_execution_plan(
            user_prompt="make a beat",
            project_state={},
            route=_make_route(),
            llm=llm,
        )
        assert plan.llm_response_text is not None
        assert len(plan.llm_response_text) > 0

    @pytest.mark.anyio
    async def test_generations_only_plan_valid(self) -> None:

        """A plan with generations but no explicit edits is still valid (complete_plan infers edits)."""
        drums_only: GenerationStepDict = {"role": "drums", "style": "house", "tempo": 128, "bars": 8}
        plan_json: PlanJsonDict = {"generations": [drums_only], "edits": [], "mix": []}
        llm = _llm_with_response(plan_json)
        plan = await build_execution_plan(
            user_prompt="make a beat",
            project_state={},
            route=_make_route(),
            llm=llm,
        )
        assert plan.is_valid
        track_calls = [tc for tc in plan.tool_calls if tc.name == "stori_add_midi_track"]
        assert len(track_calls) >= 1  # complete_plan inferred a track


# ===========================================================================
# 4. build_plan_from_dict
# ===========================================================================

class TestBuildPlanFromDict:
    """build_plan_from_dict converts a dict directly to ExecutionPlan."""

    def test_valid_dict_produces_valid_plan(self) -> None:

        plan = build_plan_from_dict(_valid_plan_json())
        assert plan.is_valid
        assert len(plan.tool_calls) > 0

    @typing.no_type_check
    def test_invalid_dict_produces_invalid_plan(self) -> None:
        """build_plan_from_dict handles runtime-invalid dicts gracefully.

        Passes a dict that does not conform to PlanJsonDict to verify Pydantic
        validation rejects it at runtime without crashing.  The @no_type_check
        decorator acknowledges this is a deliberate type violation.
        """
        plan = build_plan_from_dict({"not_a_real_field": True})
        assert not plan.is_valid

    def test_plan_from_dict_has_tool_calls_in_order(self) -> None:

        plan = build_plan_from_dict(_valid_plan_json())
        names = [tc.name for tc in plan.tool_calls]
        assert "stori_add_midi_track" in names
        assert "stori_add_midi_region" in names
        assert "stori_generate_midi" in names

    def test_empty_plan_not_valid(self) -> None:

        plan = build_plan_from_dict({"generations": [], "edits": [], "mix": []})
        assert not plan.is_valid

    def test_notes_field_present(self) -> None:

        plan = build_plan_from_dict(_valid_plan_json())
        assert isinstance(plan.notes, list)


# ===========================================================================
# 5. ExecutionPlan properties
# ===========================================================================

class TestExecutionPlanProperties:
    """ExecutionPlan.is_valid, generation_count, edit_count."""

    def _plan_with_calls(self, calls: list[ToolCall], safety: bool = True) -> ExecutionPlan:

        return ExecutionPlan(tool_calls=calls, safety_validated=safety)

    def test_is_valid_requires_safety_and_calls(self) -> None:

        calls = [ToolCall(name="stori_generate_midi", params={})]
        assert self._plan_with_calls(calls, safety=True).is_valid
        assert not self._plan_with_calls(calls, safety=False).is_valid
        assert not self._plan_with_calls([], safety=True).is_valid

    def test_generation_count(self) -> None:

        calls = [
            ToolCall(name="stori_generate_midi", params={}),
            ToolCall(name="stori_generate_midi", params={}),
            ToolCall(name="stori_add_midi_track", params={}),
        ]
        plan = self._plan_with_calls(calls)
        assert plan.generation_count == 2

    def test_edit_count(self) -> None:

        calls = [
            ToolCall(name="stori_add_midi_track", params={}),
            ToolCall(name="stori_add_midi_region", params={}),
            ToolCall(name="stori_generate_midi", params={}),
        ]
        plan = self._plan_with_calls(calls)
        assert plan.edit_count == 2

    def test_to_dict_includes_tool_calls(self) -> None:

        calls = [ToolCall(name="stori_add_midi_track", params={"name": "Drums"})]
        plan = self._plan_with_calls(calls, safety=True)
        d = plan.to_dict()
        assert "tool_calls" in d
        assert len(d["tool_calls"]) == 1

    def test_empty_plan_is_not_valid(self) -> None:

        plan = ExecutionPlan()
        assert not plan.is_valid


# ===========================================================================
# 6. Position: → startBeat regression (full path)
# ===========================================================================

class TestPositionToBeatRegressionFull:
    """
    End-to-end regression: parse → resolve → deterministic plan → tool calls.
    No LLM involved. Tests the exact chain that was broken.
    """

    def test_after_intro_offsets_all_regions(self) -> None:

        """
        Parsing 'Position: after intro' and building a deterministic plan
        produces region tool calls with startBeat >= 64 (4 bars × 4 beats × 4 = 64 beats).
        """
        from app.core.prompts import resolve_position

        prompt = (
            "MAESTRO PROMPT\n"
            "Mode: compose\n"
            "Style: house\n"
            "Tempo: 128\n"
            "Key: Am\n"
            "Section: verse\n"
            "Position: after intro\n"
            "Role:\n"
            "- kick\n"
            "- bass\n"
            "Constraints:\n"
            "  bars: 8\n"
            "Request: lay down the verse groove"
        )
        parsed = parse_prompt(prompt)
        assert parsed is not None
        assert parsed.position is not None

        project: ProjectContext = {
            "tracks": [
                {"name": "intro", "regions": [
                    {"name": "intro", "startBeat": 0, "durationBeats": 64}
                ]}
            ]
        }
        beat = resolve_position(parsed.position, project)
        assert beat == 64.0

        plan = _try_deterministic_plan(parsed, start_beat=beat)
        assert plan is not None

        region_calls = [tc for tc in plan.tool_calls if tc.name == "stori_add_midi_region"]
        assert len(region_calls) > 0
        for call in region_calls:
            _sb = call.params["startBeat"]
            sb = _sb if isinstance(_sb, (int, float)) else 0
            assert sb >= 64.0, f"startBeat={_sb} not offset. Bug: offset not applied."

    def test_no_position_regions_start_at_zero(self) -> None:

        """Without Position:, regions default to startBeat=0."""
        prompt = (
            "MAESTRO PROMPT\n"
            "Mode: compose\n"
            "Style: house\n"
            "Tempo: 128\n"
            "Key: Am\n"
            "Role:\n"
            "- kick\n"
            "Constraints:\n"
            "  bars: 4\n"
            "Request: basic kick"
        )
        parsed = parse_prompt(prompt)
        assert parsed is not None
        assert parsed.position is None

        plan = _try_deterministic_plan(parsed, start_beat=0.0)
        assert plan is not None

        for tc in plan.tool_calls:
            if tc.name == "stori_add_midi_region":
                assert tc.params.get("startBeat", 0) == 0.0


# ===========================================================================
# 7. build_execution_plan_stream
# ===========================================================================

class TestBuildExecutionPlanStream:
    """Streaming variant of build_execution_plan."""

    @pytest.mark.asyncio
    async def test_deterministic_path_yields_plan_no_reasoning(self) -> None:

        """Deterministic fast-path yields an ExecutionPlan with no reasoning SSE."""
        parsed = _minimal_parsed(roles=["drums", "bass"])
        route = _make_route()
        llm = AsyncMock()

        items: list[ExecutionPlan | str] = []
        async for item in build_execution_plan_stream(
            "make a beat", {}, route, llm, parsed=parsed,
        ):
            items.append(item)

        assert len(items) == 1
        plan = items[0]
        assert isinstance(plan, ExecutionPlan)
        assert plan.is_valid
        llm.chat_completion_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_path_yields_reasoning_then_plan(self) -> None:

        """LLM path yields reasoning SSE events then the ExecutionPlan."""
        route = _make_route()

        async def _fake_stream(**kwargs: object) -> AsyncGenerator[dict[str, object], None]:
            yield {"type": "reasoning_delta", "text": "Thinking about drums..."}
            yield {"type": "reasoning_delta", "text": " and bass."}
            yield {
                "type": "done",
                "content": json.dumps(_valid_plan_json()),
                "tool_calls": [],
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }

        llm = AsyncMock()
        llm.chat_completion_stream = MagicMock(return_value=_fake_stream())

        reasoning_events: list[str] = []
        plan_result: ExecutionPlan | None = None

        async def mock_emit_sse(data: JSONObject) -> str:
            if data.get("type") == "reasoning":
                reasoning_events.append(str(data["content"]))
            return f"data: {json.dumps(data)}\n\n"

        async for item in build_execution_plan_stream(
            "make a house beat", {}, route, llm, emit_sse=mock_emit_sse,
        ):
            if isinstance(item, ExecutionPlan):
                plan_result = item

        assert plan_result is not None
        assert plan_result.is_valid
        assert len(reasoning_events) >= 1

    @pytest.mark.asyncio
    async def test_usage_tracker_updated_on_stream(self) -> None:

        """usage_tracker is updated with prompt/completion tokens from the stream."""
        from app.core.maestro_handlers import UsageTracker

        route = _make_route()

        async def _fake_stream(**kwargs: object) -> AsyncGenerator[dict[str, object], None]:
            yield {
                "type": "done",
                "content": json.dumps(_valid_plan_json()),
                "tool_calls": [],
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 200, "completion_tokens": 60},
            }

        llm = AsyncMock()
        llm.chat_completion_stream = MagicMock(return_value=_fake_stream())

        tracker = UsageTracker()
        async for _ in build_execution_plan_stream(
            "make a beat", {}, route, llm, usage_tracker=tracker,
        ):
            pass

        assert tracker.prompt_tokens == 200
        assert tracker.completion_tokens == 60
        assert tracker.last_input_tokens == 200

    @pytest.mark.asyncio
    async def test_invalid_json_returns_failed_plan(self) -> None:

        """When LLM returns non-JSON content, streaming path returns a failed plan."""
        route = _make_route()

        async def _fake_stream(**kwargs: object) -> AsyncGenerator[dict[str, object], None]:
            yield {"type": "content_delta", "text": "No JSON here at all."}
            yield {
                "type": "done",
                "content": None,
                "tool_calls": [],
                "finish_reason": "stop",
                "usage": {},
            }

        llm = AsyncMock()
        llm.chat_completion_stream = MagicMock(return_value=_fake_stream())

        plans = []
        async for item in build_execution_plan_stream("bad prompt", {}, route, llm):
            if isinstance(item, ExecutionPlan):
                plans.append(item)

        assert len(plans) == 1
        assert not plans[0].is_valid


# =============================================================================
# Effects inference — _infer_mix_steps and deterministic plan integration
# =============================================================================

class TestInferMixSteps:
    """Tests for _infer_mix_steps style→effects inference."""

    def setup_method(self) -> None:

        from app.core.planner import _infer_mix_steps
        self._infer = _infer_mix_steps

    def test_drums_always_get_compressor(self) -> None:

        """Drums role always receives a compressor insert regardless of style."""
        steps = self._infer("house", ["drums"])
        inserts = [s for s in steps if s.action == "add_insert" and s.track == "Drums"]
        types = {s.type for s in inserts}
        assert "compressor" in types

    def test_pads_always_get_reverb_send(self) -> None:

        """Pads role always receives a reverb bus send."""
        steps = self._infer("ambient", ["pads"])
        sends = [s for s in steps if s.action == "add_send" and s.track == "Pads"]
        assert any(s.bus == "Reverb" for s in sends)

    def test_rock_lead_gets_distortion(self) -> None:

        """Rock style adds distortion to lead track."""
        steps = self._infer("progressive rock", ["lead"])
        inserts = {s.type for s in steps if s.action == "add_insert" and s.track == "Lead"}
        assert "distortion" in inserts

    def test_lofi_drums_get_filter(self) -> None:

        """Lo-fi style adds filter to drums."""
        steps = self._infer("lo-fi hip hop", ["drums"])
        inserts = {s.type for s in steps if s.action == "add_insert" and s.track == "Drums"}
        assert "filter" in inserts

    def test_reverb_goes_to_bus_not_insert(self) -> None:

        """Reverb is routed via add_send, not add_insert."""
        steps = self._infer("ambient", ["pads", "melody"])
        reverb_inserts = [s for s in steps if s.action == "add_insert" and s.type == "reverb"]
        reverb_sends = [s for s in steps if s.action == "add_send" and s.bus == "Reverb"]
        assert len(reverb_inserts) == 0
        assert len(reverb_sends) >= 1

    def test_no_effects_empty_roles(self) -> None:

        """Empty role list returns no effects."""
        steps = self._infer("jazz", [])
        assert steps == []

    def test_multiple_roles_covered(self) -> None:

        """Multi-role request covers drums and bass effects."""
        steps = self._infer("house", ["drums", "bass"])
        drum_inserts = {s.type for s in steps if s.action == "add_insert" and s.track == "Drums"}
        bass_inserts = {s.type for s in steps if s.action == "add_insert" and s.track == "Bass"}
        assert "compressor" in drum_inserts
        assert "compressor" in bass_inserts

    def test_jazz_chords_get_reverb(self) -> None:

        """Jazz style adds reverb to chords."""
        steps = self._infer("jazz", ["chords"])
        sends = [s for s in steps if s.action == "add_send" and s.track == "Chords"]
        assert any(s.bus == "Reverb" for s in sends)

    def test_shoegaze_lead_heavy_effects(self) -> None:

        """Shoegaze style adds reverb, chorus, and distortion to lead."""
        steps = self._infer("shoegaze", ["lead"])
        inserts = {s.type for s in steps if s.action == "add_insert" and s.track == "Lead"}
        assert "distortion" in inserts
        assert "chorus" in inserts


class TestDeterministicPlanEffects:
    """Tests that plan_from_parsed_prompt includes inferred effects."""

    def test_deterministic_plan_includes_effects(self) -> None:

        """A deterministic plan should include mix steps (insert/send) for appropriate styles."""
        parsed = _minimal_parsed(style="progressive rock", roles=["drums", "pads", "lead"])
        plan = _try_deterministic_plan(parsed)
        assert plan is not None
        effect_tools = {tc.name for tc in plan.tool_calls}
        # Should have at least one effect or bus tool
        assert effect_tools & {"stori_add_insert_effect", "stori_ensure_bus", "stori_add_send"}

    def test_reverb_bus_created_before_sends(self) -> None:

        """stori_ensure_bus must appear before any stori_add_send in the tool call list."""
        parsed = _minimal_parsed(style="ambient", roles=["pads", "melody"])
        plan = _try_deterministic_plan(parsed)
        assert plan is not None
        names = [tc.name for tc in plan.tool_calls]
        if "stori_add_send" in names and "stori_ensure_bus" in names:
            ensure_idx = names.index("stori_ensure_bus")
            send_idx = names.index("stori_add_send")
            assert ensure_idx < send_idx, "stori_ensure_bus must precede stori_add_send"

    def test_no_effects_constraint_skips_effects(self) -> None:

        """Constraint no_effects=true suppresses effect inference."""
        parsed = _minimal_parsed(
            style="house",
            roles=["drums", "bass"],
        )
        parsed.constraints["no_effects"] = True
        plan = _try_deterministic_plan(parsed)
        assert plan is not None
        effect_tools = [tc.name for tc in plan.tool_calls if tc.name in {
            "stori_add_insert_effect", "stori_ensure_bus", "stori_add_send"
        }]
        assert len(effect_tools) == 0

    def test_effects_come_after_own_generator_per_track(self) -> None:

        """Within each track group, effects appear after the generator."""
        parsed = _minimal_parsed(style="jazz", roles=["drums", "chords"])
        plan = _try_deterministic_plan(parsed)
        assert plan is not None
        calls = plan.tool_calls

        # Group calls by track and verify per-track ordering
        for track_name in ("Drums", "Chords"):
            gen_indices = [
                i for i, tc in enumerate(calls)
                if tc.name == "stori_generate_midi"
                and isinstance((_tn := tc.params.get("trackName")), str)
                and _tn.lower() == track_name.lower()
            ]
            fx_indices = [
                i for i, tc in enumerate(calls)
                if tc.name == "stori_add_insert_effect"
                and isinstance((_tn := tc.params.get("trackName")), str)
                and _tn.lower() == track_name.lower()
            ]
            if gen_indices and fx_indices:
                assert min(fx_indices) > max(gen_indices), (
                    f"{track_name}: effects should come after generator"
                )


class TestSchemaToToolCallsBusOrdering:
    """stori_ensure_bus must precede stori_add_send in _schema_to_tool_calls output."""

    def test_ensure_bus_before_send(self) -> None:

        """When mix has add_send, stori_ensure_bus is inserted before the first send."""
        from app.core.plan_schemas import ExecutionPlanSchema, GenerationStep, EditStep, MixStep
        schema = ExecutionPlanSchema(
            generations=[GenerationStep(role="pads", style="ambient", tempo=80, bars=8)],
            edits=[
                EditStep(action="add_track", name="Pads"),
                EditStep(action="add_region", track="Pads", barStart=0, bars=8),
            ],
            mix=[
                MixStep(action="add_send", track="Pads", bus="Reverb"),
            ],
        )
        calls = _schema_to_tool_calls(schema)
        names = [tc.name for tc in calls]
        assert "stori_ensure_bus" in names
        assert "stori_add_send" in names
        ensure_idx = names.index("stori_ensure_bus")
        send_idx = names.index("stori_add_send")
        assert ensure_idx < send_idx

    def test_bus_ensured_only_once_for_multiple_sends(self) -> None:

        """Same bus name produces only one stori_ensure_bus even with multiple sends."""
        from app.core.plan_schemas import ExecutionPlanSchema, GenerationStep, EditStep, MixStep
        schema = ExecutionPlanSchema(
            generations=[GenerationStep(role="pads", style="ambient", tempo=80, bars=8)],
            edits=[
                EditStep(action="add_track", name="Pads"),
                EditStep(action="add_track", name="Melody"),
                EditStep(action="add_region", track="Pads", barStart=0, bars=8),
                EditStep(action="add_region", track="Melody", barStart=0, bars=8),
            ],
            mix=[
                MixStep(action="add_send", track="Pads", bus="Reverb"),
                MixStep(action="add_send", track="Melody", bus="Reverb"),
            ],
        )
        calls = _schema_to_tool_calls(schema)
        bus_ensures = [tc for tc in calls if tc.name == "stori_ensure_bus"]
        assert len(bus_ensures) == 1


class TestSchemaToToolCallsTrackContiguous:
    """Tool calls are grouped contiguously by track for timeline rendering."""

    def test_track_calls_contiguous(self) -> None:

        """Each track's tool calls appear as a contiguous block."""
        from app.core.plan_schemas import ExecutionPlanSchema, GenerationStep, EditStep, MixStep
        schema = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="funk", tempo=100, bars=8),
                GenerationStep(role="bass", style="funk", tempo=100, bars=8),
            ],
            edits=[
                EditStep(action="add_track", name="Drums"),
                EditStep(action="add_track", name="Bass"),
                EditStep(action="add_region", track="Drums", barStart=0, bars=8),
                EditStep(action="add_region", track="Bass", barStart=0, bars=8),
            ],
            mix=[
                MixStep(action="add_insert", track="Drums", type="compressor"),
                MixStep(action="add_insert", track="Bass", type="compressor"),
            ],
        )
        calls = _schema_to_tool_calls(schema)

        # Extract track association for each call
        track_sequence: list[str] = []
        for tc in calls:
            _tn = tc.params.get("trackName")
            _nm = tc.params.get("name")
            _raw = (_tn if isinstance(_tn, str) else None) or (_nm if isinstance(_nm, str) else None) or ""
            if _raw:
                track_sequence.append(_raw.lower())

        # Find index ranges for each track
        drums_indices = [i for i, t in enumerate(track_sequence) if t == "drums"]
        bass_indices = [i for i, t in enumerate(track_sequence) if t == "bass"]

        # Each track's calls should be contiguous (no interleaving)
        if drums_indices and bass_indices:
            assert max(drums_indices) < min(bass_indices) or max(bass_indices) < min(drums_indices), (
                f"Track calls are interleaved: drums={drums_indices}, bass={bass_indices}"
            )

    def test_track_group_internal_order(self) -> None:

        """Within a track group: create → styling → region → generate → effects."""
        from app.core.plan_schemas import ExecutionPlanSchema, GenerationStep, EditStep, MixStep
        schema = ExecutionPlanSchema(
            generations=[GenerationStep(role="drums", style="funk", tempo=100, bars=8)],
            edits=[
                EditStep(action="add_track", name="Drums"),
                EditStep(action="add_region", track="Drums", barStart=0, bars=8),
            ],
            mix=[MixStep(action="add_insert", track="Drums", type="compressor")],
        )
        calls = _schema_to_tool_calls(schema)
        names = [tc.name for tc in calls]

        track_idx = names.index("stori_add_midi_track")
        region_idx = names.index("stori_add_midi_region")
        gen_idx = names.index("stori_generate_midi")
        effect_idx = names.index("stori_add_insert_effect")

        assert track_idx < region_idx < gen_idx < effect_idx
