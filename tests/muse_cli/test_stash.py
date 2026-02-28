"""Tests for ``muse stash`` — full lifecycle and edge cases.

Exercises:
- ``test_stash_push_pop_roundtrip``     — regression: push saves state, pop restores it
- ``test_stash_push_clears_workdir``    — full push restores HEAD (empty branch → clear)
- ``test_stash_list_shows_entries``     — list returns entries newest-first
- ``test_stash_apply_keeps_entry``      — apply does not remove the entry
- ``test_stash_drop_removes_entry``     — drop removes exactly the target entry
- ``test_stash_clear_removes_all``      — clear empties the entire stack
- ``test_stash_track_scoping``         — --track scopes files saved and restored
- ``test_stash_section_scoping``       — --section scopes files saved and restored
- ``test_stash_pop_index_oob``         — pop on empty stack exits with USER_ERROR
- ``test_stash_apply_index_oob``       — apply on missing index raises IndexError
- ``test_stash_drop_index_oob``        — drop on missing index raises IndexError
- ``test_stash_multiple_entries``      — multiple pushes produce a stack
- ``test_stash_push_empty_workdir``    — push on empty workdir is a noop
- ``test_stash_missing_objects``       — apply when object store empty reports missing
- ``test_stash_push_with_head_manifest`` — push restores HEAD snapshot to workdir
- ``test_stash_message_stored``        — custom --message is preserved
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest

from maestro.services.muse_stash import (
    StashApplyResult,
    StashEntry,
    StashPushResult,
    apply_stash,
    clear_stash,
    drop_stash,
    list_stash,
    push_stash,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Create a minimal .muse/ layout (no DB required for stash tests)."""
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _populate_workdir(
    root: pathlib.Path,
    files: dict[str, bytes] | None = None,
) -> None:
    """Write files into muse-work/."""
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    if files is None:
        files = {"beat.mid": b"MIDI-DATA", "lead.mp3": b"MP3-DATA"}
    for rel, content in files.items():
        dest = workdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)


def _object_path(root: pathlib.Path, oid: str) -> pathlib.Path:
    """Return sharded object store path."""
    return root / ".muse" / "objects" / oid[:2] / oid[2:]


# ---------------------------------------------------------------------------
# Regression — push / pop round-trip
# ---------------------------------------------------------------------------


def test_stash_push_pop_roundtrip(tmp_path: pathlib.Path) -> None:
    """Regression: push saves file content; pop restores it exactly."""
    _init_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"wip-chorus-beat"})

    # Push — no HEAD commit, so workdir is cleared after push
    result = push_stash(tmp_path, message="WIP chorus", head_manifest=None)

    assert result.files_stashed == 1
    assert result.stash_ref == "stash@{0}"
    assert result.message == "WIP chorus"
    # After full push (no HEAD), workdir should be cleared
    workdir = tmp_path / "muse-work"
    remaining = list(workdir.rglob("*"))
    assert not any(f.is_file() for f in remaining)

    # Pop — restores the stashed file
    pop_result = apply_stash(tmp_path, 0, drop=True)

    assert pop_result.dropped is True
    assert pop_result.files_applied == 1
    assert (tmp_path / "muse-work" / "beat.mid").read_bytes() == b"wip-chorus-beat"
    # Stash stack should now be empty
    assert list_stash(tmp_path) == []


# ---------------------------------------------------------------------------
# Push clears workdir (no HEAD commit)
# ---------------------------------------------------------------------------


def test_stash_push_clears_workdir(tmp_path: pathlib.Path) -> None:
    """Full push with no HEAD commit clears muse-work/ after stashing."""
    _init_repo(tmp_path)
    _populate_workdir(tmp_path, {"a.mid": b"A", "b.mid": b"B"})

    result = push_stash(tmp_path, head_manifest=None)

    assert result.files_stashed == 2
    assert result.head_restored is False

    workdir = tmp_path / "muse-work"
    files = [f for f in workdir.rglob("*") if f.is_file()]
    assert files == [], "workdir should be empty after full push with no HEAD"


# ---------------------------------------------------------------------------
# Push with HEAD manifest restores HEAD snapshot
# ---------------------------------------------------------------------------


