"""muse status — show working-tree state relative to HEAD.

MVP implementation: reads ``.muse/HEAD`` to determine the current branch,
then checks whether that branch has any commits.  Full working-tree diff
(added / modified / deleted / untracked files) is tracked in issue #44.
"""
from __future__ import annotations

import pathlib

import typer

from maestro.muse_cli._repo import require_repo

app = typer.Typer()


@app.callback(invoke_without_command=True)
def status(ctx: typer.Context) -> None:
    """Show the current branch and commit state."""
    root = require_repo()
    muse_dir = root / ".muse"

    head_path = muse_dir / "HEAD"
    if not head_path.exists():
        # Bare .muse/ directory created outside of `muse init`; nothing to report.
        typer.echo("muse status: not yet implemented")
        return

    head_ref = head_path.read_text().strip()  # e.g. "refs/heads/main"
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref

    ref_path = muse_dir / head_ref
    if not ref_path.exists() or not ref_path.read_text().strip():
        typer.echo(f"On branch {branch}, no commits yet")
        return

    # Branch has commits — full diff not yet implemented (issue #44).
    typer.echo(f"On branch {branch}")
    typer.echo("muse status: full diff not yet implemented")
