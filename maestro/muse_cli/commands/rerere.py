"""muse rerere — reuse recorded resolutions for musical merge conflicts.

Commands
--------
``muse rerere``
    Attempt to auto-apply any cached resolution for conflicts currently
    listed in ``.muse/MERGE_STATE.json``.  Prints the number resolved.

``muse rerere list``
    Show all conflict fingerprints currently in the rr-cache.  Entries
    marked with ``[R]`` have a postimage (resolution recorded); entries
    marked with ``[C]`` are conflict-only (awaiting resolution).

``muse rerere forget <hash>``
    Remove a single cached conflict/resolution from the rr-cache.

``muse rerere clear``
    Purge the entire rr-cache.  Use when cached resolutions are stale or
    incorrect.
"""
from __future__ import annotations

import logging
import pathlib

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_rerere import (
    ConflictDict,
    apply_rerere,
    clear_rerere,
    forget_rerere,
    list_rerere,
)

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="rerere",
    help="Reuse recorded resolutions for musical merge conflicts.",
    no_args_is_help=False,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_current_conflicts(root: pathlib.Path) -> list[ConflictDict]:
    """Read conflict list from .muse/MERGE_STATE.json, if present."""
    import json

    merge_state_path = root / ".muse" / "MERGE_STATE.json"
    if not merge_state_path.exists():
        return []
    try:
        data = json.loads(merge_state_path.read_text(encoding="utf-8"))
        raw = data.get("conflict_paths", [])
        # conflict_paths is a list of file-path strings in the merge engine
        # (file-level conflicts), not MergeConflict dicts.  Wrap each path
        # in a minimal dict so rerere can fingerprint it.
        return [ConflictDict(region_id=p, type="file", description=f"conflict in {p}") for p in raw]
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ muse rerere: could not read MERGE_STATE.json: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Default command — apply cached resolution
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def rerere_apply(ctx: typer.Context) -> None:
    """Auto-apply any cached resolution for current merge conflicts."""
    if ctx.invoked_subcommand is not None:
        return

    root = require_repo()
    conflicts = _load_current_conflicts(root)

    if not conflicts:
        typer.echo("✅ No active merge conflicts found (no MERGE_STATE.json or empty conflict list).")
        return

    applied, _resolution = apply_rerere(root, conflicts)
    if applied:
        typer.echo(f"✅ Resolved {applied} conflict(s) using rerere.")
    else:
        typer.echo("⚠️ No cached resolution found for current conflicts.")
        raise typer.Exit(code=ExitCode.USER_ERROR)


# ---------------------------------------------------------------------------
# list subcommand
# ---------------------------------------------------------------------------


@app.command("list")
def rerere_list() -> None:
    """Show all conflict fingerprints in the rr-cache."""
    root = require_repo()
    hashes = list_rerere(root)

    if not hashes:
        typer.echo("rr-cache is empty.")
        return

    typer.echo(f"rr-cache ({len(hashes)} entr{'y' if len(hashes) == 1 else 'ies'}):")
    cache_root = root / ".muse" / "rr-cache"
    for h in hashes:
        postimage = cache_root / h / "postimage"
        tag = "[R]" if postimage.exists() else "[C]"
        typer.echo(f"  {tag} {h}")


# ---------------------------------------------------------------------------
# forget subcommand
# ---------------------------------------------------------------------------


@app.command("forget")
def rerere_forget(
    conflict_hash: str = typer.Argument(..., help="SHA-256 fingerprint hash to remove."),
) -> None:
    """Remove a single cached conflict/resolution from the rr-cache."""
    root = require_repo()
    removed = forget_rerere(root, conflict_hash)
    if removed:
        typer.echo(f"✅ Forgot rerere entry {conflict_hash[:12]}…")
    else:
        typer.echo(f"⚠️ Hash {conflict_hash[:12]}… not found in rr-cache.")
        raise typer.Exit(code=ExitCode.USER_ERROR)


# ---------------------------------------------------------------------------
# clear subcommand
# ---------------------------------------------------------------------------


@app.command("clear")
def rerere_clear() -> None:
    """Purge the entire rr-cache."""
    root = require_repo()
    count = clear_rerere(root)
    typer.echo(f"✅ Cleared {count} rr-cache entr{'y' if count == 1 else 'ies'}.")