def test_stash_push_with_head_manifest(tmp_path: pathlib.Path) -> None:
    """Push with a HEAD manifest writes HEAD files back to muse-work/."""
    from maestro.muse_cli.snapshot import hash_file

    _init_repo(tmp_path)

    # Simulate HEAD commit: store a "committed" file in the object store
    head_file_content = b"committed-beat"
    # Compute oid by first writing a temp file
    tmp_file = tmp_path / "tmp_head_file"
    tmp_file.write_bytes(head_file_content)
    oid = hash_file(tmp_file)
    tmp_file.unlink()

    obj_dest = _object_path(tmp_path, oid)
    obj_dest.parent.mkdir(parents=True, exist_ok=True)
    obj_dest.write_bytes(head_file_content)

    head_manifest = {"beat.mid": oid}

    # Populate workdir with WIP changes
    _populate_workdir(tmp_path, {"beat.mid": b"wip-chorus", "synth.mid": b"wip-synth"})

    result = push_stash(tmp_path, message="WIP", head_manifest=head_manifest)

    assert result.files_stashed == 2
    assert result.head_restored is True
    # beat.mid should now be the HEAD version
    assert (tmp_path / "muse-work" / "beat.mid").read_bytes() == head_file_content
    # synth.mid was not in HEAD → should be deleted after full push
    assert not (tmp_path / "muse-work" / "synth.mid").exists()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_stash_list_shows_entries(tmp_path: pathlib.Path) -> None:
    """list_stash returns entries newest-first with correct indices."""
    _init_repo(tmp_path)

    _populate_workdir(tmp_path, {"a.mid": b"A"})
    push_stash(tmp_path, message="first stash", head_manifest=None)

    _populate_workdir(tmp_path, {"b.mid": b"B"})
    push_stash(tmp_path, message="second stash", head_manifest=None)

    entries = list_stash(tmp_path)
    assert len(entries) == 2
    # Newest first
    assert entries[0].index == 0
    assert entries[1].index == 1
    # Messages are preserved
    messages = {e.message for e in entries}
    assert "first stash" in messages
    assert "second stash" in messages


# ---------------------------------------------------------------------------
# apply keeps entry
# ---------------------------------------------------------------------------


def test_stash_apply_keeps_entry(tmp_path: pathlib.Path) -> None:
    """apply does NOT remove the stash entry (drop=False)."""
    _init_repo(tmp_path)
    _populate_workdir(tmp_path, {"x.mid": b"WIP"})

    push_stash(tmp_path, head_manifest=None)

    result = apply_stash(tmp_path, 0, drop=False)

    assert result.dropped is False
    # Entry still on the stack
    assert len(list_stash(tmp_path)) == 1
    # File restored
    assert (tmp_path / "muse-work" / "x.mid").read_bytes() == b"WIP"


# ---------------------------------------------------------------------------
# drop
# ---------------------------------------------------------------------------


def test_stash_drop_removes_entry(tmp_path: pathlib.Path) -> None:
    """drop_stash removes exactly the targeted entry."""
    _init_repo(tmp_path)

    _populate_workdir(tmp_path, {"a.mid": b"A"})
    push_stash(tmp_path, message="entry-A", head_manifest=None)

    _populate_workdir(tmp_path, {"b.mid": b"B"})
    push_stash(tmp_path, message="entry-B", head_manifest=None)

    # Stack: index 0 = entry-B (newest), index 1 = entry-A
    dropped = drop_stash(tmp_path, index=1)

    assert dropped.message == "entry-A"
    remaining = list_stash(tmp_path)
    assert len(remaining) == 1
    assert remaining[0].message == "entry-B"


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


def test_stash_clear_removes_all(tmp_path: pathlib.Path) -> None:
    """clear_stash removes all entries and returns the count."""
    _init_repo(tmp_path)

    for label in ["first", "second", "third"]:
        _populate_workdir(tmp_path, {f"{label}.mid": label.encode()})
        push_stash(tmp_path, message=label, head_manifest=None)

    count = clear_stash(tmp_path)

    assert count == 3
    assert list_stash(tmp_path) == []


# ---------------------------------------------------------------------------
# Track scoping
# ---------------------------------------------------------------------------


def test_stash_track_scoping(tmp_path: pathlib.Path) -> None:
    """--track scopes stash to tracks/<track>/ files only."""
    _init_repo(tmp_path)
    _populate_workdir(
        tmp_path,
        {
            "tracks/drums/beat.mid": b"drums-wip",
            "tracks/bass/bass.mid": b"bass-wip",
        },
    )

    result = push_stash(tmp_path, track="drums", head_manifest=None)

    # Only drums file stashed
    assert result.files_stashed == 1
    entries = list_stash(tmp_path)
    assert len(entries) == 1
    assert "tracks/drums/beat.mid" in entries[0].manifest
    assert "tracks/bass/bass.mid" not in entries[0].manifest
    assert entries[0].track == "drums"
    # bass file should still be in workdir (unscoped push leaves it)
    assert (tmp_path / "muse-work" / "tracks" / "bass" / "bass.mid").exists()


# ---------------------------------------------------------------------------
# Section scoping
# ---------------------------------------------------------------------------


