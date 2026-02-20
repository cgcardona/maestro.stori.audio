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

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.planner import (
    ExecutionPlan,
    _try_deterministic_plan,
    _schema_to_tool_calls,
    build_execution_plan,
    build_execution_plan_stream,
    build_plan_from_dict,
)
from app.core.prompt_parser import parse_prompt, ParsedPrompt, PositionSpec
from app.core.intent import IntentResult, Intent, SSEState
from app.core.expansion import ToolCall


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
    roles: list | None = None,
    bars: int = 8,
    position: PositionSpec | None = None,
) -> ParsedPrompt:
    """Build a ParsedPrompt directly for deterministic-plan tests."""
    from app.core.prompt_parser import ParsedPrompt
    return ParsedPrompt(
        mode="compose",
        request="make a beat",
        style=style,
        tempo=tempo,
        key=key,
        roles=["kick", "bass"] if roles is None else roles,
        constraints={"bars": bars},
        position=position,
        raw="STORI PROMPT\n...",
    )


def _llm_with_response(json_body: dict) -> AsyncMock:
    """Return a mock LLM that yields the given JSON as its chat response."""
    llm = AsyncMock()
    response_text = json.dumps(json_body)
    llm.chat.return_value = MagicMock(content=response_text)
    return llm


def _valid_plan_json(bars: int = 8, tempo: int = 128) -> dict:
    return {
        "generations": [
            {"role": "drums", "style": "house", "tempo": tempo, "bars": bars},
            {"role": "bass",  "style": "house", "tempo": tempo, "bars": bars, "key": "Am"},
        ],
        "edits": [
            {"action": "add_track", "name": "Drums"},
            {"action": "add_track", "name": "Bass"},
            {"action": "add_region", "track": "Drums", "barStart": 0, "bars": bars},
            {"action": "add_region", "track": "Bass",  "barStart": 0, "bars": bars},
        ],
        "mix": [],
    }


# ===========================================================================
# 1. _try_deterministic_plan
# ===========================================================================

