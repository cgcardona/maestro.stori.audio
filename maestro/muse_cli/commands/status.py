"""muse status — show working-tree state relative to HEAD.

Output modes
------------

**Clean working tree** (no uncommitted changes)::

    On branch main
    nothing to commit, working tree clean

**Uncommitted changes** (modified / added / deleted files)::

    On branch main

    Changes since last commit:
      (use "muse commit -m <msg>" to record changes)

            modified:   beat.mid
            new file:   lead.mp3
            deleted:    scratch.mid

**In-progress merge** (``MERGE_STATE.json`` present)::

    On branch main

    You have unmerged paths.
      (fix conflicts and run "muse commit")

    Unmerged paths:
            both modified:   beat.mid

**No commits yet** (branch has never been committed to)::

    On branch main, no commits yet

    Untracked files:
      (use "muse commit -m <msg>" to record changes)

            beat.mid
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import get_head_snapshot_manifest, open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.merge_engine import read_merge_state
from maestro.muse_cli.snapshot import diff_workdir_vs_snapshot, walk_workdir

logger = logging.getLogger(__name__)

app = typer.Typer()


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _status_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
) -> None:
    """Core status logic — fully injectable for tests.

    Reads repo state from ``.muse/``, queries the DB session for the HEAD
    snapshot manifest, diffs the working tree, and writes formatted output
    via :func:`typer.echo`.

    Args:
        root:    Repository root (directory containing ``.muse/``).
        session: An open async DB session used to load the HEAD snapshot.
    """
    muse_dir = root / ".muse"

    # -- Branch name --
    head_path = muse_dir / "HEAD"
    head_ref = head_path.read_text().strip()          # "refs/heads/main"
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref

    # -- In-progress merge --
    merge_state = read_merge_state(root)
    if merge_state is not None and merge_state.conflict_paths:
        typer.echo(f"On branch {branch}")
        typer.echo("")
        typer.echo("You have unmerged paths.")
        typer.echo('  (fix conflicts and run "muse commit")')
        typer.echo("")
        typer.echo("Unmerged paths:")
        for conflict_path in sorted(merge_state.conflict_paths):
            typer.echo(f"\tboth modified:   {conflict_path}")
        typer.echo("")
        return

    # -- Check for any commits on this branch --
    ref_path = muse_dir / pathlib.Path(head_ref)
    head_commit_id = ""
    if ref_path.exists():
        head_commit_id = ref_path.read_text().strip()

    if not head_commit_id:
        # No commits yet -- show untracked working-tree files if any.
        workdir = root / "muse-work"
        untracked_files: list[str] = []
        if workdir.exists():
            manifest = walk_workdir(workdir)
            untracked_files = sorted(manifest.keys())

        if untracked_files:
            typer.echo(f"On branch {branch}, no commits yet")
            typer.echo("")
            typer.echo("Untracked files:")
            typer.echo('  (use "muse commit -m <msg>" to record changes)')
            typer.echo("")
            for path in untracked_files:
                typer.echo(f"\t{path}")
            typer.echo("")
        else:
            typer.echo(f"On branch {branch}, no commits yet")
        return

    # -- Load HEAD snapshot manifest from DB --
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    last_manifest = await get_head_snapshot_manifest(session, repo_id, branch) or {}

    # -- Diff workdir vs HEAD snapshot --
    workdir = root / "muse-work"
    added, modified, deleted, _ = diff_workdir_vs_snapshot(workdir, last_manifest)

    if not added and not modified and not deleted:
        typer.echo(f"On branch {branch}")
        typer.echo("nothing to commit, working tree clean")
        return

    # -- Display changes --
    typer.echo(f"On branch {branch}")
    typer.echo("")
    typer.echo("Changes since last commit:")
    typer.echo('  (use "muse commit -m <msg>" to record changes)')
    typer.echo("")
    for path in sorted(modified):
        typer.echo(f"\tmodified:   {path}")
    for path in sorted(added):
        typer.echo(f"\tnew file:   {path}")
    for path in sorted(deleted):
        typer.echo(f"\tdeleted:    {path}")
    typer.echo("")


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def status(ctx: typer.Context) -> None:
    """Show the current branch and working-tree state relative to HEAD."""
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _status_async(root=root, session=session)

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"muse status failed: {exc}")
        logger.error("muse status error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
