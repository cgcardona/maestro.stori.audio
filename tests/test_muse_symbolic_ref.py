"""Tests for ``muse symbolic-ref`` — read or write a symbolic ref.

Verifies:
- read_symbolic_ref: returns SymbolicRefResult for a valid symbolic ref.
- read_symbolic_ref: returns None for a non-existent file.
- read_symbolic_ref: returns None when content is a bare SHA (not symbolic).
- read_symbolic_ref.short: last path component only.
- write_symbolic_ref: creates the file with the given target.
- write_symbolic_ref: raises ValueError for a non-refs/ target.
- write_symbolic_ref: creates intermediate directories for nested refs.
- delete_symbolic_ref: removes the file; returns True.
- delete_symbolic_ref: returns False for a non-existent file.
- CLI read path: ``muse symbolic-ref HEAD`` prints full ref.
- CLI --short: prints just the branch name.
- CLI write path: ``muse symbolic-ref HEAD refs/heads/x`` updates the file.
- CLI write rejects non-refs/ target.
- CLI --delete: removes the file; exits 0.
- CLI --delete absent file: exits USER_ERROR.
- CLI -q suppresses error output when ref is not symbolic.
- Boundary seal (AST): ``from __future__ import annotations`` present.
"""
from __future__ import annotations

import ast
import pathlib

import pytest
from typer.testing import CliRunner

