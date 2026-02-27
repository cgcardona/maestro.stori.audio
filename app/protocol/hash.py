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

from app.protocol.registry import EVENT_REGISTRY
from app.protocol.version import MAESTRO_VERSION


def _event_schemas_canonical() -> list[dict[str, object]]:
    """Extract JSON schemas from all registered event models, sorted by type.

    ``model_json_schema()`` returns ``dict[str, Any]`` (Pydantic's own annotation).
    We accept it as ``dict[str, object]`` — the cast is not needed because ``Any``
    is assignable to ``object``, and the result is only serialised by ``json.dumps``.
    """
    schemas: list[dict[str, object]] = []
    for event_type in sorted(EVENT_REGISTRY.keys()):
        model_class = EVENT_REGISTRY[event_type]
        schema: dict[str, object] = model_class.model_json_schema()
        schemas.append({"type": event_type, "schema": schema})
    return schemas


def _tool_schemas_canonical() -> list[dict[str, object]]:
    """Extract canonical tool schemas from the MCP registry, sorted by name."""
    from app.mcp.tools import MCP_TOOLS

    tools: list[dict[str, object]] = []
    for tool in sorted(MCP_TOOLS, key=lambda t: t["name"]):
        tools.append({
            "name": tool["name"],
            "inputSchema": tool.get("inputSchema", {}),
        })
    return tools


def _enum_definitions_canonical() -> list[dict[str, object]]:
    """Extract enum values for contract-critical enums.

    ``sorted(m.value ...)`` returns ``list[str]``.  Stored as ``list[object]``
    — no cast needed; ``str`` is a subtype of ``object``.
    """
    from app.core.intent_config.enums import SSEState, Intent

    return [
        {"name": "Intent", "values": sorted(m.value for m in Intent)},
        {"name": "SSEState", "values": sorted(m.value for m in SSEState)},
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
