"""muse export — export a Muse snapshot to external formats.

Usage::

    muse export [<commit>] --format midi --output /tmp/song.mid
    muse export --format json
    muse export --format musicxml --track piano
    muse export --format midi --split-tracks
    muse export --format wav   # fails clearly when Storpheus is down

Flags:
    <commit>          Short commit ID prefix (default: HEAD).
    --format          Target format: midi | json | musicxml | abc | wav.
    --track           Export only files matching this track name substring.
    --section         Export only files matching this section name substring.
    --output PATH     Destination path (default: ./exports/<commit8>.<format>).
    --split-tracks    Write one file per track (MIDI only).

This command is read-only — it never creates a new commit or modifies the
working tree.  The same commit + format always produces identical output.
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.config import settings
from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.artifact_resolver import _find_commits_by_prefix
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.export_engine import (
    ExportFormat,
    MuseExportOptions,
    MuseExportResult,
    StorpheusUnavailableError,
    export_snapshot,
    resolve_commit_id,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

_DEFAULT_EXPORTS_DIR = "exports"


def _default_output_path(commit_id: str, fmt: ExportFormat) -> pathlib.Path:
    """Return the default output path for an export.

    Pattern: ``./exports/<commit8>.<format>``.  For MIDI with --split-tracks
    or multi-file exports the caller converts this to a directory.
    """
    short = commit_id[:8]
    ext = fmt.value
    return pathlib.Path(_DEFAULT_EXPORTS_DIR) / f"{short}.{ext}"


async def _export_async(
    *,
    commit_ref: Optional[str],
    fmt: ExportFormat,
    output: Optional[pathlib.Path],
    track: Optional[str],
    section: Optional[str],
    split_tracks: bool,
    root: pathlib.Path,
    session: AsyncSession,
) -> MuseExportResult:
    """Core export logic — injectable for tests.

    Resolves the commit, loads the snapshot manifest, and dispatches to the
    appropriate format handler via export_snapshot.

    Args:
        commit_ref: Short commit ID prefix or None for HEAD.
        fmt: Target export format.
        output: Explicit output path or None for default.
        track: Track name filter.
        section: Section name filter.
        split_tracks: Whether to write one file per track (MIDI only).
        root: Muse repository root.
        session: Open async DB session.

    Returns:
        MuseExportResult describing what was written.

    Raises:
        typer.Exit: On user errors (no commits, bad prefix, etc.).
        StorpheusUnavailableError: When WAV and Storpheus is down.
    """
    from maestro.muse_cli.db import get_commit_snapshot_manifest
    from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot

    # Resolve commit ID from filesystem HEAD or prefix lookup.
    try:
        raw_ref = resolve_commit_id(root, commit_ref)
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # If a prefix was supplied, look it up in the DB.
    if len(raw_ref) < 64:
        matches = await _find_commits_by_prefix(session, raw_ref)
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

    # Load the snapshot manifest.
    manifest = await get_commit_snapshot_manifest(session, full_commit_id)
    if manifest is None:
        typer.echo(f"❌ Commit {full_commit_id[:8]} not found in database.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if not manifest:
        typer.echo(f"⚠️  Snapshot for commit {full_commit_id[:8]} is empty — nothing to export.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Resolve output path.
    out_path = output if output is not None else _default_output_path(full_commit_id, fmt)

    storpheus_url = getattr(settings, "storpheus_base_url", "http://localhost:10002")

    opts = MuseExportOptions(
        format=fmt,
        commit_id=full_commit_id,
        output_path=out_path,
        track=track,
        section=section,
        split_tracks=split_tracks,
    )

    return export_snapshot(manifest, root, opts, storpheus_url=storpheus_url)


@app.callback(invoke_without_command=True)
def export(
    ctx: typer.Context,
    commit: Optional[str] = typer.Argument(
        None,
        help="Short commit ID prefix to export (default: HEAD).",
        show_default=False,
    ),
    fmt: ExportFormat = typer.Option(
        ...,
        "--format",
        "-f",
        help="Export format: midi | json | musicxml | abc | wav.",
        case_sensitive=False,
    ),
    output: Optional[pathlib.Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output path (default: ./exports/<commit8>.<format>).",
    ),
    track: Optional[str] = typer.Option(
        None,
        "--track",
        help="Export only files matching this track name substring.",
    ),
    section: Optional[str] = typer.Option(
        None,
        "--section",
        help="Export only files matching this section name substring.",
    ),
    split_tracks: bool = typer.Option(
        False,
        "--split-tracks",
        help="Write one file per track (MIDI only).",
    ),
) -> None:
    """Export a Muse snapshot to an external format.

    Exports the snapshot referenced by COMMIT (default: HEAD) to the
    specified format.  This is a read-only operation — no commit is created.

    Supported formats:
      midi      Raw MIDI file(s) — native format, lossless.
      json      Structured JSON note index (AI/tooling consumption).
      musicxml  MusicXML for notation software (MuseScore, Sibelius, etc.).
      abc       ABC notation for folk/traditional music tools.
      wav       Audio render via Storpheus (requires Storpheus running).
    """
    root = require_repo()

    async def _run() -> MuseExportResult:
        async with open_session() as session:
            return await _export_async(
                commit_ref=commit,
                fmt=fmt,
                output=output,
                track=track,
                section=section,
                split_tracks=split_tracks,
                root=root,
                session=session,
            )

    try:
        result = asyncio.run(_run())
    except typer.Exit:
        raise
    except StorpheusUnavailableError as exc:
        typer.echo(f"❌ WAV export requires Storpheus.\n{exc}")
        logger.error("WAV export: Storpheus unavailable: %s", exc)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
    except Exception as exc:
        typer.echo(f"❌ muse export failed: {exc}")
        logger.error("muse export error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    # Report results.
    if not result.paths_written:
        typer.echo(
            f"⚠️  No {fmt.value} files found in snapshot {result.commit_id[:8]}."
        )
        if result.skipped_count:
            typer.echo(f"   ({result.skipped_count} files skipped — wrong type or missing.)")
        raise typer.Exit(code=ExitCode.SUCCESS)

    typer.echo(f"✅ Exported {len(result.paths_written)} file(s) [{fmt.value}]:")
    for p in result.paths_written:
        typer.echo(f"   {p}")
    logger.info(
        "muse export: commit=%s format=%s files=%d",
        result.commit_id[:8],
        fmt.value,
        len(result.paths_written),
    )
