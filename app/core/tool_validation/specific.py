"""Tool-specific validation rules."""

from __future__ import annotations

from typing import Any

from app.core.tool_validation.models import ValidationError
from app.core.tool_validation.constants import (
    VALID_SF_SYMBOL_ICONS,
    NAME_LENGTH_LIMITS,
    AUTOMATION_CANONICAL_PARAMETERS,
)


def _validate_tool_specific(
    tool_name: str,
    params: dict[str, Any],
) -> list[ValidationError]:
    """Run tool-specific validation rules."""
    errors: list[ValidationError] = []

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
        valid_effects = {
            "reverb", "delay", "compressor", "eq", "distortion", "filter",
            "chorus", "modulation", "overdrive", "phaser", "flanger", "tremolo",
        }
        if effect_type and effect_type not in valid_effects:
            errors.append(ValidationError(
                field="type",
                message=(
                    f"Unknown effect type '{effect_type}'. "
                    f"Valid: {', '.join(sorted(valid_effects))}"
                ),
                code="INVALID_EFFECT_TYPE",
            ))

    elif tool_name == "stori_set_track_icon":
        icon = params.get("icon", "")
        if icon and icon not in VALID_SF_SYMBOL_ICONS:
            errors.append(ValidationError(
                field="icon",
                message=(
                    f"Invalid icon '{icon}'. Must be from curated allowlist "
                    "(instrument.* custom icons or approved SF Symbols)"
                ),
                code="INVALID_ICON",
            ))

    elif tool_name == "stori_add_notes":
        _FAKE_PARAMS = {
            "_noteCount", "_beatRange", "_placeholder", "_notes", "_count", "_summary",
        }
        fake_keys = _FAKE_PARAMS.intersection(params.keys())
        notes_in_params = "notes" in params
        notes = params.get("notes", [])

        if fake_keys or not notes_in_params:
            received = {k: v for k, v in params.items() if k != "regionId"}
            errors.append(ValidationError(
                field="notes",
                message=(
                    f"'notes' array is required and must be a real MIDI note array. "
                    f"Shorthand params like {sorted(fake_keys) or list(received.keys())} are not valid. "
                    f"Each element must be: {{\"pitch\": 0-127, \"startBeat\": ≥0, "
                    f"\"durationBeats\": >0, \"velocity\": 1-127}}. "
                    f"Received: {received}"
                ),
                code="MISSING_REQUIRED",
            ))
        elif not isinstance(notes, list):
            errors.append(ValidationError(
                field="notes",
                message="notes must be an array of MIDI note objects",
                code="TYPE_MISMATCH",
            ))
        elif len(notes) == 0:
            errors.append(ValidationError(
                field="notes",
                message=(
                    "notes array cannot be empty. Provide real MIDI note objects: "
                    '[{"pitch": 60, "startBeat": 0, "durationBeats": 1, "velocity": 80}, ...]'
                ),
                code="INVALID_VALUE",
            ))
        else:
            for i, note in enumerate(notes):
                if not isinstance(note, dict):
                    continue
                if "pitch" in note:
                    pitch = note["pitch"]
                    if not isinstance(pitch, int) or pitch < 0 or pitch > 127:
                        errors.append(ValidationError(
                            field=f"notes[{i}].pitch",
                            message=f"Pitch must be 0-127, got {pitch}",
                            code="INVALID_PITCH",
                        ))
                if "velocity" in note:
                    velocity = note["velocity"]
                    if not isinstance(velocity, int) or velocity < 1 or velocity > 127:
                        errors.append(ValidationError(
                            field=f"notes[{i}].velocity",
                            message=f"Velocity must be 1-127, got {velocity}",
                            code="INVALID_VELOCITY",
                        ))
                if "startBeat" in note:
                    start = note["startBeat"]
                    if not isinstance(start, (int, float)) or start < 0:
                        errors.append(ValidationError(
                            field=f"notes[{i}].startBeat",
                            message=f"StartBeat must be >= 0, got {start}",
                            code="INVALID_START",
                        ))
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
                message=(
                    f"Invalid gridSize '{grid_size}'. "
                    "Valid: 0.0625(1/64) 0.125(1/32) 0.25(1/16) 0.5(1/8) 1.0(1/4) 2.0(1/2) 4.0(whole)"
                ),
                code="INVALID_VALUE",
            ))

    elif tool_name == "stori_add_automation":
        if "target" in params and "trackId" not in params:
            errors.append(ValidationError(
                field="trackId",
                message=(
                    "stori_add_automation requires 'trackId' (not 'target'). "
                    "Replace target=... with trackId=..."
                ),
                code="WRONG_PARAM_NAME",
            ))
        parameter = params.get("parameter")
        if not parameter:
            errors.append(ValidationError(
                field="parameter",
                message=(
                    "stori_add_automation requires 'parameter' — the canonical automation "
                    "parameter string. Valid values: 'Volume', 'Pan', 'EQ Low', 'EQ Mid', "
                    "'EQ High', 'Mod Wheel (CC1)', 'Volume (CC7)', 'Pan (CC10)', "
                    "'Expression (CC11)', 'Sustain (CC64)', 'Filter Cutoff (CC74)', "
                    "'Pitch Bend', 'Synth Cutoff', 'Synth Resonance', 'Synth Attack', "
                    "'Synth Release'"
                ),
                code="MISSING_REQUIRED",
            ))
        elif parameter not in AUTOMATION_CANONICAL_PARAMETERS:
            errors.append(ValidationError(
                field="parameter",
                message=(
                    f"'{parameter}' is not a valid automation parameter. "
                    f"Valid values: {sorted(AUTOMATION_CANONICAL_PARAMETERS)}"
                ),
                code="INVALID_VALUE",
            ))

    return errors
