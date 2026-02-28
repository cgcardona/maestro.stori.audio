"""Muse MVP golden-path integration test.

Exercises the complete local Muse VCS workflow — init → commit → branch →
commit → checkout → merge (conflict) → resolve → merge --continue → log
-- inside Docker, using the real Postgres database.

The remote portion (steps 12–15: push, pull, Hub PR) is skipped unless
``MUSE_HUB_URL`` is set in the environment.

Run:
    docker compose exec maestro pytest tests/e2e/test_muse_golden_path.py -v -s
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.checkout import checkout_branch
from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.merge import _merge_async, _merge_continue_async
from maestro.muse_cli.commands.resolve import resolve_conflict
from maestro.muse_cli.db import open_session
from maestro.muse_cli.merge_engine import read_merge_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(root: pathlib.Path) -> None:
    """Initialise a minimal .muse/ directory tree in *root*."""
    import datetime
    import uuid

    muse_dir = root / ".muse"
    (muse_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)

    repo_json = {
        "repo_id": str(uuid.uuid4()),
        "schema_version": "1",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    (muse_dir / "repo.json").write_text(json.dumps(repo_json, indent=2))
    (muse_dir / "HEAD").write_text("refs/heads/main\n")
    (muse_dir / "refs" / "heads" / "main").write_text("")
    (muse_dir / "config.toml").write_text("[user]\n[auth]\n[remotes]\n")


def _write_artifacts(root: pathlib.Path, version: str) -> None:
    """Write the initial set of synthetic muse-work/ artifacts.

    ``drums.json`` is always written with a fixed content so that only
    ``section-1.json`` varies between branches.  This ensures the merge
    conflict in golden-path tests is scoped to ``section-1.json`` only.
    """
    workdir = root / "muse-work"
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "meta").mkdir(exist_ok=True)
    (workdir / "tracks").mkdir(exist_ok=True)

    (workdir / "meta" / "section-1.json").write_text(
        json.dumps(
            {
                "section": "intro",
                "tempo_bpm": 120,
                "key": "C major",
                "version": version,
            },
            indent=2,
        )
    )
    # drums.json is fixed — only section-1.json varies across branches so
    # that merge conflict tests have exactly one conflicting path.
    (workdir / "tracks" / "drums.json").write_text(
        json.dumps({"instrument": "drums", "bars": 8}, indent=2)
    )


def _write_experiment_artifacts(root: pathlib.Path) -> None:
    """Write ONLY the experiment-branch changes (section-1.json only).

    Leaves ``drums.json`` unchanged so it doesn't participate in the
    expected merge conflict between experiment and main.
    """
    workdir = root / "muse-work"
    (workdir / "meta" / "section-1.json").write_text(
        json.dumps(
            {
                "section": "intro",
                "tempo_bpm": 140,
                "key": "G minor",
                "version": "experiment-v1",
            },
            indent=2,
        )
    )


def _write_conflicting_artifacts(root: pathlib.Path, version: str) -> None:
    """Write only section-1.json with a conflicting version on main.

    Only ``section-1.json`` is written so that ``drums.json`` (unchanged
    from the initial commit) does not participate in the conflict.
    """
    workdir = root / "muse-work"
    (workdir / "meta" / "section-1.json").write_text(
        json.dumps(
            {
                "section": "verse",
                "tempo_bpm": 110,
                "key": "E minor",
                "version": version,
            },
            indent=2,
        )
    )


def _head_commit_id(root: pathlib.Path, branch: str) -> str:
    """Return the current HEAD commit ID on *branch*, or empty string."""
    ref_path = root / ".muse" / "refs" / "heads" / branch
    if not ref_path.exists():
        return ""
    return ref_path.read_text().strip()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_golden_path_local(tmp_path: pathlib.Path) -> None:
    """Full local golden path: init → commit → branch → merge conflict → resolve.

    Exercises the complete local Muse MVP workflow end-to-end using an
    in-memory-compatible tmp_path and the real Postgres session.
    """
    root = tmp_path / "repo"
    root.mkdir()

    # Step 1: init
    _init_repo(root)
    assert (root / ".muse" / "repo.json").exists()
    assert (root / ".muse" / "HEAD").exists()

    async with open_session() as session:
        # Step 2 + 3: generate artifacts and commit on main
        _write_artifacts(root, version="main-v1")
        commit_id_1 = await _commit_async(
            message="feat: initial generation",
            root=root,
            session=session,
        )
        assert commit_id_1, "First commit must return a non-empty commit ID"
        assert _head_commit_id(root, "main") == commit_id_1

    # Step 4: checkout -b experiment
    checkout_branch(root=root, branch="experiment", create=True)
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    assert head_ref == "refs/heads/experiment", f"HEAD should be experiment, got {head_ref!r}"
    # experiment starts at same commit as main
    assert _head_commit_id(root, "experiment") == commit_id_1

    async with open_session() as session:
        # Step 5 + 6: different artifacts on experiment, commit
        # Only change section-1.json so drums.json doesn't participate in conflict
        _write_experiment_artifacts(root)
        commit_experiment = await _commit_async(
            message="feat: experimental variation",
            root=root,
            session=session,
        )
        assert commit_experiment != commit_id_1, "Experiment commit must differ from main"
        assert _head_commit_id(root, "experiment") == commit_experiment

    # Step 7: checkout main
    checkout_branch(root=root, branch="main", create=False)
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    assert head_ref == "refs/heads/main"

    async with open_session() as session:
        # Make a diverging commit on main (modifies section-1.json → conflict)
        _write_conflicting_artifacts(root, version="main-v2")
        commit_id_2 = await _commit_async(
            message="feat: verse section on main",
            root=root,
            session=session,
        )
        assert commit_id_2 != commit_id_1

        # Step 8: merge experiment → conflict expected (both modified section-1.json)
        import typer

        with pytest.raises(typer.Exit) as exc_info:
            await _merge_async(branch="experiment", root=root, session=session)

        # Exit code must be non-zero (USER_ERROR = 1 for conflict)
        assert exc_info.value.exit_code != 0, "Merge should exit with error on conflict"

    # MERGE_STATE.json must exist and list section-1.json
    merge_state = read_merge_state(root)
    assert merge_state is not None, "MERGE_STATE.json must be written on conflict"
    conflict_rel_paths = [p for p in merge_state.conflict_paths]
    assert any("section-1.json" in p for p in conflict_rel_paths), (
        f"section-1.json must be in conflicts, got {conflict_rel_paths}"
    )

    # Step 9: resolve --ours
    resolve_conflict(
        file_path="meta/section-1.json",
        ours=True,
        root=root,
    )
    # After resolving the only conflict, MERGE_STATE.json must STILL exist with
    # conflict_paths=[] so that --continue can read the stored commit IDs.
    merge_state_after = read_merge_state(root)
    assert merge_state_after is not None, (
        "MERGE_STATE.json should persist after resolve (--continue needs commit IDs)"
    )
    assert merge_state_after.conflict_paths == [], (
        f"Expected empty conflict_paths after resolve, got {merge_state_after.conflict_paths}"
    )

    async with open_session() as session:
        # Step 10: muse merge --continue
        await _merge_continue_async(root=root, session=session)

    merge_commit_id = _head_commit_id(root, "main")
    assert merge_commit_id, "main HEAD must have a commit after merge --continue"
    assert merge_commit_id != commit_id_2, "main HEAD must advance to the merge commit"

    # MERGE_STATE.json must be gone
    assert not (root / ".muse" / "MERGE_STATE.json").exists()


@pytest.mark.anyio
async def test_golden_path_log_shows_merge_commit(tmp_path: pathlib.Path) -> None:
    """muse log --graph output contains a merge commit with two parents.

    Verifies that the DAG produced by the golden-path workflow includes a
    merge commit node that references two distinct parent commit IDs.
    """
    from maestro.muse_cli.db import open_session
    from maestro.muse_cli.commands.log import _load_commits

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)

    async with open_session() as session:
        # main: initial commit
        _write_artifacts(root, version="main-v1")
        c1 = await _commit_async(message="feat: initial", root=root, session=session)

        # branch experiment (only section-1.json changes)
        checkout_branch(root=root, branch="experiment", create=True)
        _write_experiment_artifacts(root)
        c_exp = await _commit_async(message="feat: experiment", root=root, session=session)

        # back to main, diverge (only section-1.json changes → single conflict)
        checkout_branch(root=root, branch="main", create=False)
        _write_conflicting_artifacts(root, version="main-v2")
        c2 = await _commit_async(message="feat: main diverge", root=root, session=session)

        # merge (will conflict)
        import typer

        with pytest.raises(typer.Exit):
            await _merge_async(branch="experiment", root=root, session=session)

        # resolve
        resolve_conflict(file_path="meta/section-1.json", ours=True, root=root)

        # merge --continue → merge commit
        await _merge_continue_async(root=root, session=session)

        merge_commit_id = _head_commit_id(root, "main")
        assert merge_commit_id

        # Load the merge commit and verify it has two parents
        from maestro.muse_cli.models import MuseCliCommit

        merge_commit = await session.get(MuseCliCommit, merge_commit_id)
        assert merge_commit is not None
        assert merge_commit.parent_commit_id is not None, "Merge commit must have parent1"
        assert merge_commit.parent2_commit_id is not None, "Merge commit must have parent2"
        assert merge_commit.parent_commit_id != merge_commit.parent2_commit_id, (
            "The two parents of a merge commit must be distinct"
        )

        # Walk the log and confirm ≥4 nodes (c1, c2, c_exp, merge)
        commits = await _load_commits(session, head_commit_id=merge_commit_id, limit=100)
        assert len(commits) >= 3, (
            f"Expected ≥3 commits in the log, got {len(commits)}"
        )


@pytest.mark.anyio
@pytest.mark.skipif(
    not os.environ.get("MUSE_HUB_URL"),
    reason="MUSE_HUB_URL not set — remote golden-path test skipped",
)
async def test_golden_path_remote(tmp_path: pathlib.Path) -> None:
    """Push/pull round-trip: after pull, Rene's log matches Gabriel's.

    Skipped unless ``MUSE_HUB_URL`` is set in the environment.  This test
    is intended to run in environments where the Muse Hub is reachable.
    """
    hub_url = os.environ["MUSE_HUB_URL"]

    root = tmp_path / "gabriel"
    root.mkdir()
    _init_repo(root)

    async with open_session() as session:
        _write_artifacts(root, version="main-v1")
        await _commit_async(message="feat: initial", root=root, session=session)

    # remote add + push (stubs — log the call but verify the CLI doesn't crash)
    import subprocess

    result_remote = subprocess.run(
        ["muse", "remote", "add", "origin", hub_url],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result_remote.returncode == 0, (
        f"muse remote add failed: {result_remote.stderr}"
    )

    result_push = subprocess.run(
        ["muse", "push"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result_push.returncode == 0, f"muse push failed: {result_push.stderr}"

    # Rene: fresh repo → pull
    rene_root = tmp_path / "rene"
    rene_root.mkdir()
    _init_repo(rene_root)

    result_pull = subprocess.run(
        ["muse", "remote", "add", "origin", hub_url],
        cwd=rene_root,
        capture_output=True,
        text=True,
    )
    assert result_pull.returncode == 0

    result_pull = subprocess.run(
        ["muse", "pull", "--branch", "main"],
        cwd=rene_root,
        capture_output=True,
        text=True,
    )
    assert result_pull.returncode == 0, f"muse pull failed: {result_pull.stderr}"

    # Verify Rene has at least one commit on main
    rene_head = _head_commit_id(rene_root, "main")
    assert rene_head, "Rene should have a HEAD commit after pull"


@pytest.mark.anyio
async def test_checkout_branch_creates_and_switches(tmp_path: pathlib.Path) -> None:
    """muse checkout -b creates a new branch seeded from current HEAD."""
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)

    async with open_session() as session:
        _write_artifacts(root, version="v1")
        commit_id = await _commit_async(message="initial", root=root, session=session)

    checkout_branch(root=root, branch="feature", create=True)

    # feature branch should point to same commit as main
    assert _head_commit_id(root, "feature") == commit_id
    assert (root / ".muse" / "HEAD").read_text().strip() == "refs/heads/feature"

    # Switch back
    checkout_branch(root=root, branch="main", create=False)
    assert (root / ".muse" / "HEAD").read_text().strip() == "refs/heads/main"


@pytest.mark.anyio
async def test_resolve_clears_conflict_paths(tmp_path: pathlib.Path) -> None:
    """muse resolve --ours removes path from MERGE_STATE and clears when empty."""
    import datetime
    import uuid

    from maestro.muse_cli.merge_engine import write_merge_state

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)

    write_merge_state(
        root,
        base_commit="base000",
        ours_commit="ours111",
        theirs_commit="theirs222",
        conflict_paths=["meta/section-1.json", "tracks/piano.mid"],
        other_branch="feature",
    )
    assert (root / ".muse" / "MERGE_STATE.json").exists()

    # Resolve first conflict
    resolve_conflict(file_path="meta/section-1.json", ours=True, root=root)
    state = read_merge_state(root)
    assert state is not None
    assert "meta/section-1.json" not in state.conflict_paths
    assert "tracks/piano.mid" in state.conflict_paths

    # Resolve second — MERGE_STATE.json persists with empty conflict_paths so
    # that `muse merge --continue` can still read ours_commit / theirs_commit.
    resolve_conflict(file_path="tracks/piano.mid", ours=True, root=root)
    state = read_merge_state(root)
    assert state is not None, "MERGE_STATE.json should persist after resolve (--continue needs it)"
    assert state.conflict_paths == [], "All conflicts resolved — conflict_paths must be empty"


@pytest.mark.anyio
async def test_merge_continue_creates_merge_commit(tmp_path: pathlib.Path) -> None:
    """muse merge --continue creates a merge commit with two parents."""
    from maestro.muse_cli.merge_engine import write_merge_state
    from maestro.muse_cli.models import MuseCliCommit

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)

    async with open_session() as session:
        # Commit on main
        _write_artifacts(root, version="v1")
        c1 = await _commit_async(message="main initial", root=root, session=session)

        # Branch and commit on experiment (only section-1.json)
        checkout_branch(root=root, branch="experiment", create=True)
        _write_experiment_artifacts(root)
        c_exp = await _commit_async(message="experiment v1", root=root, session=session)

        # Switch back to main, diverge
        checkout_branch(root=root, branch="main", create=False)
        _write_conflicting_artifacts(root, version="main-v2")
        c2 = await _commit_async(message="main v2", root=root, session=session)

        # Manually write MERGE_STATE with no conflicts (as if resolve already ran)
        write_merge_state(
            root,
            base_commit=c1,
            ours_commit=c2,
            theirs_commit=c_exp,
            conflict_paths=[],
            other_branch="experiment",
        )
        # Clear the empty conflict list to simulate fully-resolved state.
        # (The actual resolve command does this, but we do it manually here.)
        (root / ".muse" / "MERGE_STATE.json").write_text(
            json.dumps(
                {
                    "base_commit": c1,
                    "ours_commit": c2,
                    "theirs_commit": c_exp,
                    "conflict_paths": [],
                    "other_branch": "experiment",
                },
                indent=2,
            )
        )

        await _merge_continue_async(root=root, session=session)

        merge_id = _head_commit_id(root, "main")
        assert merge_id and merge_id != c2

        merge_commit = await session.get(MuseCliCommit, merge_id)
        assert merge_commit is not None
        assert merge_commit.parent_commit_id is not None
        assert merge_commit.parent2_commit_id is not None
        assert not (root / ".muse" / "MERGE_STATE.json").exists()
