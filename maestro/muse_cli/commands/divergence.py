"""muse divergence — show how two branches have diverged musically.

Computes a per-dimension musical divergence report between two CLI branches.

Usage
-----
::

    muse divergence <branch-a> <branch-b> [OPTIONS]

Options
-------
- ``--since <commit>``       Common ancestor commit ID (auto-detected if omitted).
- ``--dimensions <name>``    Dimension(s) to analyse — may be repeated
                             (default: all five dimensions).
- ``--json``                 Machine-readable JSON output.

Output example (text mode)
--------------------------
::

    Musical Divergence: feature/guitar-version vs feature/piano-version
    Common ancestor: 7e3a1f2c

    Melodic divergence:     HIGH — High melodic divergence — branches took different creative paths.
      feature/guitar-version: 2 melodic file(s) changed
      feature/piano-version:  0 melodic file(s) changed

    Harmonic divergence:    MED  — Moderate harmonic divergence — different directions.
      feature/guitar-version: 1 harmonic file(s) changed
      feature/piano-version:  2 harmonic file(s) changed

    Overall divergence score: 0.60

Output example (JSON mode)
--------------------------
::

    {
      "branch_a": "feature/guitar-version",
      "branch_b": "feature/piano-version",
      "common_ancestor": "7e3a1f2c...",
      "overall_score": 0.60,
      "dimensions": [
        {
          "dimension": "melodic",
          "level": "high",
          "score": 1.0,
          "description": "High melodic divergence — branches took different creative paths.",
          "branch_a_summary": "2 melodic file(s) changed",
          "branch_b_summary": "0 melodic file(s) changed"
        }
      ]
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from typing import Optional, TypedDict

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_divergence import (
    DivergenceLevel,
    MuseDivergenceResult,
    compute_divergence,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


class _DimJsonEntry(TypedDict):
    """JSON-serialisable representation of a single dimension divergence entry."""

    dimension: str
    level: str
    score: float
    description: str
    branch_a_summary: str
    branch_b_summary: str


class _DivergenceJson(TypedDict):
    """JSON-serialisable representation of a full divergence result."""

    branch_a: str
    branch_b: str
    common_ancestor: str | None
    overall_score: float
    dimensions: list[_DimJsonEntry]

_LEVEL_LABELS: dict[DivergenceLevel, str] = {
    DivergenceLevel.NONE: "NONE",
    DivergenceLevel.LOW:  "LOW ",
    DivergenceLevel.MED:  "MED ",
    DivergenceLevel.HIGH: "HIGH",
}


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_text(result: MuseDivergenceResult) -> None:
    """Write a human-readable divergence report via :func:`typer.echo`.

    Args:
        result: The divergence result to render.
    """
    ancestor_short = result.common_ancestor[:8] if result.common_ancestor else "none"
    typer.echo(f"Musical Divergence: {result.branch_a} vs {result.branch_b}")
    typer.echo(f"Common ancestor: {ancestor_short}")
    typer.echo("")
    for dim in result.dimensions:
        label = _LEVEL_LABELS[dim.level]
        typer.echo(
            f"{dim.dimension.capitalize()} divergence:\t{label} — {dim.description}"
        )
        typer.echo(f"  {result.branch_a}: {dim.branch_a_summary}")
        typer.echo(f"  {result.branch_b}: {dim.branch_b_summary}")
        typer.echo("")
    typer.echo(f"Overall divergence score: {result.overall_score:.4f}")


def render_json(result: MuseDivergenceResult) -> None:
    """Write a machine-readable JSON divergence report via :func:`typer.echo`.

    Args:
        result: The divergence result to render.
    """
    data: _DivergenceJson = {
        "branch_a": result.branch_a,
        "branch_b": result.branch_b,
        "common_ancestor": result.common_ancestor,
        "overall_score": result.overall_score,
        "dimensions": [
            {
                "dimension": d.dimension,
                "level": d.level.value,
                "score": d.score,
                "description": d.description,
                "branch_a_summary": d.branch_a_summary,
                "branch_b_summary": d.branch_b_summary,
            }
            for d in result.dimensions
        ],
    }
    typer.echo(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _divergence_async(
    *,
    branch_a: str,
    branch_b: str,
    root: pathlib.Path,
    session: AsyncSession,
    since: str | None,
    dimensions: list[str],
    output_json: bool,
) -> None:
    """Core divergence logic — injectable for unit tests.

    Reads ``repo_id`` from ``.muse/repo.json``, calls :func:`compute_divergence`,
    and writes output via the appropriate renderer.

    Args:
        branch_a:    First branch name.
        branch_b:    Second branch name.
        root:        Repository root (directory containing ``.muse/``).
        session:     Open async DB session.
        since:       Common ancestor commit ID override (``None`` → auto-detect).
        dimensions:  Dimensions to analyse (empty → all).
        output_json: If ``True``, render JSON; otherwise render text.
    """
    muse_dir = root / ".muse"
    repo_data: dict[str, str] = json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]

    try:
        result = await compute_divergence(
            session,
            repo_id=repo_id,
            branch_a=branch_a,
            branch_b=branch_b,
            since=since,
            dimensions=dimensions if dimensions else None,
        )
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if output_json:
        render_json(result)
    else:
        render_text(result)


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def divergence(
    ctx: typer.Context,
    branch_a: str = typer.Argument(..., help="First branch."),
    branch_b: str = typer.Argument(..., help="Second branch."),
    since: Optional[str] = typer.Option(
        None,
        "--since",
        help="Common ancestor commit ID (auto-detected if omitted).",
    ),
    dimensions: list[str] = typer.Option(
        [],
        "--dimensions",
        help="Musical dimension to analyse — may be repeated. Default: all.",
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON.",
    ),
) -> None:
    """Show how two branches have diverged musically.

    Finds the common ancestor of BRANCH_A and BRANCH_B, then reports
    per-dimension musical divergence scores (melodic, harmonic, rhythmic,
    structural, dynamic) and an overall divergence score.
    """
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _divergence_async(
                branch_a=branch_a,
                branch_b=branch_b,
                root=root,
                session=session,
                since=since,
                dimensions=dimensions,
                output_json=output_json,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse divergence failed: {exc}")
        logger.error("❌ muse divergence error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
