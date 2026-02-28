"""Tests for ``muse describe``.

All async tests call ``_describe_async`` directly with an in-memory SQLite
session and a ``tmp_path`` repo root — no real Postgres or running process
required.  Commits are seeded via ``_commit_async`` so the two commands
are tested as an integrated pair.

Coverage:
- HEAD describe with no parent (root commit)
- HEAD describe with parent (diff shows changed files)
- Explicit commit ID describe
- --compare A B mode
- --depth brief / standard / verbose output
- --json output format
- --dimensions passthrough
- --auto-tag heuristic
- No commits → clean exit
- Outside repo → exit code 2
- --compare with wrong arg count → exit code 1
- Commit not found → exit code 1
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
from maestro.muse_cli.commands.describe import (
    DescribeDepth,
    DescribeResult,
    _describe_async,
    _diff_manifests,
    _infer_dimensions,
    _suggest_tag,
)
from maestro.muse_cli.errors import ExitCode


# ---------------------------------------------------------------------------
# Helpers (mirrors test_log.py pattern)
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


async def _make_commit(
    root: pathlib.Path,
    session: AsyncSession,
    message: str,
    files: dict[str, bytes],
) -> str:
    """Create one commit with the given files and message."""
    _write_workdir(root, files)
    return await _commit_async(message=message, root=root, session=session)


# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------


class TestDiffManifests:
    def test_diff_manifests_identical(self) -> None:
        m = {"a.mid": "aaa", "b.mid": "bbb"}
        changed, added, removed = _diff_manifests(m, m)
        assert changed == []
        assert added == []
        assert removed == []

    def test_diff_manifests_modified(self) -> None:
        base = {"a.mid": "old"}
        target = {"a.mid": "new"}
        changed, added, removed = _diff_manifests(base, target)
        assert changed == ["a.mid"]
        assert added == []
        assert removed == []

    def test_diff_manifests_added(self) -> None:
        base: dict[str, str] = {}
        target = {"new.mid": "hash"}
        changed, added, removed = _diff_manifests(base, target)
        assert changed == []
        assert added == ["new.mid"]
        assert removed == []

    def test_diff_manifests_removed(self) -> None:
        base = {"gone.mid": "hash"}
        target: dict[str, str] = {}
        changed, added, removed = _diff_manifests(base, target)
        assert changed == []
        assert added == []
        assert removed == ["gone.mid"]

    def test_diff_manifests_mixed(self) -> None:
        base = {"a.mid": "1", "b.mid": "2", "c.mid": "3"}
        target = {"a.mid": "changed", "c.mid": "3", "d.mid": "4"}
        changed, added, removed = _diff_manifests(base, target)
        assert changed == ["a.mid"]
        assert added == ["d.mid"]
        assert removed == ["b.mid"]

    def test_diff_manifests_sorted_output(self) -> None:
        base = {"z.mid": "1"}
        target = {"a.mid": "2", "z.mid": "1"}
        _, added, _ = _diff_manifests(base, target)
        assert added == ["a.mid"]


class TestInferDimensions:
    def test_infer_dimensions_no_changes(self) -> None:
        dims = _infer_dimensions([], [], [], [])
        assert dims == []

    def test_infer_dimensions_structural_singular(self) -> None:
        dims = _infer_dimensions(["a.mid"], [], [], [])
        assert len(dims) == 1
        assert "1 file" in dims[0]

    def test_infer_dimensions_structural_plural(self) -> None:
        dims = _infer_dimensions(["a.mid", "b.mid"], [], [], [])
        assert "2 files" in dims[0]

    def test_infer_dimensions_requested_passthrough(self) -> None:
        dims = _infer_dimensions(["a.mid"], [], [], ["rhythm", "harmony"])
        assert dims == ["rhythm", "harmony"]

    def test_infer_dimensions_requested_strips_whitespace(self) -> None:
        dims = _infer_dimensions([], [], [], [" rhythm ", " harmony "])
        assert dims == ["rhythm", "harmony"]


class TestSuggestTag:
    def test_suggest_tag_no_change(self) -> None:
        assert _suggest_tag([], 0) == "no-change"

    def test_suggest_tag_single_file(self) -> None:
        assert _suggest_tag(["structural"], 1) == "single-file-edit"

    def test_suggest_tag_minor(self) -> None:
        assert _suggest_tag(["structural"], 3) == "minor-revision"

    def test_suggest_tag_major(self) -> None:
        assert _suggest_tag(["structural"], 10) == "major-revision"


class TestDescribeResult:
    def _make_result(self, **kwargs: object) -> DescribeResult:
        defaults: dict[str, object] = dict(
            commit_id="a" * 64,
            message="test msg",
            depth=DescribeDepth.standard,
            parent_id=None,
            compare_commit_id=None,
            changed_files=[],
            added_files=[],
            removed_files=[],
            dimensions=[],
            auto_tag=None,
        )
        defaults.update(kwargs)
        return DescribeResult(**defaults)  # type: ignore[arg-type]

    def test_file_count_empty(self) -> None:
        r = self._make_result()
        assert r.file_count() == 0

    def test_file_count_sum(self) -> None:
        r = self._make_result(
            changed_files=["a.mid"],
            added_files=["b.mid", "c.mid"],
            removed_files=[],
        )
        assert r.file_count() == 3

    def test_to_dict_has_required_keys(self) -> None:
        r = self._make_result(changed_files=["x.mid"])
        d = r.to_dict()
        assert "commit" in d
        assert "message" in d
        assert "depth" in d
        assert "changed_files" in d
        assert "added_files" in d
        assert "removed_files" in d
        assert "dimensions" in d
        assert "file_count" in d
        assert "parent" in d
        assert "note" in d

    def test_to_dict_compare_commit_included_when_set(self) -> None:
        r = self._make_result(compare_commit_id="b" * 64)
        d = r.to_dict()
        assert "compare_commit" in d

    def test_to_dict_auto_tag_included_when_set(self) -> None:
        r = self._make_result(auto_tag="minor-revision")
        d = r.to_dict()
        assert d["auto_tag"] == "minor-revision"


# ---------------------------------------------------------------------------
# Integration tests — _describe_async with real DB
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_describe_root_commit_no_parent(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Root commit (no parent) shows all files as added."""
    _init_muse_repo(tmp_path)
    await _make_commit(
        tmp_path, muse_cli_db_session, "init session",
        {"beat.mid": b"MIDI1", "keys.mid": b"MIDI2"}
    )

    result = await _describe_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_id=None,
        compare_a=None,
        compare_b=None,
        depth=DescribeDepth.standard,
        dimensions_raw=None,
        as_json=False,
        auto_tag=False,
    )

    # Root commit: no parent, so all files are "added"
    assert result.parent_id is None
    assert result.file_count() == 2
    assert len(result.added_files) == 2
    assert result.changed_files == []
    assert result.removed_files == []


