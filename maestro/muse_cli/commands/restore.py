"""muse restore — restore specific files from a commit or index.

Surgical file-level restore: bring back "the bass from take 3" without
touching any other track.  Unlike ``muse reset --hard`` (which resets the
entire working tree), ``restore`` targets individual paths only.

Usage patterns
--------------
Restore from HEAD (default)::

    muse restore muse-work/bass/bassline.mid

Restore the index entry from HEAD (``--staged``)::

    muse restore --staged muse-work/bass/bassline.mid

Restore from a specific commit::

    muse restore --source <commit> muse-work/drums/kick.mid

Restore both worktree and staged (explicit ``--worktree``)::

    muse restore --worktree --source <commit> muse-work/drums/kick.mid muse-work/bass/bassline.mid

Exit codes
----------
0  success
1  user error (path not in snapshot, ref not found, no commits)
2  not a Muse repo
3  internal error (DB inconsistency, missing object blobs)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_reset import MissingObjectError
from maestro.services.muse_restore import PathNotInSnapshotError, perform_restore

logger = logging.getLogger(__name__)

app = typer.Typer()


@app.callback(invoke_without_command=True)
def restore(
    ctx: typer.Context,
    paths: list[str] = typer.Argument(
        ...,
        help=(
            "One or more relative paths within muse-work/ to restore. "
            "Accepts paths with or without the 'muse-work/' prefix."
        ),
    ),
    staged: bool = typer.Option(
        False,
        "--staged",
        help=(
            "Restore the index (snapshot manifest) entry for the path from "
            "the source commit rather than muse-work/. In the current Muse "
            "model (no separate staging area) this is equivalent to --worktree."
        ),
    ),
    worktree: bool = typer.Option(
        False,
        "--worktree",
        help=(
            "Restore muse-work/ files from the source snapshot. "
            "This is the default behaviour when no mode flag is specified."
        ),
    ),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        "-s",
        help=(
            "Commit reference to restore from: HEAD, HEAD~N, a full SHA, or "
            "any unambiguous SHA prefix. Defaults to HEAD when omitted."
        ),
    ),
) -> None:
    """Restore specific files from a commit or index into muse-work/."""
    if not paths:
        typer.echo("❌ At least one path is required.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            result = await perform_restore(
                root=root,
                session=session,
                paths=paths,
                source_ref=source,
                staged=staged,
            )

        short_id = result.source_commit_id[:8]
        if len(result.paths_restored) == 1:
            typer.echo(
                f"✅ Restored {result.paths_restored[0]!r} from commit {short_id}"
            )
        else:
            typer.echo(
                f"✅ Restored {len(result.paths_restored)} files from commit {short_id}:"
            )
            for p in result.paths_restored:
                typer.echo(f"   • {p}")

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except PathNotInSnapshotError as exc:
        typer.echo(f"❌ {exc}")
        logger.error("❌ muse restore: %s", exc)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except MissingObjectError as exc:
        typer.echo(f"❌ {exc}")
        logger.error("❌ muse restore: missing object: %s", exc)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
    except Exception as exc:
        typer.echo(f"❌ muse restore failed: {exc}")
        logger.error("❌ muse restore error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
