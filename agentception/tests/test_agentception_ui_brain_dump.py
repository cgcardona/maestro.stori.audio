"""Tests for the Brain Dump UI routes (issue #826 — sidebar polish).

Covers:
- GET /brain-dump page renders correctly
- GET /brain-dump/recent-runs HTMX partial
- GET /api/brain-dump/{run_id}/dump-text endpoint
- _parse_task_fields helper
- _count_dump_items helper

Run targeted:
    pytest agentception/tests/test_agentception_ui_brain_dump.py -v
"""
from __future__ import annotations

import textwrap
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agentception.app import app
from agentception.config import AgentCeptionSettings
from agentception.routes.ui.brain_dump import _count_dump_items, _parse_task_fields


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    """Synchronous test client."""
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Unit tests — pure helpers
# ---------------------------------------------------------------------------


def test_parse_task_fields_extracts_key_value_pairs() -> None:
    """_parse_task_fields must parse structured key=value lines correctly."""
    content = textwrap.dedent("""\
        WORKFLOW=bugs-to-issues
        GH_REPO=cgcardona/maestro
        BATCH_ID=brain-dump-20260303-164033
        LABEL_PREFIX=q2-rewrite

        BRAIN_DUMP:
        - Some item
    """)
    fields = _parse_task_fields(content)
    assert fields["WORKFLOW"] == "bugs-to-issues"
    assert fields["BATCH_ID"] == "brain-dump-20260303-164033"
    assert fields["LABEL_PREFIX"] == "q2-rewrite"


def test_parse_task_fields_stops_at_brain_dump_marker() -> None:
    """_parse_task_fields must not parse lines after BRAIN_DUMP:."""
    content = "KEY=value\nBRAIN_DUMP:\nFAKE_KEY=should_not_appear\n"
    fields = _parse_task_fields(content)
    assert "KEY" in fields
    assert "FAKE_KEY" not in fields


def test_parse_task_fields_stops_at_blank_line() -> None:
    """_parse_task_fields must stop at the first blank line (before BRAIN_DUMP section)."""
    content = "A=1\nB=2\n\nC=3\n"
    fields = _parse_task_fields(content)
    assert fields["A"] == "1"
    assert fields["B"] == "2"
    assert "C" not in fields


def test_parse_task_fields_empty_content() -> None:
    """_parse_task_fields must return an empty dict for empty content."""
    assert _parse_task_fields("") == {}


def test_count_dump_items_counts_non_empty_lines() -> None:
    """_count_dump_items must count only lines that have non-whitespace content."""
    dump = "- Fix login\n- Add dark mode\n\n- Rate limiter\n"
    assert _count_dump_items(dump) == 3


def test_count_dump_items_empty_returns_zero() -> None:
    """_count_dump_items must return 0 for blank/empty input."""
    assert _count_dump_items("") == 0
    assert _count_dump_items("   \n\n  ") == 0


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_brain_dump_page_renders(client: TestClient) -> None:
    """GET /brain-dump must return 200 with the page title."""
    resp = client.get("/brain-dump")
    assert resp.status_code == 200
    assert "Brain Dump" in resp.text or "brain-dump" in resp.text.lower()


def test_brain_dump_recent_runs_partial_empty(client: TestClient, tmp_path: Path) -> None:
    """GET /brain-dump/recent-runs returns 200 even when the worktrees dir is empty."""
    with patch("agentception.routes.ui.brain_dump._build_recent_dumps", return_value=[]):
        resp = client.get("/brain-dump/recent-runs")
    assert resp.status_code == 200
    assert "bd-recent-runs" in resp.text


def test_brain_dump_recent_runs_shows_cards(client: TestClient) -> None:
    """GET /brain-dump/recent-runs renders a card for each recent dump."""
    fake_runs = [
        {
            "slug": "brain-dump-20260303-164033",
            "label_prefix": "q2-rewrite",
            "preview": "- Fix login bug",
            "ts": "2026-03-03 16:40",
            "batch_id": "brain-dump-20260303-164033",
            "item_count": "3",
        }
    ]
    with patch("agentception.routes.ui.brain_dump._build_recent_dumps", return_value=fake_runs):
        resp = client.get("/brain-dump/recent-runs")
    assert resp.status_code == 200
    assert "2026-03-03 16:40" in resp.text
    assert "q2-rewrite" in resp.text
    assert "Fix login bug" in resp.text
    assert "View DAG" in resp.text
    assert "Re-run" in resp.text


def test_brain_dump_dump_text_returns_dump(client: TestClient, tmp_path: Path) -> None:
    """GET /api/brain-dump/{run_id}/dump-text returns the BRAIN_DUMP section as JSON."""
    run_id = "brain-dump-20260303-164033"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    task_file = run_dir / ".agent-task"
    task_file.write_text(
        "WORKFLOW=bugs-to-issues\nBATCH_ID=brain-dump-20260303-164033\n\nBRAIN_DUMP:\n- Fix login\n- Add dark mode\n",
        encoding="utf-8",
    )

    from agentception.config import AgentCeptionSettings
    fake_settings = AgentCeptionSettings.model_construct(worktrees_dir=tmp_path)
    with patch("agentception.config.settings", fake_settings):
        resp = client.get(f"/api/brain-dump/{run_id}/dump-text")

    assert resp.status_code == 200
    data = resp.json()
    assert "dump_text" in data
    assert "Fix login" in data["dump_text"]
    assert "Add dark mode" in data["dump_text"]


def test_brain_dump_dump_text_invalid_run_id(client: TestClient) -> None:
    """GET /api/brain-dump/{run_id}/dump-text returns 400 for invalid run_id format."""
    resp = client.get("/api/brain-dump/../../etc-passwd/dump-text")
    assert resp.status_code in (400, 404)


def test_brain_dump_dump_text_wrong_prefix(client: TestClient) -> None:
    """GET /api/brain-dump/{run_id}/dump-text returns 400 when run_id doesn't start with brain-dump-."""
    resp = client.get("/api/brain-dump/issue-826/dump-text")
    assert resp.status_code == 400


def test_brain_dump_dump_text_not_found(client: TestClient, tmp_path: Path) -> None:
    """GET /api/brain-dump/{run_id}/dump-text returns 404 when the worktree doesn't exist."""
    fake_settings = AgentCeptionSettings.model_construct(worktrees_dir=tmp_path)
    with patch("agentception.config.settings", fake_settings):
        resp = client.get("/api/brain-dump/brain-dump-99991231-999999/dump-text")
    assert resp.status_code == 404
