"""MuseHub MCP tool definitions — server-side browsing tools for AI agents.

These tools allow Cursor/Claude and other MCP clients to explore MuseHub
repositories, inspect commit history, read artifact metadata, query musical
analysis, and search — all without requiring a connected DAW.

Every tool in this module is ``server_side: True``.  The MCP server routes
them to ``musehub_mcp_executor`` rather than forwarding them to the DAW.

Naming convention: ``musehub_<verb>_<noun>`` — distinct from DAW tools
which use the ``stori_`` prefix.
"""
from __future__ import annotations

from maestro.contracts.mcp_types import MCPToolDef


MUSEHUB_TOOLS: list[MCPToolDef] = [
    {
        "name": "musehub_browse_repo",
        "server_side": True,
        "description": (
            "Get an overview of a MuseHub repository: metadata, branches, and recent commits. "
            "Use this to orient yourself before reading files or analysing commit history. "
            "Example: musehub_browse_repo(repo_id='a3f2-...')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_id": {
                    "type": "string",
                    "description": "UUID of the MuseHub repository (e.g. 'a3f2-...').",
                },
            },
            "required": ["repo_id"],
        },
    },
    {
        "name": "musehub_list_branches",
        "server_side": True,
        "description": (
            "List all branches in a MuseHub repository with their head commit IDs. "
            "Call before musehub_list_commits to identify the target branch ref. "
            "Example: musehub_list_branches(repo_id='a3f2-...')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_id": {
                    "type": "string",
                    "description": "UUID of the MuseHub repository.",
                },
            },
            "required": ["repo_id"],
        },
    },
    {
        "name": "musehub_list_commits",
        "server_side": True,
        "description": (
            "List commits on a MuseHub repository (newest first). "
            "Optionally filter by branch name and cap the result count. "
            "Example: musehub_list_commits(repo_id='a3f2-...', branch='main', limit=10)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_id": {
                    "type": "string",
                    "description": "UUID of the MuseHub repository.",
                },
                "branch": {
                    "type": "string",
                    "description": "Branch name filter (e.g. 'main'). Omit to list across all branches.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum commits to return (default: 20, max: 100).",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": ["repo_id"],
        },
    },
    {
        "name": "musehub_read_file",
        "server_side": True,
        "description": (
            "Read the metadata for a stored artifact (MIDI, MP3, WebP piano roll) "
            "in a MuseHub repo. Returns path, size_bytes, mime_type, and object_id. "
            "Binary content is not returned — discover object IDs via musehub_browse_repo first. "
            "Example: musehub_read_file(repo_id='a3f2-...', object_id='sha256:abc...')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_id": {
                    "type": "string",
                    "description": "UUID of the MuseHub repository.",
                },
                "object_id": {
                    "type": "string",
                    "description": "Content-addressed object ID (e.g. 'sha256:abc...').",
                },
            },
            "required": ["repo_id", "object_id"],
        },
    },
    {
        "name": "musehub_get_analysis",
        "server_side": True,
        "description": (
            "Get structured analysis for a MuseHub repository. "
            "Dimensions: 'overview' returns repo stats + branch/commit/object counts; "
            "'commits' returns commit activity summary (authors, message samples); "
            "'objects' returns artifact inventory grouped by MIME type. "
            "MIDI audio analysis requires Storpheus integration (not yet available — "
            "dimension fields for key/tempo will be None until integrated). "
            "Example: musehub_get_analysis(repo_id='a3f2-...', dimension='overview')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_id": {
                    "type": "string",
                    "description": "UUID of the MuseHub repository.",
                },
                "dimension": {
                    "type": "string",
                    "description": "Analysis dimension: 'overview', 'commits', or 'objects'.",
                    "enum": ["overview", "commits", "objects"],
                    "default": "overview",
                },
            },
            "required": ["repo_id"],
        },
    },
    {
        "name": "musehub_search",
        "server_side": True,
        "description": (
            "Search within a MuseHub repository by substring query. "
            "Mode 'path' matches artifact file paths (e.g. 'tracks/jazz'); "
            "mode 'commit' searches commit messages (e.g. 'add bass'). "
            "Returns matching items with their metadata. "
            "Example: musehub_search(repo_id='a3f2-...', query='bass', mode='path')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_id": {
                    "type": "string",
                    "description": "UUID of the MuseHub repository to search within.",
                },
                "query": {
                    "type": "string",
                    "description": "Substring query string (case-insensitive).",
                },
                "mode": {
                    "type": "string",
                    "description": "Search mode: 'path' searches object paths; 'commit' searches commit messages.",
                    "enum": ["path", "commit"],
                    "default": "path",
                },
            },
            "required": ["repo_id", "query"],
        },
    },
    {
        "name": "musehub_get_context",
        "server_side": True,
        "description": (
            "Get the full AI context document for a MuseHub repository. "
            "This is the primary read-side interface for music generation agents: it returns "
            "a structured summary of the repo's musical state — branches, recent commits, "
            "artifact inventory, and repo metadata — in a single call. "
            "Feed this document to the agent before generating new music to ensure "
            "harmonic and structural coherence with existing work. "
            "Example: musehub_get_context(repo_id='a3f2-...')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_id": {
                    "type": "string",
                    "description": "UUID of the MuseHub repository.",
                },
            },
            "required": ["repo_id"],
        },
    },
]

MUSEHUB_TOOL_NAMES: set[str] = {t["name"] for t in MUSEHUB_TOOLS}
"""Set of all musehub_* tool names — used by the MCP server to route calls."""
