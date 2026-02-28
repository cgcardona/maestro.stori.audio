"""Muse Stash Service — temporarily shelve uncommitted muse-work/ changes.

Stash lets a producer save in-progress work without committing, switch
context (e.g. fix the intro for a client call), then restore the shelved
state with ``muse stash pop``.

Design
------
- Stash entries live purely on the filesystem in ``.muse/stash/``.
- Each entry is a JSON file named ``stash-<epoch_ns>.json``.
- The stack is ordered by creation time: index 0 is most recent.
- File content is preserved by writing blobs to the existing
  ``.muse/objects/<oid[:2]>/<oid[2:]>`` content-addressed store
  (same layout used by ``muse commit`` and ``muse reset --hard``).
- Restoring HEAD after push reads manifests from the DB (same as hard reset).
  If the branch has no commits, muse-work/ is simply cleared.
- On apply/pop, files are copied from the object store back into muse-work/.
  Files whose objects are absent from the store are reported as missing.
- Track/section scoping on push limits which files are saved and what is
  restored afterward (only scoped paths are erased; others stay untouched).

Path-scoped stash (``--track`` / ``--section``)
------------------------------------------------
When a scope is supplied, only files under ``tracks/<track>/`` or
``sections/<section>/`` are saved to the stash.  After saving the scope,
the HEAD snapshot is restored only for those paths (other working-tree
files are left untouched).  Applying a scoped stash similarly only writes
paths that match the original scope.

Boundary rules:
  - No Typer imports.
  - No StateStore, EntityRegistry, or get_or_create_store.
  - May import muse_cli.{db,models,object_store,snapshot}.
  - Filesystem stash store is independent of the Postgres schema.
"""
from __future__ import annotations

import datetime
import json
import logging
import pathlib
import shutil
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.snapshot import hash_file

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STASH_DIR = "stash"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StashEntry:
    """A single stash entry persisted in ``.muse/stash/``.

    Attributes:
        stash_id:   Unique filename stem (``stash-<epoch_ns>-<uuid8>``).
        index:      Position in the stack (0 = most recent).
        branch:     Branch name at the time of stash.
        message:    Human-readable label (``On <branch>: <text>``).
        created_at: ISO-8601 timestamp.
        manifest:   ``{rel_path: sha256_object_id}`` of stashed files.
        track:      Optional track scope used during push.
        section:    Optional section scope used during push.
    """

    stash_id: str
    index: int
    branch: str
    message: str
    created_at: str
    manifest: dict[str, str]
    track: Optional[str]
    section: Optional[str]


@dataclass(frozen=True)
class StashPushResult:
    """Outcome of ``muse stash push``.

    Attributes:
        stash_ref:      Human label (``stash@{0}``).
        message:        The label stored in the entry.
        branch:         Branch at the time of push.
        files_stashed:  Number of files saved into the stash.
        head_restored:  Whether HEAD snapshot was restored to muse-work/.
        missing_head:   Paths that could not be restored from the object store
                        (object bytes not present; stash push succeeded but
                        HEAD restoration is incomplete).
    """

    stash_ref: str
    message: str
    branch: str
    files_stashed: int
    head_restored: bool
    missing_head: tuple[str, ...]


@dataclass(frozen=True)
class StashApplyResult:
    """Outcome of ``muse stash apply`` or ``muse stash pop``.

    Attributes:
        stash_ref:     Human label of the entry that was applied.
        message:       The entry's label.
        files_applied: Number of files written to muse-work/.
        missing:       Paths whose object bytes were absent from the store.
        dropped:       True when the entry was removed (pop); False for apply.
    """

    stash_ref: str
    message: str
    files_applied: int
    missing: tuple[str, ...]
    dropped: bool


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def _stash_dir(root: pathlib.Path) -> pathlib.Path:
    """Return the stash directory path (does not create it)."""
    return root / ".muse" / _STASH_DIR


def _entry_path(root: pathlib.Path, stash_id: str) -> pathlib.Path:
    """Return the filesystem path of a single stash entry JSON file."""
    return _stash_dir(root) / f"{stash_id}.json"


def _object_path(root: pathlib.Path, object_id: str) -> pathlib.Path:
    """Return the sharded object store path for *object_id*.

    Matches the layout used by ``muse commit`` and ``muse reset --hard``:
    ``.muse/objects/<oid[:2]>/<oid[2:]>``.
    """
    return root / ".muse" / "objects" / object_id[:2] / object_id[2:]


