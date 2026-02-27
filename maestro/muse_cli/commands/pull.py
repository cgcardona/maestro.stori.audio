"""muse pull — download remote variations to the local repository."""
from __future__ import annotations

import typer

from maestro.muse_cli._repo import require_repo

app = typer.Typer()


@app.callback(invoke_without_command=True)
def pull(ctx: typer.Context) -> None:
    """Pull remote variations into the local repository."""
    require_repo()
    typer.echo("muse pull: not yet implemented")
