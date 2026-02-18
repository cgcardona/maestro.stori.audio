"""
Plan Schema Validation for Stori Maestro (Cursor-of-DAWs).

This module defines Pydantic schemas for validating LLM-generated execution plans.

Key principles:
1. Fail fast with actionable error messages
2. Validate semantic consistency (same tempo across generations)
3. Provide sensible defaults for optional fields
4. Support partial plans for recovery
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


# =============================================================================
# Generation Steps
# =============================================================================

class GenerationStep(BaseModel):
    """
    A single MIDI generation step.
    
    Example:
        {"role": "drums", "style": "boom bap", "bars": 8, "tempo": 90, "key": "Cm"}
    """
    role: Literal["drums", "bass", "chords", "melody", "arp", "pads", "fx", "lead"] = Field(
        ...,
        description="Musical role: drums, bass, chords, melody, arp, pads, fx, lead"
    )
    style: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Style tag: boom_bap, trap, house, lofi, jazz, funk, etc"
    )
    tempo: int = Field(
        ...,
        ge=30,
        le=300,
        description="Tempo in BPM (30-300)"
    )
    bars: int = Field(
        ...,
        ge=1,
        le=64,
        description="Number of bars to generate (1-64)"
    )
    key: Optional[str] = Field(
        default=None,
        description="Musical key (e.g., 'Cm', 'F#', 'G minor'). Required for melodic instruments."
    )
    constraints: Optional[dict[str, Any]] = Field(
        default=None,
        description="Additional constraints (density, syncopation, swing, etc.)"
    )
    
    @field_validator('key')
    @classmethod
    def validate_key(cls, v: Optional[str], info) -> Optional[str]:
        """Warn if key is missing for melodic instruments."""
        if v is None:
            role = info.data.get('role', '')
            if role in ('bass', 'chords', 'melody', 'arp', 'pads', 'lead'):
                logger.warning(f"⚠️ Key not specified for {role} - generation may be inconsistent")
        return v
    
    @field_validator('style')
    @classmethod
    def normalize_style(cls, v: str) -> str:
        """Normalize style string."""
        return v.strip().lower().replace(' ', '_')


class EditStep(BaseModel):
    """
    A DAW editing step (track or region creation).
    
    Examples:
        {"action": "add_track", "name": "Drums"}
        {"action": "add_region", "track": "Drums", "barStart": 0, "bars": 8}
    """
    action: Literal["add_track", "add_region"] = Field(
        ...,
        description="Edit action type"
    )
    name: Optional[str] = Field(
        default=None,
        description="Name for the entity (track or region)"
    )
    track: Optional[str] = Field(
        default=None,
        description="Track name/ID (for add_region)"
    )
    barStart: Optional[int] = Field(
        default=0,
        ge=0,
        description="Start bar for region (0-indexed)"
    )
    bars: Optional[int] = Field(
        default=None,
        ge=1,
        le=64,
        description="Duration in bars (for add_region)"
    )
    
    @model_validator(mode='after')
    def validate_action_params(self) -> "EditStep":
        """Validate that required params are present for each action type."""
        if self.action == "add_track":
            if not self.name:
                raise ValueError("add_track requires 'name' field")
        elif self.action == "add_region":
            if not self.track:
                raise ValueError("add_region requires 'track' field")
            if self.bars is None:
                raise ValueError("add_region requires 'bars' field")
        return self


class MixStep(BaseModel):
    """
    A mixing/effects step.
    
    Example:
        {"action": "add_insert", "track": "Drums", "type": "compressor"}
    """
    action: Literal["add_insert", "add_send", "set_volume", "set_pan"] = Field(
        ...,
        description="Mix action type"
    )
    track: str = Field(
        ...,
        description="Target track name/ID"
    )
    type: Optional[str] = Field(
        default=None,
        description="Effect type for add_insert: compressor, eq, reverb, delay, chorus, etc."
    )
    bus: Optional[str] = Field(
        default=None,
        description="Bus name/ID for add_send"
    )
    value: Optional[float] = Field(
        default=None,
        description="Value for set_volume (dB) or set_pan (-100 to 100)"
    )
    
    @field_validator('type')
    @classmethod
    def validate_effect_type(cls, v: Optional[str]) -> Optional[str]:
        """Validate effect type if provided."""
        if v is None:
            return v
        
        valid_effects = {
            "compressor", "eq", "reverb", "delay", "chorus", 
            "flanger", "phaser", "distortion", "overdrive", 
            "limiter", "gate", "saturator", "filter"
        }
        
        normalized = v.strip().lower()
        if normalized not in valid_effects:
            logger.warning(f"⚠️ Unknown effect type '{v}', may not be supported")
        
        return normalized
    
    @model_validator(mode='after')
    def validate_action_params(self) -> "MixStep":
        """Validate params based on action type."""
        if self.action == "add_insert" and not self.type:
            raise ValueError("add_insert requires 'type' field")
        if self.action == "add_send" and not self.bus:
            raise ValueError("add_send requires 'bus' field")
        if self.action in ("set_volume", "set_pan") and self.value is None:
            raise ValueError(f"{self.action} requires 'value' field")
        return self


# =============================================================================
# Complete Execution Plan
# =============================================================================

class ExecutionPlanSchema(BaseModel):
    """
    Complete execution plan from LLM.
    
    This schema validates the JSON structure the planner LLM produces.
    
    Example:
        {
            "generations": [
                {"role": "drums", "style": "boom_bap", "tempo": 90, "bars": 8},
                {"role": "bass", "style": "808", "tempo": 90, "bars": 8, "key": "Cm"}
            ],
            "edits": [
                {"action": "add_track", "name": "Drums"},
                {"action": "add_region", "track": "Drums", "barStart": 0, "bars": 8}
            ],
            "mix": [
                {"action": "add_insert", "track": "Drums", "type": "compressor"}
            ]
        }
    """
    generations: list[GenerationStep] = Field(
        default_factory=list,
        description="MIDI generation steps"
    )
    edits: list[EditStep] = Field(
        default_factory=list,
        description="DAW editing steps (track/region creation)"
    )
    mix: list[MixStep] = Field(
        default_factory=list,
        description="Mixing/effects steps"
    )
    
    # Optional metadata from LLM
    explanation: Optional[str] = Field(
        default=None,
        description="LLM's explanation of the plan (ignored in execution)"
    )
    
    @model_validator(mode='after')
    def validate_tempo_consistency(self) -> "ExecutionPlanSchema":
        """Ensure all generations use the same tempo."""
        if not self.generations:
            return self
        
        tempos = [g.tempo for g in self.generations]
        if len(set(tempos)) > 1:
            # Log warning but don't fail - LLM might have a reason
            logger.warning(f"⚠️ Inconsistent tempos in plan: {tempos}")
        
        return self
    
    @model_validator(mode='after')
    def validate_key_consistency(self) -> "ExecutionPlanSchema":
        """Ensure melodic instruments use consistent keys."""
        if not self.generations:
            return self
        
        melodic_keys = [
            g.key for g in self.generations 
            if g.role in ('bass', 'chords', 'melody', 'arp', 'pads', 'lead') and g.key
        ]
        
        if melodic_keys and len(set(melodic_keys)) > 1:
            logger.warning(f"⚠️ Inconsistent keys in plan: {melodic_keys}")
        
        return self
    
    @model_validator(mode='after')
    def validate_track_region_ordering(self) -> "ExecutionPlanSchema":
        """Ensure tracks are created before their regions."""
        track_names = set()
        
        for edit in self.edits:
            if edit.action == "add_track" and edit.name:
                track_names.add(edit.name.lower())
            elif edit.action == "add_region" and edit.track:
                track_lower = edit.track.lower()
                if track_lower not in track_names:
                    # Check if it might be referencing an existing track (OK)
                    logger.debug(f"📋 Region references track '{edit.track}' - assuming it exists or will be created")
        
        return self
    
    def is_empty(self) -> bool:
        """Check if the plan has no actions."""
        return not self.generations and not self.edits and not self.mix
    
    def generation_count(self) -> int:
        """Count of generation steps."""
        return len(self.generations)
    
    def total_steps(self) -> int:
        """Total number of steps in the plan."""
        return len(self.generations) + len(self.edits) + len(self.mix)


# =============================================================================
# Validation Functions
# =============================================================================

class PlanValidationResult(BaseModel):
    """Result of plan validation."""
    valid: bool
    plan: Optional[ExecutionPlanSchema] = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw_json: Optional[dict] = None


def validate_plan_json(raw_json: dict[str, Any]) -> PlanValidationResult:
    """
    Validate a raw JSON plan against the schema.
    
    Args:
        raw_json: Raw JSON dict from LLM
        
    Returns:
        PlanValidationResult with parsed plan or errors
    """
    errors: list[str] = []
    warnings: list[str] = []
    
    try:
        plan = ExecutionPlanSchema.model_validate(raw_json)
        
        # Check for empty plan
        if plan.is_empty():
            warnings.append("Plan is empty - no actions to execute")
        
        # Collect logged warnings (hacky but works for now)
        # In production, use a custom handler
        
        return PlanValidationResult(
            valid=True,
            plan=plan,
            errors=[],
            warnings=warnings,
            raw_json=raw_json,
        )
        
    except Exception as e:
        error_msg = str(e)
        
        # Parse Pydantic validation errors for better messages
        if hasattr(e, 'errors'):
            for err in e.errors():
                loc = ' → '.join(str(x) for x in err.get('loc', []))
                msg = err.get('msg', 'Unknown error')
                errors.append(f"{loc}: {msg}")
        else:
            errors.append(error_msg)
        
        return PlanValidationResult(
            valid=False,
            plan=None,
            errors=errors,
            warnings=warnings,
            raw_json=raw_json,
        )


def extract_and_validate_plan(llm_response: str) -> PlanValidationResult:
    """
    Extract JSON from LLM response and validate it.
    
    Handles common LLM output patterns:
    - Pure JSON
    - JSON wrapped in markdown code fences (```json ... ```)
    - JSON with preamble/postamble text
    - Multiple JSON objects (takes the first valid one)
    
    Args:
        llm_response: Raw LLM response text
        
    Returns:
        PlanValidationResult
    """
    import json
    import re
    
    if not llm_response or not llm_response.strip():
        return PlanValidationResult(
            valid=False,
            errors=["Empty LLM response"],
            raw_json=None,
        )
    
    text = llm_response.strip()
    
    # Strategy 1: Try markdown code fence extraction first (most common LLM pattern)
    # Matches ```json ... ``` or ``` ... ```
    fence_patterns = [
        r'```json\s*([\s\S]*?)\s*```',  # ```json ... ```
        r'```\s*([\s\S]*?)\s*```',       # ``` ... ```
    ]
    
    for pattern in fence_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                raw_json = json.loads(match.strip())
                if isinstance(raw_json, dict):
                    return validate_plan_json(raw_json)
            except json.JSONDecodeError:
                continue
    
    # Strategy 2: Find all potential JSON objects and try each
    json_candidates = _extract_json_candidates(text)
    
    for candidate in json_candidates:
        try:
            raw_json = json.loads(candidate)
            if isinstance(raw_json, dict):
                # Validate it looks like a plan
                if _looks_like_plan(raw_json):
                    return validate_plan_json(raw_json)
        except json.JSONDecodeError:
            continue
    
    # Strategy 3: Aggressive extraction - find outermost braces
    start = text.find("{")
    end = text.rfind("}")
    
    if start != -1 and end != -1 and end > start:
        json_str = text[start:end + 1]
        
        # Try to fix common JSON issues
        json_str = _fix_common_json_issues(json_str)
        
        try:
            raw_json = json.loads(json_str)
            if isinstance(raw_json, dict):
                return validate_plan_json(raw_json)
        except json.JSONDecodeError as e:
            return PlanValidationResult(
                valid=False,
                errors=[f"Invalid JSON after extraction: {e}"],
                raw_json=None,
            )
    
    return PlanValidationResult(
        valid=False,
        errors=["No valid JSON object found in LLM response"],
        raw_json=None,
    )


def _extract_json_candidates(text: str) -> list[str]:
    """
    Extract all potential JSON object strings from text.
    
    Uses brace matching to find complete objects.
    """
    candidates = []
    i = 0
    
    while i < len(text):
        if text[i] == '{':
            # Try to find matching closing brace
            depth = 0
            start = i
            in_string = False
            escape_next = False
            
            for j in range(i, len(text)):
                char = text[j]
                
                if escape_next:
                    escape_next = False
                    continue
                
                if char == '\\':
                    escape_next = True
                    continue
                
                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue
                
                if not in_string:
                    if char == '{':
                        depth += 1
                    elif char == '}':
                        depth -= 1
                        if depth == 0:
                            candidates.append(text[start:j + 1])
                            i = j
                            break
            
        i += 1
    
    return candidates


def _looks_like_plan(obj: dict) -> bool:
    """Check if a dict looks like an execution plan."""
    plan_keys = {"generations", "edits", "mix"}
    obj_keys = set(obj.keys())
    
    # Has at least one plan key
    if obj_keys & plan_keys:
        return True
    
    # Has arrays that look like plan arrays
    for key, value in obj.items():
        if isinstance(value, list) and len(value) > 0:
            if isinstance(value[0], dict):
                # Check for generation-like structure
                if "role" in value[0] or "action" in value[0]:
                    return True
    
    return False


def _fix_common_json_issues(json_str: str) -> str:
    """
    Fix common JSON issues from LLM output.
    
    - Trailing commas
    - Single quotes instead of double
    - Unquoted keys
    """
    import re
    
    # Remove trailing commas before } or ]
    json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
    
    # Note: More aggressive fixes like single->double quotes are risky
    # because they can break valid strings. Only do this if needed.
    
    return json_str


# =============================================================================
# Plan Inference (Fill in missing parts)
# =============================================================================

def infer_edits_from_generations(generations: list[GenerationStep]) -> list[EditStep]:
    """
    Infer required track/region edits from generation steps.
    
    If the LLM only provided generations, we need to create
    tracks and regions for them.
    
    Args:
        generations: List of generation steps
        
    Returns:
        List of inferred edit steps
    """
    edits: list[EditStep] = []
    seen_tracks: set[str] = set()
    
    for gen in generations:
        # Capitalize role for track name (drums → Drums)
        track_name = gen.role.capitalize()
        
        # Add track if not seen
        if track_name.lower() not in seen_tracks:
            edits.append(EditStep(
                action="add_track",
                name=track_name,
            ))
            seen_tracks.add(track_name.lower())
        
        # Add region for this generation
        edits.append(EditStep(
            action="add_region",
            track=track_name,
            barStart=0,  # Default to start
            bars=gen.bars,
        ))
    
    return edits


def _find_track_for_role(role: str, existing_tracks: set[str]) -> Optional[str]:
    """
    Find an existing track that matches a generation role.
    
    Uses fuzzy matching: if "drums" is contained in "jam drums", it's a match.
    Returns the original track name (with original casing) if found.
    
    Args:
        role: The generation role (e.g., "drums", "bass")
        existing_tracks: Set of existing track names (lowercase)
        
    Returns:
        The matching track name (lowercase) or None
    """
    role_lower = role.lower()
    
    # Exact match first
    if role_lower in existing_tracks:
        return role_lower
    
    # Fuzzy match: check if role is contained in any track name
    for track in existing_tracks:
        if role_lower in track:
            return track
    
    return None


def complete_plan(plan: ExecutionPlanSchema) -> ExecutionPlanSchema:
    """
    Complete a partial plan by inferring missing parts.
    
    Ensures every generation has a corresponding track and region.
    Also REMOVES tracks that don't have corresponding generations (prevents empty tracks).
    Uses fuzzy matching to detect if a descriptive track name (e.g., "Jam Drums")
    matches a generation role (e.g., "drums").
    
    Args:
        plan: Partial plan from LLM
        
    Returns:
        Completed plan with inferred parts and empty tracks removed
    """
    if not plan.generations:
        return plan  # No generations, nothing to complete
    
    # Build set of roles we're actually generating MIDI for
    generation_roles = {gen.role.lower() for gen in plan.generations}
    
    # Build set of existing tracks (lowercase) and map to original names
    existing_tracks: set[str] = set()
    track_name_map: dict[str, str] = {}  # lowercase -> original name
    existing_regions: dict[str, int] = {}  # track_name_lower -> bars
    
    for edit in plan.edits:
        if edit.action == "add_track" and edit.name:
            lower_name = edit.name.lower()
            existing_tracks.add(lower_name)
            track_name_map[lower_name] = edit.name  # Preserve original casing
        elif edit.action == "add_region" and edit.track and edit.bars:
            existing_regions[edit.track.lower()] = edit.bars
    
    # Infer missing tracks and regions for each generation
    inferred_edits: list[EditStep] = []
    
    for gen in plan.generations:
        # Check if a track for this role already exists (fuzzy match)
        matching_track = _find_track_for_role(gen.role, existing_tracks)
        
        if matching_track:
            # Track exists - use its original name for region inference
            original_name = track_name_map.get(matching_track, gen.role.capitalize())
            track_lower = matching_track
            logger.debug(f"📋 Found existing track '{original_name}' for role '{gen.role}'")
        else:
            # No matching track - need to infer one
            track_name = gen.role.capitalize()
            track_lower = track_name.lower()
            inferred_edits.append(EditStep(
                action="add_track",
                name=track_name,
            ))
            existing_tracks.add(track_lower)
            track_name_map[track_lower] = track_name
            original_name = track_name
            logger.debug(f"📋 Inferred missing track: {track_name}")
        
        # Add region if missing (or if bars mismatch)
        if track_lower not in existing_regions or existing_regions[track_lower] != gen.bars:
            inferred_edits.append(EditStep(
                action="add_region",
                track=original_name,  # Use original casing
                barStart=0,
                bars=gen.bars,
            ))
            existing_regions[track_lower] = gen.bars
            logger.debug(f"📋 Inferred missing region for {original_name}: {gen.bars} bars")
    
    # Filter out tracks from edits that don't have generations
    # This prevents the LLM from creating empty tracks like "Phish Keys" when
    # it's not generating MIDI for them
    filtered_edits = []
    removed_tracks = []
    removed_track_lowers = set()
    
    for edit in plan.edits:
        if edit.action == "add_track" and edit.name:
            track_lower = edit.name.lower()
            # Check if this track matches any generation role
            has_generation = any(
                role in track_lower or track_lower in role
                for role in generation_roles
            )
            if has_generation:
                filtered_edits.append(edit)
            else:
                removed_tracks.append(edit.name)
                removed_track_lowers.add(track_lower)
                logger.warning(f"🗑️ Removing track '{edit.name}' - no corresponding generation")
        elif edit.action == "add_region" and edit.track:
            # Only keep regions for tracks that weren't removed
            if edit.track.lower() not in removed_track_lowers:
                filtered_edits.append(edit)
            else:
                logger.warning(f"🗑️ Removing region for removed track '{edit.track}'")
        else:
            # Keep other edits
            filtered_edits.append(edit)
    
    if removed_tracks:
        logger.info(f"🗑️ Removed {len(removed_tracks)} empty tracks: {removed_tracks}")
    
    if inferred_edits or removed_tracks:
        logger.info(f"📋 Plan completion: +{len(inferred_edits)} inferred, -{len(removed_tracks)} removed")
        
        # Merge filtered and inferred edits
        all_edits = filtered_edits + inferred_edits
        
        # Also filter mix steps for removed tracks
        filtered_mix = [
            mix_step for mix_step in plan.mix
            if mix_step.track.lower() not in [t.lower() for t in removed_tracks]
        ]
        
        if len(filtered_mix) < len(plan.mix):
            logger.info(f"🗑️ Removed {len(plan.mix) - len(filtered_mix)} mix steps for empty tracks")
        
        return ExecutionPlanSchema(
            generations=plan.generations,
            edits=all_edits,
            mix=filtered_mix,
            explanation=plan.explanation,
        )
    
    return plan
