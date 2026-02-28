"""muse timeline — visualize musical evolution chronologically.

Renders a commit-by-commit chronological view of a composition's creative
arc with music-semantic metadata: emotion tags, section grouping, and
per-track activity.  This is the "album liner notes" view of a project's
evolution — no Git equivalent exists.

Default output (text)::

    2026-02-01  abc1234  Initial drum arrangement    [drums]        [melancholic]  ████
    2026-02-02  def5678  Add bass line               [bass]         [melancholic]  ██████
    2026-02-03  ghi9012  Chorus melody               [keys,vocals]  [joyful]       █████████

Flags
-----
--emotion     Add emotion indicator column (shown by default when tags exist).
--sections    Group commits under section headers (e.g. ── chorus ──).
--tracks      Show per-track activity column.
--json        Machine-readable JSON for UI rendering or agent consumption.
--limit N     Cap the commit walk (default: 1000).
[<range>]     Optional commit range for future ``HEAD~10..HEAD`` syntax.
              Currently accepted but reserved (full history is always shown).
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from typing import TypedDict

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.services.muse_timeline import (
    MuseTimelineEntry,
    MuseTimelineResult,
    build_timeline,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

_DEFAULT_LIMIT = 1000


class _TimelineEntryDict(TypedDict):
    """JSON-serializable shape of a single timeline entry."""

    commit_id: str
    short_id: str
    committed_at: str
    message: str
    emotion: str | None
    sections: list[str]
    tracks: list[str]
    activity: int


class _TimelineJsonPayload(TypedDict):
    """JSON-serializable shape of the full timeline response."""

    branch: str
    total_commits: int
    emotion_arc: list[str]
    section_order: list[str]
    entries: list[_TimelineEntryDict]

# Unicode block characters for activity density bars.
_BLOCK = "█"
_MAX_BLOCKS = 10
_MIN_BLOCKS = 1


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _activity_bar(activity: int, max_activity: int) -> str:
    """Render a Unicode block bar proportional to *activity*.

    Width is scaled so the most-active commit gets ``_MAX_BLOCKS`` blocks
    and the least-active gets at least ``_MIN_BLOCKS``.  Returns a blank
    string when ``max_activity`` is 0.
    """
    if max_activity == 0:
        return _BLOCK * _MIN_BLOCKS
    scaled = max(
        _MIN_BLOCKS,
        round(_MAX_BLOCKS * activity / max_activity),
    )
    return _BLOCK * scaled


def _load_muse_state(root: pathlib.Path) -> tuple[str, str, str]:
    """Read branch name, HEAD ref, and head_commit_id from ``.muse/``.

    Returns ``(branch, head_ref, head_commit_id)``.  ``head_commit_id``
    is an empty string when the branch has no commits yet.
    """
    import json as _json

    muse_dir = root / ".muse"
    head_ref = (muse_dir / "HEAD").read_text().strip()
    branch = head_ref.rsplit("/", 1)[-1] if "/" in head_ref else head_ref
    ref_path = muse_dir / pathlib.Path(head_ref)
    head_commit_id = ref_path.read_text().strip() if ref_path.exists() else ""
    repo_data: dict[str, str] = _json.loads((muse_dir / "repo.json").read_text())
    repo_id = repo_data["repo_id"]
    return repo_id, branch, head_commit_id


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_text(
    result: MuseTimelineResult,
    *,
    show_emotion: bool,
    show_sections: bool,
    show_tracks: bool,
) -> None:
    """Render the timeline as a human-readable terminal table.

    Columns (left to right):
    - Date (YYYY-MM-DD)
    - Short ID (7 chars)
    - Message (truncated to 30 chars)
    - Tracks (comma-joined, or — if none)
    - Emotion (or — if none) [when show_emotion or show_sections are on]
    - Unicode activity bar

    When ``show_sections`` is True, a section header line is printed
    whenever the section set changes.
    """
    entries = result.entries
    if not entries:
        typer.echo("No commits in timeline.")
        return

    typer.echo(f"Timeline — branch: {result.branch}  ({result.total_commits} commit(s))")
    typer.echo("")

    max_activity = max(e.activity for e in entries) if entries else 1

    prev_sections: tuple[str, ...] = ()

    for entry in entries:
        if show_sections and entry.sections != prev_sections:
            sections_label = ", ".join(entry.sections) if entry.sections else "—"
            typer.echo(f"  ── {sections_label} ──")
            prev_sections = entry.sections

        date_str = entry.committed_at.strftime("%Y-%m-%d")
        short_id = entry.short_id
        message = entry.message[:30].ljust(30)
        bar = _activity_bar(entry.activity, max_activity)

        tracks_col = ""
        if show_tracks:
            tracks_label = ",".join(entry.tracks) if entry.tracks else "—"
            tracks_col = f"  [{tracks_label:<20}]"

        emotion_col = ""
        if show_emotion:
            emotion_label = entry.emotion or "—"
            emotion_col = f"  [{emotion_label:<15}]"

        typer.echo(
            f"{date_str}  {short_id}  {message}{tracks_col}{emotion_col}  {bar}"
        )

    typer.echo("")
    if result.emotion_arc:
        typer.echo(f"Emotion arc: {' → '.join(result.emotion_arc)}")
    if result.section_order:
        typer.echo(f"Sections:    {' → '.join(result.section_order)}")


def _entry_to_dict(entry: MuseTimelineEntry) -> _TimelineEntryDict:
    """Serialize a :class:`MuseTimelineEntry` to a JSON-safe dict."""
    return {
        "commit_id": entry.commit_id,
        "short_id": entry.short_id,
        "committed_at": entry.committed_at.isoformat(),
        "message": entry.message,
        "emotion": entry.emotion,
        "sections": list(entry.sections),
        "tracks": list(entry.tracks),
        "activity": entry.activity,
    }


def _render_json(result: MuseTimelineResult) -> None:
    """Emit the timeline as a JSON object for UI rendering or agent consumption."""
    payload: _TimelineJsonPayload = {
        "branch": result.branch,
        "total_commits": result.total_commits,
        "emotion_arc": list(result.emotion_arc),
        "section_order": list(result.section_order),
        "entries": [_entry_to_dict(e) for e in result.entries],
    }
    typer.echo(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _timeline_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    commit_range: str | None,
    show_emotion: bool,
    show_sections: bool,
    show_tracks: bool,
    as_json: bool,
    limit: int,
) -> MuseTimelineResult:
    """Core timeline logic — fully injectable for tests.

    Reads repo state from ``.muse/``, loads commits + tags from the DB
    session, then renders the result.  Returns the :class:`MuseTimelineResult`
    so callers can inspect it without parsing printed output.

    Args:
        root:          Repository root (contains ``.muse/``).
        session:       Open async DB session.
        commit_range:  Optional range string (reserved for future use).
        show_emotion:  Include emotion column in text output.
        show_sections: Group commits by section in text output.
        show_tracks:   Include tracks column in text output.
        as_json:       Emit JSON instead of the text table.
        limit:         Maximum commits to include.

    Returns:
        :class:`MuseTimelineResult` (oldest-first).
    """
    repo_id, branch, head_commit_id = _load_muse_state(root)

    if not head_commit_id:
        typer.echo(f"No commits yet on branch {branch} — timeline is empty.")
        raise typer.Exit(code=ExitCode.SUCCESS)

    if commit_range is not None:
        typer.echo(
            f"⚠️  Commit range '{commit_range}' is reserved for a future iteration. "
            "Showing full history."
        )

    result = await build_timeline(
        session,
        repo_id=repo_id,
        branch=branch,
        head_commit_id=head_commit_id,
        limit=limit,
    )

    if as_json:
        _render_json(result)
    else:
        _render_text(
            result,
            show_emotion=show_emotion,
            show_sections=show_sections,
            show_tracks=show_tracks,
        )

    return result


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def timeline(
    ctx: typer.Context,
    commit_range: str | None = typer.Argument(
        None,
        help="Commit range (reserved — full history is always shown for now).",
        metavar="RANGE",
    ),
    show_emotion: bool = typer.Option(
        False,
        "--emotion",
        help="Add an emotion column (derived from emotion:* tags).",
    ),
    show_sections: bool = typer.Option(
        False,
        "--sections",
        help="Group commits under section headers (derived from section:* tags).",
    ),
    show_tracks: bool = typer.Option(
        False,
        "--tracks",
        help="Show per-track activity column (derived from track:* tags).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON suitable for UI rendering or agent consumption.",
    ),
    limit: int = typer.Option(
        _DEFAULT_LIMIT,
        "--limit",
        "-n",
        help="Maximum number of commits to walk.",
        min=1,
    ),
) -> None:
    """Visualize the musical evolution of a composition chronologically."""
    root = require_repo()

    async def _run() -> None:
        async with open_session() as session:
            await _timeline_async(
                root=root,
                session=session,
                commit_range=commit_range,
                show_emotion=show_emotion,
                show_sections=show_sections,
                show_tracks=show_tracks,
                as_json=as_json,
                limit=limit,
            )

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse timeline failed: {exc}")
        logger.error("❌ muse timeline error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
