"""
Tool definitions and metadata for the Stori Maestro.

Tools are classified by kind:
  * PRIMITIVE (deterministic, reversible, single-mutation) — safe for direct LLM use
  * GENERATOR (creative / stochastic / expensive)          — planner-gated
  * MACRO     (multi-step convenience)                     — never directly callable by LLM

And by tier:
  * Tier 1: server-side generation/execution
  * Tier 2: client-side DAW control (Swift)
"""
from __future__ import annotations

from app.core.tools.metadata import ToolTier, ToolKind, ToolMeta
from app.core.tools.definitions import TIER1_TOOLS, TIER2_TOOLS, ALL_TOOLS
from app.core.tools.registry import (
    build_tool_registry,
    get_tool_meta,
    tools_by_kind,
    tool_schema_by_name,
)

__all__ = [
    "ToolTier",
    "ToolKind",
    "ToolMeta",
    "TIER1_TOOLS",
    "TIER2_TOOLS",
    "ALL_TOOLS",
    "build_tool_registry",
    "get_tool_meta",
    "tools_by_kind",
    "tool_schema_by_name",
]
