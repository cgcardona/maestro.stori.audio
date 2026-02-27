"""DAW adapter protocol — the only DAW interface Maestro core may depend on.

Concrete adapters (e.g. ``app.daw.stori.adapter.StoriDAWAdapter``) implement
this protocol.  Maestro orchestration code imports ``DAWAdapter`` and
``ToolRegistry``; it never imports a concrete adapter directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.contracts.mcp_types import MCPToolDef
from app.contracts.llm_types import ToolSchemaDict
from app.core.tools.metadata import ToolMeta

ToolMetaRegistry = dict[str, ToolMeta]
"""Tool name → ``ToolMeta`` mapping used as the authoritative registry."""


@dataclass(frozen=True)
class ToolRegistry:
    """Immutable snapshot of every tool a DAW adapter exposes.

    Attributes:
        mcp_tools: MCP-format tool definitions (wire contract).
        tool_schemas: OpenAI function-calling format (sent to LLM).
        tool_meta: Per-tool metadata keyed by canonical name.
        server_side_tools: Names of tools that execute server-side.
        daw_tools: Names of tools forwarded to the DAW client.
        categories: Tool name → category string.
    """

    mcp_tools: list[MCPToolDef] = field(default_factory=list)
    tool_schemas: list[ToolSchemaDict] = field(default_factory=list)
    tool_meta: ToolMetaRegistry = field(default_factory=dict)
    server_side_tools: frozenset[str] = field(default_factory=frozenset)
    daw_tools: frozenset[str] = field(default_factory=frozenset)
    categories: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class DAWAdapter(Protocol):
    """Port that every DAW integration must satisfy.

    Maestro core calls these methods; the concrete adapter (e.g.
    ``StoriDAWAdapter``) wires them to DAW-specific vocabulary,
    validation rules, and transport.
    """

    @property
    def registry(self) -> ToolRegistry:
        """Return the full tool vocabulary for this DAW."""
        ...

    def validate_tool_call(
        self,
        name: str,
        params: dict[str, object],
        allowed_tools: set[str],
    ) -> object:
        """Validate a tool call against the DAW's schema and constraints.

        Returns a ``ValidationResult`` (or adapter-specific equivalent).
        """
        ...

    def phase_for_tool(self, name: str) -> str:
        """Classify a tool into an execution phase.

        Returns ``"setup"``, ``"instrument"``, or ``"mixing"``.
        """
        ...
