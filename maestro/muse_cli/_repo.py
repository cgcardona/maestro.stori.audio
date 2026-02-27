"""Repository detection utilities for the Muse CLI."""
from __future__ import annotations

import pathlib

from maestro.muse_cli.errors import ExitCode, RepoNotFoundError


def find_repo_root(start: pathlib.Path | None = None) -> pathlib.Path:
    """Walk up from *start* (default cwd) looking for a ``.muse/`` directory.

    Returns the directory that contains ``.muse/``.
    Raises ``RepoNotFoundError`` if none is found before hitting the filesystem root.
    """
    current = (start or pathlib.Path.cwd()).resolve()
    while True:
        if (current / ".muse").is_dir():
            return current
        parent = current.parent
        if parent == current:
            raise RepoNotFoundError()
        current = parent


def require_repo(start: pathlib.Path | None = None) -> pathlib.Path:
    """Convenience wrapper: find the repo root or exit with code 2."""
    import typer

    try:
        return find_repo_root(start)
    except RepoNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=ExitCode.REPO_NOT_FOUND) from exc
