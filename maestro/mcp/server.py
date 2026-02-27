"""Maestro MCP Server — DAW control via Model Context Protocol.

Allows Claude, Cursor, and other MCP clients to control the
connected DAW (e.g. Stori) through the Maestro backend.
"""
from __future__ import annotations

import json
import logging
import asyncio
from typing import (
    TYPE_CHECKING,
    Awaitable,
    Callable,
)

if TYPE_CHECKING:
    from maestro.services.music_generator import MusicGenerator
from dataclasses import dataclass, field

from maestro.contracts.mcp_types import (
    DAWToolCallMessage,
    DAWToolResponse,
    MCPCapabilities,
    MCPContentBlock,
    MCPServerInfo,
    MCPToolDef,
)
from maestro.contracts.json_types import JSONValue, json_list, jint
from maestro.contracts.project_types import ProjectContext
from maestro.mcp.tools import MCP_TOOLS, SERVER_SIDE_TOOLS, TOOL_CATEGORIES
from maestro.core.tool_validation import validate_tool_call

logger = logging.getLogger(__name__)


@dataclass
class ToolCallResult:
    """Result of an MCP tool call."""
    success: bool
    content: list[MCPContentBlock]
    is_error: bool = False
    bad_request: bool = False


@dataclass
class DAWConnection:
    """Represents a connected DAW instance."""
    id: str
    send_callback: Callable[[DAWToolCallMessage], Awaitable[None]]
    project_state: ProjectContext | None = None
    pending_responses: dict[str, asyncio.Future[DAWToolResponse]] = field(default_factory=dict)