@pytest.mark.anyio
async def test_describe_head_shows_diff_from_parent(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """HEAD describe shows files changed relative to its parent."""
    _init_muse_repo(tmp_path)
    await _make_commit(
        tmp_path, muse_cli_db_session, "first take",
        {"beat.mid": b"v1", "keys.mid": b"v1"}
    )
    await _make_commit(
        tmp_path, muse_cli_db_session, "update beat",
        {"beat.mid": b"v2", "keys.mid": b"v1"}  # keys unchanged, beat modified
    )

    result = await _describe_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_id=None,
        compare_a=None,
        compare_b=None,
        depth=DescribeDepth.standard,
        dimensions_raw=None,
        as_json=False,
        auto_tag=False,
    )

    # Only beat.mid changed between first and second commit
    # Manifest keys are relative to muse-work/ directory (not the repo root)
    assert result.changed_files == ["beat.mid"]
    assert result.added_files == []
    assert result.removed_files == []
    assert result.file_count() == 1
    assert result.message == "update beat"


@pytest.mark.anyio
async def test_describe_explicit_commit_id(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """Describing an explicit commit ID works correctly."""
    _init_muse_repo(tmp_path)
    cid1 = await _make_commit(
        tmp_path, muse_cli_db_session, "take one",
        {"track_1.mid": b"data"}
    )
    # Make a second commit so HEAD != cid1
    await _make_commit(
        tmp_path, muse_cli_db_session, "take two",
        {"track_1.mid": b"data", "track_2.mid": b"more"}
    )

    result = await _describe_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_id=cid1,
        compare_a=None,
        compare_b=None,
        depth=DescribeDepth.standard,
        dimensions_raw=None,
        as_json=False,
        auto_tag=False,
    )

    assert result.commit_id == cid1
    assert result.message == "take one"


