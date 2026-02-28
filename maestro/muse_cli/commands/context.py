"""muse context [<commit>] — output structured musical context for AI agent consumption.

Produces a self-contained JSON (or YAML) document describing the full musical
state of the project at a given commit (or HEAD).  This is the primary entry
point for AI agents that need to generate music coherently with an existing
composition — agents run ``muse context --format json`` before generation to
understand the current key, tempo, active tracks, form, and evolutionary history.

Output modes
------------
JSON (default)::

    muse context
    muse context abc1234
    muse context --depth 10 --sections --tracks

YAML::

    muse context --format yaml

Flags
-----
[<commit>]           Optional commit ID (default: HEAD).
--depth N            Include N ancestor commits in the ``history`` array (default: 5).
--sections           Expand section-level detail in ``musical_state.sections``.
--tracks             Add per-track harmonic/dynamic breakdowns.
--include-history    Annotate history entries with dimensional deltas (reserved for
                     future Storpheus integration).
--format json|yaml   Output format (default: json).
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from enum import Enum
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_context import build_muse_context

logger = logging.getLogger(__name__)

app = typer.Typer()


class OutputFormat(str, Enum):
    """Supported output serialisation formats."""

    json = "json"
    yaml = "yaml"


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _context_async(
    *,
    root: "pathlib.Path",
    session: AsyncSession,
    commit_id: Optional[str],
    depth: int,
    sections: bool,
    tracks: bool,
    include_history: bool,
    fmt: OutputFormat,
) -> None:
    """Core context logic — fully injectable for tests.

    Delegates to ``build_muse_context()`` and serialises the result to the
    requested format via ``typer.echo``.

    Args:
        root:            Repository root path.
        session:         Open async DB session.
        commit_id:       Target commit (None = HEAD).
        depth:           Ancestor history depth.
        sections:        Whether to expand section detail.
        tracks:          Whether to include per-track breakdown.
        include_history: Whether to annotate history with dimension deltas.
        fmt:             Output format (json or yaml).
    """
    result = await build_muse_context(
        session,
        root=root,
        commit_id=commit_id,
        depth=depth,
        include_sections=sections,
        include_tracks=tracks,
        include_history=include_history,
    )

    data = result.to_dict()

    if fmt == OutputFormat.yaml:
        try:
            import yaml  # type: ignore[import-untyped]  # PyYAML ships no py.typed marker

            typer.echo(yaml.dump(data, sort_keys=False, allow_unicode=True))
        except ImportError:
            typer.echo(
                "❌ PyYAML is not installed. Install it with: pip install pyyaml",
                err=True,
            )
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
    else:
        typer.echo(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def context(
    ctx: typer.Context,
    commit: Optional[str] = typer.Argument(
        None,
        help="Commit ID to inspect (default: HEAD).",
        metavar="<commit>",
    ),
    depth: int = typer.Option(
        5,
        "--depth",
        help="Number of ancestor commits to include in history.",
        min=0,
    ),
    sections: bool = typer.Option(
        False,
        "--sections",
        help="Expand section-level detail in musical_state.sections.",
    ),
    tracks: bool = typer.Option(
        False,
        "--tracks",
        help="Add per-track harmonic and dynamic breakdowns.",
    ),
    include_history: bool = typer.Option(
        False,
        "--include-history",
        help="Annotate history entries with dimensional deltas (future Storpheus integration).",
    ),
    fmt: OutputFormat = typer.Option(
        OutputFormat.json,
        "--format",
        help="Output format: json (default) or yaml.",
    ),
) -> None:
    """Output structured musical context for AI agent consumption.

    Produces a self-contained document describing the musical state at the
    given commit (or HEAD).  Pipe it to the LLM before generating new music
    to ensure harmonic, rhythmic, and structural coherence.
    """
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _context_async(
                root=root,
                session=session,
                commit_id=commit,
                depth=depth,
                sections=sections,
                tracks=tracks,
                include_history=include_history,
                fmt=fmt,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except (ValueError, RuntimeError) as exc:
        typer.echo(f"❌ muse context: {exc}", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except Exception as exc:
        typer.echo(f"❌ muse context failed: {exc}", err=True)
        logger.error("❌ muse context error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
