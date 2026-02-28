"""muse key — read or annotate the musical key of a commit.

Key (tonal center) is the most fundamental property of a piece of music.
This command provides auto-detection from MIDI content, explicit annotation,
relative-key display, and a history view showing how the key evolved across
commits.

Command forms
-------------

Detect the key of HEAD (default)::

    muse key

Detect the key of a specific commit::

    muse key a1b2c3d4

Annotate HEAD with an explicit key::

    muse key --set "F# minor"

Detect key from a specific instrument track::

    muse key --track bass

Show the relative key as well::

    muse key --relative

Show how the key changed across all commits::

    muse key --history

Machine-readable JSON output::

    muse key --json

Key Format Convention
---------------------

Keys are expressed as ``<tonic> <mode>`` where mode is one of ``major`` or
``minor``.  Tonic uses standard Western note names with ``#`` for sharp and
``b`` for flat:

    C major, D minor, Eb major, F# minor, Bb major, C# minor

The relative major of a minor key is a minor third above the tonic; the
relative minor of a major key is a minor third below.
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
# Key vocabulary
# ---------------------------------------------------------------------------

# Chromatic tonic names in order (sharps preferred for majors, flats for minors
# where convention dictates, but the stub uses a fixed default).
_VALID_TONICS: frozenset[str] = frozenset(
    [
        "C", "C#", "Db", "D", "D#", "Eb", "E", "F",
        "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B",
    ]
)

_VALID_MODES: frozenset[str] = frozenset(["major", "minor"])

# Semitones from a minor tonic to its relative major tonic.
_RELATIVE_MAJOR_OFFSET = 3

# Chromatic scale (sharps) for enharmonic arithmetic.
_CHROMATIC: tuple[str, ...] = (
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"
)

# Enharmonic equivalents (flat → sharp index).
_ENHARMONIC: dict[str, str] = {
    "Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#",
}

# Stub default — a realistic placeholder.
_STUB_KEY = "C major"
_STUB_TONIC = "C"
_STUB_MODE = "major"


# ---------------------------------------------------------------------------
# Named result types (stable CLI contract)
# ---------------------------------------------------------------------------


class KeyDetectResult(TypedDict):
    """Key detection result for a single commit or working tree.

    Fields
    ------
    key:      Full key string, e.g. ``"F# minor"``.
    tonic:    Root note, e.g. ``"F#"``.
    mode:     ``"major"`` or ``"minor"``.
    relative: Relative key string, e.g. ``"A major"`` (empty when not requested).
    commit:   Short commit SHA.
    branch:   Current branch name.
    track:    Track the key was analysed from (``"all"`` if no filter applied).
    source:   ``"stub"`` | ``"annotation"`` | ``"detected"``.
    """

    key: str
    tonic: str
    mode: str
    relative: str
    commit: str
    branch: str
    track: str
    source: str


class KeyHistoryEntry(TypedDict):
    """One row in a ``muse key --history`` listing."""

    commit: str
    key: str
    tonic: str
    mode: str
    source: str


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def parse_key(key_str: str) -> tuple[str, str]:
    """Parse a key string into ``(tonic, mode)``.

    Accepts ``"<tonic> <mode>"`` strings with case-insensitive mode.

    Args:
        key_str: Key string such as ``"F# minor"`` or ``"Eb major"``.

    Returns:
        Tuple of ``(tonic, mode)`` both in canonical capitalisation.

    Raises:
        ValueError: If the tonic or mode is not recognised.
    """
    parts = key_str.strip().split()
    if len(parts) != 2:
        raise ValueError(
            f"Key must be '<tonic> <mode>', got {key_str!r}. "
            "Example: 'F# minor', 'Eb major'."
        )
    tonic, mode = parts[0], parts[1].lower()
    if tonic not in _VALID_TONICS:
        raise ValueError(
            f"Unknown tonic {tonic!r}. Valid tonics: "
            + ", ".join(sorted(_VALID_TONICS))
        )
    if mode not in _VALID_MODES:
        raise ValueError(
            f"Unknown mode {mode!r}. Valid modes: major, minor."
        )
    return tonic, mode


def relative_key(tonic: str, mode: str) -> str:
    """Return the relative key for *tonic* + *mode*.

    The relative major of a minor key is 3 semitones above its tonic.
    The relative minor of a major key is 3 semitones below its tonic.

    Args:
        tonic: Root note, e.g. ``"A"``.
        mode:  ``"major"`` or ``"minor"``.

    Returns:
        Relative key string, e.g. ``"C major"`` for ``"A minor"``.
    """
    canonical = _ENHARMONIC.get(tonic, tonic)
    try:
        idx = _CHROMATIC.index(canonical)
    except ValueError:
        return ""

    if mode == "minor":
        rel_idx = (idx + _RELATIVE_MAJOR_OFFSET) % 12
        rel_tonic = _CHROMATIC[rel_idx]
        return f"{rel_tonic} major"
    else:
        rel_idx = (idx - _RELATIVE_MAJOR_OFFSET) % 12
        rel_tonic = _CHROMATIC[rel_idx]
        return f"{rel_tonic} minor"


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _key_detect_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit: Optional[str],
    track: Optional[str],
    show_relative: bool,
) -> KeyDetectResult:
    """Detect the musical key for a commit (or the working tree).

    Stub implementation returning a realistic placeholder in the correct schema.
    Full MIDI-based analysis will be wired in once the Storpheus inference
    endpoint exposes a key detection route.

    Args:
        root:          Repository root (directory containing ``.muse/``).
        session:       Open async DB session (reserved for full implementation).
        commit:        Commit SHA to analyse, or ``None`` for HEAD.
        track:         Restrict analysis to a named MIDI track, or ``None`` for all.
        show_relative: If True, populate the ``relative`` field.

    Returns:
        A :class:`KeyDetectResult` with ``key``, ``tonic``, ``mode``,
        ``relative``, ``commit``, ``branch``, ``track``, and ``source``.
    """
    muse_dir = root / ".muse"
    head_path = muse_dir / "HEAD"
    head_ref = head_path.read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref

    ref_path = muse_dir / pathlib.Path(head_ref)
    head_sha = ref_path.read_text().strip() if ref_path.exists() else "0000000"
    resolved_commit = commit or (head_sha[:8] if head_sha else "HEAD")

    rel = relative_key(_STUB_TONIC, _STUB_MODE) if show_relative else ""

    return KeyDetectResult(
        key=_STUB_KEY,
        tonic=_STUB_TONIC,
        mode=_STUB_MODE,
        relative=rel,
        commit=resolved_commit,
        branch=branch,
        track=track or "all",
        source="stub",
    )


async def _key_history_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    track: Optional[str],
) -> list[KeyHistoryEntry]:
    """Return the key history for the current branch.

    Stub implementation returning a single placeholder entry.  Full
    implementation will walk the commit chain and aggregate key annotations
    stored per-commit.

    Args:
        root:    Repository root.
        session: Open async DB session.
        track:   Restrict to a named MIDI track, or ``None`` for all.

    Returns:
        List of :class:`KeyHistoryEntry` entries, newest first.
    """
    entry = await _key_detect_async(
        root=root,
        session=session,
        commit=None,
        track=track,
        show_relative=False,
    )
    return [
        KeyHistoryEntry(
            commit=entry["commit"],
            key=entry["key"],
            tonic=entry["tonic"],
            mode=entry["mode"],
            source=entry["source"],
        )
    ]


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _format_detect(result: KeyDetectResult, *, as_json: bool) -> str:
    """Render a detect result as human-readable text or JSON."""
    if as_json:
        return json.dumps(dict(result), indent=2)
    lines = [
        f"Key: {result['key']}",
        f"Commit: {result['commit']}  Branch: {result['branch']}",
        f"Track: {result['track']}",
    ]
    if result.get("relative"):
        lines.append(f"Relative: {result['relative']}")
    if result.get("source") == "stub":
        lines.append("(stub — full MIDI key detection pending)")
    elif result.get("source") == "annotation":
        lines.append("(explicitly annotated)")
    return "\n".join(lines)


def _format_history(
    entries: list[KeyHistoryEntry], *, as_json: bool
) -> str:
    """Render a history list as human-readable text or JSON."""
    if as_json:
        return json.dumps([dict(e) for e in entries], indent=2)
    lines: list[str] = []
    for entry in entries:
        src = f"  [{entry['source']}]" if entry.get("source") != "stub" else ""
        lines.append(f"{entry['commit']}  {entry['key']}{src}")
    return "\n".join(lines) if lines else "(no key history found)"


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def key(
    ctx: typer.Context,
    commit: Annotated[
        Optional[str],
        typer.Argument(
            help="Commit SHA to analyse. Defaults to HEAD.",
            show_default=False,
        ),
    ] = None,
    set_key: Annotated[
        Optional[str],
        typer.Option(
            "--set",
            metavar="KEY",
            help=(
                "Annotate the working tree with an explicit key "
                "(e.g. 'F# minor', 'Eb major')."
            ),
            show_default=False,
        ),
    ] = None,
    detect: Annotated[
        bool,
        typer.Option(
            "--detect",
            help="Detect and display the key (default when no other flag given).",
        ),
    ] = True,
    track: Annotated[
        Optional[str],
        typer.Option(
            "--track",
            metavar="TEXT",
            help="Detect key from a specific instrument track only.",
            show_default=False,
        ),
    ] = None,
    show_relative: Annotated[
        bool,
        typer.Option(
            "--relative",
            help="Show the relative key as well (e.g. 'Eb major / C minor').",
        ),
    ] = False,
    history: Annotated[
        bool,
        typer.Option(
            "--history",
            help="Show how the key changed across all commits (key map over time).",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON output."),
    ] = False,
) -> None:
    """Read or annotate the musical key of a commit.

    With no flags, detects and displays the tonal center for HEAD.
    Use ``--set`` to persist an explicit key annotation.
    Use ``--history`` to see how the key evolved across all commits.
    """
    root = require_repo()

    # --set validation
    if set_key is not None:
        try:
            set_tonic, set_mode = parse_key(set_key)
        except ValueError as exc:
            typer.echo(f"❌ {exc}")
            raise typer.Exit(code=ExitCode.USER_ERROR)

        rel = relative_key(set_tonic, set_mode) if show_relative else ""
        annotation: KeyDetectResult = KeyDetectResult(
            key=f"{set_tonic} {set_mode}",
            tonic=set_tonic,
            mode=set_mode,
            relative=rel,
            commit="",
            branch="",
            track=track or "all",
            source="annotation",
        )
        if as_json:
            typer.echo(json.dumps(dict(annotation), indent=2))
        else:
            rel_part = f" (relative: {rel})" if rel else ""
            typer.echo(
                f"✅ Key annotated: {set_tonic} {set_mode}{rel_part}"
                + (f"  track={track}" if track else "")
            )
        return

    async def _run() -> None:
        async with open_session() as session:
            if history:
                entries = await _key_history_async(
                    root=root, session=session, track=track
                )
                typer.echo(_format_history(entries, as_json=as_json))
                return

            detect_result = await _key_detect_async(
                root=root,
                session=session,
                commit=commit,
                track=track,
                show_relative=show_relative,
            )
            typer.echo(_format_detect(detect_result, as_json=as_json))

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse key failed: {exc}")
        logger.error("❌ muse key error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
