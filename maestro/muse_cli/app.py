"""Muse CLI — Typer application root.

Entry point for the ``muse`` console script. Registers all MVP
subcommands (init, status, commit, log, checkout, merge, remote,
push, pull) as Typer sub-applications.
"""
from __future__ import annotations

import typer

from maestro.muse_cli.commands import (
    commit,
    init,
    log,
    merge,
    pull,
    push,
    remote,
    status,
)
from maestro.muse_cli.commands.checkout import run_checkout as _checkout_logic

cli = typer.Typer(
    name="muse",
    help="Muse — Git-style version control for musical compositions.",
    no_args_is_help=True,
)

cli.add_typer(init.app, name="init", help="Initialise a new Muse repository.")
cli.add_typer(status.app, name="status", help="Show working-tree drift against HEAD.")
cli.add_typer(commit.app, name="commit", help="Record a new variation in history.")
cli.add_typer(log.app, name="log", help="Display the variation history graph.")
# checkout is registered as a plain @cli.command() (not add_typer) so that Click
# treats it as a Command rather than a Group.  Click Groups pass sub-contexts with
# allow_interspersed_args=False, which prevents --force from being recognised when
# it follows the positional BRANCH argument.  A plain Command keeps the default
# allow_interspersed_args=True and parses options in any position.
@cli.command("checkout", help="Create or switch branches; update .muse/HEAD.")
def _checkout_cmd(
    branch: str = typer.Argument(..., help="Branch name to checkout or create."),
    create: bool = typer.Option(
        False, "-b", "--create", help="Create a new branch at the current HEAD and switch to it."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Ignore uncommitted changes in muse-work/."
    ),
) -> None:
    _checkout_logic(branch=branch, create=create, force=force)


cli.add_typer(merge.app, name="merge", help="Three-way merge two variation branches.")
cli.add_typer(remote.app, name="remote", help="Manage remote server connections.")
cli.add_typer(push.app, name="push", help="Upload local variations to a remote.")
cli.add_typer(pull.app, name="pull", help="Download remote variations locally.")


if __name__ == "__main__":
    cli()
