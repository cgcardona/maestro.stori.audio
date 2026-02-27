"""
Maestro Core — Cursor-of-DAWs Architecture.

Core orchestration pipeline modules:

1. ROUTER (intent.py)
   - Classifies prompts → Intent + SSEState
   - Computes tool allowlist
   - LLM fallback for unrecognized prompts

2. PLANNER (planner.py)
   - Generates validated execution plans for COMPOSING flows
   - Schema validation via plan_schemas.py

3. EXECUTOR (executor.py)
   - Executes ToolCalls deterministically
   - Uses EntityRegistry for authoritative entity tracking

4. ENTITY REGISTRY (entity_registry.py)
   - Server-side entity ID management
   - Name → ID resolution

5. TOOL VALIDATION (tool_validation.py)
   - Schema + entity reference validation

Main entrypoint: run_pipeline() from pipeline.py
"""
from __future__ import annotations

from maestro.core.tools import ALL_TOOLS, TIER1_TOOLS, TIER2_TOOLS, ToolKind, ToolTier, ToolMeta
from maestro.core.tools import build_tool_registry, get_tool_meta, tools_by_kind
from maestro.core.prompts import system_prompt_base, editing_prompt, composing_prompt
from maestro.core.llm_client import LLMClient, LLMResponse, enforce_single_tool
from maestro.core.intent import get_intent_result, get_intent_result_with_llm, SSEState, Intent, IntentResult, Slots
from maestro.core.pipeline import run_pipeline, PipelineOutput
from maestro.core.planner import build_execution_plan, ExecutionPlan
from maestro.core.expansion import ToolCall, dedupe_tool_calls
from maestro.core.macro_engine import expand_macro, MACROS
from maestro.core.entity_registry import EntityRegistry, create_registry_from_context
from maestro.core.tool_validation import validate_tool_call, ValidationResult
from maestro.core.plan_schemas import ExecutionPlanSchema, validate_plan_json

__all__ = [
    # Tools
    "ALL_TOOLS",
    "TIER1_TOOLS",
    "TIER2_TOOLS",
    "ToolKind",
    "ToolTier",
    "ToolMeta",
    "build_tool_registry",
    "get_tool_meta",
    "tools_by_kind",
    # Prompts
    "system_prompt_base",
    "editing_prompt",
    "composing_prompt",
    # LLM Client
    "LLMClient",
    "LLMResponse",
    "enforce_single_tool",
    # Intent Router
    "get_intent_result",
    "get_intent_result_with_llm",
    "SSEState",
    "Intent",
    "IntentResult",
    "Slots",
    # Pipeline
    "run_pipeline",
    "PipelineOutput",
    # Planner
    "build_execution_plan",
    "ExecutionPlan",
    "ExecutionPlanSchema",
    "validate_plan_json",
    # Entity Registry
    "EntityRegistry",
    "create_registry_from_context",
    # Tool Validation
    "validate_tool_call",
    "ValidationResult",
    # Expansion
    "ToolCall",
    "dedupe_tool_calls",
    # Macros
    "expand_macro",
    "MACROS",
]