class TestTryDeterministicPlan:
    """_try_deterministic_plan builds a plan without the LLM when all fields are present."""

    def test_returns_plan_when_all_fields_present(self):
        parsed = _minimal_parsed()
        plan = _try_deterministic_plan(parsed)
        assert plan is not None
        assert isinstance(plan, ExecutionPlan)

    def test_returns_none_when_style_missing(self):
        parsed = _minimal_parsed()
        parsed.style = None
        assert _try_deterministic_plan(parsed) is None

    def test_returns_none_when_tempo_missing(self):
        parsed = _minimal_parsed()
        parsed.tempo = None
        assert _try_deterministic_plan(parsed) is None

    def test_returns_none_when_roles_empty(self):
        parsed = _minimal_parsed(roles=[])
        assert _try_deterministic_plan(parsed) is None

    def test_returns_none_when_bars_missing_from_constraints(self):
        parsed = _minimal_parsed()
        parsed.constraints = {}
        assert _try_deterministic_plan(parsed) is None

    def test_returns_none_when_bars_is_zero(self):
        parsed = _minimal_parsed(bars=0)
        assert _try_deterministic_plan(parsed) is None

    def test_plan_has_generate_calls_for_each_role(self):
        parsed = _minimal_parsed(roles=["kick", "bass", "melody"])
        plan = _try_deterministic_plan(parsed)
        assert plan is not None
        gen_calls = [tc for tc in plan.tool_calls if tc.name == "stori_generate_midi"]
        assert len(gen_calls) == 3

    def test_plan_is_safety_validated(self):
        plan = _try_deterministic_plan(_minimal_parsed())
        assert plan is not None
        assert plan.safety_validated is True

    def test_deterministic_plan_note_mentions_structured_prompt(self):
        plan = _try_deterministic_plan(_minimal_parsed())
        assert plan is not None
        assert any("structured prompt" in note.lower() or "deterministic" in note.lower()
                   for note in plan.notes)

    def test_start_beat_zero_by_default(self):
        parsed = _minimal_parsed()
        plan = _try_deterministic_plan(parsed, start_beat=0.0)
        assert plan is not None
        region_calls = [tc for tc in plan.tool_calls if tc.name == "stori_add_midi_region"]
        for call in region_calls:
            assert call.params.get("startBeat", 0) == 0

    def test_start_beat_offset_applied_to_regions(self):
        """Regression: Position: after intro should shift all region startBeats by 64."""
        parsed = _minimal_parsed()
        plan = _try_deterministic_plan(parsed, start_beat=64.0)
        assert plan is not None
        region_calls = [tc for tc in plan.tool_calls if tc.name == "stori_add_midi_region"]
        assert len(region_calls) > 0
        for call in region_calls:
            start = call.params.get("startBeat", 0)
            assert start >= 64.0, (
                f"startBeat={start} should be >=64 when start_beat=64 is passed. "
                "Position offset was not applied."
            )

    def test_start_beat_offset_in_plan_notes(self):
        """Non-zero start_beat is recorded in plan notes for traceability."""
        plan = _try_deterministic_plan(_minimal_parsed(), start_beat=32.0)
        assert plan is not None
        notes_text = " ".join(plan.notes)
        assert "32" in notes_text or "position_offset" in notes_text

    def test_tempo_from_parsed_in_generate_calls(self):
        parsed = _minimal_parsed(tempo=140)
        plan = _try_deterministic_plan(parsed)
        assert plan is not None
        for tc in plan.tool_calls:
            if tc.name == "stori_generate_midi":
                assert tc.params.get("tempo") == 140

    def test_style_from_parsed_in_generate_calls(self):
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

    def test_tracks_before_regions_before_generators(self):
        """Execution order: add_track → add_region → generate_midi."""
        schema = self._make_schema()
        calls = _schema_to_tool_calls(schema)
        names = [tc.name for tc in calls]
        # Find first occurrence of each type
        first_track = next(i for i, n in enumerate(names) if n == "stori_add_midi_track")
        first_region = next(i for i, n in enumerate(names) if n == "stori_add_midi_region")
        first_gen = next(i for i, n in enumerate(names) if n == "stori_generate_midi")
        assert first_track < first_region < first_gen

    def test_region_start_offset_zero(self):
        schema = self._make_schema()
        calls = _schema_to_tool_calls(schema, region_start_offset=0.0)
        for tc in calls:
            if tc.name == "stori_add_midi_region":
                assert tc.params["startBeat"] == 0.0

    def test_region_start_offset_applied(self):
        """All new regions are shifted by region_start_offset beats."""
        schema = self._make_schema(bars=8)
        calls = _schema_to_tool_calls(schema, region_start_offset=64.0)
        for tc in calls:
            if tc.name == "stori_add_midi_region":
                assert tc.params["startBeat"] >= 64.0, (
                    f"startBeat {tc.params['startBeat']} not offset by 64"
                )

    def test_generates_two_track_calls(self):
        schema = self._make_schema()
        calls = _schema_to_tool_calls(schema)
        track_calls = [tc for tc in calls if tc.name == "stori_add_midi_track"]
        assert len(track_calls) == 2

    def test_generates_two_region_calls(self):
        schema = self._make_schema()
        calls = _schema_to_tool_calls(schema)
        region_calls = [tc for tc in calls if tc.name == "stori_add_midi_region"]
        assert len(region_calls) == 2

    def test_generates_two_generation_calls(self):
        schema = self._make_schema()
        calls = _schema_to_tool_calls(schema)
        gen_calls = [tc for tc in calls if tc.name == "stori_generate_midi"]
        assert len(gen_calls) == 2

    def test_region_duration_from_bars(self):
        """8 bars × 4 beats/bar = 32 durationBeats."""
        schema = self._make_schema(bars=8)
        calls = _schema_to_tool_calls(schema)
        for tc in calls:
            if tc.name == "stori_add_midi_region":
                assert tc.params["durationBeats"] == 32

    def test_generate_calls_include_role(self):
        schema = self._make_schema()
        calls = _schema_to_tool_calls(schema)
        roles_generated = {
            tc.params["role"]
            for tc in calls
            if tc.name == "stori_generate_midi"
        }
        assert "drums" in roles_generated
        assert "bass" in roles_generated

    def test_mix_effects_added_last(self):
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


# ===========================================================================
# 3. build_execution_plan — mocked LLM
# ===========================================================================

