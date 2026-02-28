"""Muse Revert Service — create a new commit that undoes a prior commit.

Revert is the safe undo: given a target commit C with parent P, it creates
a new commit whose snapshot is P's snapshot (the state before C was applied).
History is preserved — no commit is deleted or rewritten.

For path-scoped reverts (--track, --section), only paths matching the filter
prefix are reverted to P's state; all other paths remain at HEAD's state.

Boundary rules:
  - Must NOT import StateStore, EntityRegistry, or get_or_create_store.
  - Must NOT import executor modules or maestro_* handlers.
  - May import muse_cli.db, muse_cli.models, muse_cli.merge_engine,
    muse_cli.snapshot.

Domain analogy: a producer accidentally committed a bad drum arrangement.
``muse revert <commit>`` creates a new "undo commit" so the DAW history
shows what happened and when, rather than silently rewriting the timeline.
"""
from __future__ import annotations

import datetime
import logging
import pathlib
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.db import (
    get_commit_snapshot_manifest,
    get_head_snapshot_id,
    insert_commit,
    resolve_commit_ref,
    upsert_snapshot,
)
from maestro.muse_cli.merge_engine import read_merge_state
from maestro.muse_cli.models import MuseCliCommit
from maestro.muse_cli.snapshot import compute_commit_id, compute_snapshot_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RevertResult:
    """Outcome of a ``muse revert`` operation.

    Attributes:
        commit_id: The new commit ID created by the revert (empty when
            ``no_commit=True`` or when there was nothing to revert).
        target_commit_id: The commit that was reverted.
        parent_commit_id: The parent of the reverted commit (whose snapshot
            the revert restores).
        revert_snapshot_id: Snapshot ID of the new reverted state.
        message: The auto-generated or user-supplied commit message.
        no_commit: True when the revert was staged but not committed.
        noop: True when reverting would produce no change.
        scoped_paths: Paths that were selectively reverted (empty = full revert).
        paths_deleted: Paths removed from muse-work/ during ``--no-commit``.
        paths_missing: Paths that could not be restored (no bytes on disk);
            only populated for ``--no-commit`` runs.
        branch: Branch on which the revert commit was created.
    """

    commit_id: str
    target_commit_id: str
    parent_commit_id: str
    revert_snapshot_id: str
    message: str
    no_commit: bool
    noop: bool
    scoped_paths: tuple[str, ...]
    paths_deleted: tuple[str, ...]
    paths_missing: tuple[str, ...]
    branch: str


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _filter_paths(
    manifest: dict[str, str],
    track: Optional[str],
    section: Optional[str],
) -> set[str]:
    """Return the set of paths in *manifest* that match the given filters.

    A path matches if it starts with ``tracks/<track>/`` (for --track)
    or ``sections/<section>/`` (for --section).  When both are supplied the
    union of matching paths is returned.

    Returns all paths in *manifest* when neither filter is given.
    """
    if not track and not section:
        return set(manifest.keys())

    matched: set[str] = set()
    for path in manifest:
        if track and path.startswith(f"tracks/{track}/"):
            matched.add(path)
        if section and path.startswith(f"sections/{section}/"):
            matched.add(path)
    return matched


