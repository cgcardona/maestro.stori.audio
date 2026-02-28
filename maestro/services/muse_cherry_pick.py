"""Muse Cherry-Pick Service — apply a specific commit's diff on top of HEAD.

Cherry-pick is the surgical transplant: given a source commit C with parent P,
compute diff(P → C) and apply that patch to the current HEAD snapshot.  The
result is a new commit whose content is HEAD's snapshot plus the delta that C
introduced, without bringing in any other commits from C's branch.

Algorithm (3-way merge model)
------------------------------
1. Resolve C and its parent P.
2. Load manifests: ``base`` = P, ``ours`` = HEAD, ``theirs`` = C.
3. Compute ``cherry_diff`` = diff(P → C) — the set of paths C changed.
4. Compute ``head_diff`` = diff(P → HEAD) — paths HEAD changed since P.
5. Conflicts = paths in cherry_diff ∩ head_diff where both sides disagree.
6. If conflicts: write ``.muse/CHERRY_PICK_STATE.json`` and exit 1.
7. If clean: build result manifest (HEAD + cherry delta), persist, create commit.

State file: ``.muse/CHERRY_PICK_STATE.json``
---------------------------------------------
Written when conflicts are detected, consumed by ``--continue`` and ``--abort``.

.. code-block:: json

    {
        "cherry_commit":  "abc123...",
        "head_commit":    "def456...",
        "conflict_paths": ["beat.mid"]
    }

Boundary rules:
  - Must NOT import StateStore, EntityRegistry, or get_or_create_store.
  - Must NOT import executor modules or maestro_* handlers.
  - May import muse_cli.db, muse_cli.models, muse_cli.merge_engine,
    muse_cli.snapshot.

Domain analogy: a producer recorded the perfect guitar solo in an experimental
branch.  ``muse cherry-pick <commit>`` transplants just that solo into main,
leaving the other 20 unrelated commits behind.
"""
from __future__ import annotations

import datetime
import json
import logging
import pathlib
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.db import (
    get_commit_snapshot_manifest,
    insert_commit,
    resolve_commit_ref,
    upsert_snapshot,
)
from maestro.muse_cli.merge_engine import diff_snapshots, read_merge_state
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot
from maestro.muse_cli.snapshot import compute_commit_id, compute_snapshot_id

logger = logging.getLogger(__name__)

_CHERRY_PICK_STATE_FILENAME = "CHERRY_PICK_STATE.json"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CherryPickState:
    """Describes an in-progress cherry-pick with unresolved conflicts.

    Attributes:
        cherry_commit:  Commit ID being cherry-picked.
        head_commit:    Commit ID of HEAD when the cherry-pick was initiated.
        conflict_paths: Relative POSIX paths that have unresolved conflicts.
    """

    cherry_commit: str
    head_commit: str
    conflict_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CherryPickResult:
    """Outcome of a ``muse cherry-pick`` operation.

    Attributes:
        commit_id:        New commit ID (empty when ``no_commit=True`` or conflict).
        cherry_commit_id: Source commit that was cherry-picked.
        head_commit_id:   HEAD commit at cherry-pick time.
        new_snapshot_id:  Snapshot ID of the resulting state.
        message:          Commit message (prefixed with cherry-pick attribution).
        no_commit:        True when ``--no-commit`` was requested.
        conflict:         True when conflicts were detected (state file written).
        conflict_paths:   Conflicting paths (non-empty iff ``conflict=True``).
        branch:           Branch on which the new commit was created.
    """

    commit_id: str
    cherry_commit_id: str
    head_commit_id: str
    new_snapshot_id: str
    message: str
    no_commit: bool
    conflict: bool
    conflict_paths: tuple[str, ...]
    branch: str


# ---------------------------------------------------------------------------
# State file helpers
# ---------------------------------------------------------------------------


