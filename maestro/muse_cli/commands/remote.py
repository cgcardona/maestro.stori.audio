"""muse remote — manage remote Muse Hub connections.

Subcommands:

  muse remote add <name> <url>
      Write ``[remotes.<name>] url = "<url>"`` to ``.muse/config.toml``.
      Creates the config file if it does not exist.

  muse remote -v / --verbose
      Print all configured remotes with their URLs.
      Token values in [auth] are masked — this command is safe to run in CI.

Exit codes follow the Muse CLI contract (``errors.ExitCode``):
  0 — success
  1 — user error (bad arguments)
  2 — not a Muse repository
"""
from __future__ import annotations

import logging

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.config import list_remotes, set_remote
from maestro.muse_cli.errors import ExitCode

logger = logging.getLogger(__name__)

app = typer.Typer(invoke_without_command=True)


@app.callback(invoke_without_command=True)
def remote(
    ctx: typer.Context,
    verbose: bool = typer.Option(
        False,
        "-v",
        "--verbose",
        help="Print all configured remotes and their URLs.",
        is_eager=False,
    ),
) -> None:
    """Manage remote Muse Hub connections.

    Run ``muse remote add <name> <url>`` to register a remote, then
    ``muse push`` / ``muse pull`` to sync with it.
    """
    root = require_repo()

    # When invoked as `muse remote -v` (no subcommand), show remotes list.
    if ctx.invoked_subcommand is None:
        remotes = list_remotes(root)
        if not remotes:
            typer.echo("(no remotes configured — run `muse remote add <name> <url>`)")
            return
        for r in remotes:
            typer.echo(f"{r['name']}\t{r['url']}")


@app.command("add")
def remote_add(
    name: str = typer.Argument(..., help="Remote name (e.g. 'origin')."),
    url: str = typer.Argument(
        ...,
        help="Remote URL (e.g. 'https://hub.example.com/musehub/repos/<repo-id>').",
    ),
) -> None:
    """Register a named remote Hub URL in .muse/config.toml.

    Example::

        muse remote add origin https://story.audio/musehub/repos/my-repo-id

    After adding a remote, use ``muse push`` and ``muse pull`` to sync.
    """
    root = require_repo()

    if not name.strip():
        typer.echo("❌ Remote name cannot be empty.")
        raise typer.Exit(code=int(ExitCode.USER_ERROR))

    if not url.strip().startswith(("http://", "https://")):
        typer.echo(f"❌ URL must start with http:// or https:// — got: {url!r}")
        raise typer.Exit(code=int(ExitCode.USER_ERROR))

    set_remote(name.strip(), url.strip(), root)
    typer.echo(f"✅ Remote '{name}' set to {url}")
    logger.info("✅ muse remote add %r %s", name, url)
