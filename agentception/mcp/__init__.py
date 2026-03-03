"""AgentCeption MCP layer — JSON-RPC 2.0 tool server.

Exposes AgentCeption plan schema and validation capabilities as MCP tools
via a self-contained JSON-RPC 2.0 dispatcher.

Public surface:
  - ``agentception.mcp.types``   — protocol TypedDicts (ACToolDef, ACToolResult, …)
  - ``agentception.mcp.plan_tools`` — plan_get_schema(), plan_validate_spec()
  - ``agentception.mcp.server``  — JSON-RPC 2.0 dispatcher (handle_request)

Boundary constraint: zero imports from maestro, muse, kly, or storpheus.
"""
from __future__ import annotations
