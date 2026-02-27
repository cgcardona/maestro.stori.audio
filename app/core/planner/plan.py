"""Main planning functions: build_execution_plan, build_execution_plan_stream, etc."""

from __future__ import annotations

import json
import logging
from typing import (
    TYPE_CHECKING,
    AsyncIterator,
    Awaitable,
    Callable,
    TypedDict,
)

if TYPE_CHECKING:
    from app.contracts.llm_types import UsageStats
    from app.core.llm_client import LLMClient
    from app.core.maestro_handlers import UsageTracker

from app.contracts.json_types import JSONObject, ToolCallDict, ToolCallPreviewDict
from app.contracts.pydantic_types import wrap_dict
from app.contracts.llm_types import ChatMessage
from app.core.plan_schemas.models import GenerationRole
from app.core.plan_schemas.plan_json_types import PlanJsonDict

from app.contracts.project_types import ProjectContext

ProjectState = ProjectContext
SSEEventDict = JSONObject  # SSE event wire dict — JSON object emitted to the client

from app.core.expansion import ToolCall
from app.core.intent import IntentResult
from app.prompts import MaestroPrompt
from app.core.prompts import (
    composing_prompt,
    resolve_position,
    sequential_context,
    structured_prompt_routing_context,
    system_prompt_base,
)
from app.core.plan_schemas import (
    ExecutionPlanSchema,
    GenerationStep,
    extract_and_validate_plan,
    complete_plan,
    validate_plan_json,
)
from app.core.planner.models import ExecutionPlan
from app.core.planner.effects import _infer_mix_steps
from app.core.planner.conversion import _schema_to_tool_calls

logger = logging.getLogger(__name__)