def read_cherry_pick_state(root: pathlib.Path) -> CherryPickState | None:
    """Return :class:`CherryPickState` if a cherry-pick is in progress, else ``None``.

    Reads ``.muse/CHERRY_PICK_STATE.json``.  Returns ``None`` when absent or unparseable.

    Args:
        root: Repository root (directory containing ``.muse/``).
    """
    path = root / ".muse" / _CHERRY_PICK_STATE_FILENAME
    if not path.exists():
        return None
    try:
        data: dict[str, object] = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("⚠️ Failed to read %s: %s", _CHERRY_PICK_STATE_FILENAME, exc)
        return None

    raw_conflicts = data.get("conflict_paths", [])
    conflict_paths: list[str] = (
        [str(c) for c in raw_conflicts] if isinstance(raw_conflicts, list) else []
    )
    return CherryPickState(
        cherry_commit=str(data.get("cherry_commit", "")),
        head_commit=str(data.get("head_commit", "")),
        conflict_paths=conflict_paths,
    )


def write_cherry_pick_state(
    root: pathlib.Path,
    *,
    cherry_commit: str,
    head_commit: str,
    conflict_paths: list[str],
) -> None:
    """Write ``.muse/CHERRY_PICK_STATE.json`` to record a paused cherry-pick.

    Args:
        root:           Repository root.
        cherry_commit:  Commit ID being cherry-picked.
        head_commit:    Commit ID of HEAD at cherry-pick time.
        conflict_paths: Paths with unresolved conflicts.
    """
    state_path = root / ".muse" / _CHERRY_PICK_STATE_FILENAME
    data: dict[str, object] = {
        "cherry_commit": cherry_commit,
        "head_commit": head_commit,
        "conflict_paths": sorted(conflict_paths),
    }
    state_path.write_text(json.dumps(data, indent=2))
    logger.info(
        "✅ Wrote CHERRY_PICK_STATE.json with %d conflict(s)", len(conflict_paths)
    )


def clear_cherry_pick_state(root: pathlib.Path) -> None:
    """Remove ``.muse/CHERRY_PICK_STATE.json`` after a successful or aborted cherry-pick."""
    state_path = root / ".muse" / _CHERRY_PICK_STATE_FILENAME
    if state_path.exists():
        state_path.unlink()
        logger.debug("✅ Cleared CHERRY_PICK_STATE.json")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def compute_cherry_manifest(
    *,
    base_manifest: dict[str, str],
    head_manifest: dict[str, str],
    cherry_manifest: dict[str, str],
    cherry_diff: set[str],
    head_diff: set[str],
) -> tuple[dict[str, str], set[str]]:
    """Apply the cherry-pick delta onto the HEAD manifest.

    For each path in ``cherry_diff``:
    - If also in ``head_diff`` AND both sides have different values → conflict.
    - Otherwise take the cherry version (or remove the path if deleted by cherry).

    Paths not in ``cherry_diff`` remain at their HEAD values.

    Args:
        base_manifest:   Manifest of the cherry commit's parent (P).
        head_manifest:   Manifest of HEAD (ours).
        cherry_manifest: Manifest of the cherry commit (C).
        cherry_diff:     Paths changed by C relative to P.
        head_diff:       Paths changed by HEAD relative to P.

    Returns:
        Tuple of (result_manifest, conflict_paths) where ``conflict_paths``
        is empty for a clean cherry-pick.
    """
    result = dict(head_manifest)
    conflicts: set[str] = set()

    for path in cherry_diff:
        cherry_oid = cherry_manifest.get(path)
        head_oid = head_manifest.get(path)
        base_oid = base_manifest.get(path)

        if path in head_diff:
            # Both sides changed this path since the base
            if cherry_oid == head_oid:
                # Same outcome on both sides — not a real conflict
                pass
            else:
                conflicts.add(path)
                continue  # leave HEAD's version in result for now

        # Apply the cherry change: add/modify or delete
        if cherry_oid is not None:
            result[path] = cherry_oid
        else:
            # Cherry deleted this path
            result.pop(path, None)

    return result, conflicts


# ---------------------------------------------------------------------------
# Async core
# ---------------------------------------------------------------------------


