"""
Maestro Pipeline.

Main entry point for processing user prompts through the intent → execution flow.

Responsibilities:
- Route prompts through intent classification
- Direct THINKING requests to LLM for answers (no tools)
- Direct EDITING requests to LLM with tool allowlist
- Direct COMPOSING requests to planner for structured execution

Flow:
1) Intent routing (app.core.intent.get_intent_result)
2) If THINKING -> answer (no tools)
3) If EDITING -> LLM tool calls with strict allowlist
4) If COMPOSING -> planner produces an ExecutionPlan (generator + primitives)
5) Executor runs the plan, resolving ids and references.

The pipeline makes tool over-completion structurally hard.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maestro.contracts.project_types import ProjectContext
    from maestro.core.maestro_handlers import UsageTracker

from maestro.core.intent import get_intent_result, SSEState, IntentResult, Intent
from maestro.core.planner import build_execution_plan, build_execution_plan_stream, ExecutionPlan
from maestro.prompts import MaestroPrompt
from maestro.core.llm_client import LLMClient, LLMResponse
from maestro.core.prompts import system_prompt_base, editing_prompt, composing_prompt, resolve_position, sequential_context, structured_prompt_context
from maestro.core.tools import ALL_TOOLS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineOutput:
    """Immutable result of one ``run_pipeline`` invocation.

    Exactly one of ``llm_response`` or ``plan`` is populated, depending on the
    intent route:

    - ``REASONING`` → ``llm_response`` set, ``plan`` is ``None``
    - ``EDITING``   → ``llm_response`` set (tool calls inside), ``plan`` is ``None``
    - ``COMPOSING`` → ``plan`` set, ``llm_response`` is ``None``

    Callers discriminate on ``route.sse_state`` (or ``route.intent``) first,
    then unpack the appropriate field.

    Attributes:
        route: Full intent classification result including ``sse_state``,
            ``allowed_tool_names``, and ``slots``.
        llm_response: Raw LLM response for REASONING and EDITING paths.
        plan: Structured ``ExecutionPlan`` for the COMPOSING path.
    """

    route: IntentResult
    llm_response: LLMResponse | None = None
    plan: ExecutionPlan | None = None


async def run_pipeline(
    user_prompt: str,
    project_state: "ProjectContext",
    llm: LLMClient,
    usage_tracker: "UsageTracker" | None = None,
) -> PipelineOutput:
    """
    Main runtime entrypoint.

    Args:
      user_prompt: raw user text
      project_state: state injected into prompts (tracks/regions/selection)
      llm: your LLM client wrapper

    Returns:
      PipelineOutput containing route, and either llm_response or plan
    """
    route = get_intent_result(user_prompt, project_state)

    # REASONING paths: no tools by default
    if route.sse_state == SSEState.REASONING:
        # Let your chat LLM answer normally (no tools)
        resp = await llm.chat(
            system=system_prompt_base(),
            user=user_prompt,
            tools=[],
            tool_choice="none",
            context={"project_state": project_state, "route": route.__dict__},
        )
        return PipelineOutput(route=route, llm_response=resp)

    _parsed_raw = route.slots.extras.get("parsed_prompt")
    parsed: MaestroPrompt | None = _parsed_raw if isinstance(_parsed_raw, MaestroPrompt) else None

    # COMPOSING: planner path
    if route.sse_state == SSEState.COMPOSING or route.intent == Intent.GENERATE_MUSIC:
        plan = await build_execution_plan(
            user_prompt=user_prompt,
            project_state=project_state,
            route=route,
            llm=llm,
            parsed=parsed,
            usage_tracker=usage_tracker,
        )
        return PipelineOutput(route=route, plan=plan)

    # EDITING: tool calling with allowlist
    required_single_tool = bool(route.force_stop_after and route.tool_choice == "required")
    sys = system_prompt_base() + "\n" + editing_prompt(required_single_tool)

    if parsed is not None:
        sys += structured_prompt_context(parsed)
        if parsed.position is not None:
            start_beat = resolve_position(parsed.position, project_state or {})
            sys += sequential_context(start_beat, parsed.section, pos=parsed.position)

    # You can pass ALL_TOOLS for caching and enforce allowlist server-side, or pass only allowed tools.
    # Cursor-style: pass only allowed tools for this request.
    allowed_tools = [t for t in ALL_TOOLS if t["function"]["name"] in route.allowed_tool_names]

    resp = await llm.chat(
        system=sys,
        user=user_prompt,
        tools=allowed_tools,
        tool_choice="auto" if not required_single_tool else "required",
        context={"project_state": project_state, "route": route.__dict__},
    )
    return PipelineOutput(route=route, llm_response=resp)
