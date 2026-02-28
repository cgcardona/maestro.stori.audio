"""muse motif — identify, track, and compare recurring melodic motifs.

A motif is a short melodic or rhythmic idea that reappears and transforms
throughout a composition.  This command group surfaces motif-level analysis
over the Muse VCS commit history.

Subcommands
-----------

``muse motif find [<commit>]``
    Detect recurring melodic/rhythmic patterns in a commit (default: HEAD).

``muse motif track <pattern>``
    Search all commits for appearances of a specific motif.  The pattern
    is expressed as space-separated note names (``"C D E G"``) or MIDI
    numbers (``"60 62 64 67"``).  Transpositions and standard transformations
    (inversion, retrograde, retrograde-inversion) are detected automatically.

``muse motif diff <commit-a> <commit-b>``
    Show how the dominant motif transformed between two commits.

``muse motif list``
    List all named motifs stored in ``.muse/motifs/``.

Flags on ``find``
-----------------
--min-length N     Minimum motif length in notes (default: 3).
--section TEXT     Scope to a named section/region.
--track TEXT       Scope to a named MIDI track.
--json             Machine-readable JSON output.

All subcommands support ``--json`` for agent consumption.

Output example (find, default)::

    Recurring motifs — commit a1b2c3d4  (HEAD -> main)
    ── stub mode: full MIDI analysis pending ──

    #  Fingerprint          Contour           Count
    -  -------------------  ----------------  -----
    1  [+2, +2, -1, +2]     ascending-step    3
    2  [-2, -2, +1, -2]     descending-step   2
    3  [+4, -2, +3]         arch              2

    3 motifs found (min-length 3)
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from typing import Optional

import typer
from typing_extensions import Annotated

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_motif import (
    MotifDiffResult,
    MotifFindResult,
    MotifListResult,
    MotifOccurrence,
    MotifTrackResult,
    diff_motifs,
    find_motifs,
    list_motifs,
    track_motif,
)

logger = logging.getLogger(__name__)

app = typer.Typer(no_args_is_help=True)

# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _format_find(result: MotifFindResult, *, as_json: bool) -> str:
    """Render a find result as a human-readable table or JSON."""
    if as_json:
        payload = {
            "commit": result.commit_id,
            "branch": result.branch,
            "min_length": result.min_length,
            "total_found": result.total_found,
            "source": result.source,
            "motifs": [
                {
                    "fingerprint": list(g.fingerprint),
                    "label": g.label,
                    "count": g.count,
                    "occurrences": [
                        {
                            "commit": o.commit_id,
                            "track": o.track,
                            "section": o.section,
                            "start_position": o.start_position,
                            "transformation": o.transformation.value,
                            "pitches": list(o.pitch_sequence),
                        }
                        for o in g.occurrences
                    ],
                }
                for g in result.motifs
            ],
        }
        return json.dumps(payload, indent=2)

    lines: list[str] = [
        f"Recurring motifs — commit {result.commit_id}  (HEAD -> {result.branch})",
    ]
    if result.source == "stub":
        lines.append("── stub mode: full MIDI analysis pending ──")
    lines.append("")
    lines.append(f"{'#':<3}  {'Fingerprint':<22}  {'Contour':<18}  {'Count':>5}")
    lines.append(f"{'-':<3}  {'-'*22}  {'-'*18}  {'-'*5}")
    for idx, group in enumerate(result.motifs, start=1):
        fp_str = "[" + ", ".join(f"{'+' if i >= 0 else ''}{i}" for i in group.fingerprint) + "]"
        lines.append(
            f"{idx:<3}  {fp_str:<22}  {group.label:<18}  {group.count:>5}"
        )
    lines.append("")
    lines.append(f"{result.total_found} motif(s) found (min-length {result.min_length})")
    return "\n".join(lines)


def _format_track(result: MotifTrackResult, *, as_json: bool) -> str:
    """Render a track result as a human-readable table or JSON."""
    if as_json:
        payload = {
            "pattern": result.pattern,
            "fingerprint": list(result.fingerprint),
            "total_commits_scanned": result.total_commits_scanned,
            "source": result.source,
            "occurrences": [
                {
                    "commit": o.commit_id,
                    "track": o.track,
                    "section": o.section,
                    "start_position": o.start_position,
                    "transformation": o.transformation.value,
                    "pitches": list(o.pitch_sequence),
                }
                for o in result.occurrences
            ],
        }
        return json.dumps(payload, indent=2)

    fp_str = "[" + ", ".join(f"{'+' if i >= 0 else ''}{i}" for i in result.fingerprint) + "]"
    lines: list[str] = [
        f"Tracking motif: {result.pattern!r}",
        f"Fingerprint: {fp_str}",
        f"Commits scanned: {result.total_commits_scanned}",
        "",
    ]
    if result.source == "stub":
        lines.append("── stub mode: full history scan pending ──")
        lines.append("")
    if not result.occurrences:
        lines.append("No occurrences found.")
        return "\n".join(lines)

    lines.append(f"{'Commit':<10}  {'Track':<12}  {'Transform':<14}  {'Position':>8}")
    lines.append(f"{'-'*10}  {'-'*12}  {'-'*14}  {'-'*8}")
    for occ in result.occurrences:
        lines.append(
            f"{occ.commit_id:<10}  {occ.track:<12}  "
            f"{occ.transformation.value:<14}  {occ.start_position:>8}"
        )
    lines.append("")
    lines.append(f"{len(result.occurrences)} occurrence(s) found.")
    return "\n".join(lines)


def _format_diff(result: MotifDiffResult, *, as_json: bool) -> str:
    """Render a diff result as human-readable text or JSON."""
    if as_json:
        payload = {
            "transformation": result.transformation.value,
            "description": result.description,
            "source": result.source,
            "commit_a": {
                "commit": result.commit_a.commit_id,
                "fingerprint": list(result.commit_a.fingerprint),
                "label": result.commit_a.label,
                "pitches": list(result.commit_a.pitch_sequence),
            },
            "commit_b": {
                "commit": result.commit_b.commit_id,
                "fingerprint": list(result.commit_b.fingerprint),
                "label": result.commit_b.label,
                "pitches": list(result.commit_b.pitch_sequence),
            },
        }
        return json.dumps(payload, indent=2)

    def _fp(intervals: tuple[int, ...]) -> str:
        return "[" + ", ".join(f"{'+' if i >= 0 else ''}{i}" for i in intervals) + "]"

    lines: list[str] = [
        f"Motif diff: {result.commit_a.commit_id} → {result.commit_b.commit_id}",
        "",
        f"  A ({result.commit_a.commit_id}): {_fp(result.commit_a.fingerprint)}  [{result.commit_a.label}]",
        f"  B ({result.commit_b.commit_id}): {_fp(result.commit_b.fingerprint)}  [{result.commit_b.label}]",
        "",
        f"Transformation: {result.transformation.value.upper()}",
        f"{result.description}",
    ]
    if result.source == "stub":
        lines.append("")
        lines.append("── stub mode: full MIDI analysis pending ──")
    return "\n".join(lines)


def _format_list(result: MotifListResult, *, as_json: bool) -> str:
    """Render a list result as a human-readable table or JSON."""
    if as_json:
        payload = {
            "source": result.source,
            "motifs": [
                {
                    "name": m.name,
                    "fingerprint": list(m.fingerprint),
                    "created_at": m.created_at,
                    "description": m.description,
                }
                for m in result.motifs
            ],
        }
        return json.dumps(payload, indent=2)

    if not result.motifs:
        return "No named motifs saved. Use `muse motif find` to discover them."

    lines: list[str] = ["Named motifs:", ""]
    lines.append(f"{'Name':<20}  {'Fingerprint':<22}  {'Created':<24}  Description")
    lines.append(f"{'-'*20}  {'-'*22}  {'-'*24}  {'-'*30}")
    for m in result.motifs:
        fp_str = "[" + ", ".join(f"{'+' if i >= 0 else ''}{i}" for i in m.fingerprint) + "]"
        desc = (m.description or "")[:30]
        lines.append(f"{m.name:<20}  {fp_str:<22}  {m.created_at:<24}  {desc}")
    return "\n".join(lines)


def _resolve_head(root: pathlib.Path) -> tuple[str, str]:
    """Return (short_commit_id, branch) for the current HEAD.

    Args:
        root: Repository root (directory containing ``.muse/``).

    Returns:
        A ``(commit_id, branch)`` pair.  ``commit_id`` is at most 8 chars;
        ``branch`` is the branch name extracted from the HEAD ref.
    """
    muse_dir = root / ".muse"
    head_ref = (muse_dir / "HEAD").read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref
    ref_path = muse_dir / pathlib.Path(head_ref)
    head_sha = ref_path.read_text().strip() if ref_path.exists() else "0000000"
    return head_sha[:8], branch


# ---------------------------------------------------------------------------
# Subcommand: find
# ---------------------------------------------------------------------------


@app.command(name="find")
def motif_find(
    commit: Annotated[
        Optional[str],
        typer.Argument(
            help="Commit SHA to analyse. Defaults to HEAD.",
            show_default=False,
        ),
    ] = None,
    min_length: Annotated[
        int,
        typer.Option(
            "--min-length",
            help="Minimum motif length in notes (default: 3).",
            min=2,
        ),
    ] = 3,
    track: Annotated[
        Optional[str],
        typer.Option(
            "--track",
            help="Restrict analysis to a named MIDI track.",
            show_default=False,
        ),
    ] = None,
    section: Annotated[
        Optional[str],
        typer.Option(
            "--section",
            help="Restrict analysis to a named section/region.",
            show_default=False,
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON output."),
    ] = False,
) -> None:
    """Detect recurring melodic/rhythmic patterns in a commit (default: HEAD)."""
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:  # noqa: F841 — reserved for DB queries
            commit_id, branch = _resolve_head(root)
            resolved = commit or commit_id
            result = await find_motifs(
                commit_id=resolved,
                branch=branch,
                min_length=min_length,
                track=track,
                section=section,
                as_json=as_json,
            )
            typer.echo(_format_find(result, as_json=as_json))

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse motif find failed: {exc}")
        logger.error("❌ muse motif find error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)


# ---------------------------------------------------------------------------
# Subcommand: track
# ---------------------------------------------------------------------------


@app.command(name="track")
def motif_track(
    pattern: Annotated[
        str,
        typer.Argument(
            help=(
                "Motif to track — space-separated note names (e.g. 'C D E G') "
                "or MIDI numbers (e.g. '60 62 64 67')."
            ),
        ),
    ],
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON output."),
    ] = False,
) -> None:
    """Search all commits for appearances of a specific motif.

    Detects the motif and its common transformations: transposition,
    inversion, retrograde, and retrograde-inversion.
    """
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:  # noqa: F841 — reserved for DB queries
            muse_dir = root / ".muse"
            commit_ids: list[str] = []
            head_ref = (muse_dir / "HEAD").read_text().strip()
            ref_path = muse_dir / pathlib.Path(head_ref)
            if ref_path.exists():
                commit_ids = [ref_path.read_text().strip()]

            result = await track_motif(pattern=pattern, commit_ids=commit_ids)
            typer.echo(_format_track(result, as_json=as_json))

    try:
        asyncio.run(_run())
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse motif track failed: {exc}")
        logger.error("❌ muse motif track error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)


# ---------------------------------------------------------------------------
# Subcommand: diff
# ---------------------------------------------------------------------------


@app.command(name="diff")
def motif_diff(
    commit_a: Annotated[
        str,
        typer.Argument(help="First (earlier) commit SHA.", metavar="COMMIT-A"),
    ],
    commit_b: Annotated[
        str,
        typer.Argument(help="Second (later) commit SHA.", metavar="COMMIT-B"),
    ],
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON output."),
    ] = False,
) -> None:
    """Show how the dominant motif transformed between two commits."""
    require_repo()

    async def _run() -> None:
        result = await diff_motifs(commit_a_id=commit_a, commit_b_id=commit_b)
        typer.echo(_format_diff(result, as_json=as_json))

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse motif diff failed: {exc}")
        logger.error("❌ muse motif diff error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)


# ---------------------------------------------------------------------------
# Subcommand: list
# ---------------------------------------------------------------------------


@app.command(name="list")
def motif_list(
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON output."),
    ] = False,
) -> None:
    """List all named motifs stored in ``.muse/motifs/``."""
    root = require_repo()

    async def _run() -> None:
        result = await list_motifs(muse_dir_path=str(root / ".muse"))
        typer.echo(_format_list(result, as_json=as_json))

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse motif list failed: {exc}")
        logger.error("❌ muse motif list error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
