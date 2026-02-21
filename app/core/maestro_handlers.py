"""
Orchestration and request handlers for Maestro (Cursor-of-DAWs).

This module contains the main orchestrate() flow and the three handlers
(REASONING, COMPOSING, EDITING). The API route layer imports orchestrate
and UsageTracker from here so the route file stays thin.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid as _uuid_mod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, cast

from app.config import settings
from app.core.entity_context import build_entity_context_for_llm, format_project_context
from app.core.expansion import ToolCall
from app.core.intent import (
    Intent,
    IntentResult,
    SSEState,
    get_intent_result_with_llm,
)
from app.core.intent_config import (
    _PRIMITIVES_FX,
    _PRIMITIVES_MIXING,
    _PRIMITIVES_REGION,
    _PRIMITIVES_TRACK,
)
from app.core.llm_client import (
    LLMClient,
    LLMResponse,
    enforce_single_tool,
)
from app.core.pipeline import run_pipeline
from app.core.planner import build_execution_plan_stream, ExecutionPlan
from app.core.prompt_parser import ParsedPrompt
from app.core.prompts import (
    editing_composition_prompt,
    editing_prompt,
    resolve_position,
    sequential_context,
    structured_prompt_context,
    system_prompt_base,
    wrap_user_request,
)
from app.core.sse_utils import ReasoningBuffer, sanitize_reasoning, sse_event, strip_tool_echoes
from app.core.gm_instruments import DRUM_ICON, icon_for_gm_program
from app.core.state_store import StateStore, get_or_create_store
from app.core.tool_validation import validate_tool_call
from app.core.tools import ALL_TOOLS
from app.core.tracing import (
    clear_trace_context,
    create_trace_context,
    log_intent,
    log_llm_call,
    log_tool_call,
    log_validation_error,
    trace_span,
)
from app.services.budget import get_model_or_default

logger = logging.getLogger(__name__)


@dataclass
class StreamFinalResponse:
    """Sentinel yielded by _stream_llm_response when the LLM stream is done. Carry the final LLMResponse."""
    response: LLMResponse


@dataclass
class UsageTracker:
    """Tracks token usage across LLM calls."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Full input tokens from the most recent LLM call.  Each call in an agentic
    # loop sends the entire conversation history, so the last call's input_tokens
    # is the best proxy for current context-window occupancy.
    last_input_tokens: int = 0

    def add(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.last_input_tokens = prompt  # snapshot of context window at this call


def _context_usage_fields(
    usage_tracker: Optional["UsageTracker"], model: str
) -> dict[str, int]:
    """Return inputTokens / contextWindowTokens for SSE complete events."""
    from app.config import get_context_window_tokens
    return {
        "inputTokens": usage_tracker.last_input_tokens if usage_tracker else 0,
        "contextWindowTokens": get_context_window_tokens(model),
    }


# Tools that create new entities. The tool result for these always includes
# the server-assigned ID(s) plus a full entity manifest so the LLM has a
# current picture of the project after every creation.
_ENTITY_CREATING_TOOLS: set[str] = {
    "stori_add_midi_track",
    "stori_add_midi_region",
    "stori_ensure_bus",
    "stori_duplicate_region",
}

# Which ID fields to echo back per entity-creating tool.
_ENTITY_ID_ECHO: dict[str, list[str]] = {
    "stori_add_midi_track":  ["trackId"],
    "stori_add_midi_region": ["regionId", "trackId"],
    "stori_ensure_bus":      ["busId"],
    "stori_duplicate_region": ["newRegionId", "regionId"],
}


_VAR_REF_RE = re.compile(r"^\$(\d+)\.(\w+)$")


def _humanize_style(style: str) -> str:
    """Convert machine-formatted style slugs to human-readable labels.

    'progressive_rock,_pink_floyd' → 'Progressive Rock · Pink Floyd'
    """
    s = style.replace("_", " ").strip()
    parts = [p.strip().title() for p in s.split(",") if p.strip()]
    return " · ".join(parts) if parts else s


def _enrich_params_with_track_context(
    params: dict[str, Any],
    store: Any,
) -> dict[str, Any]:
    """Inject trackName/trackId into SSE toolCall params for region-scoped tools.

    Tools such as stori_add_midi_cc, stori_add_pitch_bend, stori_quantize_notes,
    etc. receive only a regionId and carry no track context.  The Swift frontend
    uses trackName to humanise feed entries; without it the label falls back to a
    generic string.  We resolve the parent track at emit time and append the two
    supplementary fields.

    Rules:
    - Skip if trackName or trackId is already present (track-scoped tools).
    - Skip if regionId is absent.
    - Graceful fallback: if the region or track cannot be resolved, return params
      unchanged — never raise.
    """
    if "trackName" in params or "trackId" in params:
        return params
    region_id = params.get("regionId")
    if not region_id:
        return params
    try:
        track_id = store.get_region_track_id(region_id)
        if not track_id:
            return params
        track_name = store.get_track_name(track_id)
        if not track_name:
            return params
        return {**params, "trackId": track_id, "trackName": track_name}
    except Exception:
        return params


def _human_label_for_tool(name: str, args: dict[str, Any]) -> str:
    """Return a short, musician-friendly description of a tool call.

    Used in progress/toolStart SSE events and as the label for plan steps.
    Labels follow canonical patterns that ExecutionTimelineView uses for
    per-track grouping (see the Execution Timeline UI spec).
    """
    track = args.get("trackName") or args.get("name") or ""
    match name:
        case "stori_set_tempo":
            return f"Set tempo to {args.get('tempo', '?')} BPM"
        case "stori_set_key":
            return f"Set key signature to {args.get('key', '?')}"
        case "stori_add_midi_track":
            return f"Create {args.get('name', 'track')} track"
        case "stori_add_midi_region":
            region_name = args.get("name", "region")
            return f"Creating region: {region_name}"
        case "stori_add_notes":
            n = len(args.get("notes") or [])
            suffix = f" to {track}" if track else ""
            return f"Add notes{suffix}" if not n else f"Add {n} notes{suffix}"
        case "stori_clear_notes":
            return "Clearing notes"
        case "stori_quantize_notes":
            return f"Quantizing to {args.get('grid', '1/16')}"
        case "stori_apply_swing":
            return "Applying swing"
        case "stori_generate_midi":
            role = args.get("role", "part")
            style = _humanize_style(args.get("style", ""))
            bars = args.get("bars", "")
            tname = args.get("trackName") or role.capitalize()
            detail = f"{style} {role}" + (f", {bars} bars" if bars else "")
            return f"Add content to {tname}"
        case "stori_generate_drums":
            tname = args.get("trackName") or "Drums"
            return f"Add content to {tname}"
        case "stori_generate_bass":
            tname = args.get("trackName") or "Bass"
            return f"Add content to {tname}"
        case "stori_generate_melody":
            tname = args.get("trackName") or "Melody"
            return f"Add content to {tname}"
        case "stori_generate_chords":
            tname = args.get("trackName") or "Chords"
            return f"Add content to {tname}"
        case "stori_add_insert_effect":
            etype = args.get("type", "effect")
            if track:
                return f"Add effects to {track}"
            return f"Adding {etype}"
        case "stori_ensure_bus":
            return f"Set up shared {args.get('name', 'Bus')} bus"
        case "stori_add_send":
            if track:
                return f"Add effects for {track}"
            return "Adding send"
        case "stori_add_midi_cc":
            if track:
                return f"Add MIDI CC to {track}"
            return "Add MIDI CC"
        case "stori_add_pitch_bend":
            if track:
                return f"Add pitch bend to {track}"
            return "Add pitch bend"
        case "stori_add_automation":
            if track:
                return f"Write automation for {track}"
            return "Write automation"
        case "stori_set_track_volume":
            return "Adjusting volume"
        case "stori_set_track_pan":
            return "Adjusting pan"
        case "stori_mute_track":
            return "Muting track" if args.get("muted") else "Unmuting track"
        case "stori_move_region":
            return "Moving region"
        case "stori_duplicate_region":
            return "Duplicating region"
        case "stori_delete_region":
            return "Deleting region"
        case "stori_play":
            return "Playing"
        case "stori_stop":
            return "Stopping"
        case _:
            label = name.removeprefix("stori_").replace("_", " ")
            return label.capitalize()


def _resolve_variable_refs(
    params: dict[str, Any],
    prior_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve $N.field variable references in tool params.

    Lets the LLM reference the output of an earlier tool call in the same
    batch without guessing IDs. E.g. stori_add_notes(regionId="$1.regionId")
    uses the regionId returned by the second tool call this turn.
    """
    if not prior_results:
        return params
    resolved = {}
    for key, value in params.items():
        if isinstance(value, str):
            m = _VAR_REF_RE.match(value)
            if m:
                idx, field = int(m.group(1)), m.group(2)
                if 0 <= idx < len(prior_results):
                    substituted = prior_results[idx].get(field)
                    if substituted is not None:
                        resolved[key] = substituted
                        continue
        resolved[key] = value
    return resolved


def _entity_manifest(store: Any) -> dict[str, Any]:
    """Return a compact entity listing so the LLM always knows current IDs.

    Included in every entity-creating tool result AND injected between
    iterations so the LLM never has to guess UUIDs or rely on stale state.

    Each region includes noteCount so the model knows whether notes have
    already been added — preventing destructive clear-and-redo loops.
    """
    tracks = []
    for track in store.registry.list_tracks():
        regions = []
        for r in store.registry.get_track_regions(track.id):
            region_info: dict[str, Any] = {
                "name": r.name,
                "regionId": r.id,
                "noteCount": len(store.get_region_notes(r.id)),
            }
            if r.metadata:
                region_info["startBeat"] = r.metadata.get("startBeat", 0)
                region_info["durationBeats"] = r.metadata.get("durationBeats", 0)
            regions.append(region_info)
        tracks.append({"name": track.name, "trackId": track.id, "regions": regions})
    buses = [{"name": b.name, "busId": b.id} for b in store.registry.list_buses()]
    return {"tracks": tracks, "buses": buses}


def _build_tool_result(
    tool_name: str,
    params: dict[str, Any],
    store: Any,
) -> dict[str, Any]:
    """Build a rich tool result with state feedback for the LLM.

    Every result includes enough context for the model to know exactly
    what was created/modified — preventing ID-loss loops and duplicate adds.

    Entity-creating tools: echo server-assigned IDs + full entity manifest.
    stori_add_notes: confirm notesAdded + totalNotes in the region.
    stori_clear_notes: confirm the region was cleared.
    All other tools: basic success + entity manifest for ID continuity.
    """
    result: dict[str, Any] = {"success": True}

    if tool_name in _ENTITY_CREATING_TOOLS:
        for id_field in _ENTITY_ID_ECHO.get(tool_name, []):
            if id_field in params:
                result[id_field] = params[id_field]

        # Echo additional context for region-creating tools
        if tool_name == "stori_add_midi_region":
            result["startBeat"] = params.get("startBeat", 0)
            result["durationBeats"] = params.get("durationBeats", 16)
            result["name"] = params.get("name", "Region")

        # Echo track metadata for track-creating tools
        elif tool_name == "stori_add_midi_track":
            result["name"] = params.get("name", "Track")
            if params.get("gmProgram") is not None:
                result["gmProgram"] = params["gmProgram"]
            if params.get("drumKitId"):
                result["drumKitId"] = params["drumKitId"]
            if params.get("_gmInstrumentName"):
                result["instrumentName"] = params["_gmInstrumentName"]

        elif tool_name == "stori_ensure_bus":
            result["name"] = params.get("name", "Bus")

        result["entities"] = _entity_manifest(store)

    elif tool_name == "stori_add_notes":
        region_id = params.get("regionId", "")
        notes = params.get("notes", [])
        result["regionId"] = region_id
        result["notesAdded"] = len(notes)
        total_notes = len(store.get_region_notes(region_id)) if region_id else 0
        result["totalNotes"] = total_notes
        result["entities"] = _entity_manifest(store)

    elif tool_name == "stori_clear_notes":
        region_id = params.get("regionId", "")
        result["regionId"] = region_id
        result["totalNotes"] = 0
        result["warning"] = (
            "Region cleared. If you intended to replace notes, "
            "call stori_add_notes with the new notes now."
        )

    elif tool_name == "stori_add_insert_effect":
        result["trackId"] = params.get("trackId", "")
        result["effectType"] = params.get("type", "")

    elif tool_name == "stori_add_send":
        result["trackId"] = params.get("trackId", "")
        result["busId"] = params.get("busId", "")

    elif tool_name == "stori_add_automation":
        result["trackId"] = params.get("trackId", "")
        result["parameter"] = params.get("parameter", "")
        result["pointCount"] = len(params.get("points", []))

    elif tool_name == "stori_add_midi_cc":
        result["regionId"] = params.get("regionId", "")
        result["cc"] = params.get("cc")
        result["eventCount"] = len(params.get("events", []))

    elif tool_name == "stori_add_pitch_bend":
        result["regionId"] = params.get("regionId", "")
        result["eventCount"] = len(params.get("events", []))

    elif tool_name in ("stori_set_track_volume", "stori_set_track_pan",
                        "stori_mute_track", "stori_solo_track",
                        "stori_set_track_color", "stori_set_track_icon",
                        "stori_set_track_name", "stori_set_midi_program"):
        result["trackId"] = params.get("trackId", "")

    return result


# =========================================================================
# Plan Tracker — structured plan events for EDITING sessions
# =========================================================================

_SETUP_TOOL_NAMES: set[str] = {
    "stori_set_tempo", "stori_set_key",
}
_EFFECT_TOOL_NAMES: set[str] = {
    "stori_add_insert_effect",
    "stori_ensure_bus", "stori_add_send",
}
_MIXING_TOOL_NAMES: set[str] = {
    "stori_set_track_volume", "stori_set_track_pan",
    "stori_mute_track", "stori_solo_track",
    "stori_set_track_color", "stori_set_track_icon",
    "stori_set_track_name",
}
_TRACK_CREATION_NAMES: set[str] = {
    "stori_add_midi_track",
}
_CONTENT_TOOL_NAMES: set[str] = {
    "stori_add_midi_region", "stori_add_notes",
}
_EXPRESSIVE_TOOL_NAMES: set[str] = {
    "stori_add_midi_cc", "stori_add_pitch_bend", "stori_add_automation",
}
_GENERATOR_TOOL_NAMES: set[str] = {
    "stori_generate_midi", "stori_generate_drums", "stori_generate_bass",
    "stori_generate_melody", "stori_generate_chords",
}

# Tools whose track association can be determined from params
_TRACK_BOUND_TOOL_NAMES: set[str] = (
    _TRACK_CREATION_NAMES | _CONTENT_TOOL_NAMES | _EFFECT_TOOL_NAMES
    | _EXPRESSIVE_TOOL_NAMES | _GENERATOR_TOOL_NAMES | _MIXING_TOOL_NAMES
)

# Agent Teams — tools each instrument agent may call (no setup/mixing tools)
_INSTRUMENT_AGENT_TOOLS: frozenset[str] = frozenset({
    "stori_add_midi_track",
    "stori_add_midi_region",
    "stori_add_notes",
    "stori_generate_midi",
    "stori_generate_drums",
    "stori_generate_bass",
    "stori_generate_melody",
    "stori_generate_chords",
    "stori_add_insert_effect",
    "stori_add_midi_cc",
    "stori_add_pitch_bend",
    "stori_apply_swing",
    "stori_quantize_notes",
    "stori_set_track_icon",
    "stori_set_track_color",
})

# Agent Teams — tools the Phase 3 mixing coordinator may call
_AGENT_TEAM_PHASE3_TOOLS: frozenset[str] = frozenset({
    "stori_ensure_bus",
    "stori_add_send",
    "stori_set_track_volume",
    "stori_set_track_pan",
    "stori_mute_track",
    "stori_solo_track",
    "stori_add_automation",
})


@dataclass
class _PlanStep:
    """Internal state for one plan step."""
    step_id: str
    label: str
    detail: Optional[str] = None
    status: str = "pending"
    result: Optional[str] = None
    track_name: Optional[str] = None
    tool_name: Optional[str] = None  # canonical tool name for frontend icon/color rendering
    tool_indices: list[int] = field(default_factory=list)
    parallel_group: Optional[str] = None  # steps sharing a group run concurrently


@dataclass
class _ToolCallOutcome:
    """Outcome of executing one tool call in editing/agent mode.

    The caller decides what to do with SSE events and message objects —
    either yield them directly (editing path) or put them into a queue
    (agent-team path).
    """
    enriched_params: dict[str, Any]
    tool_result: dict[str, Any]
    sse_events: list[dict[str, Any]]    # in order: toolStart, toolCall OR toolError
    msg_call: dict[str, Any]            # assistant message containing the tool call
    msg_result: dict[str, Any]          # tool response message
    skipped: bool = False               # True when rejected by circuit-breaker or validation
    extra_tool_calls: list[dict[str, Any]] = field(default_factory=list)  # synthetic calls (icon)


class _PlanTracker:
    """Manages the structured plan lifecycle for an EDITING session.

    Builds a plan from the first batch of tool calls, emits plan / planStepUpdate
    SSE events, and tracks step progress across composition iterations.
    """

    def __init__(self) -> None:
        self.plan_id: str = str(_uuid_mod.uuid4())
        self.title: str = ""
        self.steps: list[_PlanStep] = []
        self._active_step_id: Optional[str] = None
        self._active_step_ids: set[str] = set()
        self._next_id: int = 1

    # -- Build ----------------------------------------------------------------

    def build(
        self,
        tool_calls: list[Any],
        prompt: str,
        project_context: dict[str, Any],
        is_composition: bool,
        store: Any,
    ) -> None:
        self.title = self._derive_title(prompt, tool_calls, project_context)
        self.steps = self._group_into_steps(tool_calls)
        if is_composition:
            self._add_anticipatory_steps(store)

    def _derive_title(
        self,
        prompt: str,
        tool_calls: list[Any],
        project_context: dict[str, Any],
    ) -> str:
        """Build a musically descriptive plan title.

        Target patterns (from ExecutionTimelineView spec):
          Editing:    "Building Funk Groove"
          Composing:  "Composing Lo-Fi Hip Hop"
          Multi-track: "Setting Up 6-Track Jazz"
        """
        # Count tracks being created
        track_count = sum(
            1 for tc in tool_calls if tc.name in _TRACK_CREATION_NAMES
        )

        # Extract style/section from structured prompts
        style: Optional[str] = None
        section: Optional[str] = None

        if prompt.startswith("STORI PROMPT"):
            for line in prompt.splitlines():
                stripped = line.strip()
                if stripped.lower().startswith("section:"):
                    section = stripped.split(":", 1)[1].strip()
                elif stripped.lower().startswith("style:"):
                    style = stripped.split(":", 1)[1].strip()
        else:
            # Try to extract style hints from generator tool calls
            for tc in tool_calls:
                if tc.name in _GENERATOR_TOOL_NAMES:
                    s = tc.params.get("style", "")
                    if s:
                        style = _humanize_style(s)
                        break

        style_title = _humanize_style(style) if style else None

        if track_count >= 3 and style_title:
            return f"Setting Up {track_count}-Track {style_title}"
        if section and style_title:
            return f"Building {style_title} {section.title()}"
        if style_title:
            return f"Composing {style_title}"
        if section:
            return f"Building {section.title()}"
        if track_count >= 2:
            return f"Setting Up {track_count}-Track Arrangement"

        # Free-form: extract a musical phrase from the prompt
        short = prompt[:80].rstrip()
        if len(prompt) > 80:
            short = short.rsplit(" ", 1)[0] or short
        if short.startswith("STORI PROMPT"):
            return "Composing"
        return f"Building {short}" if len(short) < 40 else short

    @staticmethod
    def _track_name_for_call(tc: Any) -> Optional[str]:
        """Extract the track name a tool call targets (None for project-level)."""
        name = tc.name
        params = tc.params
        if name in _TRACK_CREATION_NAMES:
            return params.get("name")
        if name in _GENERATOR_TOOL_NAMES:
            return params.get("trackName") or params.get("role", "").capitalize() or None
        return params.get("trackName") or params.get("name") or None

    def _group_into_steps(self, tool_calls: list[Any]) -> list[_PlanStep]:
        """Group tool calls into plan steps using canonical label patterns.

        Canonical patterns recognised by ExecutionTimelineView:
          "Create <TrackName> track"     — track creation
          "Add content to <TrackName>"   — region + note generation
          "Add notes to <TrackName>"     — note-only addition
          "Add region to <TrackName>"    — region-only
          "Add effects to <TrackName>"   — insert effects for a track
          "Add MIDI CC to <TrackName>"   — CC curves
          "Add pitch bend to <TrackName>"— pitch bend events
          "Write automation for <TrackName>" — automation lanes
          "Set up shared Reverb bus"     — project-level bus setup

        Project-level steps (tempo, key, bus) must NOT contain a
        preposition pattern — they fall into "Project Setup".
        """
        steps: list[_PlanStep] = []
        i, n = 0, len(tool_calls)

        # Leading setup tools — one step per call (project-level)
        while i < n and tool_calls[i].name in _SETUP_TOOL_NAMES:
            tc = tool_calls[i]
            if tc.name == "stori_set_tempo":
                label = f"Set tempo to {tc.params.get('tempo', '?')} BPM"
            elif tc.name == "stori_set_key":
                key_val = tc.params.get("key", "?")
                label = f"Set key signature to {key_val}"
            else:
                label = _human_label_for_tool(tc.name, tc.params)
            steps.append(_PlanStep(
                step_id=str(self._next_id),
                label=label,
                tool_name=tc.name,
                tool_indices=[i],
            ))
            self._next_id += 1
            i += 1

        while i < n:
            tc = tool_calls[i]

            # ----- Track creation: "Create <TrackName> track" -----
            if tc.name in _TRACK_CREATION_NAMES:
                track_name = tc.params.get("name", "Track")
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=f"Create {track_name} track",
                    track_name=track_name,
                    tool_name="stori_add_midi_track",
                    tool_indices=[i],
                    parallel_group="instruments",
                ))
                self._next_id += 1
                i += 1

                # Consume contiguous content/generator tools for the same track
                content_indices: list[int] = []
                content_detail_parts: list[str] = []
                while i < n and tool_calls[i].name in (
                    _CONTENT_TOOL_NAMES | _GENERATOR_TOOL_NAMES | _MIXING_TOOL_NAMES
                ):
                    next_tc = tool_calls[i]
                    # Styling tools (color/icon) are consumed silently
                    if next_tc.name in {"stori_set_track_color", "stori_set_track_icon"}:
                        content_indices.append(i)
                        i += 1
                        continue
                    next_track = self._track_name_for_call(next_tc)
                    if next_track and next_track.lower() != track_name.lower():
                        break
                    content_indices.append(i)
                    if next_tc.name in _GENERATOR_TOOL_NAMES:
                        style = next_tc.params.get("style", "")
                        bars = next_tc.params.get("bars", "")
                        role = next_tc.params.get("role", "")
                        parts = []
                        if bars:
                            parts.append(f"{bars} bars")
                        if style:
                            parts.append(_humanize_style(style))
                        if role:
                            parts.append(role)
                        if parts:
                            content_detail_parts.append(", ".join(parts))
                    i += 1
                if content_indices:
                    steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=f"Add content to {track_name}",
                        detail="; ".join(content_detail_parts) if content_detail_parts else None,
                        track_name=track_name,
                        tool_name="stori_add_notes",
                        tool_indices=content_indices,
                        parallel_group="instruments",
                    ))
                    self._next_id += 1

                # Consume contiguous effects for the same track
                effect_indices: list[int] = []
                effect_detail_parts: list[str] = []
                while i < n and tool_calls[i].name in _EFFECT_TOOL_NAMES:
                    etc = tool_calls[i]
                    etc_track = etc.params.get("trackName") or etc.params.get("name", "")
                    if etc.name == "stori_ensure_bus":
                        break  # bus setup is project-level
                    if etc_track and etc_track.lower() != track_name.lower():
                        break
                    effect_indices.append(i)
                    if etc.name == "stori_add_insert_effect":
                        etype = etc.params.get("type", "")
                        if etype:
                            effect_detail_parts.append(etype.title())
                    i += 1
                if effect_indices:
                    steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=f"Add effects to {track_name}",
                        detail=", ".join(effect_detail_parts) if effect_detail_parts else None,
                        track_name=track_name,
                        tool_name="stori_add_insert_effect",
                        tool_indices=effect_indices,
                        parallel_group="instruments",
                    ))
                    self._next_id += 1

                # Consume contiguous expressive tools for the same track
                while i < n and tool_calls[i].name in _EXPRESSIVE_TOOL_NAMES:
                    etc = tool_calls[i]
                    if etc.name == "stori_add_midi_cc":
                        steps.append(_PlanStep(
                            step_id=str(self._next_id),
                            label=f"Add MIDI CC to {track_name}",
                            track_name=track_name,
                            tool_name="stori_add_midi_cc",
                            tool_indices=[i],
                            parallel_group="instruments",
                        ))
                    elif etc.name == "stori_add_pitch_bend":
                        steps.append(_PlanStep(
                            step_id=str(self._next_id),
                            label=f"Add pitch bend to {track_name}",
                            track_name=track_name,
                            tool_name="stori_add_pitch_bend",
                            tool_indices=[i],
                            parallel_group="instruments",
                        ))
                    elif etc.name == "stori_add_automation":
                        steps.append(_PlanStep(
                            step_id=str(self._next_id),
                            label=f"Write automation for {track_name}",
                            track_name=track_name,
                            tool_name="stori_add_automation",
                            tool_indices=[i],
                            parallel_group="instruments",
                        ))
                    self._next_id += 1
                    i += 1

            # ----- Orphaned content tools (no preceding track creation) -----
            elif tc.name in (_CONTENT_TOOL_NAMES | _GENERATOR_TOOL_NAMES):
                track_name = self._track_name_for_call(tc) or "Track"
                indices = [i]
                i += 1
                while i < n and tool_calls[i].name in (
                    _CONTENT_TOOL_NAMES | _GENERATOR_TOOL_NAMES
                ):
                    next_track = self._track_name_for_call(tool_calls[i])
                    if next_track and next_track.lower() != track_name.lower():
                        break
                    indices.append(i)
                    i += 1
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=f"Add content to {track_name}",
                    track_name=track_name,
                    tool_name="stori_add_notes",
                    tool_indices=indices,
                    parallel_group="instruments",
                ))
                self._next_id += 1

            # ----- Effects (track-targeted or bus setup) -----
            elif tc.name in _EFFECT_TOOL_NAMES:
                if tc.name == "stori_ensure_bus":
                    bus_name = tc.params.get("name", "Bus")
                    bus_indices = [i]
                    i += 1
                    # Consume following sends for the same bus
                    while i < n and tool_calls[i].name == "stori_add_send":
                        bus_indices.append(i)
                        i += 1
                    steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=f"Set up shared {bus_name} bus",
                        tool_name="stori_ensure_bus",
                        tool_indices=bus_indices,
                    ))
                    self._next_id += 1
                else:
                    track_name = tc.params.get("trackName") or "Track"
                    indices = [i]
                    detail_parts: list[str] = []
                    if tc.name == "stori_add_insert_effect":
                        etype = tc.params.get("type", "")
                        if etype:
                            detail_parts.append(etype.title())
                    i += 1
                    while i < n and tool_calls[i].name in _EFFECT_TOOL_NAMES:
                        etc = tool_calls[i]
                        if etc.name == "stori_ensure_bus":
                            break
                        etc_track = etc.params.get("trackName", "")
                        if etc_track and etc_track.lower() != track_name.lower():
                            break
                        indices.append(i)
                        if etc.name == "stori_add_insert_effect":
                            etype = etc.params.get("type", "")
                            if etype:
                                detail_parts.append(etype.title())
                        i += 1
                    steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=f"Add effects to {track_name}",
                        detail=", ".join(detail_parts) if detail_parts else None,
                        track_name=track_name,
                        tool_name="stori_add_insert_effect",
                        tool_indices=indices,
                        parallel_group="instruments",
                    ))
                    self._next_id += 1

            # ----- Expressive tools (standalone) -----
            elif tc.name in _EXPRESSIVE_TOOL_NAMES:
                track_name = self._track_name_for_call(tc) or "Track"
                if tc.name == "stori_add_midi_cc":
                    label = f"Add MIDI CC to {track_name}"
                elif tc.name == "stori_add_pitch_bend":
                    label = f"Add pitch bend to {track_name}"
                else:
                    label = f"Write automation for {track_name}"
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=label,
                    track_name=track_name,
                    tool_name=tc.name,
                    tool_indices=[i],
                    parallel_group="instruments",
                ))
                self._next_id += 1
                i += 1

            # ----- Mixing tools -----
            elif tc.name in _MIXING_TOOL_NAMES:
                indices = []
                while i < n and tool_calls[i].name in _MIXING_TOOL_NAMES:
                    indices.append(i)
                    i += 1
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label="Adjust mix",
                    tool_name="stori_set_track_volume",
                    tool_indices=indices,
                ))
                self._next_id += 1

            # ----- Fallback -----
            else:
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=_human_label_for_tool(tc.name, tc.params),
                    tool_name=tc.name,
                    tool_indices=[i],
                ))
                self._next_id += 1
                i += 1

        return steps

    def build_from_prompt(
        self,
        parsed: Any,  # ParsedPrompt — avoid circular import
        prompt: str,
        project_context: dict[str, Any],
    ) -> None:
        """Build a skeleton plan from a parsed STORI PROMPT before any LLM call.

        Creates one pending step per expected action derived from the prompt's
        routing fields (Tempo, Key, Role, Style, Section) so the TODO list
        appears immediately when the user submits, not after the first LLM
        response arrives.

        Labels use canonical patterns for ExecutionTimelineView grouping.
        Steps are ordered per-track (contiguous) so the timeline renders
        coherent instrument sections.
        """
        self.title = self._derive_title(prompt, [], project_context)

        # Setup steps from routing fields — skip if project already matches
        current_tempo = project_context.get("tempo")
        current_key = (project_context.get("key") or "").strip().lower()
        if parsed.tempo and parsed.tempo != current_tempo:
            self.steps.append(_PlanStep(
                step_id=str(self._next_id),
                label=f"Set tempo to {parsed.tempo} BPM",
                tool_name="stori_set_tempo",
            ))
            self._next_id += 1
        if parsed.key and parsed.key.strip().lower() != current_key:
            self.steps.append(_PlanStep(
                step_id=str(self._next_id),
                label=f"Set key signature to {parsed.key}",
                tool_name="stori_set_key",
            ))
            self._next_id += 1

        # Build set of existing track names for label selection
        existing_track_names = {
            t.get("name", "").lower()
            for t in project_context.get("tracks", [])
            if t.get("name")
        }

        # Expressive tool steps — derive from STORI PROMPT extensions
        ext = getattr(parsed, "extensions", {}) or {}
        ext_keys = {k.lower() for k in ext}
        effects_data = ext.get("effects") or ext.get("Effects") or {}

        # One step per role — map role names to human-friendly track labels.
        # Steps are grouped per track: create → content → effects → expressive
        _ROLE_LABELS: dict[str, str] = {
            "drums": "Drums",
            "drum": "Drums",
            "bass": "Bass",
            "chords": "Chords",
            "chord": "Chords",
            "melody": "Melody",
            "lead": "Lead",
            "arp": "Arp",
            "pads": "Pads",
            "pad": "Pads",
            "fx": "FX",
        }
        for role in parsed.roles:
            track_label = _ROLE_LABELS.get(role.lower(), role.title())
            track_exists = track_label.lower() in existing_track_names

            # Step 1: Create track (or note that it exists)
            if track_exists:
                self.steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=f"Add content to {track_label}",
                    track_name=track_label,
                    tool_name="stori_add_notes",
                    parallel_group="instruments",
                ))
            else:
                self.steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=f"Create {track_label} track",
                    track_name=track_label,
                    tool_name="stori_add_midi_track",
                    parallel_group="instruments",
                ))
            self._next_id += 1

            if not track_exists:
                # Step 2: Add content (separate from creation for timeline granularity)
                self.steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=f"Add content to {track_label}",
                    track_name=track_label,
                    tool_name="stori_add_notes",
                    parallel_group="instruments",
                ))
                self._next_id += 1

            # Step 3: Per-track effects (from Effects block or style defaults)
            track_key_lower = track_label.lower()
            if "effects" in ext_keys and isinstance(effects_data, dict):
                matched_key = None
                for ek in effects_data:
                    if ek.replace("_", " ").lower() == track_key_lower:
                        matched_key = ek
                        break
                if matched_key:
                    self.steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=f"Add effects to {track_label}",
                        track_name=track_label,
                        tool_name="stori_add_insert_effect",
                        parallel_group="instruments",
                    ))
                    self._next_id += 1

        # Per-track effects from Effects block for tracks NOT in roles
        if "effects" in ext_keys and isinstance(effects_data, dict):
            role_labels_lower = {
                _ROLE_LABELS.get(r.lower(), r.title()).lower()
                for r in parsed.roles
            }
            for track_key in effects_data:
                label = track_key.replace("_", " ").title()
                if label.lower() not in role_labels_lower:
                    self.steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=f"Add effects to {label}",
                        track_name=label,
                        tool_name="stori_add_insert_effect",
                        parallel_group="instruments",
                    ))
                    self._next_id += 1

        # Generic effects step when composing without explicit Effects block
        if "effects" not in ext_keys and parsed.roles:
            self.steps.append(_PlanStep(
                step_id=str(self._next_id),
                label="Add effects and routing",
                tool_name="stori_add_insert_effect",
                parallel_group="instruments",
            ))
            self._next_id += 1

        # If no roles but it's a composition, add a generic placeholder
        if not parsed.roles:
            self.steps.append(_PlanStep(
                step_id=str(self._next_id),
                label="Generate music",
                tool_name="stori_add_midi_track",
            ))
            self._next_id += 1

        # Expressive steps with track names where possible
        if "midiexpressiveness" in ext_keys:
            midi_exp = ext.get("midiexpressiveness") or ext.get("MidiExpressiveness") or {}
            if isinstance(midi_exp, dict):
                # Try to derive target track from the extension data
                target_track = None
                if parsed.roles:
                    # Default to first melodic role
                    for r in parsed.roles:
                        if r.lower() not in ("drums",):
                            target_track = _ROLE_LABELS.get(r.lower(), r.title())
                            break
                    if not target_track:
                        target_track = _ROLE_LABELS.get(
                            parsed.roles[0].lower(), parsed.roles[0].title()
                        )

                if "cc_curves" in midi_exp:
                    label = f"Add MIDI CC to {target_track}" if target_track else "Add MIDI CC curves"
                    self.steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=label,
                        track_name=target_track,
                        tool_name="stori_add_midi_cc",
                        parallel_group="instruments",
                    ))
                    self._next_id += 1
                if "pitch_bend" in midi_exp:
                    label = f"Add pitch bend to {target_track}" if target_track else "Add pitch bend"
                    self.steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=label,
                        track_name=target_track,
                        tool_name="stori_add_pitch_bend",
                        parallel_group="instruments",
                    ))
                    self._next_id += 1
                if "sustain_pedal" in midi_exp:
                    label = f"Add MIDI CC to {target_track}" if target_track else "Add sustain pedal (CC 64)"
                    self.steps.append(_PlanStep(
                        step_id=str(self._next_id),
                        label=label,
                        track_name=target_track,
                        tool_name="stori_add_midi_cc",
                        parallel_group="instruments",
                    ))
                    self._next_id += 1

        if "automation" in ext_keys:
            automation_data = ext.get("automation") or ext.get("Automation") or []
            count = len(automation_data) if isinstance(automation_data, list) else 1
            # Try to derive track names from automation entries
            if isinstance(automation_data, list) and automation_data:
                first_track = automation_data[0].get("track")
                if first_track:
                    label = f"Write automation for {first_track.replace('_', ' ').title()}"
                else:
                    label = f"Write automation ({count} lane{'s' if count != 1 else ''})"
            else:
                label = f"Write automation ({count} lane{'s' if count != 1 else ''})"
            self.steps.append(_PlanStep(
                step_id=str(self._next_id),
                label=label,
                tool_name="stori_add_automation",
            ))
            self._next_id += 1

        # Shared reverb bus — when 2+ tracks in the Effects block need reverb
        if "effects" in ext_keys and isinstance(effects_data, dict):
            tracks_needing_reverb = [
                k for k, v in effects_data.items()
                if isinstance(v, dict) and "reverb" in v
            ]
            if len(tracks_needing_reverb) >= 2:
                self.steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label="Set up shared Reverb bus",
                    tool_name="stori_ensure_bus",
                ))
                self._next_id += 1

    def _add_anticipatory_steps(self, store: Any) -> None:
        """For composition mode, add pending steps for tracks still needing content."""
        names_with_steps = {
            s.track_name.lower() for s in self.steps if s.track_name
        }
        for track in store.registry.list_tracks():
            if track.name.lower() in names_with_steps:
                continue
            regions = store.registry.get_track_regions(track.id)
            has_notes = any(
                bool(store.get_region_notes(r.id)) for r in regions
            ) if regions else False
            if not has_notes:
                self.steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=f"Add content to {track.name}",
                    track_name=track.name,
                    tool_name="stori_add_midi_track",
                ))
                self._next_id += 1

    # -- SSE events -----------------------------------------------------------

    def to_plan_event(self) -> dict[str, Any]:
        return {
            "type": "plan",
            "planId": self.plan_id,
            "title": self.title,
            "steps": [
                {
                    "stepId": s.step_id,
                    "label": s.label,
                    "status": "pending",
                    **({"toolName": s.tool_name} if s.tool_name else {}),
                    **({"detail": s.detail} if s.detail else {}),
                    **({"parallelGroup": s.parallel_group} if s.parallel_group else {}),
                }
                for s in self.steps
            ],
        }

    def step_for_tool_index(self, index: int) -> Optional[_PlanStep]:
        """Find the step a tool-call index belongs to (first iteration only)."""
        for step in self.steps:
            if index in step.tool_indices:
                return step
        return None

    def find_step_for_tool(
        self,
        tc_name: str,
        tc_params: dict[str, Any],
        store: Any,
    ) -> Optional[_PlanStep]:
        """Map a tool call to a plan step by name/context.

        Used for both subsequent iterations (reactive plan) and upfront-built
        plans (where tool_indices are empty and steps are matched by label/name).
        """
        # Setup tools match steps whose label starts with the action keyword
        if tc_name == "stori_set_tempo":
            tempo = tc_params.get("tempo")
            for step in self.steps:
                if "tempo" in step.label.lower() and step.status != "completed":
                    return step
            _ = tempo  # silence linter
        if tc_name == "stori_set_key":
            for step in self.steps:
                if "key" in step.label.lower() and step.status != "completed":
                    return step

        # Track creation: match by track name
        if tc_name in _TRACK_CREATION_NAMES:
            track_name = tc_params.get("name", "").lower()
            for step in self.steps:
                if (
                    step.track_name
                    and step.track_name.lower() == track_name
                    and step.status != "completed"
                ):
                    return step

        if tc_name in _CONTENT_TOOL_NAMES:
            track_id = tc_params.get("trackId", "")
            if track_id:
                for track in store.registry.list_tracks():
                    if track.id == track_id:
                        for step in self.steps:
                            if (
                                step.track_name
                                and step.track_name.lower() == track.name.lower()
                                and step.status != "completed"
                            ):
                                return step
                        break
        # Effect tools: match by track name first, then fall back to generic
        if tc_name in _EFFECT_TOOL_NAMES:
            tc_track = tc_params.get("trackName", "").lower()
            if tc_track:
                for step in self.steps:
                    if (
                        "effect" in step.label.lower()
                        and step.track_name
                        and step.track_name.lower() == tc_track
                        and step.status != "completed"
                    ):
                        return step
            if tc_name == "stori_ensure_bus":
                for step in self.steps:
                    if "bus" in step.label.lower() and step.status != "completed":
                        return step
            for step in self.steps:
                if "effect" in step.label.lower() and step.status != "completed":
                    return step

        # Expressive tools: match by tool name and track
        if tc_name in _EXPRESSIVE_TOOL_NAMES:
            tc_track = tc_params.get("trackName", "").lower()
            if tc_name == "stori_add_midi_cc":
                for step in self.steps:
                    if "MIDI CC" in step.label and step.status != "completed":
                        if not tc_track or not step.track_name or step.track_name.lower() == tc_track:
                            return step
                # Also match sustain pedal steps
                for step in self.steps:
                    if "sustain" in step.label.lower() and step.status != "completed":
                        return step
            elif tc_name == "stori_add_pitch_bend":
                for step in self.steps:
                    if "pitch bend" in step.label.lower() and step.status != "completed":
                        return step
            elif tc_name == "stori_add_automation":
                for step in self.steps:
                    if "automation" in step.label.lower() and step.status != "completed":
                        return step

        if tc_name in _MIXING_TOOL_NAMES:
            for step in self.steps:
                if "mix" in step.label.lower() and step.status != "completed":
                    return step
        return None

    def get_step(self, step_id: str) -> Optional[_PlanStep]:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def activate_step(self, step_id: str) -> dict[str, Any]:
        step = self.get_step(step_id)
        if step:
            step.status = "active"
        self._active_step_id = step_id
        self._active_step_ids.add(step_id)
        return {"type": "planStepUpdate", "stepId": step_id, "status": "active"}

    def complete_active_step(self) -> Optional[dict[str, Any]]:
        """Complete the currently-active step; returns event dict or None."""
        if not self._active_step_id:
            return None
        step = self.get_step(self._active_step_id)
        if not step:
            return None
        step.status = "completed"
        self._active_step_ids.discard(self._active_step_id)
        self._active_step_id = None
        d: dict[str, Any] = {
            "type": "planStepUpdate",
            "stepId": step.step_id,
            "status": "completed",
        }
        if step.result:
            d["result"] = step.result
        return d

    def complete_step_by_id(
        self, step_id: str, result: Optional[str] = None,
    ) -> dict[str, Any]:
        step = self.get_step(step_id)
        if step:
            step.status = "completed"
            if result:
                step.result = result
        if self._active_step_id == step_id:
            self._active_step_id = None
        self._active_step_ids.discard(step_id)
        d: dict[str, Any] = {
            "type": "planStepUpdate",
            "stepId": step_id,
            "status": "completed",
        }
        if result:
            d["result"] = result
        return d

    def complete_all_active_steps(self) -> list[dict[str, Any]]:
        """Complete every currently-active step. Returns list of event dicts."""
        events: list[dict[str, Any]] = []
        for step_id in list(self._active_step_ids):
            step = self.get_step(step_id)
            if step and step.status == "active":
                step.status = "completed"
                d: dict[str, Any] = {
                    "type": "planStepUpdate",
                    "stepId": step.step_id,
                    "status": "completed",
                }
                if step.result:
                    d["result"] = step.result
                events.append(d)
        self._active_step_ids.clear()
        self._active_step_id = None
        return events

    def find_active_step_for_track(self, track_name: str) -> Optional[_PlanStep]:
        """Find the active step bound to a specific instrument track."""
        track_lower = track_name.lower()
        for step in self.steps:
            if (
                step.status == "active"
                and step.track_name
                and step.track_name.lower() == track_lower
            ):
                return step
        return None

    def finalize_pending_as_skipped(self) -> list[dict[str, Any]]:
        """Mark all remaining pending steps as skipped and return events.

        The Execution Timeline spec requires that no step is left in "pending"
        at plan completion — steps that were never activated must be emitted
        as "skipped" so the frontend can render them correctly.
        """
        events: list[dict[str, Any]] = []
        for step in self.steps:
            if step.status == "pending":
                step.status = "skipped"
                events.append({
                    "type": "planStepUpdate",
                    "stepId": step.step_id,
                    "status": "skipped",
                })
        return events

    def progress_context(self) -> str:
        """Format plan progress for injection into the system prompt."""
        icons = {
            "completed": "✅",
            "active": "🔄",
            "pending": "⬜",
            "failed": "❌",
            "skipped": "⏭",
        }
        lines = ["Current plan progress:"]
        for s in self.steps:
            icon = icons.get(s.status, "⬜")
            line = f"{icon} Step {s.step_id}: {s.label}"
            if s.status == "completed" and s.result:
                line += f" — done ({s.result})"
            elif s.status == "active":
                line += " — active"
            else:
                line += " — pending"
            lines.append(line)
        return "\n".join(lines)


def _build_step_result(
    tool_name: str,
    params: dict[str, Any],
    existing: Optional[str] = None,
) -> str:
    """Build a human-readable result string for a plan step."""
    part = _human_label_for_tool(tool_name, params)
    if existing:
        return f"{existing}; {part}"
    return part


def _project_needs_structure(project_context: dict[str, Any]) -> bool:
    """Check if the project is empty and needs structural creation.

    Returns True when the project has no tracks, meaning composition
    requests should use EDITING mode (tool_call events) rather than
    COMPOSING mode (variation review) — you can't diff against nothing.
    """
    tracks = project_context.get("tracks", [])
    return len(tracks) == 0


def _is_additive_composition(
    parsed: Optional["ParsedPrompt"],
    project_context: dict[str, Any],
) -> bool:
    """Detect if a composition request creates a new section (EDITING, not COMPOSING).

    Returns True when the request appends new content (Position: after/last)
    or introduces roles that don't map to existing tracks. In these cases
    EDITING mode is preferred because the content is additive — there is
    nothing to diff against, and COMPOSING with phraseCount: 0 is always a bug.

    STORI PROMPTs with 2+ roles always return True: they spawn Agent Teams
    regardless of whether the named tracks already exist, because the prompt
    always places new timeline content (new regions at a later beat position).
    Routing confidence and existing-track state are both irrelevant here.
    """
    if not parsed:
        return False

    # A structured STORI PROMPT (2+ roles) always runs Agent Teams — even when
    # all tracks exist. The prompt creates new regions at later beat positions,
    # so it is always additive. This prevents the composing/variation pipeline
    # from intercepting STORI PROMPTs and producing clarification questions.
    if parsed.roles and len(parsed.roles) >= 2:
        return True

    if parsed.position and parsed.position.kind in ("after", "last"):
        return True

    existing_names = {
        t.get("name", "").lower()
        for t in project_context.get("tracks", [])
        if t.get("name")
    }
    if parsed.roles:
        for role in parsed.roles:
            if role.lower() not in existing_names:
                return True

    return False


def _get_incomplete_tracks(
    store: "StateStore",
    tool_calls_collected: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Return names of tracks that are missing regions or notes.

    Checks two conditions:
    1. Track has no regions at all
    2. Track has regions but none of them have notes — either from the current
       iteration's tool calls OR persisted in the StateStore from a prior
       iteration. Checking both sources prevents false "still needs notes"
       continuations that cause the model to clear and re-add valid content.

    Used by the composition continuation loop to detect premature LLM stops.
    """
    # Build set of regionIds that received notes in the current iteration
    regions_with_notes_this_iter: set[str] = set()
    if tool_calls_collected:
        for tc in tool_calls_collected:
            if tc["tool"] == "stori_add_notes":
                rid = tc["params"].get("regionId")
                if rid:
                    regions_with_notes_this_iter.add(rid)

    incomplete: list[str] = []
    for track in store.registry.list_tracks():
        regions = store.registry.get_track_regions(track.id)
        if not regions:
            incomplete.append(track.name)
        elif not any(
            r.id in regions_with_notes_this_iter or bool(store.get_region_notes(r.id))
            for r in regions
        ):
            incomplete.append(track.name)
    return incomplete


def _get_missing_expressive_steps(
    parsed: Optional["ParsedPrompt"],
    tool_calls_collected: list[dict[str, Any]],
) -> list[str]:
    """Return human-readable descriptions of expressive steps not yet executed.

    Checks Effects, MidiExpressiveness, and Automation blocks from the parsed
    STORI PROMPT against the tool calls already made this session. Returns an
    empty list when everything has been called (or when the parsed prompt has
    no expressive blocks).
    """
    if parsed is None:
        return []

    # Keys are lowercased by the parser (prompt_parser.py line 177)
    extensions: dict[str, Any] = parsed.extensions or {}
    called_tools = {tc["tool"] for tc in tool_calls_collected}

    missing: list[str] = []

    # Effects → stori_add_insert_effect  (stored as lowercase "effects")
    if extensions.get("effects") and "stori_add_insert_effect" not in called_tools:
        missing.append(
            "Effects block present but stori_add_insert_effect was never called. "
            "Call stori_add_insert_effect for each effects entry (compressor, reverb, eq, etc.)."
        )

    # MidiExpressiveness.cc_curves → stori_add_midi_cc  (stored as "midiexpressiveness")
    me = extensions.get("midiexpressiveness") or {}
    if me.get("cc_curves") and "stori_add_midi_cc" not in called_tools:
        missing.append(
            "MidiExpressiveness.cc_curves present but stori_add_midi_cc was never called. "
            "Call stori_add_midi_cc for each cc_curves entry."
        )

    # MidiExpressiveness.sustain_pedal → stori_add_midi_cc (CC 64)
    if me.get("sustain_pedal") and "stori_add_midi_cc" not in called_tools:
        missing.append(
            "MidiExpressiveness.sustain_pedal present but stori_add_midi_cc (CC 64) was never called. "
            "Call stori_add_midi_cc with cc=64 on the target region."
        )

    # MidiExpressiveness.pitch_bend → stori_add_pitch_bend
    if me.get("pitch_bend") and "stori_add_pitch_bend" not in called_tools:
        missing.append(
            "MidiExpressiveness.pitch_bend present but stori_add_pitch_bend was never called. "
            "Call stori_add_pitch_bend with slide events on the target region."
        )

    # Automation → stori_add_automation  (stored as lowercase "automation")
    if extensions.get("automation") and "stori_add_automation" not in called_tools:
        missing.append(
            "Automation block present but stori_add_automation was never called. "
            "Call stori_add_automation(trackId=..., parameter='Volume', points=[...]) "
            "for each lane. Use trackId (NOT 'target'). parameter must be a canonical "
            "string like 'Volume', 'Pan', 'Synth Cutoff', 'Expression (CC11)', etc."
        )

    # Effects with reverb on multiple tracks → stori_ensure_bus + stori_add_send
    effects_data = extensions.get("effects") or {}
    if isinstance(effects_data, dict):
        tracks_needing_reverb = [
            k for k, v in effects_data.items()
            if isinstance(v, dict) and "reverb" in v
        ]
        if len(tracks_needing_reverb) >= 2 and "stori_ensure_bus" not in called_tools:
            missing.append(
                f"Multiple tracks ({', '.join(tracks_needing_reverb)}) need reverb — "
                "use a shared Reverb bus: call stori_ensure_bus(name='Reverb') once, "
                "then stori_add_send(trackId=X, busId=$N.busId, levelDb=-6) for each track."
            )

    return missing


def _create_editing_composition_route(route: "IntentResult") -> "IntentResult":
    """Build an EDITING IntentResult for composition on empty projects.

    When the project has no tracks, composition requests should use EDITING
    mode so structural changes (tracks, regions, instruments, notes) are
    emitted as tool_call events for real-time frontend rendering.
    """
    all_composition_tools = (
        set(_PRIMITIVES_TRACK) | set(_PRIMITIVES_REGION)
        | set(_PRIMITIVES_FX) | set(_PRIMITIVES_MIXING)
        | {"stori_set_tempo", "stori_set_key"}
    )
    return IntentResult(
        intent=route.intent,
        sse_state=SSEState.EDITING,
        confidence=route.confidence,
        slots=route.slots,
        tools=ALL_TOOLS,
        allowed_tool_names=all_composition_tools,
        tool_choice="auto",
        force_stop_after=False,
        requires_planner=False,
        reasons=route.reasons + ("empty_project_override",),
    )


def _store_variation(
    variation,
    project_context: dict[str, Any],
    store: "StateStore",
) -> None:
    """Persist a Variation to the VariationStore so commit/discard can find it.

    Called from the maestro/stream path after ``execute_plan_variation`` returns.
    Mirrors the storage logic in the ``/variation/propose`` background task.
    """
    from app.variation.storage.variation_store import (
        get_variation_store,
        PhraseRecord,
    )
    from app.variation.core.state_machine import VariationStatus

    project_id = project_context.get("id", "")
    base_state_id = store.get_state_id()

    vstore = get_variation_store()
    record = vstore.create(
        project_id=project_id,
        base_state_id=base_state_id,
        intent=variation.intent,
        variation_id=variation.variation_id,
        conversation_id=store.conversation_id,
    )

    # CREATED → STREAMING → READY (fast-forward since generation is already done)
    record.transition_to(VariationStatus.STREAMING)
    record.ai_explanation = variation.ai_explanation
    record.affected_tracks = variation.affected_tracks
    record.affected_regions = variation.affected_regions

    for phrase in variation.phrases:
        seq = record.next_sequence()

        # Look up region position from the registry so commit can build
        # updatedRegions without re-querying the compose-phase store.
        region_entity = store.registry.get_region(phrase.region_id)
        region_meta = region_entity.metadata if region_entity else {}
        region_start_beat = region_meta.get("startBeat")
        region_duration_beats = region_meta.get("durationBeats")
        region_name = region_entity.name if region_entity else None

        record.add_phrase(PhraseRecord(
            phrase_id=phrase.phrase_id,
            variation_id=variation.variation_id,
            sequence=seq,
            track_id=phrase.track_id,
            region_id=phrase.region_id,
            beat_start=phrase.start_beat,
            beat_end=phrase.end_beat,
            label=phrase.label,
            diff_json={
                "phraseId": phrase.phrase_id,
                "trackId": phrase.track_id,
                "regionId": phrase.region_id,
                "startBeat": phrase.start_beat,
                "endBeat": phrase.end_beat,
                "label": phrase.label,
                "tags": phrase.tags,
                "explanation": phrase.explanation,
                "noteChanges": [nc.model_dump(by_alias=True) for nc in phrase.note_changes],
                "controllerChanges": phrase.controller_changes,
            },
            ai_explanation=phrase.explanation,
            tags=phrase.tags,
            region_start_beat=region_start_beat,
            region_duration_beats=region_duration_beats,
            region_name=region_name,
        ))

    record.transition_to(VariationStatus.READY)
    logger.info(
        f"Variation stored: {variation.variation_id[:8]} "
        f"({len(variation.phrases)} phrases, status=READY)"
    )


async def orchestrate(
    prompt: str,
    project_context: Optional[dict[str, Any]] = None,
    model: Optional[str] = None,
    usage_tracker: Optional[UsageTracker] = None,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    conversation_history: Optional[list[dict[str, Any]]] = None,
    is_cancelled: Optional[Callable[[], Awaitable[bool]]] = None,
    quality_preset: Optional[str] = None,
) -> AsyncIterator[str]:
    """
    Main orchestration using Cursor-of-DAWs architecture.
    
    Flow:
    1. Create trace context for request
    2. Intent Router classifies prompt → route + allowlist
    3. Backend determines execution_mode from intent:
       COMPOSING → variation (human review), EDITING → apply (immediate)
    4. Route to REASONING / EDITING / COMPOSING
    5. Execute with strict tool gating + entity validation
    """
    project_context = project_context or {}
    conversation_history = conversation_history or []
    selected_model = get_model_or_default(model)
    
    # Create trace context for this request
    trace = create_trace_context(
        conversation_id=conversation_id,
        user_id=user_id,
    )
    
    llm = LLMClient(model=selected_model)
    
    # Get or create StateStore — use project_id as primary key so the
    # variation commit endpoint can find the same store instance.
    _project_id = project_context.get("id") or ""
    store = get_or_create_store(
        conversation_id=_project_id or conversation_id or "default",
        project_id=_project_id,
    )
    store.sync_from_client(project_context)
    
    try:
        with trace_span(trace, "orchestrate", {"prompt_length": len(prompt)}):
            
            # =================================================================
            # Step 1: Intent Classification
            # =================================================================
            
            with trace_span(trace, "intent_classification"):
                route = await get_intent_result_with_llm(prompt, project_context, llm, conversation_history)
                
                # Extract parsed prompt for state-detection heuristics
                _orch_slots = getattr(route, "slots", None)
                _orch_extras = getattr(_orch_slots, "extras", None) if _orch_slots is not None else None
                _orch_parsed: Optional[ParsedPrompt] = (
                    _orch_extras.get("parsed_prompt") if isinstance(_orch_extras, dict) else None
                )
                
                # Backend-owned execution mode policy:
                # COMPOSING → variation (music generation requires human review)
                #   EXCEPT: empty project → override to EDITING (can't diff against nothing)
                #   EXCEPT: additive composition (new section / new tracks) → EDITING
                # EDITING   → apply (structural ops execute directly)
                # REASONING → n/a (no tools)
                if route.sse_state == SSEState.COMPOSING:
                    if _project_needs_structure(project_context):
                        route = _create_editing_composition_route(route)
                        execution_mode = "apply"
                        logger.info(
                            f"🔄 Empty project: overriding {route.intent.value} → EDITING "
                            f"for structural creation with tool_call events"
                        )
                    elif _is_additive_composition(_orch_parsed, project_context):
                        route = _create_editing_composition_route(route)
                        execution_mode = "apply"
                        logger.info(
                            f"🔄 Additive composition (new section/tracks): "
                            f"overriding {route.intent.value} → EDITING "
                            f"for direct execution with tool_call events"
                        )
                    else:
                        execution_mode = "variation"
                        logger.info(f"Intent {route.intent.value} → COMPOSING, execution_mode='variation'")
                else:
                    execution_mode = "apply"
                    logger.info(f"Intent {route.intent.value} → {route.sse_state.value}, execution_mode='apply'")
                
                log_intent(
                    trace.trace_id,
                    prompt,
                    route.intent.value,
                    route.confidence,
                    route.sse_state.value,
                    route.reasons,
                )
            
            # Emit SSE state for frontend
            yield await sse_event({
                "type": "state",
                "state": route.sse_state.value,
                "intent": route.intent.value,
                "confidence": route.confidence,
                "traceId": trace.trace_id,
            })
            
            logger.info(f"[{trace.trace_id[:8]}] 🎯 {route.intent.value} → {route.sse_state.value}")
            
            # =================================================================
            # Step 2: Handle REASONING (questions - no tools)
            # =================================================================
            
            if route.sse_state == SSEState.REASONING:
                async for event in _handle_reasoning(
                    prompt, project_context, route, llm, trace, 
                    usage_tracker, conversation_history
                ):
                    yield event
                return
            
            # =================================================================
            # Step 3: Handle COMPOSING (planner path)
            # =================================================================
            
            if route.sse_state == SSEState.COMPOSING:
                async for event in _handle_composing(
                    prompt, project_context, route, llm, store, trace,
                    usage_tracker, conversation_id,
                    quality_preset=quality_preset,
                ):
                    yield event
                return
            
            # =================================================================
            # Step 4: Handle EDITING (LLM tool calls with allowlist)
            # =================================================================

            # ── Agent Teams intercept ──
            # Multi-instrument STORI PROMPT compositions (2+ roles, apply mode)
            # spawn one independent LLM session per instrument running in
            # parallel. Single-instrument and non-STORI-PROMPT requests fall
            # through to the standard _handle_editing path unchanged.
            if (
                route.intent == Intent.GENERATE_MUSIC
                and execution_mode == "apply"
                and _orch_parsed is not None
                and getattr(_orch_parsed, "roles", None)
                and len(_orch_parsed.roles) > 1
            ):
                async for event in _handle_composition_agent_team(
                    prompt, project_context, _orch_parsed, route, llm, store,
                    trace, usage_tracker,
                ):
                    yield event
            else:
                async for event in _handle_editing(
                    prompt, project_context, route, llm, store, trace,
                    usage_tracker, conversation_history, execution_mode,
                    is_cancelled=is_cancelled,
                    quality_preset=quality_preset,
                ):
                    yield event
    
    except Exception as e:
        logger.exception(f"[{trace.trace_id[:8]}] Orchestration error: {e}")
        yield await sse_event({
            "type": "error",
            "message": str(e),
            "traceId": trace.trace_id,
        })
        yield await sse_event({
            "type": "complete",
            "success": False,
            "error": str(e),
            "traceId": trace.trace_id,
            **_context_usage_fields(usage_tracker, selected_model),
        })
    
    finally:
        await llm.close()
        clear_trace_context()


async def _handle_reasoning(
    prompt: str,
    project_context: dict[str, Any],
    route,
    llm: LLMClient,
    trace,
    usage_tracker: Optional[UsageTracker],
    conversation_history: list[dict[str, Any]],
) -> AsyncIterator[str]:
    """Handle REASONING state - answer questions without tools."""
    yield await sse_event({"type": "status", "message": "Reasoning..."})
    
    # Check for Stori docs questions → RAG
    if route.intent == Intent.ASK_STORI_DOCS:
        try:
            from app.services.rag import get_rag_service
            rag = get_rag_service(llm_client=llm)
            
            if rag.collection_exists():
                async for chunk in rag.answer(prompt, model=llm.model):
                    yield await sse_event({"type": "content", "content": chunk})
                
                yield await sse_event({
                    "type": "complete",
                    "success": True,
                    "toolCalls": [],
                    "traceId": trace.trace_id,
                    **_context_usage_fields(usage_tracker, llm.model),
                })
                return
        except Exception as e:
            logger.warning(f"[{trace.trace_id[:8]}] RAG failed: {e}")
    
    # General question → LLM without tools
    with trace_span(trace, "llm_thinking"):
        messages = [{"role": "system", "content": system_prompt_base()}]

        if project_context:
            messages.append({"role": "system", "content": format_project_context(project_context)})

        if conversation_history:
            messages.extend(conversation_history)
        
        messages.append({"role": "user", "content": wrap_user_request(prompt)})
        
        start_time = time.time()
        response = None

        # Use streaming for reasoning models
        logger.info(f"🎯 REASONING handler: supports_reasoning={llm.supports_reasoning()}, model={llm.model}")
        if llm.supports_reasoning():
            logger.info("🌊 Using streaming path for reasoning model")
            response_text = ""
            async for raw in llm.chat_completion_stream(
                messages=messages,
                tools=[],
                tool_choice="none",
            ):
                event = cast(dict[str, Any], raw)
                if event.get("type") == "reasoning_delta":
                    # Chain of Thought reasoning (extended reasoning from OpenRouter)
                    reasoning_text = event.get("text", "")
                    if reasoning_text:
                        # Sanitize reasoning to remove internal implementation details
                        sanitized = sanitize_reasoning(reasoning_text)
                        if sanitized:  # Only emit if there's content after sanitization
                            yield await sse_event({
                                "type": "reasoning",
                                "content": sanitized,
                            })
                elif event.get("type") == "content_delta":
                    # User-facing response
                    content_text = event.get("text", "")
                    if content_text:
                        response_text += content_text
                        yield await sse_event({"type": "content", "content": content_text})
                elif event.get("type") == "done":
                    response = LLMResponse(
                        content=response_text or event.get("content"),
                        usage=event.get("usage", {})
                    )
            duration_ms = (time.time() - start_time) * 1000
        else:
            # Non-thinking models use regular completion
            response = await llm.chat_completion(
                messages=messages,
                tools=[],
                tool_choice="none",
            )
            duration_ms = (time.time() - start_time) * 1000
            
            # Stream the response content
            if response.content:
                yield await sse_event({"type": "content", "content": response.content})
        
        if response and response.usage:
            log_llm_call(
                trace.trace_id,
                llm.model,
                response.usage.get("prompt_tokens", 0),
                response.usage.get("completion_tokens", 0),
                duration_ms,
                False,
            )
            if usage_tracker:
                usage_tracker.add(
                    response.usage.get("prompt_tokens", 0),
                    response.usage.get("completion_tokens", 0),
                )
    
    yield await sse_event({
        "type": "complete",
        "success": True,
        "toolCalls": [],
        "traceId": trace.trace_id,
        **_context_usage_fields(usage_tracker, llm.model),
    })


def _create_editing_fallback_route(route) -> IntentResult:
    """
    Build an IntentResult for EDITING when the COMPOSING planner fails with function-call-like output.

    The planner is supposed to return JSON; sometimes the LLM returns tool-call syntax instead.
    This creates a one-off EDITING route with primitives so we can still produce tool calls.
    See docs/reference/architecture.md.
    """
    return IntentResult(
        intent=Intent.NOTES_ADD,
        sse_state=SSEState.EDITING,
        confidence=0.7,
        slots=route.slots,
        tools=ALL_TOOLS,
        allowed_tool_names=set(_PRIMITIVES_REGION) | set(_PRIMITIVES_TRACK),
        tool_choice="auto",
        force_stop_after=False,
        requires_planner=False,
        reasons=("Fallback from planner failure",),
    )


async def _retry_composing_as_editing(
    prompt: str,
    project_context: dict[str, Any],
    route,
    llm: LLMClient,
    store: StateStore,
    trace,
    usage_tracker: Optional[UsageTracker],
    quality_preset: Optional[str] = None,
) -> AsyncIterator[str]:
    """When planner output looks like function calls instead of JSON, retry as EDITING with primitives."""
    logger.warning(
        f"[{trace.trace_id[:8]}] Planner output looks like function calls, "
        "falling back to EDITING mode with tools"
    )
    yield await sse_event({"type": "status", "message": "Retrying with different approach..."})
    editing_route = _create_editing_fallback_route(route)
    async for event in _handle_editing(
        prompt, project_context, editing_route, llm, store,
        trace, usage_tracker, [], "variation",
        quality_preset=quality_preset,
    ):
        yield event


async def _handle_composing(
    prompt: str,
    project_context: dict[str, Any],
    route,
    llm: LLMClient,
    store: StateStore,
    trace,
    usage_tracker: Optional[UsageTracker],
    conversation_id: Optional[str],
    quality_preset: Optional[str] = None,
) -> AsyncIterator[str]:
    """Handle COMPOSING state - generate music via planner.

    All COMPOSING intents produce a Variation for human review.
    The planner generates a tool-call plan, the executor simulates it
    in variation mode, and the result is streamed as meta/phrase/done events.

    Phase 1 (Unified SSE UX): reasoning events are streamed during the
    planner's LLM call so the user sees the agent thinking — same UX as
    EDITING mode.
    """
    yield await sse_event({"type": "status", "message": "Thinking..."})

    # Extract parsed prompt from route slots (same as _handle_editing)
    _slots = getattr(route, "slots", None)
    _extras = getattr(_slots, "extras", None) if _slots is not None else None
    parsed: Optional[ParsedPrompt] = (
        _extras.get("parsed_prompt") if isinstance(_extras, dict) else None
    )

    # ── Streaming planner: yields reasoning SSE events, then the plan ──
    plan: Optional[ExecutionPlan] = None
    with trace_span(trace, "planner"):
        async for item in build_execution_plan_stream(
            user_prompt=prompt,
            project_state=project_context,
            route=route,
            llm=llm,
            parsed=parsed,
            usage_tracker=usage_tracker,
            emit_sse=lambda data: sse_event(data),
        ):
            if isinstance(item, ExecutionPlan):
                plan = item
            else:
                # SSE-formatted reasoning event — forward to client
                yield item

    if plan and plan.tool_calls:
        # ── Build plan tracker and emit plan event ──
        composing_plan_tracker = _PlanTracker()
        composing_plan_tracker.build(
            plan.tool_calls, prompt, project_context,
            is_composition=True, store=store,
        )
        if len(composing_plan_tracker.steps) >= 1:
            yield await sse_event(composing_plan_tracker.to_plan_event())

        # planSummary uses plan tracker step count (Bug 9)
        yield await sse_event({
            "type": "planSummary",
            "totalSteps": len(composing_plan_tracker.steps),
            "generations": plan.generation_count,
            "edits": plan.edit_count,
        })

        # =================================================================
        # PROPOSAL PHASE: emit all tool calls as proposals.
        # No planStepUpdate events during this phase (Bug 2).
        # Steps remain "pending" in the frontend TODO list.
        # =================================================================
        for tc in plan.tool_calls:
            yield await sse_event({
                "type": "toolCall",
                "id": "",
                "name": tc.name,
                "params": tc.params,
                "proposal": True,
            })

        # =================================================================
        # EXECUTION PHASE: execute each tool call for real.
        # Emit planStepUpdate:active/completed, toolStart, and toolCall
        # events with real UUIDs (proposal: false).
        # =================================================================
        try:
            with trace_span(trace, "variation_generation", {"steps": len(plan.tool_calls)}):
                from app.core.executor import execute_plan_variation

                logger.info(
                    f"[{trace.trace_id[:8]}] Starting variation execution: "
                    f"{len(plan.tool_calls)} tool calls"
                )

                _event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
                # Per-track active-step tracking for parallel instrument execution.
                # When multiple instruments execute concurrently, each track has
                # its own "current step" rather than a single global active step.
                _active_step_by_track: dict[str, str] = {}

                async def _on_pre_tool(
                    tool_name: str, params: dict[str, Any],
                ) -> None:
                    """Execution phase: planStepUpdate:active + toolStart.

                    Supports parallel instrument execution: multiple steps from
                    different instruments can be active simultaneously. Step
                    transitions are tracked per-instrument (by track_name) so
                    completing one instrument's step doesn't affect another's.
                    """
                    step = composing_plan_tracker.find_step_for_tool(
                        tool_name, params, store,
                    )
                    if step and step.status != "active":
                        track_key = (step.track_name or "").lower()
                        # Complete the previous step for the same instrument
                        prev_step_id = _active_step_by_track.get(track_key)
                        if prev_step_id and prev_step_id != step.step_id:
                            await _event_queue.put(
                                composing_plan_tracker.complete_step_by_id(prev_step_id)
                            )
                        await _event_queue.put(
                            composing_plan_tracker.activate_step(step.step_id)
                        )
                        if track_key:
                            _active_step_by_track[track_key] = step.step_id

                    label = _human_label_for_tool(tool_name, params)
                    await _event_queue.put({
                        "type": "toolStart",
                        "name": tool_name,
                        "label": label,
                    })

                async def _on_post_tool(
                    tool_name: str, resolved_params: dict[str, Any],
                ) -> None:
                    """Execution phase: toolCall with real UUID after success."""
                    call_id = str(_uuid_mod.uuid4())
                    emit_params = _enrich_params_with_track_context(resolved_params, store)
                    await _event_queue.put({
                        "type": "toolCall",
                        "id": call_id,
                        "name": tool_name,
                        "params": emit_params,
                        "proposal": False,
                    })

                async def _on_progress(
                    current: int, total: int,
                    tool_name: str = "", tool_args: dict | None = None,
                ) -> None:
                    label = _human_label_for_tool(tool_name, tool_args or {}) if tool_name else f"Step {current}"
                    await _event_queue.put({
                        "type": "progress",
                        "currentStep": current,
                        "totalSteps": total,
                        "message": label,
                        "toolName": tool_name,
                    })

                _VARIATION_TIMEOUT = 300
                task = asyncio.create_task(
                    execute_plan_variation(
                        tool_calls=plan.tool_calls,
                        project_state=project_context,
                        intent=prompt,
                        conversation_id=conversation_id,
                        explanation=plan.llm_response_text,
                        progress_callback=_on_progress,
                        pre_tool_callback=_on_pre_tool,
                        post_tool_callback=_on_post_tool,
                        quality_preset=quality_preset,
                    )
                )

                variation = None
                start_wall = time.time()
                try:
                    while True:
                        if time.time() - start_wall > _VARIATION_TIMEOUT:
                            task.cancel()
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass
                            raise asyncio.TimeoutError()
                        try:
                            event_data = await asyncio.wait_for(
                                _event_queue.get(), timeout=0.05,
                            )
                            yield await sse_event(event_data)
                        except asyncio.TimeoutError:
                            if task.done():
                                break
                        await asyncio.sleep(0)

                    while not _event_queue.empty():
                        yield await sse_event(await _event_queue.get())

                    variation = await task

                    # Complete all remaining active plan steps (may be multiple
                    # when instruments ran in parallel)
                    for final_evt in composing_plan_tracker.complete_all_active_steps():
                        yield await sse_event(final_evt)

                    # Mark any steps that were never activated as skipped
                    for skip_evt in composing_plan_tracker.finalize_pending_as_skipped():
                        yield await sse_event(skip_evt)

                except asyncio.TimeoutError:
                    logger.error(
                        f"[{trace.trace_id[:8]}] Variation generation timed out "
                        f"after {_VARIATION_TIMEOUT}s"
                    )
                    yield await sse_event({
                        "type": "error",
                        "message": f"Generation timed out after {_VARIATION_TIMEOUT}s",
                        "traceId": trace.trace_id,
                    })
                    yield await sse_event({
                        "type": "done",
                        "variationId": "",
                        "phraseCount": 0,
                        "status": "failed",
                    })
                    yield await sse_event({
                        "type": "complete",
                        "success": False,
                        "error": "timeout",
                        "traceId": trace.trace_id,
                        **_context_usage_fields(usage_tracker, llm.model),
                    })
                    return

                logger.info(
                    f"[{trace.trace_id[:8]}] Variation computed: "
                    f"{variation.total_changes} changes, {len(variation.phrases)} phrases"
                )

                # Safety: composing with 0 phrases is always a bug (Bug 1).
                if len(variation.phrases) == 0:
                    logger.error(
                        f"[{trace.trace_id[:8]}] COMPOSING produced 0 phrases "
                        f"despite {len(plan.tool_calls)} tool calls — "
                        f"this indicates a generation or entity resolution failure. "
                        f"Proposed notes captured: {sum(len(n) for n in getattr(variation, '_proposed_notes', {}).values()) if hasattr(variation, '_proposed_notes') else 'N/A'}"
                    )

                _store_variation(variation, project_context, store)

                # =============================================================
                # PHRASE STREAMING PHASE: one event per modified region.
                # =============================================================

                note_counts = variation.note_counts
                yield await sse_event({
                    "type": "meta",
                    "variationId": variation.variation_id,
                    "baseStateId": store.get_state_id(),
                    "intent": variation.intent,
                    "aiExplanation": variation.ai_explanation,
                    "affectedTracks": variation.affected_tracks,
                    "affectedRegions": variation.affected_regions,
                    "noteCounts": note_counts,
                })

                for i, phrase in enumerate(variation.phrases):
                    logger.debug(
                        f"[{trace.trace_id[:8]}] Emitting phrase {i + 1}/{len(variation.phrases)}: "
                        f"{len(phrase.note_changes)} note changes"
                    )
                    yield await sse_event({
                        "type": "phrase",
                        "phraseId": phrase.phrase_id,
                        "trackId": phrase.track_id,
                        "regionId": phrase.region_id,
                        "startBeat": phrase.start_beat,
                        "endBeat": phrase.end_beat,
                        "label": phrase.label,
                        "tags": phrase.tags,
                        "explanation": phrase.explanation,
                        "noteChanges": [nc.model_dump(by_alias=True) for nc in phrase.note_changes],
                        "controllerChanges": phrase.controller_changes,
                    })

                yield await sse_event({
                    "type": "done",
                    "variationId": variation.variation_id,
                    "phraseCount": len(variation.phrases),
                })

                logger.info(
                    f"[{trace.trace_id[:8]}] Variation streamed: "
                    f"{variation.total_changes} changes in {len(variation.phrases)} phrases"
                )

                yield await sse_event({
                    "type": "complete",
                    "success": True,
                    "variationId": variation.variation_id,
                    "totalChanges": variation.total_changes,
                    "phraseCount": len(variation.phrases),
                    "traceId": trace.trace_id,
                    **_context_usage_fields(usage_tracker, llm.model),
                })

        except BaseException as e:
            logger.exception(
                f"[{trace.trace_id[:8]}] Variation generation failed: {e}"
            )
            yield await sse_event({
                "type": "error",
                "message": f"Generation failed: {e}",
                "traceId": trace.trace_id,
            })
            yield await sse_event({
                "type": "done",
                "variationId": "",
                "phraseCount": 0,
                "status": "failed",
            })
            yield await sse_event({
                "type": "complete",
                "success": False,
                "error": str(e),
                "traceId": trace.trace_id,
                **_context_usage_fields(usage_tracker, llm.model),
            })
        return
    else:
        # Planner couldn't generate a valid JSON plan
        response_text = plan.llm_response_text if plan else None
        
        # Detect if LLM output looks like function call syntax (not JSON)
        looks_like_function_calls = (
            response_text and 
            ("stori_" in response_text or 
             "add_midi_track(" in response_text or
             "add_notes(" in response_text or
             "add_region(" in response_text)
        )
        
        if looks_like_function_calls:
            # Explicit fallback: planner returned function-call-like text instead of JSON.
            # Re-route as EDITING with primitives so we still get tool calls. See docs/reference/architecture.md.
            async for event in _retry_composing_as_editing(
                prompt, project_context, route, llm, store,
                trace, usage_tracker,
                quality_preset=quality_preset,
            ):
                yield event
            return
        
        # Otherwise, provide guidance to the user
        if response_text:
            # Don't stream raw LLM output that looks malformed
            # Instead, ask for clarification
            yield await sse_event({
                "type": "content",
                "content": "I understand you want to generate music. To help me create exactly what you're looking for, "
                           "could you tell me:\n"
                           "- What style or genre? (e.g., 'lofi', 'jazz', 'electronic')\n"
                           "- What tempo? (e.g., 90 BPM)\n"
                           "- How many bars? (e.g., 8 bars)\n\n"
                           "Example: 'Create an exotic melody at 100 BPM for 8 bars in C minor'",
            })
        else:
            yield await sse_event({
                "type": "content",
                "content": "I need more information to generate music. Please specify:\n"
                           "- Style/genre (e.g., 'boom bap', 'lofi', 'trap')\n"
                           "- Tempo (e.g., 90 BPM)\n"
                           "- Number of bars (e.g., 8 bars)\n\n"
                           "Example: 'Make a boom bap beat at 90 BPM with drums and bass for 8 bars'",
            })
        
        yield await sse_event({
            "type": "complete",
            "success": True,
            "toolCalls": [],
            "traceId": trace.trace_id,
            **_context_usage_fields(usage_tracker, llm.model),
        })


async def _apply_single_tool_call(
    tc_id: str,
    tc_name: str,
    resolved_args: dict[str, Any],
    allowed_tool_names: set[str],
    store: StateStore,
    trace: Any,
    add_notes_failures: dict[str, int],
    emit_sse: bool = True,
) -> _ToolCallOutcome:
    """Validate, enrich, persist, and return results for one tool call.

    Handles entity creation (UUIDs), note persistence, icon synthesis, and
    tool result building. Returns SSE events, LLM message objects, and
    enriched params without yielding — the caller decides whether to yield
    events directly (editing path) or put them into an asyncio.Queue
    (agent-team path).

    Args:
        tc_id: Tool call ID (from LLM response or synthetic UUID).
        tc_name: Tool name (e.g. ``"stori_add_midi_track"``).
        resolved_args: Params after ``$N.field`` variable ref resolution.
        allowed_tool_names: Tool allowlist for validation.
        store: StateStore for entity creation and result building.
        trace: Trace context for logging and spans.
        add_notes_failures: Mutable circuit-breaker counter (modified in-place).
        emit_sse: When ``False``, sse_events is empty (variation/proposal mode).
    """
    sse_events: list[dict[str, Any]] = []

    # ── Circuit breaker: stori_add_notes infinite-retry guard ──
    if tc_name == "stori_add_notes":
        cb_region_id = resolved_args.get("regionId", "__unknown__")
        cb_failures = add_notes_failures.get(cb_region_id, 0)
        if cb_failures >= 3:
            cb_error = (
                f"stori_add_notes: regionId '{cb_region_id}' has failed {cb_failures} times "
                f"without valid notes being added. Stop retrying with shorthand params. "
                f"Provide a real 'notes' array: "
                f"[{{\"pitch\": 60, \"startBeat\": 0, \"durationBeats\": 1, \"velocity\": 80}}, ...]"
            )
            logger.error(f"[{trace.trace_id[:8]}] 🔴 Circuit breaker: {cb_error}")
            if emit_sse:
                sse_events.append({"type": "toolError", "name": tc_name, "error": cb_error})
            msg_call: dict[str, Any] = {
                "role": "assistant",
                "tool_calls": [{"id": tc_id, "type": "function",
                                "function": {"name": tc_name, "arguments": json.dumps(resolved_args)}}],
            }
            msg_result: dict[str, Any] = {
                "role": "tool", "tool_call_id": tc_id,
                "content": json.dumps({"error": cb_error}),
            }
            return _ToolCallOutcome(
                enriched_params=resolved_args,
                tool_result={"error": cb_error},
                sse_events=sse_events,
                msg_call=msg_call,
                msg_result=msg_result,
                skipped=True,
            )

    # ── Validation ──
    with trace_span(trace, f"validate:{tc_name}"):
        validation = validate_tool_call(tc_name, resolved_args, allowed_tool_names, store.registry)

    if not validation.valid:
        log_validation_error(trace.trace_id, tc_name, [str(e) for e in validation.errors])
        if tc_name == "stori_add_notes":
            cb_region_id = resolved_args.get("regionId", "__unknown__")
            add_notes_failures[cb_region_id] = add_notes_failures.get(cb_region_id, 0) + 1
        if emit_sse:
            sse_events.append({
                "type": "toolError",
                "name": tc_name,
                "error": validation.error_message,
                "errors": [str(e) for e in validation.errors],
            })
        error_result = {"error": validation.error_message}
        msg_call = {
            "role": "assistant",
            "tool_calls": [{"id": tc_id, "type": "function",
                            "function": {"name": tc_name, "arguments": json.dumps(resolved_args)}}],
        }
        msg_result = {
            "role": "tool", "tool_call_id": tc_id,
            "content": json.dumps(error_result),
        }
        return _ToolCallOutcome(
            enriched_params=resolved_args,
            tool_result=error_result,
            sse_events=sse_events,
            msg_call=msg_call,
            msg_result=msg_result,
            skipped=True,
        )

    enriched_params = validation.resolved_params

    # ── Entity creation ──
    if tc_name == "stori_add_midi_track":
        track_name = enriched_params.get("name", "Track")
        instrument = enriched_params.get("instrument")
        gm_program = enriched_params.get("gmProgram")
        if "trackId" in enriched_params:
            logger.warning(
                f"⚠️ LLM provided trackId '{enriched_params['trackId']}' for NEW track '{track_name}'. "
                f"Ignoring and generating fresh UUID to prevent duplicates."
            )
        track_id = store.create_track(track_name)
        enriched_params["trackId"] = track_id
        logger.debug(f"🔑 Generated trackId: {track_id[:8]} for '{track_name}'")
        if gm_program is None:
            from app.core.gm_instruments import infer_gm_program_with_context
            inference = infer_gm_program_with_context(track_name=track_name, instrument=instrument)
            enriched_params["_gmInstrumentName"] = inference.instrument_name
            enriched_params["_isDrums"] = inference.is_drums
            logger.info(
                f"🎵 GM inference for '{track_name}': "
                f"program={inference.program}, instrument={inference.instrument_name}, "
                f"is_drums={inference.is_drums}"
            )
            if inference.needs_program_change:
                enriched_params["gmProgram"] = inference.program

    elif tc_name == "stori_add_midi_region":
        midi_region_track_id: Optional[str] = enriched_params.get("trackId")
        region_name: str = str(enriched_params.get("name", "Region"))
        if "regionId" in enriched_params:
            logger.warning(
                f"⚠️ LLM provided regionId '{enriched_params['regionId']}' for NEW region '{region_name}'. "
                f"Ignoring and generating fresh UUID to prevent duplicates."
            )
        if midi_region_track_id:
            try:
                region_id = store.create_region(
                    region_name, midi_region_track_id,
                    metadata={
                        "startBeat": enriched_params.get("startBeat", 0),
                        "durationBeats": enriched_params.get("durationBeats", 16),
                    }
                )
                enriched_params["regionId"] = region_id
                logger.debug(f"🔑 Generated regionId: {region_id[:8]} for '{region_name}'")
            except ValueError as e:
                logger.error(f"Failed to create region: {e}")
                error_result = {"success": False, "error": f"Failed to create region: {e}"}
                msg_call = {
                    "role": "assistant",
                    "tool_calls": [{"id": tc_id, "type": "function",
                                    "function": {"name": tc_name, "arguments": json.dumps(enriched_params)}}],
                }
                msg_result = {
                    "role": "tool", "tool_call_id": tc_id,
                    "content": json.dumps(error_result),
                }
                return _ToolCallOutcome(
                    enriched_params=enriched_params,
                    tool_result=error_result,
                    sse_events=sse_events,
                    msg_call=msg_call,
                    msg_result=msg_result,
                    skipped=True,
                )
        else:
            logger.error(
                f"⚠️ stori_add_midi_region called without trackId for region '{region_name}'"
            )
            error_result = {
                "success": False,
                "error": (
                    f"Cannot create region '{region_name}' — no trackId provided. "
                    "Use $N.trackId to reference a track created in a prior tool call, "
                    "or use trackName for name-based resolution."
                ),
            }
            msg_call = {
                "role": "assistant",
                "tool_calls": [{"id": tc_id, "type": "function",
                                "function": {"name": tc_name, "arguments": json.dumps(enriched_params)}}],
            }
            msg_result = {
                "role": "tool", "tool_call_id": tc_id,
                "content": json.dumps(error_result),
            }
            return _ToolCallOutcome(
                enriched_params=enriched_params,
                tool_result=error_result,
                sse_events=sse_events,
                msg_call=msg_call,
                msg_result=msg_result,
                skipped=True,
            )

    elif tc_name == "stori_duplicate_region":
        source_region_id: str = enriched_params.get("regionId", "")
        source_entity = store.registry.get_region(source_region_id)
        if source_entity:
            copy_name = f"{source_entity.name} (copy)"
            parent_track_id = source_entity.parent_id or ""
            try:
                new_region_id = store.create_region(
                    copy_name, parent_track_id,
                    metadata={"startBeat": enriched_params.get("startBeat", 0)},
                )
                enriched_params["newRegionId"] = new_region_id
                logger.debug(
                    f"🔑 Generated newRegionId: {new_region_id[:8]} "
                    f"for duplicate of '{source_entity.name}'"
                )
            except ValueError as e:
                logger.error(f"Failed to register duplicate region: {e}")

    elif tc_name == "stori_ensure_bus":
        bus_name = enriched_params.get("name", "Bus")
        if "busId" in enriched_params:
            logger.warning(
                f"⚠️ LLM provided busId '{enriched_params['busId']}' for bus '{bus_name}'. "
                f"Ignoring to prevent duplicates."
            )
        bus_id = store.get_or_create_bus(bus_name)
        enriched_params["busId"] = bus_id

    # ── SSE events (toolStart + toolCall) ──
    extra_tool_calls: list[dict[str, Any]] = []
    if emit_sse:
        emit_params = _enrich_params_with_track_context(enriched_params, store)
        sse_events.append({
            "type": "toolStart",
            "name": tc_name,
            "label": _human_label_for_tool(tc_name, emit_params),
        })
        sse_events.append({
            "type": "toolCall",
            "id": tc_id,
            "name": tc_name,
            "params": emit_params,
        })

    log_tool_call(trace.trace_id, tc_name, enriched_params, True)

    # ── Synthetic stori_set_track_icon after stori_add_midi_track ──
    if tc_name == "stori_add_midi_track" and emit_sse:
        _icon_track_id = enriched_params.get("trackId", "")
        _drum_kit = enriched_params.get("drumKitId")
        _is_drums = enriched_params.get("_isDrums", False)
        _gm_program = enriched_params.get("gmProgram")
        if _drum_kit or _is_drums:
            _track_icon: Optional[str] = DRUM_ICON
        elif _gm_program is not None:
            _track_icon = icon_for_gm_program(int(_gm_program))
        else:
            _track_icon = None
        if _track_icon and _icon_track_id:
            _icon_params: dict[str, Any] = {"trackId": _icon_track_id, "icon": _track_icon}
            sse_events.append({
                "type": "toolStart",
                "name": "stori_set_track_icon",
                "label": f"Setting icon for {enriched_params.get('name', 'track')}",
            })
            sse_events.append({
                "type": "toolCall",
                "id": f"{tc_id}-icon",
                "name": "stori_set_track_icon",
                "params": _icon_params,
            })
            extra_tool_calls.append({"tool": "stori_set_track_icon", "params": _icon_params})
            logger.debug(
                f"🎨 Synthetic icon '{_track_icon}' → trackId {_icon_track_id[:8]} "
                f"({'drum kit' if (_drum_kit or _is_drums) else f'GM {_gm_program}'})"
            )

    # ── Note persistence ──
    if tc_name == "stori_add_notes":
        _notes = enriched_params.get("notes", [])
        _rid = enriched_params.get("regionId", "")
        if _rid and _notes:
            store.add_notes(_rid, _notes)
            logger.debug(
                f"📝 Persisted {len(_notes)} notes for region {_rid[:8]} in StateStore"
            )
        add_notes_failures.pop(enriched_params.get("regionId", "__unknown__"), None)

    # ── Message objects for LLM conversation history ──
    if tc_name == "stori_add_notes":
        notes = enriched_params.get("notes", [])
        summary_params = {k: v for k, v in enriched_params.items() if k != "notes"}
        summary_params["_noteCount"] = len(notes)
        if notes:
            starts = [n["startBeat"] for n in notes]
            summary_params["_beatRange"] = [min(starts), max(starts)]
        msg_arguments = json.dumps(summary_params)
    else:
        msg_arguments = json.dumps(enriched_params)

    msg_call = {
        "role": "assistant",
        "tool_calls": [{"id": tc_id, "type": "function",
                        "function": {"name": tc_name, "arguments": msg_arguments}}],
    }

    # ── Tool result ──
    tool_result = _build_tool_result(tc_name, enriched_params, store)
    msg_result = {
        "role": "tool", "tool_call_id": tc_id,
        "content": json.dumps(tool_result),
    }

    return _ToolCallOutcome(
        enriched_params=enriched_params,
        tool_result=tool_result,
        sse_events=sse_events,
        msg_call=msg_call,
        msg_result=msg_result,
        skipped=False,
        extra_tool_calls=extra_tool_calls,
    )


async def _handle_editing(
    prompt: str,
    project_context: dict[str, Any],
    route,
    llm: LLMClient,
    store: StateStore,
    trace,
    usage_tracker: Optional[UsageTracker],
    conversation_history: list[dict[str, Any]],
    execution_mode: str = "apply",
    is_cancelled: Optional[Callable[[], Awaitable[bool]]] = None,
    quality_preset: Optional[str] = None,
) -> AsyncIterator[str]:
    """Handle EDITING state - LLM tool calls with allowlist + validation.
    
    Args:
        execution_mode: "apply" for immediate mutation, "variation" for proposal mode
        is_cancelled: async callback returning True if the client disconnected
    """
    status_msg = "Processing..." if execution_mode == "apply" else "Generating variation..."
    yield await sse_event({"type": "status", "message": status_msg})
    
    # Use composition-specific prompt when GENERATE_MUSIC was re-routed to EDITING
    if route.intent == Intent.GENERATE_MUSIC:
        sys_prompt = system_prompt_base() + "\n" + editing_composition_prompt()
    else:
        required_single = bool(route.force_stop_after and route.tool_choice == "required")
        sys_prompt = system_prompt_base() + "\n" + editing_prompt(required_single)

    # Inject structured context from structured prompt if present
    _slots = getattr(route, "slots", None)
    _extras = getattr(_slots, "extras", None) if _slots is not None else None
    parsed: Optional[ParsedPrompt] = _extras.get("parsed_prompt") if isinstance(_extras, dict) else None
    if parsed is not None:
        sys_prompt += structured_prompt_context(parsed)
        # Inject sequential placement context when After: is present
        if parsed.position is not None:
            start_beat = resolve_position(parsed.position, project_context or {})
            sys_prompt += sequential_context(start_beat, parsed.section, pos=parsed.position)

    # Build allowed tools only (Cursor-style action space shaping)
    allowed_tools = [t for t in ALL_TOOLS if t["function"]["name"] in route.allowed_tool_names]

    messages: list[dict[str, Any]] = [{"role": "system", "content": sys_prompt}]

    # Inject project context — prefer the request-body snapshot (authoritative),
    # fall back to the entity registry for sessions that don't send project.
    if project_context:
        messages.append({"role": "system", "content": format_project_context(project_context)})
    else:
        messages.append({"role": "system", "content": build_entity_context_for_llm(store)})

    if conversation_history:
        messages.extend(conversation_history)
    
    messages.append({"role": "user", "content": wrap_user_request(prompt)})
    
    # Use higher token budget for composition (multi-track MIDI data is token-heavy)
    is_composition = route.intent == Intent.GENERATE_MUSIC
    llm_max_tokens: Optional[int] = settings.composition_max_tokens if is_composition else None
    reasoning_fraction: Optional[float] = settings.composition_reasoning_fraction if is_composition else None
    
    tool_calls_collected: list[dict[str, Any]] = []
    plan_tracker: Optional[_PlanTracker] = None
    iteration = 0
    # Circuit breaker: track stori_add_notes failures per regionId within this session.
    # If the same regionId fails 3+ times we return a hard error so the model stops looping.
    _add_notes_failures: dict[str, int] = {}
    max_iterations = (
        settings.composition_max_iterations if is_composition
        else settings.orchestration_max_iterations
    )

    # For composition with a structured prompt, emit the full TODO list immediately
    # before any LLM call so the user sees the plan the moment they submit.
    if is_composition and parsed is not None and execution_mode == "apply":
        plan_tracker = _PlanTracker()
        plan_tracker.build_from_prompt(parsed, prompt, project_context or {})
        yield await sse_event(plan_tracker.to_plan_event())
    
    while iteration < max_iterations:
        iteration += 1

        # Check for client disconnect before each LLM call
        if is_cancelled:
            try:
                if await is_cancelled():
                    logger.info(
                        f"[{trace.trace_id[:8]}] 🛑 Client disconnected, "
                        f"stopping at iteration {iteration}"
                    )
                    break
            except Exception:
                pass  # Swallow errors from disconnect check

        logger.info(
            f"[{trace.trace_id[:8]}] 🔄 Editing iteration {iteration}/{max_iterations} "
            f"(composition={is_composition})"
        )

        with trace_span(trace, f"llm_iteration_{iteration}"):
            start_time = time.time()
            
            # Use streaming for reasoning models
            if llm.supports_reasoning():
                response = None
                async for item in _stream_llm_response(
                    llm, messages, allowed_tools, route.tool_choice,
                    trace, lambda data: sse_event(data),
                    max_tokens=llm_max_tokens,
                    reasoning_fraction=reasoning_fraction,
                    suppress_content=True,
                ):
                    # Check if this is the final response marker
                    if isinstance(item, StreamFinalResponse):
                        response = item.response
                    else:
                        # Reasoning events — forward to client
                        yield item
            else:
                response = await llm.chat_completion(
                    messages=messages,
                    tools=allowed_tools,
                    tool_choice=route.tool_choice,
                    temperature=settings.orchestration_temperature,
                    max_tokens=llm_max_tokens,
                )
            
            duration_ms = (time.time() - start_time) * 1000
            
            if response is None:
                break
            if response.usage:
                log_llm_call(
                    trace.trace_id,
                    llm.model,
                    response.usage.get("prompt_tokens", 0),
                    response.usage.get("completion_tokens", 0),
                    duration_ms,
                    response.has_tool_calls,
                )
                if usage_tracker:
                    usage_tracker.add(
                        response.usage.get("prompt_tokens", 0),
                        response.usage.get("completion_tokens", 0),
                    )
        
        # Enforce single tool for force_stop_after
        if response is None:
            break
        if route.force_stop_after:
            response = enforce_single_tool(response)

        # Emit filtered content — strip leaked tool-call syntax
        # (e.g. "(key=\"G major\")", "(,, )") while keeping
        # natural-language text the user should see.
        if response.content:
            clean_content = strip_tool_echoes(response.content)
            if clean_content:
                yield await sse_event({"type": "content", "content": clean_content})

        # No more tool calls — fall through to continuation check
        if not response.has_tool_calls:
            # For non-composition, no tool calls means we're done
            if not is_composition:
                break
            # For composition, fall through to the unified continuation
            # check after the tool-call processing block
        
        # Accumulates tool results within this iteration so $N.field refs
        # in later tool calls can reference outputs of earlier ones.
        iter_tool_results: list[dict[str, Any]] = []

        # ── Plan tracking: build plan from first batch of tool calls ──
        # Only emit a plan when there are 2+ distinct steps — a single-step
        # plan (e.g. "set tempo") is noise; the toolStart label is sufficient.
        if (
            plan_tracker is None
            and response is not None
            and response.has_tool_calls
            and execution_mode == "apply"
        ):
            _candidate = _PlanTracker()
            _candidate.build(
                response.tool_calls, prompt, project_context,
                is_composition, store,
            )
            if len(_candidate.steps) >= 2:
                plan_tracker = _candidate
                yield await sse_event(plan_tracker.to_plan_event())
        elif (
            plan_tracker is not None
            and response is not None
            and response.has_tool_calls
            and execution_mode == "apply"
        ):
            # Subsequent iterations: activate steps matched by name/context
            for tc in response.tool_calls:
                resolved = _resolve_variable_refs(tc.params, iter_tool_results)
                step = plan_tracker.find_step_for_tool(tc.name, resolved, store)
                if step and step.status == "pending":
                    yield await sse_event(plan_tracker.activate_step(step.step_id))

        # Process tool calls with validation
        for tc_idx, tc in enumerate(response.tool_calls):
            # Resolve $N.fieldName variable references before validation so
            # the substituted IDs pass entity-existence checks correctly.
            resolved_args = _resolve_variable_refs(tc.params, iter_tool_results)

            # ── Plan step tracking: activate step for this tool call ──
            if plan_tracker and execution_mode == "apply":
                step = plan_tracker.step_for_tool_index(tc_idx)
                if step is None:
                    step = plan_tracker.find_step_for_tool(
                        tc.name, resolved_args, store,
                    )
                if step and step.step_id != plan_tracker._active_step_id:
                    if plan_tracker._active_step_id:
                        evt = plan_tracker.complete_active_step()
                        if evt:
                            yield await sse_event(evt)
                    yield await sse_event(
                        plan_tracker.activate_step(step.step_id)
                    )

            # ── Execute the tool call (validation, entity creation, SSE) ──
            outcome = await _apply_single_tool_call(
                tc_id=tc.id,
                tc_name=tc.name,
                resolved_args=resolved_args,
                allowed_tool_names=route.allowed_tool_names,
                store=store,
                trace=trace,
                add_notes_failures=_add_notes_failures,
                emit_sse=(execution_mode == "apply"),
            )

            # Forward SSE events to client
            for evt in outcome.sse_events:
                yield await sse_event(evt)

            # Accumulate tool calls and plan step results for successful calls
            if not outcome.skipped:
                tool_calls_collected.append({"tool": tc.name, "params": outcome.enriched_params})
                tool_calls_collected.extend(outcome.extra_tool_calls)

                # ── Plan step tracking: accumulate result description ──
                if plan_tracker and execution_mode == "apply":
                    _step = (
                        plan_tracker.step_for_tool_index(tc_idx)
                        or plan_tracker.find_step_for_tool(
                            tc.name, outcome.enriched_params, store
                        )
                    )
                    if _step:
                        _step.result = _build_step_result(
                            tc.name, outcome.enriched_params, _step.result,
                        )

            # Add to LLM conversation history (always — errors need context too)
            messages.append(outcome.msg_call)
            iter_tool_results.append(outcome.tool_result)
            messages.append(outcome.msg_result)
        
        # ── Plan step tracking: complete last active step this iteration ──
        if plan_tracker and plan_tracker._active_step_id and execution_mode == "apply":
            evt = plan_tracker.complete_active_step()
            if evt:
                yield await sse_event(evt)

        # ── Entity snapshot injection between iterations ──
        # After all tool calls in this batch, inject an updated entity
        # manifest as a system message. This is the fundamental fix that
        # prevents the model from losing track of created entities (empty
        # regionId, re-adding notes, etc.) between tool call batches.
        if response is not None and response.has_tool_calls:
            snapshot = _entity_manifest(store)
            messages.append({
                "role": "system",
                "content": (
                    "ENTITY STATE AFTER TOOL CALLS (authoritative — use these IDs):\n"
                    + json.dumps(snapshot, indent=None)
                    + "\nUse the IDs above for subsequent tool calls. "
                    "Do NOT re-add notes to regions that already have notes (check noteCount). "
                    "Do NOT call stori_clear_notes unless explicitly replacing content. "
                    "A successful stori_add_notes response means the notes were stored — "
                    "do not redo the call."
                ),
            })

        # Force stop after first tool execution
        if route.force_stop_after and tool_calls_collected:
            logger.info(f"[{trace.trace_id[:8]}] ✅ Force stop after {len(tool_calls_collected)} tool(s)")
            break

        # ── Composition continuation: always check after tool calls ──
        # The LLM may return tool calls every iteration but never finish
        # all tracks. We must check and re-prompt regardless of whether
        # tool calls were present or what finish_reason says.
        if is_composition and iteration < max_iterations:
            all_tracks = store.registry.list_tracks()
            incomplete = _get_incomplete_tracks(store, tool_calls_collected)

            if not all_tracks:
                # No tracks created yet — the composition hasn't started
                continuation = (
                    "You haven't created any tracks yet. "
                    "Use stori_add_midi_track to create the instruments, "
                    "then stori_add_midi_region and stori_add_notes for each."
                )
                messages.append({"role": "user", "content": continuation})
                logger.info(
                    f"[{trace.trace_id[:8]}] 🔄 Continuation: no tracks yet "
                    f"(iteration {iteration})"
                )
                continue
            elif incomplete:
                # ── Plan tracking: mark completed track steps ──
                # Only mark a step completed if its track actually EXISTS in the
                # registry AND is not incomplete. A track that was never created
                # must stay pending — not be falsely marked completed (Bug 4).
                if plan_tracker and execution_mode == "apply":
                    incomplete_set = set(incomplete)
                    existing_track_names = {
                        t.name for t in store.registry.list_tracks()
                    }
                    for _step in plan_tracker.steps:
                        if (
                            _step.track_name
                            and _step.status in ("active", "pending")
                            and _step.track_name in existing_track_names
                            and _step.track_name not in incomplete_set
                        ):
                            yield await sse_event(
                                plan_tracker.complete_step_by_id(
                                    _step.step_id,
                                    f"Created {_step.track_name}",
                                )
                            )
                    # Inject plan progress so LLM knows where it is
                    messages.append({
                        "role": "system",
                        "content": plan_tracker.progress_context(),
                    })

                continuation = (
                    f"Continue — these tracks still need regions and notes: "
                    f"{', '.join(incomplete)}. "
                    f"Call stori_add_midi_region AND stori_add_notes together for each track. "
                    f"Use multiple tool calls in one response."
                )
                messages.append({"role": "user", "content": continuation})
                logger.info(
                    f"[{trace.trace_id[:8]}] 🔄 Continuation: {len(incomplete)} tracks still need content "
                    f"(iteration {iteration})"
                )
                continue
            else:
                # All tracks have content — now check expressive steps
                missing_expressive = _get_missing_expressive_steps(
                    parsed, tool_calls_collected
                )
                if missing_expressive:
                    # Include entity manifest so the model has IDs without looking them up
                    entity_snapshot = _entity_manifest(store)
                    # System-level guard prevents the model from regressing to track creation
                    messages.append({
                        "role": "system",
                        "content": (
                            "EXPRESSIVE PHASE LOCK: All tracks have been created and have notes. "
                            "You MUST NOT call stori_add_midi_track, stori_add_midi_region, "
                            "stori_add_notes, or any track/region creation tool. "
                            "Only call: stori_add_insert_effect, stori_add_midi_cc, "
                            "stori_add_pitch_bend, stori_add_automation, stori_ensure_bus, stori_add_send."
                        ),
                    })
                    expressive_msg = (
                        "⚠️ EXPRESSIVE PHASE — call ALL of these in ONE batch, then stop:\n"
                        + "\n".join(f"  {i+1}. {m}" for i, m in enumerate(missing_expressive))
                        + f"\n\nEntity IDs for your calls:\n{entity_snapshot}"
                        + "\n\nBatch ALL tool calls in a single response. No text. Just the tool calls."
                    )
                    messages.append({"role": "user", "content": expressive_msg})
                    logger.info(
                        f"[{trace.trace_id[:8]}] 🔄 Continuation: {len(missing_expressive)} "
                        f"expressive step(s) pending (iteration {iteration})"
                    )
                    continue
                # All done — tracks + expressive steps complete
                logger.info(
                    f"[{trace.trace_id[:8]}] ✅ All tracks and expressive steps done "
                    f"after iteration {iteration}"
                )
                break

        # ── Non-composition: stop after executing tool calls ──
        # For non-composition editing, the LLM should batch everything it
        # needs in one response.  Don't re-prompt — that causes runaway loops
        # where the LLM keeps adding notes indefinitely.
        # Only continue if there were NO tool calls (content-only response).
        if not is_composition:
            if response is not None and response.has_tool_calls:
                logger.info(
                    f"[{trace.trace_id[:8]}] ✅ Non-composition: executed "
                    f"{len(response.tool_calls)} tool(s), stopping after iteration {iteration}"
                )
                break
            # No tool calls — LLM is done (emitted content-only response)
            break
    
    # =========================================================================
    # Variation Mode: Compute and emit variation per spec (meta/phrase/done)
    # =========================================================================
    if execution_mode == "variation" and tool_calls_collected:
        from app.core.executor import execute_plan_variation
        from app.core.expansion import ToolCall
        
        # Convert collected tool calls to ToolCall objects
        tool_call_objs = [
            ToolCall(name=cast(str, tc["tool"]), params=cast(dict[str, Any], tc["params"]))
            for tc in tool_calls_collected
        ]
        
        variation = await execute_plan_variation(
            tool_calls=tool_call_objs,
            project_state=project_context,
            intent=prompt,
            # Use the editing flow's store so region entities created during
            # execution are visible when _store_variation builds PhraseRecords,
            # and so commit can find the same store later.
            conversation_id=store.conversation_id,
            explanation=None,
            quality_preset=quality_preset,
        )

        # Persist to VariationStore so commit/discard can find it
        _store_variation(variation, project_context, store)

        # Emit meta event (overall summary per spec)
        note_counts = variation.note_counts
        yield await sse_event({
            "type": "meta",
            "variationId": variation.variation_id,
            "baseStateId": store.get_state_id(),
            "intent": variation.intent,
            "aiExplanation": variation.ai_explanation,
            "affectedTracks": variation.affected_tracks,
            "affectedRegions": variation.affected_regions,
            "noteCounts": note_counts,
        })
        
        # Emit individual phrase events (per spec)
        for phrase in variation.phrases:
            yield await sse_event({
                "type": "phrase",
                "phraseId": phrase.phrase_id,
                "trackId": phrase.track_id,
                "regionId": phrase.region_id,
                "startBeat": phrase.start_beat,
                "endBeat": phrase.end_beat,
                "label": phrase.label,
                "tags": phrase.tags,
                "explanation": phrase.explanation,
                "noteChanges": [nc.model_dump(by_alias=True) for nc in phrase.note_changes],
                "controllerChanges": phrase.controller_changes,
            })
        
        # Emit done event (per spec)
        yield await sse_event({
            "type": "done",
            "variationId": variation.variation_id,
            "phraseCount": len(variation.phrases),
        })
        
        logger.info(
            f"[{trace.trace_id[:8]}] EDITING variation streamed: "
            f"{variation.total_changes} changes in {len(variation.phrases)} phrases"
        )
        
        # Complete event
        yield await sse_event({
            "type": "complete",
            "success": True,
            "variationId": variation.variation_id,
            "totalChanges": variation.total_changes,
            "phraseCount": len(variation.phrases),
            "traceId": trace.trace_id,
            **_context_usage_fields(usage_tracker, llm.model),
        })
        return
    
    # =========================================================================
    # Apply Mode: Standard completion (existing behavior)
    # =========================================================================

    # Mark any plan steps that were never activated as skipped
    if plan_tracker:
        for skip_evt in plan_tracker.finalize_pending_as_skipped():
            yield await sse_event(skip_evt)

    yield await sse_event({
        "type": "complete",
        "success": True,
        "toolCalls": tool_calls_collected,
        "stateVersion": store.version,
        "traceId": trace.trace_id,
        **_context_usage_fields(usage_tracker, llm.model),
    })


# =============================================================================
# Agent Teams — parallel instrument execution
# =============================================================================

async def _run_instrument_agent(
    instrument_name: str,
    role: str,
    style: str,
    bars: int,
    tempo: float,
    key: str,
    step_ids: list[str],
    plan_tracker: _PlanTracker,
    llm: LLMClient,
    store: StateStore,
    allowed_tool_names: set[str],
    trace: Any,
    sse_queue: "asyncio.Queue[dict[str, Any]]",
    collected_tool_calls: list[dict[str, Any]],
    existing_track_id: Optional[str] = None,
    start_beat: int = 0,
) -> None:
    """Independent instrument agent: dedicated multi-turn LLM session per instrument.

    Each invocation is a genuinely concurrent HTTP session running simultaneously
    with sibling agents via ``asyncio.gather``. The agent loops over LLM turns
    until all tool calls complete (create track → region → notes → effect).

    When ``existing_track_id`` is provided the track already exists in the
    project; the agent skips ``stori_add_midi_track`` and places new regions
    starting at ``start_beat`` (the beat immediately after the last existing
    region on that track).

    SSE events are written to ``sse_queue`` (forwarded to client by coordinator).
    All executed tool calls are appended to ``collected_tool_calls`` for the
    summary.final event.

    Failure is isolated: an exception marks only this agent's plan steps as
    failed and does not propagate to sibling agents.
    """
    agent_log = f"[{trace.trace_id[:8]}][{instrument_name}Agent]"
    # Use bool() so empty strings (e.g. from a client that omits "trackId" in
    # project_context) don't incorrectly trigger the reuse path with an invalid
    # empty-string trackId injected into the system prompt.
    reusing = bool(existing_track_id)
    logger.info(
        f"{agent_log} Starting — style={style}, bars={bars}, tempo={tempo}, key={key}"
        + (f", reusing trackId={existing_track_id}, startBeat={start_beat}" if reusing else "")
    )

    beat_count = bars * 4
    if reusing:
        # Track already exists — skip creation, append after existing content.
        system_content = (
            f"You are a music production agent. Your ONLY job is to add new content to the "
            f"existing **{instrument_name}** track for this {style} composition. "
            f"You must complete ALL steps before stopping.\n\n"
            f"Project context:\n"
            f"- Tempo: {tempo} BPM | Key: {key} | Style: {style} | Length: {bars} bars ({beat_count} beats)\n\n"
            f"IMPORTANT — track already exists:\n"
            f"- The {instrument_name} track already exists with trackId='{existing_track_id}'.\n"
            f"- DO NOT call stori_add_midi_track. Use '{existing_track_id}' directly as trackId.\n"
            f"- Existing content ends at beat {start_beat}. Start all new regions at beat {start_beat}.\n\n"
            f"Required pipeline — execute ALL steps in order:\n"
            f"1. stori_add_midi_region — add a {beat_count}-beat region starting at beat {start_beat} "
            f"on trackId='{existing_track_id}'\n"
            f"2. Generate content — use ONE of:\n"
            f"   • stori_add_notes — for hand-crafted MIDI with specific pitches\n"
            f"   • stori_generate_drums — for drum patterns (drums only)\n"
            f"   • stori_generate_bass — for bass lines\n"
            f"   • stori_generate_midi — for melodic/chord parts\n"
            f"   Use $0.regionId for regionId, trackId='{existing_track_id}'\n"
            f"3. stori_add_insert_effect — add one appropriate effect to trackId='{existing_track_id}'\n\n"
            f"IMPORTANT:\n"
            f"- Do NOT call stori_add_midi_track — the track already exists.\n"
            f"- Start the region at beat {start_beat}, NOT beat 0.\n"
            f"- Do NOT create tracks for other instruments.\n"
            f"- Make all tool calls now — the pipeline is not complete until step 3.\n"
            f"- Do not add any text response — only tool calls."
        )
    else:
        system_content = (
            f"You are a music production agent. Your ONLY job is to fully build the "
            f"**{instrument_name}** track for this {style} composition. "
            f"You must complete ALL steps before stopping.\n\n"
            f"Project context:\n"
            f"- Tempo: {tempo} BPM | Key: {key} | Style: {style} | Length: {bars} bars ({beat_count} beats)\n\n"
            f"Required pipeline — execute ALL steps in order:\n"
            f"1. stori_add_midi_track — create the {instrument_name} track\n"
            f"2. stori_add_midi_region — add a {beat_count}-beat region at beat 0 "
            f"(use $0.trackId for trackId)\n"
            f"3. Generate content — use ONE of:\n"
            f"   • stori_add_notes — for hand-crafted MIDI with specific pitches\n"
            f"   • stori_generate_drums — for drum patterns (drums only)\n"
            f"   • stori_generate_bass — for bass lines\n"
            f"   • stori_generate_midi — for melodic/chord parts\n"
            f"   Use $1.regionId for regionId, $0.trackId for trackId\n"
            f"4. stori_add_insert_effect — add one appropriate effect to the track\n\n"
            f"IMPORTANT:\n"
            f"- Do NOT stop after step 1. You MUST complete all 4 steps.\n"
            f"- Do NOT create tracks for other instruments.\n"
            f"- Make all tool calls now — the pipeline is not complete until step 4.\n"
            f"- Do not add any text response — only tool calls."
        )

    agent_tools = [
        t for t in ALL_TOOLS
        if t["function"]["name"] in _INSTRUMENT_AGENT_TOOLS
    ]
    if reusing:
        user_message = (
            f"Add a new {style} section to the existing {instrument_name} track "
            f"(trackId='{existing_track_id}') starting at beat {start_beat}. "
            f"Execute all 3 steps: add region at beat {start_beat}, generate content, add effect. "
            f"Do NOT create a new track. Make all tool calls in this response."
        )
    else:
        user_message = (
            f"Build the complete {instrument_name} track now. "
            f"Execute all 4 steps: create track, add region, generate content, add effect. "
            f"Make all tool calls in this response."
        )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_message},
    ]

    add_notes_failures: dict[str, int] = {}
    active_step_id: Optional[str] = None
    all_tool_results: list[dict[str, Any]] = []
    max_turns = 4  # 1 turn should be enough; cap to prevent runaway loops

    # Track which pipeline stages have been completed so we can prompt for missing ones.
    # When reusing an existing track, the create-track stage is pre-completed and the
    # continuation reminder uses start_beat instead of 0 for the region step.
    _stage_track = reusing  # already done when reusing
    _stage_region = False
    _stage_region_ok = False   # True only when region creation SUCCEEDED (has regionId)
    _stage_content = False     # notes OR a generator call
    _stage_effect = False

    def _missing_stages() -> list[str]:
        missing = []
        if not _stage_region:
            region_beat = start_beat if reusing else 0
            track_ref = f"trackId='{existing_track_id}'" if reusing else "$0.trackId"
            missing.append(
                f"stori_add_midi_region (add a {bars * 4}-beat region at beat {region_beat} "
                f"on {track_ref})"
            )
        if not _stage_content:
            missing.append("stori_generate_drums / stori_generate_bass / stori_add_notes (add musical content)")
        if not _stage_effect:
            missing.append("stori_add_insert_effect (add one insert effect)")
        return missing

    for turn in range(max_turns):
        # After turn 0, inject a continuation prompt if the pipeline is incomplete
        if turn > 0:
            missing = _missing_stages()
            if not missing:
                break  # All done — no more turns needed
            reminder = (
                "You have not finished the pipeline. You MUST still call:\n"
                + "\n".join(f"  • {m}" for m in missing)
                + "\nMake these tool calls now."
            )
            messages.append({"role": "user", "content": reminder})

        # ── LLM call ──
        try:
            response = await llm.chat_completion(
                messages=messages,
                tools=agent_tools,
                tool_choice="required",
                max_tokens=settings.composition_max_tokens,
            )
        except Exception as exc:
            logger.error(f"{agent_log} LLM call failed (turn {turn}): {exc}")
            for step_id in step_ids:
                step = next((s for s in plan_tracker.steps if s.step_id == step_id), None)
                if step and step.status in ("pending", "active"):
                    step.status = "failed"
                    await sse_queue.put({
                        "type": "planStepUpdate",
                        "stepId": step_id,
                        "status": "failed",
                        "result": f"Failed: {exc}",
                    })
            return

        logger.info(f"{agent_log} Turn {turn}: {len(response.tool_calls)} tool call(s)")

        if not response.tool_calls:
            break  # LLM made no calls — add continuation prompt on next iteration

        # Build the assistant message from tool calls for multi-turn continuation
        assistant_tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.params)},
            }
            for tc in response.tool_calls
        ]
        messages.append({"role": "assistant", "content": None, "tool_calls": assistant_tool_calls})

        # ── Process tool calls in this turn ──
        turn_tool_results: list[dict[str, Any]] = []
        tool_result_messages: list[dict[str, Any]] = []

        for tc in response.tool_calls:
            resolved_args = _resolve_variable_refs(tc.params, all_tool_results)

            # Index-based step progression: track creation tools (stori_add_midi_track)
            # map to the first step; all content/generator/effect tools map to the second
            # step. This avoids find_step_for_tool returning the creation step (still
            # "active") when content tools fire immediately after track creation.
            if tc.name in _TRACK_CREATION_NAMES:
                desired_step_id = step_ids[0] if step_ids else None
            elif step_ids:
                # Any non-track-creation tool advances to the content step
                desired_step_id = step_ids[1] if len(step_ids) > 1 else step_ids[0]
            else:
                desired_step_id = None

            if desired_step_id and desired_step_id != active_step_id:
                if active_step_id:
                    evt = plan_tracker.complete_step_by_id(active_step_id)
                    if evt:
                        await sse_queue.put(evt)
                activate_evt = plan_tracker.activate_step(desired_step_id)
                await sse_queue.put(activate_evt)
                active_step_id = desired_step_id

            # Guard: skip effect tools when no region was successfully created.
            # A failed stori_add_midi_region (e.g. "region already exists" from
            # a trackId collision) means the subsequent note and effect calls
            # would target a region that doesn't belong to this agent, causing
            # double-stacked effects and orphaned note writes.
            if tc.name in _EFFECT_TOOL_NAMES and not _stage_region_ok and reusing:
                logger.warning(
                    f"{agent_log} Skipping {tc.name} — region was not created successfully. "
                    f"This prevents adding effects to the wrong track."
                )
                tool_result_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"skipped": True, "reason": "region creation did not succeed"}),
                })
                continue

            outcome = await _apply_single_tool_call(
                tc_id=tc.id,
                tc_name=tc.name,
                resolved_args=resolved_args,
                allowed_tool_names=allowed_tool_names,
                store=store,
                trace=trace,
                add_notes_failures=add_notes_failures,
                emit_sse=True,
            )

            # Enrich events with agentId so frontend can correlate to instrument
            for evt in outcome.sse_events:
                if evt.get("type") in ("toolCall", "toolStart", "toolError"):
                    evt = {**evt, "agentId": instrument_name.lower()}
                await sse_queue.put(evt)

            # Update step result for the currently active step
            if not outcome.skipped and active_step_id:
                active_step = next(
                    (s for s in plan_tracker.steps if s.step_id == active_step_id), None
                )
                if active_step:
                    active_step.result = _build_step_result(
                        tc.name, outcome.enriched_params, active_step.result
                    )

            # Track pipeline stage completion for continuation prompting.
            # _stage_region_ok is set only when the region was created successfully
            # (outcome.tool_result contains a regionId), so the effect guard above
            # can distinguish a real success from a validation/collision failure.
            if tc.name in _TRACK_CREATION_NAMES:
                _stage_track = True
            elif tc.name in {"stori_add_midi_region"}:
                _stage_region = True
                if outcome.tool_result.get("regionId"):
                    _stage_region_ok = True
                else:
                    logger.warning(
                        f"{agent_log} stori_add_midi_region completed but returned no regionId "
                        f"(likely a collision or validation error) — effects will be skipped"
                    )
            elif tc.name in _GENERATOR_TOOL_NAMES or tc.name == "stori_add_notes":
                _stage_content = True
            elif tc.name in _EFFECT_TOOL_NAMES:
                _stage_effect = True

            # Accumulate for variable resolution and summary
            all_tool_results.append(outcome.tool_result)
            turn_tool_results.append(outcome.tool_result)
            collected_tool_calls.append({"tool": tc.name, "params": outcome.enriched_params})

            # Build tool result message for multi-turn
            tool_result_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(outcome.tool_result),
            })

            logger.debug(f"{agent_log} {tc.name} executed (skipped={outcome.skipped})")

        messages.extend(tool_result_messages)

    # Complete the last active step
    if active_step_id:
        evt = plan_tracker.complete_step_by_id(active_step_id)
        if evt:
            await sse_queue.put(evt)

    logger.info(f"{agent_log} Complete ({len(all_tool_results)} tool calls, {turn + 1} turn(s))")


