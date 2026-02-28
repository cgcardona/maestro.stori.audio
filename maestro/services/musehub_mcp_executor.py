"""MuseHub MCP tool executor — server-side logic for all musehub_* MCP tools.

This module is the execution backend for MuseHub browsing tools exposed via
MCP.  Each public function opens its own DB session via ``AsyncSessionLocal``,
delegates to ``musehub_repository`` for persistence access, and returns a
typed ``MusehubToolResult``.

Design contract
---------------
- All functions are async and return ``MusehubToolResult`` on both success
  and failure (no exceptions propagate to the MCP server).
- ``MusehubToolResult.ok`` distinguishes success from failure.
- ``MusehubToolResult.error_code`` is one of: ``"not_found"``,
  ``"invalid_dimension"``, ``"invalid_mode"``, ``"db_unavailable"``.
- Callers (``MaestroMCPServer._execute_musehub_tool``) pattern-match on
  these codes to build appropriate ``MCPContentBlock`` responses.
- This module must NOT import MCP protocol types — it is pure service layer.

``AsyncSessionLocal`` is imported at module level so tests can patch it as
``maestro.services.musehub_mcp_executor.AsyncSessionLocal``.
"""
from __future__ import annotations

import logging
import mimetypes
import os
from dataclasses import dataclass, field
from typing import Literal

from maestro.contracts.json_types import JSONValue
from maestro.db.database import AsyncSessionLocal
from maestro.services import musehub_repository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

MusehubErrorCode = Literal[
    "not_found",
    "invalid_dimension",
    "invalid_mode",
    "db_unavailable",
]
"""Enumeration of error codes returned by MuseHub MCP executors.

Callers pattern-match on these to build appropriate error messages:
  - ``not_found``         — repo or object does not exist
  - ``invalid_dimension`` — unrecognised analysis dimension
  - ``invalid_mode``      — unrecognised search mode
  - ``db_unavailable``    — DB session factory not initialised (startup race)
"""


@dataclass(frozen=True)
class MusehubToolResult:
    """Result of executing a single musehub_* MCP tool.

    ``ok`` is the primary success/failure signal.  On success, ``data``
    holds the JSON-serialisable payload for the MCP content block.  On
    failure, ``error_code`` and ``error_message`` describe what went wrong.

    This type is the contract between the executor functions and the MCP
    server's routing layer — do not bypass it with raw exceptions.
    """

    ok: bool
    data: dict[str, JSONValue] = field(default_factory=dict)
    error_code: MusehubErrorCode | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_EXTRA_MIME: dict[str, str] = {
    ".mid": "audio/midi",
    ".midi": "audio/midi",
    ".mp3": "audio/mpeg",
    ".webp": "image/webp",
}


