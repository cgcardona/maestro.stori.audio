"""muse cat-object — inspect a raw object in the Muse content-addressed store.

Mirrors ``git cat-file`` plumbing semantics for Muse's three object types:

- **object**   — a content-addressed file blob (``MuseCliObject``)
- **snapshot** — an immutable manifest mapping paths to object IDs
  (``MuseCliSnapshot``)
- **commit**   — a versioned record pointing to a snapshot and its parent
  (``MuseCliCommit``)

The command tries each table in order (object → snapshot → commit) until it
finds a match, so a full 64-character SHA-256 hash is always unambiguous.
Short prefixes are NOT supported — callers must supply the full hash (as
returned by ``muse log``, ``muse commit``, etc.).

Output modes
------------

Default (no flags) — human-readable metadata summary::

    type:       object
    object_id:  a1b2c3d4...
    size:       4096 bytes
    created_at: 2026-02-27T17:30:00+00:00

``-t / --type`` — print only the type string (one of ``object``,
``snapshot``, ``commit``) and exit, mirroring ``git cat-file -t``::

    object

``-p / --pretty`` — pretty-print the object contents:

- For **objects**: prints size and creation time (binary blobs have no
  text representation stored in the DB; the raw bytes live on disk).
- For **snapshots**: prints the manifest JSON (path → object_id mapping).
- For **commits**: prints all commit fields as indented JSON.

Agent use case
--------------
AI agents use ``muse cat-object`` to inspect object metadata before
deciding whether to re-fetch, export, or reference an artifact.  The
``-p`` flag gives structured JSON that agents can parse directly.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliObject, MuseCliSnapshot

logger = logging.getLogger(__name__)

app = typer.Typer()

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

ObjectType = str  # "object" | "snapshot" | "commit"


class CatObjectResult:
    """Structured result from looking up a Muse object by ID.

    Wraps whichever ORM row was found and records its type so renderers
    don't need to isinstance-check.

    Args:
        object_type: One of ``"object"``, ``"snapshot"``, or ``"commit"``.
        row:         The ORM row (``MuseCliObject | MuseCliSnapshot |
                     MuseCliCommit``).
    """

    def __init__(
        self,
        *,
        object_type: ObjectType,
        row: MuseCliObject | MuseCliSnapshot | MuseCliCommit,
    ) -> None:
        self.object_type = object_type
        self.row = row

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-compatible dict.

        The shape varies by object type and is intended for agent
        consumption via ``-p``.
        """
        if self.object_type == "object":
            obj = self.row
            assert isinstance(obj, MuseCliObject)
            return {
                "type": "object",
                "object_id": obj.object_id,
                "size_bytes": obj.size_bytes,
                "created_at": obj.created_at.isoformat(),
            }
        if self.object_type == "snapshot":
            snap = self.row
            assert isinstance(snap, MuseCliSnapshot)
            return {
                "type": "snapshot",
                "snapshot_id": snap.snapshot_id,
                "manifest": snap.manifest,
                "created_at": snap.created_at.isoformat(),
            }
        # commit
        commit = self.row
        assert isinstance(commit, MuseCliCommit)
        return {
            "type": "commit",
            "commit_id": commit.commit_id,
            "repo_id": commit.repo_id,
            "branch": commit.branch,
            "parent_commit_id": commit.parent_commit_id,
            "parent2_commit_id": commit.parent2_commit_id,
            "snapshot_id": commit.snapshot_id,
            "message": commit.message,
            "author": commit.author,
            "committed_at": commit.committed_at.isoformat(),
            "created_at": commit.created_at.isoformat(),
            "metadata": commit.commit_metadata,
        }


# ---------------------------------------------------------------------------
# Async core — fully injectable for tests
# ---------------------------------------------------------------------------


