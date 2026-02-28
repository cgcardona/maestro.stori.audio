"""muse form — analyze and display the musical form of a commit.

Musical form is the large-scale structural blueprint of a composition:
the ordering and labelling of sections (intro, verse, chorus, bridge,
outro, etc.) that define how a piece unfolds over time.

Command forms
-------------

Detect form on HEAD (default)::

    muse form

Detect form at a specific commit::

    muse form a1b2c3d4

Annotate the current working tree with an explicit form string::

    muse form --set "verse-chorus-verse-chorus-bridge-chorus"

Show a section timeline (map view)::

    muse form --map

Show how the form changed across commits::

    muse form --history

Machine-readable JSON output::

    muse form --json

Flags
-----
``[<commit>]``   Target commit ref (default: HEAD).
``--set TEXT``   Annotate with an explicit form string (e.g. "AABA", "verse-chorus").
``--detect``     Auto-detect form from section repetition patterns (default).
``--map``        Show the section arrangement as a visual timeline.
``--history``    Show how the form changed across commits.
``--json``       Machine-readable output.

Section vocabulary
------------------
intro, verse, pre-chorus, chorus, bridge, breakdown, outro, A, B, C

Detection heuristic
-------------------
Sections with identical content fingerprints are assigned the same label
(A, B, C...).  Named roles (verse, chorus, etc.) are inferred from MIDI
metadata stored in ``.muse/sections/`` when available; otherwise uppercase
letter labels are used.

Result type
-----------
``FormAnalysisResult`` (TypedDict) -- stable schema for agent consumers.
``FormHistoryEntry``   (TypedDict) -- wraps FormAnalysisResult with commit metadata.

See ``docs/reference/type_contracts.md S FormAnalysisResult``.
"""
from __future__ import annotations

import asyncio
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
# Section label vocabulary
# ---------------------------------------------------------------------------

#: Canonical role labels -- used when MIDI metadata provides named sections.
ROLE_LABELS: tuple[str, ...] = (
    "intro",
    "verse",
    "pre-chorus",
    "chorus",
    "bridge",
    "breakdown",
    "outro",
)

#: Structural letter labels -- used when roles cannot be inferred.
LETTER_LABELS: tuple[str, ...] = tuple("ABCDEFGHIJ")

_VALID_ROLES: frozenset[str] = frozenset(ROLE_LABELS)


# ---------------------------------------------------------------------------
# Named result types (stable CLI contract)
# ---------------------------------------------------------------------------


class FormSection(TypedDict):
    """A single structural unit within the detected form."""

    label: str
    role: str
    index: int


class FormAnalysisResult(TypedDict):
    """Full form analysis for one commit."""

    commit: str
    branch: str
    form_string: str
    sections: list[FormSection]
    source: str


class FormHistoryEntry(TypedDict):
    """Form analysis result paired with its position in the commit history."""

    position: int
    result: FormAnalysisResult


# ---------------------------------------------------------------------------
# Stub data -- realistic placeholder until section metadata is queryable
# ---------------------------------------------------------------------------

_STUB_SECTIONS: list[tuple[str, str]] = [
    ("intro", "intro"),
    ("A", "verse"),
    ("B", "chorus"),
    ("A", "verse"),
    ("B", "chorus"),
    ("C", "bridge"),
    ("B", "chorus"),
    ("outro", "outro"),
]


def _stub_form_sections() -> list[FormSection]:
    """Return stub FormSection entries (placeholder for real DB/file query).

    The stub models a common verse-chorus-verse-chorus-bridge-chorus structure,
    which is the most frequent form in contemporary pop/R&B production.
    """
    return [
        FormSection(label=label, role=role, index=i)
        for i, (label, role) in enumerate(_STUB_SECTIONS)
    ]


