"""Tests for ``muse inspect`` — structured JSON of the Muse commit graph.

All async tests call ``_inspect_async`` directly with an in-memory SQLite
session and a ``tmp_path`` repo root — no real Postgres or running
process required.  Commits are seeded via ``_commit_async``.

Naming convention: test_inspect_<behavior>_<scenario>
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli.commands.commit import _commit_async
from maestro.muse_cli.commands.inspect import _inspect_async
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_inspect import (
    InspectFormat,
    MuseInspectResult,
    build_inspect_result,
    render_dot,
    render_json,
    render_mermaid,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _init_muse_repo(root: pathlib.Path, repo_id: str | None = None) -> str:
    """Initialise a minimal ``.muse/`` directory structure for tests."""
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
    """Create N commits with unique file content and return their IDs."""
    commit_ids: list[str] = []
    for i, msg in enumerate(messages):
        _write_workdir(root, {f"track_{file_seed + i}.mid": f"MIDI-{file_seed + i}".encode()})
        cid = await _commit_async(message=msg, root=root, session=session)
        commit_ids.append(cid)
    return commit_ids


def _get_repo_id(root: pathlib.Path) -> str:
    data: dict[str, str] = json.loads((root / ".muse" / "repo.json").read_text())
    return data["repo_id"]


# ---------------------------------------------------------------------------
# Regression: test_inspect_outputs_json_graph
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inspect_outputs_json_graph(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``muse inspect`` outputs valid JSON with the full commit graph (regression test)."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["take 1", "take 2", "take 3"])

    capsys.readouterr()
    result = await _inspect_async(
        root=tmp_path,
        session=muse_cli_db_session,
        ref=None,
        depth=None,
        branches=False,
        fmt=InspectFormat.json,
    )

    out = capsys.readouterr().out
    payload = json.loads(out)

    assert "repo_id" in payload
    assert payload["current_branch"] == "main"
    assert "branches" in payload
    assert "commits" in payload
    assert isinstance(payload["commits"], list)
    assert len(payload["commits"]) == 3

    commit_ids_in_output = {c["commit_id"] for c in payload["commits"]}
    for cid in cids:
        assert cid in commit_ids_in_output


# ---------------------------------------------------------------------------
# Unit: test_inspect_result_commits_newest_first
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inspect_result_commits_newest_first(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Commits in the result are newest-first."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["oldest", "middle", "newest"])

    result = await build_inspect_result(
        muse_cli_db_session,
        tmp_path,
        ref=None,
        depth=None,
        include_branches=False,
    )

    assert result.commits[0].commit_id == cids[2]
    assert result.commits[-1].commit_id == cids[0]


# ---------------------------------------------------------------------------
# Unit: test_inspect_depth_limits_commits
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inspect_depth_limits_commits(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--depth 2`` limits the traversal to 2 commits."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["c1", "c2", "c3", "c4", "c5"])

    result = await build_inspect_result(
        muse_cli_db_session,
        tmp_path,
        ref=None,
        depth=2,
        include_branches=False,
    )

    assert len(result.commits) == 2


# ---------------------------------------------------------------------------
# Unit: test_inspect_branches_flag_includes_all_branches
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inspect_branches_flag_includes_all_branches(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``--branches`` includes commits from all branch heads."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["main commit"])

    # Simulate a second branch by writing a ref file pointing to the same commit.
    (tmp_path / ".muse" / "refs" / "heads" / "feature").write_text(cids[0])

    result = await build_inspect_result(
        muse_cli_db_session,
        tmp_path,
        ref=None,
        depth=None,
        include_branches=True,
    )

    assert "feature" in result.branches
    assert "main" in result.branches


# ---------------------------------------------------------------------------
# Unit: test_inspect_result_includes_branch_pointers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inspect_result_includes_branch_pointers(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``branches`` dict maps branch names to their HEAD commit IDs."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["v1", "v2"])

    result = await build_inspect_result(
        muse_cli_db_session,
        tmp_path,
        ref=None,
        depth=None,
        include_branches=False,
    )

    assert result.branches["main"] == cids[1]  # HEAD = newest commit


# ---------------------------------------------------------------------------
# Unit: test_inspect_commit_fields_are_populated
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inspect_commit_fields_are_populated(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Each commit node includes all required fields from the issue spec."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["test commit"])

    result = await build_inspect_result(
        muse_cli_db_session,
        tmp_path,
        ref=None,
        depth=None,
        include_branches=False,
    )

    commit = result.commits[0]
    assert commit.commit_id == cids[0]
    assert commit.short_id == cids[0][:8]
    assert commit.branch == "main"
    assert commit.message == "test commit"
    assert commit.snapshot_id != ""
    assert commit.committed_at != ""
    assert isinstance(commit.metadata, dict)
    assert isinstance(commit.tags, list)


# ---------------------------------------------------------------------------
# Unit: test_inspect_parent_chain_preserved
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inspect_parent_chain_preserved(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Parent links in the result correctly chain commits together."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["first", "second", "third"])

    result = await build_inspect_result(
        muse_cli_db_session,
        tmp_path,
        ref=None,
        depth=None,
        include_branches=False,
    )

    commits_by_id = {c.commit_id: c for c in result.commits}
    # third → second → first
    assert commits_by_id[cids[2]].parent_commit_id == cids[1]
    assert commits_by_id[cids[1]].parent_commit_id == cids[0]
    assert commits_by_id[cids[0]].parent_commit_id is None


# ---------------------------------------------------------------------------
# Format: test_inspect_format_dot_outputs_dot_graph
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inspect_format_dot_outputs_dot_graph(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--format dot`` emits a valid Graphviz DOT graph."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["beat 1", "beat 2"])

    capsys.readouterr()
    await _inspect_async(
        root=tmp_path,
        session=muse_cli_db_session,
        ref=None,
        depth=None,
        branches=False,
        fmt=InspectFormat.dot,
    )
    out = capsys.readouterr().out

    assert "digraph muse_graph" in out
    for cid in cids:
        assert cid in out
    assert "->" in out


# ---------------------------------------------------------------------------
# Format: test_inspect_format_mermaid_outputs_mermaid
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inspect_format_mermaid_outputs_mermaid(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--format mermaid`` emits a Mermaid.js graph definition."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["riff 1", "riff 2"])

    capsys.readouterr()
    await _inspect_async(
        root=tmp_path,
        session=muse_cli_db_session,
        ref=None,
        depth=None,
        branches=False,
        fmt=InspectFormat.mermaid,
    )
    out = capsys.readouterr().out

    assert "graph LR" in out
    for cid in cids:
        assert cid[:8] in out
    assert "-->" in out


# ---------------------------------------------------------------------------
# Format: render_json unit test
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inspect_render_json_is_valid_json(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``render_json`` returns valid JSON matching the issue spec shape."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["chord 1", "chord 2"])

    result = await build_inspect_result(
        muse_cli_db_session,
        tmp_path,
        ref=None,
        depth=None,
        include_branches=False,
    )
    json_str = render_json(result)
    payload = json.loads(json_str)

    assert set(payload.keys()) == {"repo_id", "current_branch", "branches", "commits"}
    assert payload["current_branch"] == "main"
    assert len(payload["commits"]) == 2
    first_commit = payload["commits"][0]
    assert "commit_id" in first_commit
    assert "short_id" in first_commit
    assert "parent_commit_id" in first_commit
    assert "snapshot_id" in first_commit
    assert "metadata" in first_commit
    assert "tags" in first_commit


# ---------------------------------------------------------------------------
# Format: render_dot unit test
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inspect_render_dot_contains_nodes_and_edges(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``render_dot`` contains one node per commit and edge for each parent link."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["n1", "n2", "n3"])

    result = await build_inspect_result(
        muse_cli_db_session,
        tmp_path,
        ref=None,
        depth=None,
        include_branches=False,
    )
    dot = render_dot(result)

    # Three commit nodes
    for cid in cids:
        assert cid in dot
    # Two parent edges (n3→n2, n2→n1)
    assert dot.count("->") >= 2
    assert "digraph" in dot


# ---------------------------------------------------------------------------
# Format: render_mermaid unit test
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inspect_render_mermaid_contains_nodes_and_edges(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """``render_mermaid`` contains one node per commit and a ``-->`` edge per parent."""
    _init_muse_repo(tmp_path)
    cids = await _make_commits(tmp_path, muse_cli_db_session, ["m1", "m2"])

    result = await build_inspect_result(
        muse_cli_db_session,
        tmp_path,
        ref=None,
        depth=None,
        include_branches=False,
    )
    mermaid = render_mermaid(result)

    for cid in cids:
        assert cid[:8] in mermaid
    assert "graph LR" in mermaid
    assert "-->" in mermaid


# ---------------------------------------------------------------------------
# Edge case: test_inspect_empty_repo_returns_empty_commits
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inspect_empty_repo_returns_empty_commits(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``muse inspect`` on an empty repo returns zero commits and valid JSON."""
    _init_muse_repo(tmp_path)

    capsys.readouterr()
    result = await _inspect_async(
        root=tmp_path,
        session=muse_cli_db_session,
        ref=None,
        depth=None,
        branches=False,
        fmt=InspectFormat.json,
    )

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["commits"] == []
    assert result.commits == []


# ---------------------------------------------------------------------------
# Edge case: test_inspect_invalid_ref_raises_value_error
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inspect_invalid_ref_raises_value_error(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """A ref that cannot be resolved raises ValueError."""
    _init_muse_repo(tmp_path)
    await _make_commits(tmp_path, muse_cli_db_session, ["only commit"])

    with pytest.raises(ValueError, match="Cannot resolve ref"):
        await build_inspect_result(
            muse_cli_db_session,
            tmp_path,
            ref="deadbeef00000000",
            depth=None,
            include_branches=False,
        )


# ---------------------------------------------------------------------------
# CLI skeleton: test_inspect_outside_repo_exits_repo_not_found
# ---------------------------------------------------------------------------


def test_inspect_outside_repo_exits_repo_not_found(tmp_path: pathlib.Path) -> None:
    """``muse inspect`` outside a .muse/ directory exits with REPO_NOT_FOUND."""
    import os
    from typer.testing import CliRunner
    from maestro.muse_cli.app import cli

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["inspect"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == ExitCode.REPO_NOT_FOUND
