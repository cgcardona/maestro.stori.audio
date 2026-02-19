"""
MCP HTTP Endpoints

HTTP-based MCP protocol endpoints for web clients.
For stdio-based clients (Cursor, Claude Desktop), use the standalone MCP server.
"""
import json
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.models.base import CamelModel

from app.mcp.server import get_mcp_server, StoriMCPServer
from app.auth.dependencies import require_valid_token
from app.auth.tokens import validate_access_code, AccessCodeError

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
    arguments: dict[str, Any] = {}


class MCPToolCallResponse(CamelModel):
    """Response from MCP tool call."""
    success: bool
    content: list[dict[str, Any]]
    isError: bool = False


# =============================================================================
# HTTP Endpoints for MCP
# =============================================================================

@router.get("/tools")
async def list_tools(
    token_claims: dict = Depends(require_valid_token),
):
    """List all available MCP tools. Requires authentication."""
    server = get_mcp_server()
    return {
        "tools": server.list_tools()
    }


@router.get("/tools/{tool_name}")
async def get_tool(
    tool_name: str,
    token_claims: dict = Depends(require_valid_token),
):
    """Get details about a specific tool. Requires authentication."""
    server = get_mcp_server()
    for tool in server.list_tools():
        if tool["name"] == tool_name:
            return tool
    return {"error": f"Tool not found: {tool_name}"}


@router.post("/tools/{tool_name}/call")
async def call_tool(
    tool_name: str,
    request: MCPToolCallRequest,
    token_claims: dict = Depends(require_valid_token),
):
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
    token_claims: dict = Depends(require_valid_token),
):
    """Get MCP server information. Requires authentication."""
    server = get_mcp_server()
    return server.get_server_info()


# =============================================================================
# WebSocket for DAW Connection
# =============================================================================

@router.websocket("/daw")
async def daw_websocket(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
):
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

    async def send_to_daw(message: dict[str, Any]):
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
    token_claims: dict = Depends(require_valid_token),
):
    """
    Obtain a server-issued connection ID for the SSE flow.
    Use this ID in GET /stream/{connection_id} and POST /response/{connection_id}.
    ID is valid for 5 minutes. Requires authentication.
    """
    connection_id = _issue_connection_id()
    return {"connectionId": connection_id}


@router.get("/stream/{connection_id}")
async def tool_stream(
    connection_id: str,
    token_claims: dict = Depends(require_valid_token),
):
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
    from fastapi.responses import StreamingResponse
    import asyncio

    async def event_generator():
        server = get_mcp_server()
        queue: asyncio.Queue = asyncio.Queue()
        
        async def send_to_queue(message: dict[str, Any]):
            await queue.put(message)
        
        server.register_daw(connection_id, send_to_queue)
        
        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(message)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
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
    data: dict[str, Any],
    token_claims: dict = Depends(require_valid_token),
):
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
    request_id = data.get("requestId")
    result = data.get("result", {})
    server.receive_tool_response(
        connection_id,
        str(request_id) if request_id is not None else "",
        result if isinstance(result, dict) else {},
    )
    return {"status": "ok"}
