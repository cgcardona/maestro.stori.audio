"""muse status — show working-tree drift against HEAD."""
from __future__ import annotations

import typer

from maestro.muse_cli._repo import require_repo

app = typer.Typer()


@app.callback(invoke_without_command=True)
def status(ctx: typer.Context) -> None:
    """Show the drift report between HEAD and the working state."""
    require_repo()
    typer.echo("muse status: not yet implemented")
