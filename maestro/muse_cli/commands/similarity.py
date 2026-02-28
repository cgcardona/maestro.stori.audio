"""muse similarity — compute musical similarity score between two commits.

Compares two Muse commits across up to five musical dimensions and
produces per-dimension scores plus a weighted overall score.  An AI
agent can use this output to calibrate how much new material to generate:
a similarity of 0.9 suggests a small variation; 0.4 suggests a major
rework.

Dimensions
----------
- harmonic   — key, chord vocabulary, chord progression similarity
- rhythmic   — tempo, time signature, rhythmic density
- melodic    — motif reuse, interval contour, pitch range
- structural — section layout (intro, verse, bridge, outro lengths)
- dynamic    — velocity profile, crescendo/decrescendo patterns

Scores are normalized to [0.0, 1.0]:
    1.0 = identical
    0.0 = completely different

Output (default)::

    Similarity: HEAD~10 vs HEAD

    Harmonic:   0.45  ██████████░░░░░░░░░░  (key modulation, new chords)
    Rhythmic:   0.89  ████████████████████  (same tempo, slightly more swing)
    Melodic:    0.72  ██████████████████░░  (same motifs, extended range)
    Structural: 0.65  █████████████░░░░░░░  (bridge added, intro shortened)
    Dynamic:    0.55  ███████████░░░░░░░░░  (much louder, crescendo added)

    Overall:    0.65  (Significantly different — major rework)

Flags
-----
COMMIT-A    First commit ref (required).
COMMIT-B    Second commit ref (required).
--dimensions TEXT    Comma-separated subset of dimensions (default: all five).
--section TEXT       Scope comparison to a named section/region.
--track TEXT         Scope comparison to a specific track.
--json               Emit machine-readable JSON.
--threshold FLOAT    Exit 1 if overall similarity < threshold (for scripting).
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import TypedDict

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode

logger = logging.getLogger(__name__)

app = typer.Typer()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DIMENSION_NAMES: tuple[str, ...] = (
    "harmonic",
    "rhythmic",
    "melodic",
    "structural",
    "dynamic",
)

_ALL_DIMENSIONS: frozenset[str] = frozenset(DIMENSION_NAMES)

# Dimension weights for computing the overall score.
# Harmonic and melodic carry the most musical identity.
_DIMENSION_WEIGHTS: dict[str, float] = {
    "harmonic": 0.25,
    "rhythmic": 0.20,
    "melodic": 0.25,
    "structural": 0.15,
    "dynamic": 0.15,
}

# Score thresholds for the human-readable quality label.
_LABEL_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.90, "Nearly identical — minimal change"),
    (0.75, "Highly similar — subtle variation"),
    (0.60, "Moderately similar — noticeable changes"),
    (0.40, "Significantly different — major rework"),
    (0.00, "Completely different — new direction"),
)

_BAR_WIDTH = 20  # characters in the progress bar


# ---------------------------------------------------------------------------
# Named result types (stable CLI contract)
# ---------------------------------------------------------------------------


class DimensionScore(TypedDict):
    """Score for a single musical dimension.

    Contract:
        dimension — one of the five canonical dimension names
        score     — normalized similarity in [0.0, 1.0]
        note      — brief human-readable interpretation of the difference
    """

    dimension: str
    score: float
    note: str


class SimilarityResult(TypedDict):
    """Overall similarity result between two commits.

    Contract:
        commit_a       — first commit ref as provided by the caller
        commit_b       — second commit ref as provided by the caller
        dimensions     — list of per-dimension scores (may be a subset)
        overall        — weighted overall similarity in [0.0, 1.0]
        label          — human-readable summary of the overall score
        max_divergence — dimension name with the lowest score
    """

    commit_a: str
    commit_b: str
    dimensions: list[DimensionScore]
    overall: float
    label: str
    max_divergence: str


# ---------------------------------------------------------------------------
# Stub data — realistic placeholder until MIDI data is queryable per-commit
# ---------------------------------------------------------------------------

# Stub per-dimension scores and interpretive notes.
_STUB_DIMENSION_DATA: dict[str, tuple[float, str]] = {
    "harmonic": (0.45, "key modulation, new chords"),
    "rhythmic": (0.89, "same tempo, slightly more swing"),
    "melodic": (0.72, "same motifs, extended range"),
    "structural": (0.65, "bridge added, intro shortened"),
    "dynamic": (0.55, "much louder, crescendo added"),
}


def _stub_dimension_scores(dimensions: frozenset[str]) -> list[DimensionScore]:
    """Return stub DimensionScore rows for the requested dimensions.

    The ordering mirrors DIMENSION_NAMES so output is always stable.
    """
    return [
        DimensionScore(
            dimension=dim,
            score=_STUB_DIMENSION_DATA[dim][0],
            note=_STUB_DIMENSION_DATA[dim][1],
        )
        for dim in DIMENSION_NAMES
        if dim in dimensions
    ]


# ---------------------------------------------------------------------------
# Score computation helpers
# ---------------------------------------------------------------------------


def _weighted_overall(scores: list[DimensionScore]) -> float:
    """Compute a weighted overall similarity score.

    Uses _DIMENSION_WEIGHTS when a dimension is in the standard set;
    falls back to equal weighting for any custom/unknown dimension.
    """
    if not scores:
        return 0.0
    total_weight = sum(_DIMENSION_WEIGHTS.get(s["dimension"], 1.0) for s in scores)
    weighted_sum = sum(
        s["score"] * _DIMENSION_WEIGHTS.get(s["dimension"], 1.0) for s in scores
    )
    if total_weight == 0.0:
        return 0.0
    return round(weighted_sum / total_weight, 4)


def _overall_label(overall: float) -> str:
    """Return a human-readable label for an overall similarity score."""
    for threshold, label in _LABEL_THRESHOLDS:
        if overall >= threshold:
            return label
    return _LABEL_THRESHOLDS[-1][1]


def _max_divergence_dimension(scores: list[DimensionScore]) -> str:
    """Return the name of the dimension with the lowest similarity score."""
    if not scores:
        return ""
    return min(scores, key=lambda s: s["score"])["dimension"]


def build_similarity_result(
    commit_a: str,
    commit_b: str,
    scores: list[DimensionScore],
) -> SimilarityResult:
    """Assemble a complete SimilarityResult from scored dimensions.

    Separated from the async core so tests can validate the computation
    without I/O.

    Args:
        commit_a: First commit ref.
        commit_b: Second commit ref.
        scores:   Per-dimension scores (may be a subset of all five).

    Returns:
        A SimilarityResult with all fields populated.
    """
    overall = _weighted_overall(scores)
    return SimilarityResult(
        commit_a=commit_a,
        commit_b=commit_b,
        dimensions=scores,
        overall=overall,
        label=_overall_label(overall),
        max_divergence=_max_divergence_dimension(scores),
    )


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _bar(score: float, width: int = _BAR_WIDTH) -> str:
    """Render a Unicode block progress bar for a score in [0, 1]."""
    filled = round(score * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def render_similarity_text(result: SimilarityResult) -> str:
    """Render a human-readable similarity report.

    Called by the CLI and by tests so the rendering contract can be
    validated independently of Typer.

    Args:
        result: A fully populated SimilarityResult.

    Returns:
        Multi-line string ready to echo to stdout.
    """
    lines: list[str] = [
        f"Similarity: {result['commit_a']} vs {result['commit_b']}",
        "",
    ]

    label_width = max((len(s["dimension"]) for s in result["dimensions"]), default=0) + 1
    for score in result["dimensions"]:
        dim_label = f"{score['dimension'].capitalize()}:".ljust(label_width + 1)
        bar = _bar(score["score"])
        lines.append(
            f"  {dim_label} {score['score']:.2f}  {bar}  ({score['note']})"
        )

    lines.append("")
    lines.append(f"  Overall:    {result['overall']:.2f}  ({result['label']})")

    if result["max_divergence"]:
        lines.append(
            f"  Max divergence: {result['max_divergence']} dimension"
        )

    return "\n".join(lines)


def render_similarity_json(result: SimilarityResult) -> str:
    """Render a SimilarityResult as indented JSON."""
    return json.dumps(dict(result), indent=2)


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _similarity_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit_a: str,
    commit_b: str,
    dimensions: frozenset[str],
    section: Optional[str],
    track: Optional[str],
    threshold: Optional[float],
    as_json: bool,
) -> int:
    """Core similarity logic — fully injectable for tests.

    Resolves both commit refs against the .muse/ directory, produces
    stub per-dimension scores for the requested dimensions, assembles a
    SimilarityResult, renders output, and returns an exit code.

    Args:
        root:       Repository root (directory containing .muse/).
        session:    Open async DB session (reserved for full implementation).
        commit_a:   First commit ref.
        commit_b:   Second commit ref.
        dimensions: Set of dimension names to compute.
        section:    Named section to scope comparison (stub: noted).
        track:      Named track to scope comparison (stub: noted).
        threshold:  Exit 1 if overall < threshold; None means no check.
        as_json:    Emit JSON instead of text.

    Returns:
        Integer exit code — 0 on success, 1 if below threshold.
    """
    muse_dir = root / ".muse"
    _head_ref = (muse_dir / "HEAD").read_text().strip()

    if section:
        typer.echo(
            f"WARNING: --section {section}: section-scoped comparison not yet implemented."
        )
    if track:
        typer.echo(
            f"WARNING: --track {track}: track-scoped comparison not yet implemented."
        )

    scores = _stub_dimension_scores(dimensions)
    result = build_similarity_result(commit_a, commit_b, scores)

    if as_json:
        typer.echo(render_similarity_json(result))
    else:
        typer.echo(render_similarity_text(result))

    if threshold is not None and result["overall"] < threshold:
        return int(1)

    return int(ExitCode.SUCCESS)


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def similarity(
    ctx: typer.Context,
    commit_a: str = typer.Argument(
        ...,
        help="First commit ref to compare.",
        metavar="COMMIT-A",
    ),
    commit_b: str = typer.Argument(
        ...,
        help="Second commit ref to compare.",
        metavar="COMMIT-B",
    ),
    dimensions: Optional[str] = typer.Option(
        None,
        "--dimensions",
        help=(
            "Comma-separated list of dimensions to compare. "
            "Valid: harmonic,rhythmic,melodic,structural,dynamic. "
            "Default: all five."
        ),
        metavar="DIMS",
    ),
    section: Optional[str] = typer.Option(
        None,
        "--section",
        help="Scope comparison to a named section/region.",
        metavar="TEXT",
    ),
    track: Optional[str] = typer.Option(
        None,
        "--track",
        help="Scope comparison to a specific track.",
        metavar="TEXT",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON output.",
    ),
    threshold: Optional[float] = typer.Option(
        None,
        "--threshold",
        help=(
            "Exit 1 if the overall similarity score is below this value. "
            "Useful in scripts to detect major reworks."
        ),
        metavar="FLOAT",
    ),
) -> None:
    """Compute musical similarity score between two commits.

    Produces per-dimension scores (harmonic, rhythmic, melodic, structural,
    dynamic) and a weighted overall score in [0.0, 1.0].

    Example::

        muse similarity HEAD~10 HEAD
        muse similarity HEAD~10 HEAD --dimensions harmonic,rhythmic
        muse similarity HEAD~10 HEAD --json
        muse similarity HEAD~10 HEAD --threshold 0.5
    """
    # -- Validate flags first — before repo detection so bad input fails fast --
    active_dimensions: frozenset[str]
    if dimensions is not None:
        requested = frozenset(
            d.strip().lower() for d in dimensions.split(",") if d.strip()
        )
        invalid = requested - _ALL_DIMENSIONS
        if invalid:
            typer.echo(
                f"Unknown dimension(s): {', '.join(sorted(invalid))}. "
                f"Valid: {', '.join(DIMENSION_NAMES)}"
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)
        if not requested:
            typer.echo("--dimensions must specify at least one dimension.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        active_dimensions = requested
    else:
        active_dimensions = _ALL_DIMENSIONS

    if threshold is not None and not (0.0 <= threshold <= 1.0):
        typer.echo(f"--threshold {threshold!r} out of range [0.0, 1.0].")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    async def _run() -> int:
        async with open_session() as session:
            return await _similarity_async(
                root=root,
                session=session,
                commit_a=commit_a,
                commit_b=commit_b,
                dimensions=active_dimensions,
                section=section,
                track=track,
                threshold=threshold,
                as_json=as_json,
            )

    try:
        exit_code = asyncio.run(_run())
        if exit_code != 0:
            raise typer.Exit(code=exit_code)
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"muse similarity failed: {exc}")
        logger.error("muse similarity error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
