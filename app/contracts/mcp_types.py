"""Typed structures for the MCP protocol layer.

Defines the entities used across tool definitions, the MCP server,
and the HTTP/WebSocket route layer.
"""
from __future__ import annotations

from typing_extensions import Required, TypedDict


class MCPInputSchema(TypedDict, total=False):
    """JSON Schema describing an MCP tool's accepted arguments."""

    type: Required[str]
    properties: Required[dict[str, dict[str, object]]]
    required: list[str]


class MCPToolDef(TypedDict, total=False):
    """Definition of a single MCP tool exposed to LLM clients."""

    name: Required[str]
    description: Required[str]
    inputSchema: Required[MCPInputSchema]
    server_side: bool


class MCPContentBlock(TypedDict):
    """A content block in an MCP tool result (currently always text)."""

    type: str
    text: str
