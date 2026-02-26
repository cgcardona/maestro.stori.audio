"""Typed structures for the MCP protocol layer.

Defines every entity used across tool definitions, the MCP server,
and the HTTP/WebSocket route layer.  No ``dict[str, object]`` is used
here — all shapes are named TypedDicts.

Organisation:
  Tool definitions    → ``MCPInputSchema``, ``MCPToolDef``, ``MCPContentBlock``
  Server capabilities → ``MCPToolsCapability``, ``MCPCapabilities``
  Server info         → ``MCPServerInfo``
  JSON-RPC messages   → ``MCPRequest``, ``MCPSuccessResponse``,
                        ``MCPErrorDetail``, ``MCPErrorResponse``, ``MCPResponse``
  DAW channel         → ``DAWToolCallMessage``, ``DAWToolResponse``
"""
from __future__ import annotations

from typing import Literal, Union

from typing_extensions import NotRequired, Required, TypedDict


# ── Tool schema shapes ────────────────────────────────────────────────────────


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


# ── Server capability shapes ──────────────────────────────────────────────────


class MCPToolsCapability(TypedDict, total=False):
    """The ``tools`` entry in ``MCPCapabilities``.

    Currently always ``{}`` — reserved for future tool metadata.
    """


class MCPResourcesCapability(TypedDict, total=False):
    """The ``resources`` entry in ``MCPCapabilities``.

    Currently always ``{}`` — reserved for future resource metadata.
    """


class MCPCapabilities(TypedDict, total=False):
    """MCP server capabilities advertised during the ``initialize`` handshake."""

    tools: MCPToolsCapability
    resources: MCPResourcesCapability


class MCPServerInfo(TypedDict):
    """MCP server info returned in ``initialize`` responses and ``get_server_info()``."""

    name: str
    version: str
    protocolVersion: str  # noqa: N815
    capabilities: MCPCapabilities


# ── JSON-RPC 2.0 message shapes ───────────────────────────────────────────────


class MCPRequest(TypedDict, total=False):
    """An incoming JSON-RPC 2.0 message from an MCP client.

    ``jsonrpc`` and ``method`` are always present.
    ``id`` is absent for notifications.
    ``params`` is absent when the method takes no parameters.
    """

    jsonrpc: Required[str]
    method: Required[str]
    id: str | int | None
    params: dict[str, object]


class MCPSuccessResponse(TypedDict):
    """A JSON-RPC 2.0 success response."""

    jsonrpc: str
    id: str | int | None
    result: dict[str, object]


class MCPErrorDetail(TypedDict, total=False):
    """The ``error`` object inside a JSON-RPC 2.0 error response."""

    code: Required[int]
    message: Required[str]
    data: object


class MCPErrorResponse(TypedDict):
    """A JSON-RPC 2.0 error response."""

    jsonrpc: str
    id: str | int | None
    error: MCPErrorDetail


MCPResponse = Union[MCPSuccessResponse, MCPErrorResponse]
"""Discriminated union of all JSON-RPC 2.0 response shapes."""


# ── DAW channel shapes ────────────────────────────────────────────────────────


class DAWToolCallMessage(TypedDict):
    """Message sent from the MCP server to the connected DAW over WebSocket.

    The DAW executes the tool and replies via ``receive_tool_response``.
    """

    type: Literal["toolCall"]
    requestId: str  # noqa: N815
    tool: str
    arguments: dict[str, object]


class DAWToolResponse(TypedDict, total=False):
    """Response sent from the DAW back to the MCP server after tool execution.

    ``success`` is always present; ``content`` and ``isError`` are optional.
    """

    success: Required[bool]
    content: list[MCPContentBlock]
    isError: bool  # noqa: N815
