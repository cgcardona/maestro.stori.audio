"""Pydantic response models for protocol introspection endpoints.

Each model names the exact entity returned by a protocol endpoint, making the
contract between the route handler, its serializer, and any caller explicit.
Using named models instead of raw ``dict[str, object]`` means the type checker
— not the programmer — enforces what fields are present and what they contain.

Wire format note: all fields use camelCase names because these models do **not**
extend ``CamelModel`` — the field names are camelCase by declaration.  This
matches the existing wire format the Swift frontend already consumes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from maestro.contracts.json_types import EnumDefinitionMap
from maestro.contracts.mcp_types import MCPToolDefWire
from maestro.contracts.pydantic_types import PydanticJson


class ProtocolInfoResponse(BaseModel):
    """Summary response for ``GET /protocol``.

    Lightweight snapshot of the active protocol: version string, content hash,
    and a flat list of registered event type names.  Suitable for polling or
    drift detection without the overhead of fetching full JSON schemas.

    Attributes:
        protocolVersion: Semver string derived from ``pyproject.toml`` (e.g.
            ``"1.4.2"``).  Bumped on any schema-breaking change.
        protocolHash: SHA-256 content hash of the full serialised schema (hex
            string).  Changes whenever any event shape, field, or tool
            definition changes, even without a version bump.
        eventTypes: Alphabetically sorted list of every registered SSE event
            type name (e.g. ``["complete", "error", "generator_complete",
            …]``).  Used to enumerate valid event keys without fetching their
            full schemas.
        eventCount: Number of registered event types.  Equals
            ``len(eventTypes)``.
    """

    protocolVersion: str = Field(
        description=(
            "Semver string derived from pyproject.toml (e.g. '1.4.2'). "
            "Bumped on any schema-breaking change."
        )
    )
    protocolHash: str = Field(
        description=(
            "SHA-256 content hash of the full serialised schema (hex string). "
            "Changes whenever any event shape, field, or tool definition changes."
        )
    )
    eventTypes: list[str] = Field(
        description=(
            "Alphabetically sorted list of every registered SSE event type name "
            "(e.g. ['complete', 'error', 'generator_complete', …])."
        )
    )
    eventCount: int = Field(
        description="Number of registered event types. Equals len(eventTypes)."
    )


class ProtocolEventsResponse(BaseModel):
    """Response for ``GET /protocol/events.json``.

    Full JSON Schema for every registered SSE event type.  The Stori DAW
    frontend consumes this endpoint to auto-generate Swift ``Codable`` structs
    and to validate incoming SSE payloads at runtime.

    Attributes:
        protocolVersion: Semver string of the protocol version that produced
            these schemas.
        events: Map from event type name to its JSON Schema object.  Keys are
            event type strings (e.g. ``"complete"``); values are ``dict``
            objects conforming to JSON Schema draft-07 (``EventSchemaMap``).
    """

    protocolVersion: str = Field(
        description="Semver string of the protocol version that produced these schemas."
    )
    events: dict[str, PydanticJson] = Field(
        description=(
            "Map from event type name to its JSON Schema object. "
            "Keys are event type strings (e.g. 'complete'); values are "
            "JSON Schema draft-07 objects wrapped as PydanticJson for schema safety."
        )
    )


class ProtocolToolsResponse(BaseModel):
    """Response for ``GET /protocol/tools.json``.

    All registered MCP tool definitions in MCP wire format.  Used by the
    frontend to build the tool palette and by the MCP adapter to expose tools
    to Cursor / Claude Desktop.

    Attributes:
        protocolVersion: Semver string of the protocol version that produced
            these tool definitions.
        tools: Ordered list of every registered ``MCPToolDef`` (TypedDict with
            ``name``, ``description``, ``inputSchema``).
        toolCount: Number of tool definitions.  Equals ``len(tools)``.
    """

    protocolVersion: str = Field(
        description="Semver string of the protocol version that produced these tool definitions."
    )
    tools: list[MCPToolDefWire] = Field(
        description=(
            "Ordered list of every registered MCP tool definition. "
            "Each MCPToolDefWire mirrors MCPToolDef as a Pydantic model with "
            "'name', 'description', and 'inputSchema' fields."
        )
    )
    toolCount: int = Field(
        description="Number of tool definitions. Equals len(tools)."
    )


class ProtocolSchemaResponse(BaseModel):
    """Response for ``GET /protocol/schema.json``.

    Unified schema snapshot — version, hash, all event schemas, enum
    definitions, and tool definitions in one fetch.  Cacheable by
    ``protocolHash``; the frontend uses this for full Swift type generation
    and for verifying that the running backend matches the code it was
    compiled against.

    Attributes:
        protocolVersion: Semver string of the protocol version that produced
            this snapshot.
        protocolHash: SHA-256 content hash of this snapshot.  Stable across
            identical schema content; changes on any structural edit.
        events: Map from event type name to its JSON Schema object
            (``EventSchemaMap``).
        enums: Map from enum name to its sorted list of allowed string values
            (``EnumDefinitionMap``).  Used to generate Swift enum types.
        tools: Ordered list of every registered ``MCPToolDef``.
        toolCount: Number of tool definitions.  Equals ``len(tools)``.
        eventCount: Number of registered event types.  Equals ``len(events)``.
    """

    protocolVersion: str = Field(
        description="Semver string of the protocol version that produced this snapshot."
    )
    protocolHash: str = Field(
        description=(
            "SHA-256 content hash of this snapshot. "
            "Stable across identical schema content; changes on any structural edit."
        )
    )
    events: dict[str, PydanticJson] = Field(
        description=(
            "Map from event type name to its JSON Schema object, "
            "wrapped as PydanticJson for Pydantic schema-generation safety."
        )
    )
    enums: EnumDefinitionMap = Field(
        description=(
            "Map from enum name to its sorted list of allowed string values "
            "(EnumDefinitionMap = dict[str, list[str]]). "
            "Used to generate Swift enum types."
        )
    )
    tools: list[MCPToolDefWire] = Field(
        description=(
            "Ordered list of every registered MCP tool definition. "
            "Each MCPToolDefWire mirrors MCPToolDef as a Pydantic model."
        )
    )
    toolCount: int = Field(
        description="Number of tool definitions. Equals len(tools)."
    )
    eventCount: int = Field(
        description="Number of registered event types. Equals len(events)."
    )