class TestBuildExecutionPlanMocked:
    """build_execution_plan with a mocked LLM client."""

    @pytest.mark.anyio
    async def test_happy_path_returns_valid_plan(self):
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
    async def test_llm_called_without_tools(self):
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
    async def test_invalid_json_returns_invalid_plan(self):
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
    async def test_empty_plan_returns_invalid(self):
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
    async def test_structured_prompt_takes_deterministic_path(self):
        """A fully-specified structured prompt skips the LLM entirely."""
        parsed = _minimal_parsed()
        llm = AsyncMock()
        plan = await build_execution_plan(
            user_prompt="STORI PROMPT\nMode: compose\n...",
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
    async def test_partial_structured_prompt_calls_llm(self):
        """A structured prompt missing bars falls back to the LLM."""
        parsed = _minimal_parsed()
        parsed.constraints = {}  # missing bars → deterministic path impossible
        llm = _llm_with_response(_valid_plan_json())
        plan = await build_execution_plan(
            user_prompt="STORI PROMPT\nMode: compose\n...",
            project_state={},
            route=_make_route(),
            llm=llm,
            parsed=parsed,
        )
        llm.chat.assert_awaited_once()
        assert plan is not None

    @pytest.mark.anyio
    async def test_position_resolved_before_llm_call(self):
        """Position: after intro is resolved to a beat and injected into system prompt."""
        parsed = _minimal_parsed()
        parsed.position = PositionSpec(kind="after", ref="intro")
        parsed.constraints = {}  # force LLM path
        project_with_intro = {
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
    async def test_llm_response_stored_in_plan(self):
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
    async def test_generations_only_plan_valid(self):
        """A plan with generations but no explicit edits is still valid (complete_plan infers edits)."""
        plan_json = {
            "generations": [
                {"role": "drums", "style": "house", "tempo": 128, "bars": 8}
            ],
            "edits": [],
            "mix": [],
        }
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

    def test_valid_dict_produces_valid_plan(self):
        plan = build_plan_from_dict(_valid_plan_json())
        assert plan.is_valid
        assert len(plan.tool_calls) > 0

    def test_invalid_dict_produces_invalid_plan(self):
        plan = build_plan_from_dict({"not_a_real_field": True})
        assert not plan.is_valid

    def test_plan_from_dict_has_tool_calls_in_order(self):
        plan = build_plan_from_dict(_valid_plan_json())
        names = [tc.name for tc in plan.tool_calls]
        assert "stori_add_midi_track" in names
        assert "stori_add_midi_region" in names
        assert "stori_generate_midi" in names

    def test_empty_plan_not_valid(self):
        plan = build_plan_from_dict({"generations": [], "edits": [], "mix": []})
        assert not plan.is_valid

    def test_notes_field_present(self):
        plan = build_plan_from_dict(_valid_plan_json())
        assert isinstance(plan.notes, list)


# ===========================================================================
# 5. ExecutionPlan properties
# ===========================================================================

class TestExecutionPlanProperties:
    """ExecutionPlan.is_valid, generation_count, edit_count."""

    def _plan_with_calls(self, calls: list[ToolCall], safety: bool = True) -> ExecutionPlan:
        return ExecutionPlan(tool_calls=calls, safety_validated=safety)

    def test_is_valid_requires_safety_and_calls(self):
        calls = [ToolCall(name="stori_generate_midi", params={})]
        assert self._plan_with_calls(calls, safety=True).is_valid
        assert not self._plan_with_calls(calls, safety=False).is_valid
        assert not self._plan_with_calls([], safety=True).is_valid

    def test_generation_count(self):
        calls = [
            ToolCall(name="stori_generate_midi", params={}),
            ToolCall(name="stori_generate_midi", params={}),
            ToolCall(name="stori_add_midi_track", params={}),
        ]
        plan = self._plan_with_calls(calls)
        assert plan.generation_count == 2

    def test_edit_count(self):
        calls = [
            ToolCall(name="stori_add_midi_track", params={}),
            ToolCall(name="stori_add_midi_region", params={}),
            ToolCall(name="stori_generate_midi", params={}),
        ]
        plan = self._plan_with_calls(calls)
        assert plan.edit_count == 2

    def test_to_dict_includes_tool_calls(self):
        calls = [ToolCall(name="stori_add_midi_track", params={"name": "Drums"})]
        plan = self._plan_with_calls(calls, safety=True)
        d = plan.to_dict()
        assert "tool_calls" in d
        assert len(d["tool_calls"]) == 1

    def test_empty_plan_is_not_valid(self):
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

    def test_after_intro_offsets_all_regions(self):
        """
        Parsing 'Position: after intro' and building a deterministic plan
        produces region tool calls with startBeat >= 64 (4 bars × 4 beats × 4 = 64 beats).
        """
        from app.core.prompts import resolve_position

        prompt = (
            "STORI PROMPT\n"
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

        project = {
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
            assert call.params["startBeat"] >= 64.0, (
                f"startBeat={call.params['startBeat']} not offset. Bug: offset not applied."
            )

    def test_no_position_regions_start_at_zero(self):
        """Without Position:, regions default to startBeat=0."""
        prompt = (
            "STORI PROMPT\n"
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
    async def test_deterministic_path_yields_plan_no_reasoning(self):
        """Deterministic fast-path yields an ExecutionPlan with no reasoning SSE."""
        parsed = _minimal_parsed(roles=["drums", "bass"])
        route = _make_route()
        llm = AsyncMock()

        items: list = []
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
    async def test_llm_path_yields_reasoning_then_plan(self):
        """LLM path yields reasoning SSE events then the ExecutionPlan."""
        route = _make_route()

        async def _fake_stream(**kwargs):
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
        plan_result = None

        async def mock_emit_sse(data):
            if data.get("type") == "reasoning":
                reasoning_events.append(data["content"])
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
    async def test_usage_tracker_updated_on_stream(self):
        """usage_tracker is updated with prompt/completion tokens from the stream."""
        from app.core.maestro_handlers import UsageTracker

        route = _make_route()

        async def _fake_stream(**kwargs):
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
    async def test_invalid_json_returns_failed_plan(self):
        """When LLM returns non-JSON content, streaming path returns a failed plan."""
        route = _make_route()

        async def _fake_stream(**kwargs):
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
