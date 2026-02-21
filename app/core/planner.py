"""
Planner for Stori Maestro.

Converts natural language music requests into validated execution plans.

Responsibilities:
- Parse LLM JSON responses into structured plans
- Validate plans against Pydantic schemas
- Infer missing edits (tracks/regions) from generation requests
- Convert validated plans to executable ToolCall sequences

Used for COMPOSING flows where the user wants to generate music.
The planner outputs an ExecutionPlan containing primitives + generator calls.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Optional

if TYPE_CHECKING:
    from app.core.maestro_handlers import UsageTracker

from app.core.expansion import ToolCall
from app.core.intent import IntentResult, Intent
from app.core.prompt_parser import ParsedPrompt
from app.core.tools import build_tool_registry
from app.core.prompts import composing_prompt, resolve_position, sequential_context, structured_prompt_routing_context, system_prompt_base
from app.core.plan_schemas import (
    ExecutionPlanSchema,
    GenerationStep,
    MixStep,
    extract_and_validate_plan,
    complete_plan,
    PlanValidationResult,
)

logger = logging.getLogger(__name__)


@dataclass
class ExecutionPlan:
    """
    Validated execution plan ready for the executor.
    """
    tool_calls: list[ToolCall] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    safety_validated: bool = False
    llm_response_text: Optional[str] = None
    validation_result: Optional[PlanValidationResult] = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "notes": self.notes,
            "safety_validated": self.safety_validated,
            "validation_errors": self.validation_result.errors if self.validation_result else [],
        }
    
    @property
    def is_valid(self) -> bool:
        """Check if plan is valid and has tool calls."""
        return self.safety_validated and len(self.tool_calls) > 0
    
    @property
    def generation_count(self) -> int:
        """Count of generation tool calls."""
        return sum(1 for tc in self.tool_calls if tc.name.startswith("stori_generate"))
    
    @property
    def edit_count(self) -> int:
        """Count of edit tool calls."""
        return sum(1 for tc in self.tool_calls if tc.name in (
            "stori_add_midi_track", "stori_add_midi_region"
        ))


async def build_execution_plan(
    user_prompt: str,
    project_state: dict[str, Any],
    route: IntentResult,
    llm,
    parsed: Optional[ParsedPrompt] = None,
    usage_tracker: Optional["UsageTracker"] = None,
) -> ExecutionPlan:
    """
    Ask the LLM for a structured JSON plan for composing.
    
    Flow:
    1. If structured prompt has all fields, build deterministically (skip LLM)
    2. Otherwise, send prompt to LLM with composing instructions
    3. Extract JSON from response
    4. Validate against schema
    5. Complete plan (infer missing parts)
    6. Convert to ToolCalls
    
    Args:
        user_prompt: User's request
        project_state: Current DAW state
        route: Intent routing result
        llm: LLM client
        parsed: Optional parsed structured prompt for deterministic planning
        
    Returns:
        ExecutionPlan ready for executor
    """
    # Resolve Position: field once so both the deterministic path and the
    # LLM fallback path use the same beat offset.
    start_beat: float = 0.0
    if parsed is not None and parsed.position is not None:
        start_beat = resolve_position(parsed.position, project_state)
        logger.info(
            f"⏱️ Position '{parsed.position.kind}' resolved to beat {start_beat} "
            f"(section='{parsed.section}', ref='{parsed.position.ref}')"
        )

    # Structured prompt fast path: if all key fields are present, build
    # the plan deterministically without an LLM call.
    if parsed is not None:
        deterministic = _try_deterministic_plan(
            parsed, start_beat=start_beat, project_state=project_state,
        )
        if deterministic is not None:
            return deterministic

    sys = system_prompt_base() + "\n" + composing_prompt()

    # Inject structured context if available (partial structured prompt
    # that couldn't be built deterministically).  Routing fields only —
    # Maestro extensions go to the executor, not the planner.
    if parsed is not None:
        sys += structured_prompt_routing_context(parsed)
        if parsed.position is not None:
            sys += sequential_context(start_beat, parsed.section, pos=parsed.position)
    
    # Call LLM for plan
    resp = await llm.chat(
        system=sys,
        user=user_prompt,
        tools=[],  # No tools: planner produces JSON plan
        tool_choice="none",
        context={"project_state": project_state, "route": route.__dict__},
    )

    if usage_tracker and resp.usage:
        usage_tracker.add(
            resp.usage.get("prompt_tokens", 0),
            resp.usage.get("completion_tokens", 0),
        )

    llm_response_text = resp.content or ""
    logger.debug(f"📋 Planner LLM response length: {len(llm_response_text)} chars")

    return _finalise_plan(llm_response_text, project_state=project_state)


async def build_execution_plan_stream(
    user_prompt: str,
    project_state: dict[str, Any],
    route: IntentResult,
    llm: Any,
    parsed: Optional[ParsedPrompt] = None,
    usage_tracker: Optional["UsageTracker"] = None,
    emit_sse: Optional[Callable[[dict[str, Any]], Awaitable[str]]] = None,
) -> AsyncIterator[ExecutionPlan | str]:
    """Streaming variant of build_execution_plan.

    Yields SSE-formatted reasoning events as the LLM thinks, then yields
    the final ExecutionPlan as the last item.  The caller should iterate
    the generator, forwarding all ``str`` items to the SSE stream and
    keeping the final ``ExecutionPlan``.

    When the deterministic fast-path fires (structured prompt with all
    required fields), no reasoning is emitted — the plan is yielded
    immediately.
    """
    from app.core.sse_utils import ReasoningBuffer

    start_beat: float = 0.0
    if parsed is not None and parsed.position is not None:
        start_beat = resolve_position(parsed.position, project_state)
        logger.info(
            f"⏱️ Position '{parsed.position.kind}' resolved to beat {start_beat} "
            f"(section='{parsed.section}', ref='{parsed.position.ref}')"
        )

    # Deterministic fast-path — no LLM, no reasoning to stream.
    if parsed is not None:
        deterministic = _try_deterministic_plan(
            parsed, start_beat=start_beat, project_state=project_state,
        )
        if deterministic is not None:
            yield deterministic
            return

    sys = system_prompt_base() + "\n" + composing_prompt()

    if parsed is not None:
        sys += structured_prompt_routing_context(parsed)
        if parsed.position is not None:
            sys += sequential_context(start_beat, parsed.section, pos=parsed.position)

    # Build messages matching llm.chat() format
    messages: list[dict[str, Any]] = [{"role": "system", "content": sys}]
    if project_state:
        messages.append({
            "role": "system",
            "content": f"Project state: {json.dumps(project_state, indent=2)}",
        })
    messages.append({"role": "user", "content": user_prompt})

    # Stream the LLM response, forwarding reasoning events
    accumulated_content: list[str] = []
    usage: dict[str, Any] = {}
    reasoning_buf = ReasoningBuffer()

    async for chunk in llm.chat_completion_stream(
        messages=messages,
        tools=None,
        tool_choice=None,
        temperature=0.1,
        reasoning_fraction=0.15,
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
            flushed = reasoning_buf.flush()
            if flushed and emit_sse:
                yield await emit_sse({
                    "type": "reasoning",
                    "content": flushed,
                })
            content_text = chunk.get("text", "")
            if content_text:
                accumulated_content.append(content_text)

        elif chunk.get("type") == "done":
            flushed = reasoning_buf.flush()
            if flushed and emit_sse:
                yield await emit_sse({
                    "type": "reasoning",
                    "content": flushed,
                })
            # Prefer accumulated content; fall back to done payload.
            if not accumulated_content and chunk.get("content"):
                accumulated_content.append(chunk["content"])
            usage = chunk.get("usage", {})

    if usage_tracker and usage:
        usage_tracker.add(
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )

    llm_response_text = "".join(accumulated_content)
    logger.debug(f"📋 Planner (stream) LLM response length: {len(llm_response_text)} chars")

    # From here the logic is identical to the non-streaming path.
    yield _finalise_plan(llm_response_text, project_state=project_state)


def _finalise_plan(
    llm_response_text: str,
    project_state: Optional[dict[str, Any]] = None,
) -> ExecutionPlan:
    """Shared post-LLM logic: validate, complete, and convert a plan."""
    validation = extract_and_validate_plan(llm_response_text)

    if not validation.valid:
        logger.warning(f"⚠️ Plan validation failed: {validation.errors}")
        return ExecutionPlan(
            notes=[f"Plan validation failed: {'; '.join(validation.errors)}"],
            llm_response_text=llm_response_text,
            validation_result=validation,
        )

    if validation.plan is None:
        return ExecutionPlan(
            notes=["Plan schema missing after validation"],
            llm_response_text=llm_response_text,
            validation_result=validation,
        )
    plan_schema = complete_plan(validation.plan)
    if plan_schema is None:
        return ExecutionPlan(
            notes=["Plan schema could not be completed"],
            llm_response_text=llm_response_text,
            validation_result=validation,
        )
    if plan_schema.is_empty():
        logger.warning("⚠️ Plan is empty after completion")
        return ExecutionPlan(
            notes=["Plan is empty - request may be too vague"],
            llm_response_text=llm_response_text,
            validation_result=validation,
        )

    tool_calls = _schema_to_tool_calls(
        plan_schema, project_state=project_state,
    )

    logger.info(
        f"✅ Planner generated {len(tool_calls)} tool calls "
        f"({plan_schema.generation_count()} generations, "
        f"{len(plan_schema.edits)} edits, {len(plan_schema.mix)} mix)"
    )

    return ExecutionPlan(
        tool_calls=tool_calls,
        notes=[
            f"planner: {len(tool_calls)} tool calls",
            *validation.warnings,
        ],
        safety_validated=True,
        llm_response_text=llm_response_text,
        validation_result=validation,
    )


def _try_deterministic_plan(
    parsed: ParsedPrompt,
    start_beat: float = 0.0,
    project_state: Optional[dict[str, Any]] = None,
) -> Optional[ExecutionPlan]:
    """
    Build an execution plan deterministically from a structured prompt.

    Requires: style, tempo, roles, and bars (from constraints).
    When all are present, we skip the LLM entirely — zero inference overhead.

    Args:
        parsed: The parsed structured prompt.
        start_beat: Beat offset for all new regions, resolved from Position:.
            0.0 means start of project; 16.0 means after a 4-bar intro, etc.
        project_state: Current project state — used to skip track creation for
            existing tracks and to attach their UUIDs to region/generator calls.
    """
    if not parsed.style or not parsed.tempo or not parsed.roles:
        return None

    bars = parsed.constraints.get("bars")
    if not isinstance(bars, int) or bars < 1:
        return None

    logger.info(
        f"⚡ Deterministic plan from structured prompt: "
        f"{len(parsed.roles)} roles, {parsed.style}, {parsed.tempo} BPM, {bars} bars"
        + (f", start_beat={start_beat}" if start_beat else "")
    )

    generations = [
        GenerationStep(
            role=role if role in ("drums", "bass", "chords", "melody", "arp", "pads", "fx", "lead") else "melody",
            style=parsed.style,
            tempo=parsed.tempo,
            bars=bars,
            key=parsed.key,
            constraints={
                k: v for k, v in parsed.constraints.items()
                if k not in ("bars",)
            } or None,
        )
        for role in parsed.roles
    ]

    plan_schema = ExecutionPlanSchema(generations=generations)
    plan_schema = complete_plan(plan_schema)
    if plan_schema is None:
        return None

    # Infer and attach effects based on style + roles (constraints may opt out)
    if not parsed.constraints.get("no_effects") and not parsed.constraints.get("no reverb"):
        mix_steps = _infer_mix_steps(parsed.style, parsed.roles)
        if mix_steps:
            plan_schema = plan_schema.model_copy(update={"mix": mix_steps})

    tool_calls = _schema_to_tool_calls(
        plan_schema,
        region_start_offset=start_beat,
        project_state=project_state,
    )

    return ExecutionPlan(
        tool_calls=tool_calls,
        notes=[
            f"deterministic_plan: {len(tool_calls)} tool calls from structured prompt",
            f"style={parsed.style}, tempo={parsed.tempo}, bars={bars}",
            *([ f"position_offset: start_beat={start_beat}" ] if start_beat else []),
        ],
        safety_validated=True,
    )


# =============================================================================
# Style → Effects inference for deterministic plans
# =============================================================================

# Per-role effects always applied regardless of style
_ROLE_ALWAYS_EFFECTS: dict[str, list[str]] = {
    "drums": ["compressor"],
    "bass":  ["compressor"],
    "pads":  ["reverb"],
    "lead":  ["reverb"],
    "chords": [],
    "melody": [],
    "arp":    ["reverb"],
    "fx":     ["reverb", "delay"],
}

# Style-keyword → additional per-role overrides
# Each entry: {style_keyword: {role: [effects]}}
_STYLE_ROLE_EFFECTS: list[tuple[str, dict[str, list[str]]]] = [
    # Rock / Metal / Prog
    ("rock",       {"lead": ["distortion", "reverb"], "drums": ["compressor"], "bass": ["overdrive"]}),
    ("metal",      {"lead": ["distortion"], "drums": ["compressor"], "bass": ["distortion"]}),
    ("prog",       {"pads": ["reverb", "chorus"], "lead": ["reverb", "distortion"]}),
    ("psychedel",  {"pads": ["reverb", "flanger"], "lead": ["reverb", "phaser"]}),

    # Electronic
    ("house",      {"drums": ["compressor"], "bass": ["compressor", "filter"], "pads": ["reverb", "chorus"]}),
    ("techno",     {"drums": ["compressor"], "bass": ["distortion", "filter"]}),
    ("trap",       {"drums": ["compressor"], "bass": ["filter"]}),
    ("dubstep",    {"bass": ["distortion", "filter"], "drums": ["compressor"]}),
    ("edm",        {"drums": ["compressor"], "pads": ["reverb", "chorus"], "lead": ["reverb", "delay"]}),

    # Ambient / Cinematic
    ("ambient",    {"pads": ["reverb", "chorus"], "melody": ["reverb", "delay"], "lead": ["reverb", "delay"]}),
    ("cinematic",  {"pads": ["reverb"], "melody": ["reverb", "delay"]}),
    ("post rock",  {"pads": ["reverb", "delay"], "lead": ["reverb", "delay"]}),
    ("shoegaze",   {"lead": ["reverb", "chorus", "distortion"], "pads": ["reverb", "flanger"]}),

    # Jazz / Blues
    ("jazz",       {"chords": ["reverb"], "melody": ["reverb"], "bass": [], "drums": ["reverb"]}),
    ("blues",      {"lead": ["overdrive", "reverb"], "chords": ["reverb"]}),
    ("funk",       {"bass": ["compressor"], "drums": ["compressor"], "chords": ["chorus"]}),

    # Lo-fi / Vintage
    ("lofi",       {"drums": ["filter", "compressor"], "chords": ["reverb", "chorus"], "bass": []}),
    ("lo-fi",      {"drums": ["filter", "compressor"], "chords": ["reverb", "chorus"]}),
    ("vintage",    {"chords": ["chorus", "reverb"], "melody": ["reverb"]}),
    ("tape",       {"drums": ["compressor"], "pads": ["reverb"]}),

    # Soul / R&B / Neosoul
    ("soul",       {"chords": ["reverb", "chorus"], "melody": ["reverb"]}),
    ("r&b",        {"chords": ["reverb"], "bass": ["compressor"]}),
    ("neosoul",    {"chords": ["reverb", "chorus"], "pads": ["reverb"], "drums": ["compressor"]}),

    # Classical / Orchestral
    ("classical",  {"pads": ["reverb"], "melody": ["reverb"], "chords": ["reverb"]}),
    ("orchestral", {"pads": ["reverb"], "melody": ["reverb"], "chords": ["reverb"]}),

    # Pop
    ("pop",        {"drums": ["compressor"], "chords": ["reverb"], "melody": ["reverb"]}),
    ("synth",      {"pads": ["chorus", "reverb"], "lead": ["reverb", "delay"]}),
]


def _infer_mix_steps(
    style: str,
    roles: list[str],
) -> list["MixStep"]:
    """Infer MixStep effects for a style + role combination.

    Returns a flat list of MixStep objects ready to include in ExecutionPlanSchema.mix.
    A shared Reverb bus is created and sends are added from every track that gets reverb.
    Direct inserts (compressor, EQ, distortion) go on the individual tracks.
    """
    style_lower = style.lower()

    # Collect per-role effects: start from always-on effects, then apply style overrides
    role_effects: dict[str, set[str]] = {}
    for role in roles:
        effects = set(_ROLE_ALWAYS_EFFECTS.get(role, []))
        for keyword, overrides in _STYLE_ROLE_EFFECTS:
            if keyword in style_lower:
                extra = overrides.get(role, [])
                effects.update(extra)
        if effects:
            role_effects[role] = effects

    if not role_effects:
        return []

    # Separate reverb (→ bus send) from insert effects
    needs_reverb_bus = any("reverb" in efx for efx in role_effects.values())

    steps: list[MixStep] = []

    for role in roles:
        track_name = role.capitalize()
        effects = role_effects.get(role, set())

        # Insert effects (everything except reverb)
        for efx in sorted(effects - {"reverb"}):
            try:
                steps.append(MixStep(action="add_insert", track=track_name, type=efx))
            except Exception:
                pass  # skip invalid effect types

        # Reverb → send to bus rather than insert
        if "reverb" in effects and needs_reverb_bus:
            try:
                steps.append(MixStep(action="add_send", track=track_name, bus="Reverb"))
            except Exception:
                pass

    return steps


_ROLE_INSTRUMENT_HINTS: dict[str, set[str]] = {
    "melody": {"organ", "piano", "guitar", "flute", "sax", "saxophone",
               "trumpet", "violin", "synth", "lead", "keys", "keyboard",
               "harmonica", "clarinet", "oboe", "fiddle", "mandolin"},
    "bass": {"bass"},
    "drums": {"drums", "drum", "percussion", "kit"},
    "chords": {"organ", "piano", "guitar", "keys", "keyboard", "chord",
               "rhodes", "wurlitzer", "clavinet", "harpsichord"},
    "pads": {"pad", "strings", "ambient"},
    "arp": {"arp", "synth"},
    "lead": {"lead", "synth", "organ", "piano", "guitar"},
}


def _match_roles_to_existing_tracks(
    roles: set[str],
    project_state: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Map generation roles to existing project tracks by name, inferred role, then instrument.

    Returns a dict of role → {"name": str, "id": str, "gmProgram": int|None}
    for each role that has a matching existing track. Roles with no match
    are absent from the result (the caller should create new tracks for them).
    """
    from app.core.entity_context import infer_track_role

    tracks = project_state.get("tracks", [])
    if not tracks:
        return {}

    # Index existing tracks with inferred role
    existing: list[dict[str, Any]] = []
    for t in tracks:
        tname = t.get("name", "")
        gm = t.get("gmProgram")
        drum_kit = t.get("drumKitId")
        inferred_role = t.get("role") or infer_track_role(tname, gm, drum_kit)
        existing.append({
            "name": tname,
            "id": t.get("id", ""),
            "gmProgram": gm,
            "instrument": t.get("instrument", ""),
            "inferred_role": inferred_role,
        })

    matched: dict[str, dict[str, Any]] = {}
    claimed_ids: set[str] = set()

    # Pass 1: inferred-role match (highest confidence — e.g. "pads" matches "Cathedral Pad")
    for role in sorted(roles):
        for track in existing:
            if track["id"] in claimed_ids:
                continue
            if track["inferred_role"] == role:
                matched[role] = track
                claimed_ids.add(track["id"])
                break

    # Pass 2: exact name match for remaining roles
    remaining_roles = roles - set(matched.keys())
    for role in sorted(remaining_roles):
        for track in existing:
            if track["id"] in claimed_ids:
                continue
            name_lower = track["name"].lower()
            if name_lower == role or role in name_lower:
                matched[role] = track
                claimed_ids.add(track["id"])
                break

    # Pass 3: instrument-keyword heuristic for remaining roles
    remaining_roles = roles - set(matched.keys())
    for role in sorted(remaining_roles):
        hints = _ROLE_INSTRUMENT_HINTS.get(role, set())
        if not hints:
            continue
        for track in existing:
            if track["id"] in claimed_ids:
                continue
            name_lower = track["name"].lower()
            inst_lower = (track.get("instrument") or "").lower()
            if any(h in name_lower or h in inst_lower for h in hints):
                matched[role] = track
                claimed_ids.add(track["id"])
                break

    return matched


