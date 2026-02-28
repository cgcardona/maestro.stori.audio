"""muse write-tree — write the current muse-work/ state as a snapshot (tree) object.

Plumbing command that mirrors ``git write-tree``. It scans ``muse-work/``,
hashes all files, builds a deterministic snapshot manifest, persists both
the individual object rows and the snapshot row to Postgres, and prints the
``snapshot_id``.

Why this exists
---------------
Porcelain commands like ``muse commit`` bundle snapshot creation with commit
creation and branch-pointer updates.  Agents and tooling sometimes need the
snapshot object alone — e.g. to compare the current working tree against a
reference snapshot without recording history, or to pre-hash the tree before
deciding whether to commit.  ``muse write-tree`` exposes that primitive.

Key properties
--------------
- **Deterministic / idempotent**: same files → same ``snapshot_id``.  Running
  the command twice without changing any files outputs the same ID and makes
  exactly zero new DB writes (the upsert is a no-op).
- **No commit**: the HEAD pointer and branch refs are never modified.
- **Prefix filter**: ``--prefix PATH`` restricts the snapshot to files whose
  relative path starts with *PATH*, enabling per-instrument or per-section
  snapshots without committing unrelated work.
- **Empty-tree handling**: by default the command exits 1 when ``muse-work/``
  is absent or empty.  Pass ``--missing-ok`` to suppress the error and still
  emit a valid (empty) ``snapshot_id``.
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session, upsert_object, upsert_snapshot
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.snapshot import (
    build_snapshot_manifest,
    compute_snapshot_id,
    hash_file,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _write_tree_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    prefix: str | None = None,
    missing_ok: bool = False,
) -> str:
    """Hash the working tree, persist snapshot objects, and return the ``snapshot_id``.

    Args:
        root: Repo root directory (must contain ``muse-work/``).
        session: Open async DB session.  The caller is responsible for
            committing.  ``open_session()`` commits on clean exit.
        prefix: When set, restrict the snapshot to files whose repo-relative
            path starts with *prefix*.  The prefix is matched against paths
            of the form ``<prefix>/<rest>`` (no leading slash).
        missing_ok: When ``True``, an absent or empty ``muse-work/`` is not
            an error — the command writes an empty snapshot and exits 0.
            When ``False`` (default), an absent or empty tree exits 1.

    Returns:
        The 64-character sha256 hex digest that uniquely identifies this
        snapshot.  The same content always returns the same ID.

    Raises:
        typer.Exit: With ``USER_ERROR`` (1) when ``muse-work/`` is missing or
            empty and *missing_ok* is ``False``.
    """
    workdir = root / "muse-work"

    # ── Build manifest ───────────────────────────────────────────────────
    if not workdir.exists():
        if not missing_ok:
            typer.echo(
                "⚠️  No muse-work/ directory found. Generate some artifacts first.\n"
                "     Tip: run the Maestro stress test to populate muse-work/.\n"
                "     Or pass --missing-ok to allow an empty tree."
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)
        manifest: dict[str, str] = {}
    else:
        manifest = build_snapshot_manifest(workdir)

        # Apply prefix filter when requested.
        if prefix is not None:
            # Normalise: strip leading/trailing slashes so callers can pass
            # either "drums" or "drums/" and get identical results.
            norm_prefix = prefix.strip("/")
            manifest = {
                path: oid
                for path, oid in manifest.items()
                if path == norm_prefix or path.startswith(norm_prefix + "/")
            }

        if not manifest and not missing_ok:
            if prefix is not None:
                typer.echo(
                    f"⚠️  No files found under prefix '{prefix}' in muse-work/.\n"
                    "     Pass --missing-ok to allow an empty snapshot."
                )
            else:
                typer.echo(
                    "⚠️  muse-work/ is empty — no files to snapshot.\n"
                    "     Pass --missing-ok to allow an empty snapshot."
                )
            raise typer.Exit(code=ExitCode.USER_ERROR)

    snapshot_id = compute_snapshot_id(manifest)

    # ── Persist object rows (content-addressed, deduped by upsert) ───────
    for rel_path, object_id in manifest.items():
        file_path = workdir / rel_path
        size = file_path.stat().st_size
        await upsert_object(session, object_id=object_id, size_bytes=size)

    # ── Persist snapshot row ─────────────────────────────────────────────
    await upsert_snapshot(session, manifest=manifest, snapshot_id=snapshot_id)

    logger.info("✅ muse write-tree snapshot_id=%s files=%d", snapshot_id[:8], len(manifest))
    return snapshot_id


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def write_tree(
    ctx: typer.Context,
    prefix: Optional[str] = typer.Option(
        None,
        "--prefix",
        help=(
            "Only include files whose path (relative to muse-work/) starts "
            "with this prefix.  Example: --prefix drums/ snapshots only the "
            "drums sub-directory."
        ),
    ),
    missing_ok: bool = typer.Option(
        False,
        "--missing-ok",
        help=(
            "Do not fail when muse-work/ is absent or empty (or when --prefix "
            "matches no files).  The empty snapshot_id is still printed."
        ),
    ),
) -> None:
    """Write the current muse-work/ state as a snapshot (tree) object.

    Hashes all files in muse-work/, persists the object and snapshot rows,
    and prints the snapshot_id.  Does NOT create a commit or modify any
    branch ref.
    """
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            snapshot_id = await _write_tree_async(
                root=root,
                session=session,
                prefix=prefix,
                missing_ok=missing_ok,
            )
            typer.echo(snapshot_id)

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse write-tree failed: {exc}")
        logger.error("❌ muse write-tree error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
