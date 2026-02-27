"""muse checkout — time-travel to a historical variation."""
from __future__ import annotations

import typer

from maestro.muse_cli._repo import require_repo

app = typer.Typer()


@app.callback(invoke_without_command=True)
def checkout(ctx: typer.Context) -> None:
    """Checkout a specific variation, reconstructing its state."""
    require_repo()
    typer.echo("muse checkout: not yet implemented")
