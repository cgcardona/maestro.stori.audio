"""muse revert — create a new commit that undoes a prior commit.

Safe undo: given a commit C with parent P, ``muse revert <commit>`` creates
a new commit whose snapshot is P's state (the world before C was applied).
History is never rewritten — the revert is a forward commit.

Domain analogy: a producer accidentally committed a bad drum arrangement.
Rather than resetting (which loses history), ``muse revert`` creates an
"undo commit" so the full timeline remains auditable.

Flags
-----
COMMIT TEXT       Commit ID to revert (required, positional, accepts prefix).
--no-commit       Apply the inverse changes to muse-work/ without committing.
--track TEXT      Scope the revert to paths under tracks/<track>/.
--section TEXT    Scope the revert to paths under sections/<section>/.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_revert import _revert_async

logger = logging.getLogger(__name__)

app = typer.Typer()


@app.callback(invoke_without_command=True)
def revert(
    ctx: typer.Context,
    commit: str = typer.Argument(
        ...,
        help="Commit ID to revert (full or abbreviated SHA).",
        metavar="COMMIT",
    ),
    no_commit: bool = typer.Option(
        False,
        "--no-commit",
        help=(
            "Apply the inverse changes to muse-work/ without creating a new commit. "
            "Note: file bytes not retained by the object store cannot be restored "
            "automatically — missing paths are listed as warnings."
        ),
    ),
    track: Optional[str] = typer.Option(
        None,
        "--track",
        help=(
            "Scope the revert to a specific track (instrument) path prefix. "
            "Only files under tracks/<track>/ are reverted; all other paths "
            "remain at HEAD."
        ),
    ),
    section: Optional[str] = typer.Option(
        None,
        "--section",
        help=(
            "Scope the revert to a specific section path prefix. "
            "Only files under sections/<section>/ are reverted; all other paths "
            "remain at HEAD."
        ),
    ),
) -> None:
    """Create a new commit that undoes a prior commit without rewriting history."""
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _revert_async(
                commit_ref=commit,
                root=root,
                session=session,
                no_commit=no_commit,
                track=track,
                section=section,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse revert failed: {exc}")
        logger.error("❌ muse revert error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
