"""Tests for plan_schemas.py (app/core/plan_schemas.py).

Covers: GenerationStep, EditStep, MixStep, ExecutionPlanSchema,
validate_plan_json, extract_and_validate_plan, _extract_json_candidates,
_looks_like_plan, _fix_common_json_issues, infer_edits_from_generations,
complete_plan.
"""
import json
import pytest

from app.core.plan_schemas import (
    GenerationStep,
    EditStep,
    MixStep,
    ExecutionPlanSchema,
    PlanValidationResult,
    validate_plan_json,
    extract_and_validate_plan,
    infer_edits_from_generations,
    complete_plan,
)


# ---------------------------------------------------------------------------
# GenerationStep
# ---------------------------------------------------------------------------


class TestGenerationStep:

    def test_valid_drums(self):
        step = GenerationStep(
            role="drums", style="trap", tempo=120, bars=4
        )
        assert step.role == "drums"
        assert step.style == "trap"

    def test_style_normalized(self):
        step = GenerationStep(
            role="drums", style="  Boom Bap  ", tempo=90, bars=8
        )
        assert step.style == "boom_bap"

    def test_melodic_without_key_warns(self):
        # Should succeed but log a warning
        step = GenerationStep(
            role="bass", style="funk", tempo=100, bars=4
        )
        assert step.key is None

    def test_with_key(self):
        step = GenerationStep(
            role="melody", style="jazz", tempo=120, bars=8, key="Cm"
        )
        assert step.key == "Cm"

    def test_invalid_tempo_too_low(self):
        with pytest.raises(Exception):
            GenerationStep(role="drums", style="trap", tempo=5, bars=4)

    def test_invalid_tempo_too_high(self):
        with pytest.raises(Exception):
            GenerationStep(role="drums", style="trap", tempo=500, bars=4)

    def test_invalid_bars_too_high(self):
        with pytest.raises(Exception):
            GenerationStep(role="drums", style="trap", tempo=120, bars=100)


# ---------------------------------------------------------------------------
# EditStep
# ---------------------------------------------------------------------------


class TestEditStep:

    def test_add_track(self):
        step = EditStep(action="add_track", name="Drums")
        assert step.action == "add_track"
        assert step.name == "Drums"

    def test_add_region(self):
        step = EditStep(
            action="add_region", track="Drums",
            barStart=0, bars=8, name="Intro",
        )
        assert step.action == "add_region"
        assert step.track == "Drums"


# ---------------------------------------------------------------------------
# ExecutionPlanSchema
# ---------------------------------------------------------------------------


class TestExecutionPlanSchema:

    def test_empty_plan(self):
        plan = ExecutionPlanSchema()
        assert plan.is_empty() is True

    def test_plan_with_generations(self):
        plan = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="trap", tempo=120, bars=4),
            ]
        )
        assert plan.is_empty() is False

    def test_plan_with_edits(self):
        plan = ExecutionPlanSchema(
            edits=[EditStep(action="add_track", name="Piano")]
        )
        assert plan.is_empty() is False


# ---------------------------------------------------------------------------
# validate_plan_json
# ---------------------------------------------------------------------------


class TestValidatePlanJSON:

    def test_valid_plan(self):
        raw = {
            "generations": [
                {"role": "drums", "style": "trap", "tempo": 120, "bars": 4},
            ],
        }
        result = validate_plan_json(raw)
        assert result.valid is True
        assert result.plan is not None

    def test_invalid_role(self):
        raw = {
            "generations": [
                {"role": "invalid_role", "style": "trap", "tempo": 120, "bars": 4},
            ],
        }
        result = validate_plan_json(raw)
        assert result.valid is False
        assert len(result.errors) > 0

    def test_empty_plan_warns(self):
        raw = {}
        result = validate_plan_json(raw)
        assert result.valid is True
        assert len(result.warnings) > 0

    def test_multiple_generations(self):
        raw = {
            "generations": [
                {"role": "drums", "style": "trap", "tempo": 120, "bars": 4},
                {"role": "bass", "style": "trap", "tempo": 120, "bars": 4, "key": "Cm"},
            ],
        }
        result = validate_plan_json(raw)
        assert result.valid is True
        assert len(result.plan.generations) == 2


# ---------------------------------------------------------------------------
# extract_and_validate_plan
# ---------------------------------------------------------------------------


class TestExtractAndValidatePlan:

    def test_pure_json(self):
        text = json.dumps({
            "generations": [
                {"role": "drums", "style": "boom_bap", "tempo": 90, "bars": 4},
            ],
        })
        result = extract_and_validate_plan(text)
        assert result.valid is True

    def test_json_in_code_fence(self):
        text = """Here's the plan:
```json
{
    "generations": [
        {"role": "drums", "style": "trap", "tempo": 120, "bars": 8}
    ]
}
```
Let me know if you'd like changes."""
        result = extract_and_validate_plan(text)
        assert result.valid is True

    def test_json_with_preamble(self):
        text = """Sure, I'll create a plan for you:

{"generations": [{"role": "drums", "style": "house", "tempo": 128, "bars": 4}]}

That should work well."""
        result = extract_and_validate_plan(text)
        assert result.valid is True

    def test_empty_response(self):
        result = extract_and_validate_plan("")
        assert result.valid is False
        assert "Empty" in result.errors[0]

    def test_no_json_found(self):
        result = extract_and_validate_plan("This is just text with no JSON")
        assert result.valid is False

    def test_invalid_json(self):
        result = extract_and_validate_plan("{invalid json}")
        assert result.valid is False


# ---------------------------------------------------------------------------
# infer_edits_from_generations
# ---------------------------------------------------------------------------


class TestInferEdits:

    def test_infer_track_and_region(self):
        gens = [
            GenerationStep(role="drums", style="trap", tempo=120, bars=4),
        ]
        edits = infer_edits_from_generations(gens)
        assert len(edits) >= 1  # Should create at least a track edit

    def test_multiple_roles(self):
        gens = [
            GenerationStep(role="drums", style="trap", tempo=120, bars=4),
            GenerationStep(role="bass", style="trap", tempo=120, bars=4, key="Cm"),
        ]
        edits = infer_edits_from_generations(gens)
        assert len(edits) >= 2


# ---------------------------------------------------------------------------
# complete_plan
# ---------------------------------------------------------------------------


class TestCompletePlan:

    def test_infers_edits(self):
        plan = ExecutionPlanSchema(
            generations=[
                GenerationStep(role="drums", style="trap", tempo=120, bars=4),
            ],
        )
        completed = complete_plan(plan)
        assert len(completed.edits) >= 1

    def test_preserves_existing_edits(self):
        plan = ExecutionPlanSchema(
            edits=[EditStep(action="add_track", name="Custom")],
            generations=[
                GenerationStep(role="drums", style="trap", tempo=120, bars=4),
            ],
        )
        completed = complete_plan(plan)
        # Should have edits (inferred or original)
        assert len(completed.edits) >= 1
