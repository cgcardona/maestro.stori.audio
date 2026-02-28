"""muse release <tag> — export and render a tagged commit as a release artifact.

This is the music-native publish step: given a tag applied via ``muse tag add``,
it resolves the tagged commit, fetches its MIDI snapshot, and renders it to
audio/MIDI artifacts ready for distribution.

Usage::

    muse release v1.0                                    # manifest only (dry run)
    muse release v1.0 --render-audio                     # single WAV file
    muse release v1.0 --render-midi                      # zip of all MIDI files
    muse release v1.0 --export-stems --format flac       # per-track FLAC stems
    muse release v1.0 --render-audio --render-midi \\
        --output-dir ./dist/v1.0                         # custom output dir

Flags:
    <tag>               Music-semantic tag created via ``muse tag add``.
    --render-audio      Render all MIDI to a single audio file via Storpheus.
    --render-midi       Bundle all .mid files into a zip archive.
    --export-stems      Export each track as a separate audio file.
    --format            Audio output format: wav | mp3 | flac (default: wav).
    --output-dir PATH   Where to write artifacts (default: ./releases/<tag>/).
    --json              Emit structured JSON output for agent consumption.

Output layout::

    <output-dir>/
        release-manifest.json        # always written; SHA-256 checksums
        audio/<commit8>.<format>     # --render-audio
        midi/midi-bundle.zip         # --render-midi
        stems/<stem>.<format>        # --export-stems

This command resolves the tag via the Muse tag database (``muse tag add``).
If multiple commits share the same tag the most recently committed one is used.
"""
from __future__ import annotations

