"""muse tag — attach and query music-semantic tags on commits.

Subcommands
-----------
``muse tag add <tag> [<commit>]``
    Attach a tag to a commit (default: branch HEAD).

``muse tag remove <tag> [<commit>]``
    Remove a tag from a commit.

``muse tag list [<commit>]``
    List all tags on a commit (default: branch HEAD).

``muse tag search <tag>``
    List commits in the current repo that carry a given tag.
    Supports prefix matching so ``emotion:`` finds all emotion tags.

Tag namespaces
--------------
Tags are free-form strings. Conventional namespace prefixes:

- ``emotion:*``  — emotional character, e.g. ``emotion:melancholic``
- ``stage:*``    — production stage, e.g. ``stage:rough-mix``
- ``ref:*``      — reference source, e.g. ``ref:beatles``
- ``key:*``      — musical key, e.g. ``key:Am``
- ``tempo:*``    — tempo annotation, e.g. ``tempo:120bpm``
- free-form      — any other descriptive label
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from typing import Optional

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit, MuseCliTag

logger = logging.getLogger(__name__)

app = typer.Typer(no_args_is_help=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_commit_id(root: pathlib.Path, commit_ref: str | None) -> str:
    """Resolve a commit reference to a full commit ID.

    When *commit_ref* is ``None`` the current branch HEAD is used.
    Raises ``typer.Exit`` with ``USER_ERROR`` if the ref cannot be resolved.
    """
    muse_dir = root / ".muse"
    if commit_ref is not None:
        return commit_ref

    # Resolve HEAD from .muse/HEAD → .muse/refs/heads/<branch>
    head_file = muse_dir / "HEAD"
    if not head_file.exists():
        typer.echo("❌ .muse/HEAD not found — is this a valid Muse repository?")
        raise typer.Exit(code=ExitCode.REPO_NOT_FOUND)

    head_ref = head_file.read_text().strip()  # "refs/heads/main"
    ref_path = muse_dir / pathlib.Path(head_ref)
    if not ref_path.exists() or not ref_path.read_text().strip():
        typer.echo("❌ No commits yet on this branch. Create a commit first.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    return ref_path.read_text().strip()


async def _get_repo_id(root: pathlib.Path) -> str:
    """Read repo_id from .muse/repo.json."""
    repo_json = root / ".muse" / "repo.json"
    data: dict[str, str] = json.loads(repo_json.read_text())
    return data["repo_id"]


# ---------------------------------------------------------------------------
# Testable async cores
# ---------------------------------------------------------------------------


async def _tag_add_async(
    *,
    tag: str,
    commit_ref: str | None,
    root: pathlib.Path,
    session: AsyncSession,
) -> None:
    """Attach *tag* to the target commit.

    No-ops silently when the same tag already exists on that commit so that
    ``muse tag add`` is idempotent and safe to re-run.
    """
    commit_id = _resolve_commit_id(root, commit_ref)
    repo_id = await _get_repo_id(root)

    # Guard: commit must exist in the DB
    commit_row = await session.get(MuseCliCommit, commit_id)
    if commit_row is None:
        typer.echo(f"❌ Commit {commit_id[:8]} not found in database.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Idempotency check
    existing = await session.execute(
        select(MuseCliTag).where(
            MuseCliTag.repo_id == repo_id,
            MuseCliTag.commit_id == commit_id,
            MuseCliTag.tag == tag,
        )
    )
    if existing.scalar_one_or_none() is not None:
        typer.echo(f"⚠️  Tag {tag!r} already exists on commit {commit_id[:8]} — skipped.")
        return

    session.add(MuseCliTag(repo_id=repo_id, commit_id=commit_id, tag=tag))
    typer.echo(f"✅ Tagged commit {commit_id[:8]} with {tag!r}")
    logger.info("✅ muse tag add %r on commit %s", tag, commit_id[:8])


async def _tag_remove_async(
    *,
    tag: str,
    commit_ref: str | None,
    root: pathlib.Path,
    session: AsyncSession,
) -> None:
    """Remove *tag* from the target commit.

    Exits with ``USER_ERROR`` when the tag does not exist.
    """
    commit_id = _resolve_commit_id(root, commit_ref)
    repo_id = await _get_repo_id(root)

    existing_result = await session.execute(
        select(MuseCliTag).where(
            MuseCliTag.repo_id == repo_id,
            MuseCliTag.commit_id == commit_id,
            MuseCliTag.tag == tag,
        )
    )
    existing_tag = existing_result.scalar_one_or_none()
    if existing_tag is None:
        typer.echo(f"❌ Tag {tag!r} not found on commit {commit_id[:8]}.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    await session.delete(existing_tag)

    typer.echo(f"✅ Removed tag {tag!r} from commit {commit_id[:8]}")
    logger.info("✅ muse tag remove %r from commit %s", tag, commit_id[:8])


async def _tag_list_async(
    *,
    commit_ref: str | None,
    root: pathlib.Path,
    session: AsyncSession,
) -> list[str]:
    """Return the sorted list of tags on the target commit."""
    commit_id = _resolve_commit_id(root, commit_ref)
    repo_id = await _get_repo_id(root)

    result = await session.execute(
        select(MuseCliTag.tag)
        .where(
            MuseCliTag.repo_id == repo_id,
            MuseCliTag.commit_id == commit_id,
        )
        .order_by(MuseCliTag.tag)
    )
    tags = [row[0] for row in result.all()]

    if not tags:
        typer.echo(f"No tags on commit {commit_id[:8]}.")
    else:
        typer.echo(f"Tags on commit {commit_id[:8]}:")
        for t in tags:
            typer.echo(f"  {t}")

    return tags


async def _tag_search_async(
    *,
    tag: str,
    root: pathlib.Path,
    session: AsyncSession,
) -> list[tuple[str, str]]:
    """Return (commit_id, tag) pairs matching *tag* within the current repo.

    Supports prefix matching: passing ``emotion:`` returns all emotion tags.
    An exact tag string matches only that exact tag.
    """
    repo_id = await _get_repo_id(root)

    if tag.endswith(":"):
        # Prefix match — find all tags in the namespace
        result = await session.execute(
            select(MuseCliTag.commit_id, MuseCliTag.tag)
            .where(
                MuseCliTag.repo_id == repo_id,
                MuseCliTag.tag.like(f"{tag}%"),
            )
            .order_by(MuseCliTag.commit_id, MuseCliTag.tag)
        )
    else:
        result = await session.execute(
            select(MuseCliTag.commit_id, MuseCliTag.tag)
            .where(
                MuseCliTag.repo_id == repo_id,
                MuseCliTag.tag == tag,
            )
            .order_by(MuseCliTag.commit_id)
        )

    pairs = [(row[0], row[1]) for row in result.all()]

    if not pairs:
        typer.echo(f"No commits tagged with {tag!r}.")
    else:
        typer.echo(f"Commits tagged with {tag!r}:")
        for commit_id, matched_tag in pairs:
            typer.echo(f"  {commit_id[:8]}  {matched_tag}")

    return pairs


# ---------------------------------------------------------------------------
# Typer subcommands
# ---------------------------------------------------------------------------


@app.command("add")
def tag_add(
    tag: str = typer.Argument(..., help="Tag string (e.g. emotion:melancholic, stage:rough-mix)."),
    commit: Optional[str] = typer.Argument(
        None, help="Commit ID to tag. Defaults to current branch HEAD."
    ),
) -> None:
    """Attach a music-semantic tag to a commit."""
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _tag_add_async(tag=tag, commit_ref=commit, root=root, session=session)

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse tag add failed: {exc}")
        logger.error("❌ muse tag add error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)


@app.command("remove")
def tag_remove(
    tag: str = typer.Argument(..., help="Tag string to remove."),
    commit: Optional[str] = typer.Argument(
        None, help="Commit ID to untag. Defaults to current branch HEAD."
    ),
) -> None:
    """Remove a tag from a commit."""
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _tag_remove_async(tag=tag, commit_ref=commit, root=root, session=session)

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse tag remove failed: {exc}")
        logger.error("❌ muse tag remove error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)


@app.command("list")
def tag_list(
    commit: Optional[str] = typer.Argument(
        None, help="Commit ID to inspect. Defaults to current branch HEAD."
    ),
) -> None:
    """List all tags on a commit."""
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _tag_list_async(commit_ref=commit, root=root, session=session)

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse tag list failed: {exc}")
        logger.error("❌ muse tag list error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)


@app.command("search")
def tag_search(
    tag: str = typer.Argument(
        ...,
        help=(
            "Tag or namespace prefix to search for. "
            "Use 'emotion:' (with colon) for prefix search; "
            "exact string for exact match."
        ),
    ),
) -> None:
    """Search commits by tag or tag-namespace prefix."""
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _tag_search_async(tag=tag, root=root, session=session)

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse tag search failed: {exc}")
        logger.error("❌ muse tag search error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
