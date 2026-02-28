"""muse contour — analyze the melodic contour and phrase shape of a composition.

Melodic contour encodes whether a melody rises, falls, arches, or waves — a
fundamental expressive quality that distinguishes two otherwise similar melodies.
This command computes the pitch trajectory, classifies the overall shape, and
reports phrase statistics for a target commit (default HEAD).

Shape vocabulary
----------------
- ascending       — net upward movement across the full phrase
- descending      — net downward movement across the full phrase
- arch            — rises then falls (single peak)
- inverted-arch   — falls then rises (valley shape)
- wave            — multiple peaks; alternating rise and fall
- static          — narrow pitch range (< 2 semitones spread)

Command forms
-------------

Analyse melodic contour at HEAD (default)::

    muse contour

Analyse at a specific commit::

    muse contour a1b2c3d4

Restrict to a named melodic track::

    muse contour --track keys

Scope to a section::

    muse contour --section verse

Compare contour between two commits::

    muse contour --compare HEAD~10 HEAD

Show overall shape label only::

    muse contour --shape

Show contour evolution across all commits::

    muse contour --history

Machine-readable JSON output::

    muse contour --json
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import Annotated, TypedDict

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode

logger = logging.getLogger(__name__)

app = typer.Typer()

# ---------------------------------------------------------------------------
# Shape label constants
# ---------------------------------------------------------------------------

ShapeLabel = str  # one of the SHAPE_LABELS values

SHAPE_LABELS: tuple[str, ...] = (
    "ascending",
    "descending",
    "arch",
    "inverted-arch",
    "wave",
    "static",
)

VALID_SHAPES: frozenset[str] = frozenset(SHAPE_LABELS)

# ---------------------------------------------------------------------------
# Named result types — stable CLI contract
# ---------------------------------------------------------------------------


class ContourResult(TypedDict):
    """Melodic contour analysis for a single commit or working tree.

    Fields
    ------
    shape:         Overall melodic shape label (ascending, descending, arch, …).
    tessitura:     Effective pitch range in semitones.
    avg_interval:  Mean absolute note-to-note interval (semitones).  Higher
                   values indicate more angular, wider-leaping melodies.
    phrase_count:  Number of detected melodic phrases.
    avg_phrase_bars: Mean phrase length in bars.
    commit:        Commit SHA analysed (8-char prefix).
    branch:        Current branch name.
    track:         Track name analysed, or "all".
    section:       Section name scoped, or "all".
    source:        "stub" until MIDI analysis is wired in.
    """

    shape: str
    tessitura: int
    avg_interval: float
    phrase_count: int
    avg_phrase_bars: float
    commit: str
    branch: str
    track: str
    section: str
    source: str


class ContourCompareResult(TypedDict):
    """Comparison of melodic contour between two commits.

    Fields
    ------
    commit_a:         ContourResult for the first commit (or HEAD).
    commit_b:         ContourResult for the reference commit.
    shape_changed:    True when the overall shape label differs.
    angularity_delta: Change in avg_interval (positive = more angular).
    tessitura_delta:  Change in tessitura semitones (positive = wider).
    """

    commit_a: ContourResult
    commit_b: ContourResult
    shape_changed: bool
    angularity_delta: float
    tessitura_delta: int


# ---------------------------------------------------------------------------
# Stub data
# ---------------------------------------------------------------------------

_STUB_SHAPE: ShapeLabel = "arch"
_STUB_TESSITURA = 24  # 2 octaves
_STUB_AVG_INTERVAL = 2.5  # semitones
_STUB_PHRASE_COUNT = 4
_STUB_AVG_PHRASE_BARS = 8.0


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _resolve_head(root: pathlib.Path) -> tuple[str, str]:
    """Return (branch, head_sha) from the .muse/ layout.

    Reads HEAD → ref → SHA, returning empty string for the SHA when no commits
    exist yet.  Called by every analysis function to avoid duplicating file I/O.

    Args:
        root: Repository root (directory that contains ``.muse/``).

    Returns:
        Tuple of (branch_name, head_sha_or_empty).
    """
    muse_dir = root / ".muse"
    head_ref = (muse_dir / "HEAD").read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref
    ref_path = muse_dir / pathlib.Path(head_ref)
    head_sha = ref_path.read_text().strip() if ref_path.exists() else ""
    return branch, head_sha


async def _contour_detect_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit: Optional[str],
    track: Optional[str],
    section: Optional[str],
) -> ContourResult:
    """Compute the melodic contour for a single commit (or working tree).

    Stub implementation: resolves branch/commit metadata from ``.muse/`` and
    returns a realistic placeholder result in the correct schema.  Full MIDI
    analysis will be wired once Storpheus exposes a pitch-trajectory route.

    Args:
        root:    Repository root.
        session: Open async DB session (reserved for full implementation).
        commit:  Commit SHA to analyse, or ``None`` for HEAD.
        track:   Named MIDI track to analyse, or ``None`` for all tracks.
        section: Named section to scope analysis, or ``None`` for the full piece.

    Returns:
        A :class:`ContourResult` describing shape, tessitura, angularity,
        phrase statistics, and provenance metadata.
    """
    branch, head_sha = await _resolve_head(root)
    resolved_commit = commit or (head_sha[:8] if head_sha else "HEAD")

    return ContourResult(
        shape=_STUB_SHAPE,
        tessitura=_STUB_TESSITURA,
        avg_interval=_STUB_AVG_INTERVAL,
        phrase_count=_STUB_PHRASE_COUNT,
        avg_phrase_bars=_STUB_AVG_PHRASE_BARS,
        commit=resolved_commit,
        branch=branch,
        track=track or "all",
        section=section or "all",
        source="stub",
    )


async def _contour_compare_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit_a: Optional[str],
    commit_b: str,
    track: Optional[str],
    section: Optional[str],
) -> ContourCompareResult:
    """Compare melodic contour between two commits.

    Stub implementation: both sides share the same placeholder metric values,
    so shape_changed is always False and deltas are always 0.  Full implementation
    will load per-commit pitch trajectories from Storpheus.

    Args:
        root:     Repository root.
        session:  Open async DB session.
        commit_a: First commit ref (defaults to HEAD when ``None``).
        commit_b: Reference commit ref to compare against.
        track:    Named MIDI track, or ``None`` for all.
        section:  Named section, or ``None`` for the full piece.

    Returns:
        A :class:`ContourCompareResult` with both sides and diff metrics.
    """
    result_a = await _contour_detect_async(
        root=root, session=session, commit=commit_a, track=track, section=section
    )
    result_b = await _contour_detect_async(
        root=root, session=session, commit=commit_b, track=track, section=section
    )
    result_b = ContourResult(**{**result_b, "commit": commit_b})

    return ContourCompareResult(
        commit_a=result_a,
        commit_b=result_b,
        shape_changed=result_a["shape"] != result_b["shape"],
        angularity_delta=round(result_a["avg_interval"] - result_b["avg_interval"], 4),
        tessitura_delta=result_a["tessitura"] - result_b["tessitura"],
    )


async def _contour_history_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    track: Optional[str],
    section: Optional[str],
) -> list[ContourResult]:
    """Return the contour history for the current branch.

    Stub implementation: returns a single entry for HEAD.  Full implementation
    will walk the commit chain and return one :class:`ContourResult` per commit,
    newest first.

    Args:
        root:    Repository root.
        session: Open async DB session.
        track:   Named MIDI track, or ``None`` for all.
        section: Named section, or ``None`` for the full piece.

    Returns:
        List of :class:`ContourResult` entries, newest first.
    """
    entry = await _contour_detect_async(
        root=root, session=session, commit=None, track=track, section=section
    )
    return [entry]


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _format_detect(result: ContourResult, *, as_json: bool, shape_only: bool) -> str:
    """Render a detect result as human-readable text or JSON.

    When *shape_only* is True, only the shape label line is printed (useful
    for quick scripting).  When *as_json* is True, the full :class:`ContourResult`
    is serialised as indented JSON.

    Args:
        result:     Analysis result to render.
        as_json:    Emit JSON instead of human-readable text.
        shape_only: Emit the shape label line only (ignored when as_json=True).

    Returns:
        Formatted string ready for ``typer.echo``.
    """
    if as_json:
        return json.dumps(dict(result), indent=2)
    if shape_only:
        return f"Shape: {result['shape']}"
    octaves = result["tessitura"] // 12
    semitones_rem = result["tessitura"] % 12
    range_str = f"{octaves} octave{'s' if octaves != 1 else ''}"
    if semitones_rem:
        range_str += f" + {semitones_rem} st"
    lines = [
        f"Shape: {result['shape']} | Range: {range_str} | "
        f"Phrases: {result['phrase_count']} avg {result['avg_phrase_bars']:.0f} bars",
        f"Commit: {result['commit']}  Branch: {result['branch']}",
        f"Track: {result['track']}  Section: {result['section']}",
        f"Angularity: {result['avg_interval']} st avg interval",
    ]
    if result.get("source") == "stub":
        lines.append("(stub — full MIDI analysis pending)")
    return "\n".join(lines)


def _format_compare(result: ContourCompareResult, *, as_json: bool) -> str:
    """Render a compare result as human-readable text or JSON.

    Args:
        result:  Comparison result to render.
        as_json: Emit JSON instead of human-readable text.

    Returns:
        Formatted string ready for ``typer.echo``.
    """
    if as_json:
        return json.dumps(
            {
                "commit_a": dict(result["commit_a"]),
                "commit_b": dict(result["commit_b"]),
                "shape_changed": result["shape_changed"],
                "angularity_delta": result["angularity_delta"],
                "tessitura_delta": result["tessitura_delta"],
            },
            indent=2,
        )
    a = result["commit_a"]
    b = result["commit_b"]
    ang_delta = result["angularity_delta"]
    sign = "+" if ang_delta >= 0 else ""
    shape_note = " (shape changed)" if result["shape_changed"] else ""
    return (
        f"A ({a['commit']})  Shape: {a['shape']} | Angularity: {a['avg_interval']} st\n"
        f"B ({b['commit']})  Shape: {b['shape']} | Angularity: {b['avg_interval']} st\n"
        f"Delta  angularity {sign}{ang_delta} st | tessitura {result['tessitura_delta']:+d} st"
        + shape_note
    )


def _format_history(entries: list[ContourResult], *, as_json: bool) -> str:
    """Render a contour history list as human-readable text or JSON.

    Args:
        entries: History entries, newest first.
        as_json: Emit JSON instead of human-readable text.

    Returns:
        Formatted string ready for ``typer.echo``.
    """
    if as_json:
        return json.dumps([dict(e) for e in entries], indent=2)
    if not entries:
        return "(no contour history found)"
    lines: list[str] = []
    for entry in entries:
        lines.append(
            f"{entry['commit']}  {entry['shape']} | "
            f"range {entry['tessitura']} st | "
            f"ang {entry['avg_interval']} st"
            + (f"  [{entry['track']}]" if entry["track"] != "all" else "")
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def contour(
    ctx: typer.Context,
    commit: Annotated[
        Optional[str],
        typer.Argument(
            help="Commit SHA to analyse. Defaults to HEAD.",
            show_default=False,
        ),
    ] = None,
    track: Annotated[
        Optional[str],
        typer.Option(
            "--track",
            metavar="TEXT",
            help="Restrict analysis to a named melodic track (e.g. 'keys', 'lead').",
            show_default=False,
        ),
    ] = None,
    section: Annotated[
        Optional[str],
        typer.Option(
            "--section",
            metavar="TEXT",
            help="Scope analysis to a named section (e.g. 'verse', 'chorus').",
            show_default=False,
        ),
    ] = None,
    compare: Annotated[
        Optional[str],
        typer.Option(
            "--compare",
            metavar="COMMIT",
            help="Compare contour against another commit.",
            show_default=False,
        ),
    ] = None,
    history: Annotated[
        bool,
        typer.Option("--history", help="Show contour evolution across all commits."),
    ] = False,
    shape: Annotated[
        bool,
        typer.Option("--shape", help="Print the overall shape label only."),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON output."),
    ] = False,
) -> None:
    """Analyze melodic contour and phrase shape for a composition commit.

    With no flags, analyses HEAD and prints shape, range, phrase count, and
    angularity.  Use ``--compare`` to diff two commits, ``--history`` to see
    how melodic character evolved, and ``--shape`` for a one-line shape label.
    """
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            if history:
                entries = await _contour_history_async(
                    root=root, session=session, track=track, section=section
                )
                typer.echo(_format_history(entries, as_json=as_json))
                return

            if compare is not None:
                compare_result = await _contour_compare_async(
                    root=root,
                    session=session,
                    commit_a=commit,
                    commit_b=compare,
                    track=track,
                    section=section,
                )
                typer.echo(_format_compare(compare_result, as_json=as_json))
                return

            # Default: detect
            detect_result = await _contour_detect_async(
                root=root, session=session, commit=commit, track=track, section=section
            )
            typer.echo(_format_detect(detect_result, as_json=as_json, shape_only=shape))

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse contour failed: {exc}")
        logger.error("❌ muse contour error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
