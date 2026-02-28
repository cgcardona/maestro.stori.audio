"""muse worktree — manage local Muse worktrees from the CLI.

Muse worktrees let a producer work on two arrangements simultaneously — one
worktree for "radio edit" mixing, another for "extended club version" — without
switching branches back and forth.

Architecture
------------
Main repo (.muse/ directory):
  .muse/worktrees/<slug>/path    — absolute path to the linked worktree directory
  .muse/worktrees/<slug>/branch  — branch name checked out there
  .muse/objects/                 — shared content-addressed object store

Linked worktree directory:
  .muse              — plain text file: "gitdir: <abs-path-to-main-.muse>"
  muse-work/         — per-worktree working files (independent)

The same-branch exclusivity constraint mirrors git: a branch can only be
checked out in one worktree at a time.  Attempting to add a second worktree
on an already-checked-out branch exits with code 1.

Subcommands
-----------
  muse worktree add <path> [branch]  — create a linked worktree
  muse worktree remove <path>        — delete a linked worktree
  muse worktree list                 — show all worktrees
  muse worktree prune                — remove stale registrations
"""
from __future__ import annotations

import dataclasses
import logging
import pathlib
import re

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.errors import ExitCode

logger = logging.getLogger(__name__)

app = typer.Typer(invoke_without_command=True, help="Manage local Muse worktrees.")


# ---------------------------------------------------------------------------
# Result types — consumed by agents and tests
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class WorktreeInfo:
    """A single worktree entry returned by ``list_worktrees``.

    ``is_main`` is True for the primary repo directory (which owns the
    ``.muse/`` directory tree), False for linked worktrees.

    ``slug`` is the sanitized key used inside ``.muse/worktrees/``.  It is
    the empty string for the main worktree.

    ``branch`` is the symbolic name of the checked-out branch (e.g.
    ``"main"`` or ``"feature/club-mix"``).  When HEAD is detached (no branch
    ref), the value is ``"(detached)"``.

    ``head_commit`` is the current HEAD commit SHA for the worktree (empty
    string when the branch has no commits yet).
    """

    path: pathlib.Path
    branch: str
    head_commit: str
    is_main: bool
    slug: str


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------


def _slugify(path: pathlib.Path) -> str:
    """Derive a filesystem-safe registration key from a worktree path.

    Converts the absolute path to a short ASCII slug by replacing every
    non-alphanumeric run with a single hyphen and stripping leading/trailing
    hyphens.  Collision is extremely unlikely given unique absolute paths.
    """
    return re.sub(r"[^a-zA-Z0-9]+", "-", str(path.resolve())).strip("-")[:64]


def _worktrees_dir(muse_dir: pathlib.Path) -> pathlib.Path:
    """Return ``<main-repo>/.muse/worktrees/``, creating it if absent."""
    wt_dir = muse_dir / "worktrees"
    wt_dir.mkdir(parents=True, exist_ok=True)
    return wt_dir


# ---------------------------------------------------------------------------
# Core — testable, no Typer coupling
# ---------------------------------------------------------------------------


def _read_head_branch(muse_dir: pathlib.Path) -> str:
    """Return the current branch name from a ``.muse/HEAD`` file.

    Returns ``"(detached)"`` when HEAD is a bare commit rather than a
    symbolic ref.
    """
    head_path = muse_dir / "HEAD"
    if not head_path.exists():
        return "(detached)"
    text = head_path.read_text().strip()
    if text.startswith("refs/heads/"):
        return text[len("refs/heads/"):]
    return "(detached)"


def _read_head_commit(muse_dir: pathlib.Path) -> str:
    """Return the commit SHA that HEAD resolves to, or empty string."""
    head_path = muse_dir / "HEAD"
    if not head_path.exists():
        return ""
    text = head_path.read_text().strip()
    if text.startswith("refs/heads/"):
        ref_path = muse_dir / text
        if ref_path.exists():
            return ref_path.read_text().strip()
        return ""
    # Detached HEAD — text is the commit SHA directly.
    return text


