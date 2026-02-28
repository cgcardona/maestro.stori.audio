"""muse grep — search for a musical pattern across all commits.

This command searches Muse VCS commit history for a given pattern and returns
all commits where the pattern is found.

**Current implementation (stub):** The pattern is matched against commit
*messages* and *branch names* using case-insensitive substring matching.
Full MIDI content analysis — scanning note sequences, intervals, and chord
shapes inside committed snapshots — is reserved for a future iteration.

Pattern formats recognised (planned for MIDI content search, not yet analysed):

- Note sequence:  ``"C4 E4 G4"``
- Interval run:   ``"+4 +3"``
- Chord symbol:   ``"Cm7"``

Future work (MIDI analysis)
---------------------------
When MIDI content search is implemented each committed snapshot will be decoded
from its object store, parsed into note events, and compared against the
pattern using the flags below:

- ``--transposition-invariant`` (default ``True``): match regardless of key.
- ``--rhythm-invariant``: match regardless of rhythm/timing.
- ``--track``: restrict search to a named track.
- ``--section``: restrict search to a labelled section.

Until then these flags are accepted to preserve the CLI contract but only
``--commits`` and ``--json`` affect output; the four MIDI-analysis flags are
recorded for future use and produce a warning when supplied.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import pathlib

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.db import open_session
from maestro.muse_cli.errors import ExitCode
from maestro.muse_cli.models import MuseCliCommit

logger = logging.getLogger(__name__)

app = typer.Typer()

_DEFAULT_LIMIT = 1000


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class GrepMatch:
    """A single commit that matched the search pattern.

    ``match_source`` records *where* the pattern was found:
    - ``"message"`` — in the commit message (implemented)
    - ``"branch"``  — in the branch name (implemented)
    - ``"midi_content"`` — inside a committed MIDI snapshot (future work)
    """

    commit_id: str
    branch: str
    message: str
    committed_at: str  # ISO-8601 string
    match_source: str


# ---------------------------------------------------------------------------
# Testable async core
# ---------------------------------------------------------------------------


async def _load_all_commits(
    session: AsyncSession,
    head_commit_id: str,
    limit: int,
) -> list[MuseCliCommit]:
    """Walk the parent chain from *head_commit_id*, returning newest-first.

    Stops when the chain is exhausted or *limit* is reached.
    """
    commits: list[MuseCliCommit] = []
    current_id: str | None = head_commit_id
    while current_id and len(commits) < limit:
        commit = await session.get(MuseCliCommit, current_id)
        if commit is None:
            logger.warning("⚠️ Commit %s not found in DB — chain broken", current_id[:8])
            break
        commits.append(commit)
        current_id = commit.parent_commit_id
    return commits


def _match_commit(
    commit: MuseCliCommit,
    pattern: str,
    *,
    track: str | None,
    section: str | None,
    transposition_invariant: bool,
    rhythm_invariant: bool,
) -> GrepMatch | None:
    """Return a :class:`GrepMatch` if the commit matches *pattern*, else ``None``.

    Currently performs case-insensitive substring matching against commit
    messages and branch names.  The ``track``, ``section``,
    ``transposition_invariant``, and ``rhythm_invariant`` flags are accepted
    for API stability but are no-ops until MIDI content search is implemented.
    """
    pat = pattern.lower()

    if pat in commit.message.lower():
        return GrepMatch(
            commit_id=commit.commit_id,
            branch=commit.branch,
            message=commit.message,
            committed_at=commit.committed_at.isoformat(),
            match_source="message",
        )

    if pat in commit.branch.lower():
        return GrepMatch(
            commit_id=commit.commit_id,
            branch=commit.branch,
            message=commit.message,
            committed_at=commit.committed_at.isoformat(),
            match_source="branch",
        )

    # NOTE: MIDI content search (track / section / transposition / rhythm filters)
    # is not yet implemented.  The flags above will be wired here in future.
    return None


async def _grep_async(
    *,
    root: pathlib.Path,
    session: AsyncSession,
    pattern: str,
    track: str | None,
    section: str | None,
    transposition_invariant: bool,
    rhythm_invariant: bool,
    show_commits: bool,
    output_json: bool,
) -> list[GrepMatch]:
    """Core grep logic — fully injectable for tests.

    Reads repo state from ``.muse/``, walks the commit chain, and returns
    all :class:`GrepMatch` objects that satisfy *pattern*.
    """
    muse_dir = root / ".muse"
    head_ref = (muse_dir / "HEAD").read_text().strip()  # "refs/heads/main"
    branch = head_ref.rsplit("/", 1)[-1]  # "main"
    ref_path = muse_dir / pathlib.Path(head_ref)

    head_commit_id = ""
    if ref_path.exists():
        head_commit_id = ref_path.read_text().strip()

    if not head_commit_id:
        typer.echo(f"No commits yet on branch {branch} — nothing to search.")
        return []

    commits = await _load_all_commits(session, head_commit_id=head_commit_id, limit=_DEFAULT_LIMIT)

    matches: list[GrepMatch] = []
    for commit in commits:
        m = _match_commit(
            commit,
            pattern,
            track=track,
            section=section,
            transposition_invariant=transposition_invariant,
            rhythm_invariant=rhythm_invariant,
        )
        if m is not None:
            matches.append(m)

    return matches


def _render_matches(
    matches: list[GrepMatch],
    *,
    pattern: str,
    show_commits: bool,
    output_json: bool,
) -> None:
    """Write grep results to stdout.

    Three output modes:
    - ``--json``:    machine-readable JSON array.
    - ``--commits``: one ``commit_id`` per line (like ``git grep --name-only``).
    - default:       human-readable summary with context.
    """
    if output_json:
        typer.echo(
            json.dumps(
                [dataclasses.asdict(m) for m in matches],
                indent=2,
            )
        )
        return

    if not matches:
        typer.echo(f"No commits match pattern: {pattern!r}")
        return

    if show_commits:
        for m in matches:
            typer.echo(m.commit_id)
        return

    # Default human-readable output
    typer.echo(f"Pattern: {pattern!r}  ({len(matches)} match(es))\n")
    for m in matches:
        typer.echo(f"commit {m.commit_id}")
        typer.echo(f"Branch:  {m.branch}")
        typer.echo(f"Date:    {m.committed_at}")
        typer.echo(f"Match:   [{m.match_source}]")
        typer.echo(f"Message: {m.message}")
        typer.echo("")


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def grep(
    ctx: typer.Context,
    pattern: str = typer.Argument(..., help="Pattern to search for (note sequence, interval, chord, or text)."),
    track: str | None = typer.Option(
        None,
        "--track",
        help="[Future] Restrict search to a named track.",
        show_default=False,
    ),
    section: str | None = typer.Option(
        None,
        "--section",
        help="[Future] Restrict search to a labelled section.",
        show_default=False,
    ),
    transposition_invariant: bool = typer.Option(
        True,
        "--transposition-invariant/--no-transposition-invariant",
        help="[Future] Match regardless of key/transposition (default: on).",
    ),
    rhythm_invariant: bool = typer.Option(
        False,
        "--rhythm-invariant",
        help="[Future] Match regardless of rhythm/timing.",
    ),
    show_commits: bool = typer.Option(
        False,
        "--commits",
        help="Output one commit ID per line (like git grep --name-only).",
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Output results as a JSON array.",
    ),
) -> None:
    """Search for a musical pattern across all commits.

    NOTE: The current implementation searches commit *messages* and *branch
    names* for the pattern string.  Full MIDI content analysis (note
    sequences, intervals, chord symbols) is planned for a future release.
    Flags marked [Future] are accepted now for API stability but have no
    effect on text-only matching.
    """
    root = require_repo()

    # Warn when MIDI-analysis flags are supplied — they are no-ops right now.
    if track is not None:
        typer.echo("⚠️  --track is not yet implemented (MIDI analysis is future work).")
    if section is not None:
        typer.echo("⚠️  --section is not yet implemented (MIDI analysis is future work).")
    if rhythm_invariant:
        typer.echo("⚠️  --rhythm-invariant is not yet implemented (MIDI analysis is future work).")

    async def _run() -> list[GrepMatch]:
        async with open_session() as session:
            return await _grep_async(
                root=root,
                session=session,
                pattern=pattern,
                track=track,
                section=section,
                transposition_invariant=transposition_invariant,
                rhythm_invariant=rhythm_invariant,
                show_commits=show_commits,
                output_json=output_json,
            )

    try:
        matches = asyncio.run(_run())
        _render_matches(matches, pattern=pattern, show_commits=show_commits, output_json=output_json)
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"❌ muse grep failed: {exc}")
        logger.error("❌ muse grep error: %s", exc, exc_info=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
