"""muse remote — manage remote Maestro server connections."""
from __future__ import annotations

import typer

from maestro.muse_cli._repo import require_repo

app = typer.Typer()


@app.callback(invoke_without_command=True)
def remote(ctx: typer.Context) -> None:
    """Manage remote Maestro server connections."""
    require_repo()
    typer.echo("muse remote: not yet implemented")
