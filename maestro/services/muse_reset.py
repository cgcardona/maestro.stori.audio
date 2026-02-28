"""Muse Reset Service — move the branch pointer to a prior commit.

Implements three reset modes that mirror git's semantics, adapted for the
Muse VCS filesystem model (``muse-work/`` working tree, ``.muse/refs/`` branch
pointers, ``.muse/objects/`` content-addressed blob store):

- **soft**  — advance/retreat the branch ref; muse-work/ and the object
  store are left completely untouched.  A subsequent ``muse commit``
  captures the current working tree on top of the new HEAD.

- **mixed** (default) — same as soft for the branch ref; semantically
  marks the index as "unstaged".  In the current Muse model (no explicit
  staging area) this is equivalent to soft.  Exists for API symmetry with
  git and for forward-compatibility when a staging index is added.

- **hard** — moves the branch ref AND overwrites ``muse-work/`` with the
  exact file contents captured in the target commit's snapshot.  Files are
  restored via :mod:`maestro.muse_cli.object_store` (the canonical blob
  store shared by all Muse commands).  Any files in ``muse-work/`` that
  are NOT in the target snapshot are deleted.

HEAD~N syntax
-------------
``resolve_ref`` understands ``HEAD``, ``HEAD~N``, a full 64-char SHA, and
any SHA prefix of ≥ 4 characters.  N-step parent traversal walks
``parent_commit_id`` only (primary parent for linear history); merge
parents (``parent2_commit_id``) are ignored for the ``~N`` walk.

Merge-in-progress guard
-----------------------
Reset is blocked when ``.muse/MERGE_STATE.json`` exists.  A merge in
progress must be completed or aborted before resetting.

Object store contract
---------------------
Hard reset requires that every object in the target snapshot's manifest
exists in ``.muse/objects/``.  Objects are written there by ``muse commit``
via :mod:`maestro.muse_cli.object_store`.  If an object is missing, hard
reset raises ``MissingObjectError`` rather than silently leaving the working
tree in a partial state.

This module is a pure service layer — no Typer, no CLI, no StateStore.
Import boundary: may import muse_cli.{db,models,merge_engine,snapshot,
object_store}, but NOT executor, maestro_handlers, mcp, or StateStore.
"""
from __future__ import annotations

import enum
import logging
import pathlib
import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.db import (
    get_commit_snapshot_manifest,
)
from maestro.muse_cli.merge_engine import read_merge_state
from maestro.muse_cli.models import MuseCliCommit
from maestro.muse_cli.object_store import has_object, object_path, restore_object

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

_HEAD_TILDE_RE = re.compile(r"^HEAD~(\d+)$", re.IGNORECASE)


class ResetMode(str, enum.Enum):
    """Three-level reset hierarchy, mirroring git semantics.

    Attributes:
        SOFT:  Move branch pointer only; working tree and object store unchanged.
        MIXED: Move branch pointer and conceptually reset the index.
               Equivalent to SOFT in the current Muse model (no staging area).
        HARD:  Move branch pointer AND overwrite muse-work/ with the target snapshot.
    """

    SOFT = "soft"
    MIXED = "mixed"
    HARD = "hard"


@dataclass(frozen=True)
class ResetResult:
    """Outcome of a completed ``muse reset`` operation.

    Attributes:
        target_commit_id: Full SHA of the commit the branch now points to.
        mode:             The reset mode that was applied.
        branch:           Name of the branch that was reset.
        files_restored:   Number of files written to muse-work/ (hard only).
        files_deleted:    Number of files deleted from muse-work/ (hard only).
    """

    target_commit_id: str
    mode: ResetMode
    branch: str
    files_restored: int = 0
    files_deleted: int = 0


class MissingObjectError(Exception):
    """Raised when a hard reset cannot find required blob content.

    Attributes:
        object_id:  The missing content-addressed object SHA.
        rel_path:   File path in the snapshot that required this object.
    """

    def __init__(self, object_id: str, rel_path: str) -> None:
        super().__init__(
            f"Object {object_id[:8]} missing from .muse/objects/ "
            f"(required by {rel_path!r}). "
            "Commit the working tree first to populate the object store."
        )
        self.object_id = object_id
        self.rel_path = rel_path


# ---------------------------------------------------------------------------
# Ref resolution
# ---------------------------------------------------------------------------


async def resolve_ref(
    session: AsyncSession,
    repo_id: str,
    branch: str,
    ref: str,
) -> MuseCliCommit | None:
    """Resolve a user-supplied commit reference to a ``MuseCliCommit`` row.

    Understands the following ref syntaxes (all case-insensitive for keywords):

    - ``HEAD``     — most recent commit on *branch*.
    - ``HEAD~N``   — N steps back from HEAD along the primary parent chain.
    - ``<sha>``    — exact 64-character commit SHA.
    - ``<prefix>`` — any prefix of ≥ 1 character; returns first match.

    Args:
        session:  Open async DB session.
        repo_id:  Repository ID (from ``.muse/repo.json``).
        branch:   Current branch name (used for HEAD resolution).
        ref:      User-supplied reference string.

    Returns:
        The resolved ``MuseCliCommit`` row, or ``None`` when not found.
    """
    from sqlalchemy.future import select

    # ── HEAD or HEAD~N ───────────────────────────────────────────────────
    tilde_match = _HEAD_TILDE_RE.match(ref)
    is_head = ref.upper() == "HEAD"

    if is_head or tilde_match:
        # Resolve HEAD first
        result = await session.execute(
            select(MuseCliCommit)
            .where(
                MuseCliCommit.repo_id == repo_id,
                MuseCliCommit.branch == branch,
            )
            .order_by(MuseCliCommit.committed_at.desc())
            .limit(1)
        )
        head_commit = result.scalar_one_or_none()
        if head_commit is None:
            return None
        if is_head:
            return head_commit

        # Walk N parents back (primary parent only)
        assert tilde_match is not None  # guaranteed: tilde_match truthy → not None
        n_steps = int(tilde_match.group(1))
        current: MuseCliCommit | None = head_commit
        for _ in range(n_steps):
            if current is None or not current.parent_commit_id:
                return None
            current = await session.get(MuseCliCommit, current.parent_commit_id)
        return current

    # ── Exact SHA match ──────────────────────────────────────────────────
    if len(ref) == 64:
        return await session.get(MuseCliCommit, ref)

    # ── SHA prefix match ─────────────────────────────────────────────────
    result2 = await session.execute(
        select(MuseCliCommit).where(
            MuseCliCommit.repo_id == repo_id,
            MuseCliCommit.commit_id.startswith(ref),
        )
    )
    return result2.scalars().first()