def _read_entry(entry_file: pathlib.Path, index: int) -> StashEntry:
    """Deserialize a stash entry JSON file."""
    raw: dict[str, object] = json.loads(entry_file.read_text())
    raw_manifest = raw.get("manifest", {})
    manifest: dict[str, str] = (
        {str(k): str(v) for k, v in raw_manifest.items()}
        if isinstance(raw_manifest, dict)
        else {}
    )
    return StashEntry(
        stash_id=str(raw["stash_id"]),
        index=index,
        branch=str(raw["branch"]),
        message=str(raw["message"]),
        created_at=str(raw["created_at"]),
        manifest=manifest,
        track=str(raw["track"]) if raw.get("track") else None,
        section=str(raw["section"]) if raw.get("section") else None,
    )


def _write_entry(root: pathlib.Path, entry_data: dict[str, object]) -> str:
    """Serialize and write a stash entry.  Returns the stash_id."""
    stash_dir = _stash_dir(root)
    stash_dir.mkdir(parents=True, exist_ok=True)
    stash_id = str(entry_data["stash_id"])
    (_stash_dir(root) / f"{stash_id}.json").write_text(
        json.dumps(entry_data, indent=2)
    )
    return stash_id


def _sorted_entries(root: pathlib.Path) -> list[pathlib.Path]:
    """Return stash JSON files sorted newest-first (index 0 = most recent)."""
    stash_dir = _stash_dir(root)
    if not stash_dir.exists():
        return []
    files = sorted(stash_dir.glob("stash-*.json"), reverse=True)
    return files


# ---------------------------------------------------------------------------
# Path-scope filter (mirrors muse_revert._filter_paths)
# ---------------------------------------------------------------------------


def _filter_paths(
    manifest: dict[str, str],
    track: Optional[str],
    section: Optional[str],
) -> set[str]:
    """Return paths in *manifest* matching the scope (all paths when no scope)."""
    if not track and not section:
        return set(manifest.keys())

    matched: set[str] = set()
    for path in manifest:
        if track and path.startswith(f"tracks/{track}/"):
            matched.add(path)
        if section and path.startswith(f"sections/{section}/"):
            matched.add(path)
    return matched


# ---------------------------------------------------------------------------
# Object store writes
# ---------------------------------------------------------------------------


def _store_files(
    root: pathlib.Path,
    workdir: pathlib.Path,
    paths: set[str],
) -> dict[str, str]:
    """Copy *paths* from *workdir* into the object store.

    Returns a manifest ``{rel_path: object_id}`` for the stored files.
    Objects already in the store are not overwritten (content-addressed).
    """
    manifest: dict[str, str] = {}
    for rel_path in sorted(paths):
        abs_path = workdir / rel_path
        if not abs_path.exists():
            logger.warning("⚠️ Stash: skipping missing file %s", rel_path)
            continue
        oid = hash_file(abs_path)
        dest = _object_path(root, oid)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(abs_path, dest)
            logger.debug("✅ Stash stored object %s ← %s", oid[:8], rel_path)
        manifest[rel_path] = oid
    return manifest


# ---------------------------------------------------------------------------
# HEAD snapshot restoration helper
# ---------------------------------------------------------------------------


def _restore_from_manifest(
    root: pathlib.Path,
    workdir: pathlib.Path,
    target_manifest: dict[str, str],
    scope_paths: Optional[set[str]],
) -> tuple[int, list[str]]:
    """Write *target_manifest* files from the object store into *workdir*.

    When *scope_paths* is given, only paths in that set are touched; other
    working-tree files are left as-is.

    Returns:
        ``(files_written, missing_paths)`` where *missing_paths* lists files
        that could not be restored because their objects are absent.
    """
    workdir.mkdir(parents=True, exist_ok=True)

    if scope_paths is not None:
        paths_to_restore = {p: oid for p, oid in target_manifest.items() if p in scope_paths}
    else:
        paths_to_restore = dict(target_manifest)

    missing: list[str] = []
    written = 0

    for rel_path, oid in sorted(paths_to_restore.items()):
        obj_file = _object_path(root, oid)
        if not obj_file.exists():
            missing.append(rel_path)
            logger.warning(
                "⚠️ Stash: object %s missing from store for %s", oid[:8], rel_path
            )
            continue
        dest = workdir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(obj_file, dest)
        written += 1

    # When scope_paths is None (full restore), delete files not in the manifest.
    if scope_paths is None:
        target_abs = {workdir / p for p in target_manifest}
        for f in list(workdir.rglob("*")):
            if f.is_file() and not f.name.startswith(".") and f not in target_abs:
                f.unlink(missing_ok=True)

    return written, missing


