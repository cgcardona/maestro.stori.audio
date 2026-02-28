"""muse show <commit> — music-aware commit inspection.

The musician's equivalent of ``git show``: displays metadata, snapshot
contents, and optional music-native views for any historical commit.

**Default output** (human-readable)::

    commit a1b2c3d4e5f6...
    Branch: main
    Author: producer@stori.app
    Date:   2026-02-27 17:30:00

        Add bridge section with Rhodes keys

    Snapshot: 3 files
      beat.mid
      keys.mid
      bass.mid

**Flag summary:**

- ``--json``          — full commit metadata + snapshot manifest as JSON
- ``--diff``          — path-level diff vs parent commit (A/M/D markers)
- ``--midi``          — list MIDI files in the snapshot
- ``--audio-preview`` — generate and open audio preview of the snapshot (macOS)

**Commit resolution** (same strategy as ``muse arrange``):

1. If the ref looks like a hex string (4–64 chars) → prefix match in the DB.
2. Otherwise → treat as a branch name and read from ``.muse/refs/heads/``.
3. ``HEAD`` → read from current ``HEAD`` ref pointer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import subprocess
from typing import Optional

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import TypedDict

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot

logger = logging.getLogger(__name__)

app = typer.Typer()

_HEX_CHARS = frozenset("0123456789abcdef")

# MIDI file extensions recognised by the show command
_MIDI_EXTENSIONS = frozenset({".mid", ".midi", ".smf"})


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class ShowCommitResult(TypedDict):
    """Full commit metadata for ``muse show``.

    Returned by ``_show_async`` and consumed by both the human-readable
    renderer and the JSON serialiser. Includes the snapshot manifest so
    callers can list files, MIDI files, and compute diffs without a second
    DB round-trip.

    Music-domain fields are surfaced at the top level for easy agent
    consumption (sourced from ``commit_metadata`` in the DB).  All are
    ``None`` when the commit was created without the corresponding flag.
    """

    commit_id: str
    branch: str
    parent_commit_id: Optional[str]
    parent2_commit_id: Optional[str]
    message: str
    author: str
    committed_at: str
    snapshot_id: str
    snapshot_manifest: dict[str, str]
    # Music-domain metadata (from commit_metadata JSON blob)
    section: Optional[str]
    track: Optional[str]
    emotion: Optional[str]


class ShowDiffResult(TypedDict):
    """Path-level diff of a commit vs its parent.

    Produced by ``_diff_vs_parent`` and used by ``--diff`` rendering.
    """

    commit_id: str
    parent_commit_id: Optional[str]
    added: list[str]
    modified: list[str]
    removed: list[str]
    total_changed: int


# ---------------------------------------------------------------------------
# Commit resolution helpers
# ---------------------------------------------------------------------------


def _looks_like_hex_prefix(s: str) -> bool:
    """Return True if *s* is a 4–64 character lowercase hex string."""
    lower = s.lower()
    return 4 <= len(lower) <= 64 and all(c in _HEX_CHARS for c in lower)


async def _resolve_commit(
    session: AsyncSession,
    muse_dir: pathlib.Path,
    ref: str,
) -> MuseCliCommit:
    """Resolve a commit reference to a ``MuseCliCommit`` row.

    Resolution order:
    1. ``HEAD`` (case-insensitive) → follow the HEAD ref file.
    2. Hex prefix (4–64 chars) → prefix match against ``commit_id`` in DB.
    3. Anything else → treat as a branch name and read the tip ref file.

    Raises ``typer.Exit`` with ``USER_ERROR`` when the ref cannot be resolved.
    """
    if ref.upper() == "HEAD" or not _looks_like_hex_prefix(ref):
        # Branch name or HEAD
        if ref.upper() == "HEAD":
            head_ref_text = (muse_dir / "HEAD").read_text().strip()
            ref_path = muse_dir / pathlib.Path(head_ref_text)
        else:
            ref_path = muse_dir / "refs" / "heads" / ref

        if not ref_path.exists():
            typer.echo(f"❌ Reference '{ref}' not found.")
            raise typer.Exit(code=ExitCode.USER_ERROR)

        commit_id = ref_path.read_text().strip()
        if not commit_id:
            typer.echo(f"❌ Reference '{ref}' has no commits yet.")
            raise typer.Exit(code=ExitCode.USER_ERROR)

        commit = await session.get(MuseCliCommit, commit_id)
        if commit is None:
            typer.echo(f"❌ Commit {commit_id[:8]} not found in database.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        return commit

    # Hex prefix: try exact match first, then startswith
    exact = await session.get(MuseCliCommit, ref)
    if exact is not None:
        return exact

    prefix = ref.lower()
    result = await session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id.startswith(prefix))
    )
    matches = list(result.scalars().all())

    if not matches:
        typer.echo(f"❌ No commit found matching '{prefix[:8]}'.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    if len(matches) > 1:
        typer.echo(f"❌ Ambiguous prefix '{prefix[:8]}' matches {len(matches)} commits:")
        for c in matches:
            typer.echo(f"   {c.commit_id[:8]}  {c.message[:60]}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    return matches[0]


async def _load_snapshot(
    session: AsyncSession, commit: MuseCliCommit
) -> dict[str, str]:
    """Load the snapshot manifest for *commit*.

    Returns an empty dict when the snapshot is missing (shouldn't happen in a
    consistent DB, but handled gracefully to avoid crashing the display path).
    """
    snapshot = await session.get(MuseCliSnapshot, commit.snapshot_id)
    if snapshot is None:
        logger.warning(
            "⚠️ Snapshot %s for commit %s missing from DB",
            commit.snapshot_id[:8],
            commit.commit_id[:8],
        )
        return {}
    return dict(snapshot.manifest)


# ---------------------------------------------------------------------------
# Core async logic — fully injectable for tests
# ---------------------------------------------------------------------------


async def _show_async(
    *,
    session: AsyncSession,
    muse_dir: pathlib.Path,
    ref: str,
) -> ShowCommitResult:
    """Load commit metadata and snapshot manifest for *ref*.

    Used by the Typer command and directly by tests.  All I/O goes through
    *session* — no filesystem side-effects beyond reading ``.muse/`` refs.
    """
    commit = await _resolve_commit(session, muse_dir, ref)
    manifest = await _load_snapshot(session, commit)

    # Extract music-domain metadata from the extensible JSON blob.
    raw_metadata: dict[str, object] = dict(commit.commit_metadata or {})

    return ShowCommitResult(
        commit_id=commit.commit_id,
        branch=commit.branch,
        parent_commit_id=commit.parent_commit_id,
        parent2_commit_id=commit.parent2_commit_id,
        message=commit.message,
        author=commit.author,
        committed_at=commit.committed_at.strftime("%Y-%m-%d %H:%M:%S"),
        snapshot_id=commit.snapshot_id,
        snapshot_manifest=manifest,
        section=str(raw_metadata["section"]) if "section" in raw_metadata else None,
        track=str(raw_metadata["track"]) if "track" in raw_metadata else None,
        emotion=str(raw_metadata["emotion"]) if "emotion" in raw_metadata else None,
    )


async def _diff_vs_parent_async(
    *,
    session: AsyncSession,
    muse_dir: pathlib.Path,
    ref: str,
) -> ShowDiffResult:
    """Compute the path-level diff of *ref* vs its parent commit.

    For the root commit (no parent) every path in the snapshot is "added".
    """
    commit = await _resolve_commit(session, muse_dir, ref)
    manifest = await _load_snapshot(session, commit)

    parent_manifest: dict[str, str] = {}
    if commit.parent_commit_id:
        parent_commit = await session.get(MuseCliCommit, commit.parent_commit_id)
        if parent_commit is not None:
            parent_manifest = await _load_snapshot(session, parent_commit)
        else:
            logger.warning(
                "⚠️ Parent %s not found; treating as empty",
                commit.parent_commit_id[:8],
            )

    all_paths = sorted(set(manifest) | set(parent_manifest))
    added: list[str] = []
    modified: list[str] = []
    removed: list[str] = []

    for path in all_paths:
        cur = manifest.get(path)
        par = parent_manifest.get(path)
        if par is None:
            added.append(path)
        elif cur is None:
            removed.append(path)
        elif cur != par:
            modified.append(path)

    return ShowDiffResult(
        commit_id=commit.commit_id,
        parent_commit_id=commit.parent_commit_id,
        added=added,
        modified=modified,
        removed=removed,
        total_changed=len(added) + len(modified) + len(removed),
    )


def _midi_files_in_manifest(manifest: dict[str, str]) -> list[str]:
    """Return the subset of manifest paths whose extension is a MIDI extension."""
    return sorted(
        path for path in manifest if pathlib.Path(path).suffix.lower() in _MIDI_EXTENSIONS
    )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_show(result: ShowCommitResult) -> None:
    """Print commit metadata in ``git show`` style."""
    typer.echo(f"commit {result['commit_id']}")
    typer.echo(f"Branch:  {result['branch']}")
    if result["author"]:
        typer.echo(f"Author:  {result['author']}")
    typer.echo(f"Date:    {result['committed_at']}")
    if result["parent_commit_id"]:
        typer.echo(f"Parent:  {result['parent_commit_id'][:8]}")
    if result["parent2_commit_id"]:
        typer.echo(f"Parent2: {result['parent2_commit_id'][:8]}")
    # Music-domain metadata (only shown when present)
    if result["section"]:
        typer.echo(f"Section: {result['section']}")
    if result["track"]:
        typer.echo(f"Track:   {result['track']}")
    if result["emotion"]:
        typer.echo(f"Emotion: {result['emotion']}")
    typer.echo("")
    typer.echo(f"    {result['message']}")
    typer.echo("")

    manifest = result["snapshot_manifest"]
    paths = sorted(manifest)
    typer.echo(f"Snapshot: {len(paths)} file{'s' if len(paths) != 1 else ''}")
    for p in paths:
        typer.echo(f"  {p}")


def _render_diff(diff: ShowDiffResult) -> None:
    """Print path-level diff vs parent in ``git diff --name-status`` style."""
    short = diff["commit_id"][:8]
    parent_short = diff["parent_commit_id"][:8] if diff["parent_commit_id"] else "(root)"
    typer.echo(f"diff {parent_short}..{short}")
    typer.echo("")

    for p in diff["added"]:
        typer.echo(f"A  {p}")
    for p in diff["modified"]:
        typer.echo(f"M  {p}")
    for p in diff["removed"]:
        typer.echo(f"D  {p}")

    if diff["total_changed"] == 0:
        typer.echo("(no changes vs parent)")
    else:
        typer.echo(f"\n{diff['total_changed']} path(s) changed")


def _render_midi(manifest: dict[str, str], commit_id: str) -> None:
    """List MIDI files contained in the snapshot."""
    midi_files = _midi_files_in_manifest(manifest)
    short = commit_id[:8]
    if not midi_files:
        typer.echo(f"No MIDI files in snapshot {short}.")
        return

    typer.echo(f"MIDI files in snapshot {short} ({len(midi_files)}):")
    for path in midi_files:
        obj_id = manifest[path]
        typer.echo(f"  {path}  ({obj_id[:8]})")


def _render_audio_preview(commit_id: str, root: pathlib.Path) -> None:
    """Trigger an audio preview for the commit's snapshot (macOS, stub).

    The full implementation would call the Storpheus render-preview pipeline
    and stream the result to ``afplay``.  This stub prints the resolved path
    and launches ``afplay`` on any pre-rendered WAV file in the export cache,
    falling back to a clear help message when nothing is cached.
    """
    short = commit_id[:8]
    export_dir = root / ".muse" / "exports" / short
    if not export_dir.exists():
        typer.echo(
            f"⚠️  No cached audio preview for commit {short}.\n"
            f"    Run: muse export {short} --wav  to render first, then retry."
        )
        return

    wav_files = sorted(export_dir.glob("*.wav"))
    if not wav_files:
        typer.echo(
            f"⚠️  Export directory exists but contains no WAV files for {short}.\n"
            f"    Run: muse export {short} --wav  to regenerate."
        )
        return

    wav = wav_files[0]
    typer.echo(f"▶ Playing {wav.name} (commit {short}) …")
    try:
        subprocess.run(["afplay", str(wav)], check=True)
    except FileNotFoundError:
        typer.echo("❌ afplay not found — audio preview requires macOS.")
    except subprocess.CalledProcessError as exc:
        typer.echo(f"❌ afplay exited with code {exc.returncode}.")


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def show(
    ctx: typer.Context,
    commit: Optional[str] = typer.Argument(
        default=None,
        help=(
            "Commit ID (full or prefix), branch name, or HEAD. "
            "Defaults to HEAD when omitted."
        ),
        metavar="COMMIT",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output complete commit metadata and snapshot manifest as JSON.",
    ),
    diff: bool = typer.Option(
        False,
        "--diff",
        help="Show path-level diff vs parent commit (A/M/D markers).",
    ),
    midi: bool = typer.Option(
        False,
        "--midi",
        help="List MIDI files contained in the commit snapshot.",
    ),
    audio_preview: bool = typer.Option(
        False,
        "--audio-preview",
        help="Open cached audio preview for this snapshot (macOS). "
        "Run `muse export <commit> --wav` first to render.",
    ),
) -> None:
    """Inspect a commit: metadata, snapshot, diff, and music-native views.

    Equivalent to ``git show`` — lets you inspect any historical creative
    decision in the Muse VCS.  The ``--midi`` and ``--audio-preview`` flags
    make it music-native, allowing direct playback of historical snapshots.

    Without flags, prints commit metadata and snapshot file list.
    Flags can be combined: ``muse show abc1234 --diff --midi``.
    """
    if ctx.invoked_subcommand is not None:
        return

    ref = commit or "HEAD"
    root = require_repo()
    muse_dir = root / ".muse"

    async def _run() -> None:
        async with open_session() as session:
            result = await _show_async(session=session, muse_dir=muse_dir, ref=ref)

            if as_json:
                typer.echo(json.dumps(dict(result), indent=2))
                return

            # Default metadata view (always shown unless --json)
            _render_show(result)

            if diff:
                diff_result = await _diff_vs_parent_async(
                    session=session, muse_dir=muse_dir, ref=ref
                )
                typer.echo("")
                _render_diff(diff_result)

            if midi:
                typer.echo("")
                _render_midi(result["snapshot_manifest"], result["commit_id"])

        if audio_preview:
            typer.echo("")
            _render_audio_preview(result["commit_id"], root)

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse show failed: {exc}")
        logger.error("❌ muse show error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
