"""Tests for the seed_musehub.py muse variation seeding section.

Validates that _make_variation_section produces the correct counts, status
distributions, parent chains, and note-change structures without touching a
live database.
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
    _, _, note_changes = neo_baroque_section
    valid = {"add", "remove", "modify"}
    for nc in note_changes:
        assert nc.change_type in valid


def test_add_changes_have_no_before_json(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    _, _, note_changes = neo_baroque_section
    for nc in note_changes:
        if nc.change_type == "add":
            assert nc.before_json is None
            assert nc.after_json is not None


def test_remove_changes_have_no_after_json(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    _, _, note_changes = neo_baroque_section
    for nc in note_changes:
        if nc.change_type == "remove":
            assert nc.after_json is None
            assert nc.before_json is not None


def test_modify_changes_have_both_json_fields(
    neo_baroque_section: tuple[list[Variation], list[Phrase], list[NoteChange]],
) -> None:
    _, _, note_changes = neo_baroque_section
    for nc in note_changes:
        if nc.change_type == "modify":
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
