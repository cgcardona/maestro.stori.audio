"""muse update-ref — write or delete a ref (branch or tag pointer).

Plumbing command that directly updates ``refs/heads/<branch>`` or
``refs/tags/<tag>`` inside the ``.muse/`` directory.  Mirrors
``git update-ref`` and is primarily intended for scripting scenarios
where a higher-level command (checkout, merge) is too heavy.

Behaviour
---------
``muse update-ref <ref> <new-value>``
    Write *new-value* (a commit_id) to the ref file.
    Validates that the commit exists in ``muse_cli_commits``.

``muse update-ref <ref> <new-value> --old-value <expected>``
    Compare-and-swap (CAS): only update if the current ref value matches
    *expected*.  Safe for scripting under concurrent access.

``muse update-ref <ref> -d``
    Delete the ref file.  Exits ``USER_ERROR`` when the ref does not exist.

Ref format
----------
*ref* must begin with ``refs/heads/`` or ``refs/tags/``.  The
corresponding file inside ``.muse/`` stores the raw commit_id string.
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit

logger = logging.getLogger(__name__)

app = typer.Typer(name="update-ref", invoke_without_command=True, no_args_is_help=False)

# Allowed ref prefixes — matches git's plumbing conventions.
_VALID_PREFIXES = ("refs/heads/", "refs/tags/")


def _validate_ref_format(ref: str) -> None:
    """Raise ``typer.Exit(USER_ERROR)`` when *ref* does not start with a valid prefix."""
    if not any(ref.startswith(p) for p in _VALID_PREFIXES):
        typer.echo(
            f"❌ Invalid ref '{ref}'. "
            "Refs must start with 'refs/heads/' or 'refs/tags/'."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)


def _ref_file(muse_dir: pathlib.Path, ref: str) -> pathlib.Path:
    """Return the absolute path to the ref file inside *muse_dir*."""
    return muse_dir / pathlib.Path(ref)


def _read_current_value(ref_path: pathlib.Path) -> str | None:
    """Read the current commit_id stored at *ref_path*, or ``None`` if absent/empty."""
    if not ref_path.exists():
        return None
    raw = ref_path.read_text().strip()
    return raw if raw else None


async def _assert_commit_exists(session: AsyncSession, commit_id: str) -> None:
    """Exit ``USER_ERROR`` when *commit_id* is not in ``muse_cli_commits``."""
    row = await session.get(MuseCliCommit, commit_id)
    if row is None:
        typer.echo(f"❌ Commit {commit_id[:8]} not found in database.")
        raise typer.Exit(code=ExitCode.USER_ERROR)


# ---------------------------------------------------------------------------
# Testable async cores
# ---------------------------------------------------------------------------


async def _update_ref_async(
    *,
    ref: str,
    new_value: str,
    old_value: str | None,
    root: pathlib.Path,
    session: AsyncSession,
) -> None:
    """Write *new_value* to the ref file, with optional CAS guard.

    Why: Plumbing commands are the building blocks scripting agents use to
    manipulate the Muse object graph.  Centralising the validation here keeps
    higher-level commands (checkout, merge) simple — they delegate to this core
    when they need to advance a branch pointer atomically.

    Args:
        ref: Fully-qualified ref name (e.g. ``refs/heads/main``).
        new_value: Commit ID to write.  Must exist in ``muse_cli_commits``.
        old_value: If provided, the current ref value must match exactly;
            mismatch exits with ``USER_ERROR`` (compare-and-swap).
        root: Repo root (directory containing ``.muse/``).
        session: Open database session — caller controls commit/rollback.

    Raises:
        typer.Exit(USER_ERROR): ref format invalid, commit not found, or CAS mismatch.
    """
    _validate_ref_format(ref)
    muse_dir = root / ".muse"
    ref_path = _ref_file(muse_dir, ref)

    # Validate the new commit exists.
    await _assert_commit_exists(session, new_value)

    # CAS guard — read current value and compare.
    if old_value is not None:
        current = _read_current_value(ref_path)
        if current != old_value:
            typer.echo(
                f"❌ CAS failure: expected '{old_value[:8] if old_value else 'empty'}' "
                f"but found '{current[:8] if current else 'empty'}'. "
                "Ref not updated."
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)

    # Write the new value.
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(new_value)
    typer.echo(f"✅ {ref} → {new_value[:8]}")
    logger.info("✅ muse update-ref %s → %s", ref, new_value[:8])


async def _delete_ref_async(
    *,
    ref: str,
    root: pathlib.Path,
) -> None:
    """Delete the ref file for *ref*.

    Why: Scripting agents need an atomic delete primitive so they can clean up
    stale branch or tag pointers without touching the commit graph.

    Args:
        ref: Fully-qualified ref name (e.g. ``refs/heads/feature``).
        root: Repo root (directory containing ``.muse/``).

    Raises:
        typer.Exit(USER_ERROR): ref format invalid or ref file does not exist.
    """
    _validate_ref_format(ref)
    muse_dir = root / ".muse"
    ref_path = _ref_file(muse_dir, ref)

    if not ref_path.exists():
        typer.echo(f"❌ Ref '{ref}' does not exist.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    ref_path.unlink()
    typer.echo(f"✅ Deleted ref '{ref}'.")
    logger.info("✅ muse update-ref -d %s", ref)


# ---------------------------------------------------------------------------
# Typer entry point
# ---------------------------------------------------------------------------


@app.callback()
def update_ref(
    ref: str = typer.Argument(..., help="Ref to update, e.g. refs/heads/main or refs/tags/v1.0"),
    new_value: Optional[str] = typer.Argument(
        None,
        help="Commit ID to write to the ref. Required unless -d is given.",
    ),
    old_value: Optional[str] = typer.Option(
        None,
        "--old-value",
        help=(
            "Compare-and-swap guard. Only update if the current ref value matches this commit ID."
        ),
    ),
    delete: bool = typer.Option(
        False,
        "-d",
        "--delete",
        help="Delete the ref instead of writing it.",
    ),
) -> None:
    """Write or delete a Muse ref (branch or tag pointer).

    Updates .muse/refs/heads/<branch> or .muse/refs/tags/<tag> directly.
    Validates that <new-value> is a real commit before writing.
    """
    root = require_repo()

    if delete:
        try:
            asyncio.run(_delete_ref_async(ref=ref, root=root))
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"❌ muse update-ref -d failed: {exc}")
            logger.error("❌ muse update-ref -d error: %s", exc, exc_info=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        return

    if new_value is None:
        typer.echo("❌ <new-value> is required when not using -d.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    async def _run() -> None:
        async with open_session() as session:
            await _update_ref_async(
                ref=ref,
                new_value=new_value,
                old_value=old_value,
                root=root,
                session=session,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse update-ref failed: {exc}")
        logger.error("❌ muse update-ref error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
