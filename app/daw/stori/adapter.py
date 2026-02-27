"""Stori DAW adapter — concrete ``DAWAdapter`` for the Stori macOS app.

This is the only file that assembles all Stori-specific pieces (tool
registry, phase mapping, validation) into a single ``DAWAdapter``
implementation.  DI/bootstrap code creates an instance and injects it
into the Maestro orchestrator.
"""
from __future__ import annotations

from app.core.tool_validation.models import ValidationResult
from app.daw.ports import DAWAdapter, ToolRegistry
from app.daw.stori.tool_registry import (
    MCP_TOOLS,
    SERVER_SIDE_TOOLS,
    DAW_TOOLS,
    TOOL_CATEGORIES,
    build_tool_registry,
)
from app.daw.stori.tool_schemas import ALL_TOOLS
from app.daw.stori.phase_map import phase_for_tool
from app.daw.stori.validation import validate_stori_tool_call


class StoriDAWAdapter:
    """``DAWAdapter`` implementation for the Stori macOS DAW.

    Assembles the Stori tool vocabulary, validation rules, and phase
    mapping into the adapter interface that Maestro core depends on.
    """

    def __init__(self) -> None:
        meta = build_tool_registry()
        self._registry = ToolRegistry(
            mcp_tools=list(MCP_TOOLS),
            tool_schemas=list(ALL_TOOLS),
            tool_meta=dict(meta),
            server_side_tools=frozenset(SERVER_SIDE_TOOLS),
            daw_tools=frozenset(DAW_TOOLS),
            categories=dict(TOOL_CATEGORIES),
        )

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def validate_tool_call(
        self,
        name: str,
        params: dict[str, object],
        allowed_tools: set[str],
    ) -> ValidationResult:
        from app.contracts.json_types import JSONValue
        typed_params: dict[str, JSONValue] = params  # type: ignore[assignment]
        return validate_stori_tool_call(name, typed_params, allowed_tools)

    def phase_for_tool(self, name: str) -> str:
        return phase_for_tool(name)


# Module-level singleton — created on first access.
_adapter: StoriDAWAdapter | None = None


def get_daw_adapter() -> StoriDAWAdapter:
    """Return the singleton ``StoriDAWAdapter`` instance."""
    global _adapter
    if _adapter is None:
        _adapter = StoriDAWAdapter()
    return _adapter


_proto_check: type = StoriDAWAdapter
