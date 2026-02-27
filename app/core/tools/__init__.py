"""Tool metadata and schema access for the Maestro orchestrator.

Generic tool classification types (``ToolTier``, ``ToolKind``,
``ToolMeta``) live in ``app.core.tools.metadata``.  Stori-specific
tool registrations, schemas, and derived sets live in
``app.daw.stori.tool_registry``.

This package re-exports both for convenience so existing callers
can continue to use ``from app.core.tools import …``.
"""
from __future__ import annotations

from app.core.tools.metadata import ToolTier, ToolKind, ToolMeta
from app.daw.stori.tool_registry import (
    build_tool_registry,
    get_tool_meta,
    tools_by_kind,
    tool_schema_by_name,
)
from app.daw.stori.tool_schemas import TIER1_TOOLS, TIER2_TOOLS, ALL_TOOLS
from app.daw.stori.tool_names import ToolName

__all__ = [
    "ToolTier",
    "ToolKind",
    "ToolMeta",
    "ToolName",
    "TIER1_TOOLS",
    "TIER2_TOOLS",
    "ALL_TOOLS",
    "build_tool_registry",
    "get_tool_meta",
    "tools_by_kind",
    "tool_schema_by_name",
]
