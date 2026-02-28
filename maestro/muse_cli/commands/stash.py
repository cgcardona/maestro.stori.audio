"""muse stash — temporarily shelve uncommitted muse-work/ changes.

Stash is a per-producer scratch pad: changes in muse-work/ are saved to
``.muse/stash/`` (filesystem, no DB table) and HEAD is restored so you can
start clean.  Later, ``muse stash pop`` brings back the shelved state.

Subcommands
-----------
push   (default) — save muse-work/ state; restore HEAD snapshot
pop             — apply most recent stash, remove it from the stack
apply           — apply a stash without removing it
list            — list all stash entries
drop            — remove a specific entry
clear           — remove all entries

Usage examples::

    muse stash                        # push (save + restore HEAD)
    muse stash push -m "chorus WIP"   # push with a label
    muse stash push --track drums     # scope to drums/ files only
    muse stash pop                    # apply most recent, drop it
    muse stash apply stash@{2}        # apply index 2 without dropping
    muse stash list                   # show all stash entries
    muse stash drop stash@{1}         # remove index 1
    muse stash clear                  # remove all (with confirmation)
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from typing import Optional

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_stash import (
    StashApplyResult,
    StashPushResult,
    apply_stash,
    clear_stash,
    drop_stash,
    list_stash,
    push_stash,
)

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="stash",
    help="Temporarily shelve uncommitted muse-work/ changes.",
    no_args_is_help=False,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_stash_ref(ref: str) -> int:
    """Parse ``stash@{N}`` or a bare integer into a 0-based index.

    Accepts ``stash@{0}``, ``stash@{2}``, or just ``0``, ``2``.

    Raises:
        typer.Exit: When *ref* cannot be parsed.
    """
    import re

    match = re.fullmatch(r"stash@\{(\d+)\}", ref.strip())
    if match:
        return int(match.group(1))
    try:
        return int(ref.strip())
    except ValueError:
        typer.echo(f"❌ Invalid stash reference: {ref!r}. Expected stash@{{N}} or N.")
        raise typer.Exit(code=ExitCode.USER_ERROR)


async def _get_head_manifest(
    root: pathlib.Path,
) -> dict[str, str] | None:
    """Return the snapshot manifest for the current HEAD commit.

    Returns ``None`` when the branch has no commits or the DB is unreachable.
    """
    from maestro.muse_cli.db import get_commit_snapshot_manifest
    from maestro.muse_cli.models import MuseCliCommit

    import json as _json

    muse_dir = root / ".muse"

    try:
        repo_data: dict[str, str] = _json.loads((muse_dir / "repo.json").read_text())
        repo_id = repo_data["repo_id"]

        head_ref = (muse_dir / "HEAD").read_text().strip()
        ref_path = muse_dir / pathlib.Path(head_ref)
        if not ref_path.exists():
            return None
        commit_id = ref_path.read_text().strip()
        if not commit_id:
            return None

        from sqlalchemy.future import select

        async with open_session() as session:
            result = await session.execute(
                select(MuseCliCommit).where(
                    MuseCliCommit.repo_id == repo_id,
                    MuseCliCommit.commit_id == commit_id,
                )
            )
            commit = result.scalar_one_or_none()
            if commit is None:
                return None
            manifest = await get_commit_snapshot_manifest(session, commit_id)
            return manifest
    except Exception as exc:
        logger.warning("⚠️ Could not load HEAD manifest: %s", exc)
        return None


# ---------------------------------------------------------------------------
# push (default command)
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def stash_default(
    ctx: typer.Context,
    message: Optional[str] = typer.Option(
        None,
        "--message",
        "-m",
        help="Label for this stash entry.",
    ),
    track: Optional[str] = typer.Option(
        None,
        "--track",
        help="Scope the stash to files under tracks/<TRACK>/.",
    ),
    section: Optional[str] = typer.Option(
        None,
        "--section",
        help="Scope the stash to files under sections/<SECTION>/.",
    ),
) -> None:
    """Save muse-work/ changes and restore HEAD snapshot (default: push)."""
    if ctx.invoked_subcommand is not None:
        return

    root = require_repo()

    # Load HEAD manifest to restore working tree after stashing.
    head_manifest: dict[str, str] | None = asyncio.run(_get_head_manifest(root))

    try:
        result = push_stash(
            root,
            message=message,
            track=track,
            section=section,
            head_manifest=head_manifest,
        )
    except Exception as exc:
        typer.echo(f"❌ muse stash push failed: {exc}")
        logger.error("❌ muse stash push error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if result.files_stashed == 0:
        typer.echo("⚠️  No local changes to stash.")
        return

    typer.echo(f"Saved working directory and index state {result.stash_ref}")
    typer.echo(f"{result.message}")

    if result.missing_head:
        typer.echo(
            "⚠️  Some HEAD files could not be restored (object store incomplete):\n"
            + "\n".join(f"   {p}" for p in result.missing_head)
        )


# ---------------------------------------------------------------------------
# push subcommand (explicit)
# ---------------------------------------------------------------------------


@app.command("push")
def stash_push(
    message: Optional[str] = typer.Option(
        None,
        "--message",
        "-m",
        help="Label for this stash entry.",
    ),
    track: Optional[str] = typer.Option(
        None,
        "--track",
        help="Scope the stash to files under tracks/<TRACK>/.",
    ),
    section: Optional[str] = typer.Option(
        None,
        "--section",
        help="Scope the stash to files under sections/<SECTION>/.",
    ),
) -> None:
    """Save muse-work/ changes and restore HEAD snapshot."""
    root = require_repo()

    head_manifest: dict[str, str] | None = asyncio.run(_get_head_manifest(root))

    try:
        result = push_stash(
            root,
            message=message,
            track=track,
            section=section,
            head_manifest=head_manifest,
        )
    except Exception as exc:
        typer.echo(f"❌ muse stash push failed: {exc}")
        logger.error("❌ muse stash push error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if result.files_stashed == 0:
        typer.echo("⚠️  No local changes to stash.")
        return

    typer.echo(f"Saved working directory and index state {result.stash_ref}")
    typer.echo(f"{result.message}")

    if result.missing_head:
        typer.echo(
            "⚠️  Some HEAD files could not be restored (object store incomplete):\n"
            + "\n".join(f"   {p}" for p in result.missing_head)
        )


# ---------------------------------------------------------------------------
# pop
# ---------------------------------------------------------------------------


@app.command("pop")
def stash_pop(
    stash_ref: str = typer.Argument(
        "stash@{0}",
        help="Which stash entry to pop (default: stash@{0}).",
    ),
) -> None:
    """Apply the most recent stash and remove it from the stack."""
    root = require_repo()
    index = _parse_stash_ref(stash_ref)

    try:
        result = apply_stash(root, index, drop=True)
    except IndexError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except Exception as exc:
        typer.echo(f"❌ muse stash pop failed: {exc}")
        logger.error("❌ muse stash pop error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    typer.echo(f"✅ Applied {result.stash_ref}: {result.message}")
    typer.echo(f"   {result.files_applied} file(s) restored.")

    if result.missing:
        typer.echo(
            "⚠️  Some files could not be restored (object store incomplete):\n"
            + "\n".join(f"   missing: {p}" for p in result.missing)
        )

    typer.echo(f"Dropped {result.stash_ref}")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


@app.command("apply")
def stash_apply(
    stash_ref: str = typer.Argument(
        "stash@{0}",
        help="Stash reference to apply (e.g. stash@{0}, stash@{2}).",
    ),
) -> None:
    """Apply a stash entry without removing it from the stack."""
    root = require_repo()
    index = _parse_stash_ref(stash_ref)

    try:
        result = apply_stash(root, index, drop=False)
    except IndexError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except Exception as exc:
        typer.echo(f"❌ muse stash apply failed: {exc}")
        logger.error("❌ muse stash apply error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    typer.echo(f"✅ Applied {result.stash_ref}: {result.message}")
    typer.echo(f"   {result.files_applied} file(s) restored.")

    if result.missing:
        typer.echo(
            "⚠️  Some files could not be restored (object store incomplete):\n"
            + "\n".join(f"   missing: {p}" for p in result.missing)
        )


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@app.command("list")
def stash_list() -> None:
    """List all stash entries."""
    root = require_repo()

    try:
        entries = list_stash(root)
    except Exception as exc:
        typer.echo(f"❌ muse stash list failed: {exc}")
        logger.error("❌ muse stash list error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if not entries:
        typer.echo("No stash entries.")
        return

    for entry in entries:
        typer.echo(f"stash@{{{entry.index}}}: On {entry.branch}: {entry.message}")


# ---------------------------------------------------------------------------
# drop
# ---------------------------------------------------------------------------


@app.command("drop")
def stash_drop(
    stash_ref: str = typer.Argument(
        "stash@{0}",
        help="Stash reference to drop (e.g. stash@{0}, stash@{2}).",
    ),
) -> None:
    """Remove a specific stash entry without applying it."""
    root = require_repo()
    index = _parse_stash_ref(stash_ref)

    try:
        entry = drop_stash(root, index)
    except IndexError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except Exception as exc:
        typer.echo(f"❌ muse stash drop failed: {exc}")
        logger.error("❌ muse stash drop error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    typer.echo(f"✅ Dropped stash@{{{entry.index}}}: {entry.message}")


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


@app.command("clear")
def stash_clear(
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt.",
    ),
) -> None:
    """Remove all stash entries."""
    root = require_repo()

    if not yes:
        confirmed = typer.confirm(
            "⚠️  This will permanently remove ALL stash entries. Proceed?",
            default=False,
        )
        if not confirmed:
            typer.echo("Aborted.")
            raise typer.Exit(code=ExitCode.SUCCESS)

    try:
        count = clear_stash(root)
    except Exception as exc:
        typer.echo(f"❌ muse stash clear failed: {exc}")
        logger.error("❌ muse stash clear error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if count == 0:
        typer.echo("No stash entries to clear.")
    else:
        typer.echo(f"✅ Cleared {count} stash entr{'y' if count == 1 else 'ies'}.")
