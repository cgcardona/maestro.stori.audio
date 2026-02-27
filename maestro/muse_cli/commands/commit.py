"""muse commit — record a new variation in Muse history."""
from __future__ import annotations

import typer

from maestro.muse_cli._repo import require_repo

app = typer.Typer()


@app.callback(invoke_without_command=True)
def commit(ctx: typer.Context) -> None:
    """Record the current working state as a new variation."""
    require_repo()
    typer.echo("muse commit: not yet implemented")
