"""muse diff — music-dimension diff between two commits.

Compares two commits across five orthogonal musical dimensions:

- **harmonic**   — key, mode, chord progression, tension profile
- **rhythmic**   — tempo, meter, swing factor, groove tightness
- **melodic**    — motifs, melodic contour, pitch range
- **structural** — arrangement form, sections, instrumentation
- **dynamic**    — overall volume arc, per-track loudness envelope

Each dimension produces a focused, human-readable diff (or structured JSON).
``--all`` runs every dimension simultaneously and combines the results into a
single musical change report.

Flags
-----
COMMIT_A TEXT  Earlier commit ref (default: HEAD~1).
COMMIT_B TEXT  Later commit ref (default: HEAD).
--harmonic     Compare harmonic content between commits.
--rhythmic     Compare rhythmic content between commits.
--melodic      Compare melodic content between commits.
--structural   Compare arrangement/form between commits.
--dynamic      Compare dynamic profiles between commits.
--all          Run all dimension analyses and produce a combined report.
--json         Emit structured JSON for each dimension instead of text.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from typing import Optional

import typer
from typing_extensions import TypedDict

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode

logger = logging.getLogger(__name__)

app = typer.Typer()


class HarmonicDiffResult(TypedDict):
    """Harmonic-dimension diff between two commits."""
    commit_a: str
    commit_b: str
    key_a: str
    key_b: str
    mode_a: str
    mode_b: str
    chord_prog_a: str
    chord_prog_b: str
    tension_a: float
    tension_b: float
    tension_label_a: str
    tension_label_b: str
    summary: str
    changed: bool


class RhythmicDiffResult(TypedDict):
    """Rhythmic-dimension diff between two commits."""
    commit_a: str
    commit_b: str
    tempo_a: float
    tempo_b: float
    meter_a: str
    meter_b: str
    swing_a: float
    swing_b: float
    swing_label_a: str
    swing_label_b: str
    groove_drift_ms_a: float
    groove_drift_ms_b: float
    summary: str
    changed: bool


class MelodicDiffResult(TypedDict):
    """Melodic-dimension diff between two commits."""
    commit_a: str
    commit_b: str
    motifs_introduced: list[str]
    motifs_removed: list[str]
    contour_a: str
    contour_b: str
    range_low_a: int
    range_low_b: int
    range_high_a: int
    range_high_b: int
    summary: str
    changed: bool


class StructuralDiffResult(TypedDict):
    """Structural/arrangement-dimension diff between two commits."""
    commit_a: str
    commit_b: str
    sections_added: list[str]
    sections_removed: list[str]
    instruments_added: list[str]
    instruments_removed: list[str]
    form_a: str
    form_b: str
    summary: str
    changed: bool


class DynamicDiffResult(TypedDict):
    """Dynamic-profile-dimension diff between two commits."""
    commit_a: str
    commit_b: str
    avg_velocity_a: int
    avg_velocity_b: int
    arc_a: str
    arc_b: str
    tracks_louder: list[str]
    tracks_softer: list[str]
    tracks_silent: list[str]
    summary: str
    changed: bool


class MusicDiffReport(TypedDict):
    """Combined multi-dimension musical diff report (produced by --all)."""
    commit_a: str
    commit_b: str
    harmonic: Optional[HarmonicDiffResult]
    rhythmic: Optional[RhythmicDiffResult]
    melodic: Optional[MelodicDiffResult]
    structural: Optional[StructuralDiffResult]
    dynamic: Optional[DynamicDiffResult]
    changed_dimensions: list[str]
    unchanged_dimensions: list[str]
    summary: str


_TENSION_LOW = 0.33
_TENSION_MED = 0.66


def _tension_label(value: float) -> str:
    """Map a normalized tension value (0-1) to a human-readable label."""
    if value < _TENSION_LOW:
        return "Low"
    if value < _TENSION_MED:
        return "Medium"
    if value < 0.80:
        return "Medium-High"
    return "High"


def _stub_harmonic(commit_a: str, commit_b: str) -> HarmonicDiffResult:
    """Return a stub harmonic diff between two commit refs."""
    return HarmonicDiffResult(
        commit_a=commit_a,
        commit_b=commit_b,
        key_a="Eb major",
        key_b="F minor",
        mode_a="Major",
        mode_b="Minor",
        chord_prog_a="I-IV-V-I",
        chord_prog_b="i-VI-III-VII",
        tension_a=0.2,
        tension_b=0.65,
        tension_label_a=_tension_label(0.2),
        tension_label_b=_tension_label(0.65),
        summary=(
            "Major harmonic restructuring — key modulation down a minor 3rd, "
            "shift to Andalusian cadence"
        ),
        changed=True,
    )


def _stub_rhythmic(commit_a: str, commit_b: str) -> RhythmicDiffResult:
    """Return a stub rhythmic diff."""
    return RhythmicDiffResult(
        commit_a=commit_a,
        commit_b=commit_b,
        tempo_a=120.0,
        tempo_b=128.0,
        meter_a="4/4",
        meter_b="4/4",
        swing_a=0.50,
        swing_b=0.57,
        swing_label_a="Straight",
        swing_label_b="Light swing",
        groove_drift_ms_a=12.0,
        groove_drift_ms_b=6.0,
        summary="Slightly faster, more swung, tighter quantization",
        changed=True,
    )


def _stub_melodic(commit_a: str, commit_b: str) -> MelodicDiffResult:
    """Return a stub melodic diff."""
    return MelodicDiffResult(
        commit_a=commit_a,
        commit_b=commit_b,
        motifs_introduced=["chromatic-descent-4"],
        motifs_removed=[],
        contour_a="ascending-arch",
        contour_b="descending-step",
        range_low_a=48,
        range_low_b=43,
        range_high_a=84,
        range_high_b=79,
        summary=(
            "New chromatic descent motif introduced; contour shifted from "
            "ascending arch to descending step; overall range dropped by a 4th"
        ),
        changed=True,
    )


def _stub_structural(commit_a: str, commit_b: str) -> StructuralDiffResult:
    """Return a stub structural diff."""
    return StructuralDiffResult(
        commit_a=commit_a,
        commit_b=commit_b,
        sections_added=["bridge"],
        sections_removed=[],
        instruments_added=["acoustic_guitar"],
        instruments_removed=["strings"],
        form_a="Intro-Verse-Chorus-Verse-Chorus-Outro",
        form_b="Intro-Verse-Chorus-Bridge-Chorus-Outro",
        summary=(
            "Bridge added between second chorus and outro; "
            "strings removed, acoustic guitar added"
        ),
        changed=True,
    )


def _stub_dynamic(commit_a: str, commit_b: str) -> DynamicDiffResult:
    """Return a stub dynamic diff."""
    return DynamicDiffResult(
        commit_a=commit_a,
        commit_b=commit_b,
        avg_velocity_a=72,
        avg_velocity_b=84,
        arc_a="flat",
        arc_b="crescendo",
        tracks_louder=["drums", "bass"],
        tracks_softer=[],
        tracks_silent=["lead-synth"],
        summary=(
            "Overall louder (+12 avg velocity), arc shifted to crescendo; "
            "lead-synth track went silent"
        ),
        changed=True,
    )


def _resolve_refs(
    root: pathlib.Path,
    commit_a: Optional[str],
    commit_b: Optional[str],
) -> tuple[str, str]:
    """Resolve commit_a and commit_b against the local .muse/ HEAD chain."""
    muse_dir = root / ".muse"
    head_path = muse_dir / "HEAD"
    head_ref = head_path.read_text().strip() if head_path.exists() else "refs/heads/main"
    ref_path = muse_dir / pathlib.Path(head_ref)
    raw_sha = ref_path.read_text().strip() if ref_path.exists() else ""
    head_sha = raw_sha[:8] if raw_sha else "HEAD"

    resolved_b = commit_b or head_sha
    resolved_a = commit_a or f"{resolved_b}~1"
    return resolved_a, resolved_b


async def _harmonic_diff_async(
    *,
    root: pathlib.Path,
    commit_a: str,
    commit_b: str,
) -> HarmonicDiffResult:
    """Compute the harmonic diff between two commits (stub)."""
    _ = root
    return _stub_harmonic(commit_a, commit_b)


async def _rhythmic_diff_async(
    *,
    root: pathlib.Path,
    commit_a: str,
    commit_b: str,
) -> RhythmicDiffResult:
    """Compute the rhythmic diff between two commits (stub)."""
    _ = root
    return _stub_rhythmic(commit_a, commit_b)


async def _melodic_diff_async(
    *,
    root: pathlib.Path,
    commit_a: str,
    commit_b: str,
) -> MelodicDiffResult:
    """Compute the melodic diff between two commits (stub)."""
    _ = root
    return _stub_melodic(commit_a, commit_b)


async def _structural_diff_async(
    *,
    root: pathlib.Path,
    commit_a: str,
    commit_b: str,
) -> StructuralDiffResult:
    """Compute the structural diff between two commits (stub)."""
    _ = root
    return _stub_structural(commit_a, commit_b)


async def _dynamic_diff_async(
    *,
    root: pathlib.Path,
    commit_a: str,
    commit_b: str,
) -> DynamicDiffResult:
    """Compute the dynamic diff between two commits (stub)."""
    _ = root
    return _stub_dynamic(commit_a, commit_b)


async def _diff_all_async(
    *,
    root: pathlib.Path,
    commit_a: str,
    commit_b: str,
) -> MusicDiffReport:
    """Run all five dimension diffs and combine into a single report."""
    harmonic = await _harmonic_diff_async(root=root, commit_a=commit_a, commit_b=commit_b)
    rhythmic = await _rhythmic_diff_async(root=root, commit_a=commit_a, commit_b=commit_b)
    melodic = await _melodic_diff_async(root=root, commit_a=commit_a, commit_b=commit_b)
    structural = await _structural_diff_async(root=root, commit_a=commit_a, commit_b=commit_b)
    dynamic = await _dynamic_diff_async(root=root, commit_a=commit_a, commit_b=commit_b)

    changed: list[str] = []
    unchanged: list[str] = []
    for dim_name, result in [
        ("harmonic", harmonic),
        ("rhythmic", rhythmic),
        ("melodic", melodic),
        ("structural", structural),
        ("dynamic", dynamic),
    ]:
        (changed if result["changed"] else unchanged).append(dim_name)

    combined_summary = "; ".join([
        f"harmonic: {harmonic['summary']}",
        f"rhythmic: {rhythmic['summary']}",
        f"melodic: {melodic['summary']}",
        f"structural: {structural['summary']}",
        f"dynamic: {dynamic['summary']}",
    ])

    return MusicDiffReport(
        commit_a=commit_a,
        commit_b=commit_b,
        harmonic=harmonic,
        rhythmic=rhythmic,
        melodic=melodic,
        structural=structural,
        dynamic=dynamic,
        changed_dimensions=changed,
        unchanged_dimensions=unchanged,
        summary=combined_summary,
    )


def _render_harmonic(result: HarmonicDiffResult) -> str:
    """Format a harmonic diff as a human-readable block."""
    lines = [
        f"Harmonic diff: {result['commit_a']} -> {result['commit_b']}",
        "",
        f"Key:           {result['key_a']} -> {result['key_b']}",
        f"Mode:          {result['mode_a']} -> {result['mode_b']}",
        f"Chord prog:    {result['chord_prog_a']} -> {result['chord_prog_b']}",
        (
            f"Tension:       {result['tension_label_a']} ({result['tension_a']}) "
            f"-> {result['tension_label_b']} ({result['tension_b']})"
        ),
        f"Summary:       {result['summary']}",
    ]
    if not result["changed"]:
        lines.append("Unchanged")
    return "\n".join(lines)


def _render_rhythmic(result: RhythmicDiffResult) -> str:
    """Format a rhythmic diff as a human-readable block."""
    tempo_sign = "+" if result["tempo_b"] >= result["tempo_a"] else ""
    tempo_delta = result["tempo_b"] - result["tempo_a"]
    lines = [
        f"Rhythmic diff: {result['commit_a']} -> {result['commit_b']}",
        "",
        f"Tempo:         {result['tempo_a']} BPM -> {result['tempo_b']} BPM ({tempo_sign}{tempo_delta:.1f} BPM)",
        f"Meter:         {result['meter_a']} -> {result['meter_b']}",
        f"Swing:         {result['swing_label_a']} ({result['swing_a']}) -> {result['swing_label_b']} ({result['swing_b']})",
        f"Groove drift:  {result['groove_drift_ms_a']}ms -> {result['groove_drift_ms_b']}ms",
        f"Summary:       {result['summary']}",
    ]
    if not result["changed"]:
        lines.append("Unchanged")
    return "\n".join(lines)


def _render_melodic(result: MelodicDiffResult) -> str:
    """Format a melodic diff as a human-readable block."""
    introduced = ", ".join(result["motifs_introduced"]) or "none"
    removed = ", ".join(result["motifs_removed"]) or "none"
    lines = [
        f"Melodic diff: {result['commit_a']} -> {result['commit_b']}",
        "",
        f"Motifs introduced: {introduced}",
        f"Motifs removed:    {removed}",
        f"Contour:           {result['contour_a']} -> {result['contour_b']}",
        f"Pitch range:       {result['range_low_a']}-{result['range_high_a']} MIDI -> {result['range_low_b']}-{result['range_high_b']} MIDI",
        f"Summary:           {result['summary']}",
    ]
    if not result["changed"]:
        lines.append("Unchanged")
    return "\n".join(lines)


def _render_structural(result: StructuralDiffResult) -> str:
    """Format a structural diff as a human-readable block."""
    s_added = ", ".join(result["sections_added"]) or "none"
    s_removed = ", ".join(result["sections_removed"]) or "none"
    i_added = ", ".join(result["instruments_added"]) or "none"
    i_removed = ", ".join(result["instruments_removed"]) or "none"
    lines = [
        f"Structural diff: {result['commit_a']} -> {result['commit_b']}",
        "",
        f"Sections added:      {s_added}",
        f"Sections removed:    {s_removed}",
        f"Instruments added:   {i_added}",
        f"Instruments removed: {i_removed}",
        f"Form:                {result['form_a']} -> {result['form_b']}",
        f"Summary:             {result['summary']}",
    ]
    if not result["changed"]:
        lines.append("Unchanged")
    return "\n".join(lines)


def _render_dynamic(result: DynamicDiffResult) -> str:
    """Format a dynamic diff as a human-readable block."""
    louder = ", ".join(result["tracks_louder"]) or "none"
    softer = ", ".join(result["tracks_softer"]) or "none"
    silent = ", ".join(result["tracks_silent"]) or "none"
    vel_sign = "+" if result["avg_velocity_b"] >= result["avg_velocity_a"] else ""
    vel_delta = result["avg_velocity_b"] - result["avg_velocity_a"]
    lines = [
        f"Dynamic diff: {result['commit_a']} -> {result['commit_b']}",
        "",
        f"Avg velocity:  {result['avg_velocity_a']} -> {result['avg_velocity_b']} ({vel_sign}{vel_delta})",
        f"Arc:           {result['arc_a']} -> {result['arc_b']}",
        f"Tracks louder: {louder}",
        f"Tracks softer: {softer}",
        f"Tracks silent: {silent}",
        f"Summary:       {result['summary']}",
    ]
    if not result["changed"]:
        lines.append("Unchanged")
    return "\n".join(lines)


def _render_report(report: MusicDiffReport) -> str:
    """Format a full MusicDiffReport as a combined multi-dimension block."""
    sections: list[str] = [
        f"Music diff: {report['commit_a']} -> {report['commit_b']}",
        f"Changed:   {', '.join(report['changed_dimensions']) or 'none'}",
        f"Unchanged: {', '.join(report['unchanged_dimensions']) or 'none'}",
        "",
    ]
    if report["harmonic"] is not None:
        sections.append("-- Harmonic --")
        sections.append(_render_harmonic(report["harmonic"]))
        sections.append("")
    if report["rhythmic"] is not None:
        sections.append("-- Rhythmic --")
        sections.append(_render_rhythmic(report["rhythmic"]))
        sections.append("")
    if report["melodic"] is not None:
        sections.append("-- Melodic --")
        sections.append(_render_melodic(report["melodic"]))
        sections.append("")
    if report["structural"] is not None:
        sections.append("-- Structural --")
        sections.append(_render_structural(report["structural"]))
        sections.append("")
    if report["dynamic"] is not None:
        sections.append("-- Dynamic --")
        sections.append(_render_dynamic(report["dynamic"]))
        sections.append("")
    return "\n".join(sections)


@app.callback(invoke_without_command=True)
def diff(
    ctx: typer.Context,
    commit_a: Optional[str] = typer.Argument(
        None,
        help="Earlier commit ref (default: HEAD~1).",
        metavar="COMMIT_A",
    ),
    commit_b: Optional[str] = typer.Argument(
        None,
        help="Later commit ref (default: HEAD).",
        metavar="COMMIT_B",
    ),
    harmonic: bool = typer.Option(
        False,
        "--harmonic",
        help="Compare harmonic content (key, mode, chord progression, tension).",
    ),
    rhythmic: bool = typer.Option(
        False,
        "--rhythmic",
        help="Compare rhythmic content (tempo, meter, swing, groove drift).",
    ),
    melodic: bool = typer.Option(
        False,
        "--melodic",
        help="Compare melodic content (motifs, contour, pitch range).",
    ),
    structural: bool = typer.Option(
        False,
        "--structural",
        help="Compare structural content (sections, instrumentation, form).",
    ),
    dynamic: bool = typer.Option(
        False,
        "--dynamic",
        help="Compare dynamic profiles (velocity arc, per-track loudness).",
    ),
    all_dims: bool = typer.Option(
        False,
        "--all",
        help="Run all dimension analyses and produce a combined report.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON output for agent consumption.",
    ),
) -> None:
    """Compare two commits across musical dimensions.

    Without dimension flags, displays a usage hint. Specify at least one of
    --harmonic, --rhythmic, --melodic, --structural, --dynamic, or --all.
    """
    if ctx.invoked_subcommand is not None:
        return

    no_dims = not any([harmonic, rhythmic, melodic, structural, dynamic, all_dims])
    if no_dims:
        typer.echo(
            "Specify at least one dimension flag: "
            "--harmonic, --rhythmic, --melodic, --structural, --dynamic, --all"
        )
        typer.echo("Run `muse diff --help` for usage.")
        raise typer.Exit(code=ExitCode.SUCCESS)

    root = require_repo()

    async def _run() -> None:
        ref_a, ref_b = _resolve_refs(root, commit_a, commit_b)

        async with open_session():
            if all_dims:
                report = await _diff_all_async(
                    root=root,
                    commit_a=ref_a,
                    commit_b=ref_b,
                )
                if as_json:
                    typer.echo(json.dumps(dict(report), indent=2))
                else:
                    typer.echo(_render_report(report))
                return

            if harmonic:
                result_h = await _harmonic_diff_async(
                    root=root, commit_a=ref_a, commit_b=ref_b
                )
                if as_json:
                    typer.echo(json.dumps(dict(result_h), indent=2))
                else:
                    typer.echo(_render_harmonic(result_h))

            if rhythmic:
                result_r = await _rhythmic_diff_async(
                    root=root, commit_a=ref_a, commit_b=ref_b
                )
                if as_json:
                    typer.echo(json.dumps(dict(result_r), indent=2))
                else:
                    typer.echo(_render_rhythmic(result_r))

            if melodic:
                result_m = await _melodic_diff_async(
                    root=root, commit_a=ref_a, commit_b=ref_b
                )
                if as_json:
                    typer.echo(json.dumps(dict(result_m), indent=2))
                else:
                    typer.echo(_render_melodic(result_m))

            if structural:
                result_s = await _structural_diff_async(
                    root=root, commit_a=ref_a, commit_b=ref_b
                )
                if as_json:
                    typer.echo(json.dumps(dict(result_s), indent=2))
                else:
                    typer.echo(_render_structural(result_s))

            if dynamic:
                result_d = await _dynamic_diff_async(
                    root=root, commit_a=ref_a, commit_b=ref_b
                )
                if as_json:
                    typer.echo(json.dumps(dict(result_d), indent=2))
                else:
                    typer.echo(_render_dynamic(result_d))

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"muse diff failed: {exc}")
        logger.error("muse diff error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
