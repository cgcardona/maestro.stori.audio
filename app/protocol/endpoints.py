"""Protocol introspection endpoints.

Exposes the protocol version, hash, event schemas, and tool schemas
so FE (and CI) can detect drift without reading source code.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.protocol.hash import compute_protocol_hash
from app.protocol.registry import EVENT_REGISTRY, ALL_EVENT_TYPES
from app.protocol.version import STORI_PROTOCOL_VERSION

router = APIRouter()


@router.get("/protocol")
async def protocol_info():
    """Protocol version, hash, and registered event types."""
    return {
        "protocolVersion": STORI_PROTOCOL_VERSION,
        "protocolHash": compute_protocol_hash(),
        "eventTypes": sorted(ALL_EVENT_TYPES),
        "eventCount": len(EVENT_REGISTRY),
    }


@router.get("/protocol/events.json")
async def protocol_events():
    """JSON Schema for every registered SSE event type.

    FE can consume this to auto-generate Swift Codable structs.
    """
    schemas = {}
    for event_type in sorted(EVENT_REGISTRY.keys()):
        model_class = EVENT_REGISTRY[event_type]
        schemas[event_type] = model_class.model_json_schema()
    return {
        "protocolVersion": STORI_PROTOCOL_VERSION,
        "events": schemas,
    }


@router.get("/protocol/tools.json")
async def protocol_tools():
    """Unified tool schema (MCP format) for all registered tools."""
    from app.mcp.tools.registry import MCP_TOOLS

    return {
        "protocolVersion": STORI_PROTOCOL_VERSION,
        "tools": MCP_TOOLS,
        "toolCount": len(MCP_TOOLS),
    }


@router.get("/protocol/schema.json")
async def protocol_schema():
    """Unified protocol schema — version + hash + events + enums + tools.

    Single fetch for FE type generation, cacheable by protocolHash.
    """
    from app.core.intent_config.enums import SSEState, Intent
    from app.mcp.tools.registry import MCP_TOOLS

    event_schemas = {}
    for event_type in sorted(EVENT_REGISTRY.keys()):
        model_class = EVENT_REGISTRY[event_type]
        event_schemas[event_type] = model_class.model_json_schema()

    enum_defs: dict[str, list[str]] = {
        "Intent": sorted(m.value for m in Intent),
        "SSEState": sorted(m.value for m in SSEState),
    }

    return {
        "protocolVersion": STORI_PROTOCOL_VERSION,
        "protocolHash": compute_protocol_hash(),
        "events": event_schemas,
        "enums": enum_defs,
        "tools": MCP_TOOLS,
        "toolCount": len(MCP_TOOLS),
        "eventCount": len(EVENT_REGISTRY),
    }
