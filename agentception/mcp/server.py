"""AgentCeption MCP JSON-RPC 2.0 server.

Implements a minimal but spec-compliant JSON-RPC 2.0 dispatcher for the
AgentCeption MCP tool layer.  The dispatcher is synchronous and stateless —
it handles exactly one request per call to :func:`handle_request`.

Supported methods:
  ``tools/list``  — returns all registered :class:`~agentception.mcp.types.ACToolDef`
  ``tools/call``  — dispatches to the named tool function

Error handling follows the JSON-RPC 2.0 specification:
  - Parse errors     → code -32700 (never raised here; caller parses JSON)
  - Invalid Request  → code -32600 (missing required fields)
  - Method not found → code -32601
  - Invalid params   → code -32602 (wrong or missing tool name / arguments)
  - Internal error   → code -32603 (unexpected exception in tool handler)

Boundary constraint: zero imports from maestro, muse, kly, or storpheus.
"""
from __future__ import annotations

import json
import logging

from agentception.mcp.plan_tools import plan_get_schema, plan_validate_spec
from agentception.mcp.types import (
    ACToolContent,
    ACToolDef,
    ACToolResult,
    JSONRPC_ERR_INTERNAL_ERROR,
    JSONRPC_ERR_INVALID_PARAMS,
    JSONRPC_ERR_INVALID_REQUEST,
    JSONRPC_ERR_METHOD_NOT_FOUND,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

#: All tools exposed by this MCP server.  Each entry is an :class:`ACToolDef`
#: mapping the tool name to its description and input JSON Schema.
TOOLS: list[ACToolDef] = [
    ACToolDef(
        name="plan_get_schema",
        description=(
            "Return the JSON Schema for PlanSpec — the plan-step-v2 YAML contract. "
            "Use this to understand the required structure before calling plan_validate_spec."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    ACToolDef(
        name="plan_validate_spec",
        description=(
            "Validate a JSON string against the PlanSpec schema. "
            "Returns {valid: true, spec: {...}} on success or "
            "{valid: false, errors: [...]} on failure."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "spec_json": {
                    "type": "string",
                    "description": "A JSON-encoded PlanSpec object to validate.",
                }
            },
            "required": ["spec_json"],
            "additionalProperties": False,
        },
    ),
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_error_response(
    request_id: int | str | None,
    code: int,
    message: str,
    data: object = None,
) -> dict[str, object]:
    """Build a well-formed JSON-RPC 2.0 error response."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": code,
            "message": message,
            "data": data,
        },
    }


def _make_success_response(
    request_id: int | str | None,
    result: object,
) -> dict[str, object]:
    """Build a well-formed JSON-RPC 2.0 success response."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def _tool_result_to_text(result: dict[str, object]) -> str:
    """Serialise a tool result dict to a compact JSON string."""
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------


def list_tools() -> list[ACToolDef]:
    """Return all registered MCP tool definitions.

    Returns:
        A list of :class:`~agentception.mcp.types.ACToolDef` objects,
        one per registered tool.
    """
    return list(TOOLS)


def call_tool(name: str, arguments: dict[str, object]) -> ACToolResult:
    """Dispatch a ``tools/call`` request to the named tool function.

    Args:
        name:      The tool name as it appears in the ``tools/list`` response.
        arguments: The tool arguments dict from the JSON-RPC params.

    Returns:
        An :class:`~agentception.mcp.types.ACToolResult` with ``isError=False``
        on success or ``isError=True`` when the tool name is unknown or
        arguments are invalid.

    Never raises — all errors are returned as ``isError=True`` results.
    """
    if name == "plan_get_schema":
        schema = plan_get_schema()
        text = _tool_result_to_text(schema)
        content: list[ACToolContent] = [ACToolContent(type="text", text=text)]
        return ACToolResult(content=content, isError=False)

    if name == "plan_validate_spec":
        spec_json = arguments.get("spec_json")
        if not isinstance(spec_json, str):
            err_text = _tool_result_to_text(
                {"error": "Missing or invalid required argument 'spec_json' (must be a string)"}
            )
            return ACToolResult(
                content=[ACToolContent(type="text", text=err_text)],
                isError=True,
            )
        result = plan_validate_spec(spec_json)
        text = _tool_result_to_text(result)
        is_error = not bool(result.get("valid", False))
        return ACToolResult(
            content=[ACToolContent(type="text", text=text)],
            isError=is_error,
        )

    err_text = _tool_result_to_text({"error": f"Unknown tool: {name!r}"})
    logger.warning("⚠️ call_tool: unknown tool %r", name)
    return ACToolResult(
        content=[ACToolContent(type="text", text=err_text)],
        isError=True,
    )


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 request handler
# ---------------------------------------------------------------------------


def handle_request(
    raw: dict[str, object],
) -> dict[str, object]:
    """Dispatch a JSON-RPC 2.0 request dict and return a response dict.

    This is the single entry point for the MCP layer.  The caller is
    responsible for JSON parsing (converting the wire bytes to a ``dict``);
    this function handles everything from field extraction through to
    building the response envelope.

    Args:
        raw: A ``dict[str, object]`` parsed from a JSON-RPC 2.0 request body.

    Returns:
        Either a :class:`~agentception.mcp.types.JsonRpcSuccessResponse` or
        a :class:`~agentception.mcp.types.JsonRpcErrorResponse`.  The caller
        should serialise the return value back to JSON for the wire.

    Never raises.
    """
    request_id: int | str | None = raw.get("id")  # type: ignore[assignment]
    # Narrow: id must be int | str | None per spec; cast safely.
    if not isinstance(request_id, (int, str, type(None))):
        request_id = None

    jsonrpc = raw.get("jsonrpc")
    if jsonrpc != "2.0":
        return _make_error_response(
            request_id,
            JSONRPC_ERR_INVALID_REQUEST,
            "jsonrpc must be '2.0'",
        )

    method = raw.get("method")
    if not isinstance(method, str):
        return _make_error_response(
            request_id,
            JSONRPC_ERR_INVALID_REQUEST,
            "method must be a string",
        )

    logger.debug("🔧 handle_request: method=%r id=%r", method, request_id)

    if method == "tools/list":
        tools = list_tools()
        return _make_success_response(request_id, {"tools": tools})

    if method == "tools/call":
        params = raw.get("params")
        if not isinstance(params, dict):
            return _make_error_response(
                request_id,
                JSONRPC_ERR_INVALID_PARAMS,
                "params must be an object for tools/call",
            )

        tool_name = params.get("name")
        if not isinstance(tool_name, str):
            return _make_error_response(
                request_id,
                JSONRPC_ERR_INVALID_PARAMS,
                "params.name must be a string",
            )

        arguments_raw = params.get("arguments", {})
        if not isinstance(arguments_raw, dict):
            return _make_error_response(
                request_id,
                JSONRPC_ERR_INVALID_PARAMS,
                "params.arguments must be an object",
            )

        arguments: dict[str, object] = {k: v for k, v in arguments_raw.items()}

        try:
            tool_result = call_tool(tool_name, arguments)
        except Exception as exc:
            logger.error("❌ handle_request: internal error in call_tool — %s", exc, exc_info=True)
            return _make_error_response(
                request_id,
                JSONRPC_ERR_INTERNAL_ERROR,
                f"Internal error: {exc}",
            )

        return _make_success_response(request_id, tool_result)

    return _make_error_response(
        request_id,
        JSONRPC_ERR_METHOD_NOT_FOUND,
        f"Method not found: {method!r}",
    )