def _all_checked_out_branches(root: pathlib.Path) -> list[str]:
    """Return all branch names currently checked out across all worktrees.

    Includes the main worktree plus every registered linked worktree.  Used
    to enforce the single-checkout-per-branch constraint.
    """
    muse_dir = root / ".muse"
    branches: list[str] = []

    # Main worktree.
    main_branch = _read_head_branch(muse_dir)
    if main_branch != "(detached)":
        branches.append(main_branch)

    # Linked worktrees.
    wt_dir = muse_dir / "worktrees"
    if not wt_dir.is_dir():
        return branches
    for entry in wt_dir.iterdir():
        if not entry.is_dir():
            continue
        branch_file = entry / "branch"
        if branch_file.exists():
            b = branch_file.read_text().strip()
            if b:
                branches.append(b)

    return branches


def list_worktrees(root: pathlib.Path) -> list[WorktreeInfo]:
    """Return all worktrees: main first, then linked (in registration order).

    The main worktree is always first.  Linked worktrees are included even
    when their target directory no longer exists (``path.exists()`` may be
    False for stale entries — callers should check before accessing files).

    Args:
        root: Repository root (parent of ``.muse/``).

    Returns:
        List of :class:`WorktreeInfo` objects.
    """
    muse_dir = root / ".muse"
    result: list[WorktreeInfo] = []

    # Main worktree.
    result.append(
        WorktreeInfo(
            path=root,
            branch=_read_head_branch(muse_dir),
            head_commit=_read_head_commit(muse_dir),
            is_main=True,
            slug="",
        )
    )

    # Linked worktrees.
    wt_dir = muse_dir / "worktrees"
    if not wt_dir.is_dir():
        return result

    for entry in sorted(wt_dir.iterdir()):
        if not entry.is_dir():
            continue
        path_file = entry / "path"
        branch_file = entry / "branch"
        if not path_file.exists():
            continue
        linked_path = pathlib.Path(path_file.read_text().strip())
        branch = branch_file.read_text().strip() if branch_file.exists() else "(detached)"

        # Read HEAD commit from the linked worktree's own HEAD file if present.
        linked_muse_dir = linked_path / ".muse"
        head_commit = ""
        if linked_muse_dir.is_dir():
            head_commit = _read_head_commit(linked_muse_dir)

        result.append(
            WorktreeInfo(
                path=linked_path,
                branch=branch,
                head_commit=head_commit,
                is_main=False,
                slug=entry.name,
            )
        )

    return result