def _build_role_to_track_map(
    plan: ExecutionPlanSchema,
    project_state: Optional[dict[str, Any]] = None,
) -> dict[str, str]:
    """
    Build a mapping from generation role to actual track name.
    
    Checks existing project tracks first (by name, then instrument keyword),
    then falls back to plan edits. This prevents creating duplicate tracks
    when the project already has matching instruments.
    
    Args:
        plan: The validated execution plan
        project_state: Current project state (tracks with names/IDs)
        
    Returns:
        Dict mapping role (lowercase) to track name (original casing)
    """
    role_to_track: dict[str, str] = {}
    
    # If project context has existing tracks, prefer them
    gen_roles = {g.role.lower() for g in plan.generations}
    if project_state:
        existing_match = _match_roles_to_existing_tracks(gen_roles, project_state)
        for role, info in existing_match.items():
            role_to_track[role] = info["name"]

    # Extract all track names from edits (for roles not yet matched)
    track_names: list[str] = [
        edit.name for edit in plan.edits 
        if edit.action == "add_track" and edit.name
    ]
    
    # For each generation role, find a matching track
    all_roles = {"drums", "bass", "chords", "melody", "arp", "pads", "fx", "lead"}
    
    for role in all_roles:
        if role in role_to_track:
            continue
        # Check each track for a match
        for track_name in track_names:
            track_lower = track_name.lower()
            if track_lower == role or role in track_lower:
                role_to_track[role] = track_name
                break
        
        # Default to capitalized role if no match found
        if role not in role_to_track:
            role_to_track[role] = role.capitalize()
    
    return role_to_track


