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


def _human_label_for_tool(name: str, args: dict[str, Any]) -> str:
    """Return a short, musician-friendly description of a tool call.

    Used in progress and toolStart SSE events so the frontend can show
    something like "Writing bass notes (8/15)" instead of "Step 8/15".
    """
    match name:
        case "stori_set_tempo":
            return f"Setting tempo to {args.get('tempo', '?')} BPM"
        case "stori_set_key":
            return f"Setting key to {args.get('key', '?')}"
        case "stori_add_midi_track":
            return f"Creating {args.get('name', 'track')} track"
        case "stori_add_midi_region":
            return f"Creating region: {args.get('name', 'region')}"
        case "stori_add_notes":
            n = len(args.get("notes") or [])
            return f"Writing {n} notes" if n else "Writing notes"
        case "stori_clear_notes":
            return "Clearing notes"
        case "stori_quantize_notes":
            return f"Quantizing to {args.get('grid', '1/16')}"
        case "stori_apply_swing":
            return "Applying swing"
        case "stori_generate_midi":
            role = args.get("role", "part")
            style = args.get("style", "")
            bars = args.get("bars", "")
            return f"Generating {style} {role}{f' ({bars} bars)' if bars else ''}"
        case "stori_generate_drums":
            return f"Generating {args.get('style', '')} drums"
        case "stori_generate_bass":
            return f"Generating {args.get('style', '')} bass"
        case "stori_generate_melody":
            return f"Generating {args.get('style', '')} melody"
        case "stori_generate_chords":
            return f"Generating {args.get('style', '')} chords"
        case "stori_add_insert_effect":
            return f"Adding {args.get('type', 'effect')}"
        case "stori_ensure_bus":
            return f"Creating {args.get('name', 'bus')} bus"
        case "stori_add_send":
            return "Adding send"
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
            # Fallback: strip stori_ prefix and humanise
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

    Included in every entity-creating tool result so the LLM never has to
    guess UUIDs or rely on the (stale) project snapshot from the request.
    """
    tracks = []
    for track in store.registry.list_tracks():
        regions = [
            {"name": r.name, "regionId": r.id}
            for r in store.registry.get_track_regions(track.id)
        ]
        tracks.append({"name": track.name, "trackId": track.id, "regions": regions})
    buses = [{"name": b.name, "busId": b.id} for b in store.registry.list_buses()]
    return {"tracks": tracks, "buses": buses}


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


@dataclass
class _PlanStep:
    """Internal state for one plan step."""
    step_id: str
    label: str
    detail: Optional[str] = None
    status: str = "pending"
    result: Optional[str] = None
    track_name: Optional[str] = None
    tool_indices: list[int] = field(default_factory=list)


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
        tempo = project_context.get("tempo")
        key = project_context.get("key")
        for tc in tool_calls:
            if tc.name == "stori_set_tempo":
                tempo = tc.params.get("tempo")
            elif tc.name == "stori_set_key":
                key = tc.params.get("key")

        # For structured prompts (STORI PROMPT header), extract Section + Style
        # instead of dumping the YAML block into the title.
        base: str
        if prompt.startswith("STORI PROMPT"):
            section: Optional[str] = None
            style: Optional[str] = None
            request_line: Optional[str] = None
            for line in prompt.splitlines():
                stripped = line.strip()
                if stripped.lower().startswith("section:"):
                    section = stripped.split(":", 1)[1].strip()
                elif stripped.lower().startswith("style:"):
                    style = stripped.split(":", 1)[1].strip()
                elif stripped.lower().startswith("request: |"):
                    pass  # next non-empty line is the request text
                elif request_line is None and section and stripped and not ":" in stripped:
                    request_line = stripped
            if section and style:
                base = f"Create {style} {section}"
            elif section:
                base = f"Create {section}"
            elif style:
                base = f"Create {style} section"
            else:
                base = "Compose"
        else:
            base = prompt[:80].rstrip()
            if len(prompt) > 80:
                base = (base.rsplit(" ", 1)[0] or base)

        params: list[str] = []
        if key:
            params.append(str(key))
        if tempo:
            params.append(f"{tempo} BPM")
        if params:
            return f"{base} ({', '.join(params)})"
        return base

    def _group_into_steps(self, tool_calls: list[Any]) -> list[_PlanStep]:
        steps: list[_PlanStep] = []
        i, n = 0, len(tool_calls)

        # Leading setup tools — one step per call for granularity
        while i < n and tool_calls[i].name in _SETUP_TOOL_NAMES:
            tc = tool_calls[i]
            if tc.name == "stori_set_tempo":
                label = f"Set tempo to {tc.params.get('tempo', '?')} BPM"
            elif tc.name == "stori_set_key":
                label = f"Set key to {tc.params.get('key', '?')}"
            else:
                label = _human_label_for_tool(tc.name, tc.params)
            steps.append(_PlanStep(
                step_id=str(self._next_id),
                label=label,
                tool_indices=[i],
            ))
            self._next_id += 1
            i += 1

        while i < n:
            tc = tool_calls[i]

            if tc.name in _TRACK_CREATION_NAMES:
                track_name = tc.params.get("name", "Track")
                indices = [i]
                i += 1
                while i < n and tool_calls[i].name in _CONTENT_TOOL_NAMES:
                    indices.append(i)
                    i += 1
                has_notes = any(
                    tool_calls[j].name == "stori_add_notes" for j in indices
                )
                label = f"Create {track_name} track"
                if has_notes:
                    label += " and add content"
                detail = None
                gm = tc.params.get("gmProgram")
                drum_kit = tc.params.get("drumKitId")
                if gm is not None:
                    detail = f"GM {gm}"
                elif drum_kit:
                    detail = str(drum_kit)
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=label,
                    detail=detail,
                    track_name=track_name,
                    tool_indices=indices,
                ))
                self._next_id += 1

            elif tc.name in _CONTENT_TOOL_NAMES:
                indices = []
                while i < n and tool_calls[i].name in _CONTENT_TOOL_NAMES:
                    indices.append(i)
                    i += 1
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label="Add musical content",
                    tool_indices=indices,
                ))
                self._next_id += 1

            elif tc.name in _EFFECT_TOOL_NAMES:
                indices = []
                detail_parts: list[str] = []
                while i < n and tool_calls[i].name in _EFFECT_TOOL_NAMES:
                    etc = tool_calls[i]
                    indices.append(i)
                    if etc.name == "stori_add_insert_effect":
                        etype = etc.params.get("type", "")
                        if etype:
                            detail_parts.append(etype)
                    elif etc.name == "stori_ensure_bus":
                        bname = etc.params.get("name", "")
                        if bname:
                            detail_parts.append(f"{bname} bus")
                    i += 1
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label="Add effects and routing",
                    detail=", ".join(detail_parts) if detail_parts else None,
                    tool_indices=indices,
                ))
                self._next_id += 1

            elif tc.name in _MIXING_TOOL_NAMES:
                indices = []
                while i < n and tool_calls[i].name in _MIXING_TOOL_NAMES:
                    indices.append(i)
                    i += 1
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label="Adjust mix",
                    tool_indices=indices,
                ))
                self._next_id += 1

            else:
                steps.append(_PlanStep(
                    step_id=str(self._next_id),
                    label=_human_label_for_tool(tc.name, tc.params),
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
        """
        self.title = self._derive_title(prompt, [], project_context)

        # Setup steps from routing fields
        if parsed.tempo:
            self.steps.append(_PlanStep(
                step_id=str(self._next_id),
                label=f"Set tempo to {parsed.tempo} BPM",
            ))
            self._next_id += 1
        if parsed.key:
            self.steps.append(_PlanStep(
                step_id=str(self._next_id),
                label=f"Set key to {parsed.key}",
            ))
            self._next_id += 1

        # One step per role — map role names to human-friendly track labels
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
            self.steps.append(_PlanStep(
                step_id=str(self._next_id),
                label=f"Create {track_label} track and add content",
                track_name=track_label,
            ))
            self._next_id += 1

        # If no roles but it's a composition, add a generic placeholder
        if not parsed.roles:
            self.steps.append(_PlanStep(
                step_id=str(self._next_id),
                label="Generate music",
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
                    **({"detail": s.detail} if s.detail else {}),
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
        if tc_name in _EFFECT_TOOL_NAMES:
            for step in self.steps:
                if "effect" in step.label.lower() and step.status != "completed":
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
        return {"type": "planStepUpdate", "stepId": step_id, "status": "active"}

    def complete_active_step(self) -> Optional[dict[str, Any]]:
        """Complete the currently-active step; returns event dict or None."""
        if not self._active_step_id:
            return None
        step = self.get_step(self._active_step_id)
        if not step:
            return None
        step.status = "completed"
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
        d: dict[str, Any] = {
            "type": "planStepUpdate",
            "stepId": step_id,
            "status": "completed",
        }
        if result:
            d["result"] = result
        return d

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
                
                # Backend-owned execution mode policy:
                # COMPOSING → variation (music generation requires human review)
                #   EXCEPT: empty project → override to EDITING (can't diff against nothing)
                # EDITING   → apply (structural ops execute directly)
                # REASONING → n/a (no tools)
                if route.sse_state == SSEState.COMPOSING:
                    if _project_needs_structure(project_context):
                        # Empty project: structural changes need tool_call events,
                        # not variation review — you can't diff against nothing.
                        route = _create_editing_composition_route(route)
                        execution_mode = "apply"
                        logger.info(
                            f"🔄 Empty project: overriding {route.intent.value} → EDITING "
                            f"for structural creation with tool_call events"
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
        # ── Phase 2 (Unified SSE UX): build plan tracker and emit plan event ──
        composing_plan_tracker = _PlanTracker()
        composing_plan_tracker.build(
            plan.tool_calls, prompt, project_context,
            is_composition=True, store=store,
        )
        if len(composing_plan_tracker.steps) >= 1:
            yield await sse_event(composing_plan_tracker.to_plan_event())

        # Deprecated — kept for backward compat during transition
        yield await sse_event({
            "type": "planSummary",
            "totalSteps": len(plan.tool_calls),
            "generations": plan.generation_count,
            "edits": plan.edit_count,
        })
        
        # =====================================================================
        # Variation Mode: Generate proposal without mutation
        # =====================================================================
        try:
            with trace_span(trace, "variation_generation", {"steps": len(plan.tool_calls)}):
                from app.core.executor import execute_plan_variation

                logger.info(
                    f"[{trace.trace_id[:8]}] Starting variation generation: "
                    f"{len(plan.tool_calls)} tool calls"
                )

                # ── Phase 2+3 unified event queue ──
                # All executor events (planStepUpdate, toolStart, toolCall,
                # deprecated progress) are funnelled through a single queue
                # so the SSE drain loop can emit them in order.
                _event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

                async def _on_tool_event(
                    call_id: str, tool_name: str, params: dict[str, Any],
                ) -> None:
                    """Phase 3: emit toolStart + proposal toolCall."""
                    # ── planStepUpdate: activate matching step ──
                    step = composing_plan_tracker.find_step_for_tool(
                        tool_name, params, store,
                    )
                    if step and step.step_id != composing_plan_tracker._active_step_id:
                        if composing_plan_tracker._active_step_id:
                            completed_evt = composing_plan_tracker.complete_active_step()
                            if completed_evt:
                                await _event_queue.put(completed_evt)
                        await _event_queue.put(
                            composing_plan_tracker.activate_step(step.step_id)
                        )

                    label = _human_label_for_tool(tool_name, params)
                    await _event_queue.put({
                        "type": "toolStart",
                        "name": tool_name,
                        "label": label,
                    })
                    await _event_queue.put({
                        "type": "toolCall",
                        "id": call_id,
                        "name": tool_name,
                        "params": params,
                        "proposal": True,
                    })

                async def _on_progress(
                    current: int, total: int,
                    tool_name: str = "", tool_args: dict | None = None,
                ) -> None:
                    """Progress callback — deprecated progress event for compat."""
                    label = _human_label_for_tool(tool_name, tool_args or {}) if tool_name else f"Step {current}"
                    await _event_queue.put({
                        "type": "progress",
                        "currentStep": current,
                        "totalSteps": total,
                        "message": label,
                        "toolName": tool_name,
                    })

                _VARIATION_TIMEOUT = 90  # seconds
                task = asyncio.create_task(
                    execute_plan_variation(
                        tool_calls=plan.tool_calls,
                        project_state=project_context,
                        intent=prompt,
                        conversation_id=conversation_id,
                        explanation=plan.llm_response_text,
                        progress_callback=_on_progress,
                        tool_event_callback=_on_tool_event,
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

                    # Drain any remaining queued events
                    while not _event_queue.empty():
                        yield await sse_event(await _event_queue.get())

                    variation = await task

                    # Complete remaining active plan step
                    if composing_plan_tracker._active_step_id:
                        final_step_evt = composing_plan_tracker.complete_active_step()
                        if final_step_evt:
                            yield await sse_event(final_step_evt)

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

                # Persist to VariationStore so commit/discard can find it
                _store_variation(variation, project_context, store)

                # Emit meta event
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

                # Emit individual phrase events
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

                # Emit done event
                yield await sse_event({
                    "type": "done",
                    "variationId": variation.variation_id,
                    "phraseCount": len(variation.phrases),
                })

                logger.info(
                    f"[{trace.trace_id[:8]}] Variation streamed: "
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

            with trace_span(trace, f"validate:{tc.name}"):
                validation = validate_tool_call(
                    tc.name, resolved_args, route.allowed_tool_names, store.registry
                )
            
            if not validation.valid:
                log_validation_error(
                    trace.trace_id,
                    tc.name,
                    [str(e) for e in validation.errors],
                )
                
                yield await sse_event({
                    "type": "toolError",
                    "name": tc.name,
                    "error": validation.error_message,
                    "errors": [str(e) for e in validation.errors],
                })
                
                # Add error to messages so LLM knows
                messages.append({
                    "role": "assistant",
                    "tool_calls": [{
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(resolved_args)}
                    }]
                })
                error_result = {"error": validation.error_message}
                iter_tool_results.append(error_result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(error_result),
                })
                continue
            
            enriched_params = validation.resolved_params
            
            # Register entities for entity-creating tools via StateStore
            if tc.name == "stori_add_midi_track":
                track_name = enriched_params.get("name", "Track")
                instrument = enriched_params.get("instrument")
                gm_program = enriched_params.get("gmProgram")
                
                # CRITICAL: Always generate new UUID for entity-creating tools
                # LLMs sometimes hallucinate duplicate UUIDs, causing frontend crashes
                # System prompt instructs LLM NOT to provide IDs for new entities,
                # but we enforce this server-side as a safety measure
                if "trackId" in enriched_params:
                    logger.warning(
                        f"⚠️ LLM provided trackId '{enriched_params['trackId']}' for NEW track '{track_name}'. "
                        f"Ignoring and generating fresh UUID to prevent duplicates."
                    )
                
                # Always generate fresh UUID via StateStore
                track_id = store.create_track(track_name)
                enriched_params["trackId"] = track_id
                logger.debug(f"🔑 Generated trackId: {track_id[:8]} for '{track_name}'")
                
                # Auto-infer GM program if not specified
                if gm_program is None:
                    from app.core.gm_instruments import infer_gm_program_with_context
                    inference = infer_gm_program_with_context(
                        track_name=track_name,
                        instrument=instrument,
                    )
                    # Always provide instrument metadata
                    enriched_params["_gmInstrumentName"] = inference.instrument_name
                    enriched_params["_isDrums"] = inference.is_drums
                    
                    logger.info(
                        f"🎵 [EDITING] GM inference for '{track_name}': "
                        f"program={inference.program}, instrument={inference.instrument_name}, is_drums={inference.is_drums}"
                    )
                    
                    # Add GM program if not drums
                    if inference.needs_program_change:
                        enriched_params["gmProgram"] = inference.program
            
            elif tc.name == "stori_add_midi_region":
                midi_region_track_id: Optional[str] = enriched_params.get("trackId")
                region_name: str = str(enriched_params.get("name", "Region"))
                
                # CRITICAL: Always generate new UUID for entity-creating tools
                # LLMs sometimes hallucinate duplicate UUIDs, causing frontend crashes
                if "regionId" in enriched_params:
                    logger.warning(
                        f"⚠️ LLM provided regionId '{enriched_params['regionId']}' for NEW region '{region_name}'. "
                        f"Ignoring and generating fresh UUID to prevent duplicates."
                    )
                
                # Always generate fresh UUID via StateStore
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
            
            elif tc.name == "stori_duplicate_region":
                # Duplicating a region creates a new entity — register the copy.
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
                        logger.debug(f"🔑 Generated newRegionId: {new_region_id[:8]} for duplicate of '{source_entity.name}'")
                    except ValueError as e:
                        logger.error(f"Failed to register duplicate region: {e}")

            elif tc.name == "stori_ensure_bus":
                bus_name = enriched_params.get("name", "Bus")
                
                # CRITICAL: Always let server manage bus IDs
                if "busId" in enriched_params:
                    logger.warning(
                        f"⚠️ LLM provided busId '{enriched_params['busId']}' for bus '{bus_name}'. "
                        f"Ignoring to prevent duplicates."
                    )
                
                bus_id = store.get_or_create_bus(bus_name)
                enriched_params["busId"] = bus_id
            
            # Emit tool start + call to client (only in apply mode)
            if execution_mode == "apply":
                yield await sse_event({
                    "type": "toolStart",
                    "name": tc.name,
                    "label": _human_label_for_tool(tc.name, enriched_params),
                })
                yield await sse_event({
                    "type": "toolCall",
                    "id": tc.id,
                    "name": tc.name,
                    "params": enriched_params,
                })
            
            log_tool_call(trace.trace_id, tc.name, enriched_params, True)
            
            tool_calls_collected.append({
                "tool": tc.name,
                "params": enriched_params,
            })

            # ── Synthetic stori_set_track_icon after stori_add_midi_track ──
            # The icon is derived from the resolved GM program or drumKitId so
            # it persists in the DAW's track model and round-trips correctly in
            # stori_read_project snapshots (mirrors the app's displayIcon logic).
            if tc.name == "stori_add_midi_track" and execution_mode == "apply":
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
                    _icon_params: dict[str, Any] = {
                        "trackId": _icon_track_id,
                        "icon": _track_icon,
                    }
                    yield await sse_event({
                        "type": "toolStart",
                        "name": "stori_set_track_icon",
                        "label": f"Setting icon for {enriched_params.get('name', 'track')}",
                    })
                    yield await sse_event({
                        "type": "toolCall",
                        "id": f"{tc.id}-icon",
                        "name": "stori_set_track_icon",
                        "params": _icon_params,
                    })
                    tool_calls_collected.append({
                        "tool": "stori_set_track_icon",
                        "params": _icon_params,
                    })
                    logger.debug(
                        f"🎨 Synthetic icon '{_track_icon}' → "
                        f"trackId {_icon_track_id[:8]} "
                        f"({'drum kit' if (_drum_kit or _is_drums) else f'GM {_gm_program}'})"
                    )

            # ── Plan step tracking: accumulate result description ──
            if plan_tracker and execution_mode == "apply":
                _step = (
                    plan_tracker.step_for_tool_index(tc_idx)
                    or plan_tracker.find_step_for_tool(tc.name, enriched_params, store)
                )
                if _step:
                    _step.result = _build_step_result(
                        tc.name, enriched_params, _step.result,
                    )

            # Persist notes so the COMPOSING handoff can diff against them
            if tc.name == "stori_add_notes":
                _notes = enriched_params.get("notes", [])
                _rid = enriched_params.get("regionId", "")
                if _rid and _notes:
                    store.add_notes(_rid, _notes)
                    logger.debug(
                        f"📝 [EDITING] Persisted {len(_notes)} notes for "
                        f"region {_rid[:8]} in StateStore"
                    )

            # Add to messages — summarize stori_add_notes to avoid
            # bloating context with hundreds of note objects
            if tc.name == "stori_add_notes":
                notes = enriched_params.get("notes", [])
                summary_params = {
                    k: v for k, v in enriched_params.items() if k != "notes"
                }
                summary_params["_noteCount"] = len(notes)
                if notes:
                    starts = [n["startBeat"] for n in notes]
                    summary_params["_beatRange"] = [min(starts), max(starts)]
                msg_arguments = json.dumps(summary_params)
            else:
                msg_arguments = json.dumps(enriched_params)

            messages.append({
                "role": "assistant",
                "tool_calls": [{
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": msg_arguments}
                }]
            })
            # Build tool result. For entity-creating tools: echo the
            # server-assigned ID(s) AND include the full current entity
            # manifest so the LLM always has an up-to-date picture of the
            # project after every creation — no stale UUIDs, no guessing.
            tool_result: dict = {"status": "success"}
            if tc.name in _ENTITY_CREATING_TOOLS:
                for _field in _ENTITY_ID_ECHO.get(tc.name, []):
                    if _field in enriched_params:
                        tool_result[_field] = enriched_params[_field]
                tool_result["entities"] = _entity_manifest(store)

            # Accumulate for $N.field variable reference resolution in
            # subsequent tool calls within this same iteration.
            iter_tool_results.append(tool_result)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(tool_result),
            })
        
        # ── Plan step tracking: complete last active step this iteration ──
        if plan_tracker and plan_tracker._active_step_id and execution_mode == "apply":
            evt = plan_tracker.complete_active_step()
            if evt:
                yield await sse_event(evt)

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
                if plan_tracker and execution_mode == "apply":
                    incomplete_set = set(incomplete)
                    for _step in plan_tracker.steps:
                        if (
                            _step.track_name
                            and _step.status in ("active", "pending")
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
                # All tracks have content — composition is complete
                logger.info(
                    f"[{trace.trace_id[:8]}] ✅ All tracks have content after iteration {iteration}"
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
    yield await sse_event({
        "type": "complete",
        "success": True,
        "toolCalls": tool_calls_collected,
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