def add_worktree(
    *,
    root: pathlib.Path,
    link_path: pathlib.Path,
    branch: str,
) -> WorktreeInfo:
    """Create a new linked worktree at ``link_path`` checked out to ``branch``.

    Enforces same-branch exclusivity: if ``branch`` is already checked out
    in any worktree (main or linked), raises ``typer.Exit(1)``.

    Creates the branch ref in the main repo if it does not yet exist,
    mirroring ``git worktree add --orphan``-like behaviour so that producers
    can start a completely fresh arrangement on a new branch.

    Layout of the new directory::

        <link_path>/
          .muse          (plain-text file: "gitdir: <main-repo>/.muse")
          muse-work/     (empty working directory)

    Registration in main repo::

        .muse/worktrees/<slug>/path    → abs path to link_path
        .muse/worktrees/<slug>/branch  → branch name

    Args:
        root:       Repository root (parent of ``.muse/``).
        link_path:  Absolute path for the new linked worktree directory.
        branch:     Branch name to check out.  Created from HEAD if absent.

    Returns:
        :class:`WorktreeInfo` describing the newly created worktree.

    Raises:
        typer.Exit(1): branch already checked out, path already exists, or
                       link_path is inside the main repo's ``.muse/`` tree.
    """
    muse_dir = root / ".muse"
    link_path = link_path.resolve()

    # Guard: link_path must not be the main repo itself or inside .muse/.
    if link_path == root or link_path.is_relative_to(muse_dir):
        typer.echo("❌ Worktree path must be outside the main repository.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Guard: path must not already exist.
    if link_path.exists():
        typer.echo(f"❌ Path already exists: {link_path}\n"
                   "   Choose a different location for the linked worktree.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Guard: branch exclusivity.
    checked_out = _all_checked_out_branches(root)
    if branch in checked_out:
        typer.echo(
            f"❌ Branch '{branch}' is already checked out in another worktree.\n"
            "   A branch can only be active in one worktree at a time."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Ensure the branch ref exists in the main repo (create from HEAD if not).
    ref_path = muse_dir / "refs" / "heads" / branch
    if not ref_path.exists():
        head_commit = _read_head_commit(muse_dir)
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(head_commit)
        logger.info("✅ Created branch ref %r (from HEAD=%s)", branch, head_commit[:8] or "(empty)")

    # Create the linked worktree directory structure.
    link_path.mkdir(parents=True)
    (link_path / "muse-work").mkdir()

    # Write the .muse gitdir file so the linked worktree points back to main.
    slug = _slugify(link_path)
    registration_dir = _worktrees_dir(muse_dir) / slug
    registration_dir.mkdir(parents=True, exist_ok=True)

    gitdir_content = f"gitdir: {muse_dir}\n"
    (link_path / ".muse").write_text(gitdir_content)

    # Write HEAD and branch ref inside a minimal .muse/ inside the linked dir.
    # Wait — per design, .muse is a *file* pointing at the main repo.
    # The linked worktree's HEAD lives in the registration entry.
    # For list_worktrees to read the HEAD commit we also write it to the
    # registration dir so no filesystem traversal to the linked path is needed.
    head_commit = ref_path.read_text().strip()
    (registration_dir / "path").write_text(str(link_path))
    (registration_dir / "branch").write_text(branch)

    logger.info("✅ muse worktree add %s (branch=%r, slug=%r)", link_path, branch, slug)

    return WorktreeInfo(
        path=link_path,
        branch=branch,
        head_commit=head_commit,
        is_main=False,
        slug=slug,
    )


def remove_worktree(*, root: pathlib.Path, link_path: pathlib.Path) -> None:
    """Remove a linked worktree: delete its directory and de-register it.

    The main worktree cannot be removed.  Only the linked worktree's own
    directory and its registration entry are removed — the shared objects
    store and branch refs in the main repo are left intact, so the branch
    remains accessible from the main worktree.

    Args:
        root:       Repository root (parent of ``.muse/``).
        link_path:  Path to the linked worktree to remove.

    Raises:
        typer.Exit(1): path is not a registered linked worktree or is the
                       main repo root.
    """
    muse_dir = root / ".muse"
    link_path = link_path.resolve()

    if link_path == root:
        typer.echo("❌ Cannot remove the main worktree.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Find the registration entry for this path.
    wt_dir = muse_dir / "worktrees"
    registration_dir: pathlib.Path | None = None
    if wt_dir.is_dir():
        for entry in wt_dir.iterdir():
            path_file = entry / "path"
            if path_file.exists() and pathlib.Path(path_file.read_text().strip()) == link_path:
                registration_dir = entry
                break

    if registration_dir is None:
        typer.echo(
            f"❌ '{link_path}' is not a registered linked worktree.\n"
            "   Run 'muse worktree list' to see registered worktrees."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Remove the linked worktree directory.
    if link_path.exists():
        _remove_tree(link_path)

    # Remove the registration entry.
    _remove_tree(registration_dir)

    logger.info("✅ muse worktree remove %s", link_path)


def prune_worktrees(*, root: pathlib.Path) -> list[str]:
    """Remove stale worktree registrations (directory no longer exists).

    Scans ``.muse/worktrees/`` for entries whose target path is absent and
    removes those registration directories.  This is a local-only operation
    that does not touch branch refs or the objects store.

    Args:
        root: Repository root (parent of ``.muse/``).

    Returns:
        List of absolute path strings that were pruned.
    """
    muse_dir = root / ".muse"
    wt_dir = muse_dir / "worktrees"
    pruned: list[str] = []

    if not wt_dir.is_dir():
        return pruned

    for entry in list(wt_dir.iterdir()):
        if not entry.is_dir():
            continue
        path_file = entry / "path"
        if not path_file.exists():
            _remove_tree(entry)
            pruned.append(str(entry))
            continue
        target = pathlib.Path(path_file.read_text().strip())
        if not target.exists():
            pruned.append(str(target))
            _remove_tree(entry)
            logger.info("⚠️  muse worktree prune: removed stale entry %s → %s", entry.name, target)

    return pruned


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _remove_tree(path: pathlib.Path) -> None:
    """Recursively remove a directory tree or a single file.

    This is a pure Python implementation to avoid shelling out.  We intentionally
    do not use ``shutil.rmtree`` to remain dependency-free (shutil is stdlib but
    the explicit implementation is clearer in tests and error traces).
    """
    if path.is_file() or path.is_symlink():
        path.unlink()
        return
    for child in path.iterdir():
        _remove_tree(child)
    path.rmdir()


# ---------------------------------------------------------------------------
# Typer commands
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def worktree_callback(ctx: typer.Context) -> None:
    """Manage local Muse worktrees.

    Run ``muse worktree --help`` to see available subcommands.
    """
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=ExitCode.SUCCESS)


@app.command("add")
def worktree_add(
    path: str = typer.Argument(..., help="Directory to create the linked worktree in."),
    branch: str = typer.Argument(
        ...,
        help="Branch name to check out.  Created from HEAD if it does not exist.",
    ),
) -> None:
    """Create a new linked worktree at PATH checked out to BRANCH.

    The new directory is created at PATH with an independent ``muse-work/``
    working directory.  The shared ``.muse/objects/`` store remains in the
    main repository.

    Example::

        muse worktree add ../club-mix feature/extended
    """
    root = require_repo()
    link_path = pathlib.Path(path).expanduser().resolve()
    info = add_worktree(root=root, link_path=link_path, branch=branch)
    typer.echo(f"✅ Linked worktree '{info.branch}' created at {info.path}")


@app.command("remove")
def worktree_remove(
    path: str = typer.Argument(..., help="Path of the linked worktree to remove."),
) -> None:
    """Remove a linked worktree and de-register it.

    The branch ref and shared objects store are preserved.  Only the linked
    worktree directory and its registration entry are deleted.

    Example::

        muse worktree remove ../club-mix
    """
    root = require_repo()
    link_path = pathlib.Path(path).expanduser().resolve()
    remove_worktree(root=root, link_path=link_path)
    typer.echo(f"✅ Worktree at {link_path} removed.")


@app.command("list")
def worktree_list() -> None:
    """List all worktrees: main and linked, with path, branch, and HEAD.

    Example output::

        /path/to/project        [main]  branch: main          head: a1b2c3d4
        /path/to/club-mix               branch: feature/club  head: a1b2c3d4
    """
    root = require_repo()
    worktrees = list_worktrees(root)
    for wt in worktrees:
        tag = "[main]" if wt.is_main else "      "
        head = wt.head_commit[:8] if wt.head_commit else "(no commits)"
        typer.echo(f"{wt.path!s:<50}  {tag}  branch: {wt.branch:<30}  head: {head}")


@app.command("prune")
def worktree_prune() -> None:
    """Remove stale worktree registrations (directory no longer exists).

    Scans ``.muse/worktrees/`` for entries whose target directory is absent
    and removes them.  Safe to run any time — no data loss risk.

    Example::

        muse worktree prune
    """
    root = require_repo()
    pruned = prune_worktrees(root=root)
    if pruned:
        for p in pruned:
            typer.echo(f"⚠️  Pruned stale worktree: {p}")
        typer.echo(f"✅ Pruned {len(pruned)} stale worktree registration(s).")
    else:
        typer.echo("✅ No stale worktrees found.")
