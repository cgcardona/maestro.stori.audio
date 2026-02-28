"""Canonical content-addressed object store for the Muse VCS.

All Muse commands that read or write blobs — ``muse commit``, ``muse read-tree``,
``muse reset`` — go through this module exclusively.  No command may implement
its own path logic or copy its own blobs.

Layout
------
Objects are stored under ``<repo_root>/.muse/objects/`` using a two-character
sharded directory layout that mirrors Git's loose-object format::

    .muse/objects/<sha2>/<sha62>

where ``<sha2>`` is the first two hex characters of the SHA-256 digest and
``<sha62>`` is the remaining 62 characters.  For example, the object with
digest ``ab1234...`` is stored at ``.muse/objects/ab/1234...``.

Why sharding?
-------------
Music repositories accumulate objects at a far higher rate than code
repositories: every generated take, every variation, every rendered clip is a
new blob.  A single recording session can produce tens of thousands of objects.
Without sharding, a flat directory exceeds filesystem limits (ext4, APFS, HFS+
all degrade or hard-limit above ~32,000 entries per directory).  Two hex
characters yield 256 subdirectories — the same trade-off Git settled on after
years of production use.

This module is the single source of truth for all local object I/O.
The store is append-only: writing the same object twice is always a no-op.
"""
from __future__ import annotations

import logging
import pathlib
import shutil

logger = logging.getLogger(__name__)

_OBJECTS_DIR = "objects"


def objects_dir(repo_root: pathlib.Path) -> pathlib.Path:
    """Return the path to the local object store root directory.

    The store lives at ``<repo_root>/.muse/objects/``.  Shard subdirectories
    are created lazily by :func:`write_object` and :func:`write_object_from_path`.

    Args:
        repo_root: Root of the Muse repository (the directory containing
                   ``.muse/``).

    Returns:
        Absolute path to the objects directory (may not yet exist).
    """
    return repo_root / ".muse" / _OBJECTS_DIR


def object_path(repo_root: pathlib.Path, object_id: str) -> pathlib.Path:
    """Return the canonical on-disk path for a single object.

    Objects are sharded by the first two hex characters of their SHA-256
    digest, matching Git's loose-object layout::

        .muse/objects/<sha2>/<sha62>

    This prevents filesystem performance issues as the repository grows.

    Args:
        repo_root: Root of the Muse repository.
        object_id: SHA-256 hex digest of the object's content (64 chars).

    Returns:
        Absolute path to the object file (may not yet exist).
    """
    return objects_dir(repo_root) / object_id[:2] / object_id[2:]


def has_object(repo_root: pathlib.Path, object_id: str) -> bool:
    """Return ``True`` if *object_id* is present in the local store.

    Cheaper than :func:`read_object` when the caller only needs to check
    existence (e.g. to pre-flight a hard reset before touching the working
    tree).

    Args:
        repo_root: Root of the Muse repository.
        object_id: SHA-256 hex digest to check.
    """
    return object_path(repo_root, object_id).exists()


def write_object(repo_root: pathlib.Path, object_id: str, content: bytes) -> bool:
    """Write *content* to the local object store under *object_id*.

    If the object already exists (same ID = same content, content-addressed)
    the write is skipped and ``False`` is returned.  Returns ``True`` when a
    new object was written.

    The shard directory is created on first write.  Subsequent writes for the
    same ``object_id`` are no-ops — they never overwrite existing content.

    Args:
        repo_root: Root of the Muse repository.
        object_id: SHA-256 hex digest that identifies this object (64 chars).
        content:   Raw bytes to persist.

    Returns:
        ``True`` if the object was newly written, ``False`` if it already
        existed (idempotent).
    """
    dest = object_path(repo_root, object_id)
    if dest.exists():
        logger.debug("⚠️ Object %s already in store — skipped", object_id[:8])
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    logger.debug("✅ Stored object %s (%d bytes)", object_id[:8], len(content))
    return True


def write_object_from_path(
    repo_root: pathlib.Path,
    object_id: str,
    src: pathlib.Path,
) -> bool:
    """Copy *src* into the object store without loading it into memory.

    Preferred over :func:`write_object` for large blobs (dense MIDI renders,
    audio previews) because ``shutil.copy2`` delegates to the OS copy
    mechanism, keeping the interpreter heap clean.

    Idempotent: if the object already exists it is never overwritten.

    Args:
        repo_root: Root of the Muse repository.
        object_id: SHA-256 hex digest of *src*'s content (64 chars).
        src:       Absolute path of the source file to store.

    Returns:
        ``True`` if the object was newly written, ``False`` if it already
        existed (idempotent).
    """
    dest = object_path(repo_root, object_id)
    if dest.exists():
        logger.debug("⚠️ Object %s already in store — skipped", object_id[:8])
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    logger.debug("✅ Stored object %s (%s)", object_id[:8], src.name)
    return True


def read_object(repo_root: pathlib.Path, object_id: str) -> bytes | None:
    """Read and return the raw bytes for *object_id* from the local store.

    Returns ``None`` when the object is not present in the store so callers
    can produce a user-facing error rather than raising ``FileNotFoundError``.

    Args:
        repo_root: Root of the Muse repository.
        object_id: SHA-256 hex digest of the desired object.

    Returns:
        Raw bytes, or ``None`` when the object is absent from the store.
    """
    dest = object_path(repo_root, object_id)
    if not dest.exists():
        logger.debug("⚠️ Object %s not found in local store", object_id[:8])
        return None
    return dest.read_bytes()


def restore_object(
    repo_root: pathlib.Path,
    object_id: str,
    dest: pathlib.Path,
) -> bool:
    """Copy an object from the store to *dest* without loading it into memory.

    Preferred over :func:`read_object` + ``dest.write_bytes()`` for large
    blobs because ``shutil.copy2`` delegates to the OS copy mechanism.

    Creates parent directories of *dest* if they do not exist.

    Args:
        repo_root: Root of the Muse repository.
        object_id: SHA-256 hex digest of the desired object (64 chars).
        dest:      Absolute path to write the restored file.

    Returns:
        ``True`` on success, ``False`` if the object is not in the store.
    """
    src = object_path(repo_root, object_id)
    if not src.exists():
        logger.debug(
            "⚠️ Object %s not found in local store — cannot restore", object_id[:8]
        )
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    logger.debug("✅ Restored object %s → %s", object_id[:8], dest)
    return True