# ---------------------------------------------------------------------------
# Public API — pure filesystem operations
# ---------------------------------------------------------------------------


def list_stash(root: pathlib.Path) -> list[StashEntry]:
    """Return all stash entries, newest first (index 0 = most recent).

    Returns an empty list when the stash is empty or ``.muse/stash/``
    does not exist.

    Args:
        root: Muse repository root.
    """
    files = _sorted_entries(root)
    return [_read_entry(f, i) for i, f in enumerate(files)]


def drop_stash(root: pathlib.Path, index: int) -> StashEntry:
    """Remove the stash entry at *index* from the stack.

    Args:
        root:  Muse repository root.
        index: 0-based stash index (0 = most recent).

    Returns:
        The dropped :class:`StashEntry`.

    Raises:
        IndexError: When *index* is out of range.
    """
    files = _sorted_entries(root)
    if index < 0 or index >= len(files):
        raise IndexError(
            f"stash@{{{index}}} does not exist (stack has {len(files)} entries)"
        )
    entry = _read_entry(files[index], index)
    files[index].unlink()
    logger.info("✅ muse stash drop stash@{%d}: %s", index, entry.message)
    return entry


def clear_stash(root: pathlib.Path) -> int:
    """Remove all stash entries.

    Args:
        root: Muse repository root.

    Returns:
        Number of entries removed.
    """
    files = _sorted_entries(root)
    for f in files:
        f.unlink()
    count = len(files)
    if count:
        logger.info("✅ muse stash clear: removed %d entries", count)
    return count


# ---------------------------------------------------------------------------
# Push — save working state and restore HEAD
# ---------------------------------------------------------------------------


def push_stash(
    root: pathlib.Path,
    *,
    message: Optional[str] = None,
    track: Optional[str] = None,
    section: Optional[str] = None,
    head_manifest: Optional[dict[str, str]] = None,
) -> StashPushResult:
    """Save muse-work/ changes to the stash and restore HEAD snapshot.

    Algorithm:
    1. Walk muse-work/ and identify paths to stash (all, or scoped by
       ``track``/``section``).
    2. Copy each file into the object store (sharded ``.muse/objects/``).
    3. Build a stash entry JSON and write it to ``.muse/stash/``.
    4. Restore HEAD snapshot to muse-work/:
       - Full push: restore full HEAD manifest (deletes extra files).
       - Scoped push: restore HEAD for scoped paths only; other files
         remain untouched.

    Args:
        root:          Muse repository root.
        message:       Optional label; defaults to ``On <branch>: stash``.
        track:         Optional track name scope (e.g. ``"drums"``).
        section:       Optional section name scope (e.g. ``"chorus"``).
        head_manifest: Snapshot manifest for the current HEAD commit, used
                       to restore muse-work/ after stashing.  When ``None``
                       the branch has no commits and muse-work/ is cleared
                       (full push) or left untouched (scoped push).

    Returns:
        :class:`StashPushResult` describing what was saved and restored.
    """
    muse_dir = root / ".muse"
    workdir = root / "muse-work"

    # ── Resolve current branch ───────────────────────────────────────────
    head_text = (muse_dir / "HEAD").read_text().strip()
    branch = head_text.rsplit("/", 1)[-1]

    # ── Build working-tree manifest ──────────────────────────────────────
    from maestro.muse_cli.snapshot import walk_workdir

    current_manifest: dict[str, str] = {}
    if workdir.exists():
        current_manifest = walk_workdir(workdir)

    # ── Identify paths to stash ──────────────────────────────────────────
    scope_paths = _filter_paths(current_manifest, track, section)
    if not scope_paths:
        # Nothing to stash (either empty workdir or scope matched nothing).
        return StashPushResult(
            stash_ref="",
            message="",
            branch=branch,
            files_stashed=0,
            head_restored=False,
            missing_head=(),
        )

    # ── Copy files into object store ─────────────────────────────────────
    stash_manifest = _store_files(root, workdir, scope_paths)

    # ── Build and write stash entry ──────────────────────────────────────
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    stash_id = f"stash-{created_at.replace(':', '').replace('+', 'Z').replace('.', '')}-{uuid.uuid4().hex[:8]}"
    label = message or f"On {branch}: stash"
    entry_data: dict[str, object] = {
        "stash_id": stash_id,
        "branch": branch,
        "message": label,
        "created_at": created_at,
        "track": track,
        "section": section,
        "manifest": stash_manifest,
    }
    _write_entry(root, entry_data)

    # ── Compute stash@{0} index (newly pushed is always newest) ──────────
    files = _sorted_entries(root)
    stash_ref = "stash@{0}"

    # ── Restore HEAD snapshot to muse-work/ ──────────────────────────────
    missing_head: list[str] = []
    head_restored = False

    if head_manifest is not None:
        # Determine scope for restore
        restore_scope: Optional[set[str]] = None
        if track or section:
            # Only restore paths that match the scope (others untouched)
            restore_scope = _filter_paths(head_manifest, track, section)
            # Also remove scoped paths that are NOT in head_manifest
            # (new files in workdir under the scope must be deleted)
            paths_to_clear = scope_paths - set(head_manifest.keys())
            for rel_path in paths_to_clear:
                abs_path = workdir / rel_path
                if abs_path.exists():
                    abs_path.unlink(missing_ok=True)
        else:
            restore_scope = None  # full restore

        _, missing_head = _restore_from_manifest(
            root, workdir, head_manifest, restore_scope
        )
        head_restored = True
    else:
        # No HEAD commit: full push clears muse-work/, scoped push does nothing.
        if not track and not section:
            for f in list(workdir.rglob("*")):
                if f.is_file() and not f.name.startswith("."):
                    f.unlink(missing_ok=True)

    logger.info(
        "✅ muse stash push: %s (%d files, branch=%r)",
        stash_ref,
        len(stash_manifest),
        branch,
    )

    return StashPushResult(
        stash_ref=stash_ref,
        message=label,
        branch=branch,
        files_stashed=len(stash_manifest),
        head_restored=head_restored,
        missing_head=tuple(sorted(missing_head)),
    )


