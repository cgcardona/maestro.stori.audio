"""Muse CLI — Typer application root.

Entry point for the ``muse`` console script. Registers all MVP
subcommands (init, status, commit, log, checkout, merge, remote,
push, pull, open, play) as Typer sub-applications.
"""
from __future__ import annotations

import typer

from maestro.muse_cli.commands import (
    checkout,
    commit,
    init,
    log,
    merge,
    open_cmd,
    play,
    pull,
    push,
    remote,
    status,
    swing,
)

cli = typer.Typer(
    name="muse",
    help="Muse — Git-style version control for musical compositions.",
    no_args_is_help=True,
)

cli.add_typer(init.app, name="init", help="Initialise a new Muse repository.")
cli.add_typer(status.app, name="status", help="Show working-tree drift against HEAD.")
cli.add_typer(commit.app, name="commit", help="Record a new variation in history.")
cli.add_typer(log.app, name="log", help="Display the variation history graph.")
cli.add_typer(checkout.app, name="checkout", help="Checkout a historical variation.")
cli.add_typer(merge.app, name="merge", help="Three-way merge two variation branches.")
cli.add_typer(remote.app, name="remote", help="Manage remote server connections.")
cli.add_typer(push.app, name="push", help="Upload local variations to a remote.")
cli.add_typer(pull.app, name="pull", help="Download remote variations locally.")
cli.add_typer(open_cmd.app, name="open", help="Open an artifact in the system default app (macOS).")
cli.add_typer(play.app, name="play", help="Play an audio artifact via afplay (macOS).")
cli.add_typer(swing.app, name="swing", help="Analyze or annotate the swing factor of a composition.")


if __name__ == "__main__":
    cli()
