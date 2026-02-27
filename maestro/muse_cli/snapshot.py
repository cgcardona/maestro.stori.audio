"""Pure filesystem snapshot logic for ``muse commit``.

All functions here are side-effect-free (no DB, no I/O besides reading
files under ``workdir``).  They are kept separate so they can be
unit-tested without a database.

ID derivation contract (deterministic, no random/UUID components):

    object_id   = sha256(file_bytes).hexdigest()
    snapshot_id = sha256("|".join(sorted(f"{path}:{oid}" for path, oid in manifest.items()))).hexdigest()
    commit_id   = sha256(
                    "|".join(sorted(parent_ids))
                    + "|" + snapshot_id
                    + "|" + message
                    + "|" + committed_at_iso
                  ).hexdigest()
"""
from __future__ import annotations

import hashlib
import pathlib


def hash_file(path: pathlib.Path) -> str:
    """Return the sha256 hex digest of a file's raw bytes.

    This is the ``object_id`` for the given file.  Reading in chunks
    keeps memory usage constant regardless of file size.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_snapshot_manifest(workdir: pathlib.Path) -> dict[str, str]:
    """Alias for ``walk_workdir`` — preferred name in public API."""
    return walk_workdir(workdir)


def walk_workdir(workdir: pathlib.Path) -> dict[str, str]:
    """Walk *workdir* recursively and return ``{rel_path: object_id}``.

    Only regular files are included (symlinks and directories are skipped).
    Paths use POSIX separators regardless of host OS for cross-platform
    reproducibility.  Hidden files (starting with ``.``) are excluded.
    """
    manifest: dict[str, str] = {}
    for file_path in sorted(workdir.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.name.startswith("."):
            continue
        rel = file_path.relative_to(workdir).as_posix()
        manifest[rel] = hash_file(file_path)
    return manifest


def compute_snapshot_id(manifest: dict[str, str]) -> str:
    """Return sha256 of the sorted ``path:object_id`` pairs.

    Sorting ensures two identical working trees always produce the same
    snapshot_id, regardless of filesystem traversal order.
    """
    parts = sorted(f"{path}:{oid}" for path, oid in manifest.items())
    payload = "|".join(parts).encode()
    return hashlib.sha256(payload).hexdigest()


def compute_commit_id(
    parent_ids: list[str],
    snapshot_id: str,
    message: str,
    committed_at_iso: str,
) -> str:
    """Return sha256 of the commit's canonical inputs.

    Given the same arguments on two machines the result is identical.
    ``parent_ids`` is sorted before hashing so insertion order does not
    affect determinism.
    """
    parts = [
        "|".join(sorted(parent_ids)),
        snapshot_id,
        message,
        committed_at_iso,
    ]
    payload = "|".join(parts).encode()
    return hashlib.sha256(payload).hexdigest()
