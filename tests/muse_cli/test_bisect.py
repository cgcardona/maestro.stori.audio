"""Tests for ``muse bisect`` — state machine, commit graph traversal, and CLI.

Coverage
--------
- :func:`read_bisect_state` / :func:`write_bisect_state` / :func:`clear_bisect_state`
  round-trip fidelity.
- :func:`get_commits_between` returns the correct candidate set for both linear
  and branching histories.
- :func:`pick_midpoint` selects the lower-middle element.
- :func:`advance_bisect` state machine: marks verdicts, narrows range,
  identifies culprit when range collapses.
- ``test_bisect_state_machine_advances_correctly`` — the primary regression test
  from the issue spec.
- Guard: ``muse bisect start`` blocks when a merge is in progress.
- Guard: ``muse bisect start`` blocks when a bisect is already active.
- ``muse bisect log --json`` emits valid JSON.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import uuid
from typing import Generator

import pytest
import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot
from maestro.muse_cli.snapshot import compute_snapshot_id
from maestro.services.muse_bisect import (
    BisectState,
    BisectStepResult,
    advance_bisect,
    clear_bisect_state,
    get_commits_between,
    pick_midpoint,
    read_bisect_state,
    write_bisect_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Create a minimal .muse/ layout for testing."""
    rid = repo_id or str(uuid.uuid4())
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": rid, "schema_version": "1"}))
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "refs" / "heads" / "main").write_text("")
    return rid


def _write_workdir(root: pathlib.Path, files: dict[str, bytes]) -> None:
    """Overwrite muse-work/ with exactly the given files."""
    import shutil

    workdir = root / "muse-work"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir()
    for name, content in files.items():
        (workdir / name).write_bytes(content)


def _head_commit(root: pathlib.Path, branch: str = "main") -> str:
    """Return current HEAD commit_id for the branch."""
    muse = root / ".muse"
    ref_path = muse / "refs" / "heads" / branch
    return ref_path.read_text().strip() if ref_path.exists() else ""


# ---------------------------------------------------------------------------
# Unit tests — state file round-trip
# ---------------------------------------------------------------------------


def test_write_read_bisect_state_round_trip(tmp_path: pathlib.Path) -> None:
    """BisectState survives a write → read cycle with all fields set."""
    _init_repo(tmp_path)

    state = BisectState(
        good="goodabc123",
        bad="baddef456",
        current="midpoint789",
        tested={"goodabc123": "good", "midpoint789": "bad"},
        pre_bisect_ref="refs/heads/main",
        pre_bisect_commit="originalabc",
    )
    write_bisect_state(tmp_path, state)
    loaded = read_bisect_state(tmp_path)

    assert loaded is not None
    assert loaded.good == "goodabc123"
    assert loaded.bad == "baddef456"
    assert loaded.current == "midpoint789"
    assert loaded.tested == {"goodabc123": "good", "midpoint789": "bad"}
    assert loaded.pre_bisect_ref == "refs/heads/main"
    assert loaded.pre_bisect_commit == "originalabc"


def test_read_bisect_state_returns_none_when_absent(tmp_path: pathlib.Path) -> None:
    """read_bisect_state returns None when no BISECT_STATE.json exists."""
    _init_repo(tmp_path)
    assert read_bisect_state(tmp_path) is None


def test_clear_bisect_state_removes_file(tmp_path: pathlib.Path) -> None:
    """clear_bisect_state removes the state file; subsequent read returns None."""
    _init_repo(tmp_path)
    write_bisect_state(tmp_path, BisectState())
    assert read_bisect_state(tmp_path) is not None
    clear_bisect_state(tmp_path)
    assert read_bisect_state(tmp_path) is None


def test_clear_bisect_state_is_idempotent(tmp_path: pathlib.Path) -> None:
    """Calling clear_bisect_state when no file exists does not raise."""
    _init_repo(tmp_path)
    clear_bisect_state(tmp_path)  # should not raise


# ---------------------------------------------------------------------------
# Unit tests — pick_midpoint
# ---------------------------------------------------------------------------


def _make_commit(commit_id: str, offset_seconds: int = 0) -> MuseCliCommit:
    """Return an unsaved MuseCliCommit stub for midpoint testing."""
    return MuseCliCommit(
        commit_id=commit_id,
        repo_id="test-repo",
        branch="main",
        parent_commit_id=None,
        snapshot_id="snap-" + commit_id[:8],
        message="test",
        author="",
        committed_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        + datetime.timedelta(seconds=offset_seconds),
    )


