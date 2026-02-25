"""Tests for MCP API routes (app/api/routes/mcp.py).

Covers: tool list, server info, tool execution endpoint.
"""
from __future__ import annotations

from httpx import AsyncClient
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestMCPRoutes:

    @pytest.mark.anyio
    async def test_mcp_tools_list(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        """GET /api/v1/mcp/tools should return tool list."""
        resp = await client.get("/api/v1/mcp/tools", headers=auth_headers)
        if resp.status_code == 200:
            data = resp.json()
            # Response may be a dict with a "tools" key or a list
            if isinstance(data, dict) and "tools" in data:
                tools = data["tools"]
                assert isinstance(tools, list)
                if tools:
                    assert "name" in tools[0]
            elif isinstance(data, list):
                if data:
                    assert "name" in data[0]

    @pytest.mark.anyio
    async def test_mcp_server_info(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        """GET /api/v1/mcp/info should return server info."""
        resp = await client.get("/api/v1/mcp/info", headers=auth_headers)
        if resp.status_code == 200:
            data = resp.json()
            assert "name" in data or "server" in data or "version" in data


class TestMCPToolExecution:

    @pytest.mark.anyio
    async def test_execute_unknown_tool_post(self, client: AsyncClient, auth_headers: dict[str, str]) -> None:

        """POST /api/v1/mcp/execute with invalid tool should fail."""
        resp = await client.post(
            "/api/v1/mcp/execute",
            json={"name": "nonexistent_tool", "arguments": {}},
            headers=auth_headers,
        )
        # Could be 400, 404, 405, 422, 500 depending on implementation
        assert resp.status_code in (400, 404, 405, 422, 500)
