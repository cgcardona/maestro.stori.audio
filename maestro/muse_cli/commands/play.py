"""muse play — play a Muse audio artifact via macOS ``afplay``.

Behaviour by file type:

- ``.mp3`` / ``.aiff`` / ``.wav`` / ``.m4a`` → played via ``afplay`` (no UI,
  process exits when playback finishes).
- ``.mid`` → falls back to ``open`` (hands off to the system default MIDI
  app).  ``afplay`` does not support MIDI; this limitation is surfaced
  clearly in the terminal output.

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

#: File extensions that ``afplay`` handles natively.
_AFPLAY_EXTENSIONS = frozenset({".mp3", ".aiff", ".aif", ".wav", ".m4a", ".caf"})

#: File extensions where we fall back to ``open``.
_OPEN_FALLBACK_EXTENSIONS = frozenset({".mid", ".midi"})


def _guard_macos() -> None:
    """Exit 1 with a clear message if not running on macOS."""
    if platform.system() != "Darwin":
        typer.echo("❌ muse play requires macOS.")
        raise typer.Exit(code=ExitCode.USER_ERROR)


def _play_path(path: pathlib.Path) -> None:
    """Dispatch playback for *path* based on its suffix.

    Extracted for unit-testability — callers mock ``subprocess.run``.
    """
    suffix = path.suffix.lower()

    if suffix in _AFPLAY_EXTENSIONS:
        typer.echo(f"▶  Playing {path.name} …")
        subprocess.run(["afplay", str(path)], check=True)
        typer.echo("⏹  Playback finished.")
        logger.info("✅ muse play (afplay): %s", path)

    elif suffix in _OPEN_FALLBACK_EXTENSIONS:
        typer.echo(
            f"⚠️  MIDI playback via afplay is not supported.\n"
            f"   Opening {path.name} in the system default MIDI app instead."
        )
        subprocess.run(["open", str(path)], check=True)
        logger.info("✅ muse play (open fallback): %s", path)

    else:
        typer.echo(
            f"⚠️  Unsupported file type '{suffix}'.\n"
            f"   Attempting to open with system default app."
        )
        subprocess.run(["open", str(path)], check=True)
        logger.warning("⚠️ muse play: unknown extension '%s' for %s", suffix, path)


@app.callback(invoke_without_command=True)
def play(
    ctx: typer.Context,
    path_or_id: str = typer.Argument(..., help="File path or short commit ID."),
) -> None:
    """Play an audio artifact via macOS afplay, or open MIDI in system default app."""
    _guard_macos()
    root = require_repo()

    async def _run() -> pathlib.Path:
        async with open_session() as session:
            return await resolve_artifact_async(path_or_id, root=root, session=session)

    try:
        resolved = asyncio.run(_run())
        _play_path(resolved)
    except typer.Exit:
        raise
    except subprocess.CalledProcessError as exc:
        typer.echo(f"❌ muse play failed: {exc}")
        logger.error("❌ muse play subprocess error: %s", exc)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
    except Exception as exc:
        typer.echo(f"❌ muse play failed: {exc}")
        logger.error("❌ muse play error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
