"""Muse Restore Service — restore specific files from a commit or index.

``muse restore`` is surgical: restore one instrument track from a specific
commit while keeping everything else at HEAD.  Critical for music production
where you want "the bass from take 3, everything else from take 7."

Restore modes
-------------
**worktree** (default, ``--worktree``)
    Copy the file content recorded in the *source* snapshot directly into
    ``muse-work/``.  Branch pointer and index are not changed.  This is the
    primary use case: "put the bass from take 3 back into my working tree."

**staged** (``--staged``)
    In a full VCS with an explicit staging area this would reset the index
    entry for the path from the source snapshot without touching ``muse-work/``.
    In the current Muse model (no separate staging area) ``--staged`` is
    documented for forward-compatibility and behaves identically to
    ``--worktree``: it restores the file in ``muse-work/`` from the source
    snapshot.  When a staging index is added this module will be updated.

**source** (``--source <commit>``)
    Selects which snapshot to extract the file from.  Defaults to ``HEAD``
    when omitted.

Object store contract
---------------------
Restore reads objects from ``.muse/objects/`` exactly like ``muse reset
--hard``.  If an object is missing, :class:`MissingObjectError` is raised
and ``muse-work/`` is left unchanged (the restore is atomic per path).

This module is a pure service layer — no Typer, no CLI, no StateStore.
Import boundary: may import muse_cli.{db,models,object_store}, muse_reset
(for MissingObjectError and resolve_ref), but NOT executor,
maestro_handlers, mcp, or StateStore.
"""
from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.db import get_commit_snapshot_manifest
from maestro.muse_cli.object_store import has_object, restore_object
from maestro.services.muse_reset import (
    MissingObjectError,
    resolve_ref,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RestoreResult:
    """Outcome of a completed ``muse restore`` operation.

    Attributes:
        source_commit_id: Full SHA of the commit the files were extracted from.
        paths_restored:   Relative paths (within ``muse-work/``) that were
                          written to disk.
        staged:           Whether ``--staged`` mode was active.
    """

    source_commit_id: str
    paths_restored: list[str] = field(default_factory=list)
    staged: bool = False


class PathNotInSnapshotError(Exception):
    """Raised when a requested path is absent from the source snapshot.

    Attributes:
        rel_path:         The path that was not found.
        source_commit_id: The commit that was searched.
    """

    def __init__(self, rel_path: str, source_commit_id: str) -> None:
        super().__init__(
            f"Path {rel_path!r} not found in snapshot of commit "
            f"{source_commit_id[:8]}. "
            "Use 'muse log' to list commits and 'muse show <commit>' to inspect "
            "the snapshot manifest."
        )
        self.rel_path = rel_path
        self.source_commit_id = source_commit_id


# ---------------------------------------------------------------------------
# Core restore logic
# ---------------------------------------------------------------------------


async def perform_restore(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    paths: list[str],
    source_ref: str | None,
    staged: bool,
) -> RestoreResult:
    """Restore specific files from a source commit into ``muse-work/``.

    Resolves the source commit reference, validates that every requested path
    exists in the snapshot manifest, verifies all required objects are present
    in the object store (fail-fast before touching ``muse-work/``), then copies
    each file atomically.

    The branch pointer is never modified — only ``muse-work/`` files are written.

    Args:
        root:       Muse repository root (directory containing ``.muse/``).
        session:    Open async DB session.
        paths:      Relative paths within ``muse-work/`` to restore.  Must be
                    non-empty.  Paths may be given as ``muse-work/bass/bassline.mid``
                    or bare ``bass/bassline.mid`` — the ``muse-work/`` prefix is
                    stripped if present.
        source_ref: Commit reference to restore from (``HEAD``, ``HEAD~N``, SHA,
                    or ``None`` for HEAD).
        staged:     When ``True``, ``--staged`` mode is active.  In the current
                    Muse model (no separate staging area) this is semantically
                    equivalent to ``--worktree``.

    Returns:
        :class:`RestoreResult` describing the completed operation.

    Raises:
        typer.Exit:              On user-facing errors (ref not found, no commits).
        PathNotInSnapshotError:  When any requested path is absent from the source.
        MissingObjectError:      When a required blob is absent from the object store.
    """
    import json

    import typer

    from maestro.muse_cli.errors import ExitCode

    muse_dir = root / ".muse"

    # ── Repo identity ────────────────────────────────────────────────────
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    # ── Current branch ───────────────────────────────────────────────────
    head_ref = (muse_dir / "HEAD").read_text().strip()  # "refs/heads/main"
    branch = head_ref.rsplit("/", 1)[-1]  # "main"
    ref_path = muse_dir / pathlib.Path(head_ref)

    if not ref_path.exists() or not ref_path.read_text().strip():
        typer.echo("❌ Current branch has no commits. Nothing to restore.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Resolve source commit ─────────────────────────────────────────────
    effective_ref = source_ref if source_ref is not None else "HEAD"
    source_commit = await resolve_ref(session, repo_id, branch, effective_ref)
    if source_commit is None:
        typer.echo(f"❌ Could not resolve source ref: {effective_ref!r}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    source_commit_id = source_commit.commit_id

    # ── Load snapshot manifest ────────────────────────────────────────────
    manifest = await get_commit_snapshot_manifest(session, source_commit_id)
    if manifest is None:
        typer.echo(
            f"❌ Could not load snapshot for commit {source_commit_id[:8]}. "
            "Database may be corrupt."
        )
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    # ── Normalise paths — strip leading "muse-work/" prefix if present ────
    normalised: list[str] = []
    for p in paths:
        stripped = p.removeprefix("muse-work/")
        normalised.append(stripped)

    # ── Validate: every path must be in the manifest ─────────────────────
    for rel_path in normalised:
        if rel_path not in manifest:
            raise PathNotInSnapshotError(rel_path, source_commit_id)

    # ── Validate: every object must exist (fail-fast before touching disk) ─
    for rel_path in normalised:
        object_id = manifest[rel_path]
        if not has_object(root, object_id):
            raise MissingObjectError(object_id, rel_path)

    # ── Restore files into muse-work/ ─────────────────────────────────────
    workdir = root / "muse-work"
    workdir.mkdir(parents=True, exist_ok=True)

    restored: list[str] = []
    for rel_path in normalised:
        object_id = manifest[rel_path]
        dest = workdir / rel_path
        restore_object(root, object_id, dest)
        restored.append(rel_path)
        logger.debug(
            "✅ Restored %s from object %s (commit %s)",
            rel_path,
            object_id[:8],
            source_commit_id[:8],
        )

    mode_label = "--staged" if staged else "--worktree"
    logger.info(
        "✅ muse restore %s: %d file(s) from commit %s",
        mode_label,
        len(restored),
        source_commit_id[:8],
    )

    return RestoreResult(
        source_commit_id=source_commit_id,
        paths_restored=restored,
        staged=staged,
    )
