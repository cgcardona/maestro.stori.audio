"""
Tests for plan schema validation.

The plan schemas ensure LLM-generated plans are valid before execution.
"""

import pytest
from pydantic import ValidationError

from app.core.plan_schemas import (
    GenerationStep,
    EditStep,
    MixStep,
    ExecutionPlanSchema,
    validate_plan_json,
    extract_and_validate_plan,
    complete_plan,
    infer_edits_from_generations,
)
from app.core.planner import _build_role_to_track_map, _schema_to_tool_calls, build_plan_from_dict


class TestGenerationStep:
    """Test GenerationStep validation."""
    
    def test_valid_drums(self):
        """Should accept valid drums generation."""
        step = GenerationStep(
            role="drums",
            style="boom_bap",
            tempo=90,
            bars=8,
        )
        
        assert step.role == "drums"
        assert step.style == "boom_bap"
    
    def test_valid_bass_with_key(self):
        """Should accept bass generation with key."""
        step = GenerationStep(
            role="bass",
            style="808",
            tempo=90,
            bars=8,
            key="Cm",
        )
        
        assert step.key == "Cm"
    
    def test_style_normalization(self):
        """Should normalize style string."""
        step = GenerationStep(
            role="drums",
            style="Boom Bap",
            tempo=90,
            bars=8,
        )
        
        assert step.style == "boom_bap"
    
    def test_invalid_role(self):
        """Should reject invalid role."""
        with pytest.raises(ValidationError):
            GenerationStep(
                role="invalid_role",
                style="boom_bap",
                tempo=90,
                bars=8,
            )
    
    def test_tempo_out_of_range(self):
        """Should reject tempo outside 30-300."""
        with pytest.raises(ValidationError):
            GenerationStep(
                role="drums",
                style="boom_bap",
                tempo=500,  # Too high
                bars=8,
            )
    
    def test_bars_out_of_range(self):
        """Should reject bars outside 1-64."""
        with pytest.raises(ValidationError):
            GenerationStep(
                role="drums",
                style="boom_bap",
                tempo=90,
                bars=100,  # Too many
            )


class TestEditStep:
    """Test EditStep validation."""
    
    def test_valid_add_track(self):
        """Should accept valid add_track."""
        step = EditStep(
            action="add_track",
            name="Drums",
        )
        
        assert step.action == "add_track"
        assert step.name == "Drums"
    
    def test_add_track_requires_name(self):
        """add_track should require name."""
        with pytest.raises(ValidationError, match="name"):
            EditStep(action="add_track")
    
    def test_valid_add_region(self):
        """Should accept valid add_region."""
        step = EditStep(
            action="add_region",
            track="Drums",
            barStart=0,
            bars=8,
        )
        
        assert step.track == "Drums"
        assert step.bars == 8
    
    def test_add_region_requires_track(self):
        """add_region should require track."""
        with pytest.raises(ValidationError, match="track"):
            EditStep(
                action="add_region",
                bars=8,
            )
    
    def test_add_region_requires_bars(self):
        """add_region should require bars."""
        with pytest.raises(ValidationError, match="bars"):
            EditStep(
                action="add_region",
                track="Drums",
            )


class TestMixStep:
    """Test MixStep validation."""
    
    def test_valid_add_insert(self):
        """Should accept valid add_insert."""
        step = MixStep(
            action="add_insert",
            track="Drums",
            type="compressor",
        )
        
        assert step.type == "compressor"
    
    def test_add_insert_requires_type(self):
        """add_insert should require type."""
        with pytest.raises(ValidationError, match="type"):
            MixStep(
                action="add_insert",
                track="Drums",
            )
    
    def test_effect_type_normalization(self):
        """Should normalize effect type."""
        step = MixStep(
            action="add_insert",
            track="Drums",
            type="Compressor",
        )
        
        assert step.type == "compressor"
    
    def test_valid_add_send(self):
        """Should accept valid add_send."""
        step = MixStep(
            action="add_send",
            track="Drums",
            bus="Reverb",
        )
        
        assert step.bus == "Reverb"
    
    def test_add_send_requires_bus(self):
        """add_send should require bus."""
        with pytest.raises(ValidationError, match="bus"):
            MixStep(
                action="add_send",
                track="Drums",
            )


