"""Tests for the MCP server (app/mcp/server.py).

Covers: StoriMCPServer, get_server_info, list_tools, call_tool,
register_daw, unregister_daw, update_project_state, receive_tool_response,
ToolCallResult, DAWConnection.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.mcp.server import (
    StoriMCPServer,
    ToolCallResult,
    DAWConnection,
)


@pytest.fixture
def mcp_server() -> StoriMCPServer:
    from app.protocol.version import STORI_VERSION
    with patch("app.config.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(app_version=STORI_VERSION)
        server = StoriMCPServer()
    return server


# ---------------------------------------------------------------------------
# ToolCallResult
# ---------------------------------------------------------------------------


class TestToolCallResult:

    def test_success(self) -> None:

        r = ToolCallResult(
            success=True,
            content=[{"type": "text", "text": "done"}],
        )
        assert r.success is True
        assert r.is_error is False

    def test_error(self) -> None:

        r = ToolCallResult(
            success=False,
            content=[{"type": "text", "text": "fail"}],
            is_error=True,
            bad_request=True,
        )
        assert r.is_error is True
        assert r.bad_request is True


# ---------------------------------------------------------------------------
# DAWConnection
# ---------------------------------------------------------------------------


class TestDAWConnection:

    def test_construction(self) -> None:

        cb = AsyncMock()
        conn = DAWConnection(id="daw-1", send_callback=cb)
        assert conn.id == "daw-1"
        assert conn.project_state is None
        assert len(conn.pending_responses) == 0


# ---------------------------------------------------------------------------
# StoriMCPServer
# ---------------------------------------------------------------------------


class TestStoriMCPServer:

    def test_get_server_info(self, mcp_server: StoriMCPServer) -> None:

        info = mcp_server.get_server_info()
        assert info["name"] == "stori-daw"
        assert "version" in info
        assert "capabilities" in info

    def test_list_tools(self, mcp_server: StoriMCPServer) -> None:

        tools = mcp_server.list_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0
        for tool in tools:
            assert "name" in tool

    def test_register_and_unregister_daw(self, mcp_server: StoriMCPServer) -> None:

        cb = AsyncMock()
        mcp_server.register_daw("daw-1", cb)
        assert "daw-1" in mcp_server._daw_connections
        assert mcp_server._active_connection == "daw-1"

        mcp_server.unregister_daw("daw-1")
        assert "daw-1" not in mcp_server._daw_connections
        assert mcp_server._active_connection is None

    def test_unregister_nonexistent(self, mcp_server: StoriMCPServer) -> None:

        mcp_server.unregister_daw("ghost")  # Should not raise

    def test_update_project_state(self, mcp_server: StoriMCPServer) -> None:

        cb = AsyncMock()
        mcp_server.register_daw("daw-1", cb)
        mcp_server.update_project_state("daw-1", {"tempo": 120})
        assert mcp_server._daw_connections["daw-1"].project_state == {"tempo": 120}

    def test_update_state_nonexistent(self, mcp_server: StoriMCPServer) -> None:

        mcp_server.update_project_state("ghost", {"tempo": 100})  # Should not raise

    @pytest.mark.anyio
    async def test_receive_tool_response(self, mcp_server: StoriMCPServer) -> None:

        cb = AsyncMock()
        mcp_server.register_daw("daw-1", cb)
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        mcp_server._daw_connections["daw-1"].pending_responses["req-1"] = future
        mcp_server.receive_tool_response("daw-1", "req-1", {"status": "ok"})
        assert future.result() == {"status": "ok"}

    def test_receive_response_nonexistent_connection(self, mcp_server: StoriMCPServer) -> None:

        mcp_server.receive_tool_response("ghost", "req-1", {})  # Should not raise

    def test_receive_response_nonexistent_request(self, mcp_server: StoriMCPServer) -> None:

        cb = AsyncMock()
        mcp_server.register_daw("daw-1", cb)
        mcp_server.receive_tool_response("daw-1", "no-req", {})  # Should not raise

    def test_multiple_daw_connections(self, mcp_server: StoriMCPServer) -> None:

        cb1, cb2 = AsyncMock(), AsyncMock()
        mcp_server.register_daw("daw-1", cb1)
        mcp_server.register_daw("daw-2", cb2)
        assert len(mcp_server._daw_connections) == 2
        assert mcp_server._active_connection == "daw-2"

        mcp_server.unregister_daw("daw-2")
        assert mcp_server._active_connection == "daw-1"


# ---------------------------------------------------------------------------
# call_tool (validation)
# ---------------------------------------------------------------------------


class TestCallTool:

    @pytest.mark.anyio
    async def test_invalid_tool_name(self, mcp_server: StoriMCPServer) -> None:

        result = await mcp_server.call_tool("nonexistent_tool", {})
        assert result.success is False
        assert result.is_error is True
        assert result.bad_request is True

    @pytest.mark.anyio
    async def test_valid_daw_tool_no_connection(self, mcp_server: StoriMCPServer) -> None:

        """Valid DAW tool but no DAW connected."""
        result = await mcp_server.call_tool("stori_set_tempo", {"tempo": 120})
        # Should fail because no DAW is connected
        assert result.success is False or result.is_error is True

    @pytest.mark.anyio
    async def test_invalid_params(self, mcp_server: StoriMCPServer) -> None:

        """Tool exists but params are invalid."""
        result = await mcp_server.call_tool("stori_set_tempo", {"tempo": -1})
        assert result.success is False
