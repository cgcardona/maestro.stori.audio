"""muse chord-map — visualize the chord progression embedded in a commit.

Extracts and displays the chord timeline of a specific commit, showing
when each chord occurs in the arrangement.  This gives an AI agent a
precise picture of the harmonic structure at any commit — not just
*which* chords are present, but *where exactly* each chord falls.

Output (default text, ``--bar-grid``)::

    Chord map — commit a1b2c3d4  (HEAD -> main)

    Bar  1: Cmaj9       ████████
    Bar  2: Am11        ████████
    Bar  3: Dm7         ████    Gsus4   ████
    Bar  4: G7          ████████
    Bar  5: Cmaj9       ████████

With ``--voice-leading``::

    Chord map — commit a1b2c3d4  (HEAD -> main)

    Bar  1: Cmaj9  → Am11  (E→E, G→G, B→A, D→C)
    Bar  2: Am11   → Dm7   (A→D, C→C, E→F, G→A)
    ...

Flags
-----
``COMMIT``          Commit ref to analyse (default: HEAD).
``--section TEXT``  Scope to a named section/region.
``--track TEXT``    Scope to a specific track (e.g. piano for chord voicings).
``--bar-grid``      Align chord events to musical bar numbers (default: on).
``--format FORMAT`` Output format: ``text`` (default), ``json``, or ``mermaid``.
``--voice-leading`` Show how individual notes move between consecutive chords.
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
# Named result types  (registered in docs/reference/type_contracts.md)
# ---------------------------------------------------------------------------


class ChordEvent(TypedDict):
    """A single chord occurrence in the arrangement timeline.

    Fields:
        bar:      Musical bar number (1-indexed, or 0 if not bar-aligned).
        beat:     Beat within the bar (1-indexed).
        chord:    Chord symbol, e.g. ``"Cmaj9"``, ``"Am11"``, ``"G7"``.
        duration: Duration in bars (fractional for chords shorter than one bar).
        track:    Track/instrument the chord belongs to (or ``"all"``).
    """

    bar: int
    beat: int
    chord: str
    duration: float
    track: str


class VoiceLeadingStep(TypedDict):
    """Voice-leading movement from one chord to the next.

    Fields:
        from_chord:  Source chord symbol.
        to_chord:    Target chord symbol.
        from_bar:    Bar where the source chord begins.
        to_bar:      Bar where the target chord begins.
        movements:   List of ``"NoteFrom->NoteTo"`` strings per voice.
    """

    from_chord: str
    to_chord: str
    from_bar: int
    to_bar: int
    movements: list[str]


class ChordMapResult(TypedDict):
    """Full chord-map result for a commit.

    Fields:
        commit:        Short commit ref (8 chars).
        branch:        Branch name at HEAD.
        track:         Track filter applied (``"all"`` if none).
        section:       Section filter applied (empty string if none).
        chords:        Ordered list of :class:`ChordEvent` entries.
        voice_leading: Ordered list of :class:`VoiceLeadingStep` entries
                       (empty unless ``--voice-leading`` was requested).
    """

    commit: str
    branch: str
    track: str
    section: str
    chords: list[ChordEvent]
    voice_leading: list[VoiceLeadingStep]


# ---------------------------------------------------------------------------
# Valid output formats
# ---------------------------------------------------------------------------

_VALID_FORMATS: frozenset[str] = frozenset({"text", "json", "mermaid"})

# ---------------------------------------------------------------------------
# Stub chord data — realistic placeholder until MIDI analysis is wired in
# ---------------------------------------------------------------------------

_STUB_CHORDS: list[tuple[int, int, str, float]] = [
    (1, 1, "Cmaj9", 1.0),
    (2, 1, "Am11", 1.0),
    (3, 1, "Dm7", 0.5),
    (3, 3, "Gsus4", 0.5),
    (4, 1, "G7", 1.0),
    (5, 1, "Cmaj9", 1.0),
]

_STUB_VOICE_LEADING: list[tuple[str, str, int, int, list[str]]] = [
    ("Cmaj9", "Am11", 1, 2, ["E->E", "G->G", "B->A", "D->C"]),
    ("Am11", "Dm7", 2, 3, ["A->D", "C->C", "E->F", "G->A"]),
    ("Dm7", "Gsus4", 3, 3, ["D->G", "F->G", "A->D", "C->C"]),
    ("Gsus4", "G7", 3, 4, ["G->G", "D->D", "C->B", "G->F"]),
    ("G7", "Cmaj9", 4, 5, ["G->C", "F->E", "D->G", "B->B"]),
]


def _stub_chord_events(track: str = "keys") -> list[ChordEvent]:
    """Return stub ChordEvent entries for a realistic I-vi-ii-V-I progression."""
    return [
        ChordEvent(bar=bar, beat=beat, chord=chord, duration=dur, track=track)
        for bar, beat, chord, dur in _STUB_CHORDS
    ]


def _stub_voice_leading_steps() -> list[VoiceLeadingStep]:
    """Return stub VoiceLeadingStep entries connecting the chord timeline."""
    return [
        VoiceLeadingStep(
            from_chord=fc,
            to_chord=tc,
            from_bar=fb,
            to_bar=tb,
            movements=mv,
        )
        for fc, tc, fb, tb, mv in _STUB_VOICE_LEADING
    ]


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

_BAR_BLOCK = "########"


def _render_text(result: ChordMapResult) -> str:
    """Render a human-readable chord-timeline table."""
    branch = result["branch"]
    commit = result["commit"]
    head_label = f"  (HEAD -> {branch})" if branch else ""
    lines: list[str] = [f"Chord map -- commit {commit}{head_label}", ""]

    if result["section"]:
        lines.append(f"Section: {result['section']}")
    if result["track"] != "all":
        lines.append(f"Track:   {result['track']}")
    if result["section"] or result["track"] != "all":
        lines.append("")

    chords = result["chords"]
    if not chords:
        lines.append("(no chord data found for this commit)")
        return "\n".join(lines)

    if result["voice_leading"]:
        vl_map: dict[int, VoiceLeadingStep] = {
            step["from_bar"]: step for step in result["voice_leading"]
        }
        prev_chord: Optional[str] = None
        prev_bar: int = -1
        for event in chords:
            bar = event["bar"]
            chord = event["chord"]
            bar_label = f"Bar {bar:>2}:"
            if prev_chord is not None and prev_bar >= 0:
                step = vl_map.get(prev_bar)
                movements = f"  ({', '.join(step['movements'])})" if step else ""
                lines.append(f"{bar_label} {prev_chord:<8} -> {chord}{movements}")
            else:
                lines.append(f"{bar_label} {chord}")
            prev_chord = chord
            prev_bar = bar
    else:
        current_bar: Optional[int] = None
        bar_chords: list[tuple[str, float]] = []

        def _flush_bar(bar_num: int, items: list[tuple[str, float]]) -> None:
            bar_label = f"Bar {bar_num:>2}:"
            chord_parts: list[str] = []
            for ch, dur in items:
                blocks = _BAR_BLOCK if dur >= 1.0 else _BAR_BLOCK[:4]
                chord_parts.append(f"{ch:<12}{blocks}  ")
            lines.append(f"{bar_label} {''.join(chord_parts).rstrip()}")

        for event in chords:
            if event["bar"] != current_bar:
                if current_bar is not None:
                    _flush_bar(current_bar, bar_chords)
                current_bar = event["bar"]
                bar_chords = []
            bar_chords.append((event["chord"], event["duration"]))
        if current_bar is not None:
            _flush_bar(current_bar, bar_chords)

    lines.append("")
    lines.append("(stub -- full MIDI chord detection pending)")
    return "\n".join(lines)


def _render_mermaid(result: ChordMapResult) -> str:
    """Render a Mermaid timeline diagram for the chord progression."""
    chords = result["chords"]
    lines: list[str] = [
        "timeline",
        f"    title Chord map -- {result['commit']}",
    ]
    if not chords:
        lines.append("    section (empty)")
        return "\n".join(lines)

    current_bar: Optional[int] = None
    for event in chords:
        bar = event["bar"]
        if bar != current_bar:
            lines.append(f"    section Bar {bar}")
            current_bar = bar
        lines.append(f"        {event['chord']}")
    return "\n".join(lines)


def _render_json(result: ChordMapResult) -> str:
    """Emit the full ChordMapResult as indented JSON."""
    return json.dumps(dict(result), indent=2)


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _chord_map_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit: Optional[str],
    section: Optional[str],
    track: Optional[str],
    bar_grid: bool,
    fmt: str,
    voice_leading: bool,
) -> ChordMapResult:
    """Core chord-map logic — fully injectable for tests.

    Reads branch/commit metadata from ``.muse/``, applies optional
    ``--section`` and ``--track`` filters to placeholder chord data, and
    returns a :class:`ChordMapResult` ready for rendering.

    Args:
        root:          Repository root (directory containing ``.muse/``).
        session:       Open async DB session (reserved for full implementation).
        commit:        Commit ref to analyse; defaults to HEAD.
        section:       Named section/region filter (stub: noted in output).
        track:         Track filter for chord voicings (stub: tag applied).
        bar_grid:      If True, align events to bar numbers (default on).
        fmt:           Output format: ``"text"``, ``"json"``, or ``"mermaid"``.
        voice_leading: If True, include voice-leading movements between chords.

    Returns:
        A :class:`ChordMapResult` with ``commit``, ``branch``, ``track``,
        ``section``, ``chords``, and ``voice_leading`` populated.
    """
    muse_dir = root / ".muse"
    head_path = muse_dir / "HEAD"
    head_ref = head_path.read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref

    ref_path = muse_dir / pathlib.Path(head_ref)
    head_sha = ref_path.read_text().strip() if ref_path.exists() else ""
    resolved_commit = commit or (head_sha[:8] if head_sha else "HEAD")

    resolved_track = track or "keys"

    chords = _stub_chord_events(track=resolved_track)
    vl_steps: list[VoiceLeadingStep] = (
        _stub_voice_leading_steps() if voice_leading else []
    )

    return ChordMapResult(
        commit=resolved_commit,
        branch=branch,
        track=resolved_track if track else "all",
        section=section or "",
        chords=chords,
        voice_leading=vl_steps,
    )


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def chord_map(
    ctx: typer.Context,
    commit: Annotated[
        Optional[str],
        typer.Argument(
            help="Commit ref to analyse. Defaults to HEAD.",
            show_default=False,
        ),
    ] = None,
    section: Annotated[
        Optional[str],
        typer.Option(
            "--section",
            help="Scope to a named section/region.",
            metavar="TEXT",
            show_default=False,
        ),
    ] = None,
    track: Annotated[
        Optional[str],
        typer.Option(
            "--track",
            help="Scope to a specific track (e.g. 'piano' for chord voicings).",
            metavar="TEXT",
            show_default=False,
        ),
    ] = None,
    bar_grid: Annotated[
        bool,
        typer.Option(
            "--bar-grid/--no-bar-grid",
            help="Align chord events to musical bar numbers (default: on).",
        ),
    ] = True,
    fmt: Annotated[
        str,
        typer.Option(
            "--format",
            help="Output format: text (default), json, or mermaid.",
            metavar="FORMAT",
        ),
    ] = "text",
    voice_leading: Annotated[
        bool,
        typer.Option(
            "--voice-leading",
            help="Show how individual notes move between consecutive chords.",
        ),
    ] = False,
) -> None:
    """Visualize the chord progression embedded in a commit.

    Shows a time-aligned chord timeline so AI agents can reason about
    harmonic structure at any point in the composition history.
    """
    if fmt not in _VALID_FORMATS:
        typer.echo(
            f"Invalid --format '{fmt}'. "
            f"Valid formats: {', '.join(sorted(_VALID_FORMATS))}"
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    async def _run() -> ChordMapResult:
        async with open_session() as session:
            return await _chord_map_async(
                root=root,
                session=session,
                commit=commit,
                section=section,
                track=track,
                bar_grid=bar_grid,
                fmt=fmt,
                voice_leading=voice_leading,
            )

    try:
        result = asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"muse chord-map failed: {exc}")
        logger.error("muse chord-map error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if fmt == "json":
        typer.echo(_render_json(result))
    elif fmt == "mermaid":
        typer.echo(_render_mermaid(result))
    else:
        typer.echo(_render_text(result))