def _schema_to_tool_calls(
    plan: ExecutionPlanSchema,
    region_start_offset: float = 0.0,
    project_state: Optional[dict[str, Any]] = None,
) -> list[ToolCall]:
    """
    Convert validated plan schema to ToolCalls.

    Tool calls are grouped contiguously by track so the Execution Timeline
    renders coherent per-instrument sections. Order within each track:
      1. stori_add_midi_track (creation)
      2. stori_set_track_color / stori_set_track_icon (styling)
      3. stori_add_midi_region (region creation)
      4. stori_generate_midi (content generation)
      5. stori_add_insert_effect (insert effects for this track)

    After all per-track groups:
      6. stori_ensure_bus + stori_add_send (shared bus routing)
      7. stori_set_track_volume / stori_set_track_pan (mix adjustments)

    Uses role→track mapping to ensure generations target the correct tracks
    when LLM uses descriptive names like "Jam Drums" instead of just "Drums".

    Args:
        region_start_offset: Beat offset applied to every new region's startBeat.
            Comes from a resolved Position: field (e.g. 16.0 = after a 4-bar intro).
        project_state: Current project state. When provided, existing tracks are
            reused (skipping add_track/set_color/set_icon) and their UUIDs are
            attached to region and generator tool calls via trackId.
    """
    project_state = project_state or {}

    # Build index of existing tracks: lowercase name → {id, name, ...}
    existing_tracks: dict[str, dict[str, Any]] = {}
    for t in project_state.get("tracks", []):
        name = t.get("name", "")
        if name:
            existing_tracks[name.lower()] = {
                "id": t.get("id", ""),
                "name": name,
                "gmProgram": t.get("gmProgram"),
            }

    # Build role→track name mapping for consistent targeting
    role_to_track = _build_role_to_track_map(plan, project_state)

    _role_mapped_existing: set[str] = set()
    for role, target_name in role_to_track.items():
        if target_name.lower() in existing_tracks:
            _role_mapped_existing.add(role)

    from app.core.track_styling import get_track_styling

    # ── Index edits and mix steps by track for per-track grouping ──
    edits_by_track: dict[str, list[Any]] = {}
    regions_by_track: dict[str, list[Any]] = {}
    for edit in plan.edits:
        if edit.action == "add_track" and edit.name:
            edits_by_track.setdefault(edit.name.lower(), []).append(edit)
        elif edit.action == "add_region" and edit.track:
            resolved = role_to_track.get(edit.track.lower(), edit.track)
            regions_by_track.setdefault(resolved.lower(), []).append(edit)

    inserts_by_track: dict[str, list[Any]] = {}
    sends: list[Any] = []
    buses: set[str] = set()
    volume_pan: list[Any] = []
    for mix in plan.mix:
        if mix.action == "add_insert" and mix.type:
            inserts_by_track.setdefault(mix.track.lower(), []).append(mix)
        elif mix.action == "add_send" and mix.bus:
            sends.append(mix)
            buses.add(mix.bus)
        elif mix.action in ("set_volume", "set_pan"):
            volume_pan.append(mix)

    # Determine the ordered list of track names (generation order preserves
    # the user's requested role ordering, which is musically meaningful).
    ordered_tracks: list[str] = []
    seen_lower: set[str] = set()

    for gen in plan.generations:
        tname = role_to_track.get(gen.role, gen.role.capitalize())
        if tname.lower() not in seen_lower:
            ordered_tracks.append(tname)
            seen_lower.add(tname.lower())

    # Include tracks from edits that have no generation (e.g. manual adds)
    for edit in plan.edits:
        if edit.action == "add_track" and edit.name:
            if edit.name.lower() not in seen_lower:
                ordered_tracks.append(edit.name)
                seen_lower.add(edit.name.lower())

    # ── Emit per-track groups contiguously ──
    tool_calls: list[ToolCall] = []

    for track_name in ordered_tracks:
        t_lower = track_name.lower()
        is_existing = t_lower in existing_tracks
        is_role_mapped = t_lower in _role_mapped_existing

        # 1. Track creation
        if not is_existing and not is_role_mapped and t_lower in edits_by_track:
            styling = get_track_styling(track_name)
            tool_calls.append(ToolCall(
                name="stori_add_midi_track",
                params={"name": track_name},
            ))
            # 2. Styling immediately after creation
            tool_calls.append(ToolCall(
                name="stori_set_track_color",
                params={"trackName": track_name, "color": styling["color"]},
            ))
            tool_calls.append(ToolCall(
                name="stori_set_track_icon",
                params={"trackName": track_name, "icon": styling["icon"]},
            ))

        # 3. Region creation
        for edit in regions_by_track.get(t_lower, []):
            bar_start = edit.barStart or 0
            resolved_track = role_to_track.get(edit.track.lower(), edit.track)
            region_params: dict[str, Any] = {
                "name": resolved_track,
                "trackName": resolved_track,
                "startBeat": bar_start * 4 + region_start_offset,
                "durationBeats": edit.bars * 4,
            }
            existing = existing_tracks.get(resolved_track.lower())
            if existing and existing["id"]:
                region_params["trackId"] = existing["id"]
            tool_calls.append(ToolCall(
                name="stori_add_midi_region",
                params=region_params,
            ))

        # 4. Content generation
        for gen in plan.generations:
            gen_track = role_to_track.get(gen.role, gen.role.capitalize())
            if gen_track.lower() != t_lower:
                continue
            normalized_style = gen.style.replace("_", " ").strip() if gen.style else ""
            gen_params: dict[str, Any] = {
                "role": gen.role,
                "style": normalized_style,
                "tempo": gen.tempo,
                "bars": gen.bars,
                "key": gen.key or "",
                "trackName": gen_track,
                "constraints": gen.constraints or {},
            }
            existing = existing_tracks.get(gen_track.lower())
            if existing and existing["id"]:
                gen_params["trackId"] = existing["id"]
            tool_calls.append(ToolCall(
                name="stori_generate_midi",
                params=gen_params,
            ))

        # 5. Insert effects for this track
        for mix in inserts_by_track.get(t_lower, []):
            tool_calls.append(ToolCall(
                name="stori_add_insert_effect",
                params={"trackName": mix.track, "type": mix.type},
            ))

    # ── Shared routing (buses + sends) ──
    _buses_ensured: set[str] = set()
    for bus_name in sorted(buses):
        tool_calls.append(ToolCall(
            name="stori_ensure_bus",
            params={"name": bus_name},
        ))
        _buses_ensured.add(bus_name)

    for mix in sends:
        tool_calls.append(ToolCall(
            name="stori_add_send",
            params={"trackName": mix.track, "busName": mix.bus},
        ))

    # ── Mix adjustments ──
    for mix in volume_pan:
        if mix.action == "set_volume" and mix.value is not None:
            tool_calls.append(ToolCall(
                name="stori_set_track_volume",
                params={"trackName": mix.track, "volume": mix.value},
            ))
        elif mix.action == "set_pan" and mix.value is not None:
            tool_calls.append(ToolCall(
                name="stori_set_track_pan",
                params={"trackName": mix.track, "pan": mix.value},
            ))

    return tool_calls


