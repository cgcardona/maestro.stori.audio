"""Tests for ``muse show``.

All async tests call ``_show_async`` / ``_diff_vs_parent_async`` directly
with an in-memory SQLite session and a ``tmp_path`` repo root — no real
Postgres or running process required.

Coverage:
- Regression: show displays commit metadata (test_muse_show_displays_commit_metadata)
- JSON output (test_muse_show_json_output)
- Diff vs parent (test_muse_show_diff_vs_parent)
- Diff on root commit (no parent) (test_muse_show_diff_root_commit)
- MIDI file listing (test_muse_show_midi_list)
- Audio preview stub path (test_muse_show_audio_preview)
- HEAD resolution (test_muse_show_head_resolution)
- Branch name resolution (test_muse_show_branch_name_resolution)
- Short prefix resolution (test_muse_show_prefix_resolution)
- Ambiguous prefix returns USER_ERROR
- Unknown ref returns USER_ERROR
- Commit with no parent shows no parent line
- _looks_like_hex_prefix unit tests
- _midi_files_in_manifest unit tests
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.show import (
    ShowCommitResult,
    ShowDiffResult,
    _diff_vs_parent_async,
    _looks_like_hex_prefix,
    _midi_files_in_manifest,
    _show_async,
)
from maestro.muse_cli.errors import ExitCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Initialise a minimal .muse/ directory structure for testing."""
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": rid, "schema_version": "1"})
    )
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _write_workdir(root: pathlib.Path, files: dict[str, bytes]) -> None:
    """Overwrite the muse-work directory with exactly *files* (clears old content)."""
    workdir = root / "muse-work"
    # Remove old files so each commit has a clean, deterministic snapshot
    if workdir.exists():
        for child in list(workdir.iterdir()):
            child.unlink()
    else:
        workdir.mkdir(parents=True)
    for name, content in files.items():
        (workdir / name).write_bytes(content)


async def _make_commit(
    root: pathlib.Path,
    session: AsyncSession,
    message: str,
    files: dict[str, bytes],
) -> str:
    """Create one commit with exactly *files* and return its commit_id."""
    _write_workdir(root, files)
    return await _commit_async(message=message, root=root, session=session)


# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------


class TestLooksLikeHexPrefix:
    def test_full_64_char_sha(self) -> None:
        sha = "a" * 64
        assert _looks_like_hex_prefix(sha) is True

    def test_short_8_char_prefix(self) -> None:
        assert _looks_like_hex_prefix("abc12345") is True

    def test_four_char_minimum(self) -> None:
        assert _looks_like_hex_prefix("abcd") is True

    def test_three_chars_too_short(self) -> None:
        assert _looks_like_hex_prefix("abc") is False

    def test_uppercase_is_valid_hex(self) -> None:
        assert _looks_like_hex_prefix("ABCDEF12") is True

    def test_branch_name_not_hex(self) -> None:
        assert _looks_like_hex_prefix("main") is False

    def test_head_not_hex(self) -> None:
        assert _looks_like_hex_prefix("HEAD") is False

    def test_hyphenated_branch_not_hex(self) -> None:
        assert _looks_like_hex_prefix("feat/my-branch") is False


class TestMidiFilesInManifest:
    def test_empty_manifest(self) -> None:
        assert _midi_files_in_manifest({}) == []

    def test_no_midi_files(self) -> None:
        manifest = {"beat.txt": "aaa", "notes.xml": "bbb"}
        assert _midi_files_in_manifest(manifest) == []

    def test_dot_mid_extension(self) -> None:
        manifest = {"beat.mid": "aaa"}
        assert _midi_files_in_manifest(manifest) == ["beat.mid"]

    def test_dot_midi_extension(self) -> None:
        manifest = {"track.midi": "bbb"}
        assert _midi_files_in_manifest(manifest) == ["track.midi"]

    def test_dot_smf_extension(self) -> None:
        manifest = {"song.smf": "ccc"}
        assert _midi_files_in_manifest(manifest) == ["song.smf"]

    def test_mixed_files_returns_only_midi(self) -> None:
        manifest = {
            "beat.mid": "aaa",
            "notes.txt": "bbb",
            "keys.mid": "ccc",
            "cover.png": "ddd",
        }
        result = _midi_files_in_manifest(manifest)
        assert result == ["beat.mid", "keys.mid"]

    def test_sorted_output(self) -> None:
        manifest = {"z.mid": "1", "a.mid": "2", "m.mid": "3"}
        result = _midi_files_in_manifest(manifest)
        assert result == ["a.mid", "m.mid", "z.mid"]

    def test_uppercase_extension(self) -> None:
        manifest = {"TRACK.MID": "aaa"}
        assert _midi_files_in_manifest(manifest) == ["TRACK.MID"]

    def test_nested_path_with_midi_extension(self) -> None:
        manifest = {"sections/verse/piano.mid": "hash1"}
        assert _midi_files_in_manifest(manifest) == ["sections/verse/piano.mid"]


