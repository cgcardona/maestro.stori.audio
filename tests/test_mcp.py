"""Tests for MCP server functionality."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from httpx import AsyncClient

import pytest
import pytest_asyncio
from app.main import app
from app.auth.dependencies import require_valid_token
from app.mcp.server import MaestroMCPServer, ToolCallResult
from app.mcp.tools import MCP_TOOLS, SERVER_SIDE_TOOLS, DAW_TOOLS, TOOL_CATEGORIES


class TestMCPTools:
    """Tests for MCP tool definitions."""

    def test_all_tools_have_required_fields(self) -> None:

        """Verify all tools have name, description, and inputSchema."""
        for tool in MCP_TOOLS:
            assert "name" in tool, f"Tool missing name: {tool}"
            assert "description" in tool, f"Tool {tool['name']} missing description"
            assert "inputSchema" in tool, f"Tool {tool['name']} missing inputSchema"

    def test_tool_names_are_prefixed(self) -> None:

        """All MCP tools should be prefixed with 'stori_'."""
        for tool in MCP_TOOLS:
            assert tool["name"].startswith("stori_"), f"Tool {tool['name']} should start with 'stori_'"

    def test_tool_categories_complete(self) -> None:

        """Every tool should be in a category."""
        for tool in MCP_TOOLS:
            assert tool["name"] in TOOL_CATEGORIES, f"Tool {tool['name']} not in TOOL_CATEGORIES"
    
    def test_server_side_and_daw_tools_disjoint(self) -> None:

        """Server-side and DAW tools should not overlap."""
        overlap = SERVER_SIDE_TOOLS.intersection(DAW_TOOLS)
        assert len(overlap) == 0, f"Overlapping tools: {overlap}"
    
    def test_generation_tools_are_server_side(self) -> None:

        """Generation tools should be marked as server-side."""
        assert "stori_generate_midi" in SERVER_SIDE_TOOLS


class TestMCPServer:
    """Tests for MCP server."""
    
    def test_get_server_info(self) -> None:

        """Test server info response."""
        server = MaestroMCPServer()
        info = server.get_server_info()
        
        assert info["name"] == "stori-daw"
        assert "version" in info
        assert "protocolVersion" in info
        assert "capabilities" in info
    
    def test_list_tools(self) -> None:

        """Test listing tools."""
        server = MaestroMCPServer()
        tools = server.list_tools()
        
        assert len(tools) > 0
        assert len(tools) == len(MCP_TOOLS)
    
    @pytest.mark.anyio
    async def test_call_unknown_tool(self) -> None:

        """Test calling an unknown tool."""
        server = MaestroMCPServer()
        result = await server.call_tool("unknown_tool", {})
        
        assert result.is_error
        assert not result.success
    
    @pytest.mark.anyio
    async def test_call_daw_tool_without_connection(self) -> None:

        """Test calling DAW tool without connected DAW."""
        server = MaestroMCPServer()
        result = await server.call_tool("stori_add_midi_track", {"name": "Test"})
        
        assert result.is_error
        assert "No DAW connected" in result.content[0]["text"]


class TestToolCallResult:
    """Tests for ToolCallResult."""
    
    def test_success_result(self) -> None:

        """Test successful result."""
        result = ToolCallResult(
            success=True,
            content=[{"type": "text", "text": "Success"}]
        )
        assert result.success
        assert not result.is_error
    
    def test_error_result(self) -> None:

        """Test error result."""
        result = ToolCallResult(
            success=False,
            content=[{"type": "text", "text": "Error"}],
            is_error=True
        )
        assert not result.success
        assert result.is_error


class TestMCPEndpoints:
    """Tests for MCP HTTP endpoints (require auth via dependency override)."""

    @pytest_asyncio.fixture
    async def mcp_client(self, client: AsyncClient) -> AsyncGenerator[AsyncClient, None]:

        """Client with auth overridden so MCP endpoints accept requests."""
        async def override_require_valid_token() -> dict[str, str]:
            return {"sub": "test-mcp-user"}

        app.dependency_overrides[require_valid_token] = override_require_valid_token
        try:
            yield client
        finally:
            app.dependency_overrides.pop(require_valid_token, None)

    @pytest.mark.anyio
    async def test_list_tools_endpoint(self, mcp_client: AsyncClient) -> None:

        """Test /mcp/tools endpoint."""
        response = await mcp_client.get("/api/v1/mcp/tools")
        assert response.status_code == 200

        data = response.json()
        assert "tools" in data
        assert len(data["tools"]) > 0

    @pytest.mark.anyio
    async def test_server_info_endpoint(self, mcp_client: AsyncClient) -> None:

        """Test /mcp/info endpoint."""
        response = await mcp_client.get("/api/v1/mcp/info")
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "stori-daw"

    @pytest.mark.anyio
    async def test_get_specific_tool(self, mcp_client: AsyncClient) -> None:

        """Test getting a specific tool."""
        response = await mcp_client.get("/api/v1/mcp/tools/stori_add_midi_track")
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "stori_add_midi_track"

    @pytest.mark.anyio
    async def test_get_unknown_tool(self, mcp_client: AsyncClient) -> None:

        """Test getting an unknown tool returns 404."""
        response = await mcp_client.get("/api/v1/mcp/tools/unknown_tool")
        assert response.status_code == 404

        data = response.json()
        assert "detail" in data

    @pytest.mark.anyio
    async def test_call_tool_endpoint(self, mcp_client: AsyncClient) -> None:

        """POST /tools/{tool_name}/call invokes tool and returns success/content."""
        # MCPToolCallRequest requires "name" and "arguments"
        response = await mcp_client.post(
            "/api/v1/mcp/tools/stori_add_midi_track/call",
            json={"name": "stori_add_midi_track", "arguments": {"name": "Test Track"}},
        )
        # DAW not connected so we expect error response but endpoint returns 200 with isError/content
        assert response.status_code == 200
        data = response.json()
        assert "success" in data
        assert "content" in data
        # Without DAW connection, call_tool returns error
        assert data.get("isError") is True or data.get("success") is False
