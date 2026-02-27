"""Repository detection utilities for the Muse CLI.

Walking up the directory tree to locate a ``.muse/`` directory is the
single most-called internal primitive. Every subcommand uses it.  Keeping
the semantics clear (``None`` on miss, never raises) makes callers simpler
and test isolation easier (``MUSE_REPO_ROOT`` env-var override).
"""
from __future__ import annotations

import logging
import os
import pathlib

import typer

from maestro.muse_cli.errors import ExitCode

logger = logging.getLogger(__name__)


def find_repo_root(start: pathlib.Path | None = None) -> pathlib.Path | None:
    """Walk up from *start* (default ``Path.cwd()``) looking for ``.muse/``.

    Returns the first directory that contains ``.muse/``, or ``None`` if no
    such ancestor exists.  Never raises — callers decide what to do on miss.

    The ``MUSE_REPO_ROOT`` environment variable overrides discovery entirely;
    set it in tests to avoid ``os.chdir`` calls.
    """
    # Env-var override — useful for tests and tooling wrappers.
    if env_root := os.environ.get("MUSE_REPO_ROOT"):
        p = pathlib.Path(env_root).resolve()
        logger.debug("⚠️ MUSE_REPO_ROOT override active: %s", p)
        return p if (p / ".muse").is_dir() else None

    current = (start or pathlib.Path.cwd()).resolve()
    while True:
        if (current / ".muse").is_dir():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def require_repo(start: pathlib.Path | None = None) -> pathlib.Path:
    """Return the repo root or exit 2 with a clear error message.

    Wraps ``find_repo_root()`` for command callbacks that must be inside a
    Muse repository.  The error text intentionally echoes to stdout so that
    ``typer.testing.CliRunner`` captures it in ``result.output`` without
    needing ``mix_stderr=True``.
    """
    root = find_repo_root(start)
    if root is None:
        typer.echo("Not a Muse repository. Run `muse init`.")
        raise typer.Exit(code=ExitCode.REPO_NOT_FOUND)
    return root
