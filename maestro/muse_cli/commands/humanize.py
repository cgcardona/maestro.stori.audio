"""muse humanize — apply micro-timing and velocity humanization to quantized MIDI.

Machine-quantized MIDI sounds robotic. This command adds realistic human-performance
variation — subtle micro-timing offsets and velocity fluctuations — drawn from a
configurable distribution, producing a new Muse commit that sounds natural while
preserving the musical identity of the original.

Presets
-------
``--tight``    Subtle humanization: timing ±5 ms, velocity ±5.
``--natural``  Moderate humanization: timing ±12 ms, velocity ±10.  (default)
``--loose``    Heavy humanization: timing ±20 ms, velocity ±15.

Custom control
--------------
``--factor FLOAT``  0.0 = no change; 1.0 = maximum natural variation (maps to
                    the ``--loose`` ceiling).
``--timing-only``   Apply timing variation only; preserve all velocities.
``--velocity-only`` Apply velocity variation only; preserve all note positions.

Scoping
-------
``--track TEXT``    Restrict humanization to a single named track.
``--section TEXT``  Restrict humanization to a named section/region.

Reproducibility
---------------
``--seed N``  Fix the random seed so the same invocation produces identical
              output every time.  Without ``--seed``, each run is stochastic.

Output
------
Default: human-readable summary table showing per-track timing/velocity deltas.
``--json``    Emit a machine-readable JSON payload — use in agentic pipelines.

Example::

    muse humanize --natural --seed 42
    muse humanize HEAD~1 --loose --track bass
    muse humanize --factor 0.6 --timing-only --json
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import random
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
# Preset constants — timing in ms, velocity in MIDI units (0–127)
# ---------------------------------------------------------------------------

TIGHT_TIMING_MS: int = 5
TIGHT_VELOCITY: int = 5

NATURAL_TIMING_MS: int = 12
NATURAL_VELOCITY: int = 10

LOOSE_TIMING_MS: int = 20
LOOSE_VELOCITY: int = 15

FACTOR_MIN: float = 0.0
FACTOR_MAX: float = 1.0

# Drum channel (General MIDI channel 10, zero-indexed as 9).
# Drum tracks are excluded from timing humanization to preserve groove identity.
DRUM_CHANNEL: int = 9


# ---------------------------------------------------------------------------
# Named result types (stable CLI contract)
# ---------------------------------------------------------------------------


class TrackHumanizeResult(TypedDict):
    """Humanization outcome for a single track."""

    track: str
    timing_range_ms: int
    velocity_range: int
    notes_affected: int
    drum_channel_excluded: bool


class HumanizeResult(TypedDict):
    """Full result emitted by ``muse humanize``.

    Consumed by downstream agents that need to know what changed and by
    how much — e.g. a groove-check agent can compare timing variance
    before/after to confirm humanization took effect.
    """

    commit: str
    branch: str
    source_commit: str
    preset: str
    factor: float
    seed: Optional[int]
    timing_only: bool
    velocity_only: bool
    track_filter: Optional[str]
    section_filter: Optional[str]
    tracks: list[TrackHumanizeResult]
    new_commit_id: str


# ---------------------------------------------------------------------------
# Humanization engine
# ---------------------------------------------------------------------------

#: Stub track names used as placeholders until real MIDI data is queryable.
_STUB_TRACKS: tuple[str, ...] = ("drums", "bass", "keys", "lead")

#: MIDI note count per track (stub placeholder).
_STUB_NOTE_COUNT: int = 64


def _resolve_preset(
    *,
    tight: bool,
    natural: bool,
    loose: bool,
    factor: Optional[float],
) -> tuple[str, float]:
    """Resolve flag combination to a (preset_label, factor) pair.

    Priority: ``--factor`` > ``--tight`` > ``--natural`` > ``--loose``.
    Default when no flag is given: ``natural`` preset.

    Args:
        tight:   ``--tight`` flag.
        natural: ``--natural`` flag.
        loose:   ``--loose`` flag.
        factor:  ``--factor`` float, if provided.

    Returns:
        Tuple of (preset_label, normalized_factor).

    Raises:
        ValueError: If more than one preset flag is set simultaneously.
    """
    preset_count = sum([tight, natural, loose, factor is not None])
    if preset_count > 1 and factor is None:
        raise ValueError(
            "Only one of --tight / --natural / --loose may be specified at a time."
        )

    if factor is not None:
        return ("custom", factor)
    if tight:
        return ("tight", 0.25)
    if loose:
        return ("loose", 1.0)
    # Default: natural
    return ("natural", 0.6)


def _timing_ms_for_factor(factor: float) -> int:
    """Return the timing range in ms for a given factor.

    Linearly interpolates between 0 ms (factor=0.0) and
    ``LOOSE_TIMING_MS`` (factor=1.0).

    Args:
        factor: Normalized humanization factor [0.0, 1.0].

    Returns:
        Timing range in milliseconds (integer, ≥ 0).
    """
    return round(factor * LOOSE_TIMING_MS)


def _velocity_range_for_factor(factor: float) -> int:
    """Return the velocity variation range for a given factor.

    Linearly interpolates between 0 (factor=0.0) and
    ``LOOSE_VELOCITY`` (factor=1.0).

    Args:
        factor: Normalized humanization factor [0.0, 1.0].

    Returns:
        Velocity range in MIDI units (integer, ≥ 0).
    """
    return round(factor * LOOSE_VELOCITY)


def _apply_humanization(
    *,
    track_name: str,
    timing_ms: int,
    velocity_range: int,
    timing_only: bool,
    velocity_only: bool,
    rng: random.Random,
) -> TrackHumanizeResult:
    """Compute humanization deltas for one track.

    Drums (channel 9) are excluded from timing variation to preserve groove
    identity — only velocity humanization is applied to the drum track.

    This is a stub: real implementation will load MIDI notes from the commit
    snapshot, apply the offsets, and write back a new object.  The returned
    ``notes_affected`` count is a realistic placeholder.

    Args:
        track_name:     Name of the MIDI track.
        timing_ms:      Maximum timing offset in milliseconds (±).
        velocity_range: Maximum velocity offset (±).
        timing_only:    If True, skip velocity humanization.
        velocity_only:  If True, skip timing humanization.
        rng:            Seeded random instance for deterministic output.

    Returns:
        :class:`TrackHumanizeResult` with applied delta metadata.
    """
    is_drum = track_name.lower() in {"drums", "percussion", "kit"}

    effective_timing = 0 if (velocity_only or is_drum) else timing_ms
    effective_velocity = 0 if timing_only else velocity_range

    # Stub: simulate a note count between 32 and 128.
    notes_affected = rng.randint(32, 128)

    return TrackHumanizeResult(
        track=track_name,
        timing_range_ms=effective_timing,
        velocity_range=effective_velocity,
        notes_affected=notes_affected,
        drum_channel_excluded=is_drum and not velocity_only,
    )


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _humanize_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    source_commit: Optional[str],
    preset: str,
    factor: float,
    seed: Optional[int],
    timing_only: bool,
    velocity_only: bool,
    track: Optional[str],
    section: Optional[str],
    message: Optional[str],
    as_json: bool,
) -> HumanizeResult:
    """Apply humanization to a commit's MIDI and emit the result.

    Stub implementation: computes per-track humanization metadata from the
    resolved factor and produces a new (fake) commit ID.  The full
    implementation will:

    1. Load MIDI notes from the source commit's snapshot.
    2. Apply Gaussian-distributed timing offsets (in ticks) and velocity
       deltas drawn from a uniform distribution within ±range.
    3. Write the modified notes back as a new snapshot object.
    4. Commit the snapshot via the Muse VCS commit engine.

    Args:
        root:          Repository root (containing ``.muse/``).
        session:       Open async DB session (reserved for full implementation).
        source_commit: Source commit ref, or ``None`` for HEAD.
        preset:        Preset label (``tight``, ``natural``, ``loose``, ``custom``).
        factor:        Normalized humanization factor [0.0, 1.0].
        seed:          Random seed for deterministic output; ``None`` = stochastic.
        timing_only:   If True, skip velocity humanization.
        velocity_only: If True, skip timing humanization.
        track:         Restrict to a single named track; ``None`` = all tracks.
        section:       Restrict to a named section/region (stub: noted in output).
        message:       Commit message override; ``None`` = auto-generated.
        as_json:       Emit JSON instead of ASCII table.

    Returns:
        :class:`HumanizeResult` with full metadata for agent consumption.
    """
    muse_dir = root / ".muse"

    # Resolve branch and HEAD commit.
    head_ref = (muse_dir / "HEAD").read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref
    ref_path = muse_dir / pathlib.Path(head_ref)
    head_sha = ref_path.read_text().strip() if ref_path.exists() else ""
    resolved_source = source_commit or (head_sha[:8] if head_sha else "HEAD")

    timing_ms = _timing_ms_for_factor(factor)
    vel_range = _velocity_range_for_factor(factor)

    # Seed-based RNG for deterministic humanization.
    rng = random.Random(seed)

    # Determine which tracks to process.
    all_tracks = list(_STUB_TRACKS)
    if track:
        all_tracks = [t for t in all_tracks if t.lower().startswith(track.lower())]

    if section:
        logger.warning("⚠️  --section %s: region scoping not yet implemented.", section)

    track_results = [
        _apply_humanization(
            track_name=t,
            timing_ms=timing_ms,
            velocity_range=vel_range,
            timing_only=timing_only,
            velocity_only=velocity_only,
            rng=rng,
        )
        for t in all_tracks
    ]

    # Stub: generate a deterministic fake commit ID.
    fake_commit_seed = rng.randint(0, 0xFFFFFFFF)
    new_commit_id = f"{fake_commit_seed:08x}stub"

    result = HumanizeResult(
        commit=resolved_source,
        branch=branch,
        source_commit=resolved_source,
        preset=preset,
        factor=factor,
        seed=seed,
        timing_only=timing_only,
        velocity_only=velocity_only,
        track_filter=track,
        section_filter=section,
        tracks=track_results,
        new_commit_id=new_commit_id,
    )

    if as_json:
        _render_json(result)
    else:
        _render_table(result)

    logger.info("✅ muse humanize: created commit %s", new_commit_id)
    return result


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

_COL_WIDTHS = (12, 12, 10, 14, 16)  # track, timing_ms, vel_range, notes, drum_excluded


def _render_table(result: HumanizeResult) -> None:
    """Emit a human-readable summary of the humanization pass."""
    seed_note = f"  seed={result['seed']}" if result["seed"] is not None else ""
    typer.echo(
        f"Humanize — {result['preset']} (factor={result['factor']:.2f})"
        f"  source={result['source_commit']}{seed_note}"
    )
    typer.echo("")

    header = (
        f"{'Track':<{_COL_WIDTHS[0]}}  "
        f"{'Timing ±ms':>{_COL_WIDTHS[1]}}  "
        f"{'Vel ±':>{_COL_WIDTHS[2]}}  "
        f"{'Notes':>{_COL_WIDTHS[3]}}  "
        f"{'Drum excluded':<{_COL_WIDTHS[4]}}"
    )
    sep = "  ".join("-" * w for w in _COL_WIDTHS)
    typer.echo(header)
    typer.echo(sep)

    for tr in result["tracks"]:
        drum_flag = "yes" if tr["drum_channel_excluded"] else "no"
        typer.echo(
            f"{tr['track']:<{_COL_WIDTHS[0]}}  "
            f"{tr['timing_range_ms']:>{_COL_WIDTHS[1]}}  "
            f"{tr['velocity_range']:>{_COL_WIDTHS[2]}}  "
            f"{tr['notes_affected']:>{_COL_WIDTHS[3]}}  "
            f"{drum_flag:<{_COL_WIDTHS[4]}}"
        )

    typer.echo("")
    typer.echo(f"✅ New commit: {result['new_commit_id']}")
    typer.echo("   (stub — full MIDI rewrite pending Storpheus note-level access)")


def _render_json(result: HumanizeResult) -> None:
    """Emit the humanization result as a JSON object."""
    typer.echo(json.dumps(dict(result), indent=2))


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def humanize(
    ctx: typer.Context,
    source_commit: Annotated[
        Optional[str],
        typer.Argument(
            help="Source commit ref to humanize (default: HEAD).",
            show_default=False,
            metavar="COMMIT",
        ),
    ] = None,
    tight: Annotated[
        bool,
        typer.Option(
            "--tight",
            help=f"Subtle humanization: timing ±{TIGHT_TIMING_MS} ms, velocity ±{TIGHT_VELOCITY}.",
        ),
    ] = False,
    natural: Annotated[
        bool,
        typer.Option(
            "--natural",
            help=f"Moderate humanization: timing ±{NATURAL_TIMING_MS} ms, velocity ±{NATURAL_VELOCITY}. (default)",
        ),
    ] = False,
    loose: Annotated[
        bool,
        typer.Option(
            "--loose",
            help=f"Heavy humanization: timing ±{LOOSE_TIMING_MS} ms, velocity ±{LOOSE_VELOCITY}.",
        ),
    ] = False,
    factor: Annotated[
        Optional[float],
        typer.Option(
            "--factor",
            help="Custom factor: 0.0 = no change, 1.0 = maximum variation. Overrides preset flags.",
            min=FACTOR_MIN,
            max=FACTOR_MAX,
            show_default=False,
        ),
    ] = None,
    timing_only: Annotated[
        bool,
        typer.Option(
            "--timing-only",
            help="Apply timing variation only; preserve all velocities.",
        ),
    ] = False,
    velocity_only: Annotated[
        bool,
        typer.Option(
            "--velocity-only",
            help="Apply velocity variation only; preserve all note positions.",
        ),
    ] = False,
    track: Annotated[
        Optional[str],
        typer.Option(
            "--track",
            help="Restrict humanization to a specific track (case-insensitive prefix match).",
            show_default=False,
            metavar="TEXT",
        ),
    ] = None,
    section: Annotated[
        Optional[str],
        typer.Option(
            "--section",
            help="Restrict humanization to a specific section/region.",
            show_default=False,
            metavar="TEXT",
        ),
    ] = None,
    seed: Annotated[
        Optional[int],
        typer.Option(
            "--seed",
            help="Random seed for reproducible humanization. Without --seed, output is stochastic.",
            show_default=False,
        ),
    ] = None,
    message: Annotated[
        Optional[str],
        typer.Option(
            "--message",
            "-m",
            help="Commit message for the humanization commit.",
            show_default=False,
            metavar="TEXT",
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit machine-readable JSON output for agent consumption.",
        ),
    ] = False,
) -> None:
    """Apply micro-timing and velocity humanization to quantized MIDI.

    Produces a new Muse commit with realistic human-performance variation.
    Use ``--seed`` for deterministic output; omit it for a fresh stochastic
    pass each invocation.  Drum tracks are automatically excluded from timing
    variation to preserve groove identity.
    """
    root = require_repo()

    # Validate mutually exclusive flags.
    if timing_only and velocity_only:
        typer.echo(
            "❌ --timing-only and --velocity-only are mutually exclusive. "
            "Omit one or both to apply full humanization."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    preset_flags = sum([tight, natural, loose])
    if preset_flags > 1:
        typer.echo("❌ Only one of --tight / --natural / --loose may be specified at a time.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    try:
        preset_label, resolved_factor = _resolve_preset(
            tight=tight,
            natural=natural,
            loose=loose,
            factor=factor,
        )
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    async def _run() -> None:
        async with open_session() as session:
            await _humanize_async(
                root=root,
                session=session,
                source_commit=source_commit,
                preset=preset_label,
                factor=resolved_factor,
                seed=seed,
                timing_only=timing_only,
                velocity_only=velocity_only,
                track=track,
                section=section,
                message=message,
                as_json=as_json,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse humanize failed: {exc}")
        logger.error("❌ muse humanize error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
