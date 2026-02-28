"""Local content-addressed object store for the Muse CLI.

Objects are stored as flat files under ``<repo_root>/.muse/objects/<object_id>``.
The ``object_id`` is the sha256 hex digest of the file's raw bytes (same value
produced by :func:`maestro.muse_cli.snapshot.hash_file`).

Design: flat layout (no sub-directory sharding) for MVP simplicity.
The store is append-only: writing the same object twice is a no-op.

This module is the single source of truth for all local object I/O.
``muse commit`` writes into the store; ``muse read-tree`` reads from it.
Both commands go through these helpers to avoid duplicating path logic.
"""
from __future__ import annotations

import logging
import pathlib

logger = logging.getLogger(__name__)

_OBJECTS_DIR = "objects"


def objects_dir(repo_root: pathlib.Path) -> pathlib.Path:
    """Return the path to the local object store directory.

    The store lives at ``<repo_root>/.muse/objects/``.  It is created lazily
    by :func:`write_object` the first time an object is stored.

    Args:
        repo_root: Root of the Muse repository (the directory containing
                   ``.muse/``).

    Returns:
        Absolute path to the objects directory (may not yet exist).
    """
    return repo_root / ".muse" / _OBJECTS_DIR


def object_path(repo_root: pathlib.Path, object_id: str) -> pathlib.Path:
    """Return the canonical on-disk path for a single object.

    Args:
        repo_root: Root of the Muse repository.
        object_id: sha256 hex digest of the object's content (64 chars).

    Returns:
        Absolute path to the object file (may not yet exist).
    """
    return objects_dir(repo_root) / object_id


def write_object(repo_root: pathlib.Path, object_id: str, content: bytes) -> bool:
    """Write *content* to the local object store under *object_id*.

    If the object already exists (same ID = same content, content-addressed)
    the write is skipped and ``False`` is returned.  Returns ``True`` when a
    new object was written.

    The objects directory is created on first write.  Subsequent writes for
    the same ``object_id`` are no-ops — they never overwrite existing content.

    Args:
        repo_root: Root of the Muse repository.
        object_id: sha256 hex digest that identifies this object.
        content:   Raw bytes to persist.

    Returns:
        ``True`` if the object was newly written, ``False`` if it already
        existed (idempotent).
    """
    store = objects_dir(repo_root)
    store.mkdir(parents=True, exist_ok=True)
    dest = store / object_id
    if dest.exists():
        logger.debug("⚠️ Object %s already in store — skipped", object_id[:8])
        return False
    dest.write_bytes(content)
    logger.debug("✅ Stored object %s (%d bytes)", object_id[:8], len(content))
    return True


def read_object(repo_root: pathlib.Path, object_id: str) -> bytes | None:
    """Read and return the raw bytes for *object_id* from the local store.

    Returns ``None`` when the object is not present in the store so callers
    can produce a user-facing error rather than raising ``FileNotFoundError``.

    Args:
        repo_root: Root of the Muse repository.
        object_id: sha256 hex digest of the desired object.

    Returns:
        Raw bytes, or ``None`` when the object is absent from the store.
    """
    dest = object_path(repo_root, object_id)
    if not dest.exists():
        logger.debug("⚠️ Object %s not found in local store", object_id[:8])
        return None
    return dest.read_bytes()


def has_object(repo_root: pathlib.Path, object_id: str) -> bool:
    """Return ``True`` if *object_id* is present in the local store.

    Cheaper than :func:`read_object` when the caller only needs to check
    existence (e.g. to decide whether a commit needs to re-store an object).

    Args:
        repo_root: Root of the Muse repository.
        object_id: sha256 hex digest to check.
    """
    return object_path(repo_root, object_id).exists()
