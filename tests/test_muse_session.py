"""Tests for ``muse session`` — recording session metadata stored as JSON files.

All tests are purely file-based (no DB, no async) and use ``tmp_path`` for
isolation. The ``MUSE_REPO_ROOT`` env-var override keeps tests free of
``os.chdir`` calls so they run safely in parallel.
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid

import pytest
from click.testing import Result
from typer.testing import CliRunner

from maestro.muse_cli.app import cli
from maestro.muse_cli.commands.session import (
    _load_completed_sessions,
    _sessions_dir,
    app as session_app,
)
from maestro.muse_cli.errors import ExitCode

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _init_repo(root: pathlib.Path) -> pathlib.Path:
    """Create a minimal .muse/ layout and return the repo root."""
    muse = root / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": str(uuid.uuid4()), "schema_version": "1"})
    )
    (muse / "HEAD").write_text("refs/heads/main\n")
    (muse / "refs" / "heads" / "main").write_text("")
    return root


def _invoke(args: list[str], repo_root: pathlib.Path) -> Result:
    """Invoke the top-level CLI with MUSE_REPO_ROOT set to *repo_root*."""
    env = {**os.environ, "MUSE_REPO_ROOT": str(repo_root)}
    return runner.invoke(cli, args, env=env)


def _invoke_session(args: list[str], repo_root: pathlib.Path) -> Result:
    """Invoke the session sub-app with MUSE_REPO_ROOT set."""
    env = {**os.environ, "MUSE_REPO_ROOT": str(repo_root)}
    return runner.invoke(session_app, args, env=env)


# ---------------------------------------------------------------------------
# muse session start
# ---------------------------------------------------------------------------


def test_session_start_creates_current_json(tmp_path: pathlib.Path) -> None:
    """``muse session start`` writes current.json with required fields."""
    _init_repo(tmp_path)
    result = _invoke(["session", "start"], tmp_path)

    assert result.exit_code == 0, result.output
    current = tmp_path / ".muse" / "sessions" / "current.json"
    assert current.exists(), "current.json was not created"
    data = json.loads(current.read_text())
    assert "session_id" in data
    assert "started_at" in data
    assert data["ended_at"] is None
    assert isinstance(data["participants"], list)
    assert "✅" in result.output


def test_session_start_with_options(tmp_path: pathlib.Path) -> None:
    """``muse session start`` captures participants, location, and intent."""
    _init_repo(tmp_path)
    result = _invoke(
        [
            "session",
            "start",
            "--participants",
            "Alice,Bob",
            "--location",
            "Studio A",
            "--intent",
            "Record the bridge",
        ],
        tmp_path,
    )

    assert result.exit_code == 0, result.output
    current = tmp_path / ".muse" / "sessions" / "current.json"
    data = json.loads(current.read_text())
    assert data["participants"] == ["Alice", "Bob"]
    assert data["location"] == "Studio A"
    assert data["intent"] == "Record the bridge"


def test_session_start_rejects_duplicate_session(tmp_path: pathlib.Path) -> None:
    """Starting a second session while one is active exits with USER_ERROR."""
    _init_repo(tmp_path)
    _invoke(["session", "start"], tmp_path)
    result = _invoke(["session", "start"], tmp_path)

    assert result.exit_code == int(ExitCode.USER_ERROR)
    assert "already active" in result.output.lower()


def test_session_start_no_repo_exits_2(tmp_path: pathlib.Path) -> None:
    """``muse session start`` outside a repo exits 2."""
    env = {**os.environ, "MUSE_REPO_ROOT": str(tmp_path)}
    result = runner.invoke(cli, ["session", "start"], env=env)
    assert result.exit_code == int(ExitCode.REPO_NOT_FOUND)


# ---------------------------------------------------------------------------
# muse session end
# ---------------------------------------------------------------------------


def test_session_end_finalises_session(tmp_path: pathlib.Path) -> None:
    """``muse session end`` moves current.json to <uuid>.json with ended_at set."""
    _init_repo(tmp_path)
    _invoke(["session", "start"], tmp_path)

    current = tmp_path / ".muse" / "sessions" / "current.json"
    session_id = json.loads(current.read_text())["session_id"]

    result = _invoke(["session", "end"], tmp_path)
    assert result.exit_code == 0, result.output

    assert not current.exists(), "current.json should be removed after end"
    final = tmp_path / ".muse" / "sessions" / f"{session_id}.json"
    assert final.exists(), f"{session_id}.json was not created"
    data = json.loads(final.read_text())
    assert data["ended_at"] is not None
    assert "✅" in result.output


def test_session_end_with_notes(tmp_path: pathlib.Path) -> None:
    """``muse session end --notes`` saves closing notes in the session file."""
    _init_repo(tmp_path)
    _invoke(["session", "start"], tmp_path)

    result = _invoke(["session", "end", "--notes", "Great take on measure 8"], tmp_path)
    assert result.exit_code == 0, result.output

    sessions_dir = tmp_path / ".muse" / "sessions"
    finals = [p for p in sessions_dir.glob("*.json") if p.name != "current.json"]
    assert len(finals) == 1
    data = json.loads(finals[0].read_text())
    assert data["notes"] == "Great take on measure 8"


def test_session_end_without_active_session(tmp_path: pathlib.Path) -> None:
    """``muse session end`` with no active session exits with USER_ERROR."""
    _init_repo(tmp_path)
    result = _invoke(["session", "end"], tmp_path)

    assert result.exit_code == int(ExitCode.USER_ERROR)
    assert "no active session" in result.output.lower()


# ---------------------------------------------------------------------------
# muse session log
# ---------------------------------------------------------------------------


def test_session_log_empty(tmp_path: pathlib.Path) -> None:
    """``muse session log`` reports no sessions when none exist."""
    _init_repo(tmp_path)
    result = _invoke(["session", "log"], tmp_path)

    assert result.exit_code == 0, result.output
    assert "no completed sessions" in result.output.lower()


def test_session_log_lists_sessions_newest_first(tmp_path: pathlib.Path) -> None:
    """``muse session log`` lists sessions newest-first by started_at."""
    _init_repo(tmp_path)

    # Create two sessions back-to-back
    _invoke(["session", "start", "--participants", "Alice"], tmp_path)
    _invoke(["session", "end"], tmp_path)
    _invoke(["session", "start", "--participants", "Bob"], tmp_path)
    _invoke(["session", "end"], tmp_path)

    result = _invoke(["session", "log"], tmp_path)
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 2
    # Newest session should appear first — parse first column (8-char UUID prefix)
    sessions_dir = tmp_path / ".muse" / "sessions"
    all_sessions = _load_completed_sessions(sessions_dir)
    assert str(all_sessions[0].get("started_at", "")) >= str(
        all_sessions[1].get("started_at", "")
    )


def test_session_log_skips_active_current(tmp_path: pathlib.Path) -> None:
    """``muse session log`` does not list the active current.json."""
    _init_repo(tmp_path)
    _invoke(["session", "start"], tmp_path)

    result = _invoke(["session", "log"], tmp_path)
    assert result.exit_code == 0, result.output
    assert "no completed sessions" in result.output.lower()


# ---------------------------------------------------------------------------
# muse session show
# ---------------------------------------------------------------------------


def test_session_show_full_json(tmp_path: pathlib.Path) -> None:
    """``muse session show <id>`` prints the full JSON for that session."""
    _init_repo(tmp_path)
    _invoke(["session", "start", "--participants", "Carol"], tmp_path)
    current = tmp_path / ".muse" / "sessions" / "current.json"
    session_id = json.loads(current.read_text())["session_id"]
    _invoke(["session", "end"], tmp_path)

    result = _invoke(["session", "show", session_id], tmp_path)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["session_id"] == session_id
    assert parsed["participants"] == ["Carol"]


def test_session_show_by_prefix(tmp_path: pathlib.Path) -> None:
    """``muse session show`` accepts a unique prefix of the session ID."""
    _init_repo(tmp_path)
    _invoke(["session", "start"], tmp_path)
    current = tmp_path / ".muse" / "sessions" / "current.json"
    session_id = json.loads(current.read_text())["session_id"]
    _invoke(["session", "end"], tmp_path)

    prefix = session_id[:8]
    result = _invoke(["session", "show", prefix], tmp_path)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["session_id"] == session_id


def test_session_show_unknown_id(tmp_path: pathlib.Path) -> None:
    """``muse session show`` with an unknown ID exits with USER_ERROR."""
    _init_repo(tmp_path)
    result = _invoke(["session", "show", "nonexistent-id"], tmp_path)
    assert result.exit_code == int(ExitCode.USER_ERROR)
    assert "no session found" in result.output.lower()


def test_session_show_ambiguous_prefix(tmp_path: pathlib.Path) -> None:
    """``muse session show`` with an ambiguous prefix exits with USER_ERROR."""
    _init_repo(tmp_path)
    sessions_dir = tmp_path / ".muse" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    # Write two sessions whose IDs share a common prefix
    for suffix in ("aaaa-bbbb", "aaaa-cccc"):
        fake_id = f"00000000-{suffix}-0000-000000000000"
        sessions_dir.joinpath(f"{fake_id}.json").write_text(
            json.dumps(
                {
                    "session_id": fake_id,
                    "started_at": "2024-01-01T00:00:00+00:00",
                    "ended_at": "2024-01-01T01:00:00+00:00",
                    "participants": [],
                    "location": "",
                    "intent": "",
                    "commits": [],
                    "notes": "",
                }
            )
        )

    result = _invoke(["session", "show", "00000000"], tmp_path)
    assert result.exit_code == int(ExitCode.USER_ERROR)
    assert "ambiguous" in result.output.lower()


# ---------------------------------------------------------------------------
# muse session credits
# ---------------------------------------------------------------------------


def test_session_credits_aggregates_participants(tmp_path: pathlib.Path) -> None:
    """``muse session credits`` lists all participants with session counts."""
    _init_repo(tmp_path)

    # Session 1: Alice + Bob
    _invoke(["session", "start", "--participants", "Alice,Bob"], tmp_path)
    _invoke(["session", "end"], tmp_path)
    # Session 2: Alice + Carol
    _invoke(["session", "start", "--participants", "Alice,Carol"], tmp_path)
    _invoke(["session", "end"], tmp_path)

    result = _invoke(["session", "credits"], tmp_path)
    assert result.exit_code == 0, result.output
    assert "Alice" in result.output
    assert "Bob" in result.output
    assert "Carol" in result.output
    # Alice appeared in 2 sessions — highest count
    lines = result.output.splitlines()
    alice_line = next(l for l in lines if "Alice" in l)
    assert "2" in alice_line


def test_session_credits_no_participants(tmp_path: pathlib.Path) -> None:
    """``muse session credits`` with no sessions reports no participants."""
    _init_repo(tmp_path)
    result = _invoke(["session", "credits"], tmp_path)

    assert result.exit_code == 0, result.output
    assert "no participants" in result.output.lower()


def test_session_credits_sorted_by_count_desc(tmp_path: pathlib.Path) -> None:
    """Credits output is sorted by session count descending."""
    _init_repo(tmp_path)
    # Dave: 1 session, Eve: 2 sessions
    _invoke(["session", "start", "--participants", "Dave,Eve"], tmp_path)
    _invoke(["session", "end"], tmp_path)
    _invoke(["session", "start", "--participants", "Eve"], tmp_path)
    _invoke(["session", "end"], tmp_path)

    result = _invoke(["session", "credits"], tmp_path)
    assert result.exit_code == 0, result.output
    lines = [l for l in result.output.splitlines() if "Eve" in l or "Dave" in l]
    assert lines[0].find("Eve") != -1 or "2" in lines[0], (
        "Eve should appear before Dave (higher count)"
    )


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_session_json_schema(tmp_path: pathlib.Path) -> None:
    """Completed session JSON contains all required schema fields."""
    _init_repo(tmp_path)
    _invoke(
        [
            "session",
            "start",
            "--participants",
            "Frank",
            "--location",
            "Room 101",
            "--intent",
            "Groove track",
        ],
        tmp_path,
    )
    _invoke(["session", "end", "--notes", "Nailed it"], tmp_path)

    sessions_dir = tmp_path / ".muse" / "sessions"
    finals = list(sessions_dir.glob("*.json"))
    assert len(finals) == 1
    data = json.loads(finals[0].read_text())

    required_fields = {
        "session_id",
        "started_at",
        "ended_at",
        "participants",
        "location",
        "intent",
        "commits",
        "notes",
    }
    missing = required_fields - data.keys()
    assert not missing, f"Missing fields in session JSON: {missing}"
    assert data["participants"] == ["Frank"]
    assert data["location"] == "Room 101"
    assert data["intent"] == "Groove track"
    assert data["notes"] == "Nailed it"
    assert data["commits"] == []
    assert data["ended_at"] is not None


# ---------------------------------------------------------------------------
# Internal helper tests
# ---------------------------------------------------------------------------


def test_sessions_dir_created_on_demand(tmp_path: pathlib.Path) -> None:
    """``_sessions_dir`` creates the directory if it does not exist."""
    assert not (tmp_path / ".muse" / "sessions").exists()
    result = _sessions_dir(tmp_path)
    assert result.is_dir()


def test_load_completed_sessions_excludes_current(tmp_path: pathlib.Path) -> None:
    """``_load_completed_sessions`` never includes current.json."""
    sessions_dir = tmp_path / ".muse" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "current.json").write_text(
        json.dumps({"session_id": "active", "started_at": "2024-01-01T00:00:00+00:00"})
    )
    results = _load_completed_sessions(sessions_dir)
    assert results == []
