"""
MCP HTTP Endpoints

HTTP-based MCP protocol endpoints for web clients.
For stdio-based clients (Cursor, Claude Desktop), use the standalone MCP server.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Depends, Query
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import Field

from app.models.base import CamelModel

from app.contracts.mcp_types import MCPContentBlock, MCPToolDef
from app.mcp.server import get_mcp_server, DAWMessage, ServerInfoDict, StoriMCPServer
from app.auth.dependencies import require_valid_token
from app.auth.tokens import validate_access_code, AccessCodeError
from app.protocol.emitter import ProtocolSerializationError, emit
from app.protocol.events import ErrorEvent, MCPMessageEvent, MCPPingEvent
from app.protocol.validation import ProtocolGuard

router = APIRouter()
logger = logging.getLogger(__name__)

# Optional auth for MCP endpoints (for backwards compatibility during transition)
security = HTTPBearer(auto_error=False)

# Server-issued connection IDs for SSE flow (R6): client must obtain ID from POST /mcp/connection first.
# TTL 5 minutes; expired entries pruned on access.
_ISSUED_CONNECTION_IDS: dict[str, float] = {}
_CONNECTION_ID_TTL_SECONDS = 300


def _issue_connection_id() -> str:
    """Return a new server-issued connection ID (UUID)."""
    cid = str(uuid.uuid4())
    _ISSUED_CONNECTION_IDS[cid] = time.monotonic() + _CONNECTION_ID_TTL_SECONDS
    return cid


def _is_valid_connection_id(connection_id: str) -> bool:
    """Return True if connection_id was issued by us and not expired. Prunes expired entries."""
    now = time.monotonic()
    expired = [k for k, v in _ISSUED_CONNECTION_IDS.items() if v <= now]
    for k in expired:
        del _ISSUED_CONNECTION_IDS[k]
    return connection_id in _ISSUED_CONNECTION_IDS


class MCPToolCallRequest(CamelModel):
    """Request to call an MCP tool."""
    name: str
    arguments: dict[str, object] = {}


class MCPToolCallResponse(CamelModel):
    """Response from MCP tool call."""
    success: bool
    content: list[MCPContentBlock]
    isError: bool = False


class ToolResponseBody(CamelModel):
    """Body for DAW tool-response POST."""
    request_id: str
    result: dict[str, object] = {}


# =============================================================================
# HTTP Endpoints for MCP
# =============================================================================

class MCPToolListResponse(CamelModel):
    """Response containing the list of available MCP tools."""
    tools: list[MCPToolDef]


class ConnectionCreatedResponse(CamelModel):
    """Server-issued connection ID for the MCP SSE flow.

    Returned by ``POST /mcp/connection``.  The caller must include this ID in
    subsequent ``GET /mcp/stream/{connection_id}`` (to receive tool calls) and
    ``POST /mcp/response/{connection_id}`` (to post results) requests.  IDs
    are valid for 5 minutes and are validated on every use.

    Wire format: camelCase (via ``CamelModel``) — serialised as
    ``{"connectionId": "…"}``.

    Attributes:
        connection_id: UUID issued by the server.  Opaque to the client;
            must be treated as a single-session token and not persisted or
            shared across sessions.
    """

    connection_id: str = Field(
        description=(
            "Server-issued UUID for this SSE session. "
            "Valid for 5 minutes. Use in GET /mcp/stream/{id} and POST /mcp/response/{id}."
        )
    )


class ToolResponseReceivedResponse(CamelModel):
    """Acknowledgement that the DAW's tool-execution result was received.

    Returned by ``POST /mcp/response/{connection_id}`` after the server has
    handed the result off to the waiting MCP coroutine.  A ``200 OK`` with
    this body means the result was queued; it does not guarantee the tool call
    itself succeeded (that is reported via the SSE stream).

    Wire format: camelCase (via ``CamelModel``) — serialised as
    ``{"status": "ok"}``.

    Attributes:
        status: Always ``"ok"`` on success.  The endpoint raises ``404`` for
            unknown or expired connection IDs rather than returning a non-ok
            status here.
    """

    status: str = Field(
        description=(
            "Always 'ok' on success. "
            "The endpoint raises 404 for unknown / expired connection IDs."
        )
    )


@router.get("/tools")
async def list_tools(
    _auth: object = Depends(require_valid_token),
) -> MCPToolListResponse:
    """list all available MCP tools. Requires authentication."""
    server = get_mcp_server()
    return MCPToolListResponse(tools=server.list_tools())


@router.get("/tools/{tool_name}")
async def get_tool(
    tool_name: str,
    _auth: object = Depends(require_valid_token),
) -> MCPToolDef:
    """Get details about a specific tool. Requires authentication."""
    server = get_mcp_server()
    for tool in server.list_tools():
        if tool["name"] == tool_name:
            return tool
    raise HTTPException(status_code=404, detail=f"Tool not found: {tool_name}")


@router.post("/tools/{tool_name}/call")
async def call_tool(
    tool_name: str,
    request: MCPToolCallRequest,
    _auth: object = Depends(require_valid_token),
) -> MCPToolCallResponse:
    """Call an MCP tool. Requires authentication. Returns 400 on validation failure."""
    server = get_mcp_server()
    result = await server.call_tool(tool_name, request.arguments)
    if result.bad_request:
        detail = result.content[0].get("text", "Invalid tool or arguments") if result.content else "Invalid tool or arguments"
        raise HTTPException(status_code=400, detail=detail)
    return MCPToolCallResponse(
        success=result.success,
        content=result.content,
        isError=result.is_error,
    )


@router.get("/info")
async def server_info(
    _auth: object = Depends(require_valid_token),
) -> ServerInfoDict:
    """Get MCP server information. Requires authentication."""
    server = get_mcp_server()
    return server.get_server_info()


# =============================================================================
# WebSocket for DAW Connection
# =============================================================================

@router.websocket("/daw")
async def daw_websocket(
    websocket: WebSocket,
    token: str | None = Query(None),
) -> None:
    """
    WebSocket endpoint for DAW connection.

    Auth: token query param only (no Authorization header). Validate before upgrade;
    if missing/invalid we close with code 4001 and do not accept. After accept we
    keep the connection open; the Stori app waits for at least one message to mark "connected".
    """
    # Validate JWT before accepting: no upgrade if invalid (client sees close, not 101)
    if not token:
        logger.warning("WebSocket connection attempt without token")
        await websocket.close(code=4001)
        return
    try:
        claims = validate_access_code(token)
        logger.debug(f"WebSocket authenticated, user: {claims.get('sub', 'unknown')}")
    except AccessCodeError as e:
        logger.warning(f"WebSocket auth failed: {e}")
        await websocket.close(code=4001)
        return

    await websocket.accept()

    connection_id = str(id(websocket))
    server = get_mcp_server()

    async def send_to_daw(message: DAWMessage) -> None:
        await websocket.send_json(message)

    server.register_daw(connection_id, send_to_daw)
    logger.info(f"DAW connected: {connection_id}")

    # Send welcome so the Stori app receives at least one message and can mark "connected"
    try:
        await websocket.send_json({"type": "connected", "connectionId": connection_id})
    except Exception as e:
        logger.warning(f"Failed to send welcome: {e}")

    try:
        while True:
            msg = await websocket.receive()
            raw = msg.get("text") or (msg.get("bytes") or b"").decode("utf-8", errors="replace")
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                logger.debug(f"Ignoring non-JSON message: {e}")
                continue
            message_type = data.get("type")

            if message_type == "projectState":
                server.update_project_state(connection_id, data.get("state", {}))
                logger.debug("Project state updated")
            elif message_type == "toolResponse":
                request_id = data.get("requestId")
                result = data.get("result", {})
                server.receive_tool_response(
                    connection_id,
                    str(request_id) if request_id is not None else "",
                    result if isinstance(result, dict) else {},
                )
                logger.debug(f"Tool response received: {request_id}")
            elif message_type == "ping":
                await websocket.send_json({"type": "pong"})
            else:
                logger.warning(f"Unknown message type: {message_type}")
    except WebSocketDisconnect:
        logger.info(f"DAW disconnected: {connection_id}")
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
    finally:
        server.unregister_daw(connection_id)


# =============================================================================
# SSE for Tool Streaming (Alternative to WebSocket)
# =============================================================================

@router.post("/connection")
async def create_connection(
    _auth: object = Depends(require_valid_token),
) -> ConnectionCreatedResponse:
    """
    Obtain a server-issued connection ID for the SSE flow.
    Use this ID in GET /stream/{connection_id} and POST /response/{connection_id}.
    ID is valid for 5 minutes. Requires authentication.
    """
    connection_id = _issue_connection_id()
    return ConnectionCreatedResponse(connection_id=connection_id)


@router.get("/stream/{connection_id}")
async def tool_stream(
    connection_id: str,
    _auth: object = Depends(require_valid_token),
) -> StreamingResponse:
    """
    SSE endpoint for receiving tool calls. Requires authentication.
    connection_id must have been obtained from POST /mcp/connection first.
    Alternative to WebSocket for environments that don't support it.
    """
    if not _is_valid_connection_id(connection_id):
        raise HTTPException(
            status_code=404,
            detail="Invalid or expired connection_id. Obtain one from POST /api/v1/mcp/connection first.",
        )
    async def event_generator() -> AsyncIterator[str]:
        server = get_mcp_server()
        queue: asyncio.Queue[DAWMessage] = asyncio.Queue()
        guard = ProtocolGuard()

        async def send_to_queue(message: DAWMessage) -> None:
            await queue.put(message)

        server.register_daw(connection_id, send_to_queue)

        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    try:
                        yield emit(MCPMessageEvent(payload=message))
                    except ProtocolSerializationError as exc:
                        logger.error(f"❌ MCP stream protocol error: {exc}")
                        yield emit(ErrorEvent(message="Protocol serialization failure"))
                        return
                except asyncio.TimeoutError:
                    yield emit(MCPPingEvent())
        finally:
            server.unregister_daw(connection_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@router.post("/response/{connection_id}")
async def post_tool_response(
    connection_id: str,
    body: ToolResponseBody,
    _auth: object = Depends(require_valid_token),
) -> ToolResponseReceivedResponse:
    """
    Endpoint for DAW to post tool execution results. Requires authentication.
    connection_id must have been obtained from POST /mcp/connection.
    """
    if not _is_valid_connection_id(connection_id):
        raise HTTPException(
            status_code=404,
            detail="Invalid or expired connection_id. Obtain one from POST /api/v1/mcp/connection first.",
        )
    server = get_mcp_server()
    server.receive_tool_response(
        connection_id,
        body.request_id,
        dict(body.result),
    )
    return ToolResponseReceivedResponse(status="ok")
