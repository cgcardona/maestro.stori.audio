"""muse reset <commit> — reset the branch pointer to a prior commit.

Algorithm
---------
1. Block if ``.muse/MERGE_STATE.json`` exists (merge in progress).
2. Resolve repo root via ``require_repo()``.
3. Read current branch from ``.muse/HEAD``.
4. Resolve *commit* argument (``HEAD~N``, full/abbreviated SHA) to a
   ``MuseCliCommit`` row via :func:`~maestro.services.muse_reset.resolve_ref`.
5. Apply the chosen mode:

   ``--soft``  — update ``.muse/refs/heads/<branch>`` only.  muse-work/
                 files and the object store are untouched.  The producer
                 can immediately ``muse commit`` a new snapshot on top of
                 the rewound head.

   ``--mixed`` (default) — same as ``--soft`` in the current Muse model
                 (no explicit staging area).  Included for API symmetry
                 and forward-compatibility.

   ``--hard``  — update the branch ref AND overwrite ``muse-work/`` with
                 the file content recorded in the target snapshot.  Objects
                 are read from ``.muse/objects/`` (the blob store populated
                 by ``muse commit``).  Prompts for confirmation unless
                 ``--yes`` is given, because this operation discards any
                 uncommitted changes in muse-work/.

HEAD~N
------
    muse reset HEAD~1        # one parent back
    muse reset HEAD~3        # three parents back
    muse reset abc123        # abbreviated SHA
    muse reset --hard HEAD~2 # two parents back + restore working tree

Exit codes
----------
0  success
1  user error (ref not found, merge in progress, no commits, abort)
2  not a Muse repo
3  internal error (DB inconsistency, missing object blobs)
"""
from __future__ import annotations

import asyncio
import logging

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_reset import (
    MissingObjectError,
    ResetMode,
    perform_reset,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


@app.callback(invoke_without_command=True)
def reset(
    ctx: typer.Context,
    commit: str = typer.Argument(
        ...,
        help=(
            "Target commit reference.  Accepts: HEAD, HEAD~N, "
            "a full 64-char SHA, or any unambiguous SHA prefix."
        ),
    ),
    soft: bool = typer.Option(
        False,
        "--soft",
        help="Move branch pointer only; muse-work/ unchanged.",
    ),
    mixed: bool = typer.Option(
        False,
        "--mixed",
        help="Move branch pointer and reset index (default mode).",
    ),
    hard: bool = typer.Option(
        False,
        "--hard",
        help="Move branch pointer AND overwrite muse-work/ with target snapshot.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt for --hard reset.",
    ),
) -> None:
    """Reset the branch pointer to a prior commit."""
    # ── Resolve mode (default: mixed) ────────────────────────────────────
    mode_count = sum([soft, mixed, hard])
    if mode_count > 1:
        typer.echo("❌ Specify at most one of --soft, --mixed, or --hard.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if hard:
        mode = ResetMode.HARD
    elif soft:
        mode = ResetMode.SOFT
    else:
        mode = ResetMode.MIXED  # default

    # ── Hard-mode confirmation ────────────────────────────────────────────
    if mode is ResetMode.HARD and not yes:
        typer.echo(
            "⚠️  muse reset --hard will OVERWRITE muse-work/ with the target snapshot.\n"
            "    All uncommitted changes will be LOST."
        )
        confirmed = typer.confirm("Proceed?", default=False)
        if not confirmed:
            typer.echo("Reset aborted.")
            raise typer.Exit(code=ExitCode.SUCCESS)

    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            result = await perform_reset(
                root=root,
                session=session,
                ref=commit,
                mode=mode,
            )

        if mode is ResetMode.HARD:
            typer.echo(
                f"✅ HEAD is now at {result.target_commit_id[:8]} "
                f"({result.files_restored} files restored, "
                f"{result.files_deleted} files deleted)"
            )
        else:
            typer.echo(
                f"✅ HEAD is now at {result.target_commit_id[:8]}"
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except MissingObjectError as exc:
        typer.echo(f"❌ {exc}")
        logger.error("❌ muse reset hard: %s", exc)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
    except Exception as exc:
        typer.echo(f"❌ muse reset failed: {exc}")
        logger.error("❌ muse reset error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
