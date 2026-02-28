"""muse rebase <upstream> — rebase commits onto a new base.

Algorithm
---------
1. Find the merge-base (LCA) of HEAD and ``<upstream>``.
2. Collect commits on the current branch that are not in ``<upstream>``'s
   history, ordered oldest-first.
3. Replay each commit onto the upstream tip as a new commit (new commit_id,
   same snapshot delta).
4. Advance the branch pointer to the final replayed commit.

``--continue`` / ``--abort``
-----------------------------
Mid-rebase state is stored in ``.muse/REBASE_STATE.json``.  On conflict:
- ``muse rebase --continue``: resume after manually resolving conflicts.
- ``muse rebase --abort``: restore the branch pointer to its pre-rebase HEAD.

``--interactive`` / ``-i``
---------------------------
Opens ``$EDITOR`` with a plan file listing all commits to replay.  Each line is::

    pick <short-sha> <message>

Actions: ``pick`` (keep), ``squash`` (fold into previous), ``drop`` (skip),
``fixup`` (squash, no message), ``reword`` (keep, change message).

``--autosquash``
----------------
Detects ``fixup! <message>`` commits and automatically moves them immediately
after the matching commit in the replay order.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_rebase import (
    _rebase_abort_async,
    _rebase_async,
    _rebase_continue_async,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


@app.callback(invoke_without_command=True)
def rebase(
    ctx: typer.Context,
    upstream: Optional[str] = typer.Argument(
        None,
        help=(
            "Branch name or commit ID to rebase onto. "
            "Omit when using --continue or --abort."
        ),
        metavar="UPSTREAM",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        "-i",
        is_flag=True,
        help=(
            "Open $EDITOR with a rebase plan before executing. "
            "Lines: pick/squash/drop <short-sha> <message>."
        ),
    ),
    autosquash: bool = typer.Option(
        False,
        "--autosquash",
        is_flag=True,
        help=(
            "Automatically detect 'fixup! <msg>' commits and move them "
            "immediately after their matching commit."
        ),
    ),
    rebase_merges: bool = typer.Option(
        False,
        "--rebase-merges",
        is_flag=True,
        help="Preserve merge commits during replay (experimental).",
    ),
    cont: bool = typer.Option(
        False,
        "--continue",
        is_flag=True,
        help="Resume a rebase that was paused due to conflicts.",
    ),
    abort: bool = typer.Option(
        False,
        "--abort",
        is_flag=True,
        help="Abort the in-progress rebase and restore the branch to its original HEAD.",
    ),
) -> None:
    """Rebase commits onto a new base, producing a linear history.

    Use ``--continue`` after resolving conflicts to resume the rebase.
    Use ``--abort`` to cancel and restore the original branch state.
    """
    root = require_repo()

    if abort:
        async def _run_abort() -> None:
            await _rebase_abort_async(root=root)

        try:
            asyncio.run(_run_abort())
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"❌ muse rebase --abort failed: {exc}")
            logger.error("❌ muse rebase --abort error: %s", exc, exc_info=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        return

    if cont:
        async def _run_continue() -> None:
            async with open_session() as session:
                await _rebase_continue_async(root=root, session=session)

        try:
            asyncio.run(_run_continue())
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"❌ muse rebase --continue failed: {exc}")
            logger.error("❌ muse rebase --continue error: %s", exc, exc_info=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        return

    if not upstream:
        typer.echo(
            "❌ UPSTREAM is required (or use --continue / --abort to manage "
            "an in-progress rebase)."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    async def _run() -> None:
        async with open_session() as session:
            await _rebase_async(
                upstream=upstream,
                root=root,
                session=session,
                interactive=interactive,
                autosquash=autosquash,
                rebase_merges=rebase_merges,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse rebase failed: {exc}")
        logger.error("❌ muse rebase error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