# =============================================================================
# Alternative: Direct Plan Building (for testing/macros)
# =============================================================================

def build_plan_from_dict(
    plan_dict: dict[str, Any],
    project_state: Optional[dict[str, Any]] = None,
) -> ExecutionPlan:
    """
    Build an execution plan from a dict (for testing or macro expansion).
    
    Args:
        plan_dict: Plan dictionary in the expected format
        project_state: Optional project state for existing track reuse
        
    Returns:
        ExecutionPlan
    """
    from app.core.plan_schemas import validate_plan_json, complete_plan
    
    validation = validate_plan_json(plan_dict)
    
    if not validation.valid:
        return ExecutionPlan(
            notes=[f"Validation failed: {'; '.join(validation.errors)}"],
            validation_result=validation,
        )
    
    if validation.plan is None:
        return ExecutionPlan(
            notes=["Plan schema missing after validation"],
            validation_result=validation,
        )
    plan_schema = complete_plan(validation.plan)
    if plan_schema is None:
        return ExecutionPlan(
            notes=["Plan schema could not be completed"],
            validation_result=validation,
        )
    tool_calls = _schema_to_tool_calls(plan_schema, project_state=project_state)
    
    return ExecutionPlan(
        tool_calls=tool_calls,
        notes=["Built from dict"],
        safety_validated=True,
        validation_result=validation,
    )


# =============================================================================
# Plan Preview
# =============================================================================

async def preview_plan(
    user_prompt: str,
    project_state: dict[str, Any],
    route: IntentResult,
    llm,
    parsed: Optional[ParsedPrompt] = None,
) -> dict[str, Any]:
    """
    Generate a plan preview without executing.
    
    Returns a summary of what would happen, for user confirmation.
    """
    plan = await build_execution_plan(user_prompt, project_state, route, llm, parsed=parsed)
    
    preview = {
        "valid": plan.is_valid,
        "total_steps": len(plan.tool_calls),
        "generations": plan.generation_count,
        "edits": plan.edit_count,
        "tool_calls": [tc.to_dict() for tc in plan.tool_calls],
        "notes": plan.notes,
    }
    
    if plan.validation_result and plan.validation_result.errors:
        preview["errors"] = plan.validation_result.errors
    
    if plan.validation_result and plan.validation_result.warnings:
        preview["warnings"] = plan.validation_result.warnings
    
    return preview