class TestExecutionPlanSchema:
    """Test complete execution plan validation."""
    
    def test_empty_plan(self):
        """Should accept empty plan."""
        plan = ExecutionPlanSchema()
        
        assert plan.is_empty()
        assert plan.generation_count() == 0
    
    def test_full_plan(self):
        """Should accept complete plan."""
        plan = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="boom_bap", tempo=90, bars=8),
                GenerationStep(role="bass", style="808", tempo=90, bars=8, key="Cm"),
            ],
            edits=[
                EditStep(action="add_track", name="Drums"),
                EditStep(action="add_track", name="Bass"),
                EditStep(action="add_region", track="Drums", barStart=0, bars=8),
                EditStep(action="add_region", track="Bass", barStart=0, bars=8),
            ],
            mix=[
                MixStep(action="add_insert", track="Drums", type="compressor"),
            ],
        )
        
        assert not plan.is_empty()
        assert plan.generation_count() == 2
        assert plan.total_steps() == 7
    
    def test_tempo_consistency_warning(self):
        """Should warn about inconsistent tempos (but not fail)."""
        # This should not raise, just log a warning
        plan = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="boom_bap", tempo=90, bars=8),
                GenerationStep(role="bass", style="808", tempo=120, bars=8, key="Cm"),  # Different tempo
            ],
        )
        
        assert plan.generation_count() == 2


class TestPlanValidation:
    """Test plan validation functions."""
    
    def test_validate_valid_json(self):
        """Should validate correct JSON."""
        raw_json = {
            "generations": [
                {"role": "drums", "style": "boom_bap", "tempo": 90, "bars": 8},
            ],
            "edits": [
                {"action": "add_track", "name": "Drums"},
            ],
            "mix": [],
        }
        
        result = validate_plan_json(raw_json)
        
        assert result.valid
        assert result.plan is not None
        assert len(result.errors) == 0
    
    def test_validate_invalid_json(self):
        """Should report errors for invalid JSON."""
        raw_json = {
            "generations": [
                {"role": "invalid", "style": "boom_bap", "tempo": 90, "bars": 8},
            ],
        }
        
        result = validate_plan_json(raw_json)
        
        assert not result.valid
        assert len(result.errors) > 0
    
    def test_extract_from_llm_response(self):
        """Should extract JSON from LLM response text."""
        llm_response = """
        Here's the plan for your beat:
        
        {
            "generations": [
                {"role": "drums", "style": "boom_bap", "tempo": 90, "bars": 8}
            ],
            "edits": [
                {"action": "add_track", "name": "Drums"}
            ],
            "mix": []
        }
        
        This will create a classic boom bap drum pattern!
        """
        
        result = extract_and_validate_plan(llm_response)
        
        assert result.valid
        assert result.plan is not None
    
    def test_extract_no_json(self):
        """Should report error when no JSON found."""
        llm_response = "I can't generate music without more details."
        
        result = extract_and_validate_plan(llm_response)
        
        assert not result.valid
        assert "JSON" in result.errors[0]  # Error message mentions JSON


class TestBuildPlanFromDict:
    """Test build_plan_from_dict (planner helper for testing/macros)."""

    def test_valid_dict_returns_execution_plan(self):
        """Valid plan dict returns ExecutionPlan with tool_calls."""
        plan_dict = {
            "generations": [
                {"role": "drums", "style": "boom_bap", "tempo": 90, "bars": 8},
            ],
            "edits": [
                {"action": "add_track", "name": "Drums"},
                {"action": "add_region", "track": "Drums", "barStart": 0, "bars": 8},
            ],
            "mix": [],
        }
        plan = build_plan_from_dict(plan_dict)
        assert plan is not None
        assert plan.safety_validated is True
        assert len(plan.tool_calls) > 0
        assert "Built from dict" in plan.notes

    def test_invalid_dict_returns_plan_with_validation_notes(self):
        """Invalid plan dict returns ExecutionPlan with validation failed notes."""
        plan_dict = {
            "generations": [{"role": "invalid_role", "style": "x", "tempo": 90, "bars": 8}],
            "edits": [],
            "mix": [],
        }
        plan = build_plan_from_dict(plan_dict)
        assert plan is not None
        assert plan.validation_result is not None
        assert plan.validation_result.valid is False
        assert len(plan.notes) > 0
        assert "Validation failed" in plan.notes[0]