@pytest.mark.anyio
async def test_describe_compare_mode(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """--compare A B produces a diff between two explicit commits."""
    _init_muse_repo(tmp_path)
    cid_a = await _make_commit(
        tmp_path, muse_cli_db_session, "baseline",
        {"beat.mid": b"v1"}
    )
    cid_b = await _make_commit(
        tmp_path, muse_cli_db_session, "add melody",
        {"beat.mid": b"v1", "melody.mid": b"new"}
    )

    result = await _describe_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_id=None,
        compare_a=cid_a,
        compare_b=cid_b,
        depth=DescribeDepth.standard,
        dimensions_raw=None,
        as_json=False,
        auto_tag=False,
    )

    assert result.commit_id == cid_b
    assert result.compare_commit_id == cid_a
    # Manifest keys are relative to muse-work/ directory
    assert "melody.mid" in result.added_files
    assert result.file_count() == 1


@pytest.mark.anyio
async def test_describe_depth_brief(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--depth brief produces a one-line summary."""
    _init_muse_repo(tmp_path)
    await _make_commit(
        tmp_path, muse_cli_db_session, "init",
        {"beat.mid": b"data"}
    )

    result = await _describe_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_id=None,
        compare_a=None,
        compare_b=None,
        depth=DescribeDepth.brief,
        dimensions_raw=None,
        as_json=False,
        auto_tag=False,
    )

    from maestro.muse_cli.commands.describe import _render_brief
    _render_brief(result)
    out = capsys.readouterr().out

    # Brief: short commit ID and file count, no commit message
    assert result.commit_id[:8] in out
    assert "file change" in out
    # No verbose detail
    assert "Note:" not in out


@pytest.mark.anyio
async def test_describe_depth_standard(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--depth standard includes message, files, dimensions, and note."""
    _init_muse_repo(tmp_path)
    await _make_commit(
        tmp_path, muse_cli_db_session, "add chorus",
        {"chorus.mid": b"x"}
    )

    result = await _describe_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_id=None,
        compare_a=None,
        compare_b=None,
        depth=DescribeDepth.standard,
        dimensions_raw=None,
        as_json=False,
        auto_tag=False,
    )

    from maestro.muse_cli.commands.describe import _render_standard
    _render_standard(result)
    out = capsys.readouterr().out

    assert "add chorus" in out
    assert "chorus.mid" in out
    assert "Dimensions analyzed:" in out
    assert "Note:" in out


