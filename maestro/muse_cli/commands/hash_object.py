"""muse hash-object — compute and optionally store a Muse content-addressed object.

Mirrors ``git hash-object`` plumbing semantics: given a file path (or stdin),
compute the SHA-256 hash of its raw bytes and print it.  With ``-w`` the object
is written into both the local on-disk store (``.muse/objects/``) and the
Postgres ``muse_cli_objects`` table so it can be referenced by future
``muse commit-tree`` or ``muse cat-object`` calls.

Usage examples
--------------

Print the hash without storing::

    muse hash-object muse-work/drums/kick.mid

Hash and store in the object store::

    muse hash-object -w muse-work/drums/kick.mid

Hash content from stdin::

    echo "data" | muse hash-object --stdin

The hash produced here is identical to what ``muse commit`` would compute for
the same file (sha256 of raw bytes, lowercase hex, 64 characters).

Agent use case
--------------
AI agents use ``muse hash-object`` to pre-check whether a file is already
stored before uploading it, or to derive the object ID that will be assigned
when the file is committed — useful for building optimistic pipelines.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import pathlib
import sys

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliObject
from maestro.muse_cli.object_store import write_object

logger = logging.getLogger(__name__)

app = typer.Typer()

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class HashObjectResult:
    """Structured result from ``muse hash-object``.

    Records the computed SHA-256 digest and whether the object was written
    to the store.

    Args:
        object_id: The 64-character lowercase hex SHA-256 digest.
        stored:    ``True`` when the object was written to the store
                   (``-w`` flag), ``False`` for a compute-only run.
        already_existed: ``True`` when ``-w`` was given but the object was
                         already present in the store (idempotent).
    """

    def __init__(
        self,
        *,
        object_id: str,
        stored: bool,
        already_existed: bool = False,
    ) -> None:
        self.object_id = object_id
        self.stored = stored
        self.already_existed = already_existed


# ---------------------------------------------------------------------------
# Pure hash helper
# ---------------------------------------------------------------------------


def hash_bytes(content: bytes) -> str:
    """Return the SHA-256 hex digest of *content*.

    Identical to the hash ``muse commit`` computes for each tracked file,
    ensuring content-addressability across all Muse plumbing commands.

    Args:
        content: Raw bytes to hash.

    Returns:
        64-character lowercase hex string.
    """
    return hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# Async core — fully injectable for tests
# ---------------------------------------------------------------------------


async def _hash_object_async(
    *,
    session: AsyncSession,
    content: bytes,
    write: bool,
    repo_root: pathlib.Path | None = None,
) -> HashObjectResult:
    """Core hash-object logic — compute SHA-256 and optionally persist.

    When *write* is ``True``:

    1. Write the raw bytes into the local on-disk store (``.muse/objects/``).
    2. Upsert a ``MuseCliObject`` row into Postgres with the object ID and
       byte count.  The upsert is idempotent: inserting the same object twice
       is a no-op.

    Args:
        session:   Open async DB session (used only when *write* is ``True``).
        content:   Raw bytes to hash (and optionally store).
        write:     When ``True``, persist the object to the store.
        repo_root: Path to the Muse repo root for the on-disk store.  When
                   ``None`` the repo root is resolved from the current
                   working directory via :func:`~maestro.muse_cli._repo.require_repo`.

    Returns:
        :class:`HashObjectResult` with the computed ID and storage status.
    """
    object_id = hash_bytes(content)

    if not write:
        return HashObjectResult(object_id=object_id, stored=False)

    # Check whether the DB row already exists before inserting.
    existing = await session.get(MuseCliObject, object_id)
    already_existed = existing is not None

    if not already_existed:
        row = MuseCliObject(object_id=object_id, size_bytes=len(content))
        session.add(row)
        await session.flush()
        logger.info("✅ Stored object %s (%d bytes)", object_id[:8], len(content))
    else:
        logger.debug("⚠️ Object %s already in DB — skipped", object_id[:8])

    # Always attempt on-disk write (idempotent).
    root = repo_root if repo_root is not None else require_repo()
    write_object(root, object_id, content)

    return HashObjectResult(
        object_id=object_id,
        stored=True,
        already_existed=already_existed,
    )


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def hash_object(
    ctx: typer.Context,
    file: str = typer.Argument(
        "",
        help="Path to the file to hash. Omit when using --stdin.",
        metavar="<file>",
    ),
    write: bool = typer.Option(
        False,
        "-w",
        "--write",
        help=(
            "Write the object into the content-addressed store "
            "(.muse/objects/) and the muse_cli_objects table."
        ),
    ),
    stdin: bool = typer.Option(
        False,
        "--stdin",
        help="Read content from stdin instead of a file.",
    ),
) -> None:
    """Compute the SHA-256 object ID for a file (or stdin content).

    Prints the 64-character hex hash to stdout.  With ``-w``, the object is
    also written to the local store and the Postgres ``muse_cli_objects``
    table so it can be referenced by other plumbing commands.

    The hash is identical to the one ``muse commit`` would assign to the same
    file, ensuring cross-command content-addressability.
    """
    if stdin and file:
        typer.echo("❌ Provide a file path OR --stdin, not both.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if not stdin and not file:
        typer.echo("❌ Provide a file path or --stdin.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    require_repo()

    # Read content — either from the file or from stdin.
    if stdin:
        content = sys.stdin.buffer.read()
    else:
        src = pathlib.Path(file)
        if not src.exists():
            typer.echo(f"❌ File not found: {file}")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        if not src.is_file():
            typer.echo(f"❌ Not a regular file: {file}")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        content = src.read_bytes()

    async def _run() -> None:
        if write:
            async with open_session() as session:
                result = await _hash_object_async(
                    session=session,
                    content=content,
                    write=True,
                )
                await session.commit()
        else:
            # Compute-only: no DB access needed.
            result = HashObjectResult(
                object_id=hash_bytes(content),
                stored=False,
            )
        typer.echo(result.object_id)

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse hash-object failed: {exc}")
        logger.error("❌ muse hash-object error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