# ---------------------------------------------------------------------------
# Core reset logic
# ---------------------------------------------------------------------------


async def perform_reset(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    ref: str,
    mode: ResetMode,
) -> ResetResult:
    """Execute a Muse VCS reset operation.

    Moves the current branch's HEAD pointer to *ref* and, for hard mode,
    overwrites ``muse-work/`` with the target snapshot's file content.

    This function is the testable async core — it performs all filesystem
    and DB I/O.  The Typer CLI wrapper in ``muse_cli/commands/reset.py``
    handles argument parsing, user confirmation, and error display.

    Raises:
        typer.Exit:        On user-facing errors (merge in progress, ref not found,
                           branch has no commits).
        MissingObjectError: When ``--hard`` cannot find a required blob in the
                           object store.

    Args:
        root:    Muse repository root (directory containing ``.muse/``).
        session: Open async DB session.
        ref:     Commit reference string (e.g. ``HEAD~2``, ``abc123``).
        mode:    Which reset mode to apply.

    Returns:
        ``ResetResult`` describing the completed operation.
    """
    import typer
    from maestro.muse_cli.errors import ExitCode

    muse_dir = root / ".muse"

    # ── Guard: merge in progress ─────────────────────────────────────────
    if read_merge_state(root) is not None:
        typer.echo(
            "❌ Merge in progress. Resolve conflicts or abort the merge before "
            "running muse reset."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Repo identity ────────────────────────────────────────────────────
    import json

    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    # ── Current branch ───────────────────────────────────────────────────
    head_ref = (muse_dir / "HEAD").read_text().strip()  # "refs/heads/main"
    branch = head_ref.rsplit("/", 1)[-1]  # "main"
    ref_path = muse_dir / pathlib.Path(head_ref)

    if not ref_path.exists() or not ref_path.read_text().strip():
        typer.echo("❌ Current branch has no commits. Nothing to reset.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Resolve target commit ─────────────────────────────────────────────
    target_commit = await resolve_ref(session, repo_id, branch, ref)
    if target_commit is None:
        typer.echo(f"❌ Could not resolve ref: {ref!r}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    target_commit_id = target_commit.commit_id

    # ── soft / mixed: only move the branch pointer ────────────────────────
    if mode in (ResetMode.SOFT, ResetMode.MIXED):
        ref_path.write_text(target_commit_id)
        logger.info(
            "✅ muse reset --%s: branch %r → %s",
            mode.value,
            branch,
            target_commit_id[:8],
        )
        return ResetResult(
            target_commit_id=target_commit_id,
            mode=mode,
            branch=branch,
        )

    # ── hard: restore muse-work/ from the target snapshot ────────────────
    assert mode is ResetMode.HARD

    manifest = await get_commit_snapshot_manifest(session, target_commit_id)
    if manifest is None:
        typer.echo(
            f"❌ Could not load snapshot for commit {target_commit_id[:8]}. "
            "Database may be corrupt."
        )
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    # Validate all objects exist before touching the working tree.
    for rel_path, object_id in manifest.items():
        if not has_object(root, object_id):
            raise MissingObjectError(object_id, rel_path)

    workdir = root / "muse-work"
    workdir.mkdir(parents=True, exist_ok=True)

    # Build set of current files in muse-work/ for deletion tracking.
    current_files: set[pathlib.Path] = {
        f for f in workdir.rglob("*") if f.is_file() and not f.name.startswith(".")
    }

    files_restored = 0
    target_paths: set[pathlib.Path] = set()

    for rel_path, object_id in manifest.items():
        dest = workdir / rel_path
        restore_object(root, object_id, dest)
        target_paths.add(dest)
        files_restored += 1
        logger.debug("✅ Restored %s from object %s", rel_path, object_id[:8])

    # Delete files not in the target snapshot.
    files_deleted = 0
    for stale_file in current_files - target_paths:
        stale_file.unlink(missing_ok=True)
        files_deleted += 1
        logger.debug("🗑 Deleted stale file %s", stale_file)

    # Remove empty directories left after deletion.
    for dirpath in sorted(workdir.rglob("*"), reverse=True):
        if dirpath.is_dir() and not any(dirpath.iterdir()):
            try:
                dirpath.rmdir()
            except OSError:
                pass

    # Update branch pointer last (after successful worktree restoration).
    ref_path.write_text(target_commit_id)

    logger.info(
        "✅ muse reset --hard: branch %r → %s (%d restored, %d deleted)",
        branch,
        target_commit_id[:8],
        files_restored,
        files_deleted,
    )
    return ResetResult(
        target_commit_id=target_commit_id,
        mode=mode,
        branch=branch,
        files_restored=files_restored,
        files_deleted=files_deleted,
    )
