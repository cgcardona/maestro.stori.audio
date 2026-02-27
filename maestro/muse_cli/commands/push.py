"""muse push — upload local variations to a remote Maestro server."""
from __future__ import annotations

import typer

from maestro.muse_cli._repo import require_repo

app = typer.Typer()


@app.callback(invoke_without_command=True)
def push(ctx: typer.Context) -> None:
    """Push local variations to the configured remote server."""
    require_repo()
    typer.echo("muse push: not yet implemented")
