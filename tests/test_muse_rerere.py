"""Tests for Muse Rerere — reuse recorded resolutions (issue #484).

Verifies:
- record_conflict stores the conflict fingerprint hash correctly.
- record_resolution stores the postimage.
- apply_rerere returns the applied count and resolution on cache hit.
- apply_rerere returns (0, None) on cache miss.
- list_rerere returns all cached hashes.
- forget_rerere removes a single hash.
- clear_rerere empties the entire cache.
- Fingerprint is transposition-invariant (same shape at different pitches → same hash).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from maestro.contracts.json_types import JSONObject
from maestro.services.muse_rerere import (
    ConflictDict,
    apply_rerere,
    clear_rerere,
    forget_rerere,
    list_rerere,
    record_conflict,
    record_resolution,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    """Return a temporary directory that looks like a Muse repo root."""
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    return tmp_path


def _make_conflicts(region_id: str = "region-1", pitch: int = 60) -> list[ConflictDict]:
    """Helper: build a minimal conflict list."""
    return [
        ConflictDict(
            region_id=region_id,
            type="note",
            description=f"Both sides modified note at pitch={pitch} beat=1.0",
        )
    ]


# ---------------------------------------------------------------------------
# record_conflict
# ---------------------------------------------------------------------------


def test_record_conflict_stores_hash_correctly(repo_root: Path) -> None:
    """record_conflict returns a consistent hex SHA-256 hash."""
    conflicts = _make_conflicts()
    h = record_conflict(repo_root, conflicts)

    assert isinstance(h, str)
    assert len(h) == 64  # SHA-256 hex digest
    assert (repo_root / ".muse" / "rr-cache" / h / "conflict").exists()


def test_record_conflict_is_idempotent(repo_root: Path) -> None:
    """Calling record_conflict twice with the same conflicts yields the same hash."""
    conflicts = _make_conflicts()
    h1 = record_conflict(repo_root, conflicts)
    h2 = record_conflict(repo_root, conflicts)
    assert h1 == h2


def test_record_conflict_transposition_invariant(repo_root: Path) -> None:
    """Conflicts at pitch=60 and pitch=72 (same interval pattern) → same hash."""
    c_at_c4 = _make_conflicts(pitch=60)
    c_at_c5 = _make_conflicts(pitch=72)
    h1 = record_conflict(repo_root, c_at_c4)
    h2 = record_conflict(repo_root, c_at_c5)
    # Both have a single conflict with relative pitch offset 0 → same fingerprint.
    assert h1 == h2


def test_record_conflict_different_structure_gives_different_hash(repo_root: Path) -> None:
    """Structurally different conflict sets produce different hashes."""
    c1 = _make_conflicts(region_id="region-1")
    c2 = _make_conflicts(region_id="region-2")
    h1 = record_conflict(repo_root, c1)
    h2 = record_conflict(repo_root, c2)
    assert h1 != h2


# ---------------------------------------------------------------------------
# record_resolution
# ---------------------------------------------------------------------------


def test_record_resolution_stores_postimage(repo_root: Path) -> None:
    """record_resolution writes the postimage file for an existing conflict hash."""
    conflicts = _make_conflicts()
    h = record_conflict(repo_root, conflicts)
    resolution: JSONObject = {"strategy": "ours", "region_id": "region-1"}

    record_resolution(repo_root, h, resolution)

    postimage = repo_root / ".muse" / "rr-cache" / h / "postimage"
    assert postimage.exists()
    stored = json.loads(postimage.read_text())
    assert stored == resolution


def test_record_resolution_raises_for_unknown_hash(repo_root: Path) -> None:
    """record_resolution raises FileNotFoundError for an unrecognised hash."""
    with pytest.raises(FileNotFoundError, match="not found in rr-cache"):
        record_resolution(repo_root, "a" * 64, {"foo": "bar"})


# ---------------------------------------------------------------------------
# apply_rerere
# ---------------------------------------------------------------------------


def test_apply_rerere_returns_applied_count_on_cache_hit(repo_root: Path) -> None:
    """apply_rerere returns (len(conflicts), resolution) when a postimage exists."""
    conflicts = _make_conflicts()
    h = record_conflict(repo_root, conflicts)
    resolution: JSONObject = {"strategy": "ours"}
    record_resolution(repo_root, h, resolution)

    applied, returned_resolution = apply_rerere(repo_root, conflicts)

    assert applied == len(conflicts)
    assert returned_resolution == resolution


def test_apply_rerere_returns_zero_on_cache_miss(repo_root: Path) -> None:
    """apply_rerere returns (0, None) when no postimage is cached."""
    conflicts = _make_conflicts()
    # Record conflict but NOT the resolution → no postimage.
    record_conflict(repo_root, conflicts)

    applied, resolution = apply_rerere(repo_root, conflicts)

    assert applied == 0
    assert resolution is None


def test_apply_rerere_returns_zero_for_unknown_conflicts(repo_root: Path) -> None:
    """apply_rerere returns (0, None) for conflicts with no rr-cache entry at all."""
    conflicts = _make_conflicts(region_id="never-seen-region")
    applied, resolution = apply_rerere(repo_root, conflicts)
    assert applied == 0
    assert resolution is None


def test_apply_rerere_empty_conflicts_returns_zero(repo_root: Path) -> None:
    """apply_rerere short-circuits and returns (0, None) when conflict list is empty."""
    applied, resolution = apply_rerere(repo_root, [])
    assert applied == 0
    assert resolution is None


# ---------------------------------------------------------------------------
# list_rerere
# ---------------------------------------------------------------------------


def test_list_rerere_returns_all_cached_hashes(repo_root: Path) -> None:
    """list_rerere returns a sorted list of all conflict hashes in the cache."""
    h1 = record_conflict(repo_root, _make_conflicts(region_id="r1"))
    h2 = record_conflict(repo_root, _make_conflicts(region_id="r2"))

    hashes = list_rerere(repo_root)

    assert sorted([h1, h2]) == hashes


def test_list_rerere_empty_cache(repo_root: Path) -> None:
    """list_rerere returns an empty list when the cache is empty."""
    assert list_rerere(repo_root) == []


# ---------------------------------------------------------------------------
# forget_rerere
# ---------------------------------------------------------------------------


def test_forget_rerere_removes_one_hash(repo_root: Path) -> None:
    """forget_rerere removes exactly the specified entry."""
    h1 = record_conflict(repo_root, _make_conflicts(region_id="r1"))
    h2 = record_conflict(repo_root, _make_conflicts(region_id="r2"))

    removed = forget_rerere(repo_root, h1)

    assert removed is True
    hashes = list_rerere(repo_root)
    assert h1 not in hashes
    assert h2 in hashes


def test_forget_rerere_returns_false_for_unknown_hash(repo_root: Path) -> None:
    """forget_rerere returns False (not an error) for an unknown hash."""
    assert forget_rerere(repo_root, "b" * 64) is False


# ---------------------------------------------------------------------------
# clear_rerere
# ---------------------------------------------------------------------------


def test_clear_rerere_empties_cache(repo_root: Path) -> None:
    """clear_rerere removes all entries from the rr-cache."""
    record_conflict(repo_root, _make_conflicts(region_id="r1"))
    record_conflict(repo_root, _make_conflicts(region_id="r2"))
    record_conflict(repo_root, _make_conflicts(region_id="r3"))

    removed = clear_rerere(repo_root)

    assert removed == 3
    assert list_rerere(repo_root) == []


def test_clear_rerere_empty_cache_returns_zero(repo_root: Path) -> None:
    """clear_rerere on an already empty cache returns 0."""
    assert clear_rerere(repo_root) == 0
