"""muse open — open a Muse artifact with the macOS system default application.

Dispatches to ``open`` (macOS ``NSWorkspace``), which opens the file in
whatever the system default app is for that file type:

- ``.mid``  → Stori DAW or GarageBand
- ``.webp`` / ``.png`` → Preview
- ``.mp3``  → QuickTime

macOS-only.  Exits 1 with a clear error on other platforms.
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
import platform
import subprocess

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.artifact_resolver import resolve_artifact_async
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode

logger = logging.getLogger(__name__)

app = typer.Typer()


def _guard_macos() -> None:
    """Exit 1 with a clear message if not running on macOS."""
    if platform.system() != "Darwin":
        typer.echo("❌ muse open requires macOS.")
        raise typer.Exit(code=ExitCode.USER_ERROR)


@app.callback(invoke_without_command=True)
def open_artifact(
    ctx: typer.Context,
    path_or_id: str = typer.Argument(..., help="File path or short commit ID."),
) -> None:
    """Open an artifact with the macOS system default application."""
    _guard_macos()
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            path = await resolve_artifact_async(path_or_id, root=root, session=session)
        subprocess.run(["open", str(path)], check=True)
        typer.echo(f"✅ Opened {path}")
        logger.info("✅ muse open: %s", path)

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except subprocess.CalledProcessError as exc:
        typer.echo(f"❌ muse open failed: {exc}")
        logger.error("❌ muse open subprocess error: %s", exc)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
    except Exception as exc:
        typer.echo(f"❌ muse open failed: {exc}")
        logger.error("❌ muse open error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