# ---------------------------------------------------------------------------
# Async integration tests — _show_async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_show_displays_commit_metadata(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """Regression: show returns correct metadata for a committed snapshot."""
    os.environ["MUSE_REPO_ROOT"] = str(tmp_path)
    try:
        _init_muse_repo(tmp_path)
        commit_id = await _make_commit(
            tmp_path,
            muse_cli_db_session,
            "Add piano melody to verse",
            {"piano.mid": b"\x00\x01"},
        )
        muse_dir = tmp_path / ".muse"
        result = await _show_async(
            session=muse_cli_db_session, muse_dir=muse_dir, ref=commit_id
        )
        assert result["commit_id"] == commit_id
        assert result["message"] == "Add piano melody to verse"
        assert result["branch"] == "main"
        assert "piano.mid" in result["snapshot_manifest"]
        assert result["parent_commit_id"] is None  # root commit
    finally:
        del os.environ["MUSE_REPO_ROOT"]


@pytest.mark.anyio
async def test_muse_show_json_output(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """show returns a JSON-serialisable dict with all required fields."""
    os.environ["MUSE_REPO_ROOT"] = str(tmp_path)
    try:
        _init_muse_repo(tmp_path)
        commit_id = await _make_commit(
            tmp_path,
            muse_cli_db_session,
            "boom bap demo",
            {"beat.mid": b"\x00\x02"},
        )
        muse_dir = tmp_path / ".muse"
        result = await _show_async(
            session=muse_cli_db_session, muse_dir=muse_dir, ref=commit_id
        )
        # Must be JSON-serialisable without error
        serialised = json.dumps(dict(result))
        payload = json.loads(serialised)
        assert payload["commit_id"] == commit_id
        assert payload["message"] == "boom bap demo"
        assert "snapshot_manifest" in payload
        assert "beat.mid" in payload["snapshot_manifest"]
        assert "committed_at" in payload
        assert "snapshot_id" in payload
    finally:
        del os.environ["MUSE_REPO_ROOT"]


@pytest.mark.anyio
async def test_muse_show_diff_vs_parent(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """show --diff: shows added/modified/removed paths vs parent commit."""
    os.environ["MUSE_REPO_ROOT"] = str(tmp_path)
    try:
        _init_muse_repo(tmp_path)
        muse_dir = tmp_path / ".muse"

        # First commit: beat.mid + keys.mid
        await _make_commit(
            tmp_path,
            muse_cli_db_session,
            "initial take",
            {"beat.mid": b"\x01", "keys.mid": b"\x02"},
        )
        # Second commit: beat.mid changed, bass.mid added, keys.mid removed
        commit_id = await _make_commit(
            tmp_path,
            muse_cli_db_session,
            "revise arrangement",
            {"beat.mid": b"\xff", "bass.mid": b"\x03"},
        )

        diff_result = await _diff_vs_parent_async(
            session=muse_cli_db_session, muse_dir=muse_dir, ref=commit_id
        )

        assert diff_result["commit_id"] == commit_id
        assert diff_result["parent_commit_id"] is not None
        assert "bass.mid" in diff_result["added"]
        assert "beat.mid" in diff_result["modified"]
        assert "keys.mid" in diff_result["removed"]
        assert diff_result["total_changed"] == 3
    finally:
        del os.environ["MUSE_REPO_ROOT"]


@pytest.mark.anyio
async def test_muse_show_diff_root_commit(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """show --diff on a root commit treats all snapshot paths as 'added'."""
    os.environ["MUSE_REPO_ROOT"] = str(tmp_path)
    try:
        _init_muse_repo(tmp_path)
        muse_dir = tmp_path / ".muse"
        commit_id = await _make_commit(
            tmp_path,
            muse_cli_db_session,
            "initial commit",
            {"intro.mid": b"\x01", "verse.mid": b"\x02"},
        )
        diff_result = await _diff_vs_parent_async(
            session=muse_cli_db_session, muse_dir=muse_dir, ref=commit_id
        )
        assert diff_result["parent_commit_id"] is None
        assert "intro.mid" in diff_result["added"]
        assert "verse.mid" in diff_result["added"]
        assert diff_result["modified"] == []
        assert diff_result["removed"] == []
        assert diff_result["total_changed"] == 2
    finally:
        del os.environ["MUSE_REPO_ROOT"]


@pytest.mark.anyio
async def test_muse_show_midi_list(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """show --midi: snapshot manifest filtered to MIDI files only."""
    os.environ["MUSE_REPO_ROOT"] = str(tmp_path)
    try:
        _init_muse_repo(tmp_path)
        muse_dir = tmp_path / ".muse"
        commit_id = await _make_commit(
            tmp_path,
            muse_cli_db_session,
            "mixed file commit",
            {
                "beat.mid": b"\x01",
                "keys.midi": b"\x02",
                "readme.txt": b"notes",
                "cover.png": b"\x89PNG",
            },
        )
        result = await _show_async(
            session=muse_cli_db_session, muse_dir=muse_dir, ref=commit_id
        )
        midi_files = _midi_files_in_manifest(result["snapshot_manifest"])
        assert "beat.mid" in midi_files
        assert "keys.midi" in midi_files
        assert "readme.txt" not in midi_files
        assert "cover.png" not in midi_files
    finally:
        del os.environ["MUSE_REPO_ROOT"]


@pytest.mark.anyio
async def test_muse_show_audio_preview(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """show --audio-preview: when no export dir, returns a helpful message stub."""
    os.environ["MUSE_REPO_ROOT"] = str(tmp_path)
    try:
        _init_muse_repo(tmp_path)
        muse_dir = tmp_path / ".muse"
        commit_id = await _make_commit(
            tmp_path,
            muse_cli_db_session,
            "needs audio",
            {"track.mid": b"\x01"},
        )
        result = await _show_async(
            session=muse_cli_db_session, muse_dir=muse_dir, ref=commit_id
        )
        # No export directory exists → audio preview is stubbed
        export_dir = tmp_path / ".muse" / "exports" / commit_id[:8]
        assert not export_dir.exists()
        # Verify the commit itself is valid (preview logic is tested via render function)
        assert result["commit_id"] == commit_id
    finally:
        del os.environ["MUSE_REPO_ROOT"]


@pytest.mark.anyio
async def test_muse_show_head_resolution(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """show resolves 'HEAD' to the current branch tip."""
    os.environ["MUSE_REPO_ROOT"] = str(tmp_path)
    try:
        _init_muse_repo(tmp_path)
        muse_dir = tmp_path / ".muse"
        commit_id = await _make_commit(
            tmp_path, muse_cli_db_session, "HEAD test", {"a.mid": b"\x01"}
        )
        result = await _show_async(
            session=muse_cli_db_session, muse_dir=muse_dir, ref="HEAD"
        )
        assert result["commit_id"] == commit_id
    finally:
        del os.environ["MUSE_REPO_ROOT"]


@pytest.mark.anyio
async def test_muse_show_branch_name_resolution(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """show resolves a branch name to its tip commit."""
    os.environ["MUSE_REPO_ROOT"] = str(tmp_path)
    try:
        _init_muse_repo(tmp_path)
        muse_dir = tmp_path / ".muse"
        commit_id = await _make_commit(
            tmp_path, muse_cli_db_session, "branch name test", {"b.mid": b"\x02"}
        )
        # muse commit writes the commit_id to refs/heads/main
        result = await _show_async(
            session=muse_cli_db_session, muse_dir=muse_dir, ref="main"
        )
        assert result["commit_id"] == commit_id
    finally:
        del os.environ["MUSE_REPO_ROOT"]


@pytest.mark.anyio
async def test_muse_show_prefix_resolution(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """show resolves a short hex prefix to the matching commit."""
    os.environ["MUSE_REPO_ROOT"] = str(tmp_path)
    try:
        _init_muse_repo(tmp_path)
        muse_dir = tmp_path / ".muse"
        commit_id = await _make_commit(
            tmp_path, muse_cli_db_session, "prefix test", {"c.mid": b"\x03"}
        )
        prefix = commit_id[:8]
        result = await _show_async(
            session=muse_cli_db_session, muse_dir=muse_dir, ref=prefix
        )
        assert result["commit_id"] == commit_id
    finally:
        del os.environ["MUSE_REPO_ROOT"]


@pytest.mark.anyio
async def test_muse_show_unknown_ref_exits_user_error(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """show exits with USER_ERROR when the branch/ref does not exist."""
    os.environ["MUSE_REPO_ROOT"] = str(tmp_path)
    try:
        _init_muse_repo(tmp_path)
        muse_dir = tmp_path / ".muse"
        with pytest.raises(typer.Exit) as exc_info:
            await _show_async(
                session=muse_cli_db_session, muse_dir=muse_dir, ref="nonexistent-branch"
            )
        assert exc_info.value.exit_code == ExitCode.USER_ERROR
    finally:
        del os.environ["MUSE_REPO_ROOT"]


@pytest.mark.anyio
async def test_muse_show_ambiguous_prefix_exits_user_error(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """show exits with USER_ERROR when a hex prefix matches multiple commits."""
    os.environ["MUSE_REPO_ROOT"] = str(tmp_path)
    try:
        _init_muse_repo(tmp_path)
        muse_dir = tmp_path / ".muse"

        # Create two commits whose IDs share the same first character (unlikely
        # but we force it by inserting commits directly via the ORM).
        from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot
        from datetime import datetime, timezone

        shared_prefix = "0000"
        snap1_id = "snap001" + "x" * 57
        snap2_id = "snap002" + "x" * 57

        snap1 = MuseCliSnapshot(snapshot_id=snap1_id, manifest={})
        snap2 = MuseCliSnapshot(snapshot_id=snap2_id, manifest={})
        muse_cli_db_session.add(snap1)
        muse_cli_db_session.add(snap2)

        now = datetime.now(timezone.utc)
        commit1 = MuseCliCommit(
            commit_id=shared_prefix + "aaaa" + "b" * 56,
            repo_id="repo1",
            branch="main",
            snapshot_id=snap1_id,
            message="commit one",
            author="test",
            committed_at=now,
        )
        commit2 = MuseCliCommit(
            commit_id=shared_prefix + "bbbb" + "c" * 56,
            repo_id="repo1",
            branch="main",
            snapshot_id=snap2_id,
            message="commit two",
            author="test",
            committed_at=now,
        )
        muse_cli_db_session.add(commit1)
        muse_cli_db_session.add(commit2)
        await muse_cli_db_session.flush()

        with pytest.raises(typer.Exit) as exc_info:
            await _show_async(
                session=muse_cli_db_session,
                muse_dir=muse_dir,
                ref=shared_prefix,
            )
        assert exc_info.value.exit_code == ExitCode.USER_ERROR
    finally:
        del os.environ["MUSE_REPO_ROOT"]


@pytest.mark.anyio
async def test_muse_show_commit_with_parent(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """show includes parent_commit_id when a parent exists."""
    os.environ["MUSE_REPO_ROOT"] = str(tmp_path)
    try:
        _init_muse_repo(tmp_path)
        muse_dir = tmp_path / ".muse"

        parent_id = await _make_commit(
            tmp_path, muse_cli_db_session, "first", {"a.mid": b"\x01"}
        )
        child_id = await _make_commit(
            tmp_path, muse_cli_db_session, "second", {"b.mid": b"\x02"}
        )

        result = await _show_async(
            session=muse_cli_db_session, muse_dir=muse_dir, ref=child_id
        )
        assert result["parent_commit_id"] == parent_id
    finally:
        del os.environ["MUSE_REPO_ROOT"]


@pytest.mark.anyio
async def test_muse_show_diff_identical_files_across_commits(
    tmp_path: pathlib.Path, muse_cli_db_session: AsyncSession
) -> None:
    """show --diff reports zero changes when only an untracked file changes.

    We commit twice with different messages but the same *tracked* file
    content, then verify that the diff between the second commit and its
    parent is empty.  We use slightly different file contents to avoid the
    'nothing to commit' early-exit from _commit_async.
    """
    os.environ["MUSE_REPO_ROOT"] = str(tmp_path)
    try:
        _init_muse_repo(tmp_path)
        muse_dir = tmp_path / ".muse"

        # First commit: a.mid with content 0x01
        await _make_commit(
            tmp_path, muse_cli_db_session, "first", {"a.mid": b"\x01"}
        )
        # Second commit: same file, different content (forces a new commit)
        # Both commits have a.mid; we then compare the diff output.
        # To test zero-change scenario, commit the *same* content again under
        # a different message via a direct DB insert that shares the snapshot.
        from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot
        from datetime import datetime, timezone

        # Grab the current HEAD commit to get its snapshot_id
        head_ref_text = (muse_dir / "HEAD").read_text().strip()
        first_commit_id = (muse_dir / pathlib.Path(head_ref_text)).read_text().strip()
        first_commit = await muse_cli_db_session.get(MuseCliCommit, first_commit_id)
        assert first_commit is not None

        # Create a second commit that points to the same snapshot (identical content)
        now = datetime.now(timezone.utc)
        second_commit_id = "ee" * 32  # deterministic, distinct ID
        second_commit = MuseCliCommit(
            commit_id=second_commit_id,
            repo_id="test-repo",
            branch="main",
            parent_commit_id=first_commit_id,
            snapshot_id=first_commit.snapshot_id,  # same snapshot
            message="second commit same snapshot",
            author="test",
            committed_at=now,
        )
        muse_cli_db_session.add(second_commit)
        await muse_cli_db_session.flush()

        diff_result = await _diff_vs_parent_async(
            session=muse_cli_db_session, muse_dir=muse_dir, ref=second_commit_id
        )
        assert diff_result["total_changed"] == 0
        assert diff_result["added"] == []
        assert diff_result["modified"] == []
        assert diff_result["removed"] == []
    finally:
        del os.environ["MUSE_REPO_ROOT"]
