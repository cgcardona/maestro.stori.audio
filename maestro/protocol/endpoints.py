"""Protocol introspection endpoints.

Exposes the protocol version, hash, event schemas, and tool schemas
so FE (and CI) can detect drift without reading source code.
"""

from __future__ import annotations

from fastapi import APIRouter

from maestro.protocol.hash import compute_protocol_hash
from maestro.protocol.registry import EVENT_REGISTRY, ALL_EVENT_TYPES
from maestro.protocol.version import MAESTRO_VERSION
from maestro.contracts.mcp_types import MCPToolDefWire
from maestro.contracts.pydantic_types import PydanticJson
from maestro.protocol.responses import (
    ProtocolInfoResponse,
    ProtocolEventsResponse,
    ProtocolToolsResponse,
    ProtocolSchemaResponse,
)

router = APIRouter()


@router.get("/protocol")
async def protocol_info() -> ProtocolInfoResponse:
    """Protocol version, hash, and registered event types."""
    return ProtocolInfoResponse(
        protocolVersion=MAESTRO_VERSION,
        protocolHash=compute_protocol_hash(),
        eventTypes=sorted(ALL_EVENT_TYPES),
        eventCount=len(EVENT_REGISTRY),
    )


@router.get("/protocol/events.json")
async def protocol_events() -> ProtocolEventsResponse:
    """JSON Schema for every registered SSE event type.

    FE can consume this to auto-generate Swift Codable structs.
    """
    return ProtocolEventsResponse(
        protocolVersion=MAESTRO_VERSION,
        events={
            event_type: PydanticJson.model_validate(
                EVENT_REGISTRY[event_type].model_json_schema()
            )
            for event_type in sorted(EVENT_REGISTRY)
        },
    )


@router.get("/protocol/tools.json")
async def protocol_tools() -> ProtocolToolsResponse:
    """Unified tool schema (MCP format) for all registered tools."""
    from maestro.mcp.tools import MCP_TOOLS

    return ProtocolToolsResponse(
        protocolVersion=MAESTRO_VERSION,
        tools=[MCPToolDefWire.model_validate(t) for t in MCP_TOOLS],
        toolCount=len(MCP_TOOLS),
    )


@router.get("/protocol/schema.json")
async def protocol_schema() -> ProtocolSchemaResponse:
    """Unified protocol schema — version + hash + events + enums + tools.

    Single fetch for FE type generation, cacheable by protocolHash.
    """
    from maestro.core.intent_config.enums import SSEState, Intent
    from maestro.mcp.tools import MCP_TOOLS

    return ProtocolSchemaResponse(
        protocolVersion=MAESTRO_VERSION,
        protocolHash=compute_protocol_hash(),
        events={
            event_type: PydanticJson.model_validate(
                EVENT_REGISTRY[event_type].model_json_schema()
            )
            for event_type in sorted(EVENT_REGISTRY)
        },
        enums={
            "Intent": sorted(m.value for m in Intent),
            "SSEState": sorted(m.value for m in SSEState),
        },
        tools=[MCPToolDefWire.model_validate(t) for t in MCP_TOOLS],
        toolCount=len(MCP_TOOLS),
        eventCount=len(EVENT_REGISTRY),
    )
