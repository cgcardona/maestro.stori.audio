"""muse log — display the variation history graph."""
from __future__ import annotations

import typer

from maestro.muse_cli._repo import require_repo

app = typer.Typer()


@app.callback(invoke_without_command=True)
def log(ctx: typer.Context) -> None:
    """Display the commit DAG for the current project."""
    require_repo()
    typer.echo("muse log: not yet implemented")