class TestPlanCompletion:
    """Test automatic plan completion."""
    
    def test_infer_edits_from_generations(self):
        """Should infer track/region edits from generations."""
        generations = [
            GenerationStep(role="drums", style="boom_bap", tempo=90, bars=8),
            GenerationStep(role="bass", style="808", tempo=90, bars=8, key="Cm"),
        ]
        
        edits = infer_edits_from_generations(generations)
        
        # Should create 2 tracks + 2 regions = 4 edits
        assert len(edits) == 4
        
        # First two should be add_track
        assert edits[0].action == "add_track"
        assert edits[0].name == "Drums"
        assert edits[1].action == "add_region"
        assert edits[1].track == "Drums"
    
    def test_complete_plan_with_missing_edits(self):
        """Should complete plan that only has generations."""
        plan = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="boom_bap", tempo=90, bars=8),
            ],
            edits=[],  # Missing!
            mix=[],
        )
        
        completed = complete_plan(plan)
        
        assert len(completed.edits) == 2  # add_track + add_region
        assert completed.edits[0].action == "add_track"
        assert completed.edits[1].action == "add_region"
    
    def test_complete_plan_preserves_existing_edits(self):
        """Should preserve existing edits and not duplicate tracks with fuzzy match."""
        plan = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="boom_bap", tempo=90, bars=8),
            ],
            edits=[
                EditStep(action="add_track", name="My Custom Drums"),
            ],
            mix=[],
        )
        
        completed = complete_plan(plan)
        
        # Should keep original track + add region (but not create duplicate "Drums" track)
        assert len(completed.edits) == 2
        assert completed.edits[0].name == "My Custom Drums"  # Original track preserved
        assert completed.edits[1].action == "add_region"
        assert completed.edits[1].track == "My Custom Drums"  # Region targets original track
    
    def test_complete_plan_fuzzy_matches_descriptive_track_names(self):
        """Should match 'Jam Drums' to drums role, preventing duplicate tracks."""
        plan = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="phish", tempo=120, bars=16),
                GenerationStep(role="bass", style="funk", tempo=120, bars=16, key="E"),
            ],
            edits=[
                EditStep(action="add_track", name="Jam Drums"),
                EditStep(action="add_track", name="Funky Bass"),
                EditStep(action="add_region", track="Jam Drums", barStart=0, bars=16),
                EditStep(action="add_region", track="Funky Bass", barStart=0, bars=16),
            ],
            mix=[
                MixStep(action="add_insert", track="Jam Drums", type="compressor"),
            ],
        )
        
        completed = complete_plan(plan)
        
        # Should NOT add new tracks - "Jam Drums" matches "drums", "Funky Bass" matches "bass"
        track_edits = [e for e in completed.edits if e.action == "add_track"]
        assert len(track_edits) == 2
        assert track_edits[0].name == "Jam Drums"
        assert track_edits[1].name == "Funky Bass"
        
        # Should NOT have generic "Drums" or "Bass" tracks
        track_names = [e.name for e in completed.edits if e.action == "add_track"]
        assert "Drums" not in track_names
        assert "Bass" not in track_names
    
    def test_complete_plan_adds_track_when_no_fuzzy_match(self):
        """Should add generic track when no existing track matches the role."""
        plan = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="boom_bap", tempo=90, bars=8),
                GenerationStep(role="bass", style="808", tempo=90, bars=8, key="Cm"),
            ],
            edits=[
                EditStep(action="add_track", name="My Kick Track"),  # Doesn't contain "drums"
            ],
            mix=[],
        )
        
        completed = complete_plan(plan)
        
        # Completion infers add_track for each generation; empty/orphan tracks may be removed
        track_edits = [e for e in completed.edits if e.action == "add_track"]
        assert len(track_edits) >= 2  # At least Drums + Bass (original "My Kick Track" may be removed)


