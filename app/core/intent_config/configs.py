"""INTENT_CONFIGS — the single source of truth for intent → configuration mapping."""

from __future__ import annotations

from app.core.intent_config.enums import Intent, SSEState
from app.core.intent_config.models import IntentConfig

# ---------------------------------------------------------------------------
# Shared primitive tool sets
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Intent → Configuration
# ---------------------------------------------------------------------------

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
        description="set zoom level",
    ),

    # Project settings - single action
    Intent.PROJECT_SET_TEMPO: IntentConfig(
        intent=Intent.PROJECT_SET_TEMPO,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_set_tempo"}),
        force_stop_after=True,
        tool_choice="required",
        description="set project tempo",
    ),
    Intent.PROJECT_SET_KEY: IntentConfig(
        intent=Intent.PROJECT_SET_KEY,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_set_key"}),
        force_stop_after=True,
        tool_choice="required",
        description="set project key",
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
        description="set track volume",
    ),
    Intent.TRACK_SET_PAN: IntentConfig(
        intent=Intent.TRACK_SET_PAN,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_set_track_pan"}),
        force_stop_after=True,
        tool_choice="required",
        description="set track pan",
    ),
    Intent.TRACK_SET_COLOR: IntentConfig(
        intent=Intent.TRACK_SET_COLOR,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_set_track_color"}),
        force_stop_after=True,
        tool_choice="required",
        description="set track color",
    ),
    Intent.TRACK_SET_ICON: IntentConfig(
        intent=Intent.TRACK_SET_ICON,
        sse_state=SSEState.EDITING,
        allowed_tools=frozenset({"stori_set_track_icon"}),
        force_stop_after=True,
        tool_choice="required",
        description="set track icon",
    ),

    # Region/Notes - may need multi-step
    Intent.REGION_ADD: IntentConfig(
        intent=Intent.REGION_ADD,
        sse_state=SSEState.EDITING,
        allowed_tools=_PRIMITIVES_REGION,
        force_stop_after=False,
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
