"""Deterministic protocol fingerprint.

Computes a SHA-256 hash of:
  1. Protocol version string
  2. All event model JSON schemas (sorted by type name)
  3. All tool schemas (sorted by tool name, canonical JSON)
  4. All enum definitions (sorted by enum name)

The hash changes if and only if the wire contract changes.
CI tests lock this hash — any schema change that doesn't bump
the protocol version or update the golden hash fails CI.
"""

from __future__ import annotations

import hashlib
import json
from typing import cast

from app.contracts.json_types import JSONObject, JSONValue
from app.protocol.registry import EVENT_REGISTRY
from app.protocol.version import MAESTRO_VERSION


def _event_schemas_canonical() -> list[JSONObject]:
    """Extract JSON schemas from all registered event models, sorted by type."""
    schemas = []
    for event_type in sorted(EVENT_REGISTRY.keys()):
        model_class = EVENT_REGISTRY[event_type]
        schema = model_class.model_json_schema()
        schemas.append({"type": event_type, "schema": cast(JSONValue, schema)})
    return schemas


def _tool_schemas_canonical() -> list[JSONObject]:
    """Extract canonical tool schemas from the MCP registry, sorted by name."""
    from app.mcp.tools.registry import MCP_TOOLS

    tools = []
    for tool in sorted(MCP_TOOLS, key=lambda t: str(t["name"])):
        tools.append({
            "name": tool["name"],
            "inputSchema": cast(JSONValue, tool.get("inputSchema", {})),
        })
    return tools


def _enum_definitions_canonical() -> list[JSONObject]:
    """Extract enum values for contract-critical enums."""
    from app.core.intent_config.enums import SSEState, Intent

    return [
        {"name": "Intent", "values": cast(JSONValue, sorted(m.value for m in Intent))},
        {"name": "SSEState", "values": cast(JSONValue, sorted(m.value for m in SSEState))},
    ]


def compute_protocol_hash() -> str:
    """Compute deterministic SHA-256 hash of the entire protocol surface."""
    payload = {
        "version": MAESTRO_VERSION,
        "events": _event_schemas_canonical(),
        "tools": _tool_schemas_canonical(),
        "enums": _enum_definitions_canonical(),
    }
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_protocol_hash_short() -> str:
    """16-char short hash for display / header use."""
    return compute_protocol_hash()[:16]