def compute_revert_manifest(
    *,
    parent_manifest: dict[str, str],
    head_manifest: dict[str, str],
    track: Optional[str] = None,
    section: Optional[str] = None,
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Compute the manifest that represents the reverted state.

    For an unscoped revert the result is ``parent_manifest`` verbatim.
    For a scoped revert (--track or --section) the result is ``head_manifest``
    with the filtered paths replaced by their values from ``parent_manifest``
    (or removed if they did not exist in the parent).

    Returns:
        Tuple of (revert_manifest, scoped_paths_tuple).  ``scoped_paths_tuple``
        is empty for an unscoped revert.

    Pure function — no I/O, no DB.
    """
    if not track and not section:
        return dict(parent_manifest), ()

    # Identify paths affected by the filter across both manifests
    filter_targets = _filter_paths(parent_manifest, track, section) | _filter_paths(
        head_manifest, track, section
    )

    result = dict(head_manifest)
    for path in filter_targets:
        if path in parent_manifest:
            result[path] = parent_manifest[path]
        else:
            # Path existed at HEAD but not in parent → remove it
            result.pop(path, None)

    return result, tuple(sorted(filter_targets))


# ---------------------------------------------------------------------------
# Filesystem materialization (--no-commit)
# ---------------------------------------------------------------------------


def apply_revert_to_workdir(
    *,
    workdir: pathlib.Path,
    revert_manifest: dict[str, str],
    current_manifest: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Update *workdir* to match *revert_manifest* as closely as possible.

    Because the Muse object store does not retain file bytes (only sha256
    hashes), this function can only:

    1. **Delete** files present in *current_manifest* but absent from
       *revert_manifest* — these are paths that the reverted commit introduced.
    2. **Warn** about files present in *revert_manifest* but absent from or
       changed in *workdir* — these need manual restoration.

    Args:
        workdir: Absolute path to ``muse-work/``.
        revert_manifest: The target manifest (parent's or scoped mix).
        current_manifest: The manifest of *workdir* as it stands now.

    Returns:
        Tuple of (paths_deleted, paths_missing):
        - ``paths_deleted``: relative paths successfully removed from *workdir*.
        - ``paths_missing``: relative paths that should exist in the revert
          state but whose bytes are unavailable (no object store) — the caller
          must warn the user and ask for manual intervention.
    """
    deleted: list[str] = []
    missing: list[str] = []

    # Remove paths that should not exist after revert
    for path in sorted(current_manifest):
        if path not in revert_manifest:
            abs_path = workdir / path
            try:
                abs_path.unlink()
                deleted.append(path)
                logger.info("✅ Removed %s from muse-work/", path)
            except OSError as exc:
                logger.warning("⚠️ Could not remove %s: %s", path, exc)

    # Identify paths that need restoration but can't be done automatically
    for path, expected_oid in sorted(revert_manifest.items()):
        current_oid = current_manifest.get(path)
        if current_oid != expected_oid:
            missing.append(path)
            logger.warning(
                "⚠️ Cannot restore %s — file bytes not in object store. "
                "Restore manually or re-run without --no-commit.",
                path,
            )

    return deleted, missing


# ---------------------------------------------------------------------------
# Async core
# ---------------------------------------------------------------------------


async def _revert_async(
    *,
    commit_ref: str,
    root: pathlib.Path,
    session: AsyncSession,
    no_commit: bool = False,
    track: Optional[str] = None,
    section: Optional[str] = None,
) -> RevertResult:
    """Core revert pipeline — resolve, validate, and execute the revert.

    Called by the CLI callback and by tests.  All filesystem and DB
    side-effects are isolated here so tests can inject an in-memory
    SQLite session and a ``tmp_path`` root.

    Args:
        commit_ref: Commit ID (full or abbreviated) to revert.
        root: Repo root (must contain ``.muse/``).
        session: Async DB session (caller owns commit/rollback lifecycle).
        no_commit: When ``True``, stage changes to muse-work/ but do not
            create a new commit record.
        track: Optional track/instrument path prefix filter.
        section: Optional section path prefix filter.

    Returns:
        :class:`RevertResult` describing what happened.

    Raises:
        ``typer.Exit`` with an appropriate exit code on user-facing errors.
    """
    import json

    import typer

    from maestro.muse_cli.errors import ExitCode
    from maestro.muse_cli.snapshot import build_snapshot_manifest

    muse_dir = root / ".muse"

    # ── Guard: block revert during in-progress merge ─────────────────────
    merge_state = read_merge_state(root)
    if merge_state is not None and merge_state.conflict_paths:
        typer.echo(
            "❌ Revert blocked: unresolved merge conflicts in progress.\n"
            "   Resolve all conflicts, then run 'muse commit' before reverting."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Repo identity ────────────────────────────────────────────────────
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    head_ref = (muse_dir / "HEAD").read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1]

    # ── Resolve target commit ────────────────────────────────────────────
    target_commit = await resolve_commit_ref(session, repo_id, branch, commit_ref)
    if target_commit is None:
        typer.echo(f"❌ Commit not found: {commit_ref!r}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    target_commit_id = target_commit.commit_id

    # ── Resolve HEAD commit ───────────────────────────────────────────────
    head_commit = await resolve_commit_ref(session, repo_id, branch, None)
    head_snapshot_id = head_commit.snapshot_id if head_commit else None

    # ── Get manifests ────────────────────────────────────────────────────
    # Parent manifest: the state before the target commit was applied
    parent_manifest: dict[str, str] = {}
    parent_commit_id: str = ""

    if target_commit.parent_commit_id:
        parent_commit_id = target_commit.parent_commit_id
        parent_snapshot = await get_commit_snapshot_manifest(session, parent_commit_id)
        if parent_snapshot is not None:
            parent_manifest = parent_snapshot
    # If target is the root commit (no parent), reverting it means an empty state

    head_manifest: dict[str, str] = {}
    if head_snapshot_id:
        head_snap = await get_commit_snapshot_manifest(session, target_commit_id)
        # head_manifest is HEAD's snapshot, not target's — use head_commit
        if head_commit:
            from sqlalchemy.future import select
            from maestro.muse_cli.models import MuseCliSnapshot
            snap_row = await session.get(MuseCliSnapshot, head_commit.snapshot_id)
            if snap_row is not None:
                head_manifest = dict(snap_row.manifest)

    # ── Compute revert manifest ──────────────────────────────────────────
    revert_manifest, scoped_paths = compute_revert_manifest(
        parent_manifest=parent_manifest,
        head_manifest=head_manifest,
        track=track,
        section=section,
    )

    revert_snapshot_id = compute_snapshot_id(revert_manifest)

    # ── Nothing-to-revert guard ──────────────────────────────────────────
    if head_snapshot_id and revert_snapshot_id == head_snapshot_id:
        typer.echo("Nothing to revert — working tree already matches the reverted state.")
        return RevertResult(
            commit_id="",
            target_commit_id=target_commit_id,
            parent_commit_id=parent_commit_id,
            revert_snapshot_id=revert_snapshot_id,
            message="",
            no_commit=no_commit,
            noop=True,
            scoped_paths=scoped_paths,
            paths_deleted=(),
            paths_missing=(),
            branch=branch,
        )

    # ── Auto-generate commit message ─────────────────────────────────────
    revert_message = f"Revert '{target_commit.message}'"

    # ── --no-commit: apply to working tree only ──────────────────────────
    if no_commit:
        workdir = root / "muse-work"
        current_manifest = build_snapshot_manifest(workdir) if workdir.exists() else {}
        paths_deleted, paths_missing = apply_revert_to_workdir(
            workdir=workdir,
            revert_manifest=revert_manifest,
            current_manifest=current_manifest,
        )
        if paths_missing:
            typer.echo(
                "⚠️  Some files cannot be restored automatically (bytes not in object store):\n"
                + "\n".join(f"   missing: {p}" for p in sorted(paths_missing))
            )
        if paths_deleted:
            typer.echo(
                "✅ Staged revert (--no-commit). Files removed:\n"
                + "\n".join(f"   deleted: {p}" for p in sorted(paths_deleted))
            )
        else:
            typer.echo("⚠️  --no-commit: no file deletions were needed.")
        return RevertResult(
            commit_id="",
            target_commit_id=target_commit_id,
            parent_commit_id=parent_commit_id,
            revert_snapshot_id=revert_snapshot_id,
            message=revert_message,
            no_commit=True,
            noop=False,
            scoped_paths=scoped_paths,
            paths_deleted=tuple(sorted(paths_deleted)),
            paths_missing=tuple(sorted(paths_missing)),
            branch=branch,
        )

    # ── Persist the revert snapshot (objects already in DB) ──────────────
    await upsert_snapshot(session, manifest=revert_manifest, snapshot_id=revert_snapshot_id)
    await session.flush()

    # ── Persist the revert commit ─────────────────────────────────────────
    head_commit_id = head_commit.commit_id if head_commit else None
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    new_commit_id = compute_commit_id(
        parent_ids=[head_commit_id] if head_commit_id else [],
        snapshot_id=revert_snapshot_id,
        message=revert_message,
        committed_at_iso=committed_at.isoformat(),
    )

    new_commit = MuseCliCommit(
        commit_id=new_commit_id,
        repo_id=repo_id,
        branch=branch,
        parent_commit_id=head_commit_id,
        snapshot_id=revert_snapshot_id,
        message=revert_message,
        author="",
        committed_at=committed_at,
    )
    await insert_commit(session, new_commit)

    # ── Update branch HEAD pointer ────────────────────────────────────────
    ref_path = muse_dir / pathlib.Path(head_ref)
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(new_commit_id)

    scope_note = ""
    if scoped_paths:
        scope_note = f" (scoped to {len(scoped_paths)} path(s))"
    typer.echo(
        f"✅ [{branch} {new_commit_id[:8]}] {revert_message}{scope_note}"
    )
    logger.info(
        "✅ muse revert %s → %s on %r: %s",
        target_commit_id[:8],
        new_commit_id[:8],
        branch,
        revert_message,
    )

    return RevertResult(
        commit_id=new_commit_id,
        target_commit_id=target_commit_id,
        parent_commit_id=parent_commit_id,
        revert_snapshot_id=revert_snapshot_id,
        message=revert_message,
        no_commit=False,
        noop=False,
        scoped_paths=scoped_paths,
        paths_deleted=(),
        paths_missing=(),
        branch=branch,
    )
