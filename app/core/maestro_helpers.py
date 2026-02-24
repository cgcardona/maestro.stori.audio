"""Shared helpers for Maestro handlers.

Contains utility functions, dataclasses, and the LLM streaming wrapper
that are used across multiple handler modules (editing, composing, agent
teams). Nothing in this module depends on other maestro_* modules.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.config import settings
from app.core.expansion import ToolCall
from app.core.llm_client import LLMClient, LLMResponse
from app.core.sse_utils import ReasoningBuffer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

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
    "stori_add_midi_track":   ["trackId"],
    "stori_add_midi_region":  ["regionId", "trackId"],
    "stori_ensure_bus":       ["busId"],
    "stori_duplicate_region": ["newRegionId", "regionId"],
}

_VAR_REF_RE = re.compile(r"^\$(\d+)\.(\w+)$")


# ---------------------------------------------------------------------------
# Shared utility functions
# ---------------------------------------------------------------------------

def _context_usage_fields(
    usage_tracker: Optional["UsageTracker"], model: str
) -> dict[str, int]:
    """Return inputTokens / contextWindowTokens for SSE complete events."""
    from app.config import get_context_window_tokens
    return {
        "inputTokens": usage_tracker.last_input_tokens if usage_tracker else 0,
        "contextWindowTokens": get_context_window_tokens(model),
    }


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
        case "stori_solo_track":
            return "Soloing track" if args.get("soloed", True) else "Unsoloing track"
        case "stori_set_track_color":
            return f"Set track color"
        case "stori_set_track_icon":
            if track:
                return f"Setting icon for {track}"
            return "Setting track icon"
        case "stori_set_track_name":
            new_name = args.get("name", "")
            return f"Rename track to {new_name}" if new_name else "Renaming track"
        case "stori_set_midi_program":
            program = args.get("program", "")
            if track:
                return f"Set instrument for {track}" if not program else f"Set {program} on {track}"
            return f"Set instrument" if not program else f"Set instrument to {program}"
        case "stori_transpose_notes":
            semitones = args.get("semitones", "?")
            return f"Transpose notes by {semitones} semitones"
        case "stori_add_aftertouch":
            if track:
                return f"Add aftertouch to {track}"
            return "Add aftertouch"
        case "stori_create_project":
            return "Create new project"
        case "stori_set_playhead":
            return f"Move playhead to beat {args.get('beat', '?')}"
        case "stori_show_panel":
            panel = args.get("panel", "panel")
            return f"Show {panel}"
        case "stori_set_zoom":
            return "Adjust zoom level"
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


def _build_tool_result(
    tool_name: str,
    params: dict[str, Any],
    store: Any,
) -> dict[str, Any]:
    """Build a tool result with state feedback for the LLM.

    Entity-creating tools: echo server-assigned IDs.
    stori_add_notes: confirm notesAdded + totalNotes in the region.
    stori_clear_notes: confirm the region was cleared.

    Note: entity manifests are injected separately via
    ``EntityRegistry.agent_manifest()`` — not embedded in tool results.
    """
    result: dict[str, Any] = {"success": True}

    if tool_name in _ENTITY_CREATING_TOOLS:
        for id_field in _ENTITY_ID_ECHO.get(tool_name, []):
            if id_field in params:
                result[id_field] = params[id_field]

        if tool_name == "stori_add_midi_region":
            result["startBeat"] = params.get("startBeat", 0)
            result["durationBeats"] = params.get("durationBeats", 16)
            result["name"] = params.get("name", "Region")

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

    elif tool_name == "stori_add_notes":
        region_id = params.get("regionId", "")
        notes = params.get("notes", [])
        result["regionId"] = region_id
        result["notesAdded"] = len(notes)
        total_notes = len(store.get_region_notes(region_id)) if region_id else 0
        result["totalNotes"] = total_notes

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


# ---------------------------------------------------------------------------
# LLM streaming helper
# ---------------------------------------------------------------------------

async def _stream_llm_response(
    llm: LLMClient,
    messages: list[dict],
    tools: list[dict],
    tool_choice: str,
    trace: Any,
    emit_sse: Callable[[dict[str, Any]], Awaitable[str]],
    max_tokens: Optional[int] = None,
    reasoning_fraction: Optional[float] = None,
    suppress_content: bool = False,
) -> Any:
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
                if to_emit:
                    yield await emit_sse({
                        "type": "reasoning",
                        "content": to_emit,
                    })
        elif chunk.get("type") == "content_delta":
            flushed = reasoning_buf.flush()
            if flushed:
                yield await emit_sse({
                    "type": "reasoning",
                    "content": flushed,
                })
            content_text = chunk.get("text", "")
            if content_text and not suppress_content:
                yield await emit_sse({
                    "type": "content",
                    "content": content_text,
                })
        elif chunk.get("type") == "done":
            flushed = reasoning_buf.flush()
            if flushed:
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
