"""muse groove-check — analyze rhythmic drift across commits.

Detects which commit in a range "killed the groove" by measuring how much
the average note-onset deviation from the quantization grid shifted between
adjacent commits.  A large drift_delta signals a quantize operation that was
too aggressive, a tempo map change that made the pocket feel stiff, or any
edit that disrupted rhythmic consistency.

Output (default tabular)::

    Groove-check — range HEAD~6..HEAD  threshold 0.1 beats

    Commit    Groove Score  Drift Δ  Status
    --------  ------------  -------  ------
    a1b2c3d4        0.0400   0.0000  OK
    e5f6a7b8        0.0500   0.0100  OK
    c9d0e1f2        0.0600   0.0100  OK
    a3b4c5d6        0.0900   0.0300  OK
    e7f8a9b0        0.1500   0.0600  WARN
    c1d2e3f4        0.1300   0.0200  OK

    Flagged: 1 / 6 commits  (worst: e7f8a9b0)

Flags
-----
[range]            Commit range to analyze (e.g. HEAD~5..HEAD). Default: last 10.
--track TEXT       Scope analysis to a specific instrument track.
--section TEXT     Scope analysis to a specific musical section.
--threshold FLOAT  Drift threshold in beats (default 0.1). Commits that exceed
                   this value are flagged WARN; >2× threshold = FAIL.
--json             Emit machine-readable JSON output.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import Annotated

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_groove_check import (
    DEFAULT_THRESHOLD,
    GrooveCheckResult,
    GrooveStatus,
    compute_groove_check,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

# ---------------------------------------------------------------------------
# Column widths
# ---------------------------------------------------------------------------

_COL_COMMIT = 8
_COL_SCORE = 12
_COL_DELTA = 7
_COL_STATUS = 6

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_table(result: GrooveCheckResult) -> None:
    """Emit an ASCII table of groove-check results to stdout.

    Includes a summary line with the total flagged count and worst commit.

    Args:
        result: Completed :class:`GrooveCheckResult` from the analysis.
    """
    typer.echo(
        f"Groove-check — range {result.commit_range}"
        f"  threshold {result.threshold} beats"
    )
    typer.echo("")

    header = (
        f"{'Commit':<{_COL_COMMIT}}  "
        f"{'Groove Score':>{_COL_SCORE}}  "
        f"{'Drift Δ':>{_COL_DELTA}}  "
        f"{'Status':<{_COL_STATUS}}"
    )
    sep = (
        f"{'-' * _COL_COMMIT}  "
        f"{'-' * _COL_SCORE}  "
        f"{'-' * _COL_DELTA}  "
        f"{'-' * _COL_STATUS}"
    )
    typer.echo(header)
    typer.echo(sep)

    for entry in result.entries:
        typer.echo(
            f"{entry.commit:<{_COL_COMMIT}}  "
            f"{entry.groove_score:>{_COL_SCORE}.4f}  "
            f"{entry.drift_delta:>{_COL_DELTA}.4f}  "
            f"{entry.status.value:<{_COL_STATUS}}"
        )

    typer.echo("")
    worst_label = (
        f"  (worst: {result.worst_commit})" if result.worst_commit else ""
    )
    typer.echo(
        f"Flagged: {result.flagged_commits} / {result.total_commits} commits"
        f"{worst_label}"
    )


def _render_json(result: GrooveCheckResult) -> None:
    """Emit groove-check results as structured JSON for agent consumption.

    Args:
        result: Completed :class:`GrooveCheckResult` from the analysis.
    """
    payload = {
        "commit_range": result.commit_range,
        "threshold": result.threshold,
        "total_commits": result.total_commits,
        "flagged_commits": result.flagged_commits,
        "worst_commit": result.worst_commit,
        "entries": [
            {
                "commit": e.commit,
                "groove_score": e.groove_score,
                "drift_delta": e.drift_delta,
                "status": e.status.value,
                "track": e.track,
                "section": e.section,
                "midi_files": e.midi_files,
            }
            for e in result.entries
        ],
    }
    typer.echo(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Async core (injectable for tests)
# ---------------------------------------------------------------------------


async def _groove_check_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit_range: Optional[str],
    track: Optional[str],
    section: Optional[str],
    threshold: float,
    as_json: bool,
) -> GrooveCheckResult:
    """Core groove-check logic — fully injectable for unit tests.

    Resolves the effective commit range from the ``.muse/`` layout, calls
    :func:`compute_groove_check` (pure, stub-backed), and renders output.

    Args:
        root:         Repository root (directory containing ``.muse/``).
        session:      Open async DB session (reserved for full implementation).
        commit_range: Explicit range string or None to use the last 10 commits.
        track:        Restrict analysis to a named instrument track.
        section:      Restrict analysis to a named musical section.
        threshold:    Drift threshold in beats for WARN/FAIL classification.
        as_json:      Emit JSON instead of the ASCII table.

    Returns:
        The :class:`GrooveCheckResult` produced by the analysis.
    """
    if threshold <= 0:
        typer.echo("❌ --threshold must be a positive number.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    muse_dir = root / ".muse"
    head_path = muse_dir / "HEAD"
    head_ref = head_path.read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref
    ref_path = muse_dir / pathlib.Path(head_ref)
    head_sha = ref_path.read_text().strip() if ref_path.exists() else ""

    if not head_sha and not commit_range:
        typer.echo(f"No commits yet on branch {branch} — nothing to analyse.")
        raise typer.Exit(code=ExitCode.SUCCESS)

    effective_range = commit_range or f"HEAD~{10}..HEAD"

    result = compute_groove_check(
        commit_range=effective_range,
        threshold=threshold,
        track=track,
        section=section,
    )

    if as_json:
        _render_json(result)
    else:
        _render_table(result)

    return result


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def groove_check(
    ctx: typer.Context,
    commit_range: Annotated[
        Optional[str],
        typer.Argument(
            help=(
                "Commit range to analyze (e.g. HEAD~5..HEAD). "
                "Defaults to the last 10 commits."
            ),
            show_default=False,
            metavar="RANGE",
        ),
    ] = None,
    track: Annotated[
        Optional[str],
        typer.Option(
            "--track",
            help="Scope analysis to a specific instrument track (e.g. 'drums').",
            show_default=False,
            metavar="TEXT",
        ),
    ] = None,
    section: Annotated[
        Optional[str],
        typer.Option(
            "--section",
            help="Scope analysis to a specific musical section (e.g. 'verse').",
            show_default=False,
            metavar="TEXT",
        ),
    ] = None,
    threshold: Annotated[
        float,
        typer.Option(
            "--threshold",
            help=(
                "Drift threshold in beats (default 0.1). Commits whose "
                "drift_delta exceeds this are flagged WARN; >2× = FAIL."
            ),
        ),
    ] = DEFAULT_THRESHOLD,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON output."),
    ] = False,
) -> None:
    """Analyze rhythmic drift across commits to find groove regressions.

    Computes note-onset deviation from the quantization grid for each commit
    in the range, then flags commits where the deviation shifted significantly
    relative to their neighbors.  Use this after a session to spot which
    commit made the pocket feel stiff.
    """
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _groove_check_async(
                root=root,
                session=session,
                commit_range=commit_range,
                track=track,
                section=section,
                threshold=threshold,
                as_json=as_json,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse groove-check failed: {exc}")
        logger.error("❌ muse groove-check error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