@pytest.mark.anyio
async def test_describe_depth_verbose(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--depth verbose includes full commit ID and parent."""
    _init_muse_repo(tmp_path)
    cid1 = await _make_commit(
        tmp_path, muse_cli_db_session, "first",
        {"a.mid": b"1"}
    )
    await _make_commit(
        tmp_path, muse_cli_db_session, "second",
        {"a.mid": b"2"}
    )

    result = await _describe_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_id=None,
        compare_a=None,
        compare_b=None,
        depth=DescribeDepth.verbose,
        dimensions_raw=None,
        as_json=False,
        auto_tag=False,
    )

    from maestro.muse_cli.commands.describe import _render_verbose
    _render_verbose(result)
    out = capsys.readouterr().out

    # Full commit ID visible
    assert result.commit_id in out
    # Parent shown
    assert cid1 in out
    # File status prefix
    assert "M  " in out


@pytest.mark.anyio
async def test_describe_json_output(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--json outputs valid JSON with the expected structure."""
    _init_muse_repo(tmp_path)
    await _make_commit(
        tmp_path, muse_cli_db_session, "json test",
        {"track.mid": b"data"}
    )

    result = await _describe_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_id=None,
        compare_a=None,
        compare_b=None,
        depth=DescribeDepth.standard,
        dimensions_raw=None,
        as_json=True,
        auto_tag=False,
    )

    from maestro.muse_cli.commands.describe import _render_result
    capsys.readouterr()  # discard ✅ output from _commit_async calls
    _render_result(result, as_json=True)
    out = capsys.readouterr().out
    data = json.loads(out)

    assert "commit" in data
    assert "message" in data
    assert data["message"] == "json test"
    assert "changed_files" in data
    assert "added_files" in data
    assert "removed_files" in data
    assert "file_count" in data
    assert "note" in data


@pytest.mark.anyio
async def test_describe_dimensions_passthrough(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """--dimensions passes user-specified dimensions through to result."""
    _init_muse_repo(tmp_path)
    await _make_commit(
        tmp_path, muse_cli_db_session, "dim test",
        {"beat.mid": b"x"}
    )

    result = await _describe_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_id=None,
        compare_a=None,
        compare_b=None,
        depth=DescribeDepth.standard,
        dimensions_raw="rhythm,harmony",
        as_json=False,
        auto_tag=False,
    )

    assert "rhythm" in result.dimensions
    assert "harmony" in result.dimensions


@pytest.mark.anyio
async def test_describe_auto_tag(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
) -> None:
    """--auto-tag adds a non-empty tag to the result."""
    _init_muse_repo(tmp_path)
    await _make_commit(
        tmp_path, muse_cli_db_session, "tagged commit",
        {"x.mid": b"y"}
    )

    result = await _describe_async(
        root=tmp_path,
        session=muse_cli_db_session,
        commit_id=None,
        compare_a=None,
        compare_b=None,
        depth=DescribeDepth.standard,
        dimensions_raw=None,
        as_json=False,
        auto_tag=True,
    )

    assert result.auto_tag is not None
    assert len(result.auto_tag) > 0


@pytest.mark.anyio
async def test_describe_no_commits_exits_zero(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``muse describe`` on a repo with no commits exits 0 with a message."""
    _init_muse_repo(tmp_path)

    with pytest.raises(typer.Exit) as exc_info:
        await _describe_async(
            root=tmp_path,
            session=muse_cli_db_session,
            commit_id=None,
            compare_a=None,
            compare_b=None,
            depth=DescribeDepth.standard,
            dimensions_raw=None,
            as_json=False,
            auto_tag=False,
        )

    assert exc_info.value.exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "No commits" in out


@pytest.mark.anyio
async def test_describe_unknown_commit_exits_user_error(
    tmp_path: pathlib.Path,
    muse_cli_db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Passing an unknown commit ID exits with USER_ERROR."""
    _init_muse_repo(tmp_path)

    bogus_id = "d" * 64
    with pytest.raises(typer.Exit) as exc_info:
        await _describe_async(
            root=tmp_path,
            session=muse_cli_db_session,
            commit_id=bogus_id,
            compare_a=None,
            compare_b=None,
            depth=DescribeDepth.standard,
            dimensions_raw=None,
            as_json=False,
            auto_tag=False,
        )

    assert exc_info.value.exit_code == ExitCode.USER_ERROR
    out = capsys.readouterr().out
    assert "not found" in out.lower()


def test_describe_outside_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse describe`` outside a .muse/ directory exits with code 2."""
    from typer.testing import CliRunner

    from maestro.muse_cli.app import cli

    runner = CliRunner()
    prev = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(cli, ["describe"], catch_exceptions=False)
    finally:
        os.chdir(prev)

    assert result.exit_code == ExitCode.REPO_NOT_FOUND
