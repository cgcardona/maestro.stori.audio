"""muse harmony — analyze and query harmonic content across commits.

Examines the harmonic profile (key, mode, chord progression, harmonic
rhythm, and tension) of a given commit (default: HEAD) or a range of
commits.  Harmonic analysis is one of the most musically significant
dimensions exposed by Muse VCS — information that Git has no concept of.

An AI agent calling ``muse harmony --json`` receives a structured snapshot
of the harmonic landscape it can use to make musically coherent generation
decisions: stay in the same key, continue the same chord progression, or
intentionally create harmonic contrast.

Command forms
-------------

Analyze HEAD (default)::

    muse harmony

Analyze a specific commit::

    muse harmony a1b2c3d4

Analyze a commit range::

    muse harmony HEAD~10..HEAD

Compare two commits::

    muse harmony --compare HEAD~5

Extract only the chord progression::

    muse harmony --progression

Show key center::

    muse harmony --key

Show mode::

    muse harmony --mode

Show harmonic tension profile::

    muse harmony --tension

Restrict to a single instrument track::

    muse harmony --track keys

Machine-readable JSON output::

    muse harmony --json

Stub note
---------
Full chord detection requires MIDI note extraction from committed snapshot
objects.  This implementation provides a realistic placeholder in the
correct schema.  The result type and CLI contract are stable.
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
# Constants — mode vocabulary
# ---------------------------------------------------------------------------

KNOWN_MODES: tuple[str, ...] = (
    "major",
    "minor",
    "dorian",
    "phrygian",
    "lydian",
    "mixolydian",
    "aeolian",
    "locrian",
)

KNOWN_MODES_SET: frozenset[str] = frozenset(KNOWN_MODES)

# ---------------------------------------------------------------------------
# Named result types (stable CLI contract)
# ---------------------------------------------------------------------------


class HarmonyResult(TypedDict):
    """Harmonic analysis result for a single commit.

    This is the primary result type for ``muse harmony``.  Every field is
    populated by stub logic today and will be backed by MIDI analysis once
    the Storpheus inference endpoint exposes a chord detection route.

    Fields
    ------
    commit_id : str
        Short or full commit SHA that was analyzed.
    branch : str
        Name of the current branch.
    key : str | None
        Detected key center (e.g. ``"Eb"``), or ``None`` for drum-only
        snapshots with no pitched content.
    mode : str | None
        Detected mode (e.g. ``"major"``, ``"dorian"``), or ``None``.
    confidence : float
        Key/mode detection confidence in [0.0, 1.0].
    chord_progression : list[str]
        Ordered list of chord symbol strings (e.g. ``["Ebmaj7", "Fm7"]``).
    harmonic_rhythm_avg : float
        Average number of chord changes per bar.
    tension_profile : list[float]
        Per-section tension scores in [0.0, 1.0], where 0.0 = fully
        consonant and 1.0 = maximally dissonant.
    track : str
        Instrument track scope (``"all"`` unless ``--track`` is specified).
    source : str
        ``"stub"`` until backed by real MIDI analysis.
    """

    commit_id: str
    branch: str
    key: Optional[str]
    mode: Optional[str]
    confidence: float
    chord_progression: list[str]
    harmonic_rhythm_avg: float
    tension_profile: list[float]
    track: str
    source: str


class HarmonyCompareResult(TypedDict):
    """Comparison of harmonic content between two commits.

    Fields
    ------
    head : HarmonyResult
        Harmonic analysis for the HEAD (or specified) commit.
    compare : HarmonyResult
        Harmonic analysis for the reference commit.
    key_changed : bool
        ``True`` if the key center differs between the two commits.
    mode_changed : bool
        ``True`` if the mode differs between the two commits.
    chord_progression_delta : list[str]
        Chords present in HEAD but absent in compare (new chords).
    """

    head: HarmonyResult
    compare: HarmonyResult
    key_changed: bool
    mode_changed: bool
    chord_progression_delta: list[str]


# ---------------------------------------------------------------------------
# Stub data — realistic placeholder until MIDI note data is queryable
# ---------------------------------------------------------------------------

_STUB_KEY = "Eb"
_STUB_MODE = "major"
_STUB_CONFIDENCE = 0.92
_STUB_CHORD_PROGRESSION = ["Ebmaj7", "Fm7", "Bb7sus4", "Bb7", "Ebmaj7", "Abmaj7", "Gm7", "Cm7"]
_STUB_HARMONIC_RHYTHM_AVG = 2.1
_STUB_TENSION_PROFILE = [0.2, 0.4, 0.8, 0.3]


def _stub_harmony(commit_id: str, branch: str, track: str = "all") -> HarmonyResult:
    """Return a realistic placeholder HarmonyResult.

    Produces a II-V-I flavored progression in Eb major — one of the most
    common key centers in jazz and soul productions.  Confidence and tension
    values reflect a textbook tension-release arc.
    """
    return HarmonyResult(
        commit_id=commit_id,
        branch=branch,
        key=_STUB_KEY,
        mode=_STUB_MODE,
        confidence=_STUB_CONFIDENCE,
        chord_progression=list(_STUB_CHORD_PROGRESSION),
        harmonic_rhythm_avg=_STUB_HARMONIC_RHYTHM_AVG,
        tension_profile=list(_STUB_TENSION_PROFILE),
        track=track,
        source="stub",
    )


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _harmony_analyze_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit: Optional[str],
    track: Optional[str],
    section: Optional[str],
    compare: Optional[str],
    commit_range: Optional[str],
    show_progression: bool,
    show_key: bool,
    show_mode: bool,
    show_tension: bool,
    as_json: bool,
) -> HarmonyResult:
    """Core harmonic analysis logic — fully injectable for tests.

    Resolves the target commit from the ``.muse/`` layout, produces a
    ``HarmonyResult`` (stub today, full MIDI analysis in future), and
    renders it to stdout according to the active flags.

    Returns the ``HarmonyResult`` so callers (tests) can assert on values
    without parsing stdout.

    Args:
        root:             Repository root (directory containing ``.muse/``).
        session:          Open async DB session (reserved for full implementation).
        commit:           Commit ref to analyse; defaults to HEAD.
        track:            Restrict to a named MIDI track, or ``None`` for all.
        section:          Restrict to a named region (stub: noted in output).
        compare:          Second commit ref for side-by-side comparison.
        commit_range:     ``from..to`` range string (stub: noted in output).
        show_progression: If ``True``, show only the chord progression sequence.
        show_key:         If ``True``, show only the detected key center.
        show_mode:        If ``True``, show only the detected mode.
        show_tension:     If ``True``, show only the tension profile.
        as_json:          Emit JSON instead of human-readable text.
    """
    muse_dir = root / ".muse"

    # -- Resolve branch / commit ref --
    head_ref = (muse_dir / "HEAD").read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref
    ref_path = muse_dir / pathlib.Path(head_ref)

    head_sha = ref_path.read_text().strip() if ref_path.exists() else ""

    if not head_sha and not commit:
        typer.echo(f"No commits yet on branch {branch} — nothing to analyse.")
        raise typer.Exit(code=ExitCode.SUCCESS)

    resolved_commit = commit or (head_sha[:8] if head_sha else "HEAD")
    effective_track = track or "all"

    # -- Stub: produce placeholder result --
    result = _stub_harmony(
        commit_id=resolved_commit,
        branch=branch,
        track=effective_track,
    )

    # -- Stub boundary notes for unimplemented flags --
    if commit_range:
        typer.echo(
            f"⚠️  --range {commit_range!r}: range analysis not yet implemented. "
            f"Showing HEAD ({resolved_commit}) only."
        )
    if section:
        typer.echo(f"⚠️  --section {section!r}: region filtering not yet implemented.")

    # -- Render --
    if compare is not None:
        compare_result = _stub_harmony(
            commit_id=compare,
            branch=branch,
            track=effective_track,
        )
        cmp = HarmonyCompareResult(
            head=result,
            compare=compare_result,
            key_changed=result["key"] != compare_result["key"],
            mode_changed=result["mode"] != compare_result["mode"],
            chord_progression_delta=[
                c
                for c in result["chord_progression"]
                if c not in compare_result["chord_progression"]
            ],
        )
        if as_json:
            _render_compare_json(cmp)
        else:
            _render_compare_human(cmp)
        return result

    # Single-commit render with optional field scoping
    if as_json:
        _render_result_json(result, show_progression, show_key, show_mode, show_tension)
    else:
        _render_result_human(result, show_progression, show_key, show_mode, show_tension)

    return result


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _tension_label(profile: list[float]) -> str:
    """Classify a tension profile into a human-readable arc description.

    Uses the shape of the profile (monotone rise/fall, arch, valley) to
    produce vocabulary familiar to producers and music directors.
    """
    if not profile:
        return "unknown"
    if len(profile) == 1:
        v = profile[0]
        if v < 0.3:
            return "Low"
        if v < 0.6:
            return "Medium"
        return "High"

    rising = all(profile[i] <= profile[i + 1] for i in range(len(profile) - 1))
    falling = all(profile[i] >= profile[i + 1] for i in range(len(profile) - 1))
    peak_idx = profile.index(max(profile))
    valley_idx = profile.index(min(profile))

    if rising:
        return "Rising (tension build)"
    if falling:
        return "Falling (tension release)"
    if 0 < peak_idx < len(profile) - 1:
        return "Low → Medium → High → Resolution (textbook tension-release arc)"
    if 0 < valley_idx < len(profile) - 1:
        return "High → Resolution → High (bracketed release)"
    return "Variable"


def _render_result_human(
    result: HarmonyResult,
    show_progression: bool,
    show_key: bool,
    show_mode: bool,
    show_tension: bool,
) -> None:
    """Render a HarmonyResult as human-readable text."""
    full = not any([show_progression, show_key, show_mode, show_tension])

    if full:
        typer.echo(f"Commit {result['commit_id']} — Harmonic Analysis")
        if result["source"] == "stub":
            typer.echo("(stub — full MIDI analysis pending)")
        typer.echo("")

    if full or show_key:
        key_display = result["key"] or "—  (no pitched content)"
        typer.echo(
            f"Key: {key_display}"
            + (f" (confidence: {result['confidence']:.2f})" if result["key"] else "")
        )

    if full or show_mode:
        mode_display = result["mode"] or "—"
        typer.echo(f"Mode: {mode_display}")

    if full or show_progression:
        if result["chord_progression"]:
            progression_str = " | ".join(result["chord_progression"])
        else:
            progression_str = "(no pitched content — drums only)"
        typer.echo(f"Chord progression: {progression_str}")

    if full:
        typer.echo(f"Harmonic rhythm: {result['harmonic_rhythm_avg']:.1f} chords/bar avg")

    if full or show_tension:
        label = _tension_label(result["tension_profile"])
        profile_str = " → ".join(f"{v:.1f}" for v in result["tension_profile"])
        typer.echo(f"Tension profile: {label}  [{profile_str}]")


def _render_result_json(
    result: HarmonyResult,
    show_progression: bool,
    show_key: bool,
    show_mode: bool,
    show_tension: bool,
) -> None:
    """Render a HarmonyResult as JSON, optionally scoped to requested fields."""
    full = not any([show_progression, show_key, show_mode, show_tension])

    if full:
        payload: dict[str, object] = dict(result)
    else:
        payload = {"commit_id": result["commit_id"], "branch": result["branch"]}
        if show_key:
            payload["key"] = result["key"]
            payload["confidence"] = result["confidence"]
        if show_mode:
            payload["mode"] = result["mode"]
        if show_progression:
            payload["chord_progression"] = result["chord_progression"]
        if show_tension:
            payload["tension_profile"] = result["tension_profile"]

    typer.echo(json.dumps(payload, indent=2))


def _render_compare_human(cmp: HarmonyCompareResult) -> None:
    """Render a HarmonyCompareResult as human-readable text."""
    head = cmp["head"]
    ref = cmp["compare"]

    typer.echo(f"Harmonic Comparison — HEAD ({head['commit_id']}) vs {ref['commit_id']}")
    typer.echo("")
    typer.echo(f"  Key   HEAD: {head['key'] or '—'}   Compare: {ref['key'] or '—'}")
    typer.echo(f"  Mode  HEAD: {head['mode'] or '—'}  Compare: {ref['mode'] or '—'}")
    typer.echo(f"  Key changed:  {'yes' if cmp['key_changed'] else 'no'}")
    typer.echo(f"  Mode changed: {'yes' if cmp['mode_changed'] else 'no'}")
    if cmp["chord_progression_delta"]:
        typer.echo(f"  New chords in HEAD: {' '.join(cmp['chord_progression_delta'])}")
    else:
        typer.echo("  Chord progression: unchanged")


def _render_compare_json(cmp: HarmonyCompareResult) -> None:
    """Render a HarmonyCompareResult as JSON."""
    typer.echo(json.dumps(dict(cmp), indent=2))


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def harmony(
    ctx: typer.Context,
    commit: Annotated[
        Optional[str],
        typer.Argument(
            help="Commit SHA to analyze. Defaults to HEAD.",
            show_default=False,
        ),
    ] = None,
    track: Annotated[
        Optional[str],
        typer.Option(
            "--track",
            help="Restrict analysis to a named MIDI track (e.g. 'keys', 'bass').",
            show_default=False,
        ),
    ] = None,
    section: Annotated[
        Optional[str],
        typer.Option(
            "--section",
            help="Restrict analysis to a named musical section or region.",
            show_default=False,
        ),
    ] = None,
    compare: Annotated[
        Optional[str],
        typer.Option(
            "--compare",
            metavar="COMMIT",
            help="Compare harmonic content of HEAD against another commit.",
            show_default=False,
        ),
    ] = None,
    commit_range: Annotated[
        Optional[str],
        typer.Option(
            "--range",
            metavar="FROM..TO",
            help="Analyze harmonic content across a commit range (e.g. HEAD~10..HEAD).",
            show_default=False,
        ),
    ] = None,
    show_progression: Annotated[
        bool,
        typer.Option(
            "--progression",
            help="Show only the chord progression sequence.",
        ),
    ] = False,
    show_key: Annotated[
        bool,
        typer.Option(
            "--key",
            help="Show only the detected key center.",
        ),
    ] = False,
    show_mode: Annotated[
        bool,
        typer.Option(
            "--mode",
            help="Show only the detected mode (major, minor, dorian, etc.).",
        ),
    ] = False,
    show_tension: Annotated[
        bool,
        typer.Option(
            "--tension",
            help="Show only the harmonic tension profile.",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit machine-readable JSON output.",
        ),
    ] = False,
) -> None:
    """Analyze harmonic content (key, mode, chords, tension) of a commit.

    Without flags, prints a full harmonic summary for the target commit.
    Use ``--key``, ``--mode``, ``--progression``, or ``--tension`` to
    scope the output to a single dimension.  Use ``--json`` for structured
    output suitable for AI agent consumption.
    """
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _harmony_analyze_async(
                root=root,
                session=session,
                commit=commit,
                track=track,
                section=section,
                compare=compare,
                commit_range=commit_range,
                show_progression=show_progression,
                show_key=show_key,
                show_mode=show_mode,
                show_tension=show_tension,
                as_json=as_json,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse harmony failed: {exc}")
        logger.error("❌ muse harmony error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
