"""muse init — initialise a new Muse repository."""
from __future__ import annotations

import typer

app = typer.Typer()


@app.callback(invoke_without_command=True)
def init(ctx: typer.Context) -> None:
    """Initialise a new Muse repository in the current directory."""
    typer.echo("muse init: not yet implemented")
