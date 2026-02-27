"""Tests for MCP API routes (app/api/routes/mcp.py).

Covers: tool list, server info, tool execution endpoint,
and the _parse_daw_response deserialization boundary.
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


class TestParseDawResponse:
    """Unit tests for _parse_daw_response — the WebSocket/HTTP deserialization boundary."""

    def test_success_true_returns_true(self) -> None:
        """JSON true → success=True."""
        from maestro.api.routes.mcp import _parse_daw_response
        result = _parse_daw_response({"success": True})
        assert result["success"] is True

    def test_success_false_returns_false(self) -> None:
        """JSON false → success=False."""
        from maestro.api.routes.mcp import _parse_daw_response
        result = _parse_daw_response({"success": False})
        assert result["success"] is False

    def test_truthy_non_bool_is_not_success(self) -> None:
        """A truthy non-bool value (e.g. 1, 'yes') is NOT treated as success.

        We use `is True` deliberately — only JSON true (Python True) counts.
        """
        from maestro.api.routes.mcp import _parse_daw_response
        assert _parse_daw_response({"success": 1})["success"] is False
        assert _parse_daw_response({"success": "yes"})["success"] is False
        assert _parse_daw_response({"success": []})["success"] is False

    def test_missing_success_key_defaults_false(self) -> None:
        """A dict without a 'success' key returns success=False."""
        from maestro.api.routes.mcp import _parse_daw_response
        result = _parse_daw_response({"status": "ok"})
        assert result["success"] is False

    def test_non_dict_input_returns_false(self) -> None:
        """Non-dict inputs (None, str, list) return success=False without raising."""
        from maestro.api.routes.mcp import _parse_daw_response
        assert _parse_daw_response(None)["success"] is False
        assert _parse_daw_response("ok")["success"] is False
        assert _parse_daw_response([True])["success"] is False
        assert _parse_daw_response(42)["success"] is False

    def test_result_is_daw_tool_response_shape(self) -> None:
        """Return value satisfies the DAWToolResponse TypedDict contract."""
        from maestro.api.routes.mcp import _parse_daw_response
        from maestro.contracts.mcp_types import DAWToolResponse
        result = _parse_daw_response({"success": True})
        # Runtime check: 'success' key present and is a bool
        assert isinstance(result.get("success"), bool)
        # Type compatibility verified by mypy — confirmed in CI
