"""
Centralized Intent Configuration for Stori Maestro (Cursor-of-DAWs).

This is the SINGLE SOURCE OF TRUTH for:
1. Intent → Allowed Tools mapping
2. Intent → SSE State routing
3. Intent → Execution policy (force_stop, tool_choice)

No more scattered mappings across intent.py, maestro.py, etc.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, FrozenSet

from app.core.tools import ToolKind, build_tool_registry


class SSEState(str, Enum):
    """SSE state for frontend UI."""
    REASONING = "reasoning"
    EDITING = "editing"
    COMPOSING = "composing"


class Intent(str, Enum):
    """All recognized intents."""
    # Transport
    PLAY = "transport.play"
    STOP = "transport.stop"
    SEEK = "transport.seek"
    
    # UI
    UI_SHOW_PANEL = "ui.show_panel"
    UI_SET_ZOOM = "ui.set_zoom"
    
    # Project
    PROJECT_SET_TEMPO = "project.set_tempo"
    PROJECT_SET_KEY = "project.set_key"
    
    # Track Operations
    TRACK_ADD = "track.add"
    TRACK_RENAME = "track.rename"
    TRACK_MUTE = "track.mute"
    TRACK_SOLO = "track.solo"
    TRACK_SET_VOLUME = "track.set_volume"
    TRACK_SET_PAN = "track.set_pan"
    TRACK_SET_COLOR = "track.set_color"
    TRACK_SET_ICON = "track.set_icon"
    
    # Region/Notes Operations
    REGION_ADD = "region.add"
    NOTES_ADD = "notes.add"
    NOTES_CLEAR = "notes.clear"
    NOTES_QUANTIZE = "notes.quantize"
    NOTES_SWING = "notes.swing"
    
    # Effects/Routing
    FX_ADD_INSERT = "fx.add_insert"
    ROUTE_CREATE_BUS = "route.create_bus"
    ROUTE_ADD_SEND = "route.add_send"
    
    # Automation
    AUTOMATION_ADD = "automation.add"
    MIDI_CC_ADD = "midi_cc.add"
    PITCH_BEND_ADD = "pitch_bend.add"
    AFTERTOUCH_ADD = "aftertouch.add"
    
    # Producer Idioms (high-level mixing)
    MIX_TONALITY = "mix.tonality"
    MIX_DYNAMICS = "mix.dynamics"
    MIX_SPACE = "mix.space"
    MIX_ENERGY = "mix.energy"
    
    # Composition
    GENERATE_MUSIC = "compose.generate_music"
    
    # Questions
    ASK_STORI_DOCS = "ask.stori_docs"
    ASK_GENERAL = "ask.general"
    
    # Control
    NEEDS_CLARIFICATION = "control.needs_clarification"
    UNKNOWN = "control.unknown"


@dataclass(frozen=True)
class IntentConfig:
    """Configuration for an intent."""
    intent: Intent
    sse_state: SSEState
    allowed_tools: FrozenSet[str]
    force_stop_after: bool = True  # Stop after first tool call
    tool_choice: str = "required"  # "required", "auto", or "none"
    requires_planner: bool = False  # Route through planner instead of direct LLM
    description: str = ""


# =============================================================================
# The Single Source of Truth: Intent → Configuration
# =============================================================================

_PRIMITIVES_MIXING = frozenset({
    "stori_add_insert_effect",
    "stori_set_track_volume",
    "stori_set_track_pan",
    "stori_add_send",
    "stori_ensure_bus",
    "stori_add_automation",
    "stori_add_midi_cc",
    "stori_add_pitch_bend",
    "stori_add_aftertouch",
})

_PRIMITIVES_TRACK = frozenset({
    "stori_add_midi_track",
    "stori_set_midi_program",
    "stori_set_track_name",
    "stori_mute_track",
    "stori_solo_track",
    "stori_set_track_volume",
    "stori_set_track_pan",
    "stori_set_track_color",
    "stori_set_track_icon",
})

_PRIMITIVES_REGION = frozenset({
    "stori_add_midi_region",
    "stori_add_notes",
    "stori_clear_notes",
    "stori_quantize_notes",
    "stori_apply_swing",
})

_PRIMITIVES_FX = frozenset({
    "stori_add_insert_effect",
    "stori_add_send",
    "stori_ensure_bus",
})


INTENT_CONFIGS: dict[Intent, IntentConfig] = {
    # Transport - single action, stop immediately
    Intent.PLAY: IntentConfig(
        intent=Intent.PLAY,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_play"}),
        force_stop_after=True,
        tool_choice="required",
        description="Start playback",
    ),
    Intent.STOP: IntentConfig(
        intent=Intent.STOP,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_stop"}),
        force_stop_after=True,
        tool_choice="required",
        description="Stop playback",
    ),
    Intent.SEEK: IntentConfig(
        intent=Intent.SEEK,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_set_playhead"}),
        force_stop_after=True,
        tool_choice="required",
        description="Move playhead",
    ),
    
    # UI - single action
    Intent.UI_SHOW_PANEL: IntentConfig(
        intent=Intent.UI_SHOW_PANEL,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_show_panel"}),
        force_stop_after=True,
        tool_choice="required",
        description="Show/hide panel",
    ),
    Intent.UI_SET_ZOOM: IntentConfig(
        intent=Intent.UI_SET_ZOOM,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_set_zoom"}),
        force_stop_after=True,
        tool_choice="required",
        description="Set zoom level",
    ),
    
    # Project settings - single action
    Intent.PROJECT_SET_TEMPO: IntentConfig(
        intent=Intent.PROJECT_SET_TEMPO,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_set_tempo"}),
        force_stop_after=True,
        tool_choice="required",
        description="Set project tempo",
    ),
    Intent.PROJECT_SET_KEY: IntentConfig(
        intent=Intent.PROJECT_SET_KEY,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_set_key"}),
        force_stop_after=True,
        tool_choice="required",
        description="Set project key",
    ),
    
    # Track operations
    Intent.TRACK_ADD: IntentConfig(
        intent=Intent.TRACK_ADD,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_add_midi_track"}),
        force_stop_after=True,
        tool_choice="required",
        description="Add new track",
    ),
    Intent.TRACK_RENAME: IntentConfig(
        intent=Intent.TRACK_RENAME,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_set_track_name"}),
        force_stop_after=True,
        tool_choice="required",
        description="Rename track",
    ),
    Intent.TRACK_MUTE: IntentConfig(
        intent=Intent.TRACK_MUTE,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_mute_track"}),
        force_stop_after=True,
        tool_choice="required",
        description="Mute/unmute track",
    ),
    Intent.TRACK_SOLO: IntentConfig(
        intent=Intent.TRACK_SOLO,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_solo_track"}),
        force_stop_after=True,
        tool_choice="required",
        description="Solo/unsolo track",
    ),
    Intent.TRACK_SET_VOLUME: IntentConfig(
        intent=Intent.TRACK_SET_VOLUME,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_set_track_volume"}),
        force_stop_after=True,
        tool_choice="required",
        description="Set track volume",
    ),
    Intent.TRACK_SET_PAN: IntentConfig(
        intent=Intent.TRACK_SET_PAN,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_set_track_pan"}),
        force_stop_after=True,
        tool_choice="required",
        description="Set track pan",
    ),
    Intent.TRACK_SET_COLOR: IntentConfig(
        intent=Intent.TRACK_SET_COLOR,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_set_track_color"}),
        force_stop_after=True,
        tool_choice="required",
        description="Set track color",
    ),
    Intent.TRACK_SET_ICON: IntentConfig(
        intent=Intent.TRACK_SET_ICON,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_set_track_icon"}),
        force_stop_after=True,
        tool_choice="required",
        description="Set track icon",
    ),
    
    # Region/Notes - may need multi-step
    Intent.REGION_ADD: IntentConfig(
        intent=Intent.REGION_ADD,
        sse_state=SSEState.EDITING,
        allowed_tools=_PRIMITIVES_REGION,
        force_stop_after=False,  # May need multiple operations
        tool_choice="auto",
        description="Add region or notes",
    ),
    Intent.NOTES_ADD: IntentConfig(
        intent=Intent.NOTES_ADD,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_add_notes", "stori_add_midi_region"}),
        force_stop_after=False,
        tool_choice="auto",
        description="Add MIDI notes",
    ),
    Intent.NOTES_CLEAR: IntentConfig(
        intent=Intent.NOTES_CLEAR,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_clear_notes"}),
        force_stop_after=True,
        tool_choice="required",
        description="Clear notes",
    ),
    Intent.NOTES_QUANTIZE: IntentConfig(
        intent=Intent.NOTES_QUANTIZE,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_quantize_notes"}),
        force_stop_after=True,
        tool_choice="required",
        description="Quantize notes",
    ),
    Intent.NOTES_SWING: IntentConfig(
        intent=Intent.NOTES_SWING,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_apply_swing"}),
        force_stop_after=True,
        tool_choice="required",
        description="Apply swing",
    ),
    
    # Effects/Routing - may chain
    Intent.FX_ADD_INSERT: IntentConfig(
        intent=Intent.FX_ADD_INSERT,
        sse_state=SSEState.EDITING,
        allowed_tools=_PRIMITIVES_FX,
        force_stop_after=False,  # "Add reverb and delay" = 2 calls
        tool_choice="auto",
        description="Add effect",
    ),
    Intent.ROUTE_CREATE_BUS: IntentConfig(
        intent=Intent.ROUTE_CREATE_BUS,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_ensure_bus"}),
        force_stop_after=True,
        tool_choice="required",
        description="Create bus",
    ),
    Intent.ROUTE_ADD_SEND: IntentConfig(
        intent=Intent.ROUTE_ADD_SEND,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_add_send", "stori_ensure_bus"}),
        force_stop_after=False,
        tool_choice="auto",
        description="Add send",
    ),
    
    # Automation
    Intent.AUTOMATION_ADD: IntentConfig(
        intent=Intent.AUTOMATION_ADD,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_add_automation"}),
        force_stop_after=True,
        tool_choice="required",
        description="Add automation",
    ),
    Intent.MIDI_CC_ADD: IntentConfig(
        intent=Intent.MIDI_CC_ADD,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_add_midi_cc"}),
        force_stop_after=True,
        tool_choice="required",
        description="Add MIDI CC",
    ),
    Intent.PITCH_BEND_ADD: IntentConfig(
        intent=Intent.PITCH_BEND_ADD,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_add_pitch_bend"}),
        force_stop_after=True,
        tool_choice="required",
        description="Add pitch bend",
    ),
    Intent.AFTERTOUCH_ADD: IntentConfig(
        intent=Intent.AFTERTOUCH_ADD,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_add_aftertouch"}),
        force_stop_after=True,
        tool_choice="required",
        description="Add aftertouch",
    ),
    
    # Producer idioms - mixing primitives
    Intent.MIX_TONALITY: IntentConfig(
        intent=Intent.MIX_TONALITY,
        sse_state=SSEState.EDITING,
        allowed_tools=_PRIMITIVES_MIXING,
        force_stop_after=False,
        tool_choice="required",
        description="Adjust tonality (darker/brighter)",
    ),
    Intent.MIX_DYNAMICS: IntentConfig(
        intent=Intent.MIX_DYNAMICS,
        sse_state=SSEState.EDITING,
        allowed_tools=_PRIMITIVES_MIXING,
        force_stop_after=False,
        tool_choice="required",
        description="Adjust dynamics (punchier/tighter)",
    ),
    Intent.MIX_SPACE: IntentConfig(
        intent=Intent.MIX_SPACE,
        sse_state=SSEState.EDITING,
        allowed_tools=_PRIMITIVES_MIXING,
        force_stop_after=False,
        tool_choice="required",
        description="Adjust space (wider/closer)",
    ),
    Intent.MIX_ENERGY: IntentConfig(
        intent=Intent.MIX_ENERGY,
        sse_state=SSEState.EDITING,
        allowed_tools=_PRIMITIVES_MIXING,
        force_stop_after=False,
        tool_choice="required",
        description="Adjust energy (more movement)",
    ),
    
    # Composition - planner path
    Intent.GENERATE_MUSIC: IntentConfig(
        intent=Intent.GENERATE_MUSIC,
        sse_state=SSEState.COMPOSING,
        allowed_tools=frozenset(),  # Planner handles
        force_stop_after=True,
        tool_choice="auto",
        requires_planner=True,
        description="Generate music",
    ),
    
    # Questions - no tools
    Intent.ASK_STORI_DOCS: IntentConfig(
        intent=Intent.ASK_STORI_DOCS,
        sse_state=SSEState.REASONING,
        allowed_tools=frozenset(),
        force_stop_after=True,
        tool_choice="none",
        description="Stori documentation question",
    ),
    Intent.ASK_GENERAL: IntentConfig(
        intent=Intent.ASK_GENERAL,
        sse_state=SSEState.REASONING,
        allowed_tools=frozenset(),
        force_stop_after=True,
        tool_choice="none",
        description="General question",
    ),
    
    # Control
    Intent.NEEDS_CLARIFICATION: IntentConfig(
        intent=Intent.NEEDS_CLARIFICATION,
        sse_state=SSEState.REASONING,
        allowed_tools=frozenset(),
        force_stop_after=True,
        tool_choice="none",
        description="Request needs clarification",
    ),
    Intent.UNKNOWN: IntentConfig(
        intent=Intent.UNKNOWN,
        sse_state=SSEState.REASONING,
        allowed_tools=frozenset(),
        force_stop_after=True,
        tool_choice="auto",
        description="Unknown intent",
    ),
}


def get_intent_config(intent: Intent) -> IntentConfig:
    """Get configuration for an intent."""
    return INTENT_CONFIGS.get(intent, INTENT_CONFIGS[Intent.UNKNOWN])


def get_allowed_tools_for_intent(intent: Intent) -> frozenset[str]:
    """Get allowed tool names for an intent."""
    config = get_intent_config(intent)
    return config.allowed_tools


def get_sse_state_for_intent(intent: Intent) -> SSEState:
    """Get SSE state for an intent."""
    config = get_intent_config(intent)
    return config.sse_state


# =============================================================================
# Producer Idioms Lexicon with Polarity
# =============================================================================

@dataclass(frozen=True)
class IdiomMatch:
    """A matched producer idiom with direction and optional weight."""
    intent: Intent
    phrase: str
    direction: str  # "increase", "decrease", "add", "remove"
    target: Optional[str] = None  # e.g., "highs", "lows", "width"
    suggested_tools: FrozenSet[str] = frozenset()
    weight: int = 1  # 1-5 scale from structured prompt Vibe weights


PRODUCER_IDIOMS: dict[str, IdiomMatch] = {
    # Tonality
    "darker": IdiomMatch(
        Intent.MIX_TONALITY, "darker", "decrease", "highs",
        frozenset({"stori_add_insert_effect"})
    ),
    "brighter": IdiomMatch(
        Intent.MIX_TONALITY, "brighter", "increase", "highs",
        frozenset({"stori_add_insert_effect"})
    ),
    "warmer": IdiomMatch(
        Intent.MIX_TONALITY, "warmer", "increase", "low_mids",
        frozenset({"stori_add_insert_effect"})
    ),
    "colder": IdiomMatch(
        Intent.MIX_TONALITY, "colder", "decrease", "low_mids",
        frozenset({"stori_add_insert_effect"})
    ),
    "too bright": IdiomMatch(
        Intent.MIX_TONALITY, "too bright", "decrease", "highs",
        frozenset({"stori_add_insert_effect"})
    ),
    "too dark": IdiomMatch(
        Intent.MIX_TONALITY, "too dark", "increase", "highs",
        frozenset({"stori_add_insert_effect"})
    ),
    
    # Dynamics
    "punchier": IdiomMatch(
        Intent.MIX_DYNAMICS, "punchier", "increase", "attack",
        frozenset({"stori_add_insert_effect"})
    ),
    "more punch": IdiomMatch(
        Intent.MIX_DYNAMICS, "more punch", "increase", "attack",
        frozenset({"stori_add_insert_effect"})
    ),
    "tighter": IdiomMatch(
        Intent.MIX_DYNAMICS, "tighter", "decrease", "release",
        frozenset({"stori_add_insert_effect"})
    ),
    "fatter": IdiomMatch(
        Intent.MIX_DYNAMICS, "fatter", "increase", "saturation",
        frozenset({"stori_add_insert_effect"})
    ),
    "thicker": IdiomMatch(
        Intent.MIX_DYNAMICS, "thicker", "increase", "lows",
        frozenset({"stori_add_insert_effect"})
    ),
    "less muddy": IdiomMatch(
        Intent.MIX_DYNAMICS, "less muddy", "decrease", "low_mids",
        frozenset({"stori_add_insert_effect"})
    ),
    
    # Space
    "wider": IdiomMatch(
        Intent.MIX_SPACE, "wider", "increase", "stereo_width",
        frozenset({"stori_add_insert_effect", "stori_set_track_pan"})
    ),
    "bigger": IdiomMatch(
        Intent.MIX_SPACE, "bigger", "increase", "reverb",
        frozenset({"stori_add_insert_effect", "stori_add_send"})
    ),
    "more space": IdiomMatch(
        Intent.MIX_SPACE, "more space", "increase", "reverb",
        frozenset({"stori_add_insert_effect", "stori_add_send"})
    ),
    "more depth": IdiomMatch(
        Intent.MIX_SPACE, "more depth", "increase", "delay",
        frozenset({"stori_add_insert_effect", "stori_add_send"})
    ),
    "closer": IdiomMatch(
        Intent.MIX_SPACE, "closer", "decrease", "reverb",
        frozenset({"stori_add_insert_effect"})
    ),
    "more intimate": IdiomMatch(
        Intent.MIX_SPACE, "more intimate", "decrease", "reverb",
        frozenset({"stori_add_insert_effect"})
    ),
    
    # Energy
    "more energy": IdiomMatch(
        Intent.MIX_ENERGY, "more energy", "increase", "dynamics",
        frozenset({"stori_add_insert_effect"})
    ),
    "more movement": IdiomMatch(
        Intent.MIX_ENERGY, "more movement", "add", "modulation",
        frozenset({"stori_add_automation", "stori_add_insert_effect"})
    ),
    "add life": IdiomMatch(
        Intent.MIX_ENERGY, "add life", "add", "variation",
        frozenset({"stori_add_automation"})
    ),
    "too static": IdiomMatch(
        Intent.MIX_ENERGY, "too static", "add", "modulation",
        frozenset({"stori_add_automation", "stori_add_insert_effect"})
    ),
    "boring": IdiomMatch(
        Intent.MIX_ENERGY, "boring", "add", "variation",
        frozenset({"stori_add_automation"})
    ),
}


def match_producer_idiom(text: str) -> Optional[IdiomMatch]:
    """
    Match producer idiom in text with polarity.
    
    Returns:
        IdiomMatch with intent, direction, target, and suggested tools
        or None if no match
    """
    text_lower = text.lower()
    
    # Direct phrase match
    for phrase, match in PRODUCER_IDIOMS.items():
        if phrase in text_lower:
            return match
    
    return None


def match_weighted_vibes(
    vibes: list[tuple[str, int]],
) -> list[IdiomMatch]:
    """
    Match a list of weighted vibes from a structured prompt against the idiom lexicon.

    Args:
        vibes: List of (vibe_text, weight) tuples from ParsedPrompt.vibes

    Returns:
        List of IdiomMatch objects with weights set, sorted by weight descending.
        Unknown vibes are silently skipped.
    """
    matches: list[IdiomMatch] = []
    for vibe_text, weight in vibes:
        idiom = match_producer_idiom(vibe_text)
        if idiom:
            # Create a new IdiomMatch with the user's weight
            weighted = IdiomMatch(
                intent=idiom.intent,
                phrase=idiom.phrase,
                direction=idiom.direction,
                target=idiom.target,
                suggested_tools=idiom.suggested_tools,
                weight=weight,
            )
            matches.append(weighted)
    matches.sort(key=lambda m: m.weight, reverse=True)
    return matches
