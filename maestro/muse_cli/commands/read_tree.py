"""muse read-tree — read a snapshot into the muse-work/ directory.

This is a low-level plumbing command (analogous to ``git read-tree``) that
hydrates ``muse-work/`` from a stored snapshot manifest WITHOUT touching any
branch or HEAD references.  It is intentionally destructive by default: any
file in ``muse-work/`` whose path appears in the snapshot is overwritten.

Use cases
---------
- Inspect an older snapshot without losing your current branch position.
- Restore a specific state before running ``muse commit``.
- Agent tooling that needs to populate a clean working directory from a
  known snapshot (e.g., after ``muse pull`` or before an automated test).

Flags
-----
``<snapshot_id>``
    Positional — the full or abbreviated (≥ 4 chars) snapshot SHA to restore.

``--dry-run``
    Print the list of files that *would* be written without touching disk.
    Exit 0 on success.

``--reset``
    Remove all existing files from ``muse-work/`` before populating.
    Without this flag, only the files referenced by the snapshot are
    written (files not in the snapshot are left untouched).

Algorithm
---------
1. Resolve *snapshot_id* — accept full 64-char SHA or a ≥4-char prefix via
   a DB prefix scan.
2. Fetch the snapshot manifest: ``{rel_path → object_id}``.
3. For each entry, read the object from ``.muse/objects/<object_id>``.
4. If ``--reset``: remove all files currently in ``muse-work/``.
5. Write each file to ``muse-work/<rel_path>`` (creating parent dirs).
6. Does NOT update ``.muse/HEAD`` or any branch ref.
"""
from __future__ import annotations

import asyncio
import logging
import pathlib

import typer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliSnapshot
from maestro.muse_cli.object_store import read_object

logger = logging.getLogger(__name__)

app = typer.Typer()

# Minimum prefix length for abbreviated snapshot IDs.
_MIN_PREFIX_LEN = 4


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class ReadTreeResult:
    """Structured result of a ``read-tree`` operation.

    Carries the set of files written (or that would have been written in
    dry-run mode) so that tests can assert on content without inspecting
    the filesystem.

    Attributes:
        snapshot_id:   Full 64-char snapshot ID that was resolved.
        files_written: Relative paths of files written to muse-work/.
        dry_run:       True when ``--dry-run`` was requested (no writes made).
        reset:         True when ``--reset`` cleared muse-work/ first.
    """

    def __init__(
        self,
        *,
        snapshot_id: str,
        files_written: list[str],
        dry_run: bool,
        reset: bool,
    ) -> None:
        self.snapshot_id = snapshot_id
        self.files_written = files_written
        self.dry_run = dry_run
        self.reset = reset

    def __repr__(self) -> str:
        return (
            f"<ReadTreeResult snap={self.snapshot_id[:8]}"
            f" files={len(self.files_written)}"
            f" dry_run={self.dry_run} reset={self.reset}>"
        )


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _resolve_snapshot(
    session: AsyncSession,
    snapshot_id: str,
) -> MuseCliSnapshot | None:
    """Resolve *snapshot_id* to a :class:`MuseCliSnapshot` row.

    Accepts both full 64-char SHAs and abbreviated prefixes (≥ 4 chars).
    When a prefix is provided, a table scan is used to find the first
    matching row (acceptable for CLI use; snapshot IDs are content-addressed
    so collisions are astronomically unlikely).

    Returns ``None`` when no snapshot matches.
    """
    if len(snapshot_id) == 64:
        return await session.get(MuseCliSnapshot, snapshot_id)

    # Abbreviated prefix scan.
    result = await session.execute(
        select(MuseCliSnapshot).where(
            MuseCliSnapshot.snapshot_id.startswith(snapshot_id)
        )
    )
    return result.scalars().first()


