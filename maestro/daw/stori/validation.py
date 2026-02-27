"""Stori DAW tool validation — delegates to the generic validation engine.

This module provides a Stori-specific entry point that wires the
generic ``app.core.tool_validation`` engine with the Stori tool
vocabulary (MCP definitions and metadata).
"""
from __future__ import annotations

from maestro.contracts.json_types import JSONValue
from maestro.core.tool_validation.models import ValidationResult
from maestro.core.tool_validation.validators import (
    validate_tool_call,
    validate_tool_call_simple,
    validate_tool_calls_batch,
    all_valid,
    collect_errors,
)
from maestro.daw.stori.tool_registry import MCP_TOOLS

_STORI_TOOL_NAMES: frozenset[str] = frozenset(t["name"] for t in MCP_TOOLS)


def validate_stori_tool_call(
    name: str,
    params: dict[str, JSONValue],
    allowed_tools: set[str] | None = None,
) -> ValidationResult:
    """Validate a Stori DAW tool call.

    If ``allowed_tools`` is ``None``, the full Stori tool vocabulary is used.
    """
    tools = allowed_tools if allowed_tools is not None else set(_STORI_TOOL_NAMES)
    return validate_tool_call(name, params, tools, registry=None)


__all__ = [
    "validate_stori_tool_call",
    "validate_tool_call",
    "validate_tool_call_simple",
    "validate_tool_calls_batch",
    "all_valid",
    "collect_errors",
    "ValidationResult",
]