async def _lookup_object(
    session: AsyncSession,
    object_id: str,
) -> CatObjectResult | None:
    """Probe all three object tables and return the first match.

    Lookup order: MuseCliObject → MuseCliSnapshot → MuseCliCommit.
    Returns ``None`` when the ID is not found in any table.

    Args:
        session:   An open async DB session.
        object_id: The full SHA-256 hash to look up (64 hex chars).
    """
    obj = await session.get(MuseCliObject, object_id)
    if obj is not None:
        return CatObjectResult(object_type="object", row=obj)

    snap = await session.get(MuseCliSnapshot, object_id)
    if snap is not None:
        return CatObjectResult(object_type="snapshot", row=snap)

    commit = await session.get(MuseCliCommit, object_id)
    if commit is not None:
        return CatObjectResult(object_type="commit", row=commit)

    return None


async def _cat_object_async(
    *,
    session: AsyncSession,
    object_id: str,
    type_only: bool,
    pretty: bool,
) -> None:
    """Core cat-object logic — fully injectable for tests.

    Resolves *object_id* across all three Muse object tables and prints
    the requested representation.  Exits non-zero when the object is not found.

    Args:
        session:   Open async DB session.
        object_id: Full SHA-256 hash to look up.
        type_only: When ``True``, print only the type string and exit.
        pretty:    When ``True``, pretty-print the object's content as JSON.
    """
    result = await _lookup_object(session, object_id)
    if result is None:
        typer.echo(f"❌ Object not found: {object_id}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if type_only:
        typer.echo(result.object_type)
        return

    if pretty:
        typer.echo(json.dumps(result.to_dict(), indent=2))
        return

    # Default: human-readable metadata summary
    _render_metadata(result)


def _render_metadata(result: CatObjectResult) -> None:
    """Print a terse human-readable metadata block for the found object."""
    if result.object_type == "object":
        obj = result.row
        assert isinstance(obj, MuseCliObject)
        typer.echo(f"type:       object")
        typer.echo(f"object_id:  {obj.object_id}")
        typer.echo(f"size:       {obj.size_bytes} bytes")
        typer.echo(f"created_at: {obj.created_at.isoformat()}")
        return

    if result.object_type == "snapshot":
        snap = result.row
        assert isinstance(snap, MuseCliSnapshot)
        file_count = len(snap.manifest) if snap.manifest else 0
        typer.echo(f"type:        snapshot")
        typer.echo(f"snapshot_id: {snap.snapshot_id}")
        typer.echo(f"files:       {file_count}")
        typer.echo(f"created_at:  {snap.created_at.isoformat()}")
        return

    # commit
    commit = result.row
    assert isinstance(commit, MuseCliCommit)
    typer.echo(f"type:       commit")
    typer.echo(f"commit_id:  {commit.commit_id}")
    typer.echo(f"branch:     {commit.branch}")
    typer.echo(f"snapshot:   {commit.snapshot_id}")
    typer.echo(f"message:    {commit.message}")
    if commit.parent_commit_id:
        typer.echo(f"parent:     {commit.parent_commit_id}")
    typer.echo(f"committed_at: {commit.committed_at.isoformat()}")


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def cat_object(
    ctx: typer.Context,
    object_id: str = typer.Argument(
        ...,
        help="Full SHA-256 object ID to look up (64 hex characters).",
        metavar="<object-id>",
    ),
    type_only: bool = typer.Option(
        False,
        "-t",
        "--type",
        help="Print only the type of the object (object, snapshot, or commit).",
    ),
    pretty: bool = typer.Option(
        False,
        "-p",
        "--pretty",
        help=(
            "Pretty-print the object content as JSON. "
            "For snapshots: manifest. For commits: all fields. "
            "For objects: size and creation time."
        ),
    ),
) -> None:
    """Read and display a stored Muse object by its SHA-256 hash.

    Probes the object store (blob, snapshot, commit) for the given ID and
    prints a summary.  Use ``-t`` to get just the type, or ``-p`` to get
    the full JSON representation.
    """
    if type_only and pretty:
        typer.echo("❌ --type and --pretty are mutually exclusive.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _cat_object_async(
                session=session,
                object_id=object_id,
                type_only=type_only,
                pretty=pretty,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse cat-object failed: {exc}")
        logger.error("❌ muse cat-object error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
