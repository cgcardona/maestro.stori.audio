"""muse resolve — mark a conflicted file as resolved.

Workflow
--------
When ``muse merge`` encounters conflicts it writes ``.muse/MERGE_STATE.json``
and exits.  The user then inspects the listed conflict paths and resolves each
one:

- ``--ours``:   Keep the current branch's version already in ``muse-work/``.
                The file is left untouched; the path is removed from the conflict
                list in ``MERGE_STATE.json``.

- ``--theirs``: Accept the incoming branch's version.  This command fetches
                the object from the local store (written when the other branch's
                commits were made) and writes it to ``muse-work/<path>`` before
                removing the path from the conflict list.

After resolving each conflict, run ``muse merge --continue`` to create the
merge commit.

Resolution strategies
---------------------
Both strategies ultimately remove the path from ``conflict_paths`` in
``MERGE_STATE.json``.  When the list reaches zero, ``muse merge --continue``
can proceed.

The ``--theirs`` strategy requires the theirs commit's objects to be present
in the local ``.muse/objects/`` store.  Objects are written there when commits
are made locally; ``muse pull`` fetches them from the remote.
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
from typing import TYPE_CHECKING

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.merge_engine import (
    apply_resolution,
    read_merge_state,
    write_merge_state,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

app = typer.Typer()


# ---------------------------------------------------------------------------
# Testable async core — no Typer coupling
# ---------------------------------------------------------------------------


async def resolve_conflict_async(
    *,
    file_path: str,
    ours: bool,
    root: pathlib.Path,
    session: AsyncSession,
) -> None:
    """Mark *file_path* as resolved in ``.muse/MERGE_STATE.json``.

    For ``--ours`` no file change is made — the current ``muse-work/`` content
    is accepted as-is.  For ``--theirs`` this function fetches the theirs
    branch's object from the local store and writes it to
    ``muse-work/<file_path>``.

    Args:
        file_path: Path of the conflicted file.  Accepted as:
                   - absolute path (converted to relative to ``muse-work/``)
                   - path relative to ``muse-work/`` (e.g. ``meta/foo.json``)
                   - path relative to repo root (e.g. ``muse-work/meta/foo.json``)
        ours:      ``True`` to accept ours (no file change); ``False`` to
                   accept theirs (object is fetched from local store and written
                   to ``muse-work/<file_path>``).
        root:      Repository root containing ``.muse/``.
        session:   Open async DB session (used for ``--theirs`` to look up the
                   theirs commit's snapshot manifest).

    Raises:
        :class:`typer.Exit`: On user errors (no merge in progress, path not
                             in conflict list, object missing from local store).
    """
    merge_state = read_merge_state(root)
    if merge_state is None:
        typer.echo("❌ No merge in progress. Nothing to resolve.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Normalise path to be relative to muse-work/.
    workdir = root / "muse-work"
    abs_target = pathlib.Path(file_path)
    if not abs_target.is_absolute():
        # Try treating as relative to repo root first, then fall back to muse-work.
        candidate = root / file_path
        if candidate.exists() or str(file_path).startswith("muse-work/"):
            abs_target = candidate
        else:
            abs_target = workdir / file_path

    try:
        rel_path = abs_target.relative_to(workdir).as_posix()
    except ValueError:
        # File may be given as a bare relative path already relative to muse-work/
        rel_path = file_path.lstrip("/")

    if rel_path not in merge_state.conflict_paths:
        typer.echo(
            f"❌ '{rel_path}' is not listed as a conflict.\n"
            f"   Current conflicts: {merge_state.conflict_paths}"
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # For --theirs, fetch the object from the local store and write to workdir.
    if not ours:
        theirs_commit_id = merge_state.theirs_commit
        if not theirs_commit_id:
            typer.echo("❌ MERGE_STATE.json is missing theirs_commit. Cannot resolve --theirs.")
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

        from maestro.muse_cli.db import get_commit_snapshot_manifest

        theirs_manifest = (
            await get_commit_snapshot_manifest(session, theirs_commit_id) or {}
        )
        object_id = theirs_manifest.get(rel_path)

        if object_id is None:
            # Path was deleted on the theirs branch — remove from workdir.
            dest = workdir / rel_path
            if dest.exists():
                dest.unlink()
            typer.echo(f"✅ Resolved '{rel_path}' — file deleted on theirs branch")
            logger.info("✅ muse resolve %r --theirs (deleted on theirs)", rel_path)
        else:
            try:
                apply_resolution(root, rel_path, object_id)
            except FileNotFoundError:
                typer.echo(
                    f"❌ Object for '{rel_path}' is not in the local store.\n"
                    "   Run 'muse pull' to fetch the remote objects, then retry."
                )
                raise typer.Exit(code=ExitCode.USER_ERROR)
            typer.echo(f"✅ Resolved '{rel_path}' — keeping theirs")
            logger.info("✅ muse resolve %r --theirs", rel_path)
    else:
        typer.echo(f"✅ Resolved '{rel_path}' — keeping ours")
        logger.info("✅ muse resolve %r --ours", rel_path)

    remaining = [p for p in merge_state.conflict_paths if p != rel_path]

    # Always rewrite MERGE_STATE with the updated (possibly empty) conflict list.
    # Keeping the file even when conflict_paths=[] lets `muse merge --continue`
    # read the stored commit IDs (ours_commit, theirs_commit) to build the merge
    # commit.  `muse merge --continue` is responsible for clearing this file.
    write_merge_state(
        root,
        base_commit=merge_state.base_commit or "",
        ours_commit=merge_state.ours_commit or "",
        theirs_commit=merge_state.theirs_commit or "",
        conflict_paths=remaining,
        other_branch=merge_state.other_branch,
    )

    if remaining:
        typer.echo(
            f"   {len(remaining)} conflict(s) remaining. "
            "Resolve all, then run 'muse merge --continue'."
        )
    else:
        typer.echo(
            "✅ All conflicts resolved. Run 'muse merge --continue' to create the merge commit."
        )
        logger.info("✅ muse resolve: all conflicts cleared, ready for --continue")


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def resolve(
    ctx: typer.Context,
    file_path: str = typer.Argument(
        ...,
        help="Conflicted file path (relative to muse-work/ or repo root).",
    ),
    ours: bool = typer.Option(
        False,
        "--ours",
        is_flag=True,
        help="Keep the current branch's version (no file change required).",
    ),
    theirs: bool = typer.Option(
        False,
        "--theirs",
        is_flag=True,
        help="Accept the incoming branch's version (fetched from local object store).",
    ),
) -> None:
    """Mark a conflicted file as resolved using --ours or --theirs."""
    if ours == theirs:
        typer.echo("❌ Specify exactly one of --ours or --theirs.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    async def _run() -> None:
        from maestro.muse_cli.db import open_session

        async with open_session() as session:
            await resolve_conflict_async(
                file_path=file_path, ours=ours, root=root, session=session
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse resolve failed: {exc}")
        logger.error("❌ muse resolve error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
