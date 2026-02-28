"""muse cherry-pick — apply a specific commit's diff on top of HEAD.

Transplants the changes introduced by a single commit (from any branch)
onto the current branch, without bringing in other commits from that branch.

Domain analogy: a producer recorded the perfect guitar solo in
``experiment/guitar-solo``.  ``muse cherry-pick <commit>`` transplants just
that solo into main, leaving the other 20 unrelated commits behind.

Flags
-----
COMMIT TEXT       Commit ID to cherry-pick (required, positional, accepts prefix).
--no-commit       Apply the changes to muse-work/ without committing.
--continue        Resume after resolving conflicts from a previous cherry-pick.
--abort           Abort in-progress cherry-pick and restore pre-cherry-pick state.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_cherry_pick import (
    _cherry_pick_abort_async,
    _cherry_pick_async,
    _cherry_pick_continue_async,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


@app.callback(invoke_without_command=True)
def cherry_pick(
    ctx: typer.Context,
    commit: Optional[str] = typer.Argument(
        None,
        help="Commit ID to cherry-pick (full or abbreviated SHA).",
        metavar="COMMIT",
    ),
    no_commit: bool = typer.Option(
        False,
        "--no-commit",
        is_flag=True,
        help=(
            "Apply the cherry-pick changes to muse-work/ without creating a new commit. "
            "Useful for inspecting or further editing the result before committing."
        ),
    ),
    cont: bool = typer.Option(
        False,
        "--continue",
        is_flag=True,
        help="Resume after resolving conflicts from a previous cherry-pick.",
    ),
    abort: bool = typer.Option(
        False,
        "--abort",
        is_flag=True,
        help=(
            "Abort an in-progress cherry-pick and restore the branch to its "
            "pre-cherry-pick state."
        ),
    ),
) -> None:
    """Apply a specific commit's diff on top of HEAD without merging the full branch."""
    root = require_repo()

    if abort:
        async def _run_abort() -> None:
            async with open_session() as session:
                await _cherry_pick_abort_async(root=root, session=session)

        try:
            asyncio.run(_run_abort())
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"❌ muse cherry-pick --abort failed: {exc}")
            logger.error("❌ muse cherry-pick --abort error: %s", exc, exc_info=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        return

    if cont:
        async def _run_continue() -> None:
            async with open_session() as session:
                await _cherry_pick_continue_async(root=root, session=session)

        try:
            asyncio.run(_run_continue())
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"❌ muse cherry-pick --continue failed: {exc}")
            logger.error(
                "❌ muse cherry-pick --continue error: %s", exc, exc_info=True
            )
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        return

    if not commit:
        typer.echo(
            "❌ Commit ID required (or use --continue to resume, --abort to cancel)."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    async def _run() -> None:
        async with open_session() as session:
            await _cherry_pick_async(
                commit_ref=commit,
                root=root,
                session=session,
                no_commit=no_commit,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse cherry-pick failed: {exc}")
        logger.error("❌ muse cherry-pick error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
