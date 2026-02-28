"""Integration tests for ``muse commit``.

Tests exercise ``_commit_async`` directly with an in-memory SQLite session
so no real Postgres instance is required.  The ``muse_cli_db_session``
fixture (defined in tests/muse_cli/conftest.py) provides the isolated
SQLite session.

All async tests use ``@pytest.mark.anyio`` (configured for asyncio mode
in pyproject.toml).
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from maestro.muse_cli.commands.commit import (
    _append_co_author,
    _apply_commit_music_metadata,
    _commit_async,
    build_snapshot_manifest_from_batch,
    load_muse_batch,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliObject, MuseCliSnapshot
from maestro.muse_cli.snapshot import (
    build_snapshot_manifest,
    compute_snapshot_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Create a minimal .muse/ layout so _commit_async can read repo state."""
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": rid, "schema_version": "1"})
    )
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")  # no commits yet
    return rid


def _populate_workdir(root: pathlib.Path, files: dict[str, bytes] | None = None) -> None:
    """Create muse-work/ with one or more files."""
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    if files is None:
        files = {"beat.mid": b"MIDI-DATA", "lead.mp3": b"MP3-DATA"}
    for name, content in files.items():
        (workdir / name).write_bytes(content)


# ---------------------------------------------------------------------------
# Basic commit creation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_creates_postgres_row(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    repo_id = _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    commit_id = await _commit_async(
        message="boom bap demo take 1",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == commit_id)
    )
    row = result.scalar_one_or_none()
    assert row is not None, "commit row must exist after _commit_async"
    assert row.message == "boom bap demo take 1"
    assert row.repo_id == repo_id
    assert row.branch == "main"


@pytest.mark.anyio
async def test_commit_id_is_deterministic(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """commit_id is a 64-char sha256 hex string stored exactly once in DB.

    Pure determinism of ``compute_commit_id`` is covered by
    ``test_snapshot.py::test_commit_id_parametrized_deterministic``.
    Here we verify the integration contract: _commit_async returns a
    valid object ID and the row is findable by that ID.
    """
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"track.mid": b"CONSISTENT"})

    commit_id = await _commit_async(
        message="determinism check",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Valid sha256 hex digest
    assert len(commit_id) == 64
    assert all(c in "0123456789abcdef" for c in commit_id)

    # Stored in DB and findable by its own ID (no duplication)
    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == commit_id)
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].message == "determinism check"


@pytest.mark.anyio
async def test_commit_snapshot_content_addressed_same_files_same_id(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Same files → same snapshot_id on two successive commits."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"a.mid": b"CONSTANT"})

    cid1 = await _commit_async(
        message="first", root=tmp_path, session=muse_cli_db_session
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == cid1)
    )
    snap_id_1 = result.scalar_one().snapshot_id

    # Manually compute expected snapshot_id from the on-disk files
    manifest = build_snapshot_manifest(tmp_path / "muse-work")
    assert compute_snapshot_id(manifest) == snap_id_1


@pytest.mark.anyio
async def test_commit_snapshot_content_addressed_changed_file_new_id(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Changing a file produces a different snapshot_id."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"a.mid": b"VERSION1"})

    cid1 = await _commit_async(
        message="v1", root=tmp_path, session=muse_cli_db_session
    )
    r1 = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == cid1)
    )
    snap1 = r1.scalar_one().snapshot_id

    # Change file content and commit again
    (tmp_path / "muse-work" / "a.mid").write_bytes(b"VERSION2")
    cid2 = await _commit_async(
        message="v2", root=tmp_path, session=muse_cli_db_session
    )
    r2 = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == cid2)
    )
    snap2 = r2.scalar_one().snapshot_id

    assert snap1 != snap2


