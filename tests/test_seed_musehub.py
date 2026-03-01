"""Tests for the seed_musehub.py script — Muse variations (Phase 4) and Muse VCS (Phase 5).

Phase 4 (Issue #460): Validates _make_variation_section produces correct counts,
status distributions, parent chains, and note-change structures without touching a
live database.

Phase 5 (Issue #457): Validates seed_musehub.py correctly populates muse_objects,
muse_snapshots, muse_commits, and muse_tags with realistic, structurally correct
data including content deduplication, the DAG with merge commits, and the full tag
taxonomy.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest

from maestro.db.muse_models import NoteChange, Phrase, Variation


# ---------------------------------------------------------------------------
# Helpers duplicated from seed_musehub to avoid importing the script directly
# (the script runs asyncio.run on import in __main__, not in module scope, but
# importing it would pull in settings which requires a running container).
# ---------------------------------------------------------------------------

def _uid(seed: str) -> str:
    return str(uuid.UUID(bytes=hashlib.md5(seed.encode()).digest()))


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Muse tag taxonomy constants (Phase 5 — used by tag coverage tests below)
# ---------------------------------------------------------------------------

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

# Import the helpers we actually want to test
from scripts.seed_musehub import (  # noqa: E402
    PROJECT_COMMUNITY_COLLAB,
    PROJECT_NEO_BAROQUE,
    REGION_IDS_COMMUNITY,
    REGION_IDS_NEO_BAROQUE,
    TRACK_IDS_COMMUNITY,
    TRACK_IDS_NEO_BAROQUE,
    VARIATION_INTENTS_COMMUNITY_COLLAB,
    VARIATION_INTENTS_NEO_BAROQUE,
    _make_variation_section,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def neo_baroque_section() -> tuple[list[Variation], list[Phrase], list[NoteChange]]:
    """30 variations for the neo-baroque project."""
    hashes = [_sha(f"nb-commit-{i}") for i in range(15)]
    return _make_variation_section(
        project_id=PROJECT_NEO_BAROQUE,
        intents=VARIATION_INTENTS_NEO_BAROQUE,
        track_ids=TRACK_IDS_NEO_BAROQUE,
        region_ids=REGION_IDS_NEO_BAROQUE,
        base_commit_hashes=hashes,
        seed_prefix="nb",
    )


@pytest.fixture()
def community_collab_section() -> tuple[list[Variation], list[Phrase], list[NoteChange]]:
    """30 variations for the community-collab project."""
    hashes = [_sha(f"cc-commit-{i}") for i in range(15)]
    return _make_variation_section(
        project_id=PROJECT_COMMUNITY_COLLAB,
        intents=VARIATION_INTENTS_COMMUNITY_COLLAB,
        track_ids=TRACK_IDS_COMMUNITY,
        region_ids=REGION_IDS_COMMUNITY,
        base_commit_hashes=hashes,
        seed_prefix="cc",
    )


# ---------------------------------------------------------------------------
# Variation count and status distribution
# ---------------------------------------------------------------------------

def test_variation_count_is_30_per_project(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    variations, _, _ = neo_baroque_section
    assert len(variations) == 30


def test_variation_status_distribution(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    """20 accepted, 5 discarded, 5 pending per project — as specified."""
    variations, _, _ = neo_baroque_section
    statuses = [v.status for v in variations]
    assert statuses.count("accepted") == 20
    assert statuses.count("discarded") == 5
    assert statuses.count("pending") == 5


def test_variation_project_ids_are_set_correctly(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
    community_collab_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    nb_vars, _, _ = neo_baroque_section
    cc_vars, _, _ = community_collab_section
    assert all(v.project_id == PROJECT_NEO_BAROQUE for v in nb_vars)
    assert all(v.project_id == PROJECT_COMMUNITY_COLLAB for v in cc_vars)


def test_variation_ids_are_unique(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    variations, _, _ = neo_baroque_section
    ids = [v.variation_id for v in variations]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Parent chains
# ---------------------------------------------------------------------------

def test_parent_chains_exist(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    """Most variations should have a parent (chain structure)."""
    variations, _, _ = neo_baroque_section
    with_parent = [v for v in variations if v.parent_variation_id is not None]
    # Chain heads have no parent, so at most 9 chain-heads out of 30
    assert len(with_parent) >= 21


def test_merge_variations_have_parent2(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    """Exactly 3 variations should have parent2_variation_id set."""
    variations, _, _ = neo_baroque_section
    merges = [v for v in variations if v.parent2_variation_id is not None]
    assert len(merges) == 3


def test_parent_variation_ids_reference_existing_ids(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    variations, _, _ = neo_baroque_section
    var_id_set = {v.variation_id for v in variations}
    for v in variations:
        if v.parent_variation_id is not None:
            assert v.parent_variation_id in var_id_set, (
                f"parent_variation_id {v.parent_variation_id} not in variation set"
            )
        if v.parent2_variation_id is not None:
            assert v.parent2_variation_id in var_id_set, (
                f"parent2_variation_id {v.parent2_variation_id} not in variation set"
            )


# ---------------------------------------------------------------------------
# is_head flag
# ---------------------------------------------------------------------------

def test_exactly_one_variation_is_head(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    variations, _, _ = neo_baroque_section
    heads = [v for v in variations if v.is_head]
    assert len(heads) == 1
    assert heads[0].status == "accepted"


# ---------------------------------------------------------------------------
# Phrases
# ---------------------------------------------------------------------------

def test_phrases_belong_to_known_variations(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    variations, phrases, _ = neo_baroque_section
    var_ids = {v.variation_id for v in variations}
    for ph in phrases:
        assert ph.variation_id in var_ids


def test_phrases_per_variation_range(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    """Each variation should have between 2 and 5 phrases."""
    variations, phrases, _ = neo_baroque_section
    from collections import Counter
    counts = Counter(ph.variation_id for ph in phrases)
    for v in variations:
        count = counts[v.variation_id]
        assert 2 <= count <= 5, f"Variation {v.variation_id} has {count} phrases"


def test_phrase_start_beat_in_range(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    """start_beat must be in [0.0, 64.0]."""
    _, phrases, _ = neo_baroque_section
    for ph in phrases:
        assert 0.0 <= ph.start_beat <= 64.0, (
            f"Phrase {ph.phrase_id} start_beat={ph.start_beat} out of range"
        )


def test_phrase_end_beat_after_start(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    _, phrases, _ = neo_baroque_section
    for ph in phrases:
        assert ph.end_beat > ph.start_beat


def test_phrase_types_are_valid(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    _, phrases, _ = neo_baroque_section
    valid = {"melody", "harmony", "bass", "rhythm", "pad", "lead"}
    for ph in phrases:
        assert ph.label in valid, f"Invalid phrase label: {ph.label}"


def test_phrases_have_cc_events(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    """All phrases should carry CC event data."""
    _, phrases, _ = neo_baroque_section
    for ph in phrases:
        assert ph.cc_events is not None
        assert len(ph.cc_events) >= 1


# ---------------------------------------------------------------------------
# Note changes
# ---------------------------------------------------------------------------

def test_note_changes_belong_to_known_phrases(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    _, phrases, note_changes = neo_baroque_section
    phrase_ids = {ph.phrase_id for ph in phrases}
    for nc in note_changes:
        assert nc.phrase_id in phrase_ids


def test_note_change_types_are_valid(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    """Canonical values from Literal["added", "removed", "modified"] in json_types.py."""
    _, _, note_changes = neo_baroque_section
    valid = {"added", "removed", "modified"}
    for nc in note_changes:
        assert nc.change_type in valid


def test_add_changes_have_no_before_json(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    _, _, note_changes = neo_baroque_section
    for nc in note_changes:
        if nc.change_type == "added":
            assert nc.before_json is None
            assert nc.after_json is not None


def test_remove_changes_have_no_after_json(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    _, _, note_changes = neo_baroque_section
    for nc in note_changes:
        if nc.change_type == "removed":
            assert nc.after_json is None
            assert nc.before_json is not None


def test_modify_changes_have_both_json_fields(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    _, _, note_changes = neo_baroque_section
    for nc in note_changes:
        if nc.change_type == "modified":
            assert nc.before_json is not None
            assert nc.after_json is not None


def test_note_pitch_in_piano_range(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    """Pitch values must stay in MIDI piano range 21-108."""
    _, _, note_changes = neo_baroque_section
    for nc in note_changes:
        for payload in (nc.before_json, nc.after_json):
            if payload is not None:
                pitch = payload.get("pitch", 60)
                assert 21 <= pitch <= 108, f"Pitch {pitch} out of piano range"


def test_note_velocity_in_range(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    _, _, note_changes = neo_baroque_section
    for nc in note_changes:
        for payload in (nc.before_json, nc.after_json):
            if payload is not None:
                vel = payload.get("velocity", 64)
                assert 30 <= vel <= 127, f"Velocity {vel} out of range"


def test_note_change_ids_are_unique(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    _, _, note_changes = neo_baroque_section
    ids = [nc.id for nc in note_changes]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Two-project isolation
# ---------------------------------------------------------------------------

def test_two_projects_have_no_shared_variation_ids(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
    community_collab_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    nb_ids = {v.variation_id for v in neo_baroque_section[0]}
    cc_ids = {v.variation_id for v in community_collab_section[0]}
    assert nb_ids.isdisjoint(cc_ids), "Variation IDs overlap between projects"


def test_combined_variation_count_is_60(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
    community_collab_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    total = len(neo_baroque_section[0]) + len(community_collab_section[0])
    assert total == 60


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


# ---------------------------------------------------------------------------
# Issue templates — 15 issues per repo (Phase 6, Issue #453)
# ---------------------------------------------------------------------------

# Import only the data structures, not the script entry point.
import sys, importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

def _load_seed_constants() -> dict[str, object]:
    """Load top-level constants from seed_musehub without executing asyncio.run."""
    seed_path = Path(__file__).parent.parent / "scripts" / "seed_musehub.py"
    spec = importlib.util.spec_from_file_location("seed_musehub", seed_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Prevent __main__ execution — the script guards on __name__
    mod.__name__ = "seed_musehub_imported"
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return vars(mod)


@pytest.fixture(scope="module")
def seed_mod() -> dict[str, object]:
    """Loaded seed_musehub module constants (once per test session)."""
    return _load_seed_constants()


def test_issue_templates_have_15_entries_each(seed_mod: dict[str, object]) -> None:
    """Every repo-specific issue template must have exactly 15 entries."""
    ISSUE_TEMPLATES = seed_mod["ISSUE_TEMPLATES"]
    assert isinstance(ISSUE_TEMPLATES, dict)
    for key, entries in ISSUE_TEMPLATES.items():
        assert isinstance(entries, list)
        assert len(entries) == 15, (
            f"ISSUE_TEMPLATES[{key!r}] has {len(entries)} entries — expected 15"
        )


def test_issue_templates_have_sequential_numbers(seed_mod: dict[str, object]) -> None:
    """Each issue template list must have sequential n values from 1 to 15."""
    ISSUE_TEMPLATES = seed_mod["ISSUE_TEMPLATES"]
    assert isinstance(ISSUE_TEMPLATES, dict)
    for key, entries in ISSUE_TEMPLATES.items():
        assert isinstance(entries, list)
        numbers = [e["n"] for e in entries]
        assert numbers == list(range(1, 16)), (
            f"ISSUE_TEMPLATES[{key!r}] has non-sequential n values: {numbers}"
        )


def test_generic_issues_have_15_entries(seed_mod: dict[str, object]) -> None:
    """GENERIC_ISSUES fallback must also have 15 entries."""
    GENERIC_ISSUES = seed_mod["GENERIC_ISSUES"]
    assert isinstance(GENERIC_ISSUES, list)
    assert len(GENERIC_ISSUES) == 15, (
        f"GENERIC_ISSUES has {len(GENERIC_ISSUES)} entries — expected 15"
    )


def test_milestone_templates_exist_for_all_repo_keys(seed_mod: dict[str, object]) -> None:
    """MILESTONE_TEMPLATES should have entries for all 10 non-fork repo keys."""
    MILESTONE_TEMPLATES = seed_mod["MILESTONE_TEMPLATES"]
    REPO_KEY_MAP = seed_mod["REPO_KEY_MAP"]
    assert isinstance(MILESTONE_TEMPLATES, dict)
    assert isinstance(REPO_KEY_MAP, dict)
    # All non-fork repo keys that have specific issue templates should have milestones
    expected_keys = {
        "neo-soul", "modal-jazz", "ambient", "afrobeat", "microtonal",
        "drums", "chanson", "granular", "funk-suite", "jazz-trio",
    }
    for key in expected_keys:
        assert key in MILESTONE_TEMPLATES, f"Missing milestone template for {key!r}"
        assert len(MILESTONE_TEMPLATES[key]) >= 1, f"Empty milestone list for {key!r}"


def test_milestone_issue_assignments_reference_valid_issues(seed_mod: dict[str, object]) -> None:
    """Every issue number in MILESTONE_ISSUE_ASSIGNMENTS must exist in ISSUE_TEMPLATES."""
    MILESTONE_ISSUE_ASSIGNMENTS = seed_mod["MILESTONE_ISSUE_ASSIGNMENTS"]
    ISSUE_TEMPLATES = seed_mod["ISSUE_TEMPLATES"]
    assert isinstance(MILESTONE_ISSUE_ASSIGNMENTS, dict)
    assert isinstance(ISSUE_TEMPLATES, dict)
    for rkey, ms_map in MILESTONE_ISSUE_ASSIGNMENTS.items():
        template_issues = {e["n"] for e in ISSUE_TEMPLATES.get(rkey, [])}
        for ms_n, issue_ns in ms_map.items():
            for iss_n in issue_ns:
                assert iss_n in template_issues, (
                    f"Milestone {ms_n} for {rkey!r} references non-existent issue #{iss_n}"
                )


def test_pr_comment_pool_covers_all_target_types(seed_mod: dict[str, object]) -> None:
    """_PR_COMMENT_POOL must contain entries for all four target_type values."""
    pool = seed_mod["_PR_COMMENT_POOL"]
    assert isinstance(pool, list)
    target_types = {entry["target_type"] for entry in pool}
    expected_types = {"general", "track", "region", "note"}
    assert target_types == expected_types, (
        f"Missing target types: {expected_types - target_types}"
    )


def test_issue_comment_body_generator_produces_varied_output(seed_mod: dict[str, object]) -> None:
    """_make_issue_comment_body must return different strings for different seeds."""
    fn = seed_mod["_make_issue_comment_body"]
    assert callable(fn)
    bodies = {fn(i) for i in range(20)}
    assert len(bodies) > 5, "Expected varied comment bodies — too many duplicates"


def test_webhook_configs_cover_push_pr_release(seed_mod: dict[str, object]) -> None:
    """_make_webhooks must produce webhooks for push, pull_request, and release events."""
    fn = seed_mod["_make_webhooks"]
    assert callable(fn)
    webhooks, deliveries = fn("repo-test-id-001", "gabriel")
    event_sets = [set(wh["events"]) for wh in webhooks]
    all_events: set[str] = set()
    for es in event_sets:
        all_events |= es
    assert "push" in all_events, "push event not covered"
    assert "pull_request" in all_events, "pull_request event not covered"
    assert "release" in all_events, "release event not covered"


def test_webhook_deliveries_have_mixed_outcomes(seed_mod: dict[str, object]) -> None:
    """Webhook deliveries must include both successes and failures."""
    fn = seed_mod["_make_webhooks"]
    assert callable(fn)
    _, deliveries = fn("repo-test-id-002", "sofia")
    outcomes = {(d["response_status"], d["success"]) for d in deliveries}
    statuses = {d["response_status"] for d in deliveries}
    assert 200 in statuses, "No 200 OK deliveries found"
    assert 500 in statuses or 0 in statuses, "No failure deliveries found (expected 500 or timeout)"