async def _read_tree_async(
    *,
    snapshot_id: str,
    root: pathlib.Path,
    session: AsyncSession,
    dry_run: bool = False,
    reset: bool = False,
) -> ReadTreeResult:
    """Core read-tree logic — fully injectable for tests.

    Resolves the snapshot from the DB, then hydrates ``muse-work/`` from the
    local object store.  Raises ``typer.Exit`` with a clean exit code on any
    user-facing error so the Typer callback can surface it without a traceback.

    Args:
        snapshot_id: Full or abbreviated snapshot ID to restore.
        root:        Muse repository root (directory containing ``.muse/``).
        session:     Open async DB session used for snapshot lookup.
        dry_run:     When True, report what would be written but do nothing.
        reset:       When True, clear muse-work/ before populating.

    Returns:
        :class:`ReadTreeResult` describing what was (or would have been) done.

    Raises:
        typer.Exit: On user-facing errors (unknown snapshot, missing objects).
    """
    if len(snapshot_id) < _MIN_PREFIX_LEN:
        typer.echo(
            f"❌ Snapshot ID too short: '{snapshot_id}' "
            f"(need at least {_MIN_PREFIX_LEN} hex chars)."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── 1. Resolve snapshot ──────────────────────────────────────────────
    snapshot = await _resolve_snapshot(session, snapshot_id.lower())
    if snapshot is None:
        typer.echo(f"❌ No snapshot found matching '{snapshot_id[:8]}'.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest: dict[str, str] = dict(snapshot.manifest)
    if not manifest:
        typer.echo(f"⚠️  Snapshot {snapshot.snapshot_id[:8]} has an empty manifest.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    workdir = root / "muse-work"
    sorted_paths = sorted(manifest.keys())

    # ── 2. Dry-run: list files and exit ─────────────────────────────────
    if dry_run:
        typer.echo(f"Snapshot {snapshot.snapshot_id[:8]} — {len(manifest)} file(s):")
        for rel_path in sorted_paths:
            obj_id = manifest[rel_path]
            typer.echo(f"  {rel_path}  ({obj_id[:8]})")
        return ReadTreeResult(
            snapshot_id=snapshot.snapshot_id,
            files_written=sorted_paths,
            dry_run=True,
            reset=reset,
        )

    # ── 3. Pre-flight: verify all objects are present in the store ───────
    missing: list[str] = []
    for rel_path, object_id in manifest.items():
        content = read_object(root, object_id)
        if content is None:
            missing.append(rel_path)

    if missing:
        typer.echo(
            f"❌ {len(missing)} object(s) missing from local store "
            f"(snapshot {snapshot.snapshot_id[:8]}):"
        )
        for p in sorted(missing):
            typer.echo(f"   {p}  ({manifest[p][:8]})")
        typer.echo(
            "   Objects are written by 'muse commit'. "
            "Re-commit this snapshot or fetch it via 'muse pull'."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── 4. Optional reset: remove all files from muse-work/ ─────────────
    if reset and workdir.exists():
        removed = 0
        for existing_file in sorted(workdir.rglob("*")):
            if existing_file.is_file():
                existing_file.unlink()
                removed += 1
        logger.info("⚠️  --reset: removed %d file(s) from muse-work/", removed)
        # Clean up empty directories left behind.
        for d in sorted(workdir.rglob("*"), reverse=True):
            if d.is_dir():
                try:
                    d.rmdir()
                except OSError:
                    pass  # Not empty — leave it.

    # ── 5. Write objects to muse-work/ ──────────────────────────────────
    workdir.mkdir(parents=True, exist_ok=True)
    files_written: list[str] = []

    for rel_path in sorted_paths:
        object_id = manifest[rel_path]
        content = read_object(root, object_id)
        # content is guaranteed non-None by the pre-flight check above.
        assert content is not None

        dest = workdir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        files_written.append(rel_path)
        logger.debug("✅ Wrote %s (%d bytes)", rel_path, len(content))

    return ReadTreeResult(
        snapshot_id=snapshot.snapshot_id,
        files_written=files_written,
        dry_run=False,
        reset=reset,
    )


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def read_tree(
    ctx: typer.Context,
    snapshot_id: str = typer.Argument(
        ...,
        help=(
            "Snapshot ID to restore into muse-work/. "
            "Accepts the full 64-char SHA or an abbreviated prefix (≥ 4 chars)."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the file list without writing anything.",
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Remove all existing muse-work/ files before populating.",
    ),
) -> None:
    """Read a snapshot into muse-work/ without updating HEAD.

    Populates muse-work/ with the exact file set recorded in SNAPSHOT_ID.
    Files not referenced by the snapshot are left untouched unless --reset
    is specified.  HEAD and branch refs are never modified.
    """
    root = require_repo()

    async def _run() -> ReadTreeResult:
        async with open_session() as session:
            return await _read_tree_async(
                snapshot_id=snapshot_id,
                root=root,
                session=session,
                dry_run=dry_run,
                reset=reset,
            )

    try:
        result = asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse read-tree failed: {exc}")
        logger.error("❌ muse read-tree error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if result.dry_run:
        return

    action = "reset and populated" if result.reset else "populated"
    typer.echo(
        f"✅ muse-work/ {action} from snapshot {result.snapshot_id[:8]} "
        f"({len(result.files_written)} file(s))."
    )
    logger.info(
        "✅ read-tree snapshot=%s files=%d reset=%s",
        result.snapshot_id[:8],
        len(result.files_written),
        result.reset,
    )