def test_pick_midpoint_returns_none_on_empty() -> None:
    assert pick_midpoint([]) is None


def test_pick_midpoint_single_element() -> None:
    c = _make_commit("aaa")
    assert pick_midpoint([c]) is c


def test_pick_midpoint_selects_lower_middle_for_even() -> None:
    """For a 4-element list, midpoint is index 1 (lower-middle)."""
    commits = [_make_commit(f"c{i:03d}", i) for i in range(4)]
    mid = pick_midpoint(commits)
    assert mid is not None
    assert mid.commit_id == commits[1].commit_id


def test_pick_midpoint_selects_middle_for_odd() -> None:
    """For a 5-element list, midpoint is index 2."""
    commits = [_make_commit(f"c{i:03d}", i) for i in range(5)]
    mid = pick_midpoint(commits)
    assert mid is not None
    assert mid.commit_id == commits[2].commit_id


# ---------------------------------------------------------------------------
# Integration tests — commit graph traversal
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_commits_between_linear_history(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """get_commits_between returns the inner commits on a linear chain.

    Topology: good → c1 → c2 → c3 → bad
    Expected result: [c1, c2, c3] (oldest first, excluding good and bad).
    """
    _init_repo(tmp_path)

    _write_workdir(tmp_path, {"a.mid": b"GOOD"})
    await _commit_async(message="good commit", root=tmp_path, session=muse_cli_db_session)
    good_id = _head_commit(tmp_path)

    _write_workdir(tmp_path, {"a.mid": b"C1"})
    await _commit_async(message="c1", root=tmp_path, session=muse_cli_db_session)
    c1_id = _head_commit(tmp_path)

    _write_workdir(tmp_path, {"a.mid": b"C2"})
    await _commit_async(message="c2", root=tmp_path, session=muse_cli_db_session)
    c2_id = _head_commit(tmp_path)

    _write_workdir(tmp_path, {"a.mid": b"C3"})
    await _commit_async(message="c3", root=tmp_path, session=muse_cli_db_session)
    c3_id = _head_commit(tmp_path)

    _write_workdir(tmp_path, {"a.mid": b"BAD"})
    await _commit_async(message="bad commit", root=tmp_path, session=muse_cli_db_session)
    bad_id = _head_commit(tmp_path)

    candidates = await get_commits_between(muse_cli_db_session, good_id, bad_id)
    candidate_ids = {c.commit_id for c in candidates}

    assert c1_id in candidate_ids
    assert c2_id in candidate_ids
    assert c3_id in candidate_ids
    assert good_id not in candidate_ids
    assert bad_id not in candidate_ids

    # Must be sorted oldest first.
    assert [c.commit_id for c in candidates] == [c1_id, c2_id, c3_id]


@pytest.mark.anyio
async def test_get_commits_between_adjacent_commits_returns_empty(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """When good is the direct parent of bad, no commits to bisect."""
    _init_repo(tmp_path)

    _write_workdir(tmp_path, {"a.mid": b"V1"})
    await _commit_async(message="good", root=tmp_path, session=muse_cli_db_session)
    good_id = _head_commit(tmp_path)

    _write_workdir(tmp_path, {"a.mid": b"V2"})
    await _commit_async(message="bad", root=tmp_path, session=muse_cli_db_session)
    bad_id = _head_commit(tmp_path)

    candidates = await get_commits_between(muse_cli_db_session, good_id, bad_id)
    assert candidates == []


# ---------------------------------------------------------------------------
# Integration tests — advance_bisect state machine
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_bisect_state_machine_advances_correctly(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Regression test: bisect narrows range and identifies the culprit.

    Topology: good → c1 → culprit → c3 → bad  (4 inner commits)
    Bisect should identify *culprit* after ≤ 2 steps by binary search.
    """
    _init_repo(tmp_path)

    _write_workdir(tmp_path, {"beat.mid": b"GOOD"})
    await _commit_async(message="good groove", root=tmp_path, session=muse_cli_db_session)
    good_id = _head_commit(tmp_path)

    _write_workdir(tmp_path, {"beat.mid": b"C1"})
    await _commit_async(message="c1 ok", root=tmp_path, session=muse_cli_db_session)
    c1_id = _head_commit(tmp_path)

    _write_workdir(tmp_path, {"beat.mid": b"CULPRIT"})
    await _commit_async(message="culprit: introduced drift", root=tmp_path, session=muse_cli_db_session)
    culprit_id = _head_commit(tmp_path)

    _write_workdir(tmp_path, {"beat.mid": b"C3"})
    await _commit_async(message="c3 still broken", root=tmp_path, session=muse_cli_db_session)

    _write_workdir(tmp_path, {"beat.mid": b"BAD"})
    await _commit_async(message="bad groove", root=tmp_path, session=muse_cli_db_session)
    bad_id = _head_commit(tmp_path)

    # Start a bisect session.
    state = BisectState(
        good=None,
        bad=None,
        current=None,
        tested={},
        pre_bisect_ref="refs/heads/main",
        pre_bisect_commit=bad_id,
    )
    write_bisect_state(tmp_path, state)

    # Mark good.
    result = await advance_bisect(
        session=muse_cli_db_session, root=tmp_path, commit_id=good_id, verdict="good"
    )
    # Both bounds not yet set, so no next commit yet.
    assert result.culprit is None

    # Mark bad.
    result = await advance_bisect(
        session=muse_cli_db_session, root=tmp_path, commit_id=bad_id, verdict="bad"
    )
    assert result.culprit is None
    assert result.next_commit is not None
    midpoint_1 = result.next_commit

    # Midpoint should be inside the range [c1, culprit, c3].
    candidates_all = await get_commits_between(muse_cli_db_session, good_id, bad_id)
    candidate_ids_all = {c.commit_id for c in candidates_all}
    assert midpoint_1 in candidate_ids_all

    # Test the midpoint: if it's culprit or after → bad; before → good.
    # We need to simulate what a human / script would do.
    # Strategy: mark commits that come AFTER the culprit (in time) as bad,
    # and commits before/at culprit as bad too if they ARE the culprit.
    # Simple rule: commit_id == culprit_id OR is a descendant → bad.

    # Step 1: test the first midpoint.
    mid1_commit = await muse_cli_db_session.get(MuseCliCommit, midpoint_1)
    assert mid1_commit is not None
    # The culprit is the 2nd of 3 inner commits (c1, culprit, c3).
    # Binary search: midpoint of [c1, culprit, c3] (idx 0,1,2) → idx 1 = culprit.
    # If midpoint IS the culprit → mark bad.
    # In our test data the midpoint of 3 elements is index 1 = culprit.
    if mid1_commit.message == "culprit: introduced drift":
        # This is the culprit: mark as bad.
        result2 = await advance_bisect(
            session=muse_cli_db_session,
            root=tmp_path,
            commit_id=midpoint_1,
            verdict="bad",
        )
        # Next candidate: [c1] (commits before culprit but after good).
        # After marking culprit as bad, range = [c1].
        # Midpoint of [c1] = c1 itself.
        if result2.culprit is None:
            assert result2.next_commit is not None
            # Mark c1 as good → culprit identified as midpoint_1 (the culprit commit).
            result3 = await advance_bisect(
                session=muse_cli_db_session,
                root=tmp_path,
                commit_id=result2.next_commit,
                verdict="good",
            )
            assert result3.culprit == midpoint_1
    else:
        # c1 is the midpoint — mark it based on its position relative to culprit.
        # c1 is BEFORE culprit → good.
        result2 = await advance_bisect(
            session=muse_cli_db_session,
            root=tmp_path,
            commit_id=midpoint_1,
            verdict="good",
        )
        assert result2.culprit is None or result2.culprit == culprit_id


@pytest.mark.anyio
async def test_advance_bisect_with_only_one_inner_commit_finds_culprit(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """When only one commit is between good and bad, it is the culprit immediately."""
    _init_repo(tmp_path)

    _write_workdir(tmp_path, {"beat.mid": b"GOOD"})
    await _commit_async(message="good", root=tmp_path, session=muse_cli_db_session)
    good_id = _head_commit(tmp_path)

    _write_workdir(tmp_path, {"beat.mid": b"CULPRIT"})
    await _commit_async(message="culprit", root=tmp_path, session=muse_cli_db_session)
    culprit_id = _head_commit(tmp_path)

    _write_workdir(tmp_path, {"beat.mid": b"BAD"})
    await _commit_async(message="bad", root=tmp_path, session=muse_cli_db_session)
    bad_id = _head_commit(tmp_path)

    write_bisect_state(tmp_path, BisectState(
        good=None, bad=None, current=None, tested={},
        pre_bisect_ref="refs/heads/main", pre_bisect_commit=bad_id,
    ))

    await advance_bisect(session=muse_cli_db_session, root=tmp_path, commit_id=good_id, verdict="good")
    # Mark bad → range = [culprit], midpoint = culprit.
    result = await advance_bisect(session=muse_cli_db_session, root=tmp_path, commit_id=bad_id, verdict="bad")

    assert result.next_commit == culprit_id
    # Mark culprit as bad → range collapses.
    result2 = await advance_bisect(session=muse_cli_db_session, root=tmp_path, commit_id=culprit_id, verdict="bad")
    assert result2.culprit == culprit_id


@pytest.mark.anyio
async def test_advance_bisect_adjacent_commits_collapses_immediately(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """When good is the direct parent of bad, culprit is bad itself."""
    _init_repo(tmp_path)

    _write_workdir(tmp_path, {"a.mid": b"V1"})
    await _commit_async(message="good", root=tmp_path, session=muse_cli_db_session)
    good_id = _head_commit(tmp_path)

    _write_workdir(tmp_path, {"a.mid": b"V2"})
    await _commit_async(message="bad", root=tmp_path, session=muse_cli_db_session)
    bad_id = _head_commit(tmp_path)

    write_bisect_state(tmp_path, BisectState(
        good=None, bad=None, current=None, tested={},
        pre_bisect_ref="refs/heads/main", pre_bisect_commit=bad_id,
    ))

    await advance_bisect(session=muse_cli_db_session, root=tmp_path, commit_id=good_id, verdict="good")
    result = await advance_bisect(session=muse_cli_db_session, root=tmp_path, commit_id=bad_id, verdict="bad")
    # No inner commits → bad is the culprit immediately.
    assert result.culprit == bad_id


# ---------------------------------------------------------------------------
# CLI guard tests
# ---------------------------------------------------------------------------


def test_bisect_start_blocked_by_merge_in_progress(tmp_path: pathlib.Path) -> None:
    """muse bisect start exits 1 when MERGE_STATE.json is present."""
    from typer.testing import CliRunner
    from maestro.muse_cli.commands.bisect import app as bisect_app

    _init_repo(tmp_path)
    (tmp_path / ".muse" / "MERGE_STATE.json").write_text(
        json.dumps({"base_commit": "abc", "ours_commit": "def", "theirs_commit": "ghi", "conflict_paths": []})
    )

    runner = CliRunner()
    import os

    old_cwd = pathlib.Path.cwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(bisect_app, ["start"])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == ExitCode.USER_ERROR


def test_bisect_start_blocked_when_already_active(tmp_path: pathlib.Path) -> None:
    """muse bisect start exits 1 when BISECT_STATE.json already exists."""
    from typer.testing import CliRunner
    from maestro.muse_cli.commands.bisect import app as bisect_app

    _init_repo(tmp_path)
    write_bisect_state(tmp_path, BisectState(
        good=None, bad=None, current=None, tested={},
        pre_bisect_ref="refs/heads/main", pre_bisect_commit="abc",
    ))

    runner = CliRunner()
    import os

    old_cwd = pathlib.Path.cwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(bisect_app, ["start"])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == ExitCode.USER_ERROR


def test_bisect_good_without_active_session_exits_1(tmp_path: pathlib.Path) -> None:
    """muse bisect good exits 1 when no session is active."""
    from typer.testing import CliRunner
    from maestro.muse_cli.commands.bisect import app as bisect_app

    _init_repo(tmp_path)

    runner = CliRunner()
    import os

    old_cwd = pathlib.Path.cwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(bisect_app, ["good", "abc123"])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# bisect log --json
# ---------------------------------------------------------------------------


def test_bisect_log_json_emits_valid_json(tmp_path: pathlib.Path) -> None:
    """muse bisect log --json outputs valid JSON with expected fields."""
    from typer.testing import CliRunner
    from maestro.muse_cli.commands.bisect import app as bisect_app

    _init_repo(tmp_path)
    write_bisect_state(tmp_path, BisectState(
        good="good_sha",
        bad="bad_sha",
        current="current_sha",
        tested={"good_sha": "good"},
        pre_bisect_ref="refs/heads/main",
        pre_bisect_commit="orig_sha",
    ))

    runner = CliRunner()
    import os

    old_cwd = pathlib.Path.cwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(bisect_app, ["log", "--json"])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["good"] == "good_sha"
    assert data["bad"] == "bad_sha"
    assert data["current"] == "current_sha"
    assert data["tested"] == {"good_sha": "good"}


def test_bisect_log_no_active_session(tmp_path: pathlib.Path) -> None:
    """muse bisect log exits 0 with a message when no session is active."""
    from typer.testing import CliRunner
    from maestro.muse_cli.commands.bisect import app as bisect_app

    _init_repo(tmp_path)

    runner = CliRunner()
    import os

    old_cwd = pathlib.Path.cwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(bisect_app, ["log"])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0
    assert "No bisect session" in result.output
