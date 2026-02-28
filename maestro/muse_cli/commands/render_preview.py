"""muse render-preview — generate an audio preview of a commit's snapshot.

Fetches the MIDI snapshot for a target commit, passes it to the Storpheus
render pipeline, and writes the resulting audio file to disk.  Optionally
opens the file in the system default audio player.

This is the musical equivalent of ``git show <commit>`` + audio playback:
it lets producers hear what the project sounded like at any point in history
without opening a DAW session.

Usage::

    muse render-preview                                 # HEAD → /tmp/muse-preview-<id>.wav
    muse render-preview abc1234                         # specific commit
    muse render-preview --format mp3 --output ./my.mp3 # custom path and format
    muse render-preview --track drums --section chorus  # filtered render
    muse render-preview abc1234 --open                  # render + open in system player

Flags:
    [<commit>]       Short commit ID prefix (default: HEAD).
    --track TEXT     Render only MIDI files matching this track name substring.
    --section TEXT   Render only MIDI files matching this section name substring.
    --format         Output audio format: wav | mp3 | flac (default: wav).
    --open           Open the rendered file in the system default player after rendering.
    --output PATH    Write the preview to a specific path
                     (default: /tmp/muse-preview-<short_id>.<format>).
    --json           Emit structured JSON instead of human-readable output.

This command is read-only — it never creates a new Muse commit or modifies
the working tree.
"""
from __future__ import annotations