def _mime_for_path(path: str) -> str:
    """Resolve MIME type from a file path extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _EXTRA_MIME:
        return _EXTRA_MIME[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------


async def execute_browse_repo(repo_id: str) -> MusehubToolResult:
    """Return repo metadata, branch list, and the 10 most recent commits.

    This is the entry-point tool for orienting an agent before it calls more
    specific tools.  It aggregates three repository queries into one response
    to minimise round-trips for the common "explore a new repo" case.

    Args:
        repo_id: UUID of the target MuseHub repository.

    Returns:
        ``MusehubToolResult`` with ``data`` containing ``repo``, ``branches``,
        and ``recent_commits`` keys, or ``error_code="not_found"`` if the
        repo does not exist.
    """
    async with AsyncSessionLocal() as session:
        repo = await musehub_repository.get_repo(session, repo_id)
        if repo is None:
            return MusehubToolResult(
                ok=False,
                error_code="not_found",
                error_message=f"Repository '{repo_id}' not found.",
            )

        branches = await musehub_repository.list_branches(session, repo_id)
        commits, total = await musehub_repository.list_commits(
            session, repo_id, limit=10
        )

        data: dict[str, JSONValue] = {
            "repo": {
                "repo_id": repo.repo_id,
                "name": repo.name,
                "visibility": repo.visibility,
                "owner_user_id": repo.owner_user_id,
                "clone_url": repo.clone_url,
                "created_at": repo.created_at.isoformat(),
            },
            "branches": [
                {
                    "branch_id": b.branch_id,
                    "name": b.name,
                    "head_commit_id": b.head_commit_id,
                }
                for b in branches
            ],
            "recent_commits": [
                {
                    "commit_id": c.commit_id,
                    "branch": c.branch,
                    "message": c.message,
                    "author": c.author,
                    "timestamp": c.timestamp.isoformat(),
                }
                for c in commits
            ],
            "total_commits": total,
            "branch_count": len(branches),
        }
        return MusehubToolResult(ok=True, data=data)


async def execute_list_branches(repo_id: str) -> MusehubToolResult:
    """Return all branches for a MuseHub repository.

    Agents call this before ``execute_list_commits`` to discover available
    branch names and their current head commit IDs.

    Args:
        repo_id: UUID of the target MuseHub repository.

    Returns:
        ``MusehubToolResult`` with ``data.branches`` as a list of branch
        dicts, or ``error_code="not_found"`` if the repo does not exist.
    """
    from maestro.db.database import AsyncSessionLocal
    from maestro.services import musehub_repository

    async with AsyncSessionLocal() as session:
        repo = await musehub_repository.get_repo(session, repo_id)
        if repo is None:
            return MusehubToolResult(
                ok=False,
                error_code="not_found",
                error_message=f"Repository '{repo_id}' not found.",
            )

        branches = await musehub_repository.list_branches(session, repo_id)
        data: dict[str, JSONValue] = {
            "repo_id": repo_id,
            "branches": [
                {
                    "branch_id": b.branch_id,
                    "name": b.name,
                    "head_commit_id": b.head_commit_id,
                }
                for b in branches
            ],
            "branch_count": len(branches),
        }
        return MusehubToolResult(ok=True, data=data)


async def execute_list_commits(
    repo_id: str,
    branch: str | None = None,
    limit: int = 20,
) -> MusehubToolResult:
    """Return paginated commits for a MuseHub repository, newest first.

    Args:
        repo_id: UUID of the target MuseHub repository.
        branch:  Optional branch name filter; None returns across all branches.
        limit:   Maximum commits to return (clamped to 1–100).

    Returns:
        ``MusehubToolResult`` with ``data.commits`` and ``data.total``,
        or ``error_code="not_found"`` if the repo does not exist.
    """
    from maestro.db.database import AsyncSessionLocal
    from maestro.services import musehub_repository

    limit = max(1, min(limit, 100))

    async with AsyncSessionLocal() as session:
        repo = await musehub_repository.get_repo(session, repo_id)
        if repo is None:
            return MusehubToolResult(
                ok=False,
                error_code="not_found",
                error_message=f"Repository '{repo_id}' not found.",
            )

        commits, total = await musehub_repository.list_commits(
            session, repo_id, branch=branch, limit=limit
        )

        commit_list: list[JSONValue] = []
        for c in commits:
            # parent_ids is list[str]; build list[JSONValue] explicitly (list invariance).
            parent_ids_json: list[JSONValue] = []
            for pid in c.parent_ids:
                parent_ids_json.append(pid)
            commit_list.append({
                "commit_id": c.commit_id,
                "branch": c.branch,
                "parent_ids": parent_ids_json,
                "message": c.message,
                "author": c.author,
                "timestamp": c.timestamp.isoformat(),
                "snapshot_id": c.snapshot_id,
            })

        data: dict[str, JSONValue] = {
            "repo_id": repo_id,
            "branch_filter": branch,
            "commits": commit_list,
            "returned": len(commits),
            "total": total,
        }
        return MusehubToolResult(ok=True, data=data)


async def execute_read_file(repo_id: str, object_id: str) -> MusehubToolResult:
    """Return metadata for a stored artifact in a MuseHub repo.

    Returns path, size_bytes, mime_type, and object_id.  Binary content is
    intentionally excluded — MCP tool responses must be text-safe JSON.
    Agents that need the raw bytes should use the HTTP objects endpoint.

    Args:
        repo_id:   UUID of the target MuseHub repository.
        object_id: Content-addressed ID (e.g. ``sha256:abc...``).

    Returns:
        ``MusehubToolResult`` with file metadata, or ``error_code="not_found"``
        if the repo or object does not exist.
    """
    from maestro.db.database import AsyncSessionLocal
    from maestro.services import musehub_repository

    async with AsyncSessionLocal() as session:
        repo = await musehub_repository.get_repo(session, repo_id)
        if repo is None:
            return MusehubToolResult(
                ok=False,
                error_code="not_found",
                error_message=f"Repository '{repo_id}' not found.",
            )

        obj = await musehub_repository.get_object_row(session, repo_id, object_id)
        if obj is None:
            return MusehubToolResult(
                ok=False,
                error_code="not_found",
                error_message=f"Object '{object_id}' not found in repository '{repo_id}'.",
            )

        data: dict[str, JSONValue] = {
            "object_id": obj.object_id,
            "repo_id": repo_id,
            "path": obj.path,
            "size_bytes": obj.size_bytes,
            "mime_type": _mime_for_path(obj.path),
            "created_at": obj.created_at.isoformat(),
        }
        return MusehubToolResult(ok=True, data=data)


async def execute_get_analysis(
    repo_id: str,
    dimension: str = "overview",
) -> MusehubToolResult:
    """Return structured analysis for a MuseHub repository.

    Supported dimensions:
    - ``overview``  — repo stats: branch count, commit count, object count,
                      most active author, most recent commit timestamp.
    - ``commits``   — commit activity: total, per-branch breakdown, author
                      distribution, and a sample of the most recent messages.
    - ``objects``   — artifact inventory: total size, per-MIME-type counts
                      and sizes, and a sample of object paths.

    MIDI audio analysis (key, tempo, harmonic content) requires Storpheus
    integration and is not yet available; those fields will be ``null``.

    Args:
        repo_id:   UUID of the target MuseHub repository.
        dimension: Analysis dimension — one of ``overview``, ``commits``,
                   ``objects``.

    Returns:
        ``MusehubToolResult`` with analysis data, or an error code on failure.
    """
    from maestro.db.database import AsyncSessionLocal
    from maestro.services import musehub_repository

    valid_dimensions = {"overview", "commits", "objects"}
    if dimension not in valid_dimensions:
        return MusehubToolResult(
            ok=False,
            error_code="invalid_dimension",
            error_message=(
                f"Unknown dimension '{dimension}'. "
                f"Valid values: {', '.join(sorted(valid_dimensions))}."
            ),
        )

    async with AsyncSessionLocal() as session:
        repo = await musehub_repository.get_repo(session, repo_id)
        if repo is None:
            return MusehubToolResult(
                ok=False,
                error_code="not_found",
                error_message=f"Repository '{repo_id}' not found.",
            )

        if dimension == "overview":
            branches = await musehub_repository.list_branches(session, repo_id)
            commits, total_commits = await musehub_repository.list_commits(
                session, repo_id, limit=1
            )
            objects = await musehub_repository.list_objects(session, repo_id)

            last_commit_at: JSONValue = None
            most_recent_author: JSONValue = None
            if commits:
                last_commit_at = commits[0].timestamp.isoformat()
                most_recent_author = commits[0].author

            data: dict[str, JSONValue] = {
                "repo_id": repo_id,
                "dimension": "overview",
                "repo_name": repo.name,
                "visibility": repo.visibility,
                "branch_count": len(branches),
                "commit_count": total_commits,
                "object_count": len(objects),
                "last_commit_at": last_commit_at,
                "most_recent_author": most_recent_author,
                "midi_analysis": None,
            }
            return MusehubToolResult(ok=True, data=data)

        if dimension == "commits":
            all_commits, total = await musehub_repository.list_commits(
                session, repo_id, limit=100
            )

            by_branch: dict[str, int] = {}
            by_author: dict[str, int] = {}
            for c in all_commits:
                by_branch[c.branch] = by_branch.get(c.branch, 0) + 1
                by_author[c.author] = by_author.get(c.author, 0) + 1

            data = {
                "repo_id": repo_id,
                "dimension": "commits",
                "total_commits": total,
                "commits_in_sample": len(all_commits),
                "by_branch": {k: v for k, v in by_branch.items()},
                "by_author": {k: v for k, v in by_author.items()},
                "recent_messages": [c.message for c in all_commits[:5]],
            }
            return MusehubToolResult(ok=True, data=data)

        # dimension == "objects"
        objects = await musehub_repository.list_objects(session, repo_id)

        by_mime: dict[str, int] = {}
        size_by_mime: dict[str, int] = {}
        total_size = 0
        for obj in objects:
            mime = _mime_for_path(obj.path)
            by_mime[mime] = by_mime.get(mime, 0) + 1
            size_by_mime[mime] = size_by_mime.get(mime, 0) + obj.size_bytes
            total_size += obj.size_bytes

        data = {
            "repo_id": repo_id,
            "dimension": "objects",
            "total_objects": len(objects),
            "total_size_bytes": total_size,
            "by_mime_type": {k: v for k, v in by_mime.items()},
            "size_by_mime_type": {k: v for k, v in size_by_mime.items()},
            "sample_paths": [obj.path for obj in objects[:10]],
        }
        return MusehubToolResult(ok=True, data=data)


async def execute_search(
    repo_id: str,
    query: str,
    mode: str = "path",
) -> MusehubToolResult:
    """Search within a MuseHub repository by substring match.

    Search is case-insensitive substring matching.  Two modes are supported:
    - ``path``   — matches object file paths (e.g. ``tracks/jazz_4b.mid``).
    - ``commit`` — matches commit messages (e.g. ``add bass intro``).

    The search operates on the full in-memory dataset (no DB-level LIKE query)
    so results are consistent across database backends.  For very large repos
    (>10 k objects/commits) this may be slow — index-backed search is a
    planned future enhancement.

    Args:
        repo_id: UUID of the target MuseHub repository.
        query:   Case-insensitive substring to search for.
        mode:    ``"path"`` or ``"commit"``.

    Returns:
        ``MusehubToolResult`` with ``data.results`` list, or an error on failure.
    """
    from maestro.db.database import AsyncSessionLocal
    from maestro.services import musehub_repository

    valid_modes = {"path", "commit"}
    if mode not in valid_modes:
        return MusehubToolResult(
            ok=False,
            error_code="invalid_mode",
            error_message=(
                f"Unknown search mode '{mode}'. "
                f"Valid values: {', '.join(sorted(valid_modes))}."
            ),
        )

    q = query.lower()

    async with AsyncSessionLocal() as session:
        repo = await musehub_repository.get_repo(session, repo_id)
        if repo is None:
            return MusehubToolResult(
                ok=False,
                error_code="not_found",
                error_message=f"Repository '{repo_id}' not found.",
            )

        if mode == "path":
            objects = await musehub_repository.list_objects(session, repo_id)
            results: list[JSONValue] = [
                {
                    "object_id": obj.object_id,
                    "path": obj.path,
                    "size_bytes": obj.size_bytes,
                    "mime_type": _mime_for_path(obj.path),
                }
                for obj in objects
                if q in obj.path.lower()
            ]
        else:  # mode == "commit"
            commits, _ = await musehub_repository.list_commits(
                session, repo_id, limit=100
            )
            results = [
                {
                    "commit_id": c.commit_id,
                    "branch": c.branch,
                    "message": c.message,
                    "author": c.author,
                    "timestamp": c.timestamp.isoformat(),
                }
                for c in commits
                if q in c.message.lower()
            ]

        data: dict[str, JSONValue] = {
            "repo_id": repo_id,
            "query": query,
            "mode": mode,
            "result_count": len(results),
            "results": results,
        }
        return MusehubToolResult(ok=True, data=data)


async def execute_get_context(repo_id: str) -> MusehubToolResult:
    """Return the full AI context document for a MuseHub repository.

    This is the primary read-side interface for music generation agents.
    It aggregates repo metadata, all branches, the 10 most recent commits
    across all branches, and the full artifact inventory into a single
    structured document — ready to paste into an agent's context window.

    Feed this document to the agent before generating new music to ensure
    harmonic and structural coherence with existing work in the repository.

    Musical analysis fields (key, tempo, time_signature) are ``null`` until
    Storpheus MIDI analysis integration is complete.

    Args:
        repo_id: UUID of the target MuseHub repository.

    Returns:
        ``MusehubToolResult`` with ``data.context`` (the full context doc),
        or ``error_code="not_found"`` if the repo does not exist.
    """
    from maestro.db.database import AsyncSessionLocal
    from maestro.services import musehub_repository

    async with AsyncSessionLocal() as session:
        repo = await musehub_repository.get_repo(session, repo_id)
        if repo is None:
            return MusehubToolResult(
                ok=False,
                error_code="not_found",
                error_message=f"Repository '{repo_id}' not found.",
            )

        branches = await musehub_repository.list_branches(session, repo_id)
        commits, total_commits = await musehub_repository.list_commits(
            session, repo_id, limit=10
        )
        objects = await musehub_repository.list_objects(session, repo_id)

        by_mime: dict[str, int] = {}
        for obj in objects:
            mime = _mime_for_path(obj.path)
            by_mime[mime] = by_mime.get(mime, 0) + 1

        context: dict[str, JSONValue] = {
            "repo": {
                "repo_id": repo.repo_id,
                "name": repo.name,
                "visibility": repo.visibility,
                "owner_user_id": repo.owner_user_id,
                "created_at": repo.created_at.isoformat(),
            },
            "branches": [
                {
                    "name": b.name,
                    "head_commit_id": b.head_commit_id,
                }
                for b in branches
            ],
            "recent_commits": [
                {
                    "commit_id": c.commit_id,
                    "branch": c.branch,
                    "message": c.message,
                    "author": c.author,
                    "timestamp": c.timestamp.isoformat(),
                }
                for c in commits
            ],
            "commit_stats": {
                "total": total_commits,
                "shown": len(commits),
            },
            "artifacts": {
                "total_count": len(objects),
                "by_mime_type": {k: v for k, v in by_mime.items()},
                "paths": [obj.path for obj in objects],
            },
            "musical_analysis": {
                "key": None,
                "tempo": None,
                "time_signature": None,
                "note": (
                    "Musical analysis requires Storpheus MIDI integration "
                    "(not yet available — fields will be populated in a future release)."
                ),
            },
        }

        data: dict[str, JSONValue] = {
            "repo_id": repo_id,
            "context": context,
        }
        return MusehubToolResult(ok=True, data=data)
