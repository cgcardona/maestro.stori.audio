"""muse tempo-scale — stretch or compress the timing of a commit.

Time-scaling (changing tempo while preserving pitch) is a fundamental
production operation: halving time values creates a double-time feel;
doubling them creates a half-time groove.  Because Muse commits track
MIDI note events and tempo metadata, this transformation is applied
deterministically — same factor + same source commit = identical result.

Pitch is *not* affected; this is pure MIDI timing manipulation, not
audio time-stretching.

Command forms
-------------

Scale by an explicit factor (0.5 = half-time, 2.0 = double-time)::

    muse tempo-scale 0.5

Scale to reach an exact BPM (computes factor = target / current BPM)::

    muse tempo-scale --bpm 128

Scale a specific commit instead of HEAD::

    muse tempo-scale 0.5 a1b2c3d4

Scale only one MIDI track::

    muse tempo-scale 2.0 --track bass

Preserve CC/expression timing proportionally::

    muse tempo-scale 0.5 --preserve-expressions

Provide a custom commit message::

    muse tempo-scale 0.5 --message "half-time remix"

Machine-readable JSON output::

    muse tempo-scale 2.0 --json
"""
from __future__ import annotations

import asyncio
import hashlib
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
# Constants
# ---------------------------------------------------------------------------

FACTOR_MIN = 0.01  # below this the result is effectively silence
FACTOR_MAX = 100.0  # above this is unreasonably fast


# ---------------------------------------------------------------------------
# Named result types (stable CLI contract)
# ---------------------------------------------------------------------------


class TempoScaleResult(TypedDict):
    """Result of a tempo-scale operation.

    Returned by ``_tempo_scale_async`` and emitted as JSON when ``--json``
    is given.  Agents should treat ``new_commit`` as the SHA that replaces
    the source commit in the timeline.

    Fields
    ------
    source_commit:
        Short SHA of the input commit.
    new_commit:
        Short SHA of the newly created tempo-scaled commit.
    factor:
        Scaling factor that was applied. ``< 1`` = slower; ``> 1`` = faster.
    source_bpm:
        Tempo of the source commit, in beats per minute (stub: placeholder).
    new_bpm:
        Resulting tempo after scaling.
    track:
        Name of the MIDI track that was scaled, or ``"all"`` if no filter.
    preserve_expressions:
        Whether CC/expression events were scaled proportionally.
    message:
        Commit message for the new scaled commit.
    """

    source_commit: str
    new_commit: str
    factor: float
    source_bpm: float
    new_bpm: float
    track: str
    preserve_expressions: bool
    message: str


# ---------------------------------------------------------------------------
# Pure helper — factor computation from BPM target
# ---------------------------------------------------------------------------


def compute_factor_from_bpm(source_bpm: float, target_bpm: float) -> float:
    """Compute the scaling factor needed to reach *target_bpm* from *source_bpm*.

    Uses the relation: factor = target_bpm / source_bpm.  A factor > 1
    compresses time (faster); < 1 stretches it (slower).

    Args:
        source_bpm: Current tempo (must be > 0).
        target_bpm: Desired tempo in BPM (must be > 0).

    Returns:
        Scaling factor as a float.

    Raises:
        ValueError: If either BPM value is non-positive.
    """
    if source_bpm <= 0:
        raise ValueError(f"source_bpm must be positive, got {source_bpm}")
    if target_bpm <= 0:
        raise ValueError(f"target_bpm must be positive, got {target_bpm}")
    return target_bpm / source_bpm


