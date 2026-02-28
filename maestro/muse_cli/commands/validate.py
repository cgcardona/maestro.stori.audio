"""muse validate — check musical integrity of the working tree.

Runs a suite of integrity checks against the Muse working tree and reports
issues in a structured format.  Designed as the pre-commit quality gate so
agents and producers can catch problems before ``muse commit`` records bad
state into history.

Checks performed
----------------
- **midi_integrity** — every .mid/.midi file has a valid Standard MIDI File header.
- **manifest_consistency** — working tree matches the committed snapshot manifest.
- **no_duplicate_tracks** — no two MIDI files share the same instrument role.
- **section_naming** — section subdirectories follow ``[a-z][a-z0-9_-]*``.
- **emotion_tags** — emotion tags (if present) are from the allowed vocabulary.

Exit codes
----------
- 0 — all checks passed (clean working tree)
- 1 — one or more ERROR issues found
- 2 — one or more WARN issues found and ``--strict`` was passed
- 3 — internal error (unexpected exception)

Output (default human-readable)::

    Validating working tree …

    ✅ midi_integrity        PASS
    ❌ manifest_consistency  FAIL
       ERROR  beat.mid  File in committed manifest is missing from working tree.
    ✅ no_duplicate_tracks   PASS
    ⚠️  section_naming       WARN
       WARN   Verse  Section directory 'Verse' does not follow naming convention.
    ✅ emotion_tags           PASS

    1 error, 1 warning — working tree has integrity issues.

Flags
-----
--strict          Fail (exit 2) on warnings as well as errors.
--track TEXT      Restrict checks to files/paths containing TEXT.
--section TEXT    Restrict section-naming check to directories containing TEXT.
--fix             Auto-fix correctable issues (quantisation, manifest).
--json            Emit full results as JSON for agent consumption.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_validate import (
    MuseValidateResult,
    ValidationSeverity,
    run_validate,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

_SEVERITY_ICON: dict[str, str] = {
    ValidationSeverity.ERROR: "❌",
    ValidationSeverity.WARN: "⚠️ ",
    ValidationSeverity.INFO: "ℹ️ ",
}


def _render_human(result: MuseValidateResult) -> None:
    """Print a human-readable validate report to stdout."""
    typer.echo("Validating working tree …")
    typer.echo("")

    for check in result.checks:
        if check.passed:
            icon = "✅"
            label = "PASS"
        elif any(i.severity == ValidationSeverity.ERROR for i in check.issues):
            icon = "❌"
            label = "FAIL"
        else:
            icon = "⚠️ "
            label = "WARN"

        typer.echo(f"  {icon} {check.name:<28} {label}")
        for issue in check.issues:
            sev_icon = _SEVERITY_ICON.get(issue.severity, "  ")
            typer.echo(f"       {sev_icon} {issue.severity.upper():<6}  {issue.path}")
            typer.echo(f"              {issue.message}")

    typer.echo("")

    if result.fixes_applied:
        typer.echo("Fixes applied:")
        for fix in result.fixes_applied:
            typer.echo(f"  ✅ {fix}")
        typer.echo("")

    if result.clean:
        typer.echo("✅ Working tree is clean — all checks passed.")
    else:
        errors = sum(
            1
            for c in result.checks
            for i in c.issues
            if i.severity == ValidationSeverity.ERROR
        )
        warnings = sum(
            1
            for c in result.checks
            for i in c.issues
            if i.severity == ValidationSeverity.WARN
        )
        parts: list[str] = []
        if errors:
            parts.append(f"{errors} error{'s' if errors != 1 else ''}")
        if warnings:
            parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
        typer.echo(f"{'❌' if result.has_errors else '⚠️ '} {', '.join(parts)} — working tree has integrity issues.")


def _render_json(result: MuseValidateResult) -> None:
    """Emit the validate result as a JSON object."""
    typer.echo(json.dumps(result.to_dict(), indent=2))


# ---------------------------------------------------------------------------
# Exit code resolution
# ---------------------------------------------------------------------------

def _exit_code(result: MuseValidateResult, strict: bool) -> int:
    """Map a MuseValidateResult to the appropriate CLI exit code.

    Args:
        result: The aggregated validation result.
        strict: When True, warnings are treated as errors (exit 2).

    Returns:
        Integer exit code: 0=clean, 1=errors, 2=warnings-in-strict-mode.
    """
    if result.has_errors:
        return ExitCode.USER_ERROR
    if strict and result.has_warnings:
        return 2
    return ExitCode.SUCCESS


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def validate(
    ctx: typer.Context,
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Fail (exit 2) on warnings as well as errors.",
    ),
    track: Optional[str] = typer.Option(
        None,
        "--track",
        help="Restrict checks to files/paths whose relative path contains TEXT.",
        metavar="TEXT",
    ),
    section: Optional[str] = typer.Option(
        None,
        "--section",
        help="Restrict section-naming check to directories containing TEXT.",
        metavar="TEXT",
    ),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Auto-fix correctable issues (e.g. re-quantize off-grid notes).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit full results as JSON for agent consumption.",
    ),
) -> None:
    """Check musical integrity of the working tree before committing."""
    root = require_repo()

    try:
        result = run_validate(
            root,
            strict=strict,
            track_filter=track,
            section_filter=section,
            auto_fix=fix,
        )
    except Exception as exc:
        typer.echo(f"❌ muse validate failed: {exc}")
        logger.error("❌ muse validate error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if as_json:
        _render_json(result)
    else:
        _render_human(result)

    code = _exit_code(result, strict=strict)
    if code != ExitCode.SUCCESS:
        raise typer.Exit(code=code)
