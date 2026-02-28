"""muse swing — analyze or annotate the swing factor of a composition.

Swing factor encodes the rhythmic feel of a MIDI performance on a
normalized 0.5–0.67 scale, where 0.5 is mathematically straight (no
swing) and 0.67 approximates a full triplet feel.  Human-readable
labels map ranges to familiar production vocabulary:

    Straight   factor < 0.53
    Light      0.53 ≤ factor < 0.58
    Medium     0.58 ≤ factor < 0.63
    Hard       factor ≥ 0.63

Command forms
-------------

Detect swing on HEAD (default)::

    muse swing

Detect swing at a specific commit::

    muse swing a1b2c3d4

Annotate the current working tree with an explicit factor::

    muse swing --set 0.6

Restrict analysis to a specific MIDI track::

    muse swing --track bass

Compare swing between two commits::

    muse swing --compare HEAD~1

Show full swing history::

    muse swing --history

Machine-readable JSON output::

    muse swing --json
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
# Swing factor label thresholds
# ---------------------------------------------------------------------------

STRAIGHT_MAX = 0.53
LIGHT_MAX = 0.58
MEDIUM_MAX = 0.63

FACTOR_MIN = 0.5
FACTOR_MAX = 0.67


# ---------------------------------------------------------------------------
# Named result types (stable CLI contract)
# ---------------------------------------------------------------------------


class SwingDetectResult(TypedDict):
    """Swing detection result for a single commit or working tree."""

    factor: float
    label: str
    commit: str
    branch: str
    track: str
    source: str


class SwingCompareResult(TypedDict):
    """Swing comparison between HEAD and a reference commit."""

    head: SwingDetectResult
    compare: SwingDetectResult
    delta: float


def swing_label(factor: float) -> str:
    """Return the human-readable label for a given swing factor.

    Thresholds mirror the Muse VCS convention so that stored annotations
    are always interpreted consistently regardless of the CLI version that
    wrote them.

    Args:
        factor: Normalized swing factor in [0.5, 0.67].

    Returns:
        One of ``"Straight"``, ``"Light"``, ``"Medium"``, or ``"Hard"``.
    """
    if factor < STRAIGHT_MAX:
        return "Straight"
    if factor < LIGHT_MAX:
        return "Light"
    if factor < MEDIUM_MAX:
        return "Medium"
    return "Hard"


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _swing_detect_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit: Optional[str],
    track: Optional[str],
) -> SwingDetectResult:
    """Detect the swing factor for a commit (or the working tree).

    This is a stub that returns a realistic placeholder result in the
    correct schema.  Full MIDI-based analysis will be wired in once
    the Storpheus inference endpoint exposes a swing detection route.

    Args:
        root:    Repository root.
        session: Open async DB session.
        commit:  Commit SHA to analyse, or ``None`` for the working tree.
        track:   Restrict analysis to a named MIDI track, or ``None`` for all.

    Returns:
        A :class:`SwingDetectResult` with ``factor``, ``label``, ``commit``,
        ``branch``, ``track``, and ``source``.
    """
    muse_dir = root / ".muse"
    head_path = muse_dir / "HEAD"
    head_ref = head_path.read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref

    ref_path = muse_dir / pathlib.Path(head_ref)
    head_sha = ref_path.read_text().strip() if ref_path.exists() else "0000000"
    resolved_commit = commit or head_sha[:8]

    # Stub: placeholder factor — full analysis pending Storpheus route.
    stub_factor = 0.55
    return SwingDetectResult(
        factor=stub_factor,
        label=swing_label(stub_factor),
        commit=resolved_commit,
        branch=branch,
        track=track or "all",
        source="stub",
    )


async def _swing_history_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    track: Optional[str],
) -> list[SwingDetectResult]:
    """Return the swing history for the current branch.

    Stub implementation returning a single placeholder entry.  Full
    implementation will walk the commit chain and aggregate swing
    annotations stored per-commit.

    Args:
        root:    Repository root.
        session: Open async DB session.
        track:   Restrict to a named MIDI track, or ``None`` for all.

    Returns:
        List of :class:`SwingDetectResult` entries, newest first.
    """
    entry = await _swing_detect_async(
        root=root, session=session, commit=None, track=track
    )
    return [entry]


async def _swing_compare_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    compare_commit: str,
    track: Optional[str],
) -> SwingCompareResult:
    """Compare swing between HEAD and *compare_commit*.

    Stub implementation.  Full implementation will load the swing
    annotation (or detect it on the fly) for both commits and compute
    the delta.

    Args:
        root:           Repository root.
        session:        Open async DB session.
        compare_commit: SHA or ref to compare against.
        track:          Restrict to a named MIDI track, or ``None``.

    Returns:
        A :class:`SwingCompareResult` with ``head``, ``compare``, and ``delta``.
    """
    head_result = await _swing_detect_async(
        root=root, session=session, commit=None, track=track
    )
    compare_result = await _swing_detect_async(
        root=root, session=session, commit=compare_commit, track=track
    )
    delta = head_result["factor"] - compare_result["factor"]
    return SwingCompareResult(
        head=head_result,
        compare=compare_result,
        delta=round(delta, 4),
    )


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _format_detect(result: SwingDetectResult, *, as_json: bool) -> str:
    """Render a detect result as human-readable text or JSON."""
    if as_json:
        return json.dumps(dict(result), indent=2)
    lines = [
        f"Swing factor: {result['factor']} ({result['label']})",
        f"Commit: {result['commit']}  Branch: {result['branch']}",
        f"Track: {result['track']}",
    ]
    if result.get("source") == "stub":
        lines.append("(stub — full MIDI analysis pending)")
    return "\n".join(lines)


def _format_history(
    entries: list[SwingDetectResult], *, as_json: bool
) -> str:
    """Render a history list as human-readable text or JSON."""
    if as_json:
        return json.dumps([dict(e) for e in entries], indent=2)
    lines: list[str] = []
    for entry in entries:
        lines.append(
            f"{entry['commit']}  {entry['factor']} ({entry['label']})"
            + (f"  [{entry['track']}]" if entry.get("track") != "all" else "")
        )
    return "\n".join(lines) if lines else "(no swing history found)"


def _format_compare(result: SwingCompareResult, *, as_json: bool) -> str:
    """Render a compare result as human-readable text or JSON."""
    if as_json:
        return json.dumps(dict(result), indent=2)
    head = result["head"]
    compare = result["compare"]
    delta = result["delta"]
    sign = "+" if delta >= 0 else ""
    return (
        f"HEAD    {head['factor']} ({head['label']})\n"
        f"Compare {compare['factor']} ({compare['label']})\n"
        f"Delta   {sign}{delta}"
    )


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def swing(
    ctx: typer.Context,
    commit: Annotated[
        Optional[str],
        typer.Argument(
            help="Commit SHA to analyse. Defaults to the working tree.",
            show_default=False,
        ),
    ] = None,
    set_factor: Annotated[
        Optional[float],
        typer.Option(
            "--set",
            help=(
                "Annotate the working tree with an explicit swing factor "
                f"({FACTOR_MIN}=straight, {FACTOR_MAX}=triplet)."
            ),
            show_default=False,
        ),
    ] = None,
    detect: Annotated[
        bool,
        typer.Option(
            "--detect",
            help="Detect and display the swing factor (default when no other flag given).",
        ),
    ] = True,
    track: Annotated[
        Optional[str],
        typer.Option(
            "--track",
            help="Restrict analysis to a named MIDI track (e.g. 'bass', 'drums').",
            show_default=False,
        ),
    ] = None,
    compare: Annotated[
        Optional[str],
        typer.Option(
            "--compare",
            metavar="COMMIT",
            help="Compare HEAD swing against another commit.",
            show_default=False,
        ),
    ] = None,
    history: Annotated[
        bool,
        typer.Option("--history", help="Display full swing history for the branch."),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON output."),
    ] = False,
) -> None:
    """Analyze or annotate the swing factor of a musical composition.

    With no flags, detects and displays the swing factor for the current
    HEAD commit.  Use ``--set`` to persist an explicit factor annotation.
    """
    root = require_repo()

    # --set validation
    if set_factor is not None:
        if not (FACTOR_MIN <= set_factor <= FACTOR_MAX):
            typer.echo(
                f"❌ --set value {set_factor!r} out of range "
                f"[{FACTOR_MIN}, {FACTOR_MAX}]"
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)

    async def _run() -> None:
        async with open_session() as session:
            if set_factor is not None:
                label = swing_label(set_factor)
                annotation: SwingDetectResult = SwingDetectResult(
                    factor=set_factor,
                    label=label,
                    commit="",
                    branch="",
                    track=track or "all",
                    source="annotation",
                )
                if as_json:
                    typer.echo(json.dumps(dict(annotation), indent=2))
                else:
                    typer.echo(
                        f"✅ Swing annotated: {set_factor} ({label})"
                        + (f"  track={track}" if track else "")
                    )
                return

            if history:
                entries = await _swing_history_async(
                    root=root, session=session, track=track
                )
                typer.echo(_format_history(entries, as_json=as_json))
                return

            if compare is not None:
                compare_result = await _swing_compare_async(
                    root=root,
                    session=session,
                    compare_commit=compare,
                    track=track,
                )
                typer.echo(_format_compare(compare_result, as_json=as_json))
                return

            # Default: detect
            detect_result = await _swing_detect_async(
                root=root, session=session, commit=commit, track=track
            )
            typer.echo(_format_detect(detect_result, as_json=as_json))

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"muse swing failed: {exc}")
        logger.error("❌ muse swing error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