import asyncio
import json as json_mod
import logging
import pathlib
from typing import Optional

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.config import settings
from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import find_commits_by_prefix, get_commit_snapshot_manifest, open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliTag
from maestro.services.muse_release import (
    ReleaseAudioFormat,
    ReleaseResult,
    StorpheusReleaseUnavailableError,
    build_release,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

_DEFAULT_RELEASES_DIR = "releases"


def _default_output_dir(tag: str) -> pathlib.Path:
    """Return the default output directory for a release.

    Pattern: ``./releases/<tag>/``.  Safe for any tag string that is a valid
    directory name — callers should sanitise the tag before passing here.

    Args:
        tag: Release tag string (e.g. ``"v1.0"``).

    Returns:
        A Path relative to the current working directory.
    """
    safe_tag = tag.replace("/", "_").replace("\\", "_")
    return pathlib.Path(_DEFAULT_RELEASES_DIR) / safe_tag


async def _resolve_tag_to_commit(
    session: AsyncSession,
    root: pathlib.Path,
    tag: str,
) -> str:
    """Resolve a music-semantic tag string to a full commit ID.

    Queries the ``muse_cli_tags`` table for commits carrying *tag*.  When
    multiple commits share the tag, the most recently committed one is returned
    — this matches the producer's expectation that ``v1.0`` refers to the
    latest commit labelled with that tag.

    Falls back to prefix-based commit lookup when no tag record is found, so
    producers can also pass a raw commit SHA prefix directly.

    Args:
        session: Open async DB session.
        root: Muse repository root (used to read repo.json).
        tag: Tag string (e.g. ``"v1.0"``) or short commit SHA prefix.

    Returns:
        Full 64-character commit ID.

    Raises:
        typer.Exit: With ``USER_ERROR`` when the tag/prefix cannot be resolved.
    """
    import json

    # Read repo_id for scoped tag lookup.
    repo_json = root / ".muse" / "repo.json"
    repo_id: str | None = None
    if repo_json.exists():
        data: dict[str, str] = json.loads(repo_json.read_text())
        repo_id = data.get("repo_id")

    # 1. Tag-based lookup (join MuseCliTag → MuseCliCommit).
    if repo_id is not None:
        tag_result = await session.execute(
            select(MuseCliTag.commit_id)
            .where(MuseCliTag.repo_id == repo_id, MuseCliTag.tag == tag)
        )
        tag_commit_ids: list[str] = list(tag_result.scalars().all())

        if tag_commit_ids:
            if len(tag_commit_ids) == 1:
                return tag_commit_ids[0]

            # Multiple commits share the tag — return the most recently committed.
            commits_result = await session.execute(
                select(MuseCliCommit)
                .where(MuseCliCommit.commit_id.in_(tag_commit_ids))
                .order_by(MuseCliCommit.committed_at.desc())
                .limit(1)
            )
            latest = commits_result.scalar_one_or_none()
            if latest is not None:
                logger.warning(
                    "⚠️ release: tag %r exists on %d commits — using most recent: %s",
                    tag,
                    len(tag_commit_ids),
                    latest.commit_id[:8],
                )
                return latest.commit_id

    # 2. Prefix-based fallback — treat <tag> as a short commit SHA.
    prefix_matches = await find_commits_by_prefix(session, tag)
    if len(prefix_matches) == 1:
        return prefix_matches[0].commit_id
    if len(prefix_matches) > 1:
        typer.echo(
            f"❌ Ambiguous commit prefix '{tag[:8]}' "
            f"— matches {len(prefix_matches)} commits:"
        )
        for c in prefix_matches:
            typer.echo(f"   {c.commit_id[:8]}  {c.message[:60]}")
        typer.echo("Use a longer prefix or an exact tag string to disambiguate.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    typer.echo(
        f"❌ No commit found for tag or prefix '{tag}'. "
        "Create the tag first: muse tag add <tag> [<commit>]"
    )
    raise typer.Exit(code=ExitCode.USER_ERROR)


async def _release_async(
    *,
    tag: str,
    audio_format: ReleaseAudioFormat,
    output_dir: Optional[pathlib.Path],
    render_audio: bool,
    render_midi: bool,
    export_stems: bool,
    root: pathlib.Path,
    session: AsyncSession,
) -> ReleaseResult:
    """Core release logic — injectable for tests.

    Resolves the tag to a commit ID, loads the snapshot manifest, and
    delegates to ``build_release`` in the service layer.

    Args:
        tag: Tag string or short commit SHA prefix.
        audio_format: Target audio format for rendered files.
        output_dir: Explicit output directory or None for the default path.
        render_audio: Whether to render the primary MIDI to an audio file.
        render_midi: Whether to bundle all MIDI files into a zip archive.
        export_stems: Whether to export each MIDI track as a separate audio file.
        root: Muse repository root.
        session: Open async DB session.

    Returns:
        ReleaseResult describing what was written.

    Raises:
        typer.Exit: On user errors (missing tag, empty snapshot, etc.).
        StorpheusReleaseUnavailableError: When audio render is requested and
            Storpheus is unreachable.
    """
    full_commit_id = await _resolve_tag_to_commit(session, root, tag)

    manifest = await get_commit_snapshot_manifest(session, full_commit_id)
    if manifest is None:
        typer.echo(f"❌ Commit {full_commit_id[:8]} not found in database.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if not manifest:
        typer.echo(
            f"⚠️  Snapshot for commit {full_commit_id[:8]} is empty — nothing to release."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    out_dir = output_dir if output_dir is not None else _default_output_dir(tag)
    storpheus_url = settings.storpheus_base_url

    return build_release(
        tag=tag,
        commit_id=full_commit_id,
        manifest=manifest,
        root=root,
        output_dir=out_dir,
        audio_format=audio_format,
        render_audio=render_audio,
        render_midi=render_midi,
        export_stems=export_stems,
        storpheus_url=storpheus_url,
    )


@app.callback(invoke_without_command=True)
def release(
    ctx: typer.Context,
    tag: str = typer.Argument(
        ...,
        help=(
            "Tag or commit prefix to release (e.g. v1.0). "
            "Tags are created via 'muse tag add <tag>'."
        ),
    ),
    render_audio: bool = typer.Option(
        False,
        "--render-audio",
        help="Render all MIDI snapshots to a single audio file via Storpheus.",
    ),
    render_midi: bool = typer.Option(
        False,
        "--render-midi",
        help="Bundle all .mid files from the snapshot into a zip archive.",
    ),
    export_stems: bool = typer.Option(
        False,
        "--export-stems",
        help="Export each instrument track as a separate audio file.",
    ),
    audio_format: ReleaseAudioFormat = typer.Option(
        ReleaseAudioFormat.WAV,
        "--format",
        "-f",
        help="Audio output format: wav | mp3 | flac (default: wav).",
        case_sensitive=False,
    ),
    output_dir: Optional[pathlib.Path] = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Where to write release artifacts (default: ./releases/<tag>/).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON output for agent consumption.",
    ),
) -> None:
    """Export a tagged commit as distribution-ready release artifacts.

    Resolves TAG to a commit (via ``muse tag add``), fetches its snapshot,
    and produces the requested artifacts under the output directory.  Always
    writes a ``release-manifest.json`` with SHA-256 checksums.

    Examples::

        muse release v1.0 --render-audio
        muse release v1.0 --render-midi --export-stems --format flac
        muse release v1.0 --render-audio --output-dir ~/dist/v1.0
    """
    root = require_repo()

    async def _run() -> ReleaseResult:
        async with open_session() as session:
            return await _release_async(
                tag=tag,
                audio_format=audio_format,
                output_dir=output_dir,
                render_audio=render_audio,
                render_midi=render_midi,
                export_stems=export_stems,
                root=root,
                session=session,
            )

    try:
        result = asyncio.run(_run())
    except typer.Exit:
        raise
    except StorpheusReleaseUnavailableError as exc:
        typer.echo(f"❌ Storpheus not reachable — audio render aborted.\n{exc}")
        logger.error("muse release: Storpheus unavailable: %s", exc)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        logger.error("muse release: %s", exc)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except Exception as exc:
        typer.echo(f"❌ muse release failed: {exc}")
        logger.error("muse release error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if as_json:
        payload = {
            "tag": result.tag,
            "commit_id": result.commit_id,
            "commit_short": result.commit_id[:8],
            "output_dir": str(result.output_dir),
            "manifest_path": str(result.manifest_path),
            "audio_format": result.audio_format.value,
            "stubbed": result.stubbed,
            "artifacts": [
                {
                    "path": str(a.path),
                    "sha256": a.sha256,
                    "size_bytes": a.size_bytes,
                    "role": a.role,
                }
                for a in result.artifacts
            ],
        }
        typer.echo(json_mod.dumps(payload, indent=2))
    else:
        non_manifest = [a for a in result.artifacts if a.role != "manifest"]
        if non_manifest:
            typer.echo(
                f"✅ Release artifacts for tag {result.tag!r} "
                f"(commit {result.commit_id[:8]}):"
            )
            for a in non_manifest:
                typer.echo(f"   [{a.role}] {a.path}")
        else:
            typer.echo(
                f"⚠️  No render flags specified — only manifest written for tag {result.tag!r}."
                "\nUse --render-audio, --render-midi, or --export-stems."
            )

        typer.echo(f"   [manifest] {result.manifest_path}")

        if result.stubbed:
            typer.echo(
                "⚠️  Audio files are MIDI stubs (Storpheus /render endpoint not yet deployed)."
            )

    logger.info(
        "muse release: tag=%r commit=%s output_dir=%s artifacts=%d stubbed=%s",
        result.tag,
        result.commit_id[:8],
        result.output_dir,
        len(result.artifacts),
        result.stubbed,
    )