_CC_NAMES: dict[int, str] = {
    1: "Mod Wheel", 7: "Volume", 10: "Pan", 11: "Expression",
    64: "Sustain Pedal", 74: "Filter Cutoff", 91: "Reverb Send", 93: "Chorus",
}


def _build_composition_summary(
    tool_calls_collected: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate composition metadata for the summary.final SSE event.

    Recognises the synthetic ``_reused_track`` tool name injected by the
    coordinator for tracks that already existed (Bug 1 fix) so the frontend
    can display "reused" vs "created" labels correctly.
    """
    tracks_created: list[dict[str, Any]] = []
    tracks_reused: list[dict[str, Any]] = []
    regions_created = 0
    notes_generated = 0
    effects_added: list[dict[str, str]] = []
    sends_created = 0
    cc_counts: dict[int, str] = {}
    automation_lanes = 0

    for tc in tool_calls_collected:
        name = tc.get("tool", "")
        params = tc.get("params", {})
        if name == "stori_add_midi_track":
            tracks_created.append({
                "name": params.get("name", ""),
                "instrument": params.get("_gmInstrumentName") or params.get("drumKitId") or "Unknown",
                "trackId": params.get("trackId", ""),
            })
        elif name == "_reused_track":
            # Synthetic marker — track existed before this prompt.
            tracks_reused.append({
                "name": params.get("name", ""),
                "trackId": params.get("trackId", ""),
            })
        elif name == "stori_add_midi_region":
            regions_created += 1
        elif name == "stori_add_notes":
            notes_generated += len(params.get("notes", []))
        elif name == "stori_add_insert_effect":
            effects_added.append({
                "trackId": params.get("trackId", ""),
                "type": params.get("effectType") or params.get("type", ""),
            })
        elif name == "stori_add_send":
            sends_created += 1
        elif name == "stori_add_midi_cc":
            cc_num = int(params.get("cc", 0))
            cc_counts[cc_num] = _CC_NAMES.get(cc_num, f"CC {cc_num}")
        elif name == "stori_add_automation":
            automation_lanes += 1

    return {
        "tracksCreated": tracks_created,
        "tracksReused": tracks_reused,
        "trackCount": len(tracks_created) + len(tracks_reused),
        "regionsCreated": regions_created,
        "notesGenerated": notes_generated,
        "effectsAdded": effects_added,
        "effectCount": len(effects_added),
        "sendsCreated": sends_created,
        "ccEnvelopes": [{"cc": k, "name": v} for k, v in sorted(cc_counts.items())],
        "automationLanes": automation_lanes,
    }


async def _handle_composition_agent_team(
    prompt: str,
    project_context: dict[str, Any],
    parsed: Any,  # ParsedPrompt — avoids circular import at module level
    route: Any,
    llm: LLMClient,
    store: StateStore,
    trace: Any,
    usage_tracker: Optional["UsageTracker"],
) -> AsyncIterator[str]:
    """Agent Teams coordinator for multi-instrument STORI PROMPT compositions.

    Three-phase execution:

    - **Phase 1** (sequential): tempo and key applied deterministically from
      the parsed prompt — no LLM call needed.
    - **Phase 2** (parallel): one independent ``_run_instrument_agent`` task
      per role, all launched simultaneously via ``asyncio.gather``. SSE events
      from all agents are multiplexed through a shared queue and forwarded to
      the client as they arrive.
    - **Phase 3** (sequential): optional mixing coordinator LLM call for
      shared buses, sends, and volume adjustments.
    """
    yield await sse_event({"type": "status", "message": "Preparing composition..."})

    # ── Build plan from parsed prompt (no LLM needed) ──
    plan_tracker = _PlanTracker()
    plan_tracker.build_from_prompt(parsed, prompt, project_context or {})
    if plan_tracker.steps:
        yield await sse_event(plan_tracker.to_plan_event())

    tool_calls_collected: list[dict[str, Any]] = []
    add_notes_failures: dict[str, int] = {}

    # ── Phase 1: Deterministic setup ──
    current_tempo = project_context.get("tempo")
    current_key = (project_context.get("key") or "").strip().lower()

    if parsed.tempo and parsed.tempo != current_tempo:
        tempo_step = next(
            (s for s in plan_tracker.steps if s.tool_name == "stori_set_tempo"), None
        )
        if tempo_step:
            yield await sse_event(plan_tracker.activate_step(tempo_step.step_id))

        outcome = await _apply_single_tool_call(
            tc_id=str(_uuid_mod.uuid4()),
            tc_name="stori_set_tempo",
            resolved_args={"tempo": parsed.tempo},
            allowed_tool_names=route.allowed_tool_names,
            store=store,
            trace=trace,
            add_notes_failures=add_notes_failures,
            emit_sse=True,
        )
        for evt in outcome.sse_events:
            yield await sse_event(evt)
        if not outcome.skipped:
            tool_calls_collected.append({"tool": "stori_set_tempo", "params": outcome.enriched_params})
            if tempo_step:
                yield await sse_event(
                    plan_tracker.complete_step_by_id(
                        tempo_step.step_id, f"Set tempo to {parsed.tempo} BPM"
                    )
                )

    if parsed.key and parsed.key.strip().lower() != current_key:
        key_step = next(
            (s for s in plan_tracker.steps if s.tool_name == "stori_set_key"), None
        )
        if key_step:
            yield await sse_event(plan_tracker.activate_step(key_step.step_id))

        outcome = await _apply_single_tool_call(
            tc_id=str(_uuid_mod.uuid4()),
            tc_name="stori_set_key",
            resolved_args={"key": parsed.key},
            allowed_tool_names=route.allowed_tool_names,
            store=store,
            trace=trace,
            add_notes_failures=add_notes_failures,
            emit_sse=True,
        )
        for evt in outcome.sse_events:
            yield await sse_event(evt)
        if not outcome.skipped:
            tool_calls_collected.append({"tool": "stori_set_key", "params": outcome.enriched_params})
            if key_step:
                yield await sse_event(
                    plan_tracker.complete_step_by_id(
                        key_step.step_id, f"Set key to {parsed.key}"
                    )
                )

    # ── Phase 2: Spawn instrument agents ──
    _ROLE_LABELS: dict[str, str] = {
        "drums": "Drums", "drum": "Drums",
        "bass": "Bass",
        "chords": "Chords", "chord": "Chords",
        "melody": "Melody",
        "lead": "Lead",
        "arp": "Arp",
        "pads": "Pads", "pad": "Pads",
        "fx": "FX",
    }

    # Map instrument label → step IDs owned by that agent
    instrument_step_ids: dict[str, list[str]] = {}
    for step in plan_tracker.steps:
        if step.parallel_group == "instruments" and step.track_name:
            key_label = step.track_name.lower()
            instrument_step_ids.setdefault(key_label, []).append(step.step_id)

    style = parsed.style or "default"
    # bars is not a first-class ParsedPrompt field — stored in extensions
    ext = getattr(parsed, "extensions", {}) or {}
    bars = int(ext.get("bars") or ext.get("Bars") or 4)
    tempo = float(parsed.tempo or project_context.get("tempo") or 120)
    key = parsed.key or project_context.get("key") or "C"

    # ── Detect existing tracks to avoid creating duplicates ──
    # For each instrument role, check whether a track with that name already
    # exists in project_context. If it does, pass its trackId and the beat
    # immediately after its last region so the agent appends rather than
    # recreates. Keys are lower-cased instrument labels (e.g. "drums", "bass").
    #
    # The client may send track IDs under "trackId" (our internal format) OR
    # "id" (the Stori DAW's native field name). Accept both so the lookup
    # doesn't silently return an empty string and mis-trigger the reuse path
    # with an invalid trackId injected into every agent's system prompt.
    _existing_track_info: dict[str, dict[str, Any]] = {}
    for pc_track in project_context.get("tracks", []):
        track_name_lower = (pc_track.get("name") or "").lower()
        if not track_name_lower:
            continue
        # Resolve track ID — try both field names that the client may use
        track_id: str = (
            pc_track.get("trackId")
            or pc_track.get("id")
            or ""
        )
        regions = pc_track.get("regions", [])
        next_beat: int = 0
        if regions:
            next_beat = int(max(
                r.get("startBeat", 0) + r.get("durationBeats", 0)
                for r in regions
            ))
        # Only record the FIRST matching track (avoid stale duplicates from
        # a previous run; those extra tracks are the bug we're fixing here).
        if track_name_lower not in _existing_track_info:
            _existing_track_info[track_name_lower] = {
                "trackId": track_id,
                "next_beat": next_beat,
            }
    logger.debug(
        f"[{trace.trace_id[:8]}] Existing track map: "
        + ", ".join(f"{k}={v['trackId'][:8]}" for k, v in _existing_track_info.items() if v["trackId"])
    )

    # ── Preflight events — latency masking (emit before agents start) ──
    # Lets the frontend pre-allocate timeline rows and show "incoming" states
    # for every predicted instrument step. Derived from the plan, no LLM needed.
    for role in parsed.roles:
        instrument_name = _ROLE_LABELS.get(role.lower(), role.title())
        step_ids_for_role = instrument_step_ids.get(instrument_name.lower(), [])
        steps_for_role = [s for s in plan_tracker.steps if s.step_id in step_ids_for_role]
        for step in steps_for_role:
            yield await sse_event({
                "type": "preflight",
                "stepId": step.step_id,
                "agentId": instrument_name.lower(),
                "agentRole": role,
                "label": step.label,
                "toolName": step.tool_name,
                "parallelGroup": step.parallel_group,
                "confidence": 0.9,
            })

    sse_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    # Shared list for agent tool calls — safe because asyncio is single-threaded.
    agent_tool_calls: list[dict[str, Any]] = []
    tasks: list[asyncio.Task] = []

    # Pre-compute per-role existing track resolution so we can assert distinct
    # trackIds before spawning. Catches any future regression where two agents
    # would receive the same trackId.
    _role_track_info: dict[str, dict[str, Any]] = {}
    for role in parsed.roles:
        instrument_name = _ROLE_LABELS.get(role.lower(), role.title())
        # Exact (case-insensitive) name match
        existing_info = _existing_track_info.get(instrument_name.lower())
        if not existing_info:
            # Fuzzy fallback: role word appears in track name or vice-versa
            # e.g. role="horns" matches track "Horns Section"
            for key, info in _existing_track_info.items():
                if instrument_name.lower() in key or key in instrument_name.lower():
                    existing_info = info
                    break
        track_id = existing_info["trackId"] if existing_info else None
        _role_track_info[role] = {
            "instrument_name": instrument_name,
            "existing_track_id": track_id if track_id else None,
            "start_beat": existing_info["next_beat"] if existing_info and track_id else 0,
        }
        logger.debug(
            f"[{trace.trace_id[:8]}] Role '{role}' → instrument='{instrument_name}' "
            f"existing_track_id={track_id or 'None (will create)'}"
        )

    # Verify distinct trackIds across all roles that have existing tracks —
    # all reused agents must write to different tracks.
    _reused_ids = [
        info["existing_track_id"]
        for info in _role_track_info.values()
        if info["existing_track_id"]
    ]
    if len(_reused_ids) != len(set(_reused_ids)):
        logger.error(
            f"[{trace.trace_id[:8]}] ❌ DUPLICATE trackId in role→track mapping! "
            f"ids={_reused_ids} — check project_context track names vs role names"
        )

    for role in parsed.roles:
        role_info = _role_track_info[role]
        instrument_name = role_info["instrument_name"]
        step_ids_for_role = instrument_step_ids.get(instrument_name.lower(), [])
        existing_track_id = role_info["existing_track_id"]
        agent_start_beat = role_info["start_beat"]
        # Record reused tracks in the shared list for summary.final.
        if existing_track_id:
            agent_tool_calls.append({
                "tool": "_reused_track",
                "params": {"name": instrument_name, "trackId": existing_track_id},
            })
        task = asyncio.create_task(
            _run_instrument_agent(
                instrument_name=instrument_name,
                role=role,
                style=style,
                bars=bars,
                tempo=tempo,
                key=key,
                step_ids=step_ids_for_role,
                plan_tracker=plan_tracker,
                llm=llm,
                store=store,
                allowed_tool_names=_INSTRUMENT_AGENT_TOOLS,
                trace=trace,
                sse_queue=sse_queue,
                collected_tool_calls=agent_tool_calls,
                existing_track_id=existing_track_id,
                start_beat=agent_start_beat,
            )
        )
        tasks.append(task)
        logger.info(
            f"[{trace.trace_id[:8]}] 🚀 Spawned {instrument_name} agent "
            f"(step_ids={step_ids_for_role}"
            + (f", reusing trackId={existing_track_id}, startBeat={agent_start_beat}" if existing_info else "")
            + ")"
        )

    # Drain queue while agents run — forward events to client as they arrive
    pending: set[asyncio.Task] = set(tasks)
    while pending:
        done, pending = await asyncio.wait(pending, timeout=0.05)
        while not sse_queue.empty():
            yield await sse_event(sse_queue.get_nowait())
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                logger.error(
                    f"[{trace.trace_id[:8]}] ❌ Instrument agent failed: {task.exception()}"
                )
    # Drain tail events after all tasks finish
    while not sse_queue.empty():
        yield await sse_event(sse_queue.get_nowait())

    logger.info(f"[{trace.trace_id[:8]}] ✅ All instrument agents complete")

    # ── Phase 3: Mixing coordinator (optional, one LLM call) ──
    phase3_steps = [
        s for s in plan_tracker.steps
        if s.status == "pending" and s.parallel_group is None
        and s.tool_name in _AGENT_TEAM_PHASE3_TOOLS
    ]
    if phase3_steps:
        entity_snapshot = _entity_manifest(store)
        phase3_tools = [
            t for t in ALL_TOOLS
            if t["function"]["name"] in _AGENT_TEAM_PHASE3_TOOLS
        ]
        mixing_prompt = (
            "All instrument tracks have been created. Apply final mixing:\n"
            + "\n".join(f"- {s.label}" for s in phase3_steps)
            + f"\n\nCurrent entity IDs:\n{json.dumps(entity_snapshot)}\n\n"
            "Batch ALL mixing tool calls in a single response. No text."
        )
        try:
            phase3_response = await llm.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt_base()},
                    {"role": "user", "content": mixing_prompt},
                ],
                tools=phase3_tools,
                tool_choice="auto",
                max_tokens=2000,
            )
            phase3_iter_results: list[dict[str, Any]] = []
            phase3_failures: dict[str, int] = {}
            for tc in phase3_response.tool_calls:
                p3_resolved = _resolve_variable_refs(tc.params, phase3_iter_results)
                p3_step = plan_tracker.find_step_for_tool(tc.name, p3_resolved, store)
                if p3_step:
                    yield await sse_event(plan_tracker.activate_step(p3_step.step_id))
                p3_outcome = await _apply_single_tool_call(
                    tc_id=tc.id,
                    tc_name=tc.name,
                    resolved_args=p3_resolved,
                    allowed_tool_names=_AGENT_TEAM_PHASE3_TOOLS,
                    store=store,
                    trace=trace,
                    add_notes_failures=phase3_failures,
                    emit_sse=True,
                )
                for evt in p3_outcome.sse_events:
                    yield await sse_event(evt)
                if not p3_outcome.skipped:
                    tool_calls_collected.append({"tool": tc.name, "params": p3_outcome.enriched_params})
                    tool_calls_collected.extend(p3_outcome.extra_tool_calls)
                    if p3_step:
                        yield await sse_event(
                            plan_tracker.complete_step_by_id(p3_step.step_id)
                        )
                phase3_iter_results.append(p3_outcome.tool_result)
        except Exception as exc:
            logger.error(f"[{trace.trace_id[:8]}] Phase 3 coordinator failed: {exc}")

    # ── Finalize ──
    for skip_evt in plan_tracker.finalize_pending_as_skipped():
        yield await sse_event(skip_evt)

    # summary.final — rich composition summary for the frontend "Ready!" line.
    # Combines coordinator tool calls (Phase 1 + 3) with all agent tool calls.
    all_collected = tool_calls_collected + agent_tool_calls
    summary = _build_composition_summary(all_collected)
    yield await sse_event({
        "type": "summary.final",
        "traceId": trace.trace_id,
        **summary,
    })

    yield await sse_event({
        "type": "complete",
        "success": True,
        "toolCalls": all_collected,
        "stateVersion": store.version,
        "traceId": trace.trace_id,
        **_context_usage_fields(usage_tracker, llm.model),
    })


async def _stream_llm_response(
    llm: LLMClient,
    messages: list[dict],
    tools: list[dict],
    tool_choice: str,
    trace,
    emit_sse,
    max_tokens: Optional[int] = None,
    reasoning_fraction: Optional[float] = None,
    suppress_content: bool = False,
):
    """Stream LLM response with thinking deltas. Yields SSE events and final response.

    Reasoning tokens are buffered via ReasoningBuffer so BPE sub-word pieces
    are merged into complete words before sanitization and SSE emission.

    Args:
        suppress_content: When True, content deltas are accumulated on the
            response but NOT emitted as SSE events.  Used by the EDITING
            handler because the LLM often interleaves tool-call argument
            syntax (e.g. ``(key="G major")``) into the content stream,
            which is meaningless to the user.  The caller decides whether
            to emit ``response.content`` after the stream ends.
    """
    response_content = None
    response_tool_calls: list[dict[str, Any]] = []
    finish_reason: Optional[str] = None
    usage: dict[str, Any] = {}
    reasoning_buf = ReasoningBuffer()
    
    async for chunk in llm.chat_completion_stream(
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        temperature=settings.orchestration_temperature,
        max_tokens=max_tokens,
        reasoning_fraction=reasoning_fraction,
    ):
        if chunk.get("type") == "reasoning_delta":
            reasoning_text = chunk.get("text", "")
            if reasoning_text:
                to_emit = reasoning_buf.add(reasoning_text)
                if to_emit and emit_sse:
                    yield await emit_sse({
                        "type": "reasoning",
                        "content": to_emit,
                    })
        elif chunk.get("type") == "content_delta":
            # Flush any remaining reasoning before content starts
            flushed = reasoning_buf.flush()
            if flushed and emit_sse:
                yield await emit_sse({
                    "type": "reasoning",
                    "content": flushed,
                })
            content_text = chunk.get("text", "")
            if content_text and emit_sse and not suppress_content:
                yield await emit_sse({
                    "type": "content",
                    "content": content_text,
                })
        elif chunk.get("type") == "done":
            # Flush remaining reasoning buffer
            flushed = reasoning_buf.flush()
            if flushed and emit_sse:
                yield await emit_sse({
                    "type": "reasoning",
                    "content": flushed,
                })
            response_content = chunk.get("content")
            response_tool_calls = chunk.get("tool_calls", [])
            finish_reason = chunk.get("finish_reason")
            usage = chunk.get("usage", {})
    
    response = LLMResponse(
        content=response_content,
        finish_reason=finish_reason,
        usage=usage,
    )
    for tc in response_tool_calls:
        try:
            args = tc.get("function", {}).get("arguments", "{}")
            if isinstance(args, str):
                args = json.loads(args) if args else {}
            response.tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=tc.get("function", {}).get("name", ""),
                params=args,
            ))
        except Exception as e:
            logger.error(f"Error parsing tool call: {e}")
    
    # Yield sentinel so caller can consume final LLMResponse (see StreamFinalResponse)
    yield StreamFinalResponse(response=response)