class MaestroMCPServer:
    """MCP Server for DAW control.

    Exposes DAW control tools via MCP protocol, forwards tool calls to
    connected DAW clients, executes generation tools server-side
    (calling Orpheus), and returns results back to MCP clients.
    """
    
    def __init__(self) -> None:
        from maestro.config import get_settings
        self.name = "stori-daw"
        self.version = get_settings().app_version
        # Lazy init on first generation call to avoid circular import when running stdio server standalone
        self._generator: MusicGenerator | None = None
        self._daw_connections: dict[str, DAWConnection] = {}
        self._active_connection: str | None = None

    @property
    def generator(self) -> MusicGenerator:
        if self._generator is None:
            from maestro.services.music_generator import get_music_generator
            self._generator = get_music_generator()
        return self._generator
    
    # =========================================================================
    # MCP Protocol Methods
    # =========================================================================
    
    def get_server_info(self) -> MCPServerInfo:
        """Return MCP server information."""
        capabilities: MCPCapabilities = {"tools": {}, "resources": {}}
        return MCPServerInfo(
            name=self.name,
            version=self.version,
            protocolVersion="2024-11-05",
            capabilities=capabilities,
        )
    
    def list_tools(self) -> list[MCPToolDef]:
        """list all available MCP tools."""
        return MCP_TOOLS
    
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, JSONValue],
    ) -> ToolCallResult:
        """
        Execute an MCP tool call.

        Validates allowlist, schema, and value ranges before execution.
        Routes to either server-side generation or DAW forwarding.
        """
        logger.info(f"MCP tool call: {name}")
        logger.debug(f"Arguments: {arguments}")

        allowed_tools: set[str] = {t["name"] for t in MCP_TOOLS}
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
        send_callback: Callable[[DAWToolCallMessage], Awaitable[None]],
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
        project_state: ProjectContext,
    ) -> None:
        """Update cached project state from DAW."""
        if connection_id in self._daw_connections:
            self._daw_connections[connection_id].project_state = project_state
    
    def receive_tool_response(
        self,
        connection_id: str,
        request_id: str,
        result: DAWToolResponse,
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
        arguments: dict[str, JSONValue],
    ) -> ToolCallResult:
        """Execute a music generation tool server-side."""
        raw_chords = arguments.get("chords")
        chords: list[str] | None = None
        if isinstance(raw_chords, list):
            chords = [str(c) for c in raw_chords]

        raw_tempo = arguments.get("tempo", 120)
        raw_bars = arguments.get("bars", 4)
        result = await self.generator.generate(
            instrument=str(arguments.get("role", "melody")),
            style=str(arguments.get("style", "boom_bap")),
            tempo=int(raw_tempo) if isinstance(raw_tempo, (int, float, str)) else 120,
            bars=int(raw_bars) if isinstance(raw_bars, (int, float, str)) else 4,
            key=str(arguments["key"]) if "key" in arguments else None,
            chords=chords,
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
        arguments: dict[str, JSONValue],
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
        response_future: asyncio.Future[DAWToolResponse] = asyncio.Future()
        conn.pending_responses[request_id] = response_future

        try:
            # Send tool call to DAW
            await conn.send_callback(DAWToolCallMessage(
                type="toolCall",
                requestId=request_id,
                tool=name,
                arguments=arguments,
            ))

            # Wait for response (with timeout)
            result = await asyncio.wait_for(response_future, timeout=30.0)
            succeeded = result["success"]

            return ToolCallResult(
                success=succeeded,
                content=[{"type": "text", "text": json.dumps(result, indent=2)}],
                is_error=not succeeded,
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
        state: ProjectContext,
        arguments: dict[str, JSONValue],
    ) -> ToolCallResult:
        """Format project state for MCP response."""

        include_notes = bool(arguments.get("include_notes", False))
        include_automation = bool(arguments.get("include_automation", False))

        tracks_out: list[dict[str, JSONValue]] = []
        for track in state.get("tracks", []):
            mixer = track.get("mixerSettings") or {}
            track_info: dict[str, JSONValue] = {
                "id": track.get("id"),
                "name": track.get("name"),
                "type": "drums" if track.get("drumKitId") else "instrument",
                "instrument": track.get("drumKitId") or f"GM {track.get('gmProgram', 0)}",
                "volume": mixer.get("volume", 0.8),
                "pan": mixer.get("pan", 0.5),
                "muted": mixer.get("isMuted", False),
                "regions": [],
            }

            regions_out: list[dict[str, JSONValue]] = []
            for region in track.get("regions", []):
                region_info: dict[str, JSONValue] = {
                    "id": region.get("id"),
                    "name": region.get("name"),
                    "startBeat": region.get("startBeat", 0),
                    "durationBeats": region.get("durationBeats", 0),
                    "noteCount": len(region.get("notes", [])),
                }
                if include_notes:
                    region_info["notes"] = json_list(region.get("notes", []))
                regions_out.append(region_info)

            regions_json: list[JSONValue] = json_list(regions_out)
            track_info["regions"] = regions_json
            if include_automation:
                track_info["automation"] = [
                    {
                        "id": lane.get("id", ""),
                        "parameter": lane.get("parameter", ""),
                        "points": [
                            {"beat": pt.get("beat", 0.0), "value": pt.get("value", 0.0)}
                            for pt in lane.get("points", [])
                        ],
                    }
                    for lane in track.get("automationLanes", [])
                ]
            tracks_out.append(track_info)

        _ts_raw = state.get("timeSignature")
        if isinstance(_ts_raw, dict):
            _ts_num = jint(_ts_raw.get("numerator", 4))
            _ts_den = jint(_ts_raw.get("denominator", 4))
        elif isinstance(_ts_raw, str):
            _parts = _ts_raw.split("/")
            _ts_num = int(_parts[0]) if _parts[0].isdigit() else 4
            _ts_den = int(_parts[1]) if len(_parts) > 1 and _parts[1].isdigit() else 4
        else:
            _ts_num, _ts_den = 4, 4
        time_sig: JSONValue = {"numerator": _ts_num, "denominator": _ts_den}
        tracks_json: list[JSONValue] = list(tracks_out)
        summary: dict[str, JSONValue] = {
            "name": state.get("name", "Untitled"),
            "tempo": state.get("tempo", 120),
            "key": state.get("key", "C"),
            "timeSignature": time_sig,
            "trackCount": len(state.get("tracks", [])),
            "tracks": tracks_json,
        }

        return ToolCallResult(
            success=True,
            content=[{"type": "text", "text": json.dumps(summary, indent=2)}],
        )
    
    # =========================================================================
    # Cleanup
    # =========================================================================
    
    async def close(self) -> None:
        """Clean up resources."""
        pass


# Singleton instance
_server: MaestroMCPServer | None = None


def get_mcp_server() -> MaestroMCPServer:
    """Get the singleton MCP server instance."""
    global _server
    if _server is None:
        _server = MaestroMCPServer()
    return _server