# ---------------------------------------------------------------------------
# Apply / Pop — restore stash to working tree
# ---------------------------------------------------------------------------


def apply_stash(
    root: pathlib.Path,
    index: int = 0,
    *,
    drop: bool = False,
) -> StashApplyResult:
    """Restore a stash entry to muse-work/.

    Algorithm:
    1. Resolve *index* → ``StashEntry``.
    2. For each path in the entry's manifest, copy the object from the
       store back into muse-work/ (overwriting any conflicting file).
    3. If *drop* is True, remove the stash entry (this is ``pop`` semantics).

    Conflict strategy: last-write-wins.  Files in muse-work/ that are NOT
    in the stash manifest are left untouched; only stash paths are written.

    Args:
        root:  Muse repository root.
        index: 0-based stash index (0 = most recent).
        drop:  Remove the entry after applying (True → pop, False → apply).

    Returns:
        :class:`StashApplyResult` describing what was applied.

    Raises:
        IndexError: When *index* is out of range.
    """
    files = _sorted_entries(root)
    if index < 0 or index >= len(files):
        raise IndexError(
            f"stash@{{{index}}} does not exist (stack has {len(files)} entries)"
        )

    entry = _read_entry(files[index], index)
    stash_ref = f"stash@{{{index}}}"
    workdir = root / "muse-work"
    workdir.mkdir(parents=True, exist_ok=True)

    # ── Restore files from object store ──────────────────────────────────
    missing: list[str] = []
    written = 0

    for rel_path, oid in sorted(entry.manifest.items()):
        obj_file = _object_path(root, oid)
        if not obj_file.exists():
            missing.append(rel_path)
            logger.warning(
                "⚠️ Stash apply: object %s missing for %s", oid[:8], rel_path
            )
            continue
        dest = workdir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(obj_file, dest)
        written += 1
        logger.debug("✅ Stash apply: restored %s from object %s", rel_path, oid[:8])

    # ── Drop entry if pop semantics ───────────────────────────────────────
    if drop:
        files[index].unlink()
        logger.info("✅ muse stash pop: applied and dropped %s", stash_ref)
    else:
        logger.info("✅ muse stash apply: applied %s (kept)", stash_ref)

    return StashApplyResult(
        stash_ref=stash_ref,
        message=entry.message,
        files_applied=written,
        missing=tuple(sorted(missing)),
        dropped=drop,
    )