class TestRoleToTrackMapping:
    """Test planner role→track name mapping (prevents duplicate track bug)."""
    
    def test_build_role_to_track_map_exact_match(self):
        """Should map role to exact track name."""
        plan = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="boom_bap", tempo=90, bars=8),
            ],
            edits=[
                EditStep(action="add_track", name="Drums"),
            ],
            mix=[],
        )
        
        mapping = _build_role_to_track_map(plan)
        
        assert mapping["drums"] == "Drums"
    
    def test_build_role_to_track_map_fuzzy_match(self):
        """Should map role to descriptive track name containing the role."""
        plan = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="phish", tempo=120, bars=16),
                GenerationStep(role="bass", style="funk", tempo=120, bars=16, key="E"),
            ],
            edits=[
                EditStep(action="add_track", name="Jam Drums"),
                EditStep(action="add_track", name="Funky Bass"),
            ],
            mix=[],
        )
        
        mapping = _build_role_to_track_map(plan)
        
        assert mapping["drums"] == "Jam Drums"
        assert mapping["bass"] == "Funky Bass"
    
    def test_build_role_to_track_map_defaults_to_capitalized(self):
        """Should default to capitalized role when no track matches."""
        plan = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="boom_bap", tempo=90, bars=8),
            ],
            edits=[],  # No tracks defined
            mix=[],
        )
        
        mapping = _build_role_to_track_map(plan)
        
        assert mapping["drums"] == "Drums"  # Default
    
    def test_schema_to_tool_calls_uses_descriptive_track_names(self):
        """Should generate MIDI to descriptive track name, not generic role name."""
        plan = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="phish", tempo=120, bars=16),
                GenerationStep(role="bass", style="funk", tempo=120, bars=16, key="E"),
            ],
            edits=[
                EditStep(action="add_track", name="Jam Drums"),
                EditStep(action="add_track", name="Funky Bass"),
                EditStep(action="add_region", track="Jam Drums", barStart=0, bars=16),
                EditStep(action="add_region", track="Funky Bass", barStart=0, bars=16),
            ],
            mix=[
                MixStep(action="add_insert", track="Jam Drums", type="compressor"),
                MixStep(action="add_insert", track="Funky Bass", type="eq"),
            ],
        )
        
        tool_calls = _schema_to_tool_calls(plan)
        
        # Find generate_midi tool calls
        midi_calls = [tc for tc in tool_calls if tc.name == "stori_generate_midi"]
        assert len(midi_calls) == 2
        
        # Drums generation should target "Jam Drums", not "Drums"
        drums_call = next(tc for tc in midi_calls if tc.params["role"] == "drums")
        assert drums_call.params["trackName"] == "Jam Drums"
        
        # Bass generation should target "Funky Bass", not "Bass"
        bass_call = next(tc for tc in midi_calls if tc.params["role"] == "bass")
        assert bass_call.params["trackName"] == "Funky Bass"
        
        # Insert effects should also target the descriptive names
        insert_calls = [tc for tc in tool_calls if tc.name == "stori_add_insert_effect"]
        assert len(insert_calls) == 2
        
        insert_tracks = {tc.params["trackName"] for tc in insert_calls}
        assert "Jam Drums" in insert_tracks
        assert "Funky Bass" in insert_tracks
    
    def test_schema_to_tool_calls_prevents_duplicate_track_bug(self):
        """
        Regression test for the duplicate track bug.
        
        Previously, if LLM created "Jam Drums" but generation used role="drums",
        the system would create two tracks: "Jam Drums" (with effects) and "Drums"
        (with MIDI). This test ensures MIDI goes to the correct track.
        """
        plan = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="phish", tempo=120, bars=16),
            ],
            edits=[
                EditStep(action="add_track", name="Jam Drums"),
                EditStep(action="add_region", track="Jam Drums", barStart=0, bars=16),
            ],
            mix=[
                MixStep(action="add_insert", track="Jam Drums", type="compressor"),
            ],
        )
        
        tool_calls = _schema_to_tool_calls(plan)
        
        # Should only create one track: "Jam Drums"
        track_calls = [tc for tc in tool_calls if tc.name == "stori_add_midi_track"]
        assert len(track_calls) == 1
        assert track_calls[0].params["name"] == "Jam Drums"
        
        # MIDI should go to "Jam Drums"
        midi_calls = [tc for tc in tool_calls if tc.name == "stori_generate_midi"]
        assert len(midi_calls) == 1
        assert midi_calls[0].params["trackName"] == "Jam Drums"
        
        # Effect should go to "Jam Drums"
        effect_calls = [tc for tc in tool_calls if tc.name == "stori_add_insert_effect"]
        assert len(effect_calls) == 1
        assert effect_calls[0].params["trackName"] == "Jam Drums"


