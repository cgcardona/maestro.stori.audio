"""Artifact resolution for ``muse open`` / ``muse play``.

Resolves a user-supplied path-or-commit-ID to a concrete ``pathlib.Path``:

- If the argument is an existing filesystem path (absolute or relative to
  ``muse-work/``), return it directly — no DB needed.
- If the argument looks like a commit-ID prefix (4–64 lowercase hex chars),
  query the DB for matching commits, present an interactive selection menu
  when the snapshot contains multiple files, and return the resolved
  working-tree path.

The public async entry point ``resolve_artifact_async`` accepts an injected
``AsyncSession`` so it can be unit-tested without a live database.
The synchronous wrapper ``resolve_artifact`` is suitable for use inside
Typer command callbacks.
"""
from __future__ import annotations

import asyncio
import logging
import pathlib

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.db import find_commits_by_prefix, open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot

logger = logging.getLogger(__name__)

_HEX_CHARS = frozenset("0123456789abcdef")


def _looks_like_commit_prefix(s: str) -> bool:
    """Return True if *s* could be a commit-ID prefix.

    Accepts 4–64 lower-case hex characters.  Intentionally conservative:
    actual filesystem paths that happen to be hex strings are excluded
    early by the existence-check callers perform before calling this.
    """
    lower = s.lower()
    return 4 <= len(lower) <= 64 and all(c in _HEX_CHARS for c in lower)



async def resolve_artifact_async(
    path_or_commit_id: str,
    root: pathlib.Path,
    session: AsyncSession,
) -> pathlib.Path:
    """Resolve *path_or_commit_id* to a concrete working-tree path.

    Resolution order:
    1. Existing absolute/relative path on the filesystem.
    2. Path relative to ``<root>/muse-work/``.
    3. Commit-ID prefix lookup → interactive file selection from snapshot.

    Calls ``typer.Exit(ExitCode.USER_ERROR)`` on any user-facing error so
    Typer surfaces a clean message instead of a traceback.

    Parameters
    ----------
    path_or_commit_id:
        Either a filesystem path or a hex commit-ID prefix (≥ 4 chars).
    root:
        The Muse repository root (containing ``.muse/`` and ``muse-work/``).
    session:
        An open ``AsyncSession`` — injected by callers for testability.
    """
    # ── 1. Direct filesystem path ──────────────────────────────────────────
    candidate = pathlib.Path(path_or_commit_id)
    if candidate.exists():
        return candidate.resolve()

    # ── 2. Relative to muse-work/ ─────────────────────────────────────────
    workdir_candidate = root / "muse-work" / path_or_commit_id
    if workdir_candidate.exists():
        return workdir_candidate.resolve()

    # ── 3. Commit-ID prefix ───────────────────────────────────────────────
    prefix = path_or_commit_id.lower()
    if not _looks_like_commit_prefix(prefix):
        typer.echo(f"❌ File not found: {path_or_commit_id}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    commits = await find_commits_by_prefix(session, prefix)
    if not commits:
        typer.echo(f"❌ No commit found matching prefix '{prefix[:8]}'")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if len(commits) > 1:
        typer.echo(
            f"❌ Ambiguous commit prefix '{prefix[:8]}' — matches {len(commits)} commits:"
        )
        for c in commits:
            typer.echo(f"   {c.commit_id[:8]}  {c.message[:60]}")
        typer.echo("Use a longer prefix to disambiguate.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    commit = commits[0]
    snapshot: MuseCliSnapshot | None = await session.get(MuseCliSnapshot, commit.snapshot_id)
    if snapshot is None or not snapshot.manifest:
        typer.echo(f"❌ Snapshot for commit {commit.commit_id[:8]} is empty.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest: dict[str, str] = snapshot.manifest
    paths = sorted(manifest.keys())

    if len(paths) == 1:
        chosen = paths[0]
    else:
        typer.echo(f"Commit {commit.commit_id[:8]} — {commit.message}")
        typer.echo("Files in this snapshot:")
        for i, p in enumerate(paths, 1):
            typer.echo(f"  [{i}] {p}")
        raw = typer.prompt("Select file number", default="1")
        try:
            idx = int(raw) - 1
            if idx < 0 or idx >= len(paths):
                raise ValueError("out of range")
        except ValueError:
            typer.echo("❌ Invalid selection.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        chosen = paths[idx]

    resolved = root / "muse-work" / chosen
    if not resolved.exists():
        typer.echo(
            f"❌ '{chosen}' from commit {commit.commit_id[:8]} is no longer in muse-work/.\n"
            "   The snapshot references files that have been removed from the working tree."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    logger.info("✅ Resolved '%s' → %s", path_or_commit_id, resolved)
    return resolved.resolve()


def resolve_artifact(
    path_or_commit_id: str,
    root: pathlib.Path,
) -> pathlib.Path:
    """Synchronous wrapper around ``resolve_artifact_async``.

    Opens its own DB session via ``open_session()`` which reads
    ``DATABASE_URL`` from settings.  Suitable for use in Typer command
    callbacks that need a blocking call.
    """

    async def _run() -> pathlib.Path:
        async with open_session() as session:
            return await resolve_artifact_async(
                path_or_commit_id, root=root, session=session
            )

    return asyncio.run(_run())
