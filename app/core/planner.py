"""
Planner for Stori Composer.

Converts natural language music requests into validated execution plans.

Responsibilities:
- Parse LLM JSON responses into structured plans
- Validate plans against Pydantic schemas
- Infer missing edits (tracks/regions) from generation requests
- Convert validated plans to executable ToolCall sequences

Used for COMPOSING flows where the user wants to generate music.
The planner outputs an ExecutionPlan containing primitives + generator calls.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.expansion import ToolCall
from app.core.intent import IntentResult, Intent
from app.core.prompt_parser import ParsedPrompt
from app.core.tools import build_tool_registry
from app.core.prompts import composing_prompt, structured_prompt_context, system_prompt_base
from app.core.plan_schemas import (
    ExecutionPlanSchema,
    GenerationStep,
    extract_and_validate_plan,
    complete_plan,
    PlanValidationResult,
)

logger = logging.getLogger(__name__)


@dataclass
class ExecutionPlan:
    """
    Validated execution plan ready for the executor.
    """
    tool_calls: list[ToolCall] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    safety_validated: bool = False
    llm_response_text: Optional[str] = None
    validation_result: Optional[PlanValidationResult] = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "notes": self.notes,
            "safety_validated": self.safety_validated,
            "validation_errors": self.validation_result.errors if self.validation_result else [],
        }
    
    @property
    def is_valid(self) -> bool:
        """Check if plan is valid and has tool calls."""
        return self.safety_validated and len(self.tool_calls) > 0
    
    @property
    def generation_count(self) -> int:
        """Count of generation tool calls."""
        return sum(1 for tc in self.tool_calls if tc.name.startswith("stori_generate"))
    
    @property
    def edit_count(self) -> int:
        """Count of edit tool calls."""
        return sum(1 for tc in self.tool_calls if tc.name in (
            "stori_add_midi_track", "stori_add_midi_region"
        ))


async def build_execution_plan(
    user_prompt: str,
    project_state: dict[str, Any],
    route: IntentResult,
    llm,
    parsed: Optional[ParsedPrompt] = None,
) -> ExecutionPlan:
    """
    Ask the LLM for a structured JSON plan for composing.
    
    Flow:
    1. If structured prompt has all fields, build deterministically (skip LLM)
    2. Otherwise, send prompt to LLM with composing instructions
    3. Extract JSON from response
    4. Validate against schema
    5. Complete plan (infer missing parts)
    6. Convert to ToolCalls
    
    Args:
        user_prompt: User's request
        project_state: Current DAW state
        route: Intent routing result
        llm: LLM client
        parsed: Optional parsed structured prompt for deterministic planning
        
    Returns:
        ExecutionPlan ready for executor
    """
    # Structured prompt fast path: if all key fields are present, build
    # the plan deterministically without an LLM call.
    if parsed is not None:
        deterministic = _try_deterministic_plan(parsed)
        if deterministic is not None:
            return deterministic

    sys = system_prompt_base() + "\n" + composing_prompt()

    # Inject structured context if available (partial structured prompt
    # that couldn't be built deterministically)
    if parsed is not None:
        sys += structured_prompt_context(parsed)
    
    # Call LLM for plan
    resp = await llm.chat(
        system=sys,
        user=user_prompt,
        tools=[],  # No tools: planner produces JSON plan
        tool_choice="none",
        context={"project_state": project_state, "route": route.__dict__},
    )
    
    llm_response_text = resp.content or ""
    logger.debug(f"📋 Planner LLM response length: {len(llm_response_text)} chars")
    
    # Validate the response
    validation = extract_and_validate_plan(llm_response_text)
    
    if not validation.valid:
        logger.warning(f"⚠️ Plan validation failed: {validation.errors}")
        return ExecutionPlan(
            notes=[f"Plan validation failed: {'; '.join(validation.errors)}"],
            llm_response_text=llm_response_text,
            validation_result=validation,
        )
    
    # Complete the plan (infer missing edits)
    if validation.plan is None:
        return ExecutionPlan(
            notes=["Plan schema missing after validation"],
            llm_response_text=llm_response_text,
            validation_result=validation,
        )
    plan_schema = complete_plan(validation.plan)
    if plan_schema is None:
        return ExecutionPlan(
            notes=["Plan schema could not be completed"],
            llm_response_text=llm_response_text,
            validation_result=validation,
        )
    if plan_schema.is_empty():
        logger.warning("⚠️ Plan is empty after completion")
        return ExecutionPlan(
            notes=["Plan is empty - request may be too vague"],
            llm_response_text=llm_response_text,
            validation_result=validation,
        )
    
    # Convert schema to ToolCalls
    tool_calls = _schema_to_tool_calls(plan_schema)
    
    logger.info(
        f"✅ Planner generated {len(tool_calls)} tool calls "
        f"({plan_schema.generation_count()} generations, "
        f"{len(plan_schema.edits)} edits, {len(plan_schema.mix)} mix)"
    )
    
    return ExecutionPlan(
        tool_calls=tool_calls,
        notes=[
            f"planner: {len(tool_calls)} tool calls",
            *validation.warnings,
        ],
        safety_validated=True,
        llm_response_text=llm_response_text,
        validation_result=validation,
    )


def _try_deterministic_plan(parsed: ParsedPrompt) -> Optional[ExecutionPlan]:
    """
    Build an execution plan deterministically from a structured prompt.

    Requires: style, tempo, roles, and bars (from constraints).
    When all are present, we skip the LLM entirely — zero inference overhead.
    Returns None if any required field is missing (caller falls back to LLM).
    """
    if not parsed.style or not parsed.tempo or not parsed.roles:
        return None

    bars = parsed.constraints.get("bars")
    if not isinstance(bars, int) or bars < 1:
        return None

    logger.info(
        f"⚡ Deterministic plan from structured prompt: "
        f"{len(parsed.roles)} roles, {parsed.style}, {parsed.tempo} BPM, {bars} bars"
    )

    generations = [
        GenerationStep(
            role=role if role in ("drums", "bass", "chords", "melody", "arp", "pads", "fx", "lead") else "melody",
            style=parsed.style,
            tempo=parsed.tempo,
            bars=bars,
            key=parsed.key,
            constraints={
                k: v for k, v in parsed.constraints.items()
                if k not in ("bars",)
            } or None,
        )
        for role in parsed.roles
    ]

    plan_schema = ExecutionPlanSchema(generations=generations)
    plan_schema = complete_plan(plan_schema)
    if plan_schema is None:
        return None

    tool_calls = _schema_to_tool_calls(plan_schema)

    return ExecutionPlan(
        tool_calls=tool_calls,
        notes=[
            f"deterministic_plan: {len(tool_calls)} tool calls from structured prompt",
            f"style={parsed.style}, tempo={parsed.tempo}, bars={bars}",
        ],
        safety_validated=True,
    )


def _build_role_to_track_map(plan: ExecutionPlanSchema) -> dict[str, str]:
    """
    Build a mapping from generation role to actual track name.
    
    This handles cases where the LLM creates descriptive track names
    (e.g., "Jam Drums") that should be matched to roles (e.g., "drums").
    
    Args:
        plan: The validated execution plan
        
    Returns:
        Dict mapping role (lowercase) to track name (original casing)
    """
    role_to_track: dict[str, str] = {}
    
    # Extract all track names from edits
    track_names: list[str] = [
        edit.name for edit in plan.edits 
        if edit.action == "add_track" and edit.name
    ]
    
    # For each generation role, find a matching track
    all_roles = {"drums", "bass", "chords", "melody", "arp", "pads", "fx", "lead"}
    
    for role in all_roles:
        # Check each track for a match
        for track_name in track_names:
            track_lower = track_name.lower()
            # Exact match or role contained in track name
            if track_lower == role or role in track_lower:
                role_to_track[role] = track_name
                break
        
        # Default to capitalized role if no match found
        if role not in role_to_track:
            role_to_track[role] = role.capitalize()
    
    return role_to_track


def _schema_to_tool_calls(plan: ExecutionPlanSchema) -> list[ToolCall]:
    """
    Convert validated plan schema to ToolCalls.
    
    Execution order is critical:
    1. Create tracks (add_track edits)
    2. Create regions (add_region edits)
    3. Generate MIDI into regions (generations)
    4. Apply mixing/effects (mix)
    
    Uses role→track mapping to ensure generations target the correct tracks
    when LLM uses descriptive names like "Jam Drums" instead of just "Drums".
    """
    tool_calls: list[ToolCall] = []
    
    # Build role→track name mapping for consistent targeting
    role_to_track = _build_role_to_track_map(plan)
    
    # Step 1: Create tracks
    from app.core.track_styling import get_track_styling
    
    track_styling_map = {}  # Store styling for later
    
    for edit in plan.edits:
        if edit.action == "add_track" and edit.name:
            styling = get_track_styling(edit.name)
            track_styling_map[edit.name] = styling
            
            tool_calls.append(ToolCall(
                name="stori_add_midi_track",
                params={"name": edit.name}
            ))
    
    # Step 1b: Set colors and icons for tracks
    for track_name, styling in track_styling_map.items():
        tool_calls.append(ToolCall(
            name="stori_set_track_color",
            params={
                "trackName": track_name,
                "color": styling["color"],
            }
        ))
        tool_calls.append(ToolCall(
            name="stori_set_track_icon",
            params={
                "trackName": track_name,
                "icon": styling["icon"],
            }
        ))
    
    # Step 2: Create regions (convert bars to beats: 4 beats per bar in 4/4 time)
    for edit in plan.edits:
        if edit.action == "add_region" and edit.track and edit.bars:
            bar_start = edit.barStart or 0
            tool_calls.append(ToolCall(
                name="stori_add_midi_region",
                params={
                    "name": edit.track,  # Use track name for region display
                    "trackName": edit.track,  # For resolution
                    "startBeat": bar_start * 4,
                    "durationBeats": edit.bars * 4,
                }
            ))
    
    # Step 3: Generate MIDI - use role→track mapping for correct targeting
    for gen in plan.generations:
        track_name = role_to_track.get(gen.role, gen.role.capitalize())
        tool_calls.append(ToolCall(
            name="stori_generate_midi",
            params={
                "role": gen.role,
                "style": gen.style,
                "tempo": gen.tempo,
                "bars": gen.bars,
                "key": gen.key or "",
                "trackName": track_name,  # Use mapped track name, not just role
                "constraints": gen.constraints or {},
            }
        ))
    
    # Step 4: Apply effects/mixing
    for mix in plan.mix:
        if mix.action == "add_insert" and mix.type:
            tool_calls.append(ToolCall(
                name="stori_add_insert_effect",
                params={
                    "trackName": mix.track,
                    "type": mix.type,
                }
            ))
        elif mix.action == "add_send" and mix.bus:
            tool_calls.append(ToolCall(
                name="stori_add_send",
                params={
                    "trackName": mix.track,
                    "busName": mix.bus,
                }
            ))
        elif mix.action == "set_volume" and mix.value is not None:
            tool_calls.append(ToolCall(
                name="stori_set_track_volume",
                params={
                    "trackName": mix.track,
                    "volume": mix.value,
                }
            ))
        elif mix.action == "set_pan" and mix.value is not None:
            tool_calls.append(ToolCall(
                name="stori_set_track_pan",
                params={
                    "trackName": mix.track,
                    "pan": mix.value,
                }
            ))
    
    return tool_calls


# =============================================================================
# Alternative: Direct Plan Building (for testing/macros)
# =============================================================================

def build_plan_from_dict(plan_dict: dict[str, Any]) -> ExecutionPlan:
    """
    Build an execution plan from a dict (for testing or macro expansion).
    
    Args:
        plan_dict: Plan dictionary in the expected format
        
    Returns:
        ExecutionPlan
    """
    from app.core.plan_schemas import validate_plan_json, complete_plan
    
    validation = validate_plan_json(plan_dict)
    
    if not validation.valid:
        return ExecutionPlan(
            notes=[f"Validation failed: {'; '.join(validation.errors)}"],
            validation_result=validation,
        )
    
    if validation.plan is None:
        return ExecutionPlan(
            notes=["Plan schema missing after validation"],
            validation_result=validation,
        )
    plan_schema = complete_plan(validation.plan)
    if plan_schema is None:
        return ExecutionPlan(
            notes=["Plan schema could not be completed"],
            validation_result=validation,
        )
    tool_calls = _schema_to_tool_calls(plan_schema)
    
    return ExecutionPlan(
        tool_calls=tool_calls,
        notes=["Built from dict"],
        safety_validated=True,
        validation_result=validation,
    )


# =============================================================================
# Plan Preview
# =============================================================================

async def preview_plan(
    user_prompt: str,
    project_state: dict[str, Any],
    route: IntentResult,
    llm,
    parsed: Optional[ParsedPrompt] = None,
) -> dict[str, Any]:
    """
    Generate a plan preview without executing.
    
    Returns a summary of what would happen, for user confirmation.
    """
    plan = await build_execution_plan(user_prompt, project_state, route, llm, parsed=parsed)
    
    preview = {
        "valid": plan.is_valid,
        "total_steps": len(plan.tool_calls),
        "generations": plan.generation_count,
        "edits": plan.edit_count,
        "tool_calls": [tc.to_dict() for tc in plan.tool_calls],
        "notes": plan.notes,
    }
    
    if plan.validation_result and plan.validation_result.errors:
        preview["errors"] = plan.validation_result.errors
    
    if plan.validation_result and plan.validation_result.warnings:
        preview["warnings"] = plan.validation_result.warnings
    
    return preview
