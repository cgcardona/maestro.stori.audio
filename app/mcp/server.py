"""
Stori MCP Server

Model Context Protocol server for DAW control.
Allows Claude, Cursor, and other MCP clients to control Stori DAW.
"""
import json
import logging
import asyncio
from typing import Any, Optional, Callable, Awaitable, cast
from dataclasses import dataclass, field

from app.mcp.tools import MCP_TOOLS, SERVER_SIDE_TOOLS, TOOL_CATEGORIES
from app.core.tool_validation import validate_tool_call

logger = logging.getLogger(__name__)


@dataclass
class ToolCallResult:
    """Result of an MCP tool call."""
    success: bool
    content: list[dict[str, Any]]
    is_error: bool = False
    bad_request: bool = False  # True when validation failed (caller may return HTTP 400)


@dataclass
class DAWConnection:
    """Represents a connected DAW instance."""
    id: str
    send_callback: Callable[[dict[str, Any]], Awaitable[None]]
    project_state: Optional[dict[str, Any]] = None
    pending_responses: dict[str, asyncio.Future] = field(default_factory=dict)


class StoriMCPServer:
    """
    MCP Server for Stori DAW control.
    
    This server:
    1. Exposes DAW control tools via MCP protocol
    2. Forwards tool calls to connected Swift DAW clients
    3. Executes generation tools server-side (calling Orpheus)
    4. Returns results back to the MCP client (Claude, etc.)
    """
    
    def __init__(self):
        from app.config import get_settings
        self.name = "stori-daw"
        self.version = get_settings().app_version
        # Lazy init on first generation call to avoid circular import when running stdio server standalone
        self._generator = None
        self._daw_connections: dict[str, DAWConnection] = {}
        self._active_connection: Optional[str] = None

    @property
    def generator(self):
        if self._generator is None:
            from app.services.music_generator import get_music_generator
            self._generator = get_music_generator()
        return self._generator
    
    # =========================================================================
    # MCP Protocol Methods
    # =========================================================================
    
    def get_server_info(self) -> dict[str, Any]:
        """Return MCP server information."""
        return {
            "name": self.name,
            "version": self.version,
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "resources": {},
            }
        }
    
    def list_tools(self) -> list[dict[str, Any]]:
        """List all available MCP tools."""
        return MCP_TOOLS
    
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> ToolCallResult:
        """
        Execute an MCP tool call.

        Validates allowlist, schema, and value ranges before execution.
        Routes to either server-side generation or DAW forwarding.
        """
        logger.info(f"MCP tool call: {name}")
        logger.debug(f"Arguments: {arguments}")

        allowed_tools = cast(set[str], {t["name"] for t in MCP_TOOLS})
        validation = validate_tool_call(name, arguments, allowed_tools, registry=None)
        if not validation.valid:
            logger.warning(f"MCP tool validation failed: {validation.error_message}")
            return ToolCallResult(
                success=False,
                content=[{"type": "text", "text": validation.error_message}],
                is_error=True,
                bad_request=True,
            )
        params = validation.resolved_params

        try:
            if name in SERVER_SIDE_TOOLS:
                return await self._execute_generation_tool(name, params)
            else:
                return await self._forward_to_daw(name, params)
        except Exception as e:
            logger.exception(f"Tool call failed: {name}")
            return ToolCallResult(
                success=False,
                content=[{"type": "text", "text": f"Error: {str(e)}"}],
                is_error=True,
            )
    
    # =========================================================================
    # DAW Connection Management
    # =========================================================================
    
    def register_daw(
        self,
        connection_id: str,
        send_callback: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Register a DAW connection."""
        self._daw_connections[connection_id] = DAWConnection(
            id=connection_id,
            send_callback=send_callback,
        )
        self._active_connection = connection_id
        logger.info(f"DAW registered: {connection_id}")
    
    def unregister_daw(self, connection_id: str) -> None:
        """Unregister a DAW connection."""
        if connection_id in self._daw_connections:
            del self._daw_connections[connection_id]
            if self._active_connection == connection_id:
                self._active_connection = next(iter(self._daw_connections), None)
            logger.info(f"DAW unregistered: {connection_id}")
    
    def update_project_state(
        self,
        connection_id: str,
        project_state: dict[str, Any],
    ) -> None:
        """Update cached project state from DAW."""
        if connection_id in self._daw_connections:
            self._daw_connections[connection_id].project_state = project_state
    
    def receive_tool_response(
        self,
        connection_id: str,
        request_id: str,
        result: dict[str, Any],
    ) -> None:
        """Receive a tool execution response from DAW."""
        if connection_id in self._daw_connections:
            conn = self._daw_connections[connection_id]
            if request_id in conn.pending_responses:
                conn.pending_responses[request_id].set_result(result)
    
    # =========================================================================
    # Tool Execution
    # =========================================================================
    
    async def _execute_generation_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> ToolCallResult:
        """Execute a music generation tool server-side."""
        
        # Map tool names to instrument types
        instrument_map = {
            "stori_generate_drums": "drums",
            "stori_generate_bass": "bass",
            "stori_generate_melody": "lead",
            "stori_generate_chords": "piano",
        }
        
        instrument = instrument_map.get(name)
        if not instrument:
            return ToolCallResult(
                success=False,
                content=[{"type": "text", "text": f"Unknown generation tool: {name}"}],
                is_error=True,
            )
        
        # Use the pluggable music generator with fallbacks
        result = await self.generator.generate(
            instrument=instrument,
            style=arguments.get("style", "boom_bap"),
            tempo=arguments.get("tempo", 90),
            bars=arguments.get("bars", 4),
            key=arguments.get("key"),
            chords=arguments.get("chords"),
            complexity=arguments.get("complexity", 0.5),
        )
        
        if result.success:
            return ToolCallResult(
                success=True,
                content=[{
                    "type": "text",
                    "text": json.dumps({
                        "generated": True,
                        "noteCount": len(result.notes),
                        "notes": result.notes,
                        "backend": result.backend_used.value,
                        "metadata": result.metadata,
                    }, indent=2)
                }],
            )
        else:
            return ToolCallResult(
                success=False,
                content=[{
                    "type": "text",
                    "text": f"Music generation failed: {result.error}. "
                           f"You can manually create notes using stori_add_notes."
                }],
                is_error=True,
            )
    
    async def _forward_to_daw(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> ToolCallResult:
        """Forward a tool call to the connected DAW."""
        
        if not self._active_connection:
            return ToolCallResult(
                success=False,
                content=[{"type": "text", "text": "No DAW connected. Please open Stori and connect."}],
                is_error=True,
            )
        
        conn = self._daw_connections[self._active_connection]
        
        # Special case: read project uses cached state if available
        if name == "stori_read_project" and conn.project_state:
            return self._format_project_state(conn.project_state, arguments)
        
        # Create request ID and future for response
        request_id = f"{name}_{id(arguments)}"
        response_future: asyncio.Future = asyncio.Future()
        conn.pending_responses[request_id] = response_future
        
        try:
            # Send tool call to DAW
            await conn.send_callback({
                "type": "toolCall",
                "requestId": request_id,
                "tool": name,
                "arguments": arguments,
            })
            
            # Wait for response (with timeout)
            result = await asyncio.wait_for(response_future, timeout=30.0)
            
            return ToolCallResult(
                success=result.get("success", False),
                content=[{"type": "text", "text": json.dumps(result, indent=2)}],
                is_error=not result.get("success", False),
            )
            
        except asyncio.TimeoutError:
            return ToolCallResult(
                success=False,
                content=[{"type": "text", "text": "DAW did not respond in time."}],
                is_error=True,
            )
        finally:
            conn.pending_responses.pop(request_id, None)
    
    def _format_project_state(
        self,
        state: dict[str, Any],
        arguments: dict[str, Any],
    ) -> ToolCallResult:
        """Format project state for MCP response."""
        
        include_notes = arguments.get("include_notes", False)
        include_automation = arguments.get("include_automation", False)
        
        # Build summary
        summary = {
            "name": state.get("name", "Untitled"),
            "tempo": state.get("tempo", 120),
            "key": state.get("key", "C"),
            "timeSignature": state.get("timeSignature", {"numerator": 4, "denominator": 4}),
            "trackCount": len(state.get("tracks", [])),
            "tracks": [],
        }
        
        for track in state.get("tracks", []):
            track_info = {
                "id": track.get("id"),
                "name": track.get("name"),
                "type": "drums" if track.get("drumKitId") else "instrument",
                "instrument": track.get("drumKitId") or f"GM {track.get('gmProgram', 0)}",
                "volume": track.get("mixerSettings", {}).get("volume", 0.8),
                "pan": track.get("mixerSettings", {}).get("pan", 0.5),
                "muted": track.get("mixerSettings", {}).get("isMuted", False),
                "regions": [],
            }
            
            for region in track.get("regions", []):
                region_info = {
                    "id": region.get("id"),
                    "name": region.get("name"),
                    "startBeat": region.get("startBeat", 0),
                    "durationBeats": region.get("durationBeats", 0),
                    "noteCount": len(region.get("notes", [])),
                }
                
                if include_notes:
                    region_info["notes"] = region.get("notes", [])
                
                track_info["regions"].append(region_info)
            
            if include_automation:
                track_info["automation"] = track.get("automationLanes", [])
            
            summary["tracks"].append(track_info)
        
        return ToolCallResult(
            success=True,
            content=[{"type": "text", "text": json.dumps(summary, indent=2)}],
        )
    
    # =========================================================================
    # Cleanup
    # =========================================================================
    
    async def close(self):
        """Clean up resources."""
        # Generator manages its own cleanup
        pass


# Singleton instance
_server: Optional[StoriMCPServer] = None


def get_mcp_server() -> StoriMCPServer:
    """Get the singleton MCP server instance."""
    global _server
    if _server is None:
        _server = StoriMCPServer()
    return _server
