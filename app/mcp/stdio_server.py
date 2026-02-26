#!/usr/bin/env python3
"""
Stori MCP Stdio Server

Standalone MCP server that communicates via stdio.
This can be registered with Cursor or Claude Desktop.

When STORI_MAESTRO_MCP_URL and STORI_MCP_TOKEN are set, DAW tool calls are
proxied to the Maestro backend (where the Stori app WebSocket is registered).
That way Cursor and the Stori app share the same DAW connection.

Usage:
    python -m app.mcp.stdio_server
    # With proxy (Cursor → backend → Stori WebSocket):
    STORI_MAESTRO_MCP_URL=http://localhost:10001 STORI_MCP_TOKEN=<jwt> python -m app.mcp.stdio_server
"""
from __future__ import annotations

import sys
import json
import asyncio
import logging
from app.contracts.mcp_types import MCPContentBlock

import httpx

from app.mcp.server import ToolCallResult

# Setup logging to stderr (stdout is for MCP protocol).
# Cursor shows stderr as [error] in the MCP Output panel, so keep httpx at WARNING
# to avoid every proxy request appearing as a red "error" when it actually succeeded.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


class StdioMCPServer:
    """MCP server that communicates via stdin/stdout."""

    def __init__(self) -> None:
        from app.mcp.server import get_mcp_server
        from app.mcp.tools import SERVER_SIDE_TOOLS
        from app.config import settings
        self.mcp = get_mcp_server()
        self._request_id = 0
        self._server_side_tools = SERVER_SIDE_TOOLS
        self._maestro_url = (settings.maestro_mcp_url or "").rstrip("/")
        self._mcp_token = settings.mcp_token
        self._proxy_daw = bool(self._maestro_url and self._mcp_token)
        if self._proxy_daw:
            logger.info("DAW tool calls will be proxied to %s", self._maestro_url)
    
    async def run(self) -> None:
        """Main loop - read from stdin, write to stdout."""
        logger.info("Stori MCP Server starting...")
        
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(
            lambda: protocol, sys.stdin
        )
        
        while True:
            try:
                line = await reader.readline()
                if not line:
                    break
                
                raw = json.loads(line.decode())
                message: dict[str, object] = raw if isinstance(raw, dict) else {}
                response = await self.handle_message(message)
                
                if response:
                    self.send_response(response)
                    
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON: {e}")
            except Exception as e:
                logger.exception(f"Error handling message: {e}")
    
    def send_response(self, message: dict[str, object]) -> None:
        """Send a response via stdout."""
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    async def _proxy_daw_tool(self, tool_name: str, arguments: dict[str, object]) -> ToolCallResult:
        """Proxy a DAW tool call to the Maestro backend (which has the WebSocket)."""
        from app.mcp.server import ToolCallResult
        url = f"{self._maestro_url}/api/v1/mcp/tools/{tool_name}/call"
        payload: dict[str, object] = {"name": tool_name, "arguments": arguments}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._mcp_token}",
        }
        try:
            async with httpx.AsyncClient(timeout=35.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data: dict[str, object] = resp.json()
            raw_content = data.get("content")
            content: list[MCPContentBlock]
            if isinstance(raw_content, list):
                content = [
                    {"type": str(b.get("type", "text")), "text": str(b.get("text", ""))}
                    for b in raw_content
                    if isinstance(b, dict)
                ]
            else:
                content = [{"type": "text", "text": "No content"}]
            return ToolCallResult(
                success=bool(data.get("success", False)),
                content=content,
                is_error=bool(data.get("isError", False)),
            )
        except httpx.HTTPStatusError as e:
            body = e.response.text
            try:
                detail = e.response.json().get("detail", body)
            except Exception:
                detail = body
            logger.warning("Backend proxy failed %s: %s", e.response.status_code, detail)
            return ToolCallResult(
                success=False,
                content=[{"type": "text", "text": f"Backend error: {detail}"}],
                is_error=True,
            )
        except Exception as e:
            logger.exception("Backend proxy error: %s", e)
            return ToolCallResult(
                success=False,
                content=[{"type": "text", "text": f"Proxy error: {e!s}"}],
                is_error=True,
            )

    async def handle_message(self, message: dict[str, object]) -> dict[str, object] | None:
        """Handle an incoming MCP message."""
        method = str(message.get("method", ""))
        msg_id = message.get("id")
        raw_params = message.get("params")
        params: dict[str, object] = raw_params if isinstance(raw_params, dict) else {}
        
        logger.debug(f"Received: {method}")
        
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": self.mcp.get_server_info(),
                    "capabilities": {
                        "tools": {},
                    }
                }
            }
        
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": self.mcp.list_tools()
                }
            }
        
        elif method == "tools/call":
            tool_name = str(params.get("name", ""))
            raw_args = params.get("arguments")
            arguments: dict[str, object] = raw_args if isinstance(raw_args, dict) else {}

            # DAW tools: proxy to Maestro backend if configured (backend has the WebSocket)
            if (
                self._proxy_daw
                and tool_name not in self._server_side_tools
            ):
                result = await self._proxy_daw_tool(tool_name, arguments)
            else:
                result = await self.mcp.call_tool(tool_name, arguments)

            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": result.content,
                    "isError": result.is_error,
                }
            }
        
        elif method == "notifications/initialized":
            # Client is ready, no response needed
            logger.info("Client initialized")
            return None
        
        elif method == "ping":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {}
            }
        
        else:
            logger.warning(f"Unknown method: {method}")
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }


async def main() -> None:
    server = StdioMCPServer()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
