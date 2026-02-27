"""MCP (Model Context Protocol) server for DAW control."""
from __future__ import annotations

from maestro.mcp.server import MaestroMCPServer
from maestro.mcp.tools import MCP_TOOLS

__all__ = ["MaestroMCPServer", "MCP_TOOLS"]