def _finalise_plan(
    llm_response_text: str,
    project_state: ProjectState | None = None,
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

    if plan_schema.is_empty():
        logger.warning("⚠️ Plan is empty after completion")
        return ExecutionPlan(
            notes=["Plan is empty - request may be too vague"],
            llm_response_text=llm_response_text,
            validation_result=validation,
        )

    tool_calls = _schema_to_tool_calls(plan_schema, project_state=project_state)

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
    parsed: MaestroPrompt,
    start_beat: float = 0.0,
    project_state: ProjectState | None = None,
) -> ExecutionPlan | None:
    """Build an execution plan deterministically from a structured prompt.

    Requires: style, tempo, roles, and bars. When all are present, the LLM is
    skipped entirely — zero inference overhead.
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

    valid_roles: frozenset[GenerationRole] = frozenset({"drums", "bass", "chords", "melody", "arp", "pads", "fx", "lead"})
    _INSTRUMENT_ROLE_MAP: dict[str, GenerationRole] = {
        "kick": "drums", "snare": "drums", "hihat": "drums", "hi-hat": "drums",
        "percussion": "drums", "drum kit": "drums", "congas": "drums", "bongos": "drums",
        "tabla": "drums", "cajon": "drums", "shaker": "drums", "tambourine": "drums",
        "upright bass": "bass", "electric bass": "bass", "synth bass": "bass",
        "sub bass": "bass", "acoustic bass": "bass", "fretless bass": "bass",
        "piano": "chords", "keys": "chords", "organ": "chords", "rhodes": "chords",
        "wurlitzer": "chords", "clavinet": "chords", "harpsichord": "chords",
        "synth": "pads", "pad": "pads", "strings": "pads", "choir": "pads",
        "guitar": "chords", "acoustic guitar": "chords", "electric guitar": "lead",
        "fiddle": "melody", "violin": "melody", "flute": "melody", "oboe": "melody",
        "clarinet": "melody", "trumpet": "melody", "saxophone": "melody",
        "sax": "melody", "harmonica": "melody", "mandolin": "melody",
        "banjo": "melody", "ukulele": "chords", "harp": "chords",
        "trombone": "melody", "tuba": "bass", "french horn": "melody",
        "cello": "bass", "viola": "melody",
    }

    generations = []
    for role in parsed.roles:
        role_lower = role.lower().strip()
        storpheus_role: GenerationRole
        _identity_map: dict[str, GenerationRole] = {r: r for r in valid_roles}
        if role_lower in _identity_map:
            storpheus_role = _identity_map[role_lower]
            track_name = None
        else:
            storpheus_role = _INSTRUMENT_ROLE_MAP.get(role_lower, "melody")
            track_name = role.strip().title()
        generations.append(
            GenerationStep(
                role=storpheus_role,
                style=parsed.style,
                tempo=parsed.tempo,
                bars=bars,
                key=parsed.key,
                constraints=wrap_dict({k: v for k, v in parsed.constraints.items() if k != "bars"}) or None,
                trackName=track_name,
            )
        )

    plan_schema = ExecutionPlanSchema(generations=generations)
    plan_schema = complete_plan(plan_schema)

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
            *(["f position_offset: start_beat={start_beat}"] if start_beat else []),
        ],
        safety_validated=True,
    )


async def build_execution_plan(
    user_prompt: str,
    project_state: ProjectState,
    route: IntentResult,
    llm: "LLMClient",
    parsed: MaestroPrompt | None = None,
    usage_tracker: "UsageTracker" | None = None,
) -> ExecutionPlan:
    """Ask the LLM for a structured JSON plan for composing.

    Flow:
    1. If structured prompt has all fields, build deterministically (skip LLM).
    2. Otherwise, send prompt to LLM with composing instructions.
    3. Extract JSON from response.
    4. Validate against schema.
    5. Complete plan (infer missing parts).
    6. Convert to ToolCalls.
    """
    start_beat: float = 0.0
    if parsed is not None and parsed.position is not None:
        start_beat = resolve_position(parsed.position, project_state)
        logger.info(
            f"⏱️ Position '{parsed.position.kind}' resolved to beat {start_beat} "
            f"(section='{parsed.section}', ref='{parsed.position.ref}')"
        )

    if parsed is not None:
        deterministic = _try_deterministic_plan(
            parsed, start_beat=start_beat, project_state=project_state,
        )
        if deterministic is not None:
            return deterministic

    sys = system_prompt_base() + "\n" + composing_prompt()

    if parsed is not None:
        sys += structured_prompt_routing_context(parsed)
        if parsed.position is not None:
            sys += sequential_context(start_beat, parsed.section, pos=parsed.position)

    resp = await llm.chat(
        system=sys,
        user=user_prompt,
        tools=[],
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
    project_state: ProjectState,
    route: IntentResult,
    llm: "LLMClient",
    parsed: MaestroPrompt | None = None,
    usage_tracker: "UsageTracker" | None = None,
    emit_sse: Callable[[SSEEventDict], Awaitable[str]] | None = None,
) -> AsyncIterator[ExecutionPlan | str]:
    """Streaming variant of build_execution_plan.

    Yields SSE-formatted reasoning events as the LLM thinks, then yields the
    final ExecutionPlan as the last item. String items are SSE lines; the caller
    keeps the final ExecutionPlan.
    """
    from app.core.stream_utils import ReasoningBuffer

    start_beat: float = 0.0
    if parsed is not None and parsed.position is not None:
        start_beat = resolve_position(parsed.position, project_state)
        logger.info(
            f"⏱️ Position '{parsed.position.kind}' resolved to beat {start_beat}"
        )

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

    messages: list[ChatMessage] = [{"role": "system", "content": sys}]
    if project_state:
        messages.append({
            "role": "system",
            "content": f"Project state: {json.dumps(project_state, indent=2)}",
        })
    messages.append({"role": "user", "content": user_prompt})

    accumulated_content: list[str] = []
    usage: "UsageStats" = {}
    reasoning_buf = ReasoningBuffer()

    async for chunk in llm.chat_completion_stream(
        messages=messages,
        tools=None,
        tool_choice=None,
        temperature=0.1,
        reasoning_fraction=0.15,
    ):
        if chunk["type"] == "reasoning_delta":
            reasoning_text = chunk["text"]
            if reasoning_text:
                to_emit = reasoning_buf.add(reasoning_text)
                if to_emit and emit_sse:
                    yield await emit_sse({"type": "reasoning", "content": to_emit})

        elif chunk["type"] == "content_delta":
            flushed = reasoning_buf.flush()
            if flushed and emit_sse:
                yield await emit_sse({"type": "reasoning", "content": flushed})
            content_text = chunk["text"]
            if content_text:
                accumulated_content.append(content_text)

        elif chunk["type"] == "done":
            flushed = reasoning_buf.flush()
            if flushed and emit_sse:
                yield await emit_sse({"type": "reasoning", "content": flushed})
            if not accumulated_content and chunk["content"]:
                accumulated_content.append(chunk["content"])
            usage = chunk["usage"]

    if usage_tracker and usage:
        usage_tracker.add(
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )

    llm_response_text = "".join(accumulated_content)
    logger.debug(f"📋 Planner (stream) LLM response length: {len(llm_response_text)} chars")

    yield _finalise_plan(llm_response_text, project_state=project_state)


def build_plan_from_dict(
    plan_dict: PlanJsonDict,
    project_state: ProjectState | None = None,
) -> ExecutionPlan:
    """Build an execution plan from a dict (for testing or macro expansion)."""
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

    tool_calls = _schema_to_tool_calls(plan_schema, project_state=project_state)

    return ExecutionPlan(
        tool_calls=tool_calls,
        notes=["Built from dict"],
        safety_validated=True,
        validation_result=validation,
    )


class PlanPreview(TypedDict, total=False):
    """Preview of an execution plan (return type of preview_plan)."""

    valid: bool
    total_steps: int
    generations: int
    edits: int
    tool_calls: list[ToolCallPreviewDict]
    notes: list[str]
    errors: list[str]
    warnings: list[str]


async def preview_plan(
    user_prompt: str,
    project_state: ProjectState,
    route: IntentResult,
    llm: "LLMClient",
    parsed: MaestroPrompt | None = None,
) -> PlanPreview:
    """Generate a plan preview without executing."""
    plan = await build_execution_plan(user_prompt, project_state, route, llm, parsed=parsed)

    preview: PlanPreview = {
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
