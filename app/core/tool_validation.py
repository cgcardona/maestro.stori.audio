"""
Tool Argument Validation for Stori Composer (Cursor-of-DAWs).

This module provides comprehensive validation for tool calls:
1. Schema validation (required params, types)
2. Entity reference validation (trackId, regionId, busId exist)
3. Value range validation (volume dB, pan range, etc.)

Key principles:
- Fail fast with clear error messages
- Validate before execution, not during
- Separate concerns: schema vs entity vs value validation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.core.entity_registry import EntityRegistry
from app.core.tools import tool_schema_by_name

logger = logging.getLogger(__name__)


@dataclass
class ValidationError:
    """A single validation error."""
    field: str
    message: str
    code: str  # For programmatic handling
    
    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


@dataclass
class ValidationResult:
    """Result of tool call validation."""
    valid: bool
    tool_name: str
    original_params: dict[str, Any]
    resolved_params: dict[str, Any]  # Params with resolved entity IDs
    errors: list[ValidationError]
    warnings: list[str]
    
    @property
    def error_message(self) -> str:
        """Combined error message."""
        if not self.errors:
            return ""
        return "; ".join(str(e) for e in self.errors)


# =============================================================================
# Validation Rules per Tool
# =============================================================================

# Entity reference fields that need validation
ENTITY_REF_FIELDS = {
    "trackId": "track",
    "regionId": "region", 
    "busId": "bus",
    "trackName": "track",  # Will be resolved to trackId
}

# Entity-creating tools → which ID field to SKIP validation on.
# The server replaces these IDs with fresh UUIDs after validation,
# so a hallucinated ID from the LLM should not cause rejection.
_ENTITY_CREATING_SKIP: dict[str, set[str]] = {
    "stori_add_midi_track": {"trackId"},
    "stori_add_midi_region": {"regionId"},
    "stori_ensure_bus": {"busId"},
}

# Value range constraints
VALUE_RANGES = {
    "volume": (0.0, 1.5),     # stori_set_track_volume: linear 0–1.5
    "pan": (0.0, 1.0),        # stori_set_track_pan: 0.0=left, 0.5=center, 1.0=right
    "sendLevel": (0.0, 1.0),  # stori_add_send
    "gridSize": (0.0625, 4.0),# stori_quantize_notes: 1/64 beat to whole note
    "tempo": (30, 300),
    "bars": (1, 64),
    "zoomPercent": (10, 500),
    "velocity": (1, 127),
    "pitch": (0, 127),
    "amount": (0, 1),
    "strength": (0, 1),
    "startBeat": (0, float('inf')),
    "durationBeats": (0.01, 1000),
}

# Name length constraints (per frontend validation)
NAME_LENGTH_LIMITS = {
    "track": 50,
    "region": 50,
    "bus": 50,
    "project": 100,
}

# Required fields per tool (beyond schema "required")
TOOL_REQUIRED_FIELDS = {
    "stori_add_notes": ["regionId", "notes"],
    "stori_add_midi_region": ["trackId", "startBeat", "durationBeats"],
    "stori_set_track_volume": ["trackId", "volume"],
    "stori_set_track_pan": ["trackId", "pan"],
    "stori_add_insert_effect": ["trackId", "type"],
    "stori_add_send": ["trackId", "busName"],
    "stori_quantize_notes": ["regionId", "gridSize"],
    "stori_transpose_notes": ["regionId", "semitones"],
    "stori_move_region": ["regionId", "startBeat"],
}


# =============================================================================
# Main Validation Functions
# =============================================================================

def validate_tool_call(
    tool_name: str,
    params: dict[str, Any],
    allowed_tools: set[str],
    registry: Optional[EntityRegistry] = None,
    target_scope: Optional[tuple[str, Optional[str]]] = None,
) -> ValidationResult:
    """
    Validate a tool call. Allowlist is the single source of truth: only tools in
    allowed_tools may be called. Compose passes intent-derived allowlists (no
    generators); MCP passes all MCP tool names.

    Performs:
    1. Allowlist check
    2. Entity resolution (when registry provided)
    3. Schema validation (required params, types)
    4. Value range validation
    5. Tool-specific validation
    6. Target scope check (advisory warning, never blocks)

    Args:
        target_scope: Optional (kind, name) from a structured prompt Target field.
            Example: ("track", "Bass"). When provided, a warning is emitted if
            the tool call appears to affect a different entity.
    """
    errors: list[ValidationError] = []
    warnings: list[str] = []
    resolved_params = params.copy()

    # 1. Allowlist check (single source of truth)
    if tool_name not in allowed_tools:
        errors.append(ValidationError(
            field="tool_name",
            message=f"Tool '{tool_name}' is not allowed for this request",
            code="TOOL_NOT_ALLOWED",
        ))
        return ValidationResult(
            valid=False,
            tool_name=tool_name,
            original_params=params,
            resolved_params=resolved_params,
            errors=errors,
            warnings=warnings,
        )

    # 2. Entity resolution (when registry provided) (so resolved params can satisfy schema requirements)
    if registry:
        resolution_result = _resolve_and_validate_entities(
            tool_name, resolved_params, registry
        )
        resolved_params = resolution_result["params"]
        errors.extend(resolution_result["errors"])
        warnings.extend(resolution_result["warnings"])
    
    # 3. Schema validation (against resolved params)
    schema = tool_schema_by_name(tool_name)
    if schema:
        schema_errors = _validate_schema(tool_name, resolved_params, schema)
        errors.extend(schema_errors)
    
    # 4. Value range validation
    range_errors = _validate_value_ranges(resolved_params)
    errors.extend(range_errors)
    
    # 5. Tool-specific validation
    specific_errors = _validate_tool_specific(tool_name, resolved_params)
    errors.extend(specific_errors)

    # 6. Target scope advisory check (structured prompt)
    if target_scope is not None:
        scope_warnings = _check_target_scope(
            tool_name, resolved_params, target_scope, registry
        )
        warnings.extend(scope_warnings)
    
    return ValidationResult(
        valid=len(errors) == 0,
        tool_name=tool_name,
        original_params=params,
        resolved_params=resolved_params,
        errors=errors,
        warnings=warnings,
    )


def validate_tool_call_simple(
    tool_name: str,
    params: dict[str, Any],
    allowed_tools: set[str],
) -> tuple[bool, str]:
    """
    Simple validation that returns (valid, error_message).
    
    For backwards compatibility with existing code.
    """
    result = validate_tool_call(tool_name, params, allowed_tools, registry=None)
    return result.valid, result.error_message


# =============================================================================
# Schema Validation
# =============================================================================

def _validate_schema(
    tool_name: str,
    params: dict[str, Any],
    schema: dict[str, Any],
) -> list[ValidationError]:
    """Validate params against tool schema."""
    errors: list[ValidationError] = []
    
    func_schema = schema.get("function", {}).get("parameters", {})
    required = func_schema.get("required", [])
    properties = func_schema.get("properties", {})
    
    # Check required fields
    for field in required:
        if field not in params:
            errors.append(ValidationError(
                field=field,
                message=f"Required field '{field}' is missing",
                code="MISSING_REQUIRED",
            ))
    
    # Check for additional required fields from our rules
    tool_required = TOOL_REQUIRED_FIELDS.get(tool_name, [])
    for field in tool_required:
        if field not in params and field not in required:
            # Only warn, don't fail - might be provided by resolution
            pass
    
    # Type validation
    for field, value in params.items():
        if field not in properties:
            continue
        
        expected_type = properties[field].get("type")
        if expected_type:
            type_error = _validate_type(field, value, expected_type)
            if type_error:
                errors.append(type_error)
    
    return errors


def _validate_type(field: str, value: Any, expected_type: str) -> Optional[ValidationError]:
    """Validate a value against expected JSON Schema type."""
    type_map: dict[str, type | tuple[type, ...]] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    
    expected = type_map.get(expected_type)
    if expected is not None and not isinstance(value, expected):
        return ValidationError(
            field=field,
            message=f"Expected {expected_type}, got {type(value).__name__}",
            code="TYPE_MISMATCH",
        )
    
    return None


# =============================================================================
# Entity Resolution and Validation
# =============================================================================

def _resolve_and_validate_entities(
    tool_name: str,
    params: dict[str, Any],
    registry: EntityRegistry,
) -> dict[str, Any]:
    """
    Resolve and validate entity references with suggestions.
    
    - trackName → trackId (resolution)
    - trackId → validate exists
    - regionId → validate exists
    - busId → validate exists (or create for ensure_bus)
    
    When entities are not found, provides suggestions of available entities.
    """
    errors: list[ValidationError] = []
    warnings: list[str] = []
    resolved = params.copy()
    
    # Helper to get available entity names for suggestions
    def _get_track_suggestions() -> list[str]:
        return [t.name for t in registry.list_tracks()]
    
    def _get_region_suggestions(track_id: str | None = None) -> list[str]:
        if track_id:
            return [f"{r.name} (regionId: {r.id})" for r in registry.get_track_regions(track_id)]
        return [f"{r.name} (regionId: {r.id})" for r in registry.list_regions()]
    
    def _get_bus_suggestions() -> list[str]:
        return [b.name for b in registry.list_buses()]
    
    # Resolve trackName to trackId
    if "trackName" in resolved and "trackId" not in resolved:
        track_name = resolved["trackName"]
        track_id = registry.resolve_track(track_name)
        
        if track_id:
            resolved["trackId"] = track_id
            logger.debug(f"🔗 Resolved trackName '{track_name}' → {track_id[:8]}")
        else:
            available = _get_track_suggestions()
            suggestion = ""
            if available:
                # Find closest match
                closest = _find_closest_match(track_name, available)
                if closest:
                    suggestion = f" Did you mean '{closest}'?"
                else:
                    suggestion = f" Available tracks: {', '.join(available[:5])}"
            
            errors.append(ValidationError(
                field="trackName",
                message=f"Track '{track_name}' not found.{suggestion}",
                code="ENTITY_NOT_FOUND",
            ))
    
    # Validate trackId exists (skip for entity-creating tools — server replaces the ID)
    skip_fields = _ENTITY_CREATING_SKIP.get(tool_name, set())
    if "trackId" in resolved and "trackId" not in skip_fields:
        track_id = resolved["trackId"]
        if not registry.exists_track(track_id):
            # Check if it might be a name
            resolved_id = registry.resolve_track(track_id)
            if resolved_id:
                resolved["trackId"] = resolved_id
            else:
                available = _get_track_suggestions()
                suggestion = ""
                if available:
                    closest = _find_closest_match(track_id, available)
                    if closest:
                        suggestion = f" Did you mean '{closest}'?"
                    else:
                        suggestion = f" Available: {', '.join(available[:5])}"
                
                errors.append(ValidationError(
                    field="trackId",
                    message=f"Track '{track_id}' not found.{suggestion}",
                    code="ENTITY_NOT_FOUND",
                ))
    
    # Validate regionId exists (skip for entity-creating tools)
    if "regionId" in resolved and "regionId" not in skip_fields:
        region_id = resolved["regionId"]
        if not registry.exists_region(region_id):
            resolved_id = registry.resolve_region(region_id)
            if resolved_id:
                resolved["regionId"] = resolved_id
            else:
                track_id = resolved.get("trackId")
                suggestion = ""
                # Detect the common hallucination: LLM passed a trackId as regionId
                if registry.exists_track(region_id):
                    track_regions = _get_region_suggestions(region_id)
                    if track_regions:
                        suggestion = (
                            f" NOTE: '{region_id[:8]}...' is a trackId, not a regionId."
                            f" Use the regionId from stori_add_midi_region."
                            f" Regions on this track: {', '.join(track_regions[:3])}"
                        )
                    else:
                        suggestion = (
                            f" NOTE: '{region_id[:8]}...' is a trackId, not a regionId."
                            " Call stori_add_midi_region first to create a region."
                        )
                else:
                    available = _get_region_suggestions(track_id)
                    if available:
                        closest = _find_closest_match(region_id, available)
                        if closest:
                            suggestion = f" Did you mean '{closest}'?"
                        else:
                            suggestion = f" Available: {', '.join(available[:5])}"

                errors.append(ValidationError(
                    field="regionId",
                    message=f"Region '{region_id}' not found.{suggestion}",
                    code="ENTITY_NOT_FOUND",
                ))
    
    # Validate or create busId (skip for entity-creating tools)
    if "busId" in resolved and "busId" not in skip_fields:
        bus_id = resolved["busId"]
        if not registry.exists_bus(bus_id):
            resolved_id = registry.resolve_bus(bus_id)
            if resolved_id:
                resolved["busId"] = resolved_id
            else:
                available = _get_bus_suggestions()
                suggestion = ""
                if available:
                    closest = _find_closest_match(bus_id, available)
                    if closest:
                        suggestion = f" Did you mean '{closest}'?"
                    else:
                        suggestion = f" Available: {', '.join(available[:5])}"
                
                errors.append(ValidationError(
                    field="busId",
                    message=f"Bus '{bus_id}' not found.{suggestion}",
                    code="ENTITY_NOT_FOUND",
                ))
    
    return {
        "params": resolved,
        "errors": errors,
        "warnings": warnings,
    }


def _find_closest_match(query: str, candidates: list[str], threshold: float = 0.6) -> str | None:
    """
    Find the closest matching string from candidates.
    
    Uses simple character-level similarity.
    """
    if not candidates:
        return None
    
    query_lower = query.lower()
    
    # First try exact prefix/suffix match
    for c in candidates:
        c_lower = c.lower()
        if c_lower.startswith(query_lower) or query_lower.startswith(c_lower):
            return c
    
    # Then try substring match
    for c in candidates:
        if query_lower in c.lower() or c.lower() in query_lower:
            return c
    
    # Finally try character overlap
    best_match = None
    best_score = 0.0
    
    for c in candidates:
        c_lower = c.lower()
        # Simple Jaccard-like similarity
        query_chars = set(query_lower)
        c_chars = set(c_lower)
        
        if not query_chars or not c_chars:
            continue
            
        intersection = len(query_chars & c_chars)
        union = len(query_chars | c_chars)
        score = intersection / union
        
        if score > best_score and score >= threshold:
            best_score = score
            best_match = c
    
    return best_match


# =============================================================================
# Value Range Validation
# =============================================================================

def _validate_value_ranges(params: dict[str, Any]) -> list[ValidationError]:
    """Validate numeric values are within expected ranges."""
    errors: list[ValidationError] = []
    
    for field, (min_val, max_val) in VALUE_RANGES.items():
        if field in params:
            value = params[field]
            if isinstance(value, (int, float)):
                if value < min_val or value > max_val:
                    errors.append(ValidationError(
                        field=field,
                        message=f"Value {value} is out of range [{min_val}, {max_val}]",
                        code="VALUE_OUT_OF_RANGE",
                    ))
    
    # Special validation for notes array
    if "notes" in params and isinstance(params["notes"], list):
        for i, note in enumerate(params["notes"]):
            if isinstance(note, dict):
                for field in ["pitch", "velocity"]:
                    if field in note:
                        value = note[field]
                        if field in VALUE_RANGES:
                            min_val, max_val = VALUE_RANGES[field]
                            if isinstance(value, (int, float)) and (value < min_val or value > max_val):
                                errors.append(ValidationError(
                                    field=f"notes[{i}].{field}",
                                    message=f"Value {value} is out of range [{min_val}, {max_val}]",
                                    code="VALUE_OUT_OF_RANGE",
                                ))
    
    return errors


# =============================================================================
# Tool-Specific Validation
# =============================================================================

def _validate_tool_specific(tool_name: str, params: dict[str, Any]) -> list[ValidationError]:
    """Tool-specific validation rules."""
    errors: list[ValidationError] = []
    
    # Name length validation (per frontend constraints)
    if tool_name == "stori_add_midi_track":
        name = params.get("name", "")
        if name and len(name) > NAME_LENGTH_LIMITS["track"]:
            errors.append(ValidationError(
                field="name",
                message=f"Track name exceeds {NAME_LENGTH_LIMITS['track']} characters",
                code="NAME_TOO_LONG",
            ))
        if not name or not name.strip():
            errors.append(ValidationError(
                field="name",
                message="Track name cannot be empty or whitespace-only",
                code="INVALID_NAME",
            ))
    
    elif tool_name == "stori_add_midi_region":
        name = params.get("name")
        if name and len(name) > NAME_LENGTH_LIMITS["region"]:
            errors.append(ValidationError(
                field="name",
                message=f"Region name exceeds {NAME_LENGTH_LIMITS['region']} characters",
                code="NAME_TOO_LONG",
            ))
        
        start_beat = params.get("startBeat", 0)
        duration = params.get("durationBeats", 0)
        
        if isinstance(start_beat, (int, float)) and start_beat < 0:
            errors.append(ValidationError(
                field="startBeat",
                message="startBeat cannot be negative",
                code="INVALID_VALUE",
            ))
        
        if isinstance(duration, (int, float)) and duration < 0.01:
            errors.append(ValidationError(
                field="durationBeats",
                message="durationBeats must be at least 0.01",
                code="INVALID_VALUE",
            ))
    
    elif tool_name == "stori_ensure_bus":
        name = params.get("name", "")
        if name and len(name) > NAME_LENGTH_LIMITS["bus"]:
            errors.append(ValidationError(
                field="name",
                message=f"Bus name exceeds {NAME_LENGTH_LIMITS['bus']} characters",
                code="NAME_TOO_LONG",
            ))
    
    elif tool_name == "stori_create_project":
        name = params.get("name", "")
        if name and len(name) > NAME_LENGTH_LIMITS["project"]:
            errors.append(ValidationError(
                field="name",
                message=f"Project name exceeds {NAME_LENGTH_LIMITS['project']} characters",
                code="NAME_TOO_LONG",
            ))
    
    elif tool_name == "stori_add_insert_effect":
        effect_type = params.get("type", "").lower()
        # Per frontend validation constraints
        valid_effects = {
            "reverb", "delay", "compressor", "eq", "distortion", "filter",
            "chorus", "modulation", "overdrive", "phaser", "flanger", "tremolo"
        }
        if effect_type and effect_type not in valid_effects:
            errors.append(ValidationError(
                field="type",
                message=f"Unknown effect type '{effect_type}'. Valid: {', '.join(sorted(valid_effects))}",
                code="INVALID_EFFECT_TYPE",
            ))
    
    elif tool_name == "stori_set_track_icon":
        icon = params.get("icon", "")
        # Valid SF Symbols per frontend constraints
        valid_icons = {
            # Instruments
            "guitars", "guitars.fill", "pianokeys", "pianokeys.inverse",
            "music.mic", "music.mic.circle", "music.mic.circle.fill",
            "headphones", "headphones.circle", "headphones.circle.fill",
            "hifispeaker", "hifispeaker.fill", "hifispeaker.2", "hifispeaker.2.fill",
            "tuningfork", "speaker", "speaker.fill",
            "speaker.wave.2", "speaker.wave.3",
            "speaker.slash", "speaker.slash.fill",
            # Notes & Waveforms
            "music.note", "music.note.list", "music.quarternote.3",
            "music.note.house", "music.note.tv",
            "waveform", "waveform.circle", "waveform.circle.fill",
            "waveform.path", "waveform.path.ecg",
            "music.note.house.fill", "music.note.tv.fill",
            "waveform.and.mic", "waveform.badge.mic", "waveform.slash",
            # Effects & Controls
            "slider.horizontal.3", "slider.vertical.3",
            "sparkles", "wand.and.rays", "wand.and.stars", "wand.and.stars.inverse",
            "bolt", "bolt.fill", "bolt.circle", "bolt.circle.fill",
            "flame", "flame.fill", "metronome", "star", "star.fill",
            "dial.min", "dial.medium", "dial.max",
            "repeat", "repeat.1", "shuffle",
            "ear", "ear.badge.waveform"
        }
        if icon and icon not in valid_icons:
            errors.append(ValidationError(
                field="icon",
                message=f"Invalid icon '{icon}'. Must be from curated SF Symbols list (58 options)",
                code="INVALID_ICON",
            ))
    
    elif tool_name == "stori_add_notes":
        notes = params.get("notes", [])
        if not isinstance(notes, list):
            errors.append(ValidationError(
                field="notes",
                message="notes must be an array",
                code="TYPE_MISMATCH",
            ))
        elif len(notes) == 0:
            errors.append(ValidationError(
                field="notes",
                message="notes array cannot be empty",
                code="INVALID_VALUE",
            ))
        else:
            # Validate individual notes per frontend constraints
            for i, note in enumerate(notes):
                if not isinstance(note, dict):
                    continue
                
                # Pitch validation (0-127)
                if "pitch" in note:
                    pitch = note["pitch"]
                    if not isinstance(pitch, int) or pitch < 0 or pitch > 127:
                        errors.append(ValidationError(
                            field=f"notes[{i}].pitch",
                            message=f"Pitch must be 0-127, got {pitch}",
                            code="INVALID_PITCH",
                        ))
                
                # Velocity validation (1-127, not 0)
                if "velocity" in note:
                    velocity = note["velocity"]
                    if not isinstance(velocity, int) or velocity < 1 or velocity > 127:
                        errors.append(ValidationError(
                            field=f"notes[{i}].velocity",
                            message=f"Velocity must be 1-127, got {velocity}",
                            code="INVALID_VELOCITY",
                        ))
                
                # StartBeat validation (>= 0)
                if "startBeat" in note:
                    start = note["startBeat"]
                    if not isinstance(start, (int, float)) or start < 0:
                        errors.append(ValidationError(
                            field=f"notes[{i}].startBeat",
                            message=f"StartBeat must be >= 0, got {start}",
                            code="INVALID_START",
                        ))
                
                # Duration validation (0.01-1000)
                if "durationBeats" in note:
                    duration = note["durationBeats"]
                    if not isinstance(duration, (int, float)) or duration < 0.01 or duration > 1000:
                        errors.append(ValidationError(
                            field=f"notes[{i}].durationBeats",
                            message=f"Duration must be 0.01-1000 beats, got {duration}",
                            code="INVALID_DURATION",
                        ))
    
    elif tool_name == "stori_quantize_notes":
        grid_size = params.get("gridSize")
        valid_grid_sizes = {0.0625, 0.125, 0.25, 0.5, 1.0, 2.0, 4.0}
        if grid_size is not None and grid_size not in valid_grid_sizes:
            errors.append(ValidationError(
                field="gridSize",
                message=f"Invalid gridSize '{grid_size}'. Valid: 0.0625(1/64) 0.125(1/32) 0.25(1/16) 0.5(1/8) 1.0(1/4) 2.0(1/2) 4.0(whole)",
                code="INVALID_VALUE",
            ))
    
    return errors


# =============================================================================
# Target Scope Check (Structured Prompt)
# =============================================================================


def _check_target_scope(
    tool_name: str,
    params: dict[str, Any],
    target_scope: tuple[str, Optional[str]],
    registry: Optional[EntityRegistry],
) -> list[str]:
    """
    Advisory check: warn if a tool call operates outside the structured prompt's
    Target scope. Never blocks execution — warnings only.

    Args:
        target_scope: (kind, name) e.g. ("track", "Bass")
    """
    kind, name = target_scope

    # "project" scope means everything is in scope
    if kind == "project":
        return []

    # Only check track/region-scoped targets when we have a name
    if not name:
        return []

    warnings: list[str] = []

    if kind == "track":
        # Check if tool call references a track different from the target
        track_id = params.get("trackId")
        track_name = params.get("trackName") or params.get("name")

        if track_name and track_name.lower() != name.lower():
            warnings.append(
                f"Target scope is track:{name} but tool call "
                f"references track '{track_name}'"
            )
        elif track_id and registry:
            # Resolve the target name to an ID and compare
            target_id = registry.resolve_track(name)
            if target_id and track_id != target_id:
                warnings.append(
                    f"Target scope is track:{name} but tool call "
                    f"references a different track"
                )

    elif kind == "region":
        region_name = params.get("name")
        if region_name and region_name.lower() != name.lower():
            warnings.append(
                f"Target scope is region:{name} but tool call "
                f"references region '{region_name}'"
            )

    return warnings


# =============================================================================
# Batch Validation
# =============================================================================

def validate_tool_calls_batch(
    tool_calls: list[tuple[str, dict[str, Any]]],
    allowed_tools: set[str],
    registry: Optional[EntityRegistry] = None,
) -> list[ValidationResult]:
    """
    Validate a batch of tool calls.
    
    Args:
        tool_calls: List of (tool_name, params) tuples
        allowed_tools: Set of allowed tool names
        registry: Entity registry
        
    Returns:
        List of ValidationResults
    """
    results: list[ValidationResult] = []
    
    for tool_name, params in tool_calls:
        result = validate_tool_call(tool_name, params, allowed_tools, registry)
        results.append(result)
    
    return results


def all_valid(results: list[ValidationResult]) -> bool:
    """Check if all validation results are valid."""
    return all(r.valid for r in results)


def collect_errors(results: list[ValidationResult]) -> list[str]:
    """Collect all error messages from validation results."""
    errors: list[str] = []
    for result in results:
        if not result.valid:
            errors.append(f"{result.tool_name}: {result.error_message}")
    return errors