from maestro.muse_cli.app import cli
from maestro.muse_cli.commands.symbolic_ref import (
    SymbolicRefResult,
    delete_symbolic_ref,
    read_symbolic_ref,
    write_symbolic_ref,
)
from maestro.muse_cli.errors import ExitCode

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def muse_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal .muse directory."""
    d = tmp_path / ".muse"
    d.mkdir()
    return d


@pytest.fixture
def repo_root_with_head(tmp_path: pathlib.Path) -> pathlib.Path:
    """Full repo directory with .muse/HEAD pointing at refs/heads/main."""
    muse = tmp_path / ".muse"
    muse.mkdir()
    (muse / "HEAD").write_text("refs/heads/main\n")
    refs = muse / "refs" / "heads"
    refs.mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Unit tests — pure logic
# ---------------------------------------------------------------------------


def test_read_symbolic_ref_returns_result(muse_dir: pathlib.Path) -> None:
    (muse_dir / "HEAD").write_text("refs/heads/main\n")
    result = read_symbolic_ref(muse_dir, "HEAD")
    assert result is not None
    assert result.ref == "refs/heads/main"
    assert result.name == "HEAD"
    assert result.short == "main"


def test_read_symbolic_ref_missing_file_returns_none(muse_dir: pathlib.Path) -> None:
    result = read_symbolic_ref(muse_dir, "HEAD", quiet=True)
    assert result is None


def test_read_symbolic_ref_bare_sha_returns_none(muse_dir: pathlib.Path) -> None:
    """Detached HEAD contains a bare SHA — not a symbolic ref."""
    (muse_dir / "HEAD").write_text("a" * 64 + "\n")
    result = read_symbolic_ref(muse_dir, "HEAD", quiet=True)
    assert result is None


def test_symbolic_ref_result_short_no_slash() -> None:
    """A ref without slashes uses the whole string as short form."""
    result = SymbolicRefResult(name="HEAD", ref="main")
    assert result.short == "main"


def test_write_symbolic_ref_creates_file(muse_dir: pathlib.Path) -> None:
    write_symbolic_ref(muse_dir, "HEAD", "refs/heads/feature/x")
    content = (muse_dir / "HEAD").read_text().strip()
    assert content == "refs/heads/feature/x"


def test_write_symbolic_ref_raises_for_non_refs_target(muse_dir: pathlib.Path) -> None:
    with pytest.raises(ValueError, match="must start with 'refs/'"):
        write_symbolic_ref(muse_dir, "HEAD", "main")


def test_write_symbolic_ref_creates_intermediate_dirs(muse_dir: pathlib.Path) -> None:
    write_symbolic_ref(muse_dir, "refs/heads/feature/guitar", "refs/heads/main")
    assert (muse_dir / "refs" / "heads" / "feature" / "guitar").exists()


def test_delete_symbolic_ref_removes_file(muse_dir: pathlib.Path) -> None:
    (muse_dir / "HEAD").write_text("refs/heads/main\n")
    deleted = delete_symbolic_ref(muse_dir, "HEAD")
    assert deleted is True
    assert not (muse_dir / "HEAD").exists()


def test_delete_symbolic_ref_absent_returns_false(muse_dir: pathlib.Path) -> None:
    deleted = delete_symbolic_ref(muse_dir, "HEAD", quiet=True)
    assert deleted is False


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


def test_cli_read_prints_full_ref(repo_root_with_head: pathlib.Path) -> None:
    result = runner.invoke(cli, ["symbolic-ref", "HEAD"], env={"MUSE_REPO_ROOT": str(repo_root_with_head)})
    assert result.exit_code == ExitCode.SUCCESS
    assert "refs/heads/main" in result.output


def test_cli_read_short_prints_branch_name(repo_root_with_head: pathlib.Path) -> None:
    result = runner.invoke(
        cli,
        ["symbolic-ref", "--short", "HEAD"],
        env={"MUSE_REPO_ROOT": str(repo_root_with_head)},
    )
    assert result.exit_code == ExitCode.SUCCESS
    assert result.output.strip() == "main"
    assert "refs/heads/main" not in result.output


def test_cli_write_updates_file(repo_root_with_head: pathlib.Path) -> None:
    result = runner.invoke(
        cli,
        ["symbolic-ref", "HEAD", "refs/heads/feature/guitar"],
        env={"MUSE_REPO_ROOT": str(repo_root_with_head)},
    )
    assert result.exit_code == ExitCode.SUCCESS
    content = (repo_root_with_head / ".muse" / "HEAD").read_text().strip()
    assert content == "refs/heads/feature/guitar"


def test_cli_write_rejects_non_refs_target(repo_root_with_head: pathlib.Path) -> None:
    result = runner.invoke(
        cli,
        ["symbolic-ref", "HEAD", "main"],
        env={"MUSE_REPO_ROOT": str(repo_root_with_head)},
    )
    assert result.exit_code == ExitCode.USER_ERROR
    assert "refs/" in result.output


def test_cli_delete_removes_file(repo_root_with_head: pathlib.Path) -> None:
    result = runner.invoke(
        cli,
        ["symbolic-ref", "--delete", "HEAD"],
        env={"MUSE_REPO_ROOT": str(repo_root_with_head)},
    )
    assert result.exit_code == ExitCode.SUCCESS
    assert not (repo_root_with_head / ".muse" / "HEAD").exists()


def test_cli_delete_absent_file_exits_user_error(repo_root_with_head: pathlib.Path) -> None:
    muse_dir = repo_root_with_head / ".muse"
    (muse_dir / "HEAD").unlink()
    result = runner.invoke(
        cli,
        ["symbolic-ref", "--delete", "HEAD"],
        env={"MUSE_REPO_ROOT": str(repo_root_with_head)},
    )
    assert result.exit_code == ExitCode.USER_ERROR


def test_cli_read_non_symbolic_exits_user_error(repo_root_with_head: pathlib.Path) -> None:
    """Detached HEAD (bare SHA) should exit USER_ERROR on read."""
    (repo_root_with_head / ".muse" / "HEAD").write_text("a" * 64 + "\n")
    result = runner.invoke(
        cli,
        ["symbolic-ref", "HEAD"],
        env={"MUSE_REPO_ROOT": str(repo_root_with_head)},
    )
    assert result.exit_code == ExitCode.USER_ERROR


def test_cli_quiet_suppresses_error_output(repo_root_with_head: pathlib.Path) -> None:
    """With -q, reading a non-symbolic ref should produce no user-facing text."""
    (repo_root_with_head / ".muse" / "HEAD").write_text("a" * 64 + "\n")
    result = runner.invoke(
        cli,
        ["symbolic-ref", "-q", "HEAD"],
        env={"MUSE_REPO_ROOT": str(repo_root_with_head)},
    )
    assert result.exit_code == ExitCode.USER_ERROR
    assert result.output.strip() == ""


# ---------------------------------------------------------------------------
# Boundary seal
# ---------------------------------------------------------------------------


def test_boundary_seal_future_annotations() -> None:
    """Verify ``from __future__ import annotations`` is the first import."""
    import maestro.muse_cli.commands.symbolic_ref as mod

    source = pathlib.Path(mod.__file__).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "__future__" and any(
                alias.name == "annotations" for alias in node.names
            ):
                return
    pytest.fail("from __future__ import annotations not found in symbolic_ref.py")
