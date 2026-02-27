"""
Plan Schema Validation for Maestro.

Pydantic schemas for validating LLM-generated execution plans.

Key principles:
1. Fail fast with actionable error messages
2. Validate semantic consistency (same tempo across generations)
3. Provide sensible defaults for optional fields
4. Support partial plans for recovery
"""
from __future__ import annotations

from app.core.plan_schemas.models import (
    GenerationStep,
    EditStep,
    MixStep,
    ExecutionPlanSchema,
    PlanValidationResult,
)
from app.core.plan_schemas.validation import (
    validate_plan_json,
    extract_and_validate_plan,
)
from app.core.plan_schemas.completion import (
    infer_edits_from_generations,
    complete_plan,
)

__all__ = [
    "GenerationStep",
    "EditStep",
    "MixStep",
    "ExecutionPlanSchema",
    "PlanValidationResult",
    "validate_plan_json",
    "extract_and_validate_plan",
    "infer_edits_from_generations",
    "complete_plan",
]
