"""muse remote — manage remote Muse Hub connections.

Subcommands:

  muse remote add <name> <url>
      Write ``[remotes.<name>] url = "<url>"`` to ``.muse/config.toml``.
      Creates the config file if it does not exist.

  muse remote remove <name>
      Remove a configured remote and all its refs/remotes/<name>/ tracking refs.

  muse remote rename <old> <new>
      Rename a remote in config and move its tracking ref paths.

  muse remote set-url <name> <url>
      Update the URL of an existing remote without touching tracking refs.

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
from maestro.muse_cli.config import get_remote, list_remotes, remove_remote, rename_remote, set_remote
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


@app.command("remove")
def remote_remove(
    name: str = typer.Argument(..., help="Remote name to remove (e.g. 'origin')."),
) -> None:
    """Remove a configured remote and all its local tracking refs.

    Deletes ``[remotes.<name>]`` from ``.muse/config.toml`` and removes the
    ``.muse/remotes/<name>/`` directory tree.  Errors if the remote does not
    exist.

    Example::

        muse remote remove origin
    """
    root = require_repo()

    try:
        remove_remote(name.strip(), root)
    except KeyError:
        typer.echo(f"❌ Remote '{name}' does not exist.")
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None

    typer.echo(f"✅ Remote '{name}' removed.")
    logger.info("✅ muse remote remove %r", name)


@app.command("rename")
def remote_rename(
    old_name: str = typer.Argument(..., help="Current remote name."),
    new_name: str = typer.Argument(..., help="New remote name."),
) -> None:
    """Rename a remote in config and move its tracking ref paths.

    Updates ``[remotes.<old>]`` → ``[remotes.<new>]`` in ``.muse/config.toml``
    and moves ``.muse/remotes/<old>/`` → ``.muse/remotes/<new>/``.  Errors if
    the old remote does not exist or the new name is already taken.

    Example::

        muse remote rename origin upstream
    """
    root = require_repo()

    if not new_name.strip():
        typer.echo("❌ New remote name cannot be empty.")
        raise typer.Exit(code=int(ExitCode.USER_ERROR))

    try:
        rename_remote(old_name.strip(), new_name.strip(), root)
    except KeyError:
        typer.echo(f"❌ Remote '{old_name}' does not exist.")
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None
    except ValueError:
        typer.echo(f"❌ Remote '{new_name}' already exists.")
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None

    typer.echo(f"✅ Remote '{old_name}' renamed to '{new_name}'.")
    logger.info("✅ muse remote rename %r → %r", old_name, new_name)


@app.command("set-url")
def remote_set_url(
    name: str = typer.Argument(..., help="Remote name (e.g. 'origin')."),
    url: str = typer.Argument(..., help="New URL for the remote."),
) -> None:
    """Update the URL of an existing remote without touching tracking refs.

    Updates ``[remotes.<name>] url`` in ``.muse/config.toml``.  Unlike
    ``muse remote add``, this command errors if the remote does not already
    exist — use ``add`` for first-time registration.

    Example::

        muse remote set-url origin https://new-hub.example.com/musehub/repos/my-repo
    """
    root = require_repo()

    if not url.strip().startswith(("http://", "https://")):
        typer.echo(f"❌ URL must start with http:// or https:// — got: {url!r}")
        raise typer.Exit(code=int(ExitCode.USER_ERROR))

    if get_remote(name.strip(), root) is None:
        typer.echo(f"❌ Remote '{name}' does not exist. Use `muse remote add` to create it.")
        raise typer.Exit(code=int(ExitCode.USER_ERROR))

    set_remote(name.strip(), url.strip(), root)
    typer.echo(f"✅ Remote '{name}' URL changed to {url}")
    logger.info("✅ muse remote set-url %r %s", name, url)
