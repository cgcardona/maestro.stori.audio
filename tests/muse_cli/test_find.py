"""Tests for ``muse find`` — search commit history by musical properties.

All async tests call ``_find_async`` directly with an in-memory SQLite
session and a ``tmp_path`` repo root — no real Postgres or running
process required.  Commits are seeded via ``_commit_async`` so the two
commands are tested as an integrated pair.

Naming convention: test_muse_find_<behavior>_<scenario>
"""
from __future__ import annotations

import json
import pathlib
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.find import _find_async
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_find import (
    MuseFindQuery,
    MuseFindResults,
    _matches_property,
    _parse_property_filter,
    search_commits,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
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
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    for name, content in files.items():
        (workdir / name).write_bytes(content)


async def _make_commits(
    root: pathlib.Path,
    session: AsyncSession,
    messages: list[str],
    file_seed: int = 0,
) -> list[str]:
    """Create N commits with unique file content."""
    commit_ids: list[str] = []
    for i, msg in enumerate(messages):
        _write_workdir(root, {f"track_{file_seed + i}.mid": f"MIDI-{file_seed + i}".encode()})
        cid = await _commit_async(message=msg, root=root, session=session)
        commit_ids.append(cid)
    return commit_ids


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


def test_muse_find_parse_range_filter_returns_triple() -> None:
    """``tempo=120-130`` parses to (tempo, 120.0, 130.0)."""
    result = _parse_property_filter("tempo=120-130")
    assert result == ("tempo", 120.0, 130.0)


def test_muse_find_parse_range_filter_returns_none_for_text() -> None:
    """``key=Eb`` is not a range and returns None."""
    assert _parse_property_filter("key=Eb") is None


def test_muse_find_parse_range_filter_returns_none_for_no_equals() -> None:
    """A string without ``=`` is not a range."""
    assert _parse_property_filter("melancholic") is None


def test_muse_find_matches_property_plain_text_case_insensitive() -> None:
    """Plain text filter matches case-insensitively."""
    assert _matches_property("key=Eb major, mode=major", "key=Eb") is True
    assert _matches_property("key=Eb major, mode=major", "KEY=EB") is True
    assert _matches_property("key=Eb major, mode=major", "mode=minor") is False


def test_muse_find_matches_property_range_in_bounds() -> None:
    """Range filter matches when message tempo is within range."""
    assert _matches_property("tempo=125 bpm, swing=0.6", "tempo=120-130") is True


def test_muse_find_matches_property_range_out_of_bounds() -> None:
    """Range filter rejects message tempo outside the range."""
    assert _matches_property("tempo=115 bpm", "tempo=120-130") is False


def test_muse_find_matches_property_range_at_boundary() -> None:
    """Range filter matches when tempo equals boundary value exactly."""
    assert _matches_property("tempo=120", "tempo=120-130") is True
    assert _matches_property("tempo=130", "tempo=120-130") is True


def test_muse_find_matches_property_missing_key() -> None:
    """Range filter returns False when key is absent from the message."""
    assert _matches_property("mode=minor, has=bridge", "tempo=120-130") is False


# ---------------------------------------------------------------------------
# Regression: test_muse_find_harmony_key_returns_matching_commits
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_find_harmony_key_returns_matching_commits(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--harmony 'key=F minor'`` lists all commits where the key was F minor."""
    _init_muse_repo(tmp_path)
    await _make_commits(
        tmp_path,
        muse_cli_db_session,
        [
            "ambient sketch, key=F minor, tempo=90 bpm",
            "jazz take, key=Eb major, tempo=140 bpm",
            "brooding outro, key=F minor, tempo=72 bpm",
        ],
    )

    query = MuseFindQuery(harmony="key=F minor", limit=20)
    results = await search_commits(muse_cli_db_session, _get_repo_id(tmp_path), query)

    assert results.total_scanned == 2  # ILIKE applied at SQL level
    assert len(results.matches) == 2
    for match in results.matches:
        assert "key=F minor" in match.message


def _get_repo_id(root: pathlib.Path) -> str:
    data: dict[str, str] = json.loads((root / ".muse" / "repo.json").read_text())
    return data["repo_id"]


# ---------------------------------------------------------------------------
# test_muse_find_rhythm_tempo_range_filter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_find_rhythm_tempo_range_filter(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--rhythm 'tempo=120-130'`` finds only commits with tempo in range."""
    _init_muse_repo(tmp_path)
    await _make_commits(
        tmp_path,
        muse_cli_db_session,
        [
            "slow groove, tempo=95 bpm",
            "medium vibe, tempo=125 bpm",
            "fast run, tempo=160 bpm",
            "another mid, tempo=120 bpm",
        ],
    )

    query = MuseFindQuery(rhythm="tempo=120-130", limit=20)
    results = await search_commits(muse_cli_db_session, _get_repo_id(tmp_path), query)

    assert len(results.matches) == 2
    messages = {m.message for m in results.matches}
    assert "medium vibe, tempo=125 bpm" in messages
    assert "another mid, tempo=120 bpm" in messages


# ---------------------------------------------------------------------------
# test_muse_find_multiple_flags_combine_with_and_logic
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_find_multiple_flags_combine_with_and_logic(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Multiple filter flags combine with AND — commit must satisfy all."""
    _init_muse_repo(tmp_path)
    await _make_commits(
        tmp_path,
        muse_cli_db_session,
        [
            "melancholic bridge, key=F minor, has=bridge",
            "melancholic verse, key=F minor",        # no bridge
            "bright bridge, key=C major, has=bridge",  # wrong key
        ],
    )

    query = MuseFindQuery(emotion="melancholic", structure="has=bridge", limit=20)
    results = await search_commits(muse_cli_db_session, _get_repo_id(tmp_path), query)

    assert len(results.matches) == 1
    assert "melancholic bridge" in results.matches[0].message


# ---------------------------------------------------------------------------
# test_muse_find_json_output
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_find_json_output(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--json`` output is valid JSON with correct commit_id fields."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(
        tmp_path,
        muse_cli_db_session,
        ["jazz chord, key=Cm7"],
    )

    capsys.readouterr()
    query = MuseFindQuery(harmony="key=Cm7", limit=20)
    await _find_async(
        root=tmp_path,
        session=muse_cli_db_session,
        query=query,
        output_json=True,
    )

    captured = capsys.readouterr().out
    payload = json.loads(captured)

    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["commit_id"] == cids[0]
    assert payload[0]["message"] == "jazz chord, key=Cm7"


# ---------------------------------------------------------------------------
# test_muse_find_since_until_date_filter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_find_since_until_date_filter(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--since`` / ``--until`` restrict results to the given date window."""
    from maestro.muse_cli.models import MuseCliCommit
    from maestro.muse_cli.snapshot import compute_commit_id, compute_snapshot_id
    from maestro.muse_cli.db import upsert_object, upsert_snapshot, insert_commit

    _init_muse_repo(tmp_path)
    repo_id = _get_repo_id(tmp_path)

    # Manually insert commits with specific timestamps
    old_ts = datetime(2025, 6, 1, tzinfo=timezone.utc)
    new_ts = datetime(2026, 2, 1, tzinfo=timezone.utc)

    for i, (msg, ts) in enumerate([("old take", old_ts), ("new take", new_ts)]):
        manifest = {f"track_{i}.mid": f"abcdef{i:02x}" * 4}
        snapshot_id = compute_snapshot_id(manifest)
        await upsert_object(muse_cli_db_session, object_id=f"abcdef{i:02x}" * 4, size_bytes=8)
        await upsert_snapshot(muse_cli_db_session, manifest=manifest, snapshot_id=snapshot_id)
        await muse_cli_db_session.flush()
        commit_id = compute_commit_id(
            parent_ids=[],
            snapshot_id=snapshot_id,
            message=msg,
            committed_at_iso=ts.isoformat(),
        )
        commit = MuseCliCommit(
            commit_id=commit_id,
            repo_id=repo_id,
            branch="main",
            parent_commit_id=None,
            snapshot_id=snapshot_id,
            message=msg,
            author="",
            committed_at=ts,
        )
        await insert_commit(muse_cli_db_session, commit)

    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    query = MuseFindQuery(since=cutoff, limit=20)
    results = await search_commits(muse_cli_db_session, repo_id, query)

    assert len(results.matches) == 1
    assert results.matches[0].message == "new take"


# ---------------------------------------------------------------------------
# test_muse_find_no_matches_returns_empty
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_find_no_matches_returns_empty(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """No-match query returns empty results without error."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["take 1", "take 2"])

    query = MuseFindQuery(emotion="epic", limit=20)
    results = await search_commits(muse_cli_db_session, _get_repo_id(tmp_path), query)

    assert len(results.matches) == 0
    assert results.total_scanned == 0  # SQL ILIKE filters row out entirely


# ---------------------------------------------------------------------------
# test_muse_find_limit_caps_results
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_find_limit_caps_results(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--limit N`` caps the result set even when more matches exist."""
    _init_muse_repo(tmp_path)
    await _make_commits(
        tmp_path,
        muse_cli_db_session,
        [f"minor key take {i}, key=F minor" for i in range(5)],
    )

    query = MuseFindQuery(harmony="key=F minor", limit=3)
    results = await search_commits(muse_cli_db_session, _get_repo_id(tmp_path), query)

    assert len(results.matches) == 3
    assert results.total_scanned == 5


# ---------------------------------------------------------------------------
# test_muse_find_results_are_newest_first
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_muse_find_results_are_newest_first(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Results are ordered newest-first."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(
        tmp_path,
        muse_cli_db_session,
        [
            "first minor, key=F minor",
            "second minor, key=F minor",
            "third minor, key=F minor",
        ],
    )

    query = MuseFindQuery(harmony="key=F minor", limit=20)
    results = await search_commits(muse_cli_db_session, _get_repo_id(tmp_path), query)

    # Newest (cids[2]) should be first
    assert results.matches[0].commit_id == cids[2]
    assert results.matches[-1].commit_id == cids[0]


# ---------------------------------------------------------------------------
# test_muse_find_no_filters_exits_user_error (CLI skeleton)
# ---------------------------------------------------------------------------


def test_muse_find_no_filters_exits_user_error(tmp_path: pathlib.Path) -> None:
    """``muse find`` with no flags exits with USER_ERROR."""
    import os
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    muse = tmp_path / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": "test", "schema_version": "1"}))
    (muse / "HEAD").write_text("refs/heads/main")

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["find"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == ExitCode.USER_ERROR
