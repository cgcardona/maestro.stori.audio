"""muse merge — three-way merge of two variation branches."""
from __future__ import annotations

import typer

from maestro.muse_cli._repo import require_repo

app = typer.Typer()


@app.callback(invoke_without_command=True)
def merge(ctx: typer.Context) -> None:
    """Three-way merge of two variation branches."""
    require_repo()
    typer.echo("muse merge: not yet implemented")