def _sections_to_form_string(sections: list[FormSection]) -> str:
    """Convert a section list into the canonical pipe-separated form string.

    Example: ``"intro | A | B | A | B | C | B | outro"``

    Args:
        sections: Ordered list of FormSection entries.

    Returns:
        Human-readable form string.
    """
    return " | ".join(s["label"] for s in sections)


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _render_form_text(result: FormAnalysisResult) -> str:
    """Render a form result as human-readable text.

    Args:
        result: Populated FormAnalysisResult.

    Returns:
        Multi-line string ready for typer.echo.
    """
    head_label = f"  (HEAD -> {result['branch']})" if result["branch"] else ""
    lines = [
        f"Musical form -- commit {result['commit']}{head_label}",
        "",
        f"  {result['form_string']}",
        "",
        "Sections:",
    ]
    for sec in result["sections"]:
        role_hint = f"  [{sec['role']}]" if sec["role"] != sec["label"] else ""
        lines.append(f"  {sec['index'] + 1:>2}. {sec['label']:<12}{role_hint}")
    if result.get("source") == "stub":
        lines.append("")
        lines.append("  (stub -- full section analysis pending)")
    return "\n".join(lines)


def _render_map_text(result: FormAnalysisResult) -> str:
    """Render the section arrangement as a visual timeline.

    Produces a compact horizontal timeline where each section occupies a
    fixed-width cell, making structural repetition immediately visible.

    Args:
        result: Populated FormAnalysisResult.

    Returns:
        Multi-line string ready for typer.echo.
    """
    head_label = f"  (HEAD -> {result['branch']})" if result["branch"] else ""
    cell_w = 10
    sections = result["sections"]
    top = "+" + "+".join("-" * cell_w for _ in sections) + "+"
    mid = "|" + "|".join(s["label"][:cell_w].center(cell_w) for s in sections) + "|"
    bot = "+" + "+".join("-" * cell_w for _ in sections) + "+"
    nums = " " + " ".join(str(s["index"] + 1).center(cell_w) for s in sections)
    lines = [
        f"Form map -- commit {result['commit']}{head_label}",
        "",
        top,
        mid,
        bot,
        nums,
    ]
    if result.get("source") == "stub":
        lines.append("")
        lines.append("(stub -- full section analysis pending)")
    return "\n".join(lines)


def _render_history_text(entries: list[FormHistoryEntry]) -> str:
    """Render the form history as a chronological list.

    Args:
        entries: List of FormHistoryEntry from newest to oldest.

    Returns:
        Multi-line string ready for typer.echo.
    """
    if not entries:
        return "(no form history found)"
    lines: list[str] = []
    for entry in entries:
        r = entry["result"]
        lines.append(f"  #{entry['position']}  {r['commit']}  {r['form_string']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _form_detect_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit: Optional[str],
) -> FormAnalysisResult:
    """Detect the musical form for a given commit (or HEAD).

    Stub implementation: resolves the branch/commit from ``.muse/HEAD`` and
    returns a placeholder verse-chorus-bridge structure.  Full analysis will
    read section fingerprints from ``.muse/sections/`` and compare content
    hashes to assign repeated-section labels automatically.

    Args:
        root:    Repository root (directory containing ``.muse/``).
        session: Open async DB session (reserved for full implementation).
        commit:  Commit SHA to analyse, or None for HEAD.

    Returns:
        A FormAnalysisResult with commit, branch, form_string, sections, source.
    """
    muse_dir = root / ".muse"
    head_path = muse_dir / "HEAD"
    head_ref = head_path.read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref

    ref_path = muse_dir / pathlib.Path(head_ref)
    head_sha = ref_path.read_text().strip() if ref_path.exists() else "0000000"
    resolved_commit = commit or (head_sha[:8] if head_sha else "HEAD")

    sections = _stub_form_sections()
    form_string = _sections_to_form_string(sections)

    return FormAnalysisResult(
        commit=resolved_commit,
        branch=branch,
        form_string=form_string,
        sections=sections,
        source="stub",
    )


