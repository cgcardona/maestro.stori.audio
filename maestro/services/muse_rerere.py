"""Muse Rerere — Reuse Recorded Resolutions for musical merge conflicts.

In parallel multi-branch Muse workflows identical merge conflicts appear
repeatedly (e.g. the same MIDI region modified in the same way on two
independent branches).  rerere records conflict shapes and their resolutions
so they can be applied automatically on subsequent merges.

Cache layout::

    .muse/rr-cache/<hash>/
        conflict    — serialised conflict fingerprint (JSON)
        postimage   — serialised resolution (JSON, written only after resolve)

The conflict fingerprint is a normalised, transposition-independent hash
of the conflict shape.  Two conflicts with the same structural shape but
different absolute pitches are treated as the same conflict so that a
resolution recorded in one key can be applied in another.

Boundary rules:
  - Must NOT import StateStore, executor, MCP tools, routes, or handlers.
  - May import muse_merge types.
  - All file I/O uses pathlib.Path — never open() with bare strings.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RR_CACHE_DIR = ".muse/rr-cache"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _rr_cache_root(repo_root: Path) -> Path:
    """Return (and create if needed) the rr-cache directory."""
    cache = repo_root / _RR_CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _conflict_fingerprint(conflicts: list[dict[str, Any]]) -> str:
    """Compute a normalised, transposition-independent fingerprint for *conflicts*.

    Normalisation steps:
    1.  Sort conflicts by (region_id, type, description) so that order does
        not affect the fingerprint.
    2.  Strip absolute pitch values from descriptions and replace them with
        relative pitch offsets from the lowest pitch in the conflict set.
        This makes the fingerprint invariant to transposition.
    3.  SHA-256 the resulting JSON.
    """
    # Collect all numeric pitch values mentioned in descriptions for normalisation.
    import re

    pitch_re = re.compile(r"pitch=(\d+)")

    all_pitches: list[int] = []
    for c in conflicts:
        for m in pitch_re.finditer(c.get("description", "")):
            all_pitches.append(int(m.group(1)))

    min_pitch = min(all_pitches) if all_pitches else 0

    def _normalise(c: dict[str, Any]) -> dict[str, Any]:
        desc = c.get("description", "")
        normalised_desc = pitch_re.sub(
            lambda m: f"pitch={int(m.group(1)) - min_pitch}",
            desc,
        )
        return {
            "region_id": c.get("region_id", ""),
            "type": c.get("type", ""),
            "description": normalised_desc,
        }

    normalised = sorted(
        [_normalise(c) for c in conflicts],
        key=lambda x: (x["region_id"], x["type"], x["description"]),
    )
    blob = json.dumps(normalised, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def _hash_dir(repo_root: Path, conflict_hash: str) -> Path:
    """Return the cache sub-directory for *conflict_hash*."""
    return _rr_cache_root(repo_root) / conflict_hash


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_conflict(repo_root: Path, conflicts: list[dict[str, Any]]) -> str:
    """Record a conflict shape in the rr-cache.

    If the same conflict shape is already cached this is a no-op (idempotent).

    Args:
        repo_root:  Repository root (directory containing ``.muse/``).
        conflicts:  List of conflict dicts (keys: region_id, type, description).
                    These are typically derived from :class:`MergeConflict` instances.

    Returns:
        The SHA-256 fingerprint hash identifying this conflict shape.
    """
    h = _conflict_fingerprint(conflicts)
    slot = _hash_dir(repo_root, h)
    slot.mkdir(parents=True, exist_ok=True)

    conflict_file = slot / "conflict"
    if not conflict_file.exists():
        conflict_file.write_text(
            json.dumps(conflicts, indent=2),
            encoding="utf-8",
        )
        logger.info("✅ muse rerere: recorded conflict %s", h[:12])
    else:
        logger.debug("muse rerere: conflict %s already cached", h[:12])

    return h


def record_resolution(
    repo_root: Path,
    conflict_hash: str,
    resolution: dict[str, Any],
) -> None:
    """Persist a resolution for an existing conflict fingerprint.

    Args:
        repo_root:      Repository root.
        conflict_hash:  Hash returned by :func:`record_conflict`.
        resolution:     Arbitrary resolution data (e.g. merged snapshot or
                        per-file resolution strategies).  Must be JSON-serialisable.

    Raises:
        FileNotFoundError: If *conflict_hash* is not in the cache (i.e.
                           :func:`record_conflict` was never called for it).
    """
    slot = _hash_dir(repo_root, conflict_hash)
    if not slot.is_dir():
        raise FileNotFoundError(
            f"rerere: conflict hash {conflict_hash!r} not found in rr-cache"
        )
    postimage = slot / "postimage"
    postimage.write_text(
        json.dumps(resolution, indent=2),
        encoding="utf-8",
    )
    logger.info("✅ muse rerere: recorded resolution for %s", conflict_hash[:12])


def apply_rerere(
    repo_root: Path,
    conflicts: list[dict[str, Any]],
) -> tuple[int, dict[str, Any] | None]:
    """Attempt to auto-apply a cached resolution for *conflicts*.

    Args:
        repo_root:  Repository root.
        conflicts:  Current merge conflicts (same format as :func:`record_conflict`).

    Returns:
        A tuple ``(applied, resolution)`` where *applied* is the number of
        conflicts resolved (0 or len(conflicts)) and *resolution* is the
        cached resolution dict (or ``None`` when no cache hit exists).
    """
    if not conflicts:
        return 0, None

    h = _conflict_fingerprint(conflicts)
    postimage = _hash_dir(repo_root, h) / "postimage"
    if not postimage.exists():
        logger.debug("muse rerere: no cached resolution for %s", h[:12])
        return 0, None

    resolution: dict[str, Any] = json.loads(postimage.read_text(encoding="utf-8"))
    applied = len(conflicts)
    logger.info(
        "✅ muse rerere: resolved %d conflict(s) using rerere (hash %s)",
        applied,
        h[:12],
    )
    return applied, resolution


def list_rerere(repo_root: Path) -> list[str]:
    """Return all conflict fingerprint hashes currently in the rr-cache.

    Only hashes that have an associated ``conflict`` file are returned.
    Incomplete entries (e.g. conflict recorded but not yet resolved) are
    included — they are distinct from resolved entries which also have a
    ``postimage`` file.

    Args:
        repo_root:  Repository root.

    Returns:
        Sorted list of SHA-256 hex-digest strings.
    """
    cache = _rr_cache_root(repo_root)
    hashes: list[str] = []
    for entry in sorted(cache.iterdir()):
        if entry.is_dir() and (entry / "conflict").exists():
            hashes.append(entry.name)
    return hashes


def forget_rerere(repo_root: Path, conflict_hash: str) -> bool:
    """Remove a single cached conflict/resolution from the rr-cache.

    Args:
        repo_root:      Repository root.
        conflict_hash:  Hash to remove.

    Returns:
        ``True`` if the entry existed and was removed, ``False`` if it
        was not found (idempotent — callers need not handle this as an error).
    """
    slot = _hash_dir(repo_root, conflict_hash)
    if not slot.is_dir():
        logger.warning("⚠️ muse rerere forget: hash %r not found", conflict_hash[:12])
        return False

    for child in slot.iterdir():
        child.unlink()
    slot.rmdir()
    logger.info("✅ muse rerere: forgot %s", conflict_hash[:12])
    return True


def clear_rerere(repo_root: Path) -> int:
    """Remove ALL entries from the rr-cache.

    Args:
        repo_root:  Repository root.

    Returns:
        Number of entries removed.
    """
    cache = _rr_cache_root(repo_root)
    removed = 0
    for entry in list(cache.iterdir()):
        if entry.is_dir():
            for child in entry.iterdir():
                child.unlink()
            entry.rmdir()
            removed += 1
    logger.info("✅ muse rerere: cleared %d cache entr%s", removed, "y" if removed == 1 else "ies")
    return removed
