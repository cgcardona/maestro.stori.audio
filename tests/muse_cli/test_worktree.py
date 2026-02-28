"""Tests for ``muse worktree`` subcommands.

Covers the acceptance criteria from issue #83:
- ``muse worktree add`` creates a linked worktree with shared objects store.
- Linked worktrees have independent muse-work/ and .muse gitdir file.
- ``muse worktree list`` shows main + linked worktrees with path, branch, HEAD.
- ``muse worktree remove`` cleans up the directory and registration.
- ``muse worktree prune`` removes stale registrations (directory gone).
- Cannot check out the same branch in two worktrees simultaneously.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from typer.testing import CliRunner

from maestro.muse_cli.app import cli
from maestro.muse_cli.commands.worktree import (
    WorktreeInfo,
    add_worktree,
    list_worktrees,
    prune_worktrees,
    remove_worktree,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: pathlib.Path, *, initial_commit: str = "") -> pathlib.Path:
    """Create a minimal .muse/ repo under tmp_path."""
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    (muse_dir / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo-id", "schema_version": "1"}),
        encoding="utf-8",
    )
    (muse_dir / "HEAD").write_text("refs/heads/main\n", encoding="utf-8")
    refs_dir = muse_dir / "refs" / "heads"
    refs_dir.mkdir(parents=True)
    (refs_dir / "main").write_text(initial_commit, encoding="utf-8")
    return tmp_path


def _env(root: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(root)}


# ---------------------------------------------------------------------------
# list_worktrees
# ---------------------------------------------------------------------------


class TestListWorktrees:
    def test_main_only_returns_one_entry(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        worktrees = list_worktrees(root)
        assert len(worktrees) == 1
        assert worktrees[0].is_main
        assert worktrees[0].branch == "main"

    def test_main_worktree_path_is_root(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        worktrees = list_worktrees(root)
        assert worktrees[0].path == root

    def test_main_worktree_head_commit(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path, initial_commit="abc12345")
        worktrees = list_worktrees(root)
        assert worktrees[0].head_commit == "abc12345"

    def test_linked_worktrees_appear_after_main(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _init_repo(tmp_path)
        link_path = tmp_path.parent / "linked-wt"
        add_worktree(root=root, link_path=link_path, branch="feature/test")
        worktrees = list_worktrees(root)
        assert len(worktrees) == 2
        assert worktrees[0].is_main
        assert not worktrees[1].is_main
        assert worktrees[1].branch == "feature/test"


# ---------------------------------------------------------------------------
# add_worktree — regression test required by issue #83
# ---------------------------------------------------------------------------


class TestWorktreeAdd:
    def test_worktree_add_creates_linked_worktree_with_shared_objects(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Regression: add_worktree creates a linked dir sharing the main .muse/."""
        root = _init_repo(tmp_path)
        link_path = tmp_path.parent / "my-feature-wt"
        info = add_worktree(root=root, link_path=link_path, branch="feature/guitar")

        assert isinstance(info, WorktreeInfo)
        assert info.path == link_path
        assert info.branch == "feature/guitar"
        assert not info.is_main

        # Directory created.
        assert link_path.is_dir()
        # muse-work/ created.
        assert (link_path / "muse-work").is_dir()
        # .muse gitdir file created.
        muse_file = link_path / ".muse"
        assert muse_file.is_file()
        assert "gitdir:" in muse_file.read_text()

        # Registered in main .muse/worktrees/.
        wt_dir = root / ".muse" / "worktrees"
        entries = list(wt_dir.iterdir())
        assert len(entries) == 1
        registration = entries[0]
        assert (registration / "path").read_text().strip() == str(link_path)
        assert (registration / "branch").read_text().strip() == "feature/guitar"

    def test_worktree_add_creates_branch_ref_if_absent(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _init_repo(tmp_path)
        link_path = tmp_path.parent / "new-branch-wt"
        add_worktree(root=root, link_path=link_path, branch="feature/new")
        ref_path = root / ".muse" / "refs" / "heads" / "feature" / "new"
        assert ref_path.exists()

    def test_worktree_add_reuses_existing_branch_ref(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _init_repo(tmp_path, initial_commit="deadbeef")
        muse_dir = root / ".muse"
        # Pre-create a branch ref.
        branch_ref = muse_dir / "refs" / "heads" / "feature" / "existing"
        branch_ref.parent.mkdir(parents=True, exist_ok=True)
        branch_ref.write_text("deadbeef")

        link_path = tmp_path.parent / "existing-branch-wt"
        info = add_worktree(root=root, link_path=link_path, branch="feature/existing")
        assert info.branch == "feature/existing"
        # Ref still has the correct commit.
        assert branch_ref.read_text() == "deadbeef"

    def test_worktree_add_returns_worktree_info(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _init_repo(tmp_path)
        link_path = tmp_path.parent / "info-wt"
        info = add_worktree(root=root, link_path=link_path, branch="main2")
        assert info.path == link_path
        assert info.slug != ""

    def test_worktree_add_path_already_exists_exits_1(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _init_repo(tmp_path)
        existing = tmp_path.parent / "already-exists"
        existing.mkdir()
        result = runner.invoke(
            cli, ["worktree", "add", str(existing), "feature/x"], env=_env(root)
        )
        assert result.exit_code == 1
        assert "already exists" in result.output.lower()

    def test_worktree_add_same_branch_twice_exits_1(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Cannot check out the same branch in two worktrees simultaneously."""
        root = _init_repo(tmp_path)
        first = tmp_path.parent / "first-wt"
        add_worktree(root=root, link_path=first, branch="feature/shared")

        second = tmp_path.parent / "second-wt"
        result = runner.invoke(
            cli, ["worktree", "add", str(second), "feature/shared"], env=_env(root)
        )
        assert result.exit_code == 1
        assert "already checked out" in result.output.lower()

    def test_worktree_add_main_branch_conflicts_exits_1(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Adding a worktree for the branch currently in main exits 1."""
        root = _init_repo(tmp_path)
        link_path = tmp_path.parent / "main-conflict-wt"
        result = runner.invoke(
            cli, ["worktree", "add", str(link_path), "main"], env=_env(root)
        )
        assert result.exit_code == 1
        assert "already checked out" in result.output.lower()

    def test_worktree_add_outside_repo_exits_2(self, tmp_path: pathlib.Path) -> None:
        result = runner.invoke(
            cli,
            ["worktree", "add", str(tmp_path / "new-wt"), "feature/x"],
            env={"MUSE_REPO_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# remove_worktree
# ---------------------------------------------------------------------------


class TestWorktreeRemove:
    def test_remove_deletes_directory_and_registration(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _init_repo(tmp_path)
        link_path = tmp_path.parent / "remove-me-wt"
        add_worktree(root=root, link_path=link_path, branch="feature/removeme")

        assert link_path.exists()
        remove_worktree(root=root, link_path=link_path)
        assert not link_path.exists()

        wt_dir = root / ".muse" / "worktrees"
        remaining = list(wt_dir.iterdir())
        assert len(remaining) == 0

    def test_remove_preserves_branch_ref_in_main(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _init_repo(tmp_path)
        link_path = tmp_path.parent / "branch-preserved-wt"
        add_worktree(root=root, link_path=link_path, branch="feature/keep")
        remove_worktree(root=root, link_path=link_path)

        # Branch ref still exists in main repo.
        ref_path = root / ".muse" / "refs" / "heads" / "feature" / "keep"
        assert ref_path.exists()

    def test_remove_main_worktree_exits_1(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["worktree", "remove", str(root)], env=_env(root)
        )
        assert result.exit_code == 1
        assert "cannot remove" in result.output.lower()

    def test_remove_unregistered_path_exits_1(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        unregistered = tmp_path.parent / "not-registered"
        unregistered.mkdir()
        result = runner.invoke(
            cli, ["worktree", "remove", str(unregistered)], env=_env(root)
        )
        assert result.exit_code == 1
        assert "not a registered" in result.output.lower()

    def test_remove_stale_directory_still_deregisters(
        self, tmp_path: pathlib.Path
    ) -> None:
        """remove works even if the linked directory is already gone."""
        root = _init_repo(tmp_path)
        link_path = tmp_path.parent / "gone-wt"
        add_worktree(root=root, link_path=link_path, branch="feature/gone")
        # Simulate the directory disappearing externally.
        import shutil
        shutil.rmtree(link_path)

        # remove should still deregister cleanly.
        remove_worktree(root=root, link_path=link_path)
        wt_dir = root / ".muse" / "worktrees"
        assert list(wt_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# prune_worktrees
# ---------------------------------------------------------------------------


class TestWorktreePrune:
    def test_prune_removes_stale_registration(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        link_path = tmp_path.parent / "stale-wt"
        add_worktree(root=root, link_path=link_path, branch="feature/stale")
        # Externally delete the linked directory.
        import shutil
        shutil.rmtree(link_path)

        pruned = prune_worktrees(root=root)
        assert str(link_path) in pruned

        wt_dir = root / ".muse" / "worktrees"
        assert list(wt_dir.iterdir()) == []

    def test_prune_leaves_live_worktrees_intact(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        live = tmp_path.parent / "live-wt"
        add_worktree(root=root, link_path=live, branch="feature/live")

        pruned = prune_worktrees(root=root)
        assert len(pruned) == 0

        worktrees = list_worktrees(root)
        assert len(worktrees) == 2

    def test_prune_empty_worktrees_dir_returns_empty(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _init_repo(tmp_path)
        pruned = prune_worktrees(root=root)
        assert pruned == []

    def test_prune_mixed_live_and_stale(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        live = tmp_path.parent / "live-wt2"
        stale = tmp_path.parent / "stale-wt2"
        add_worktree(root=root, link_path=live, branch="feature/live2")
        add_worktree(root=root, link_path=stale, branch="feature/stale2")
        import shutil
        shutil.rmtree(stale)

        pruned = prune_worktrees(root=root)
        assert str(stale) in pruned
        assert str(live) not in pruned
        # Live worktree still registered.
        worktrees = list_worktrees(root)
        assert any(w.path == live for w in worktrees)


# ---------------------------------------------------------------------------
# CLI integration — Typer CliRunner
# ---------------------------------------------------------------------------


class TestWorktreeCLI:
    def test_worktree_list_shows_main(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        result = runner.invoke(cli, ["worktree", "list"], env=_env(root))
        assert result.exit_code == 0
        assert "[main]" in result.output
        assert "main" in result.output

    def test_worktree_list_shows_linked(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        link_path = tmp_path.parent / "cli-linked-wt"
        add_worktree(root=root, link_path=link_path, branch="feature/cli")
        result = runner.invoke(cli, ["worktree", "list"], env=_env(root))
        assert result.exit_code == 0
        assert "feature/cli" in result.output

    def test_worktree_add_cli_success(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        link_path = tmp_path.parent / "cli-add-wt"
        result = runner.invoke(
            cli,
            ["worktree", "add", str(link_path), "feature/cli-add"],
            env=_env(root),
        )
        assert result.exit_code == 0, result.output
        assert "created" in result.output.lower()
        assert link_path.is_dir()

    def test_worktree_remove_cli_success(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        link_path = tmp_path.parent / "cli-remove-wt"
        add_worktree(root=root, link_path=link_path, branch="feature/cli-rm")
        result = runner.invoke(
            cli, ["worktree", "remove", str(link_path)], env=_env(root)
        )
        assert result.exit_code == 0, result.output
        assert "removed" in result.output.lower()
        assert not link_path.exists()

    def test_worktree_prune_cli_no_stale(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        result = runner.invoke(cli, ["worktree", "prune"], env=_env(root))
        assert result.exit_code == 0
        assert "no stale" in result.output.lower()

    def test_worktree_prune_cli_removes_stale(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        link_path = tmp_path.parent / "cli-stale-wt"
        add_worktree(root=root, link_path=link_path, branch="feature/cli-stale")
        import shutil
        shutil.rmtree(link_path)
        result = runner.invoke(cli, ["worktree", "prune"], env=_env(root))
        assert result.exit_code == 0
        assert "pruned" in result.output.lower()

    def test_worktree_outside_repo_exits_2(self, tmp_path: pathlib.Path) -> None:
        result = runner.invoke(
            cli, ["worktree", "list"], env={"MUSE_REPO_ROOT": str(tmp_path)}
        )
        assert result.exit_code == 2
