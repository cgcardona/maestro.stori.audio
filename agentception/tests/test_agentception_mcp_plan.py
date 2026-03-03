"""Tests for the AgentCeption MCP layer — plan schema and validation tools.

Covers:
- agentception.mcp.types: ACToolDef shape, ACToolResult shape
- agentception.mcp.plan_tools: plan_get_schema(), plan_validate_spec()
- agentception.mcp.server: list_tools(), call_tool(), handle_request()

All tests are synchronous (no async I/O); pytest-anyio is not required here.

Boundary: zero imports from maestro, muse, kly, or storpheus.
"""
from __future__ import annotations

import json

import pytest

from agentception.mcp.plan_tools import plan_get_schema, plan_validate_spec
from agentception.mcp.server import TOOLS, call_tool, handle_request, list_tools
from agentception.mcp.types import (
    ACToolDef,
    ACToolResult,
    JSONRPC_ERR_INVALID_PARAMS,
    JSONRPC_ERR_INVALID_REQUEST,
    JSONRPC_ERR_METHOD_NOT_FOUND,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _minimal_plan_spec_json() -> str:
    """Return a JSON string for a valid minimal PlanSpec."""
    return json.dumps(
        {
            "initiative": "smoke-test",
            "phases": [
                {
                    "label": "0-foundation",
                    "description": "Foundation phase",
                    "depends_on": [],
                    "issues": [
                        {
                            "title": "Bootstrap the repo",
                            "body": "Set up the initial project structure.",
                            "depends_on": [],
                        }
                    ],
                }
            ],
        }
    )


# ---------------------------------------------------------------------------
# ACToolDef shape tests
# ---------------------------------------------------------------------------


def test_ac_tool_def_has_required_keys() -> None:
    """ACToolDef TypedDict must carry name, description, and inputSchema."""
    tool: ACToolDef = ACToolDef(
        name="my_tool",
        description="Does something useful.",
        inputSchema={"type": "object", "properties": {}},
    )
    assert tool["name"] == "my_tool"
    assert tool["description"] == "Does something useful."
    assert "type" in tool["inputSchema"]


def test_ac_tool_result_has_required_keys() -> None:
    """ACToolResult must carry content list and isError bool."""
    result: ACToolResult = ACToolResult(
        content=[{"type": "text", "text": "hello"}],  # type: ignore[list-item]
        isError=False,
    )
    assert result["isError"] is False
    assert len(result["content"]) == 1
    assert result["content"][0]["text"] == "hello"


# ---------------------------------------------------------------------------
# plan_get_schema tests
# ---------------------------------------------------------------------------


def test_plan_get_schema_returns_dict() -> None:
    """plan_get_schema() returns a non-empty dict."""
    schema = plan_get_schema()
    assert isinstance(schema, dict)
    assert len(schema) > 0


def test_plan_get_schema_has_title() -> None:
    """plan_get_schema() output references PlanSpec as the schema title."""
    schema = plan_get_schema()
    assert schema.get("title") == "PlanSpec"


def test_plan_get_schema_has_required_fields() -> None:
    """plan_get_schema() output includes initiative and phases in required."""
    schema = plan_get_schema()
    required = schema.get("required", [])
    assert isinstance(required, list)
    assert "initiative" in required
    assert "phases" in required


def test_plan_get_schema_has_properties() -> None:
    """plan_get_schema() output has a non-empty properties dict."""
    schema = plan_get_schema()
    props = schema.get("properties", {})
    assert isinstance(props, dict)
    assert "initiative" in props
    assert "phases" in props


def test_plan_get_schema_is_cached() -> None:
    """Repeated calls to plan_get_schema() return the same object (cached)."""
    first = plan_get_schema()
    second = plan_get_schema()
    assert first is second


# ---------------------------------------------------------------------------
# plan_validate_spec — valid input
# ---------------------------------------------------------------------------


def test_plan_validate_spec_valid_minimal() -> None:
    """plan_validate_spec returns valid=True for a correct minimal spec."""
    result = plan_validate_spec(_minimal_plan_spec_json())
    assert result["valid"] is True
    assert "spec" in result


def test_plan_validate_spec_valid_returns_spec_dict() -> None:
    """plan_validate_spec result 'spec' is a dict with initiative and phases."""
    result = plan_validate_spec(_minimal_plan_spec_json())
    spec = result["spec"]
    assert isinstance(spec, dict)
    assert spec["initiative"] == "smoke-test"
    assert isinstance(spec["phases"], list)
    assert len(spec["phases"]) == 1


def test_plan_validate_spec_valid_multi_phase() -> None:
    """plan_validate_spec returns valid=True for a multi-phase spec with deps."""
    spec_json = json.dumps(
        {
            "initiative": "auth-rewrite",
            "phases": [
                {
                    "label": "0-foundation",
                    "description": "Core types",
                    "depends_on": [],
                    "issues": [
                        {"title": "Define AuthToken", "body": "Token model.", "depends_on": []}
                    ],
                },
                {
                    "label": "1-api",
                    "description": "API endpoints",
                    "depends_on": ["0-foundation"],
                    "issues": [
                        {"title": "POST /auth/login", "body": "Login endpoint.", "depends_on": []}
                    ],
                },
            ],
        }
    )
    result = plan_validate_spec(spec_json)
    assert result["valid"] is True


# ---------------------------------------------------------------------------
# plan_validate_spec — invalid JSON
# ---------------------------------------------------------------------------


def test_plan_validate_spec_invalid_json_syntax() -> None:
    """plan_validate_spec returns valid=False for malformed JSON."""
    result = plan_validate_spec("{bad json}")
    assert result["valid"] is False
    errors = result["errors"]
    assert isinstance(errors, list)
    assert len(errors) > 0
    assert "JSON parse error" in errors[0]


def test_plan_validate_spec_empty_string() -> None:
    """plan_validate_spec returns valid=False for an empty string."""
    result = plan_validate_spec("")
    assert result["valid"] is False


# ---------------------------------------------------------------------------
# plan_validate_spec — schema violations
# ---------------------------------------------------------------------------


def test_plan_validate_spec_missing_initiative() -> None:
    """plan_validate_spec returns valid=False when initiative is absent."""
    bad = json.dumps(
        {
            "phases": [
                {
                    "label": "0-foundation",
                    "description": "d",
                    "issues": [{"title": "t", "body": "b"}],
                }
            ]
        }
    )
    result = plan_validate_spec(bad)
    assert result["valid"] is False
    assert "errors" in result


def test_plan_validate_spec_missing_phases() -> None:
    """plan_validate_spec returns valid=False when phases is absent."""
    bad = json.dumps({"initiative": "no-phases"})
    result = plan_validate_spec(bad)
    assert result["valid"] is False


def test_plan_validate_spec_empty_phases() -> None:
    """plan_validate_spec returns valid=False when phases list is empty."""
    bad = json.dumps({"initiative": "empty", "phases": []})
    result = plan_validate_spec(bad)
    assert result["valid"] is False


def test_plan_validate_spec_forward_phase_dep() -> None:
    """plan_validate_spec returns valid=False for a forward phase dependency."""
    bad = json.dumps(
        {
            "initiative": "bad-deps",
            "phases": [
                {
                    "label": "0-foundation",
                    "description": "Phase A",
                    "depends_on": ["1-api"],  # forward reference
                    "issues": [{"title": "t", "body": "b"}],
                },
                {
                    "label": "1-api",
                    "description": "Phase B",
                    "depends_on": [],
                    "issues": [{"title": "t", "body": "b"}],
                },
            ],
        }
    )
    result = plan_validate_spec(bad)
    assert result["valid"] is False


def test_plan_validate_spec_errors_is_list_of_strings() -> None:
    """plan_validate_spec error list items are always strings."""
    bad = json.dumps({"initiative": "x"})
    result = plan_validate_spec(bad)
    assert result["valid"] is False
    errors = result["errors"]
    assert isinstance(errors, list)
    for err in errors:
        assert isinstance(err, str)


# ---------------------------------------------------------------------------
# list_tools tests
# ---------------------------------------------------------------------------


def test_list_tools_returns_non_empty_list() -> None:
    """list_tools() returns at least one tool definition."""
    tools = list_tools()
    assert isinstance(tools, list)
    assert len(tools) > 0


def test_list_tools_contains_plan_get_schema() -> None:
    """list_tools() includes the plan_get_schema tool."""
    names = [t["name"] for t in list_tools()]
    assert "plan_get_schema" in names


def test_list_tools_contains_plan_validate_spec() -> None:
    """list_tools() includes the plan_validate_spec tool."""
    names = [t["name"] for t in list_tools()]
    assert "plan_validate_spec" in names


def test_list_tools_all_have_required_keys() -> None:
    """Every tool returned by list_tools() has name, description, inputSchema."""
    for tool in list_tools():
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool
        assert isinstance(tool["name"], str)
        assert isinstance(tool["description"], str)
        assert isinstance(tool["inputSchema"], dict)


def test_list_tools_input_schema_is_object_type() -> None:
    """Every tool's inputSchema has type='object'."""
    for tool in list_tools():
        assert tool["inputSchema"].get("type") == "object"


def test_tools_module_constant_matches_list_tools() -> None:
    """The module-level TOOLS constant is consistent with list_tools()."""
    assert list_tools() == list(TOOLS)


# ---------------------------------------------------------------------------
# call_tool tests
# ---------------------------------------------------------------------------


def test_call_tool_plan_get_schema_returns_result() -> None:
    """call_tool('plan_get_schema', {}) returns isError=False with text content."""
    result = call_tool("plan_get_schema", {})
    assert result["isError"] is False
    assert len(result["content"]) == 1
    assert result["content"][0]["type"] == "text"


def test_call_tool_plan_get_schema_content_is_valid_json() -> None:
    """call_tool('plan_get_schema') content text parses as valid JSON."""
    result = call_tool("plan_get_schema", {})
    text = result["content"][0]["text"]
    parsed = json.loads(text)
    assert isinstance(parsed, dict)
    assert "title" in parsed or "properties" in parsed  # JSON Schema marker


def test_call_tool_plan_validate_spec_valid_returns_no_error() -> None:
    """call_tool('plan_validate_spec') with valid JSON returns isError=False."""
    result = call_tool("plan_validate_spec", {"spec_json": _minimal_plan_spec_json()})
    assert result["isError"] is False


def test_call_tool_plan_validate_spec_invalid_returns_error() -> None:
    """call_tool('plan_validate_spec') with bad JSON returns isError=True."""
    result = call_tool("plan_validate_spec", {"spec_json": "{bad}"})
    assert result["isError"] is True


def test_call_tool_plan_validate_spec_missing_arg_returns_error() -> None:
    """call_tool('plan_validate_spec', {}) without spec_json returns isError=True."""
    result = call_tool("plan_validate_spec", {})
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "spec_json" in text


def test_call_tool_unknown_returns_error() -> None:
    """call_tool with an unknown tool name returns isError=True."""
    result = call_tool("nonexistent_tool", {})
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "Unknown tool" in text


# ---------------------------------------------------------------------------
# handle_request tests — tools/list
# ---------------------------------------------------------------------------


def _list_request(req_id: int | str | None = 1) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": req_id, "method": "tools/list"}


def _call_request(
    name: str,
    arguments: dict[str, object],
    req_id: int | str | None = 2,
) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def test_handle_request_tools_list_success() -> None:
    """handle_request for tools/list returns a success response."""
    resp = handle_request(_list_request())
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert "result" in resp
    assert "error" not in resp


def test_handle_request_tools_list_result_has_tools_key() -> None:
    """handle_request tools/list result contains a 'tools' list."""
    resp = handle_request(_list_request())
    result = resp.get("result")
    assert isinstance(result, dict)
    assert "tools" in result
    assert isinstance(result["tools"], list)


def test_handle_request_tools_list_preserves_request_id() -> None:
    """handle_request preserves the request id in the response."""
    resp = handle_request(_list_request(req_id=42))
    assert resp["id"] == 42


def test_handle_request_tools_list_string_id() -> None:
    """handle_request works with string request IDs."""
    resp = handle_request(_list_request(req_id="abc-123"))
    assert resp["id"] == "abc-123"


# ---------------------------------------------------------------------------
# handle_request tests — tools/call
# ---------------------------------------------------------------------------


def test_handle_request_tools_call_plan_get_schema() -> None:
    """handle_request tools/call plan_get_schema returns success result."""
    resp = handle_request(_call_request("plan_get_schema", {}))
    assert "result" in resp
    assert "error" not in resp


def test_handle_request_tools_call_plan_validate_spec_valid() -> None:
    """handle_request tools/call plan_validate_spec with valid spec succeeds."""
    resp = handle_request(
        _call_request("plan_validate_spec", {"spec_json": _minimal_plan_spec_json()})
    )
    assert "result" in resp
    result = resp.get("result")
    assert isinstance(result, dict)
    assert result.get("isError") is False


def test_handle_request_tools_call_plan_validate_spec_invalid() -> None:
    """handle_request tools/call plan_validate_spec with bad spec returns isError=True."""
    resp = handle_request(_call_request("plan_validate_spec", {"spec_json": "{bad}"}))
    assert "result" in resp
    result = resp.get("result")
    assert isinstance(result, dict)
    assert result.get("isError") is True


# ---------------------------------------------------------------------------
# handle_request tests — error cases
# ---------------------------------------------------------------------------


def _assert_error_code(resp: dict[str, object], expected_code: int) -> None:
    """Assert resp is a JSON-RPC error response with the given error code."""
    assert "error" in resp
    error = resp["error"]
    assert isinstance(error, dict)
    assert error["code"] == expected_code


def test_handle_request_wrong_jsonrpc_version() -> None:
    """handle_request returns INVALID_REQUEST for wrong jsonrpc version."""
    resp = handle_request({"jsonrpc": "1.0", "id": 1, "method": "tools/list"})
    _assert_error_code(resp, JSONRPC_ERR_INVALID_REQUEST)


def test_handle_request_missing_jsonrpc_field() -> None:
    """handle_request returns INVALID_REQUEST when jsonrpc is absent."""
    resp = handle_request({"id": 1, "method": "tools/list"})
    _assert_error_code(resp, JSONRPC_ERR_INVALID_REQUEST)


def test_handle_request_missing_method() -> None:
    """handle_request returns INVALID_REQUEST when method is absent."""
    resp = handle_request({"jsonrpc": "2.0", "id": 1})
    _assert_error_code(resp, JSONRPC_ERR_INVALID_REQUEST)


def test_handle_request_unknown_method() -> None:
    """handle_request returns METHOD_NOT_FOUND for an unregistered method."""
    resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/unknown"})
    _assert_error_code(resp, JSONRPC_ERR_METHOD_NOT_FOUND)


def test_handle_request_tools_call_missing_params() -> None:
    """handle_request returns INVALID_PARAMS when params is missing for tools/call."""
    resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})
    _assert_error_code(resp, JSONRPC_ERR_INVALID_PARAMS)


def test_handle_request_tools_call_missing_name() -> None:
    """handle_request returns INVALID_PARAMS when params.name is missing."""
    resp = handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"arguments": {}}}
    )
    _assert_error_code(resp, JSONRPC_ERR_INVALID_PARAMS)


def test_handle_request_null_id_is_preserved() -> None:
    """handle_request preserves id=null (None) per JSON-RPC 2.0 spec."""
    resp = handle_request({"jsonrpc": "2.0", "id": None, "method": "tools/list"})
    assert resp["id"] is None


def test_handle_request_returns_dict() -> None:
    """handle_request always returns a dict regardless of input."""
    resp = handle_request(_list_request())
    assert isinstance(resp, dict)
