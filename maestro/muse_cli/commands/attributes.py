"""muse attributes — read and validate the .museattributes configuration file.

A plumbing command that parses the ``.museattributes`` file in the current
repository root and displays the resulting rules in a human-readable table
(or machine-readable JSON).

Usage::

    muse attributes [--json]

Exit codes:
- ``0``: parsed successfully (or file not found — that is not an error).
- ``1``: file is present but contains no valid rules.
- ``2``: internal error.
"""
from __future__ import annotations

import json
import logging

import typer
from typing_extensions import Annotated

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_attributes import (
    MuseAttribute,
    load_attributes,
)

logger = logging.getLogger(__name__)

app = typer.Typer(no_args_is_help=False)


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _format_attributes(attributes: list[MuseAttribute], *, as_json: bool) -> str:
    """Render parsed attributes as a table or JSON."""
    if as_json:
        payload = [
            {
                "track_pattern": a.track_pattern,
                "dimension": a.dimension,
                "strategy": a.strategy.value,
            }
            for a in attributes
        ]
        return json.dumps(payload, indent=2)

    if not attributes:
        return "No rules defined. Create a .museattributes file in the repository root."

    col_widths = (
        max(len(a.track_pattern) for a in attributes),
        max(len(a.dimension) for a in attributes),
        max(len(a.strategy.value) for a in attributes),
    )
    col_widths = (
        max(col_widths[0], len("Track Pattern")),
        max(col_widths[1], len("Dimension")),
        max(col_widths[2], len("Strategy")),
    )

    sep = "  "
    header = sep.join(
        label.ljust(w)
        for label, w in zip(
            ("Track Pattern", "Dimension", "Strategy"), col_widths, strict=True
        )
    )
    divider = sep.join("-" * w for w in col_widths)
    rows = [
        sep.join(
            cell.ljust(w)
            for cell, w in zip(
                (a.track_pattern, a.dimension, a.strategy.value), col_widths, strict=True
            )
        )
        for a in attributes
    ]
    lines = [f".museattributes — {len(attributes)} rule(s)", "", header, divider, *rows]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def attributes_show(
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON output."),
    ] = False,
) -> None:
    """Read and display the .museattributes merge-strategy configuration."""
    root = require_repo()

    try:
        rules = load_attributes(root)
        output = _format_attributes(rules, as_json=as_json)
        typer.echo(output)
        if not rules:
            raise typer.Exit(code=ExitCode.USER_ERROR)
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse attributes failed: {exc}")
        logger.error("❌ muse attributes error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
