"""muse session — record and query recording session metadata.

Sessions are stored as JSON files in ``.muse/sessions/`` — purely local,
never in Postgres. This mirrors the way git stores commit metadata as
plain files rather than in a database.

Directory layout::

    .muse/
        sessions/
            current.json        ← active session (only while recording)
            <uuid>.json         ← completed sessions (one file each)

Session JSON schema::

    {
        "session_id": "<uuid4>",
        "started_at": "<ISO-8601>",
        "ended_at": "<ISO-8601 | null>",
        "participants": ["name", ...],
        "location": "<string>",
        "intent": "<string>",
        "commits": [],
        "notes": "<string>"
    }

Subcommands
-----------
- ``start``   — open a new session (writes ``current.json``)
- ``end``     — finalise the active session (moves to ``<uuid>.json``)
- ``log``     — list all completed sessions, newest first
- ``show``    — print a specific session by ID (prefix match supported)
- ``credits`` — aggregate all participants across completed sessions
"""
from __future__ import annotations

import datetime
import json
import logging
import pathlib
import uuid
from typing import Annotated

import typer

from maestro.muse_cli._repo import require_repo
from maestro.muse_cli.errors import ExitCode

logger = logging.getLogger(__name__)

app = typer.Typer(no_args_is_help=True)

_CURRENT = "current.json"
_SESSION_SCHEMA_VERSION = "1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sessions_dir(repo_root: pathlib.Path) -> pathlib.Path:
    """Return (and create if needed) the .muse/sessions/ directory."""
    d = repo_root / ".muse" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_json(path: pathlib.Path) -> dict[str, object]:
    """Read and parse a JSON file; raise typer.Exit on error."""
    try:
        raw: dict[str, object] = json.loads(path.read_text())
        return raw
    except json.JSONDecodeError as exc:
        typer.echo(f"❌ Corrupt session file {path.name}: {exc}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR) from exc
    except OSError as exc:
        typer.echo(f"❌ Cannot read {path}: {exc}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR) from exc


def _write_json(path: pathlib.Path, data: dict[str, object]) -> None:
    """Write *data* as indented JSON to *path*."""
    try:
        path.write_text(json.dumps(data, indent=2) + "\n")
    except OSError as exc:
        typer.echo(f"❌ Cannot write {path}: {exc}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR) from exc


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _load_completed_sessions(
    sessions_dir: pathlib.Path,
) -> list[dict[str, object]]:
    """Return all completed session dicts sorted by started_at desc."""
    sessions: list[dict[str, object]] = []
    for p in sessions_dir.glob("*.json"):
        if p.name == _CURRENT:
            continue
        try:
            data = _read_json(p)
            sessions.append(data)
        except SystemExit:
            # _read_json already printed an error; skip corrupt files in log/credits
            logger.warning("⚠️ Skipping corrupt session file: %s", p.name)
    sessions.sort(key=lambda s: str(s.get("started_at", "")), reverse=True)
    return sessions


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


@app.command("start")
def start(
    participants: Annotated[
        str,
        typer.Option(
            "--participants",
            help="Comma-separated list of participant names.",
        ),
    ] = "",
    location: Annotated[
        str,
        typer.Option("--location", help="Recording location or studio name."),
    ] = "",
    intent: Annotated[
        str,
        typer.Option("--intent", help="Creative intent or goal for this session."),
    ] = "",
) -> None:
    """Start a new recording session.

    Writes ``.muse/sessions/current.json``. Only one active session is
    supported at a time; use ``muse session end`` before starting a new one.
    """
    repo_root = require_repo()
    sessions_dir = _sessions_dir(repo_root)
    current_path = sessions_dir / _CURRENT

    if current_path.exists():
        typer.echo(
            "⚠️  A session is already active. Run `muse session end` before starting a new one."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    participant_list = [p.strip() for p in participants.split(",") if p.strip()]
    session_id = str(uuid.uuid4())
    session: dict[str, object] = {
        "session_id": session_id,
        "schema_version": _SESSION_SCHEMA_VERSION,
        "started_at": _now_iso(),
        "ended_at": None,
        "participants": participant_list,
        "location": location,
        "intent": intent,
        "commits": [],
        "notes": "",
    }
    _write_json(current_path, session)

    typer.echo(f"✅ Session started  [{session_id}]")
    if participant_list:
        typer.echo(f"   Participants : {', '.join(participant_list)}")
    if location:
        typer.echo(f"   Location     : {location}")
    if intent:
        typer.echo(f"   Intent       : {intent}")
    logger.info("✅ Session started: %s", session_id)


@app.command("end")
def end(
    notes: Annotated[
        str,
        typer.Option("--notes", help="Closing notes for the session."),
    ] = "",
) -> None:
    """End the active recording session.

    Reads ``.muse/sessions/current.json``, sets ``ended_at``, then moves
    the file to ``.muse/sessions/<session_id>.json``.
    """
    repo_root = require_repo()
    sessions_dir = _sessions_dir(repo_root)
    current_path = sessions_dir / _CURRENT

    if not current_path.exists():
        typer.echo("⚠️  No active session. Run `muse session start` first.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    session = _read_json(current_path)
    session["ended_at"] = _now_iso()
    if notes:
        session["notes"] = notes

    session_id = str(session.get("session_id", uuid.uuid4()))
    dest = sessions_dir / f"{session_id}.json"
    _write_json(dest, session)

    try:
        current_path.unlink()
    except OSError as exc:
        typer.echo(f"❌ Failed to remove current.json: {exc}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR) from exc

    typer.echo(f"✅ Session ended    [{session_id}]")
    typer.echo(f"   Saved to       : .muse/sessions/{session_id}.json")
    logger.info("✅ Session ended: %s", session_id)


@app.command("log")
def log_sessions() -> None:
    """List all completed sessions, newest first."""
    repo_root = require_repo()
    sessions_dir = _sessions_dir(repo_root)
    sessions = _load_completed_sessions(sessions_dir)

    if not sessions:
        typer.echo("No completed sessions found.")
        return

    for s in sessions:
        sid = str(s.get("session_id", "?"))
        started = str(s.get("started_at", "?"))
        ended = str(s.get("ended_at", "?"))
        parts = s.get("participants", [])
        part_list = parts if isinstance(parts, list) else []
        part_str = ", ".join(str(p) for p in part_list)
        typer.echo(f"{sid[:8]}  {started[:19]}  →  {ended[:19]}  [{part_str}]")


@app.command("show")
def show(
    session_id: Annotated[
        str,
        typer.Argument(help="Session ID or unique prefix to display."),
    ],
) -> None:
    """Show the full JSON for a completed session.

    Accepts a unique prefix of the session UUID (minimum 4 characters).
    """
    repo_root = require_repo()
    sessions_dir = _sessions_dir(repo_root)

    matches: list[pathlib.Path] = []
    for p in sessions_dir.glob("*.json"):
        if p.name == _CURRENT:
            continue
        if p.stem.startswith(session_id):
            matches.append(p)

    if not matches:
        typer.echo(f"❌ No session found matching '{session_id}'.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if len(matches) > 1:
        typer.echo(
            f"⚠️  Ambiguous prefix '{session_id}' matches {len(matches)} sessions. "
            "Provide more characters."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    session = _read_json(matches[0])
    typer.echo(json.dumps(session, indent=2))


@app.command("credits")
def credits_cmd() -> None:
    """Aggregate all participants across completed sessions.

    Outputs each unique participant name and the number of sessions they
    appear in, sorted by session count descending.
    """
    repo_root = require_repo()
    sessions_dir = _sessions_dir(repo_root)
    sessions = _load_completed_sessions(sessions_dir)

    counts: dict[str, int] = {}
    for s in sessions:
        parts = s.get("participants", [])
        if isinstance(parts, list):
            for p in parts:
                name = str(p).strip()
                if name:
                    counts[name] = counts.get(name, 0) + 1

    if not counts:
        typer.echo("No participants recorded across any completed sessions.")
        return

    typer.echo("Session credits:")
    for name, n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        typer.echo(f"  {name:30s}  {n} session{'s' if n != 1 else ''}")