def test_stash_section_scoping(tmp_path: pathlib.Path) -> None:
    """--section scopes stash to sections/<section>/ files only."""
    _init_repo(tmp_path)
    _populate_workdir(
        tmp_path,
        {
            "sections/chorus/chords.mid": b"chorus-wip",
            "sections/verse/lead.mid": b"verse-stable",
        },
    )

    result = push_stash(tmp_path, section="chorus", head_manifest=None)

    assert result.files_stashed == 1
    entries = list_stash(tmp_path)
    assert "sections/chorus/chords.mid" in entries[0].manifest
    assert "sections/verse/lead.mid" not in entries[0].manifest
    assert entries[0].section == "chorus"
    # verse file untouched
    assert (tmp_path / "muse-work" / "sections" / "verse" / "lead.mid").exists()


# ---------------------------------------------------------------------------
# OOB index handling
# ---------------------------------------------------------------------------


def test_stash_pop_on_empty_stack_raises(tmp_path: pathlib.Path) -> None:
    """apply_stash raises IndexError when stash is empty."""
    _init_repo(tmp_path)

    with pytest.raises(IndexError):
        apply_stash(tmp_path, 0, drop=True)


def test_stash_apply_index_oob_raises(tmp_path: pathlib.Path) -> None:
    """apply_stash raises IndexError for an out-of-range index."""
    _init_repo(tmp_path)
    _populate_workdir(tmp_path, {"x.mid": b"X"})
    push_stash(tmp_path, head_manifest=None)

    with pytest.raises(IndexError):
        apply_stash(tmp_path, 5, drop=False)


def test_stash_drop_index_oob_raises(tmp_path: pathlib.Path) -> None:
    """drop_stash raises IndexError for an out-of-range index."""
    _init_repo(tmp_path)

    with pytest.raises(IndexError):
        drop_stash(tmp_path, 0)


# ---------------------------------------------------------------------------
# Multiple entries stack ordering
# ---------------------------------------------------------------------------


def test_stash_multiple_entries(tmp_path: pathlib.Path) -> None:
    """Multiple pushes build a stack; index 0 is always most recent."""
    _init_repo(tmp_path)

    content_map = {"first": b"v1", "second": b"v2", "third": b"v3"}
    for label, content in content_map.items():
        _populate_workdir(tmp_path, {f"{label}.mid": content})
        push_stash(tmp_path, message=label, head_manifest=None)

    entries = list_stash(tmp_path)
    assert len(entries) == 3
    # index 0 = most recently pushed = "third"
    assert entries[0].message == "third"
    assert entries[1].message == "second"
    assert entries[2].message == "first"


# ---------------------------------------------------------------------------
# Empty workdir push is a noop
# ---------------------------------------------------------------------------


def test_stash_push_empty_workdir(tmp_path: pathlib.Path) -> None:
    """push_stash on an empty workdir returns files_stashed=0 (noop)."""
    _init_repo(tmp_path)
    (tmp_path / "muse-work").mkdir()

    result = push_stash(tmp_path, head_manifest=None)

    assert result.files_stashed == 0
    assert result.stash_ref == ""
    assert list_stash(tmp_path) == []


# ---------------------------------------------------------------------------
# Missing objects are reported (not silently dropped)
# ---------------------------------------------------------------------------


def test_stash_missing_objects_reported(tmp_path: pathlib.Path) -> None:
    """apply_stash reports paths whose objects are absent from the store."""
    from maestro.muse_cli.snapshot import hash_file

    _init_repo(tmp_path)
    _populate_workdir(tmp_path, {"chorus.mid": b"wip"})

    # Push normally (object is stored)
    push_stash(tmp_path, head_manifest=None)

    # Manually delete the object to simulate missing store entry
    entries = list_stash(tmp_path)
    oid = entries[0].manifest["chorus.mid"]
    obj_file = _object_path(tmp_path, oid)
    assert obj_file.exists()
    obj_file.unlink()

    # Apply should report the missing file, not crash
    result = apply_stash(tmp_path, 0, drop=False)

    assert "chorus.mid" in result.missing
    assert result.files_applied == 0


# ---------------------------------------------------------------------------
# Custom message is stored
# ---------------------------------------------------------------------------


def test_stash_message_stored(tmp_path: pathlib.Path) -> None:
    """Custom --message text is persisted in the stash entry."""
    _init_repo(tmp_path)
    _populate_workdir(tmp_path, {"synth.mid": b"wip"})

    push_stash(tmp_path, message="half-finished synth arpeggio", head_manifest=None)

    entries = list_stash(tmp_path)
    assert entries[0].message == "half-finished synth arpeggio"