def apply_factor(bpm: float, factor: float) -> float:
    """Return the new BPM after applying *factor*.

    Args:
        bpm:    Source tempo in BPM.
        factor: Scaling factor (> 0).

    Returns:
        New tempo = ``bpm * factor``, rounded to four decimal places.
    """
    return round(bpm * factor, 4)


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _tempo_scale_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit: Optional[str],
    factor: Optional[float],
    bpm: Optional[float],
    track: Optional[str],
    preserve_expressions: bool,
    message: Optional[str],
) -> TempoScaleResult:
    """Apply tempo scaling to a commit and return the operation result.

    This is a stub implementation that models the correct schema and
    deterministic semantics.  Full MIDI note manipulation will be wired in
    when the Storpheus note-event query route is available.

    The scaling factor is resolved in this order:
    1. If *bpm* is given, compute factor = bpm / source_bpm.
    2. Otherwise use *factor* directly.

    Args:
        root:                  Repository root (directory containing ``.muse/``).
        session:               Open async DB session (reserved for full impl).
        commit:                Source commit SHA; defaults to HEAD.
        factor:                Explicit scaling factor (``None`` when ``--bpm`` used).
        bpm:                   Target BPM (``None`` when factor is used directly).
        track:                 Restrict scaling to a named MIDI track, or ``None``.
        preserve_expressions:  Scale CC/expression events proportionally.
        message:               Commit message for the new commit.

    Returns:
        A :class:`TempoScaleResult` describing the new tempo-scaled commit.

    Raises:
        ValueError: If neither *factor* nor *bpm* is provided, or if the
                    computed factor is outside ``[FACTOR_MIN, FACTOR_MAX]``.
    """
    muse_dir = root / ".muse"
    head_ref = (muse_dir / "HEAD").read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref
    ref_path = muse_dir / pathlib.Path(head_ref)
    head_sha = ref_path.read_text().strip() if ref_path.exists() else "0000000"
    source_commit = commit or (head_sha[:8] if head_sha else "0000000")

    # Stub source BPM — full implementation queries tempo metadata from the commit.
    stub_source_bpm = 120.0

    # Resolve factor
    if bpm is not None:
        resolved_factor = compute_factor_from_bpm(stub_source_bpm, bpm)
    elif factor is not None:
        resolved_factor = factor
    else:
        raise ValueError("Either factor or --bpm must be provided.")

    if not (FACTOR_MIN <= resolved_factor <= FACTOR_MAX):
        raise ValueError(
            f"Computed factor {resolved_factor:.4f} is outside the allowed range "
            f"[{FACTOR_MIN}, {FACTOR_MAX}]."
        )

    new_bpm = apply_factor(stub_source_bpm, resolved_factor)

    # Deterministic stub commit SHA — same inputs always produce the same hash.
    # Full implementation persists note events and tempo metadata to the DB.
    raw = f"{source_commit}:{resolved_factor}:{track or 'all'}:{preserve_expressions}"
    new_commit = hashlib.sha1(raw.encode()).hexdigest()[:8]  # noqa: S324 — not crypto

    resolved_message = message or f"tempo-scale {resolved_factor:.4f}x (stub)"

    logger.info(
        "✅ tempo-scale: %s -> %s  factor=%.4f  %.1f->%.1f BPM  track=%s",
        source_commit,
        new_commit,
        resolved_factor,
        stub_source_bpm,
        new_bpm,
        track or "all",
    )

    return TempoScaleResult(
        source_commit=source_commit,
        new_commit=new_commit,
        factor=round(resolved_factor, 6),
        source_bpm=stub_source_bpm,
        new_bpm=new_bpm,
        track=track or "all",
        preserve_expressions=preserve_expressions,
        message=resolved_message,
    )


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _format_result(result: TempoScaleResult, *, as_json: bool) -> str:
    """Render a TempoScaleResult as human-readable text or compact JSON.

    Args:
        result:  The tempo-scale operation result to render.
        as_json: Emit compact JSON when True; ASCII summary when False.

    Returns:
        Formatted string ready for ``typer.echo``.
    """
    if as_json:
        return json.dumps(dict(result), indent=2)

    factor = result["factor"]
    if factor >= 1:
        display = f"x{factor:.4f}"
    else:
        display = f"/{1.0 / factor:.4f}"
    lines = [
        f"Tempo scaled: {result['source_commit']} -> {result['new_commit']}",
        f"  Factor:  {factor:.4f}  ({display})",
        f"  Tempo:   {result['source_bpm']:.1f} BPM -> {result['new_bpm']:.1f} BPM",
        f"  Track:   {result['track']}",
    ]
    if result["preserve_expressions"]:
        lines.append("  Expressions: scaled proportionally")
    lines.append(f"  Message: {result['message']}")
    lines.append("  (stub -- full MIDI note manipulation pending)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def tempo_scale(
    ctx: typer.Context,
    factor: Annotated[
        Optional[float],
        typer.Argument(
            help=(
                "Scaling factor: 0.5 = half-time (slower), 2.0 = double-time (faster). "
                "Omit when using --bpm."
            ),
            show_default=False,
        ),
    ] = None,
    commit: Annotated[
        Optional[str],
        typer.Argument(
            help="Source commit SHA to scale. Defaults to HEAD.",
            show_default=False,
        ),
    ] = None,
    bpm: Annotated[
        Optional[float],
        typer.Option(
            "--bpm",
            metavar="N",
            help=(
                "Scale to reach exactly N BPM. "
                "Computes factor = N / current_bpm. "
                "Mutually exclusive with the <factor> argument."
            ),
            show_default=False,
        ),
    ] = None,
    track: Annotated[
        Optional[str],
        typer.Option(
            "--track",
            metavar="TEXT",
            help="Scale only a specific MIDI track (useful for individual overdubs).",
            show_default=False,
        ),
    ] = None,
    preserve_expressions: Annotated[
        bool,
        typer.Option(
            "--preserve-expressions",
            help="Preserve CC/expression event timing proportionally.",
        ),
    ] = False,
    message: Annotated[
        Optional[str],
        typer.Option(
            "--message",
            "-m",
            metavar="TEXT",
            help="Commit message for the new tempo-scaled commit.",
            show_default=False,
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON output."),
    ] = False,
) -> None:
    """Stretch or compress the timing of a commit.

    Scales all MIDI note onset/offset times by <factor>, updates tempo
    metadata, and records a new commit.  Pitch is preserved -- this is pure
    MIDI timing manipulation, not audio time-stretching.

    Use ``--bpm N`` instead of <factor> to target an exact tempo.
    """
    root = require_repo()

    # Validate: at least one of factor or --bpm must be given
    if factor is None and bpm is None:
        typer.echo("Provide either a <factor> argument or --bpm N.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Validate: factor and --bpm are mutually exclusive
    if factor is not None and bpm is not None:
        typer.echo("<factor> and --bpm are mutually exclusive. Provide one or the other.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Validate factor range when provided directly
    if factor is not None and not (FACTOR_MIN <= factor <= FACTOR_MAX):
        typer.echo(
            f"Factor {factor!r} is outside the allowed range "
            f"[{FACTOR_MIN}, {FACTOR_MAX}]."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Validate bpm when provided
    if bpm is not None and bpm <= 0:
        typer.echo(f"--bpm must be a positive number, got {bpm!r}.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    async def _run() -> None:
        async with open_session() as session:
            result = await _tempo_scale_async(
                root=root,
                session=session,
                commit=commit,
                factor=factor,
                bpm=bpm,
                track=track,
                preserve_expressions=preserve_expressions,
                message=message,
            )
            typer.echo(_format_result(result, as_json=as_json))

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except Exception as exc:
        typer.echo(f"muse tempo-scale failed: {exc}")
        logger.error("muse tempo-scale error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
