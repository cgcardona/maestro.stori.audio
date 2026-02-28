"""muse dynamics — analyze the dynamic profile of a commit.

Examines velocity data across tracks for a given commit (defaults to HEAD)
and reports per-track statistics with an arc classification.

Output (default tabular)::

    Dynamic profile — commit a1b2c3d4  (HEAD -> main)

    Track      Avg Vel  Peak  Range  Arc
    ---------  -------  ----  -----  -----------
    drums           88   110     42  terraced
    bass            72    85     28  flat
    keys            64    95     56  crescendo
    lead            79   105     38  swell

Arc vocabulary
--------------
- flat        — velocity variance < 10; steady throughout
- crescendo   — monotonically rising from start to end
- decrescendo — monotonically falling from start to end
- terraced    — step-wise plateaus; sudden jumps between stable levels
- swell       — rises then falls (arch shape)

Flags
-----
--track TEXT     Filter to a single track (case-insensitive prefix match).
--section TEXT   Restrict analysis to a named section/region.
--compare COMMIT Compare dynamics of <commit> against <COMMIT>.
--history        Print dynamics for every commit in the branch history.
--peak           Show only tracks whose peak velocity exceeds the branch average.
--range          Sort output by velocity range (descending).
--arc            Filter to only tracks matching the arc label given via --track.
--json           Emit results as JSON instead of the ASCII table.
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
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode

logger = logging.getLogger(__name__)

app = typer.Typer()

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ArcLabel = str  # one of: flat | crescendo | decrescendo | terraced | swell

_ARC_LABELS: tuple[str, ...] = ("flat", "crescendo", "decrescendo", "terraced", "swell")

_VALID_ARCS: frozenset[str] = frozenset(_ARC_LABELS)


class TrackDynamics:
    """Dynamic profile for a single track."""

    __slots__ = ("name", "avg_velocity", "peak_velocity", "velocity_range", "arc")

    def __init__(
        self,
        name: str,
        avg_velocity: int,
        peak_velocity: int,
        velocity_range: int,
        arc: ArcLabel,
    ) -> None:
        self.name = name
        self.avg_velocity = avg_velocity
        self.peak_velocity = peak_velocity
        self.velocity_range = velocity_range
        self.arc = arc

    def to_dict(self) -> dict[str, object]:
        return {
            "track": self.name,
            "avg_velocity": self.avg_velocity,
            "peak_velocity": self.peak_velocity,
            "velocity_range": self.velocity_range,
            "arc": self.arc,
        }


# ---------------------------------------------------------------------------
# Stub data — realistic placeholder until MIDI note data is queryable
# ---------------------------------------------------------------------------

_STUB_TRACKS: list[tuple[str, int, int, int, ArcLabel]] = [
    ("drums", 88, 110, 42, "terraced"),
    ("bass", 72, 85, 28, "flat"),
    ("keys", 64, 95, 56, "crescendo"),
    ("lead", 79, 105, 38, "swell"),
]


def _stub_profiles() -> list[TrackDynamics]:
    """Return stub TrackDynamics rows (placeholder for real DB query)."""
    return [
        TrackDynamics(
            name=name,
            avg_velocity=avg,
            peak_velocity=peak,
            velocity_range=rng,
            arc=arc,
        )
        for name, avg, peak, rng, arc in _STUB_TRACKS
    ]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_COL_WIDTHS = (9, 7, 4, 5, 11)  # track, avg, peak, range, arc


def _render_table(rows: list[TrackDynamics], commit_ref: str, branch: str) -> None:
    """Print a human-readable ASCII table of dynamics."""
    head_label = f"  (HEAD -> {branch})" if branch else ""
    typer.echo(f"Dynamic profile — commit {commit_ref}{head_label}")
    typer.echo("")

    # Header
    header = (
        f"{'Track':<{_COL_WIDTHS[0]}}  "
        f"{'Avg Vel':>{_COL_WIDTHS[1]}}  "
        f"{'Peak':>{_COL_WIDTHS[2]}}  "
        f"{'Range':>{_COL_WIDTHS[3]}}  "
        f"{'Arc':<{_COL_WIDTHS[4]}}"
    )
    sep = (
        f"{'-' * _COL_WIDTHS[0]}  "
        f"{'-' * _COL_WIDTHS[1]}  "
        f"{'-' * _COL_WIDTHS[2]}  "
        f"{'-' * _COL_WIDTHS[3]}  "
        f"{'-' * _COL_WIDTHS[4]}"
    )
    typer.echo(header)
    typer.echo(sep)

    for row in rows:
        typer.echo(
            f"{row.name:<{_COL_WIDTHS[0]}}  "
            f"{row.avg_velocity:>{_COL_WIDTHS[1]}}  "
            f"{row.peak_velocity:>{_COL_WIDTHS[2]}}  "
            f"{row.velocity_range:>{_COL_WIDTHS[3]}}  "
            f"{row.arc:<{_COL_WIDTHS[4]}}"
        )
    typer.echo("")


def _render_json(
    rows: list[TrackDynamics],
    commit_ref: str,
    branch: str,
) -> None:
    """Emit dynamics as a JSON object."""
    payload = {
        "commit": commit_ref,
        "branch": branch,
        "tracks": [r.to_dict() for r in rows],
    }
    typer.echo(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _dynamics_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit: Optional[str],
    track: Optional[str],
    section: Optional[str],
    compare: Optional[str],
    history: bool,
    peak: bool,
    range_flag: bool,
    arc: bool,
    as_json: bool,
) -> None:
    """Core dynamics analysis logic — fully injectable for tests.

    Stub implementation: reads branch/commit metadata from ``.muse/``,
    applies flag-driven filters to placeholder velocity data, and emits
    a formatted table or JSON payload.

    Args:
        root:       Repository root (directory containing ``.muse/``).
        session:    Open async DB session (reserved for full implementation).
        commit:     Commit ref to analyse; defaults to HEAD.
        track:      Case-insensitive prefix filter; only matching tracks shown.
        section:    Restrict analysis to a named region (future: pass to query).
        compare:    Second commit ref for side-by-side comparison (stub: noted).
        history:    If True, show dynamics for every commit in branch history.
        peak:       If True, show only tracks whose peak exceeds branch average.
        range_flag: If True, sort by velocity range descending.
        arc:        If True, filter tracks to the arc matching the --track value.
        as_json:    Emit JSON instead of ASCII table.
    """
    muse_dir = root / ".muse"

    # -- Resolve branch / commit ref --
    head_ref = (muse_dir / "HEAD").read_text().strip()  # "refs/heads/main"
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref
    ref_path = muse_dir / pathlib.Path(head_ref)

    head_commit_id = ""
    if ref_path.exists():
        head_commit_id = ref_path.read_text().strip()

    commit_ref = commit or (head_commit_id[:8] if head_commit_id else "HEAD")

    if not head_commit_id and not commit:
        typer.echo(f"No commits yet on branch {branch} — nothing to analyse.")
        raise typer.Exit(code=ExitCode.SUCCESS)

    # -- Stub: produce placeholder profiles --
    profiles = _stub_profiles()

    # -- Apply --track filter --
    if track:
        prefix = track.lower()
        if arc:
            # --arc mode: filter to tracks whose arc matches the --track value as arc label
            if prefix not in _VALID_ARCS:
                typer.echo(
                    f"⚠️  '{track}' is not a valid arc label. "
                    f"Valid arcs: {', '.join(sorted(_VALID_ARCS))}"
                )
                raise typer.Exit(code=ExitCode.USER_ERROR)
            profiles = [p for p in profiles if p.arc == prefix]
        else:
            profiles = [p for p in profiles if p.name.lower().startswith(prefix)]

    # -- Apply --peak filter (show only above-average peak tracks) --
    if peak and profiles:
        avg_peak = sum(p.peak_velocity for p in profiles) / len(profiles)
        profiles = [p for p in profiles if p.peak_velocity > avg_peak]

    # -- Apply --range sort --
    if range_flag:
        profiles = sorted(profiles, key=lambda p: p.velocity_range, reverse=True)

    # -- --history mode: note the stub boundary --
    if history:
        typer.echo(
            f"⚠️  --history: full commit-chain dynamics not yet implemented. "
            f"Showing HEAD ({commit_ref}) only."
        )

    # -- --compare note --
    if compare:
        typer.echo(
            f"⚠️  --compare {compare}: side-by-side comparison not yet implemented."
        )

    # -- --section note --
    if section:
        typer.echo(f"⚠️  --section {section}: region filtering not yet implemented.")

    # -- Render --
    if not profiles:
        typer.echo("No tracks match the specified filters.")
        return

    if as_json:
        _render_json(profiles, commit_ref=commit_ref, branch=branch)
    else:
        _render_table(profiles, commit_ref=commit_ref, branch=branch)


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def dynamics(
    ctx: typer.Context,
    commit: Optional[str] = typer.Argument(
        None,
        help="Commit ref to analyse (default: HEAD).",
        metavar="COMMIT",
    ),
    track: Optional[str] = typer.Option(
        None,
        "--track",
        help="Filter to a single track (case-insensitive prefix match).",
        metavar="TEXT",
    ),
    section: Optional[str] = typer.Option(
        None,
        "--section",
        help="Restrict analysis to a named section/region.",
        metavar="TEXT",
    ),
    compare: Optional[str] = typer.Option(
        None,
        "--compare",
        help="Compare dynamics against a second commit.",
        metavar="COMMIT",
    ),
    history: bool = typer.Option(
        False,
        "--history",
        help="Show dynamics for every commit in branch history.",
    ),
    peak: bool = typer.Option(
        False,
        "--peak",
        help="Show only tracks whose peak velocity exceeds the branch average.",
    ),
    range_flag: bool = typer.Option(
        False,
        "--range",
        help="Sort output by velocity range (descending).",
    ),
    arc: bool = typer.Option(
        False,
        "--arc",
        help="When combined with --track, treat the value as an arc label filter.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit results as JSON instead of the ASCII table.",
    ),
) -> None:
    """Analyse the dynamic (velocity) profile of a commit."""
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _dynamics_async(
                root=root,
                session=session,
                commit=commit,
                track=track,
                section=section,
                compare=compare,
                history=history,
                peak=peak,
                range_flag=range_flag,
                arc=arc,
                as_json=as_json,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse dynamics failed: {exc}")
        logger.error("❌ muse dynamics error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
