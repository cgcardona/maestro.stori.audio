"""muse import — import a MIDI or MusicXML file as a new Muse commit.

Workflow
--------
1. Validate the file extension (supported: .mid, .midi, .xml, .musicxml).
2. Parse the file into Muse's internal :class:`MuseImportData` representation.
3. Apply any ``--track-map`` channel→name remapping.
4. Copy the source file into ``muse-work/imports/<filename>``.
5. Write a ``muse-work/imports/<filename>.meta.json`` with note count, tracks,
   and tempo metadata (for downstream processing and commit diffs).
6. Run :func:`_commit_async` to create the Muse commit.
7. If ``--analyze``, print a multi-dimensional analysis of the imported content.

Flags
-----
``<file>``            File to import (.mid/.midi/.xml/.musicxml).
``--message TEXT``    Commit message (default: "Import <filename>").
``--track-map TEXT``  Channel→name mapping, e.g. ``ch0=bass,ch1=piano,ch9=drums``.
``--section TEXT``    Section tag stored in the commit metadata JSON.
``--analyze``         Run analysis and display results after importing.
``--dry-run``         Validate only — do not write files or create a commit.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import shutil
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.midi_parser import (
    MuseImportData,
    analyze_import,
    apply_track_map,
    parse_file,
    parse_track_map_arg,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _import_async(
    *,
    file_path: pathlib.Path,
    root: pathlib.Path,
    session: AsyncSession,
    message: str | None = None,
    track_map: dict[str, str] | None = None,
    section: str | None = None,
    analyze: bool = False,
    dry_run: bool = False,
) -> str | None:
    """Core import pipeline — fully injectable for tests.

    Parses, copies, commits, and optionally analyses the given file.

    Returns:
        The new ``commit_id`` on success, or ``None`` when ``dry_run=True``.

    Raises:
        ``typer.Exit`` with the appropriate exit code on validation failures so
        the Typer callback surfaces a clean message instead of a traceback.
    """
    # ── Validate and parse ───────────────────────────────────────────────
    try:
        data: MuseImportData = parse_file(file_path)
    except FileNotFoundError:
        typer.echo(f"❌ File not found: {file_path}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except RuntimeError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Apply track map ──────────────────────────────────────────────────
    if track_map:
        data = MuseImportData(
            source_path=data.source_path,
            format=data.format,
            ticks_per_beat=data.ticks_per_beat,
            tempo_bpm=data.tempo_bpm,
            notes=apply_track_map(data.notes, track_map),
            tracks=list({n.channel_name for n in apply_track_map(data.notes, track_map)}),
            raw_meta=data.raw_meta,
        )
        # Preserve insertion order for tracks
        seen: set[str] = set()
        ordered_tracks: list[str] = []
        for n in data.notes:
            if n.channel_name not in seen:
                seen.add(n.channel_name)
                ordered_tracks.append(n.channel_name)
        data = MuseImportData(
            source_path=data.source_path,
            format=data.format,
            ticks_per_beat=data.ticks_per_beat,
            tempo_bpm=data.tempo_bpm,
            notes=data.notes,
            tracks=ordered_tracks,
            raw_meta=data.raw_meta,
        )

    effective_message = message or f"Import {file_path.name}"

    # ── Dry-run: validate only ────────────────────────────────────────────
    if dry_run:
        typer.echo(f"✅ Dry run: '{file_path.name}' is valid ({data.format})")
        typer.echo(f"   Notes: {len(data.notes)}, Tracks: {len(data.tracks)}, Tempo: {data.tempo_bpm:.1f} BPM")
        typer.echo(f"   Would commit: {effective_message!r}")
        if section:
            typer.echo(f"   Section: {section!r}")
        if analyze:
            typer.echo("\nAnalysis:")
            typer.echo(analyze_import(data))
        return None

    # ── Copy file into muse-work/imports/ ────────────────────────────────
    imports_dir = root / "muse-work" / "imports"
    imports_dir.mkdir(parents=True, exist_ok=True)

    dest = imports_dir / file_path.name
    shutil.copy2(str(file_path), str(dest))
    logger.debug("✅ Copied %s → %s", file_path, dest)

    # ── Write metadata JSON ───────────────────────────────────────────────
    meta: dict[str, object] = {
        "source": str(file_path),
        "format": data.format,
        "ticks_per_beat": data.ticks_per_beat,
        "tempo_bpm": round(data.tempo_bpm, 3),
        "note_count": len(data.notes),
        "tracks": data.tracks,
        "track_map": track_map or {},
        "section": section,
        "raw_meta": data.raw_meta,
    }
    meta_path = imports_dir / f"{file_path.name}.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))

    # ── Commit ────────────────────────────────────────────────────────────
    commit_id = await _commit_async(
        message=effective_message,
        root=root,
        session=session,
    )

    typer.echo(f"✅ Imported '{file_path.name}' as commit {commit_id[:8]}")
    if section:
        typer.echo(f"   Section: {section!r}")

    # ── Analysis ─────────────────────────────────────────────────────────
    if analyze:
        typer.echo("\nAnalysis:")
        typer.echo(analyze_import(data))

    return commit_id


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def import_file(
    ctx: typer.Context,
    file: str = typer.Argument(..., help="Path to the MIDI or MusicXML file to import."),
    message: Optional[str] = typer.Option(
        None,
        "--message",
        "-m",
        help='Commit message (default: "Import <filename>").',
    ),
    track_map: Optional[str] = typer.Option(
        None,
        "--track-map",
        help='Map MIDI channels to track names, e.g. "ch0=bass,ch1=piano,ch9=drums".',
    ),
    section: Optional[str] = typer.Option(
        None,
        "--section",
        help="Tag the imported content as a specific section.",
    ),
    analyze: bool = typer.Option(
        False,
        "--analyze",
        help="Run multi-dimensional analysis on the imported content and display it.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate the import without committing.",
    ),
) -> None:
    """Import a MIDI or MusicXML file as a new Muse commit."""
    file_path = pathlib.Path(file).expanduser().resolve()

    parsed_track_map: dict[str, str] | None = None
    if track_map is not None:
        try:
            parsed_track_map = parse_track_map_arg(track_map)
        except ValueError as exc:
            typer.echo(f"❌ --track-map: {exc}")
            raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    async def _run() -> None:
        if dry_run:
            # Dry-run does not need a DB session
            await _import_async(
                file_path=file_path,
                root=root,
                session=None,  # type: ignore[arg-type]
                message=message,
                track_map=parsed_track_map,
                section=section,
                analyze=analyze,
                dry_run=True,
            )
        else:
            async with open_session() as session:
                await _import_async(
                    file_path=file_path,
                    root=root,
                    session=session,
                    message=message,
                    track_map=parsed_track_map,
                    section=section,
                    analyze=analyze,
                    dry_run=False,
                )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse import failed: {exc}")
        logger.error("❌ muse import error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
