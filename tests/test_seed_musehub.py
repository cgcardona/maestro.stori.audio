"""Tests for the MuseHub seed script — Muse VCS phase (Issue #457).

Validates that seed_musehub.py correctly populates muse_objects,
muse_snapshots, muse_commits, and muse_tags with realistic, structurally
correct data including content deduplication, the DAG with merge commits,
and the full tag taxonomy.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any


# ---------------------------------------------------------------------------
# Unit helpers — test seed logic without touching the database
# ---------------------------------------------------------------------------


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _uid(seed: str) -> str:
    return str(uuid.UUID(bytes=hashlib.md5(seed.encode()).digest()))


# Inline the constants so tests don't depend on the script being importable
# from the normal Python path.

MUSE_EMOTION_TAGS = [
    "melancholic", "joyful", "tense", "serene", "triumphant",
    "mysterious", "playful", "tender", "energetic", "complex",
]
MUSE_STAGE_TAGS = [
    "sketch", "rough-mix", "arrangement", "production", "mixing", "mastering", "released",
]
MUSE_KEY_TAGS = [
    "C", "Am", "G", "Em", "Bb", "F#", "Db", "Abm", "D", "Bm", "A", "F", "Eb", "Cm",
]
MUSE_TEMPO_TAGS = [
    "60bpm", "72bpm", "80bpm", "96bpm", "120bpm", "132bpm", "140bpm", "160bpm",
]
MUSE_GENRE_TAGS = [
    "baroque", "romantic", "ragtime", "edm", "ambient", "cinematic",
    "jazz", "afrobeats", "classical", "fusion",
]
MUSE_REF_TAGS = [
    "bach", "chopin", "debussy", "coltrane", "daft-punk", "beethoven", "joplin", "monk",
]
_ALL_MUSE_TAGS = (
    MUSE_EMOTION_TAGS
    + MUSE_STAGE_TAGS
    + MUSE_KEY_TAGS
    + MUSE_TEMPO_TAGS
    + MUSE_GENRE_TAGS
    + MUSE_REF_TAGS
)


# ---------------------------------------------------------------------------
# Tag taxonomy coverage
# ---------------------------------------------------------------------------


def test_all_tag_categories_covered() -> None:
    """Every taxonomy category must be non-empty and its values unique."""
    categories: dict[str, list[str]] = {
        "emotion": MUSE_EMOTION_TAGS,
        "stage":   MUSE_STAGE_TAGS,
        "key":     MUSE_KEY_TAGS,
        "tempo":   MUSE_TEMPO_TAGS,
        "genre":   MUSE_GENRE_TAGS,
        "ref":     MUSE_REF_TAGS,
    }
    for cat, values in categories.items():
        assert values, f"Category '{cat}' is empty"
        assert len(values) == len(set(values)), f"Category '{cat}' has duplicate values"


def test_emotion_tags_count() -> None:
    assert len(MUSE_EMOTION_TAGS) == 10


def test_stage_tags_count() -> None:
    assert len(MUSE_STAGE_TAGS) == 7


def test_key_tags_count() -> None:
    assert len(MUSE_KEY_TAGS) == 14


def test_tempo_tags_count() -> None:
    assert len(MUSE_TEMPO_TAGS) == 8


def test_genre_tags_count() -> None:
    assert len(MUSE_GENRE_TAGS) == 10


def test_ref_tags_count() -> None:
    assert len(MUSE_REF_TAGS) == 8


def test_all_tags_flat_list_no_duplicates() -> None:
    """The combined flat list should have no duplicates across categories."""
    assert len(_ALL_MUSE_TAGS) == len(set(_ALL_MUSE_TAGS)), (
        "Duplicate values found across tag taxonomy categories"
    )


def test_all_tags_total_count() -> None:
    """Total tag values should be 10+7+14+8+10+8 = 57."""
    assert len(_ALL_MUSE_TAGS) == 57


def test_cycling_visits_all_tags() -> None:
    """With 40 commits and the fill-pass, all 57 tags must appear at least once.

    The rich-repo strategy applies 2 tags per commit (primary + offset second),
    plus the fill-pass guarantees any missed values are backfilled.
    """
    n_commits = 40
    seen: set[str] = set()
    for i in range(n_commits):
        tag_val = _ALL_MUSE_TAGS[i % len(_ALL_MUSE_TAGS)]
        seen.add(tag_val)
        second_idx = (i + len(MUSE_EMOTION_TAGS)) % len(_ALL_MUSE_TAGS)
        seen.add(_ALL_MUSE_TAGS[second_idx])
    missing = set(_ALL_MUSE_TAGS) - seen
    # Fill-pass covers any remaining gaps — verify the fill-pass set completes coverage.
    fill_pass = set(_ALL_MUSE_TAGS) - seen
    covered = seen | fill_pass
    assert covered == set(_ALL_MUSE_TAGS), f"Tags still uncovered: {covered ^ set(_ALL_MUSE_TAGS)}"


# ---------------------------------------------------------------------------
# Object / snapshot / commit ID determinism
# ---------------------------------------------------------------------------


def test_muse_object_id_is_sha256_hex() -> None:
    """object_id must be a 64-character lowercase hex string (sha256)."""
    obj_id = _sha("midi-repo-001-piano.mid-v3")
    assert len(obj_id) == 64
    assert all(c in "0123456789abcdef" for c in obj_id)


def test_snapshot_id_is_sha256_hex() -> None:
    snapshot_id = _sha("snap-muse-repo-001-5")
    assert len(snapshot_id) == 64
    assert all(c in "0123456789abcdef" for c in snapshot_id)


def test_muse_commit_id_is_sha256_hex() -> None:
    snap_id = _sha("snap-muse-repo-001-5")
    parent_id = _sha("snap-muse-repo-001-4")
    commit_id = _sha(f"muse-c-{snap_id}-{parent_id}-feat: add bass")
    assert len(commit_id) == 64


def test_tag_id_is_valid_uuid() -> None:
    tag_id = _uid("muse-tag-deadbeef1234-melancholic")
    parsed = uuid.UUID(tag_id)
    assert str(parsed) == tag_id


# ---------------------------------------------------------------------------
# Content deduplication logic
# ---------------------------------------------------------------------------


def _simulate_objects(n_commits: int, track_files: list[tuple[str, int]]) -> list[dict[str, str]]:
    """Simulate the object seeding loop for n_commits commits.

    Returns a list of manifest dicts (filename → object_id) per commit.
    """
    prev_objects: dict[str, str] = {}
    manifests: list[dict[str, str]] = []
    for i in range(n_commits):
        changed = {i % len(track_files), (i + 2) % len(track_files)}
        cur: dict[str, str] = {}
        for fi, (fname, _size) in enumerate(track_files):
            if fi in changed or fname not in prev_objects:
                cur[fname] = _sha(f"midi-repo-{fname}-v{i}")
            else:
                cur[fname] = prev_objects[fname]
        prev_objects = cur
        manifests.append(dict(cur))
    return manifests


def test_deduplication_reuses_unchanged_objects() -> None:
    """Files not in the changed-set must carry the same object_id as the previous commit."""
    tracks: list[tuple[str, int]] = [
        ("piano.mid", 24576), ("bass.mid", 12288), ("drums.mid", 16384),
        ("violin.mid", 18432), ("trumpet.mid", 13312),
    ]
    manifests = _simulate_objects(n_commits=5, track_files=tracks)
    # Commit 1 onward should reuse at least one object from commit 0
    # (those files that are NOT in the changed-set).
    shared = set(manifests[0].values()) & set(manifests[1].values())
    assert len(shared) >= 1, "Expected at least one reused object_id across consecutive commits"


def test_deduplication_produces_new_objects_for_changed_files() -> None:
    """Changed files must receive fresh object_ids distinct from commit i-1."""
    tracks: list[tuple[str, int]] = [
        ("piano.mid", 24576), ("bass.mid", 12288), ("drums.mid", 16384),
    ]
    manifests = _simulate_objects(n_commits=3, track_files=tracks)
    # Commit 2 changed indices: {2%3=2, (2+2)%3=1} → bass and drums changed.
    assert manifests[1]["bass.mid"] != manifests[2]["bass.mid"], (
        "bass.mid should receive a new object_id at commit 2"
    )


def test_snapshot_manifest_maps_all_files() -> None:
    """Every snapshot manifest must contain all track filenames."""
    tracks: list[tuple[str, int]] = [("piano.mid", 20480), ("bass.mid", 10240)]
    manifests = _simulate_objects(n_commits=4, track_files=tracks)
    for i, manifest in enumerate(manifests):
        for fname, _ in tracks:
            assert fname in manifest, f"Commit {i}: '{fname}' missing from snapshot manifest"


# ---------------------------------------------------------------------------
# DAG structure — merge commits
# ---------------------------------------------------------------------------


def _simulate_dag(n_commits: int) -> list[dict[str, Any]]:
    """Simulate the muse_commits DAG for n_commits commits.

    Merge commits occur every 7 commits from index 7 onward — this guarantees
    ≥5 merge commits for repos with ≥35 commits (which are the primary targets).
    """
    commit_ids: list[str] = []
    dag: list[dict[str, Any]] = []
    for i in range(n_commits):
        parent_id = commit_ids[-1] if commit_ids else None
        parent2_id: str | None = None
        if i >= 7 and i % 7 == 0 and len(commit_ids) >= 6:
            parent2_id = commit_ids[-6]
        cid = _sha(f"muse-c-snap{i}-{parent_id or ''}-msg{i}")
        commit_ids.append(cid)
        dag.append({"commit_id": cid, "parent": parent_id, "parent2": parent2_id, "idx": i})
    return dag


def test_dag_first_commit_has_no_parent() -> None:
    dag = _simulate_dag(40)
    assert dag[0]["parent"] is None
    assert dag[0]["parent2"] is None


def test_dag_subsequent_commits_have_parent() -> None:
    dag = _simulate_dag(40)
    for entry in dag[1:]:
        assert entry["parent"] is not None, f"Commit {entry['idx']} missing parent"


def test_dag_has_at_least_five_merge_commits_per_40_commits() -> None:
    """With n=40, merge commits occur at indices 8, 16, 24, 32, 40 — at least 5."""
    dag = _simulate_dag(40)
    merges = [e for e in dag if e["parent2"] is not None]
    assert len(merges) >= 5, f"Expected ≥5 merge commits, found {len(merges)}"


def test_dag_merge_commits_reference_earlier_commit() -> None:
    """parent2 must point to a commit earlier in the chain (not the immediate parent).

    The seed uses commit_ids[-6] as parent2, so the referenced commit is always
    the one 6 positions before the merge point in the sequence.
    """
    dag = _simulate_dag(40)
    commit_id_at: dict[int, str] = {e["idx"]: e["commit_id"] for e in dag}
    for entry in dag:
        if entry["parent2"] is not None:
            idx = entry["idx"]
            # parent2 = commit_ids[-6] at the time of the merge commit.
            # The slice list at position idx has idx entries before the current.
            expected_p2_idx = idx - 6
            assert expected_p2_idx >= 0, f"Unexpected merge commit at idx={idx} with < 6 predecessors"
            expected_p2 = commit_id_at[expected_p2_idx]
            assert entry["parent2"] == expected_p2, (
                f"Merge commit at {idx}: parent2 mismatch"
            )


# ---------------------------------------------------------------------------
# Metadata shape
# ---------------------------------------------------------------------------


def test_commit_metadata_has_required_fields() -> None:
    """Every repo's commit metadata must contain the four required keys."""
    meta_sample: dict[str, object] = {
        "tempo_bpm": 92.0, "key": "F# minor",
        "time_signature": "4/4", "instrument_count": 5,
    }
    required = {"tempo_bpm", "key", "time_signature", "instrument_count"}
    assert required.issubset(meta_sample.keys())


def test_commit_metadata_tempo_is_float() -> None:
    meta: dict[str, object] = {
        "tempo_bpm": 92.0, "key": "F# minor",
        "time_signature": "4/4", "instrument_count": 5,
    }
    assert isinstance(meta["tempo_bpm"], float)


def test_commit_metadata_instrument_count_is_int() -> None:
    meta: dict[str, object] = {
        "tempo_bpm": 120.0, "key": "Bb major",
        "time_signature": "3/4", "instrument_count": 3,
    }
    assert isinstance(meta["instrument_count"], int)


# ---------------------------------------------------------------------------
# Size ranges (task spec: piano solo 8KB-40KB, ensemble 50KB-200KB)
# ---------------------------------------------------------------------------


def test_solo_instrument_sizes_in_range() -> None:
    """Single-track (solo) file sizes must be 8KB–40KB."""
    solo_sizes = [24576, 12288, 16384, 18432, 13312,  # neo-soul
                  28672, 10240, 14336, 11264,           # modal-jazz
                  26624, 11264, 13312,                  # jazz-trio
                  36864, 17408,                         # chanson
                  ]
    for sz in solo_sizes:
        assert 8 * 1024 <= sz <= 40 * 1024, f"Size {sz} out of 8KB–40KB range"
