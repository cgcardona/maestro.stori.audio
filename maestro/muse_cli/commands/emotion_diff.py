"""muse emotion-diff — compare emotion vectors between two commits.

Answers "how did the emotional character of my composition change?" by comparing
two commits' emotion profiles.  When explicit ``emotion:*`` tags exist (set via
``muse tag add emotion:<label> <commit>``), those are diffed directly.  When tags
are absent, the engine infers an emotion vector from available musical metadata
(tempo, commit annotation) and reports it alongside an ``[inferred]`` notice.

Usage
-----
::

    # Compare HEAD~1 to HEAD (most common usage)
    muse emotion-diff

    # Compare specific commits
    muse emotion-diff a1b2c3d4 f9e8d7c6

    # Scope to keyboard tracks only
    muse emotion-diff HEAD~1 HEAD --track keys

    # Machine-readable JSON for agent consumption
    muse emotion-diff HEAD~5 HEAD --json

Output example (text mode)
--------------------------
::

    Emotion diff — a1b2c3d4 → f9e8d7c6
    Source: explicit_tags

    Commit A (a1b2c3d4):  melancholic
    Commit B (f9e8d7c6):  joyful

    Dimension      Commit A   Commit B   Delta
    -----------    --------   --------   -----
    energy           0.3000     0.8000  +0.5000
    valence          0.3000     0.9000  +0.6000
    tension          0.4000     0.2000  -0.2000
    darkness         0.6000     0.1000  -0.5000

    Drift: 0.9747 (major)
    melancholic → joyful (+valence, -darkness) [explicit_tags]

Output example (JSON mode)
--------------------------
::

    {
      "commit_a": "a1b2c3d4",
      "commit_b": "f9e8d7c6",
      "source": "explicit_tags",
      "label_a": "melancholic",
      "label_b": "joyful",
      "vector_a": {"energy": 0.3, "valence": 0.3, "tension": 0.4, "darkness": 0.6},
      "vector_b": {"energy": 0.8, "valence": 0.9, "tension": 0.2, "darkness": 0.1},
      "dimensions": [...],
      "drift": 0.9747,
      "narrative": "...",
      "track": null,
      "section": null
    }

Flags
-----
``COMMIT_A``      First (baseline) commit ref. Default: HEAD~1.
``COMMIT_B``      Second (target) commit ref. Default: HEAD.
``--track TEXT``  Scope analysis to a specific track (noted; full per-track
                  scoping requires MIDI content — tracked as follow-up).
``--section TEXT`` Scope to a named section (same stub note as --track).
``--json``        Emit structured JSON for agent or tool consumption.
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
from maestro.services.muse_emotion_diff import (
    EmotionDiffResult,
    EmotionVector,
    compute_emotion_diff,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

# ---------------------------------------------------------------------------
# JSON serialisation types
# ---------------------------------------------------------------------------


class _VectorJson(TypedDict):
    """JSON representation of an :class:`~maestro.services.muse_emotion_diff.EmotionVector`."""

    energy: float
    valence: float
    tension: float
    darkness: float


class _DimDeltaJson(TypedDict):
    """JSON representation of a single dimension delta."""

    dimension: str
    value_a: float
    value_b: float
    delta: float


class _EmotionDiffJson(TypedDict):
    """JSON representation of a full emotion-diff result."""

    commit_a: str
    commit_b: str
    source: str
    label_a: str | None
    label_b: str | None
    vector_a: _VectorJson | None
    vector_b: _VectorJson | None
    dimensions: list[_DimDeltaJson]
    drift: float
    narrative: str
    track: str | None
    section: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vec_to_json(vec: EmotionVector) -> _VectorJson:
    return {
        "energy": vec.energy,
        "valence": vec.valence,
        "tension": vec.tension,
        "darkness": vec.darkness,
    }


def _resolve_branch(root: pathlib.Path) -> str:
    """Read the current branch name from ``.muse/HEAD``."""
    head_file = root / ".muse" / "HEAD"
    if not head_file.exists():
        return "main"
    head_ref = head_file.read_text().strip()
    return head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref


def _resolve_repo_id(root: pathlib.Path) -> str:
    """Read repo_id from ``.muse/repo.json``."""
    repo_json = root / ".muse" / "repo.json"
    data: dict[str, str] = json.loads(repo_json.read_text())
    return data["repo_id"]


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

_COL_WIDTHS = (11, 9, 9, 8)  # dimension, commit_a, commit_b, delta


def render_text(result: EmotionDiffResult) -> None:
    """Write a human-readable emotion-diff table via :func:`typer.echo`.

    Args:
        result: The emotion-diff result to render.
    """
    typer.echo(f"Emotion diff — {result.commit_a} → {result.commit_b}")
    typer.echo(f"Source: {result.source}")
    typer.echo("")

    label_a_str = result.label_a or "(inferred)"
    label_b_str = result.label_b or "(inferred)"
    typer.echo(f"Commit A ({result.commit_a}):  {label_a_str}")
    typer.echo(f"Commit B ({result.commit_b}):  {label_b_str}")

    if result.track:
        typer.echo(f"Track filter: {result.track}")
        typer.echo("⚠️  Per-track emotion scoping not yet implemented — showing full-commit vectors.")
    if result.section:
        typer.echo(f"Section filter: {result.section}")
        typer.echo("⚠️  Section-scoped emotion analysis not yet implemented.")

    typer.echo("")

    if result.vector_a is None or result.vector_b is None:
        typer.echo("⚠️  One or both commits have no emotion data available.")
        return

    # Header
    header = (
        f"{'Dimension':<{_COL_WIDTHS[0]}}  "
        f"{'Commit A':>{_COL_WIDTHS[1]}}  "
        f"{'Commit B':>{_COL_WIDTHS[2]}}  "
        f"{'Delta':>{_COL_WIDTHS[3]}}"
    )
    sep = (
        f"{'-' * _COL_WIDTHS[0]}  "
        f"{'-' * _COL_WIDTHS[1]}  "
        f"{'-' * _COL_WIDTHS[2]}  "
        f"{'-' * _COL_WIDTHS[3]}"
    )
    typer.echo(header)
    typer.echo(sep)

    for dim in result.dimensions:
        sign = "+" if dim.delta > 0 else ""
        typer.echo(
            f"{dim.dimension:<{_COL_WIDTHS[0]}}  "
            f"{dim.value_a:>{_COL_WIDTHS[1]}.4f}  "
            f"{dim.value_b:>{_COL_WIDTHS[2]}.4f}  "
            f"{sign}{dim.delta:>{_COL_WIDTHS[3] - 1}.4f}"
        )

    typer.echo("")
    typer.echo(f"Drift: {result.drift:.4f}")
    typer.echo(result.narrative)


def render_json(result: EmotionDiffResult) -> None:
    """Write a machine-readable JSON emotion-diff report via :func:`typer.echo`.

    Args:
        result: The emotion-diff result to render.
    """
    payload: _EmotionDiffJson = {
        "commit_a": result.commit_a,
        "commit_b": result.commit_b,
        "source": result.source,
        "label_a": result.label_a,
        "label_b": result.label_b,
        "vector_a": _vec_to_json(result.vector_a) if result.vector_a else None,
        "vector_b": _vec_to_json(result.vector_b) if result.vector_b else None,
        "dimensions": [
            {
                "dimension": d.dimension,
                "value_a": d.value_a,
                "value_b": d.value_b,
                "delta": d.delta,
            }
            for d in result.dimensions
        ],
        "drift": result.drift,
        "narrative": result.narrative,
        "track": result.track,
        "section": result.section,
    }
    typer.echo(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _emotion_diff_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit_a: str,
    commit_b: str,
    track: str | None,
    section: str | None,
    as_json: bool,
) -> None:
    """Core emotion-diff logic — fully injectable for tests.

    Reads repository configuration from ``.muse/``, delegates to
    :func:`~maestro.services.muse_emotion_diff.compute_emotion_diff`, and
    renders the result in text or JSON format.

    Args:
        root:      Repository root (directory containing ``.muse/``).
        session:   Open async DB session.
        commit_a:  First commit ref (baseline).
        commit_b:  Second commit ref (target).
        track:     Optional track name filter.
        section:   Optional section name filter.
        as_json:   If ``True``, render JSON; otherwise render text table.
    """
    branch = _resolve_branch(root)
    repo_id = _resolve_repo_id(root)

    try:
        result = await compute_emotion_diff(
            session,
            repo_id=repo_id,
            commit_a=commit_a,
            commit_b=commit_b,
            branch=branch,
            track=track,
            section=section,
        )
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if as_json:
        render_json(result)
    else:
        render_text(result)


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def emotion_diff(
    ctx: typer.Context,
    commit_a: str = typer.Argument(
        "HEAD~1",
        help="Baseline commit ref (default: HEAD~1).",
        metavar="COMMIT_A",
    ),
    commit_b: str = typer.Argument(
        "HEAD",
        help="Target commit ref (default: HEAD).",
        metavar="COMMIT_B",
    ),
    track: Optional[str] = typer.Option(
        None,
        "--track",
        help="Scope analysis to a specific track (case-insensitive prefix match).",
        metavar="TEXT",
    ),
    section: Optional[str] = typer.Option(
        None,
        "--section",
        help="Scope analysis to a named section/region.",
        metavar="TEXT",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON for agent or tool consumption.",
    ),
) -> None:
    """Compare emotion vectors between two commits.

    Reads ``emotion:*`` tags on COMMIT_A and COMMIT_B and reports the shift
    in emotional space.  When explicit tags are absent, infers emotion from
    available musical metadata and notes the inference source.

    Defaults to comparing HEAD~1 against HEAD so that ``muse emotion-diff``
    shows how the most recent commit changed the emotional character.
    """
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _emotion_diff_async(
                root=root,
                session=session,
                commit_a=commit_a,
                commit_b=commit_b,
                track=track,
                section=section,
                as_json=as_json,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse emotion-diff failed: {exc}")
        logger.error("❌ muse emotion-diff error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