async def _cherry_pick_async(
    *,
    commit_ref: str,
    root: pathlib.Path,
    session: AsyncSession,
    no_commit: bool = False,
) -> CherryPickResult:
    """Core cherry-pick pipeline — resolve, validate, apply, and commit.

    Called by the CLI callback and by tests.  All filesystem and DB
    side-effects are isolated here so tests can inject an in-memory SQLite
    session and a ``tmp_path`` root.

    Args:
        commit_ref: Commit ID (full or abbreviated) to cherry-pick.
        root:       Repo root (must contain ``.muse/``).
        session:    Async DB session (caller owns commit/rollback lifecycle).
        no_commit:  When ``True``, stage changes to muse-work/ but do not
                    create a new commit record.

    Returns:
        :class:`CherryPickResult` describing what happened.

    Raises:
        ``typer.Exit`` with an appropriate exit code on user-facing errors.
    """
    import typer

    from maestro.muse_cli.errors import ExitCode

    muse_dir = root / ".muse"

    # ── Guard: block if merge is in progress ─────────────────────────────
    merge_state = read_merge_state(root)
    if merge_state is not None and merge_state.conflict_paths:
        typer.echo(
            "❌ Cherry-pick blocked: unresolved merge conflicts in progress.\n"
            "   Resolve all conflicts, then run 'muse commit' before cherry-picking."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Guard: block if cherry-pick already in progress ──────────────────
    existing_state = read_cherry_pick_state(root)
    if existing_state is not None:
        typer.echo(
            "❌ Cherry-pick already in progress.\n"
            "   Resolve conflicts and run 'muse cherry-pick --continue', or\n"
            "   run 'muse cherry-pick --abort' to cancel."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Repo identity ────────────────────────────────────────────────────
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    head_ref = (muse_dir / "HEAD").read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1]

    # ── Resolve HEAD from the branch ref file (not DB ordering) ──────────
    # Reading the ref file directly is the authoritative source for HEAD,
    # because the DB committed_at ordering does not reflect manual resets.
    branch_ref_path = muse_dir / pathlib.Path(head_ref)
    head_commit_id = (
        branch_ref_path.read_text().strip() if branch_ref_path.exists() else ""
    )
    if not head_commit_id:
        typer.echo("❌ Current branch has no commits. Cannot cherry-pick onto an empty branch.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    head_commit = await session.get(MuseCliCommit, head_commit_id)
    if head_commit is None:
        typer.echo(f"❌ HEAD commit {head_commit_id[:8]} not found in DB.")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    # ── Resolve cherry commit ────────────────────────────────────────────
    cherry_commit = await resolve_commit_ref(session, repo_id, branch, commit_ref)
    if cherry_commit is None:
        # resolve_commit_ref only searches the current branch; try by prefix across all
        from sqlalchemy.future import select

        stmt = select(MuseCliCommit).where(
            MuseCliCommit.repo_id == repo_id,
            MuseCliCommit.commit_id.startswith(commit_ref),
        )
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            typer.echo(f"❌ Commit not found: {commit_ref!r}")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        if len(rows) > 1:
            typer.echo(
                f"❌ Ambiguous commit ref {commit_ref!r} — matches {len(rows)} commits. "
                "Use a longer prefix."
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)
        cherry_commit = rows[0]

    cherry_commit_id = cherry_commit.commit_id

    # ── Guard: cherry-pick of HEAD itself is a noop ───────────────────────
    if cherry_commit_id == head_commit_id:
        typer.echo(
            f"⚠️  Commit {cherry_commit_id[:8]} is already HEAD — nothing to cherry-pick."
        )
        raise typer.Exit(code=ExitCode.SUCCESS)

    # ── Load manifests ───────────────────────────────────────────────────
    # base = cherry commit's parent (P)
    base_manifest: dict[str, str] = {}
    if cherry_commit.parent_commit_id:
        loaded = await get_commit_snapshot_manifest(session, cherry_commit.parent_commit_id)
        base_manifest = loaded or {}

    cherry_manifest = await get_commit_snapshot_manifest(session, cherry_commit_id) or {}

    head_manifest: dict[str, str] = {}
    head_snap_row = await session.get(MuseCliSnapshot, head_commit.snapshot_id)
    if head_snap_row is not None:
        head_manifest = dict(head_snap_row.manifest)

    # ── Compute diffs ────────────────────────────────────────────────────
    cherry_diff = diff_snapshots(base_manifest, cherry_manifest)
    head_diff = diff_snapshots(base_manifest, head_manifest)

    # ── Apply cherry delta ───────────────────────────────────────────────
    result_manifest, conflict_paths = compute_cherry_manifest(
        base_manifest=base_manifest,
        head_manifest=head_manifest,
        cherry_manifest=cherry_manifest,
        cherry_diff=cherry_diff,
        head_diff=head_diff,
    )

    result_snapshot_id = compute_snapshot_id(result_manifest)

    # ── Conflict path ─────────────────────────────────────────────────────
    if conflict_paths:
        write_cherry_pick_state(
            root,
            cherry_commit=cherry_commit_id,
            head_commit=head_commit_id,
            conflict_paths=sorted(conflict_paths),
        )
        typer.echo(f"❌ Cherry-pick conflict in {len(conflict_paths)} file(s):")
        for path in sorted(conflict_paths):
            typer.echo(f"\tboth modified:   {path}")
        typer.echo(
            "Fix conflicts and run 'muse cherry-pick --continue' to create the commit."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # ── Auto-generate commit message ─────────────────────────────────────
    short_id = cherry_commit_id[:8]
    cherry_message = (
        f"{cherry_commit.message}\n\n(cherry picked from commit {short_id})"
    )

    # ── --no-commit: return without persisting ────────────────────────────
    if no_commit:
        typer.echo(
            f"✅ Cherry-pick applied (--no-commit). "
            f"Changes from {short_id} staged in muse-work/."
        )
        return CherryPickResult(
            commit_id="",
            cherry_commit_id=cherry_commit_id,
            head_commit_id=head_commit_id,
            new_snapshot_id=result_snapshot_id,
            message=cherry_message,
            no_commit=True,
            conflict=False,
            conflict_paths=(),
            branch=branch,
        )

    # ── Persist snapshot ─────────────────────────────────────────────────
    await upsert_snapshot(session, manifest=result_manifest, snapshot_id=result_snapshot_id)
    await session.flush()

    # ── Persist commit ───────────────────────────────────────────────────
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    new_commit_id = compute_commit_id(
        parent_ids=[head_commit_id],
        snapshot_id=result_snapshot_id,
        message=cherry_message,
        committed_at_iso=committed_at.isoformat(),
    )

    new_commit = MuseCliCommit(
        commit_id=new_commit_id,
        repo_id=repo_id,
        branch=branch,
        parent_commit_id=head_commit_id,
        snapshot_id=result_snapshot_id,
        message=cherry_message,
        author="",
        committed_at=committed_at,
    )
    await insert_commit(session, new_commit)

    # ── Update branch HEAD pointer ────────────────────────────────────────
    ref_path = muse_dir / pathlib.Path(head_ref)
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(new_commit_id)

    typer.echo(
        f"✅ [{branch} {new_commit_id[:8]}] {cherry_commit.message}\n"
        f"   (cherry picked from commit {short_id})"
    )
    logger.info(
        "✅ muse cherry-pick %s → %s on %r",
        short_id,
        new_commit_id[:8],
        branch,
    )

    return CherryPickResult(
        commit_id=new_commit_id,
        cherry_commit_id=cherry_commit_id,
        head_commit_id=head_commit_id,
        new_snapshot_id=result_snapshot_id,
        message=cherry_message,
        no_commit=False,
        conflict=False,
        conflict_paths=(),
        branch=branch,
    )


async def _cherry_pick_continue_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
) -> CherryPickResult:
    """Finalize a cherry-pick that was paused due to conflicts.

    Reads ``CHERRY_PICK_STATE.json``, verifies all conflicts are cleared,
    builds a snapshot from the current ``muse-work/`` contents, inserts a
    commit, advances the branch pointer, and clears the state file.

    Args:
        root:    Repository root.
        session: Open async DB session.

    Raises:
        :class:`typer.Exit`: If no cherry-pick is in progress, unresolved
            conflicts remain, or ``muse-work/`` is empty.
    """
    import typer

    from maestro.muse_cli.errors import ExitCode
    from maestro.muse_cli.snapshot import build_snapshot_manifest

    state = read_cherry_pick_state(root)
    if state is None:
        typer.echo("❌ No cherry-pick in progress. Nothing to continue.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if state.conflict_paths:
        typer.echo(
            f"❌ {len(state.conflict_paths)} conflict(s) not yet resolved:\n"
            + "\n".join(f"\tboth modified:   {p}" for p in state.conflict_paths)
            + "\nRun 'muse resolve <path> --ours/--theirs' for each file."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    muse_dir = root / ".muse"
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    head_ref = (muse_dir / "HEAD").read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1]
    our_ref_path = muse_dir / pathlib.Path(head_ref)

    head_commit_id = state.head_commit
    cherry_commit_id = state.cherry_commit

    # Load cherry commit message for attribution
    cherry_commit_row = await session.get(MuseCliCommit, cherry_commit_id)
    if cherry_commit_row is None:
        typer.echo(f"❌ Cherry commit {cherry_commit_id[:8]} not found in DB.")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    # Build snapshot from current muse-work/ (conflicts already resolved)
    workdir = root / "muse-work"
    if not workdir.exists():
        typer.echo("⚠️  muse-work/ is missing. Cannot create cherry-pick snapshot.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest = build_snapshot_manifest(workdir)
    if not manifest:
        typer.echo("⚠️  muse-work/ is empty. Nothing to commit for the cherry-pick.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    snapshot_id = compute_snapshot_id(manifest)
    await upsert_snapshot(session, manifest=manifest, snapshot_id=snapshot_id)
    await session.flush()

    short_id = cherry_commit_id[:8]
    cherry_message = (
        f"{cherry_commit_row.message}\n\n(cherry picked from commit {short_id})"
    )

    committed_at = datetime.datetime.now(datetime.timezone.utc)
    new_commit_id = compute_commit_id(
        parent_ids=[head_commit_id],
        snapshot_id=snapshot_id,
        message=cherry_message,
        committed_at_iso=committed_at.isoformat(),
    )

    new_commit = MuseCliCommit(
        commit_id=new_commit_id,
        repo_id=repo_id,
        branch=branch,
        parent_commit_id=head_commit_id,
        snapshot_id=snapshot_id,
        message=cherry_message,
        author="",
        committed_at=committed_at,
    )
    await insert_commit(session, new_commit)

    our_ref_path.write_text(new_commit_id)
    clear_cherry_pick_state(root)

    typer.echo(
        f"✅ [{branch} {new_commit_id[:8]}] {cherry_commit_row.message}\n"
        f"   (cherry picked from commit {short_id})"
    )
    logger.info(
        "✅ muse cherry-pick --continue: commit %s on %r (cherry: %s)",
        new_commit_id[:8],
        branch,
        short_id,
    )

    return CherryPickResult(
        commit_id=new_commit_id,
        cherry_commit_id=cherry_commit_id,
        head_commit_id=head_commit_id,
        new_snapshot_id=snapshot_id,
        message=cherry_message,
        no_commit=False,
        conflict=False,
        conflict_paths=(),
        branch=branch,
    )


async def _cherry_pick_abort_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
) -> None:
    """Abort an in-progress cherry-pick and restore pre-cherry-pick HEAD.

    Reads ``CHERRY_PICK_STATE.json`` to recover the original HEAD commit,
    resets the branch pointer, and removes the state file.

    Args:
        root:    Repository root.
        session: Open async DB session (unused but required for interface consistency).

    Raises:
        :class:`typer.Exit`: If no cherry-pick is in progress.
    """
    import typer

    from maestro.muse_cli.errors import ExitCode

    state = read_cherry_pick_state(root)
    if state is None:
        typer.echo("❌ No cherry-pick in progress. Nothing to abort.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    muse_dir = root / ".muse"
    head_ref = (muse_dir / "HEAD").read_text().strip()
    ref_path = muse_dir / pathlib.Path(head_ref)

    # Restore the branch pointer to HEAD at cherry-pick initiation time
    ref_path.write_text(state.head_commit)
    clear_cherry_pick_state(root)

    typer.echo(
        f"✅ Cherry-pick aborted. HEAD restored to {state.head_commit[:8]}."
    )
    logger.info(
        "✅ muse cherry-pick --abort: restored HEAD to %s",
        state.head_commit[:8],
    )
