"""muse transpose — apply MIDI pitch transposition as a Muse commit.

Transposes all MIDI files in ``muse-work/`` by the given interval and records
the result as a new Muse commit.  Drum channels (MIDI channel 9) are always
excluded from pitch transposition because drums are unpitched.

Usage
-----
::

    muse transpose +3                          # up 3 semitones from HEAD
    muse transpose -5                          # down 5 semitones from HEAD
    muse transpose up-minor3rd                 # named interval
    muse transpose down-perfect5th --track melody  # single-track scope
    muse transpose +2 --section chorus         # section scope (stub)
    muse transpose +3 --dry-run                # preview without committing
    muse transpose +3 --json                   # machine-readable result
    muse transpose +2 <commit>                 # transpose from a named commit

Interval syntax
---------------
- Signed integers: ``+3``, ``-5``, ``+12``
- Named intervals: ``up-minor3rd``, ``down-perfect5th``, ``up-octave``

Named interval identifiers
--------------------------
unison, minor2nd, major2nd, minor3rd, major3rd, perfect4th,
perfect5th, minor6th, major6th, minor7th, major7th, octave
(prefix with ``up-`` or ``down-``)

Key metadata
------------
If the source commit has a ``key`` field in its ``metadata`` JSON blob
(e.g. set via a future ``muse key --set`` command), the new commit's
``metadata.key`` is automatically updated to reflect the transposition
(e.g. ``"Eb major"`` + 2 semitones → ``"F major"``).
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import pathlib
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import (
    get_head_snapshot_id,
    insert_commit,
    open_session,
    resolve_commit_ref,
    upsert_object,
    upsert_snapshot,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit
from maestro.muse_cli.snapshot import (
    build_snapshot_manifest,
    compute_commit_id,
    compute_snapshot_id,
)
from maestro.services.muse_transpose import (
    TransposeResult,
    apply_transpose_to_workdir,
    parse_interval,
    update_key_metadata,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


# ---------------------------------------------------------------------------
# Repo context helper (shared with tempo.py pattern)
# ---------------------------------------------------------------------------


def _read_repo_context(root: pathlib.Path) -> tuple[str, str, str]:
    """Return ``(repo_id, branch, head_commit_id_or_empty)`` from ``.muse/``."""
    muse_dir = root / ".muse"
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]
    head_ref = (muse_dir / "HEAD").read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1]
    ref_path = muse_dir / pathlib.Path(head_ref)
    head_commit_id = ref_path.read_text().strip() if ref_path.exists() else ""
    return repo_id, branch, head_commit_id


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _transpose_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    semitones: int,
    commit_ref: str | None,
    track_filter: str | None,
    section_filter: str | None,
    message: str | None,
    dry_run: bool,
    as_json: bool,
) -> TransposeResult:
    """Apply transposition and optionally commit the result.

    This is the injectable async core used by tests and the Typer callback.
    All filesystem and DB side-effects are isolated here so tests can inject
    an in-memory SQLite session and a ``tmp_path`` root.

    Workflow:
    1. Resolve source commit (default HEAD).
    2. Extract ``metadata.key`` from the source commit (if any).
    3. Apply transposition to all MIDI files in ``muse-work/``.
    4. Unless ``--dry-run``, create a new Muse commit and update HEAD.
    5. Annotate the new commit with updated key metadata.
    6. Return a ``TransposeResult`` and print human/JSON output.

    Args:
        root:           Repository root (directory containing ``.muse/``).
        session:        Open async DB session; committed by caller.
        semitones:      Signed semitone offset to apply.
        commit_ref:     Commit SHA or ref to transpose from, or ``None`` for HEAD.
        track_filter:   Case-insensitive track name substring filter, or ``None``.
        section_filter: Section name filter (stub — logged as not implemented).
        message:        Custom commit message, or ``None`` for auto-generated.
        dry_run:        When True, do not write files or create a commit.
        as_json:        When True, emit JSON output instead of human text.

    Returns:
        ``TransposeResult`` with all fields populated.

    Raises:
        ``typer.Exit``: On user errors (missing commit, parse failure) or
                        internal errors (DB failure, I/O error).
    """
    repo_id, branch, _ = _read_repo_context(root)

    # ── Resolve source commit ─────────────────────────────────────────────
    commit = await resolve_commit_ref(session, repo_id, branch, commit_ref)
    if commit is None:
        ref_label = commit_ref or "HEAD"
        typer.echo(f"❌ No commit found for ref '{ref_label}'")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    source_commit_id = commit.commit_id

    # ── Extract existing key metadata ─────────────────────────────────────
    meta: dict[str, object] = dict(commit.commit_metadata or {})
    original_key: str | None = None
    new_key: str | None = None
    key_raw = meta.get("key")
    if isinstance(key_raw, str) and key_raw:
        original_key = key_raw
        new_key = update_key_metadata(original_key, semitones)
        logger.debug("✅ Key: %r → %r", original_key, new_key)

    # ── Apply transposition to muse-work/ ────────────────────────────────
    workdir = root / "muse-work"
    files_modified, files_skipped = apply_transpose_to_workdir(
        workdir=workdir,
        semitones=semitones,
        track_filter=track_filter,
        section_filter=section_filter,
        dry_run=dry_run,
    )

    if dry_run:
        result = TransposeResult(
            source_commit_id=source_commit_id,
            semitones=semitones,
            files_modified=files_modified,
            files_skipped=files_skipped,
            new_commit_id=None,
            original_key=original_key,
            new_key=new_key,
            dry_run=True,
        )
        _print_result(result, as_json=as_json)
        return result

    if not files_modified:
        typer.echo(
            "⚠️  No MIDI files were modified. "
            "Check that muse-work/ contains .mid files and the interval is non-zero."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Build snapshot from modified workdir ──────────────────────────────
    if not workdir.exists():
        typer.echo("❌ muse-work/ directory not found — cannot create commit")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest = build_snapshot_manifest(workdir)
    if not manifest:
        typer.echo("❌ muse-work/ is empty — nothing to commit")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    snapshot_id = compute_snapshot_id(manifest)

    # Guard: nothing changed (shouldn't happen, but be safe)
    last_snapshot_id = await get_head_snapshot_id(session, repo_id, branch)
    if last_snapshot_id == snapshot_id:
        typer.echo("Nothing to commit — working tree unchanged after transposition")
        raise typer.Exit(code=ExitCode.SUCCESS)

    # ── Persist objects ───────────────────────────────────────────────────
    for rel_path, object_id in manifest.items():
        file_path = workdir / rel_path
        size = file_path.stat().st_size
        await upsert_object(session, object_id=object_id, size_bytes=size)

    # ── Persist snapshot ──────────────────────────────────────────────────
    await upsert_snapshot(session, manifest=manifest, snapshot_id=snapshot_id)
    await session.flush()

    # ── Compute and persist commit ────────────────────────────────────────
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    interval_label = f"{semitones:+d} semitones" if semitones != 0 else "0 semitones"
    effective_message = message or f"Transpose {interval_label}"
    commit_metadata: dict[str, object] = dict(meta)
    if new_key is not None:
        commit_metadata["key"] = new_key

    parent_commit_id = source_commit_id
    new_commit_id = compute_commit_id(
        parent_ids=[parent_commit_id],
        snapshot_id=snapshot_id,
        message=effective_message,
        committed_at_iso=committed_at.isoformat(),
    )

    new_commit = MuseCliCommit(
        commit_id=new_commit_id,
        repo_id=repo_id,
        branch=branch,
        parent_commit_id=parent_commit_id,
        snapshot_id=snapshot_id,
        message=effective_message,
        author="",
        committed_at=committed_at,
        commit_metadata=commit_metadata if commit_metadata else None,
    )
    await insert_commit(session, new_commit)

    # ── Update branch HEAD pointer ────────────────────────────────────────
    muse_dir = root / ".muse"
    head_ref = (muse_dir / "HEAD").read_text().strip()
    ref_path = muse_dir / pathlib.Path(head_ref)
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(new_commit_id)

    result = TransposeResult(
        source_commit_id=source_commit_id,
        semitones=semitones,
        files_modified=files_modified,
        files_skipped=files_skipped,
        new_commit_id=new_commit_id,
        original_key=original_key,
        new_key=new_key,
        dry_run=False,
    )
    _print_result(result, as_json=as_json)
    return result


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _print_result(result: TransposeResult, *, as_json: bool) -> None:
    """Render a TransposeResult as human-readable text or JSON."""
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "source_commit_id": result.source_commit_id,
                    "semitones": result.semitones,
                    "files_modified": result.files_modified,
                    "files_skipped": result.files_skipped,
                    "new_commit_id": result.new_commit_id,
                    "original_key": result.original_key,
                    "new_key": result.new_key,
                    "dry_run": result.dry_run,
                },
                indent=2,
            )
        )
        return

    prefix = "DRY RUN — " if result.dry_run else ""
    if result.new_commit_id:
        typer.echo(
            f"✅ {prefix}[{result.new_commit_id[:8]}] Transpose {result.semitones:+d} semitones"
        )
    else:
        typer.echo(f"{prefix}Transpose {result.semitones:+d} semitones")

    if result.original_key and result.new_key:
        typer.echo(f"   Key: {result.original_key}  →  {result.new_key}")

    typer.echo(f"   Modified: {len(result.files_modified)} file(s)")
    for f in result.files_modified:
        typer.echo(f"     ✅ {f}")

    if result.files_skipped:
        typer.echo(f"   Skipped:  {len(result.files_skipped)} file(s) (non-MIDI or no pitched notes)")

    if result.dry_run:
        typer.echo("   (dry-run: no files written, no commit created)")


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def transpose(
    ctx: typer.Context,
    interval: str = typer.Argument(
        ...,
        metavar="<interval>",
        help=(
            "Interval to transpose by. "
            "Signed integer (+3, -5) or named interval (up-minor3rd, down-perfect5th)."
        ),
    ),
    commit_ref: Optional[str] = typer.Argument(
        None,
        metavar="[<commit>]",
        help="Source commit SHA or 'HEAD' (default: HEAD).",
    ),
    track: Optional[str] = typer.Option(
        None,
        "--track",
        metavar="TEXT",
        help="Transpose only the MIDI track whose name contains TEXT (case-insensitive).",
    ),
    section: Optional[str] = typer.Option(
        None,
        "--section",
        metavar="TEXT",
        help="Transpose only the named section (stub — full implementation pending).",
    ),
    message: Optional[str] = typer.Option(
        None,
        "--message",
        "-m",
        metavar="TEXT",
        help="Custom commit message (default: 'Transpose +N semitones').",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would change without writing files or creating a commit.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON output.",
    ),
) -> None:
    """Apply MIDI pitch transposition and record it as a Muse commit.

    Transposes all MIDI files in ``muse-work/`` by the given interval,
    then creates a new commit capturing the transposed snapshot.  Drum
    channels (MIDI channel 9) are always excluded.

    Use ``--dry-run`` to preview what would change without committing.
    Use ``--track`` to restrict transposition to a specific instrument track.
    """
    # Parse interval first — fail fast before touching the repo
    try:
        semitones = parse_interval(interval)
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _transpose_async(
                root=root,
                session=session,
                semitones=semitones,
                commit_ref=commit_ref,
                track_filter=track,
                section_filter=section,
                message=message,
                dry_run=dry_run,
                as_json=as_json,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse transpose failed: {exc}")
        logger.error("❌ muse transpose error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