@pytest.mark.anyio
async def test_commit_moves_branch_head(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """After commit, .muse/refs/heads/main contains the new commit_id."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    commit_id = await _commit_async(
        message="update head", root=tmp_path, session=muse_cli_db_session
    )

    ref_content = (tmp_path / ".muse" / "refs" / "heads" / "main").read_text().strip()
    assert ref_content == commit_id


@pytest.mark.anyio
async def test_commit_sets_parent_pointer(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Second commit's parent_commit_id equals the first commit_id."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"V1"})

    cid1 = await _commit_async(
        message="first", root=tmp_path, session=muse_cli_db_session
    )

    # Change content so it's not a "nothing to commit" situation
    (tmp_path / "muse-work" / "beat.mid").write_bytes(b"V2")
    cid2 = await _commit_async(
        message="second", root=tmp_path, session=muse_cli_db_session
    )

    r2 = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == cid2)
    )
    row2 = r2.scalar_one()
    assert row2.parent_commit_id == cid1


@pytest.mark.anyio
async def test_commit_objects_are_deduplicated(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """The same file committed twice → exactly one object row in DB."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path, {"beat.mid": b"SHARED"})

    await _commit_async(message="c1", root=tmp_path, session=muse_cli_db_session)

    # Second workdir with same file content but different name → same object_id
    (tmp_path / "muse-work" / "copy.mid").write_bytes(b"SHARED")
    await _commit_async(message="c2", root=tmp_path, session=muse_cli_db_session)

    result = await muse_cli_db_session.execute(select(MuseCliObject))
    all_objects = result.scalars().all()
    object_ids = {o.object_id for o in all_objects}
    # Both files have identical bytes → same object_id → only 1 row for that content
    import hashlib
    shared_oid = hashlib.sha256(b"SHARED").hexdigest()
    assert shared_oid in object_ids
    # Ensure no duplicate rows for shared_oid
    shared_rows = [o for o in all_objects if o.object_id == shared_oid]
    assert len(shared_rows) == 1


# ---------------------------------------------------------------------------
# Nothing to commit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_nothing_to_commit_exits_zero(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Committing the same working tree twice exits 0 with the clean-tree message."""
    import typer

    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    await _commit_async(
        message="initial", root=tmp_path, session=muse_cli_db_session
    )

    # Second commit with unchanged tree should exit 0
    with pytest.raises(typer.Exit) as exc_info:
        await _commit_async(
            message="nothing changed", root=tmp_path, session=muse_cli_db_session
        )

    assert exc_info.value.exit_code == ExitCode.SUCCESS

    captured = capsys.readouterr()
    assert "Nothing to commit" in captured.out


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_outside_repo_exits_2(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """_commit_async never calls require_repo — that's the Typer callback's job.
    This test uses the Typer CLI runner to verify exit code 2 when there is
    no .muse/ directory.
    """
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["commit", "-m", "no repo"], catch_exceptions=False)
    assert result.exit_code == ExitCode.REPO_NOT_FOUND


@pytest.mark.anyio
async def test_commit_no_workdir_exits_1(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """When muse-work/ does not exist, commit exits with USER_ERROR (1)."""
    import typer

    _init_muse_repo(tmp_path)
    # Deliberately do NOT create muse-work/

    with pytest.raises(typer.Exit) as exc_info:
        await _commit_async(
            message="no workdir", root=tmp_path, session=muse_cli_db_session
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_commit_empty_workdir_exits_1(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """When muse-work/ exists but is empty, commit exits with USER_ERROR (1)."""
    import typer

    _init_muse_repo(tmp_path)
    (tmp_path / "muse-work").mkdir()  # empty directory

    with pytest.raises(typer.Exit) as exc_info:
        await _commit_async(
            message="empty", root=tmp_path, session=muse_cli_db_session
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# --from-batch fast path
# ---------------------------------------------------------------------------


def _write_muse_batch(
    batch_root: pathlib.Path,
    files: list[dict[str, object]],
    run_id: str = "stress-test",
    suggestion: str = "feat: jazz stress test",
) -> pathlib.Path:
    """Write a minimal muse-batch.json fixture and return its path."""
    data = {
        "run_id": run_id,
        "generated_at": "2026-02-27T17:29:19Z",
        "commit_message_suggestion": suggestion,
        "files": files,
        "provenance": {"prompt": "test", "model": "storpheus", "seed": run_id, "storpheus_version": "1.0"},
    }
    batch_path = batch_root / "muse-batch.json"
    batch_path.write_text(json.dumps(data, indent=2))
    return batch_path


@pytest.mark.anyio
async def test_commit_from_batch_uses_suggestion(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--from-batch uses commit_message_suggestion as the commit message."""
    _init_muse_repo(tmp_path)
    workdir = tmp_path / "muse-work" / "tracks" / "drums"
    workdir.mkdir(parents=True)
    mid_file = workdir / "jazz_4b_comp-0001.mid"
    mid_file.write_bytes(b"MIDI-DATA")

    batch_path = _write_muse_batch(
        tmp_path,
        files=[{
            "path": "muse-work/tracks/drums/jazz_4b_comp-0001.mid",
            "role": "midi",
            "genre": "jazz",
            "bars": 4,
            "cached": False,
        }],
        suggestion="feat: jazz stress test",
    )

    commit_id = await _commit_async(
        message="",  # overridden by batch suggestion
        root=tmp_path,
        session=muse_cli_db_session,
        batch_path=batch_path,
    )

    result = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == commit_id)
    )
    row = result.scalar_one()
    assert row.message == "feat: jazz stress test"


@pytest.mark.anyio
async def test_commit_from_batch_snapshots_listed_files_only(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--from-batch snapshots only files listed in files[], not the entire muse-work/."""
    _init_muse_repo(tmp_path)

    # Create two files in muse-work/
    listed_dir = tmp_path / "muse-work" / "tracks" / "drums"
    listed_dir.mkdir(parents=True)
    listed_file = listed_dir / "jazz_4b_comp-0001.mid"
    listed_file.write_bytes(b"LISTED-MIDI")

    unlisted_dir = tmp_path / "muse-work" / "renders"
    unlisted_dir.mkdir(parents=True)
    unlisted_file = unlisted_dir / "house_8b_comp-9999.mp3"
    unlisted_file.write_bytes(b"UNLISTED-MP3")

    # Batch only references the MIDI file
    batch_path = _write_muse_batch(
        tmp_path,
        files=[{
            "path": "muse-work/tracks/drums/jazz_4b_comp-0001.mid",
            "role": "midi",
            "genre": "jazz",
            "bars": 4,
            "cached": False,
        }],
        suggestion="feat: partial batch commit",
    )

    commit_id = await _commit_async(
        message="",
        root=tmp_path,
        session=muse_cli_db_session,
        batch_path=batch_path,
    )

    # Retrieve the snapshot manifest from DB
    from maestro.muse_cli.db import get_head_snapshot_id
    from maestro.muse_cli.models import MuseCliSnapshot
    from sqlalchemy.future import select as sa_select

    row = await muse_cli_db_session.execute(
        select(MuseCliCommit).where(MuseCliCommit.commit_id == commit_id)
    )
    commit_row = row.scalar_one()
    snap_row = await muse_cli_db_session.execute(
        sa_select(MuseCliSnapshot).where(
            MuseCliSnapshot.snapshot_id == commit_row.snapshot_id
        )
    )
    snapshot = snap_row.scalar_one()
    manifest: dict[str, str] = snapshot.manifest

    # Only the listed MIDI file should be in the snapshot
    assert any("jazz_4b_comp-0001.mid" in k for k in manifest.keys())
    assert not any("house_8b_comp-9999.mp3" in k for k in manifest.keys()), (
        "Unlisted files must NOT appear in the --from-batch snapshot"
    )


@pytest.mark.anyio
async def test_commit_from_batch_missing_batch_file_exits_1(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """When muse-batch.json does not exist, commit exits USER_ERROR."""
    import typer

    _init_muse_repo(tmp_path)
    nonexistent = tmp_path / "muse-batch.json"

    with pytest.raises(typer.Exit) as exc_info:
        await _commit_async(
            message="",
            root=tmp_path,
            session=muse_cli_db_session,
            batch_path=nonexistent,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


@pytest.mark.anyio
async def test_commit_from_batch_all_files_missing_exits_1(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """When no listed files exist on disk, commit exits USER_ERROR."""
    import typer

    _init_muse_repo(tmp_path)
    batch_path = _write_muse_batch(
        tmp_path,
        files=[{
            "path": "muse-work/tracks/drums/nonexistent.mid",
            "role": "midi",
            "genre": "jazz",
            "bars": 4,
            "cached": False,
        }],
    )

    with pytest.raises(typer.Exit) as exc_info:
        await _commit_async(
            message="",
            root=tmp_path,
            session=muse_cli_db_session,
            batch_path=batch_path,
        )
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


def test_load_muse_batch_invalid_json_exits_1(tmp_path: pathlib.Path) -> None:
    """load_muse_batch raises typer.Exit USER_ERROR on malformed JSON."""
    import typer

    bad_json = tmp_path / "muse-batch.json"
    bad_json.write_text("{ not valid json }")

    with pytest.raises(typer.Exit) as exc_info:
        load_muse_batch(bad_json)
    assert exc_info.value.exit_code == ExitCode.USER_ERROR


def test_build_snapshot_manifest_from_batch_skips_missing_files(
    tmp_path: pathlib.Path,
) -> None:
    """build_snapshot_manifest_from_batch silently skips files not on disk."""
    workdir = tmp_path / "muse-work" / "tracks"
    workdir.mkdir(parents=True)
    existing = workdir / "jazz_4b.mid"
    existing.write_bytes(b"MIDI")

    batch_data: dict[str, object] = {
        "files": [
            {"path": "muse-work/tracks/jazz_4b.mid", "role": "midi"},
            {"path": "muse-work/tracks/missing.mid", "role": "midi"},
        ]
    }

    manifest = build_snapshot_manifest_from_batch(batch_data, tmp_path)
    assert "tracks/jazz_4b.mid" in manifest
    assert "tracks/missing.mid" not in manifest


# ---------------------------------------------------------------------------
# --section / --track / --emotion music-domain metadata
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_section_stored_in_metadata(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--section value is stored in commit_metadata['section']."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    commit_id = await _commit_async(
        message="chorus bass take",
        root=tmp_path,
        session=muse_cli_db_session,
        section="chorus",
    )

    row = await muse_cli_db_session.get(MuseCliCommit, commit_id)
    assert row is not None
    assert row.commit_metadata is not None
    assert row.commit_metadata.get("section") == "chorus"


@pytest.mark.anyio
async def test_commit_track_stored_in_metadata(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--track value is stored in commit_metadata['track']."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    commit_id = await _commit_async(
        message="bass groove",
        root=tmp_path,
        session=muse_cli_db_session,
        track="bass",
    )

    row = await muse_cli_db_session.get(MuseCliCommit, commit_id)
    assert row is not None
    assert row.commit_metadata is not None
    assert row.commit_metadata.get("track") == "bass"


@pytest.mark.anyio
async def test_commit_emotion_stored_in_metadata(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--emotion value is stored in commit_metadata['emotion']."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    commit_id = await _commit_async(
        message="sad piano take",
        root=tmp_path,
        session=muse_cli_db_session,
        emotion="melancholic",
    )

    row = await muse_cli_db_session.get(MuseCliCommit, commit_id)
    assert row is not None
    assert row.commit_metadata is not None
    assert row.commit_metadata.get("emotion") == "melancholic"


@pytest.mark.anyio
async def test_commit_all_music_metadata_flags_together(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--section + --track + --emotion all land in commit_metadata simultaneously."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    commit_id = await _commit_async(
        message="full take",
        root=tmp_path,
        session=muse_cli_db_session,
        section="verse",
        track="keys",
        emotion="joyful",
    )

    row = await muse_cli_db_session.get(MuseCliCommit, commit_id)
    assert row is not None
    meta = row.commit_metadata
    assert meta is not None
    assert meta.get("section") == "verse"
    assert meta.get("track") == "keys"
    assert meta.get("emotion") == "joyful"


@pytest.mark.anyio
async def test_commit_no_music_flags_metadata_is_none(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """When no music flags are provided, commit_metadata is None (not an empty dict)."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    commit_id = await _commit_async(
        message="plain commit",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    row = await muse_cli_db_session.get(MuseCliCommit, commit_id)
    assert row is not None
    assert row.commit_metadata is None


# ---------------------------------------------------------------------------
# --co-author trailer
# ---------------------------------------------------------------------------


def test_append_co_author_adds_trailer() -> None:
    """_append_co_author appends a Co-authored-by trailer separated by a blank line."""
    result = _append_co_author("Initial commit", "Alice <alice@stori.app>")
    assert result == "Initial commit\n\nCo-authored-by: Alice <alice@stori.app>"


def test_append_co_author_empty_message() -> None:
    """_append_co_author with an empty base message produces just the trailer."""
    result = _append_co_author("", "Bob <bob@stori.app>")
    assert result == "Co-authored-by: Bob <bob@stori.app>"


@pytest.mark.anyio
async def test_commit_co_author_appended_to_message(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--co-author appends a Co-authored-by trailer to the stored commit message."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    commit_id = await _commit_async(
        message="collab jam session",
        root=tmp_path,
        session=muse_cli_db_session,
        co_author="Alice <alice@stori.app>",
    )

    row = await muse_cli_db_session.get(MuseCliCommit, commit_id)
    assert row is not None
    assert "Co-authored-by: Alice <alice@stori.app>" in row.message
    assert row.message.startswith("collab jam session")


# ---------------------------------------------------------------------------
# --allow-empty
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_commit_allow_empty_bypasses_clean_tree_guard(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--allow-empty allows committing the same snapshot twice."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    await _commit_async(
        message="first commit",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Second commit with identical tree — would normally exit with "Nothing to commit"
    commit_id2 = await _commit_async(
        message="milestone marker",
        root=tmp_path,
        session=muse_cli_db_session,
        allow_empty=True,
    )

    row = await muse_cli_db_session.get(MuseCliCommit, commit_id2)
    assert row is not None
    assert row.message == "milestone marker"


@pytest.mark.anyio
async def test_commit_allow_empty_with_emotion_metadata(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """--allow-empty + --emotion enables metadata-only milestone commits."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    await _commit_async(
        message="initial session",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    commit_id = await _commit_async(
        message="emotional annotation",
        root=tmp_path,
        session=muse_cli_db_session,
        allow_empty=True,
        emotion="tense",
    )

    row = await muse_cli_db_session.get(MuseCliCommit, commit_id)
    assert row is not None
    assert row.commit_metadata is not None
    assert row.commit_metadata.get("emotion") == "tense"


@pytest.mark.anyio
async def test_commit_without_allow_empty_still_exits_on_clean_tree(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Without --allow-empty the nothing-to-commit guard still fires."""
    import typer

    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    await _commit_async(
        message="initial", root=tmp_path, session=muse_cli_db_session
    )

    with pytest.raises(typer.Exit) as exc_info:
        await _commit_async(
            message="duplicate",
            root=tmp_path,
            session=muse_cli_db_session,
            allow_empty=False,
        )
    assert exc_info.value.exit_code == ExitCode.SUCCESS


# ---------------------------------------------------------------------------
# _apply_commit_music_metadata helper
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_apply_commit_music_metadata_updates_existing_commit(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """_apply_commit_music_metadata merges keys without overwriting unrelated ones."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    commit_id = await _commit_async(
        message="base commit",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    # Simulate tempo already set by muse tempo --set
    row = await muse_cli_db_session.get(MuseCliCommit, commit_id)
    assert row is not None
    from sqlalchemy.orm.attributes import flag_modified

    row.commit_metadata = {"tempo_bpm": 120.0}
    flag_modified(row, "commit_metadata")
    muse_cli_db_session.add(row)
    await muse_cli_db_session.flush()

    await _apply_commit_music_metadata(
        session=muse_cli_db_session,
        commit_id=commit_id,
        section="bridge",
        track=None,
        emotion="melancholic",
    )
    await muse_cli_db_session.flush()

    updated = await muse_cli_db_session.get(MuseCliCommit, commit_id)
    assert updated is not None
    meta = updated.commit_metadata
    assert meta is not None
    assert meta.get("tempo_bpm") == 120.0      # preserved
    assert meta.get("section") == "bridge"     # added
    assert meta.get("emotion") == "melancholic"  # added
    assert "track" not in meta                 # not supplied → absent


@pytest.mark.anyio
async def test_apply_commit_music_metadata_noop_when_no_keys(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """_apply_commit_music_metadata is a no-op when all args are None."""
    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    commit_id = await _commit_async(
        message="plain commit",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    await _apply_commit_music_metadata(
        session=muse_cli_db_session,
        commit_id=commit_id,
        section=None,
        track=None,
        emotion=None,
    )

    row = await muse_cli_db_session.get(MuseCliCommit, commit_id)
    assert row is not None
    assert row.commit_metadata is None  # untouched


# ---------------------------------------------------------------------------
# muse show reflects music metadata
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_show_reflects_music_metadata(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """ShowCommitResult exposes section/track/emotion from commit_metadata."""
    from maestro.muse_cli.commands.show import _show_async

    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    await _commit_async(
        message="rich take",
        root=tmp_path,
        session=muse_cli_db_session,
        section="chorus",
        track="drums",
        emotion="joyful",
    )

    result = await _show_async(
        session=muse_cli_db_session,
        muse_dir=tmp_path / ".muse",
        ref="HEAD",
    )

    assert result["section"] == "chorus"
    assert result["track"] == "drums"
    assert result["emotion"] == "joyful"


@pytest.mark.anyio
async def test_show_music_metadata_absent_when_not_set(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """ShowCommitResult returns None for music fields when commit has no metadata."""
    from maestro.muse_cli.commands.show import _show_async

    _init_muse_repo(tmp_path)
    _populate_workdir(tmp_path)

    await _commit_async(
        message="plain commit",
        root=tmp_path,
        session=muse_cli_db_session,
    )

    result = await _show_async(
        session=muse_cli_db_session,
        muse_dir=tmp_path / ".muse",
        ref="HEAD",
    )

    assert result["section"] is None
    assert result["track"] is None
    assert result["emotion"] is None
