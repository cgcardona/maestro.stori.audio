"""muse inspect [<ref>] — print structured JSON of the Muse commit graph.

Serializes the full commit graph reachable from a starting reference (default:
HEAD) into machine-readable output.  Three formats are supported:

JSON (default)::

    muse inspect
    muse inspect abc1234 --depth 10
    muse inspect --branches --format json

Graphviz DOT::

    muse inspect --format dot | dot -Tsvg -o graph.svg

Mermaid.js::

    muse inspect --format mermaid

Flags
-----
[<ref>]          Optional starting commit or branch name (default: HEAD).
--depth N        Limit traversal to N commits per branch (default: unlimited).
--branches       Include all branch heads and their reachable commits.
--tags           Include tag refs in the output (branch pointers always included).
--format         Output format: json (default), dot, mermaid.
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
from typing import Optional

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_inspect import (
    InspectFormat,
    MuseInspectResult,
    build_inspect_result,
    render_dot,
    render_json,
    render_mermaid,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _inspect_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    ref: Optional[str],
    depth: Optional[int],
    branches: bool,
    fmt: InspectFormat,
) -> MuseInspectResult:
    """Core inspect logic — fully injectable for tests.

    Delegates graph traversal to :func:`~maestro.services.muse_inspect.build_inspect_result`
    and renders the result to the requested format via ``typer.echo``.

    Args:
        root:     Repository root path.
        session:  Open async DB session.
        ref:      Starting commit reference (None = HEAD).
        depth:    Maximum commits per branch (None = unlimited).
        branches: Whether to traverse all branches.
        fmt:      Output format (json, dot, mermaid).

    Returns:
        The :class:`~maestro.services.muse_inspect.MuseInspectResult` so tests
        can assert on the data model without parsing printed output.
    """
    result = await build_inspect_result(
        session,
        root,
        ref=ref,
        depth=depth,
        include_branches=branches,
    )

    if fmt == InspectFormat.dot:
        typer.echo(render_dot(result))
    elif fmt == InspectFormat.mermaid:
        typer.echo(render_mermaid(result))
    else:
        typer.echo(render_json(result))

    return result


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def inspect(
    ctx: typer.Context,
    ref: Optional[str] = typer.Argument(
        None,
        help="Starting commit ID or branch name (default: HEAD).",
        metavar="<ref>",
    ),
    depth: Optional[int] = typer.Option(
        None,
        "--depth",
        help="Limit graph traversal to N commits per branch (default: unlimited).",
        min=1,
    ),
    branches: bool = typer.Option(
        False,
        "--branches",
        help="Include all branch heads and their reachable commits.",
    ),
    tags: bool = typer.Option(
        False,
        "--tags",
        help="Include tag refs in the output (currently branch pointers always included).",
    ),
    fmt: InspectFormat = typer.Option(
        InspectFormat.json,
        "--format",
        help="Output format: json (default), dot, mermaid.",
    ),
) -> None:
    """Print structured output of the Muse commit graph.

    Serializes the full commit graph reachable from the starting reference
    (default: HEAD) into machine-readable output.  Use ``--format json`` (the
    default) for agent consumption, ``--format dot`` for Graphviz, or
    ``--format mermaid`` for GitHub markdown embedding.
    """
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _inspect_async(
                root=root,
                session=session,
                ref=ref,
                depth=depth,
                branches=branches,
                fmt=fmt,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except (ValueError, FileNotFoundError) as exc:
        typer.echo(f"❌ muse inspect: {exc}", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except Exception as exc:
        typer.echo(f"❌ muse inspect failed: {exc}", err=True)
        logger.error("❌ muse inspect error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
