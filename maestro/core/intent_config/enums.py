"""SSEState and Intent enums."""

from __future__ import annotations

from enum import Enum


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