class TestEmptyTrackRemoval:
    """Test that tracks without generations are removed."""
    
    def test_removes_tracks_without_generations(self):
        """Should remove tracks that don't have corresponding MIDI generations."""
        plan = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="phish", tempo=120, bars=16),
                GenerationStep(role="bass", style="funk", tempo=120, bars=16, key="E"),
            ],
            edits=[
                EditStep(action="add_track", name="Phish Drums"),
                EditStep(action="add_track", name="Phish Bass"),
                EditStep(action="add_track", name="Phish Keys"),     # No generation!
                EditStep(action="add_track", name="Phish Guitar"),   # No generation!
                EditStep(action="add_region", track="Phish Keys", barStart=0, bars=16),  # Should be removed
                EditStep(action="add_region", track="Phish Guitar", barStart=0, bars=16),  # Should be removed
            ],
            mix=[
                MixStep(action="add_insert", track="Phish Keys", type="reverb"),
                MixStep(action="add_insert", track="Phish Guitar", type="delay"),
            ],
        )
        
        completed = complete_plan(plan)
        
        # Should have removed Phish Keys and Phish Guitar tracks
        track_names = [e.name for e in completed.edits if e.action == "add_track"]
        assert "Phish Drums" in track_names
        assert "Phish Bass" in track_names
        assert "Phish Keys" not in track_names, "Should remove track without generation"
        assert "Phish Guitar" not in track_names, "Should remove track without generation"
        
        # Should have removed their regions too
        region_tracks = [e.track for e in completed.edits if e.action == "add_region"]
        assert "Phish Keys" not in region_tracks, "Should remove region for removed track"
        assert "Phish Guitar" not in region_tracks, "Should remove region for removed track"
        
        # Should also remove their mix steps
        mix_tracks = [m.track for m in completed.mix]
        assert "Phish Keys" not in mix_tracks
        assert "Phish Guitar" not in mix_tracks
    
    def test_keeps_tracks_with_fuzzy_matched_generations(self):
        """Should keep tracks that fuzzy match generation roles."""
        plan = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="phish", tempo=120, bars=16),
                GenerationStep(role="chords", style="jazz", tempo=120, bars=16, key="C"),
            ],
            edits=[
                EditStep(action="add_track", name="Phish Drums"),  # Matches "drums"
                EditStep(action="add_track", name="Chords"),       # Matches "chords"
                EditStep(action="add_track", name="Lead"),         # No match - should be removed
            ],
            mix=[],
        )
        
        completed = complete_plan(plan)
        
        track_names = [e.name for e in completed.edits if e.action == "add_track"]
        assert "Phish Drums" in track_names
        assert "Chords" in track_names
        assert "Lead" not in track_names, "Should remove track without generation"
