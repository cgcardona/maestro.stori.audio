"""muse tempo — read or set the tempo (BPM) of a commit.

Usage
-----
::

    muse tempo [<commit>]            # read tempo from HEAD or named commit
    muse tempo --set 128             # annotate HEAD with explicit BPM
    muse tempo --set 128 <commit>    # annotate a named commit
    muse tempo --history             # show BPM across all commits
    muse tempo --json                # machine-readable JSON output

Tempo resolution order (read path)
-----------------------------------
1. Explicit annotation stored via ``muse tempo --set`` (``metadata.tempo_bpm``).
2. Auto-detection from MIDI Set Tempo events in the commit's snapshot.
3. ``None`` (displayed as ``--`` in table output) when neither is available.

Tempo storage (write path)
---------------------------
``--set`` writes ``{"tempo_bpm": <float>}`` into the ``metadata`` JSON column
of the target commit row.  Other metadata keys are preserved.  No new DB rows
are created — only the existing commit is annotated.

History traversal
-----------------
``--history`` walks the full parent chain from HEAD (or the named commit),
using only explicitly annotated values (``metadata.tempo_bpm``).  Auto-detected
BPM is shown on the single-commit read path but is not persisted, so it cannot
appear in history.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import (
    open_session,
    resolve_commit_ref,
    set_commit_tempo_bpm,
)
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit
from maestro.services.muse_tempo import (
    MuseTempoHistoryEntry,
    MuseTempoResult,
    build_tempo_history,
    detect_tempo_from_snapshot,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

_BPM_MIN = 20.0
_BPM_MAX = 400.0


# ---------------------------------------------------------------------------
# Repo context helpers
# ---------------------------------------------------------------------------


def _read_repo_context(root: pathlib.Path) -> tuple[str, str, str]:
    """Return (repo_id, branch, head_commit_id_or_empty) from .muse/."""
    import json as _json

    muse_dir = root / ".muse"
    repo_data: dict[str, str] = _json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]
    head_ref = (muse_dir / "HEAD").read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1]
    ref_path = muse_dir / pathlib.Path(head_ref)
    head_commit_id = ref_path.read_text().strip() if ref_path.exists() else ""
    return repo_id, branch, head_commit_id


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _load_commit_chain(
    session: AsyncSession,
    head_commit_id: str,
    limit: int = 1000,
) -> list[MuseCliCommit]:
    """Walk the parent chain from *head_commit_id*, returning newest-first."""
    commits: list[MuseCliCommit] = []
    current_id: str | None = head_commit_id
    while current_id and len(commits) < limit:
        commit = await session.get(MuseCliCommit, current_id)
        if commit is None:
            logger.warning("⚠️ Commit %s not found — chain broken", current_id[:8])
            break
        commits.append(commit)
        current_id = commit.parent_commit_id
    return commits


async def _tempo_read_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit_ref: str | None,
    as_json: bool,
) -> MuseTempoResult:
    """Load a commit and return its tempo result.

    Reads the annotated BPM from ``metadata.tempo_bpm``.  If absent, scans
    MIDI files in the commit's snapshot for a Set Tempo event.
    """
    from maestro.muse_cli.db import get_commit_snapshot_manifest

    repo_id, branch, _ = _read_repo_context(root)
    commit = await resolve_commit_ref(session, repo_id, branch, commit_ref)
    if commit is None:
        ref_label = commit_ref or "HEAD"
        typer.echo(f"❌ No commit found for ref '{ref_label}'")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    meta: dict[str, object] = commit.commit_metadata or {}
    bpm_raw = meta.get("tempo_bpm")
    annotated_bpm: float | None = float(bpm_raw) if isinstance(bpm_raw, (int, float)) else None

    # Auto-detect from MIDI files in snapshot
    detected_bpm: float | None = None
    manifest = await get_commit_snapshot_manifest(session, commit.commit_id)
    if manifest:
        workdir = root / "muse-work"
        detected_bpm = detect_tempo_from_snapshot(manifest, workdir)

    result = MuseTempoResult(
        commit_id=commit.commit_id,
        branch=branch,
        message=commit.message,
        tempo_bpm=annotated_bpm,
        detected_bpm=detected_bpm,
    )

    if as_json:
        _print_result_json(result)
    else:
        _print_result_human(result)

    return result


async def _tempo_set_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit_ref: str | None,
    bpm: float,
) -> None:
    """Annotate a commit with an explicit BPM."""
    repo_id, branch, _ = _read_repo_context(root)
    commit = await resolve_commit_ref(session, repo_id, branch, commit_ref)
    if commit is None:
        ref_label = commit_ref or "HEAD"
        typer.echo(f"❌ No commit found for ref '{ref_label}'")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    updated = await set_commit_tempo_bpm(session, commit.commit_id, bpm)
    if updated is None:
        typer.echo(f"❌ Could not update commit {commit.commit_id[:8]}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    typer.echo(f"✅ Set tempo {bpm:.1f} BPM on commit {commit.commit_id[:8]}  ({commit.message})")


async def _tempo_history_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit_ref: str | None,
    as_json: bool,
) -> list[MuseTempoHistoryEntry]:
    """Walk parent chain and return a tempo history list."""
    repo_id, branch, _ = _read_repo_context(root)
    commit = await resolve_commit_ref(session, repo_id, branch, commit_ref)
    if commit is None:
        ref_label = commit_ref or "HEAD"
        typer.echo(f"❌ No commit found for ref '{ref_label}'")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    chain = await _load_commit_chain(session, commit.commit_id)
    history = build_tempo_history(chain)

    if as_json:
        _print_history_json(history)
    else:
        _print_history_human(history)

    return history


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _bpm_str(bpm: float | None) -> str:
    return f"{bpm:.1f}" if bpm is not None else "--"


def _print_result_human(result: MuseTempoResult) -> None:
    typer.echo(f"commit  {result.commit_id}")
    typer.echo(f"branch  {result.branch}")
    typer.echo(f"message {result.message}")
    typer.echo("")
    if result.tempo_bpm is not None:
        typer.echo(f"tempo   {result.tempo_bpm:.1f} BPM  (annotated)")
    elif result.detected_bpm is not None:
        typer.echo(f"tempo   {result.detected_bpm:.1f} BPM  (detected from MIDI)")
    else:
        typer.echo("tempo   -- (no annotation; no MIDI tempo event found)")


def _print_result_json(result: MuseTempoResult) -> None:
    typer.echo(
        json.dumps(
            {
                "commit_id": result.commit_id,
                "branch": result.branch,
                "message": result.message,
                "tempo_bpm": result.tempo_bpm,
                "detected_bpm": result.detected_bpm,
                "effective_bpm": result.effective_bpm,
            },
            indent=2,
        )
    )


def _print_history_human(history: list[MuseTempoHistoryEntry]) -> None:
    if not history:
        typer.echo("No commits in history.")
        return
    header = f"{'COMMIT':<10}  {'BPM':>7}  {'DELTA':>7}  MESSAGE"
    typer.echo(header)
    typer.echo("-" * len(header))
    for entry in history:
        short_id = entry.commit_id[:8]
        bpm_col = _bpm_str(entry.effective_bpm)
        if entry.delta_bpm is None:
            delta_col = "  --"
        elif entry.delta_bpm > 0:
            delta_col = f"+{entry.delta_bpm:.1f}"
        else:
            delta_col = f"{entry.delta_bpm:.1f}"
        typer.echo(f"{short_id:<10}  {bpm_col:>7}  {delta_col:>7}  {entry.message}")


def _print_history_json(history: list[MuseTempoHistoryEntry]) -> None:
    rows = [
        {
            "commit_id": e.commit_id,
            "message": e.message,
            "effective_bpm": e.effective_bpm,
            "delta_bpm": e.delta_bpm,
        }
        for e in history
    ]
    typer.echo(json.dumps(rows, indent=2))


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def tempo(
    ctx: typer.Context,
    commit_ref: Optional[str] = typer.Argument(
        None,
        metavar="<commit>",
        help="Commit SHA (full or abbreviated) or 'HEAD' (default).",
    ),
    set_bpm: Optional[float] = typer.Option(
        None,
        "--set",
        metavar="<bpm>",
        help=f"Annotate the commit with this BPM ({_BPM_MIN}–{_BPM_MAX}).",
    ),
    history: bool = typer.Option(
        False,
        "--history",
        help="Show tempo changes across all commits (newest first).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Read or set the tempo (BPM) of a commit.

    Without flags, prints the BPM for the target commit.  Use ``--set``
    to annotate a commit with an explicit BPM.  Use ``--history`` to show
    the BPM timeline across the full parent chain.
    """
    root = require_repo()

    if set_bpm is not None:
        if not (_BPM_MIN <= set_bpm <= _BPM_MAX):
            typer.echo(f"❌ BPM must be between {_BPM_MIN} and {_BPM_MAX} (got {set_bpm})")
            raise typer.Exit(code=ExitCode.USER_ERROR)

        async def _run_set() -> None:
            async with open_session() as session:
                await _tempo_set_async(
                    root=root,
                    session=session,
                    commit_ref=commit_ref,
                    bpm=set_bpm,
                )

        try:
            asyncio.run(_run_set())
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"❌ muse tempo --set failed: {exc}")
            logger.error("❌ muse tempo --set error: %s", exc, exc_info=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        return

    if history:
        async def _run_history() -> None:
            async with open_session() as session:
                await _tempo_history_async(
                    root=root,
                    session=session,
                    commit_ref=commit_ref,
                    as_json=as_json,
                )

        try:
            asyncio.run(_run_history())
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"❌ muse tempo --history failed: {exc}")
            logger.error("❌ muse tempo --history error: %s", exc, exc_info=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        return

    # Default: read path
    async def _run_read() -> None:
        async with open_session() as session:
            await _tempo_read_async(
                root=root,
                session=session,
                commit_ref=commit_ref,
                as_json=as_json,
            )

    try:
        asyncio.run(_run_read())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse tempo failed: {exc}")
        logger.error("❌ muse tempo error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
