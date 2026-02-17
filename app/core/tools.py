"""
Tool definitions + metadata for the Stori Composer.

This file is intentionally *Cursor-shaped*:

- Tools are classified into:
  * PRIMITIVE (deterministic, reversible, single-mutation)  -> safe for direct LLM use
  * GENERATOR  (creative / stochastic / expensive)          -> planner-gated
  * MACRO      (multi-step convenience)                     -> never directly callable by LLM

- Tools are additionally grouped by tier:
  * Tier 1: server-side generation/execution
  * Tier 2: client-side DAW control (Swift)

The LLM should usually only receive PRIMITIVE tools for EDITING,
and receive a small set of GENERATOR tools for COMPOSING (via planner).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, cast


# =============================================================================
# Metadata model
# =============================================================================

class ToolTier(str, Enum):
    TIER1 = "tier1"  # server-side
    TIER2 = "tier2"  # client-side


class ToolKind(str, Enum):
    PRIMITIVE = "primitive"
    GENERATOR = "generator"
    MACRO = "macro"


@dataclass(frozen=True)
class ToolMeta:
    name: str
    tier: ToolTier
    kind: ToolKind
    # Safety / routing hints:
    creates_entity: Optional[str] = None      # "track" | "region" | "bus" | None
    id_fields: tuple[str, ...] = ()           # e.g. ("trackId",)
    reversible: bool = True
    # Planner gates:
    planner_only: bool = False                # True => never directly exposed to the LLM
    deprecated: bool = False


# =============================================================================
# Tool definitions (OpenAI tool schema)
# =============================================================================

# ---- Tier 1: Generators ------------------------------------------------------

# Cursor principle: prefer 1 general generator tool over N specialized tools.
# Keep older specific generators for backwards compatibility but mark deprecated.
TIER1_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "stori_generate_midi",
            "description": "Generate MIDI for a musical role (drums/bass/chords/melody/etc). Returns MIDI notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "description": "Role: drums, bass, chords, melody, arp, pads, fx"},
                    "style": {"type": "string", "description": "Style tag: boom_bap, trap, house, lofi, jazz, funk, etc"},
                    "tempo": {"type": "integer", "description": "Tempo in BPM"},
                    "bars": {"type": "integer", "description": "Number of bars to generate (1-64)"},
                    "key": {"type": "string", "description": "Key like Cm, F# minor, etc"},
                    "constraints": {
                        "type": "object",
                        "description": "Optional structured constraints (density, syncopation, swing, note_range, etc)",
                    },
                },
                "required": ["role", "style", "tempo", "bars"],
            },
        },
    },
    # Back-compat specialized generators
    {
        "type": "function",
        "function": {
            "name": "stori_generate_drums",
            "description": "Generate a drum pattern using AI. Returns MIDI notes for drums.",
            "parameters": {
                "type": "object",
                "properties": {
                    "style": {"type": "string", "description": "Drum style: boom_bap, trap, house, lofi, jazz"},
                    "tempo": {"type": "integer", "description": "Tempo in BPM"},
                    "bars": {"type": "integer", "description": "Number of bars to generate (1-16)"},
                },
                "required": ["style", "tempo", "bars"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_generate_bass",
            "description": "Generate a bass line using AI. Returns MIDI notes for bass.",
            "parameters": {
                "type": "object",
                "properties": {
                    "style": {"type": "string", "description": "Bass style: boom_bap, trap, house, lofi, funk"},
                    "tempo": {"type": "integer", "description": "Tempo in BPM"},
                    "bars": {"type": "integer", "description": "Number of bars to generate (1-16)"},
                },
                "required": ["style", "tempo", "bars"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_generate_chords",
            "description": "Generate chord progression using AI. Returns MIDI notes for chords.",
            "parameters": {
                "type": "object",
                "properties": {
                    "style": {"type": "string", "description": "Chord style: lofi, jazz, pop, house, trap"},
                    "tempo": {"type": "integer", "description": "Tempo in BPM"},
                    "bars": {"type": "integer", "description": "Number of bars to generate (1-16)"},
                    "key": {"type": "string", "description": "Key like Cm, F# minor"},
                },
                "required": ["style", "tempo", "bars", "key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_generate_melody",
            "description": "Generate a melody using AI. Returns MIDI notes for melody.",
            "parameters": {
                "type": "object",
                "properties": {
                    "style": {"type": "string", "description": "Melody style: lofi, trap, house, jazz, pop"},
                    "tempo": {"type": "integer", "description": "Tempo in BPM"},
                    "bars": {"type": "integer", "description": "Number of bars to generate (1-16)"},
                    "key": {"type": "string", "description": "Key like Cm, F# minor"},
                },
                "required": ["style", "tempo", "bars", "key"],
            },
        },
    },
]


# ---- Tier 2: DAW primitives --------------------------------------------------

# NOTE: Your existing list is long; keeping it intact and adding missing
# "editorial primitives" is a core supercharger.
TIER2_TOOLS: list[dict[str, Any]] = [
    # Project / transport
    {
        "type": "function",
        "function": {
            "name": "stori_create_project",
            "description": "Create a new project.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Project name"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_tempo",
            "description": "Set project tempo in BPM.",
            "parameters": {
                "type": "object",
                "properties": {"tempo": {"type": "integer", "description": "Tempo in BPM"}},
                "required": ["tempo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_key_signature",
            "description": "Set project key signature (e.g. Cm, F# minor).",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string", "description": "Key string"}},
                "required": ["key"],
            },
        },
    },
    {"type": "function", "function": {"name": "stori_play", "description": "Start playback.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "stori_stop", "description": "Stop playback.", "parameters": {"type": "object", "properties": {}}}},
    {
        "type": "function",
        "function": {
            "name": "stori_set_playhead",
            "description": "Move playhead to a bar/beat or absolute time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bar": {"type": "integer"},
                    "beat": {"type": "integer"},
                    "seconds": {"type": "number"},
                },
            },
        },
    },

    # UI
    {
        "type": "function",
        "function": {
            "name": "stori_show_panel",
            "description": "Show or hide a panel (mixer, inspector, piano_roll, etc).",
            "parameters": {
                "type": "object",
                "properties": {
                    "panel": {"type": "string"},
                    "visible": {"type": "boolean"},
                },
                "required": ["panel", "visible"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_zoom",
            "description": "Set editor zoom (percent).",
            "parameters": {
                "type": "object",
                "properties": {"zoomPercent": {"type": "number"}},
                "required": ["zoomPercent"],
            },
        },
    },

    # Tracks
    {
        "type": "function",
        "function": {
            "name": "stori_add_midi_track",
            "description": "Add a new MIDI track with optional MIDI voice/program.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Track name (e.g., 'Acoustic Guitar', 'Bass', 'Piano')"},
                    "instrument": {"type": "string", "description": "Optional instrument name for voice selection"},
                    "gmProgram": {"type": "integer", "description": "GM MIDI program number (0-127). Auto-inferred from name/instrument if not specified."},
                    "color": {"type": "string", "description": "Optional hex color (e.g., '#FF6B6B'). Auto-generated if not specified."},
                    "icon": {"type": "string", "description": "Optional SF Symbol icon name (e.g., 'pianokeys', 'guitars.fill'). Auto-inferred from name if not specified."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_midi_program",
            "description": "Set the MIDI program (instrument voice) for a track. Uses General MIDI standard (0-127).",
            "parameters": {
                "type": "object",
                "properties": {
                    "trackId": {"type": "string", "description": "Track UUID"},
                    "program": {"type": "integer", "description": "GM program number 0-127 (e.g., 0=Piano, 25=Acoustic Guitar, 33=Electric Bass)"},
                    "channel": {"type": "integer", "description": "MIDI channel 1-16 (default: 1, drums should use 10)"},
                },
                "required": ["trackId", "program"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_track_name",
            "description": "Rename a track.",
            "parameters": {"type": "object", "properties": {"trackId": {"type": "string"}, "name": {"type": "string"}}, "required": ["trackId", "name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_mute_track",
            "description": "Mute/unmute a track.",
            "parameters": {"type": "object", "properties": {"trackId": {"type": "string"}, "muted": {"type": "boolean"}}, "required": ["trackId", "muted"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_solo_track",
            "description": "Solo/unsolo a track.",
            "parameters": {"type": "object", "properties": {"trackId": {"type": "string"}, "solo": {"type": "boolean"}}, "required": ["trackId", "solo"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_track_volume",
            "description": "Set track volume in dB.",
            "parameters": {"type": "object", "properties": {"trackId": {"type": "string"}, "volumeDb": {"type": "number"}}, "required": ["trackId", "volumeDb"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_track_pan",
            "description": "Set track pan (-100 left to +100 right).",
            "parameters": {"type": "object", "properties": {"trackId": {"type": "string"}, "pan": {"type": "number"}}, "required": ["trackId", "pan"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_track_color",
            "description": "Set track color.",
            "parameters": {"type": "object", "properties": {"trackId": {"type": "string"}, "color": {"type": "string"}}, "required": ["trackId","color"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_set_track_icon",
            "description": "Set track icon.",
            "parameters": {"type": "object", "properties": {"trackId": {"type": "string"}, "icon": {"type": "string"}}, "required": ["trackId", "icon"]},
        },
    },

    # Regions / Notes editorial primitives (missing in many systems)
    {
        "type": "function",
        "function": {
            "name": "stori_add_midi_region",
            "description": "Create a MIDI region on a track.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trackId": {"type": "string", "description": "Track ID to add the region to"},
                    "startBeat": {"type": "number", "description": "Start position in beats (must be >= 0)"},
                    "durationBeats": {"type": "number", "description": "Region duration in beats (must be > 0)"},
                    "name": {"type": "string", "description": "Optional display name for the region"},
                },
                "required": ["trackId", "startBeat", "durationBeats"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_add_notes",
            "description": "Add MIDI notes into a region. Notes are explicit (pitch/start/duration/velocity).",
            "parameters": {
                "type": "object",
                "properties": {
                    "regionId": {"type": "string"},
                    "notes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "pitch": {"type": "integer"},
                                "startBeats": {"type": "number"},
                                "durationBeats": {"type": "number"},
                                "velocity": {"type": "integer"},
                                "channel": {"type": "integer"},
                            },
                            "required": ["pitch", "startBeats", "durationBeats", "velocity"],
                        },
                    },
                },
                "required": ["regionId", "notes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_clear_notes",
            "description": "Clear notes in current selection or by regionId.",
            "parameters": {"type": "object", "properties": {"regionId": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_quantize_notes",
            "description": "Quantize notes in selection/region.",
            "parameters": {"type": "object", "properties": {"grid": {"type": "string", "description": "1/4, 1/8, 1/16, 1/32"}, "strength": {"type": "number"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_apply_swing",
            "description": "Apply swing to selection/region.",
            "parameters": {"type": "object", "properties": {"amount": {"type": "number", "description": "0..1"}}},
        },
    },

    # FX / routing primitives (keep minimal; complex should be macro planner)
    {
        "type": "function",
        "function": {
            "name": "stori_add_insert_effect",
            "description": "Add an insert effect to a track. Valid effect types: compressor, eq, reverb, delay, chorus, flanger, phaser, distortion, overdrive, limiter, gate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trackId": {"type": "string", "description": "UUID of the track"},
                    "type": {"type": "string", "description": "Effect type: compressor, eq, reverb, delay, chorus, flanger, phaser, distortion, overdrive, limiter, gate"}
                },
                "required": ["trackId", "type"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_add_send",
            "description": "Add a send from track to a bus.",
            "parameters": {"type": "object", "properties": {"trackId": {"type": "string"}, "busId": {"type": "string"}, "levelDb": {"type": "number"}}, "required": ["trackId", "busId"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_ensure_bus",
            "description": "Ensure a named bus exists (create if missing).",
            "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_add_automation",
            "description": "Add automation to a parameter (volume, pan, filter, etc).",
            "parameters": {"type": "object", "properties": {"target": {"type": "string"}, "points": {"type": "array"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_add_midi_cc",
            "description": "Add MIDI CC events.",
            "parameters": {"type": "object", "properties": {"regionId": {"type": "string"}, "cc": {"type": "integer"}, "events": {"type": "array"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stori_add_pitch_bend",
            "description": "Add pitch bend events.",
            "parameters": {"type": "object", "properties": {"regionId": {"type": "string"}, "events": {"type": "array"}}},
        },
    },
]

# All tools (for caching), but the router will select subsets.
ALL_TOOLS = TIER1_TOOLS + TIER2_TOOLS


# =============================================================================
# Metadata registry
# =============================================================================

_TOOL_META: dict[str, ToolMeta] = {}

def _register(meta: ToolMeta) -> None:
    _TOOL_META[meta.name] = meta


def build_tool_registry() -> dict[str, ToolMeta]:
    if _TOOL_META:
        return _TOOL_META

    # Tier 1 generators
    _register(ToolMeta("stori_generate_midi", ToolTier.TIER1, ToolKind.GENERATOR, planner_only=True, reversible=False))
    _register(ToolMeta("stori_generate_drums", ToolTier.TIER1, ToolKind.GENERATOR, planner_only=True, reversible=False, deprecated=True))
    _register(ToolMeta("stori_generate_bass", ToolTier.TIER1, ToolKind.GENERATOR, planner_only=True, reversible=False, deprecated=True))
    _register(ToolMeta("stori_generate_chords", ToolTier.TIER1, ToolKind.GENERATOR, planner_only=True, reversible=False, deprecated=True))
    _register(ToolMeta("stori_generate_melody", ToolTier.TIER1, ToolKind.GENERATOR, planner_only=True, reversible=False, deprecated=True))

    # Tier 2 primitives
    _register(ToolMeta("stori_create_project", ToolTier.TIER2, ToolKind.PRIMITIVE, creates_entity="project", id_fields=("projectId",), reversible=False))
    _register(ToolMeta("stori_set_tempo", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_key_signature", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_play", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_stop", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_playhead", ToolTier.TIER2, ToolKind.PRIMITIVE))

    _register(ToolMeta("stori_show_panel", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_zoom", ToolTier.TIER2, ToolKind.PRIMITIVE))

    _register(ToolMeta("stori_add_midi_track", ToolTier.TIER2, ToolKind.PRIMITIVE, creates_entity="track", id_fields=("trackId",)))
    _register(ToolMeta("stori_set_midi_program", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_track_name", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_mute_track", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_solo_track", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_track_volume", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_track_pan", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_track_color", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_set_track_icon", ToolTier.TIER2, ToolKind.PRIMITIVE))

    _register(ToolMeta("stori_add_midi_region", ToolTier.TIER2, ToolKind.PRIMITIVE, creates_entity="region", id_fields=("regionId",)))
    _register(ToolMeta("stori_add_notes", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_clear_notes", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_quantize_notes", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_apply_swing", ToolTier.TIER2, ToolKind.PRIMITIVE))

    _register(ToolMeta("stori_add_insert_effect", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_add_send", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_ensure_bus", ToolTier.TIER2, ToolKind.PRIMITIVE, creates_entity="bus", id_fields=("busId",)))
    _register(ToolMeta("stori_add_automation", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_add_midi_cc", ToolTier.TIER2, ToolKind.PRIMITIVE))
    _register(ToolMeta("stori_add_pitch_bend", ToolTier.TIER2, ToolKind.PRIMITIVE))

    return _TOOL_META


def get_tool_meta(name: str) -> Optional[ToolMeta]:
    build_tool_registry()
    return _TOOL_META.get(name)


def tools_by_kind(kind: ToolKind) -> list[dict[str, Any]]:
    build_tool_registry()
    allowed = {k for k,v in _TOOL_META.items() if v.kind == kind and not v.planner_only}
    return [t for t in ALL_TOOLS if t["function"]["name"] in allowed]


def tool_schema_by_name(name: str) -> Optional[dict[str, Any]]:
    for t in ALL_TOOLS:
        if t["function"]["name"] == name:
            return cast(dict[str, Any], t)
    return None