import asyncio
import json as json_mod
import logging
import pathlib
import platform
import subprocess
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.config import settings
from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import find_commits_by_prefix, open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.export_engine import resolve_commit_id
from maestro.services.muse_render_preview import (
    PreviewFormat,
    RenderPreviewResult,
    StorpheusRenderUnavailableError,
    render_preview,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


def _default_output_path(commit_id: str, fmt: PreviewFormat) -> pathlib.Path:
    """Return the default /tmp output path for a render-preview.

    Pattern: ``/tmp/muse-preview-<commit8>.<format>``.

    Args:
        commit_id: Full commit ID (uses first 8 chars).
        fmt: Target audio format.

    Returns:
        A Path under /tmp suitable for ephemeral preview files.
    """
    return pathlib.Path(f"/tmp/muse-preview-{commit_id[:8]}.{fmt.value}")


def _open_file(path: pathlib.Path) -> None:
    """Open *path* in the system default application (macOS only).

    Falls back gracefully on non-macOS with a warning rather than crashing,
    since ``muse render-preview`` is otherwise platform-agnostic.

    Args:
        path: Path to the rendered audio file.
    """
    if platform.system() != "Darwin":
        typer.echo(
            "⚠️  --open is only supported on macOS. "
            f"Open manually: {path}"
        )
        return
    try:
        subprocess.run(["open", str(path)], check=True)
        logger.info("✅ muse render-preview: opened %s in system player", path)
    except subprocess.CalledProcessError as exc:
        typer.echo(f"⚠️  Failed to open {path}: {exc}")
        logger.warning("⚠️ muse render-preview: open failed: %s", exc)


async def _render_preview_async(
    *,
    commit_ref: Optional[str],
    fmt: PreviewFormat,
    output: Optional[pathlib.Path],
    track: Optional[str],
    section: Optional[str],
    root: pathlib.Path,
    session: AsyncSession,
) -> RenderPreviewResult:
    """Core render-preview logic — injectable for tests.

    Resolves the commit reference to a full commit ID, loads its snapshot
    manifest from the database, and delegates to the render-preview service.

    Args:
        commit_ref: Short commit ID prefix or None for HEAD.
        fmt: Target audio format.
        output: Explicit output path or None to use the default /tmp path.
        track: Track name filter.
        section: Section name filter.
        root: Muse repository root.
        session: Open async DB session.

    Returns:
        RenderPreviewResult describing the rendered file.

    Raises:
        typer.Exit: On user errors (no commits, bad prefix, empty snapshot).
        StorpheusRenderUnavailableError: When Storpheus is not reachable.
    """
    from maestro.muse_cli.db import get_commit_snapshot_manifest

    try:
        raw_ref = resolve_commit_id(root, commit_ref)
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if len(raw_ref) < 64:
        matches = await find_commits_by_prefix(session, raw_ref)
        if not matches:
            typer.echo(f"❌ No commit found matching prefix '{raw_ref[:8]}'")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        if len(matches) > 1:
            typer.echo(
                f"❌ Ambiguous commit prefix '{raw_ref[:8]}' "
                f"— matches {len(matches)} commits:"
            )
            for c in matches:
                typer.echo(f"   {c.commit_id[:8]}  {c.message[:60]}")
            typer.echo("Use a longer prefix to disambiguate.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        full_commit_id = matches[0].commit_id
    else:
        full_commit_id = raw_ref

    manifest = await get_commit_snapshot_manifest(session, full_commit_id)
    if manifest is None:
        typer.echo(f"❌ Commit {full_commit_id[:8]} not found in database.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if not manifest:
        typer.echo(
            f"⚠️  Snapshot for commit {full_commit_id[:8]} is empty — nothing to render."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    out_path = output if output is not None else _default_output_path(full_commit_id, fmt)
    storpheus_url = settings.storpheus_base_url

    return render_preview(
        manifest=manifest,
        root=root,
        commit_id=full_commit_id,
        output_path=out_path,
        fmt=fmt,
        track=track,
        section=section,
        storpheus_url=storpheus_url,
    )


@app.callback(invoke_without_command=True)
def render_preview_cmd(
    ctx: typer.Context,
    commit: Optional[str] = typer.Argument(
        None,
        help="Short commit ID prefix to preview (default: HEAD).",
        show_default=False,
    ),
    fmt: PreviewFormat = typer.Option(
        PreviewFormat.WAV,
        "--format",
        "-f",
        help="Output audio format: wav | mp3 | flac.",
        case_sensitive=False,
    ),
    output: Optional[pathlib.Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the preview to this path (default: /tmp/muse-preview-<id>.<format>).",
    ),
    track: Optional[str] = typer.Option(
        None,
        "--track",
        help="Render only MIDI files matching this track name substring.",
    ),
    section: Optional[str] = typer.Option(
        None,
        "--section",
        help="Render only MIDI files matching this section name substring.",
    ),
    open_after: bool = typer.Option(
        False,
        "--open",
        help="Open the rendered preview in the system default audio player.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON output for agent consumption.",
    ),
) -> None:
    """Generate an audio preview of a commit's MIDI snapshot.

    Retrieves the snapshot for COMMIT (default: HEAD), renders its MIDI
    content to audio via Storpheus, and writes the result to disk.

    Use --open to hear the preview immediately.  Use --json for structured
    output suitable for AI agent consumption.

    Supported formats: wav (default), mp3, flac.
    """
    root = require_repo()

    async def _run() -> RenderPreviewResult:
        async with open_session() as session:
            return await _render_preview_async(
                commit_ref=commit,
                fmt=fmt,
                output=output,
                track=track,
                section=section,
                root=root,
                session=session,
            )

    try:
        result = asyncio.run(_run())
    except typer.Exit:
        raise
    except StorpheusRenderUnavailableError as exc:
        typer.echo(f"❌ Storpheus not reachable — render aborted.\n{exc}")
        logger.error("muse render-preview: Storpheus unavailable: %s", exc)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        logger.error("muse render-preview: %s", exc)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except Exception as exc:
        typer.echo(f"❌ muse render-preview failed: {exc}")
        logger.error("muse render-preview error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if as_json:
        payload = {
            "commit_id": result.commit_id,
            "commit_short": result.commit_id[:8],
            "output_path": str(result.output_path),
            "format": result.format.value,
            "midi_files_used": result.midi_files_used,
            "skipped_count": result.skipped_count,
            "stubbed": result.stubbed,
        }
        typer.echo(json_mod.dumps(payload, indent=2))
    else:
        if result.stubbed:
            typer.echo(
                f"⚠️  Preview generated (stub — Storpheus /render not yet deployed):\n"
                f"   {result.output_path}"
            )
        else:
            typer.echo(
                f"✅ Preview rendered [{result.format.value}]:\n"
                f"   {result.output_path}"
            )
        if result.midi_files_used > 1:
            typer.echo(f"   ({result.midi_files_used} MIDI files used)")
        if result.skipped_count:
            typer.echo(f"   ({result.skipped_count} entries skipped)")

    logger.info(
        "muse render-preview: commit=%s format=%s output=%s stubbed=%s",
        result.commit_id[:8],
        result.format.value,
        result.output_path,
        result.stubbed,
    )

    if open_after:
        _open_file(result.output_path)