async def _form_set_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    form_value: str,
) -> FormAnalysisResult:
    """Store an explicit form annotation for the current working tree.

    Parses the user-supplied form string (e.g. "AABA" or
    "verse-chorus-bridge") into FormSection entries and records the
    annotation.  The stub writes the annotation to
    ``.muse/form_annotation.json``; the full implementation will attach it
    to the pending commit object.

    Args:
        root:       Repository root.
        session:    Open async DB session.
        form_value: Explicit form string supplied via --set.

    Returns:
        A FormAnalysisResult representing the stored annotation.
    """
    muse_dir = root / ".muse"
    head_path = muse_dir / "HEAD"
    head_ref = head_path.read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref

    # Parse pipe-separated, hyphen-separated, or space-separated tokens.
    if "|" in form_value:
        tokens = [t.strip() for t in form_value.split("|") if t.strip()]
    elif "-" in form_value and not any(c in form_value for c in (" ", "|")):
        tokens = [t.strip() for t in form_value.split("-") if t.strip()]
    else:
        tokens = [t.strip() for t in form_value.split() if t.strip()]

    sections: list[FormSection] = [
        FormSection(
            label=tok,
            role=tok.lower() if tok.lower() in _VALID_ROLES else tok,
            index=i,
        )
        for i, tok in enumerate(tokens)
    ]
    reconstructed = _sections_to_form_string(sections)

    # Stub: persist to .muse/form_annotation.json
    annotation_path = muse_dir / "form_annotation.json"
    annotation_path.write_text(
        json.dumps(
            {
                "form_string": reconstructed,
                "sections": [dict(s) for s in sections],
                "source": "annotation",
            },
            indent=2,
        )
    )

    return FormAnalysisResult(
        commit="",
        branch=branch,
        form_string=reconstructed,
        sections=sections,
        source="annotation",
    )


async def _form_history_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
) -> list[FormHistoryEntry]:
    """Return the form history for the current branch.

    Stub implementation returning a single HEAD entry.  Full implementation
    will walk the commit chain and aggregate form annotations stored per-commit
    in ``.muse/objects/``, surfacing structural restructures as distinct entries.

    Args:
        root:    Repository root.
        session: Open async DB session.

    Returns:
        List of FormHistoryEntry entries, newest first.
    """
    head_result = await _form_detect_async(root=root, session=session, commit=None)
    return [FormHistoryEntry(position=1, result=head_result)]


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def form(
    ctx: typer.Context,
    commit: Annotated[
        Optional[str],
        typer.Argument(
            help="Commit ref to analyse (default: HEAD).",
            show_default=False,
        ),
    ] = None,
    set_form: Annotated[
        Optional[str],
        typer.Option(
            "--set",
            help=(
                "Annotate with an explicit form string "
                "(e.g. \"AABA\", \"verse-chorus-bridge\", \"Intro | A | B | A\")."
            ),
            show_default=False,
        ),
    ] = None,
    detect: Annotated[
        bool,
        typer.Option(
            "--detect",
            help="Auto-detect form from section repetition patterns (default).",
        ),
    ] = True,
    map_flag: Annotated[
        bool,
        typer.Option(
            "--map",
            help="Show the section arrangement as a visual timeline.",
        ),
    ] = False,
    history: Annotated[
        bool,
        typer.Option(
            "--history",
            help="Show how the form changed across commits.",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON output."),
    ] = False,
) -> None:
    """Analyze and display the musical form of a composition.

    With no flags, detects and displays the musical form for the HEAD commit.
    Use --set to persist an explicit form annotation.  Use --map to
    visualise the section layout as a timeline.  Use --history to see how
    the form evolved across the commit chain.
    """
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            if set_form is not None:
                result = await _form_set_async(
                    root=root, session=session, form_value=set_form
                )
                if as_json:
                    typer.echo(json.dumps(dict(result), indent=2))
                else:
                    typer.echo(f"Form annotated: {result['form_string']}")
                return

            if history:
                entries = await _form_history_async(root=root, session=session)
                if as_json:
                    payload = [
                        {"position": e["position"], "result": dict(e["result"])}
                        for e in entries
                    ]
                    typer.echo(json.dumps(payload, indent=2))
                else:
                    typer.echo("Form history (newest first):")
                    typer.echo("")
                    typer.echo(_render_history_text(entries))
                return

            # Default or --detect: show the form for the target commit.
            result = await _form_detect_async(
                root=root, session=session, commit=commit
            )

            if as_json:
                typer.echo(json.dumps(dict(result), indent=2))
            elif map_flag:
                typer.echo(_render_map_text(result))
            else:
                typer.echo(_render_form_text(result))

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"muse form failed: {exc}")
        logger.error("❌ muse form error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
