"""Tests for the manual spawn endpoint and issue-picker UI (AC-103).

Covers POST /api/control/spawn and GET /control/spawn.
All GitHub calls and git operations are mocked — no live network, no
filesystem side-effects.

Run targeted:
    pytest agentception/tests/test_agentception_spawn.py -v
"""
from __future__ import annotations

import asyncio
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agentception.app import app
from agentception.models import SpawnRequest, VALID_ROLES


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    """Synchronous test client with full lifespan."""
    with TestClient(app) as c:
        yield c


# ── Helper: build a fake open issue dict ──────────────────────────────────────

def _open_issue(
    number: int,
    title: str = "Test issue",
    labels: list[str] | None = None,
) -> dict[str, object]:
    """Return a minimal open-issue dict as returned by get_issue()."""
    return {
        "number": number,
        "state": "OPEN",
        "title": title,
        "labels": labels or [],
    }


def _open_issue_list(
    number: int,
    title: str = "Test issue",
    label_names: list[str] | None = None,
) -> dict[str, object]:
    """Return a minimal open-issue dict as returned by get_open_issues()."""
    label_objs: list[object] = [
        {"name": name} for name in (label_names or [])
    ]
    return {
        "number": number,
        "title": title,
        "labels": label_objs,
        "body": "",
    }


# ── POST /api/control/spawn — success ─────────────────────────────────────────


def test_spawn_creates_worktree_and_task_file(
    client: TestClient, tmp_path: Path
) -> None:
    """POST /api/control/spawn must create a worktree and return SpawnResult on success."""
    worktree_dir = tmp_path / "worktrees" / "maestro"
    worktree_dir.mkdir(parents=True)
    # Simulate what `git worktree add` would do: create the directory.
    expected_worktree = worktree_dir / "issue-42"

    proc_mock = MagicMock()
    proc_mock.returncode = 0

    async def _fake_communicate() -> tuple[bytes, bytes]:
        # Simulate git creating the worktree directory on communicate().
        expected_worktree.mkdir(parents=True, exist_ok=True)
        return (b"", b"")

    proc_mock.communicate = _fake_communicate

    async def _fake_exec(*args: object, **kwargs: object) -> MagicMock:
        return proc_mock

    with (
        patch(
            "agentception.routes.api.get_issue",
            return_value=_open_issue(42, "Fix the thing"),
        ),
        patch("agentception.routes.api.add_wip_label", new_callable=AsyncMock),
        patch(
            "agentception.routes.api.settings.worktrees_dir",
            worktree_dir,
        ),
        patch(
            "agentception.routes.api.settings.repo_dir",
            Path("/fake/repo"),
        ),
        patch(
            "asyncio.create_subprocess_exec",
            side_effect=_fake_exec,
        ),
    ):
        response = client.post(
            "/api/control/spawn",
            json={"issue_number": 42, "role": "python-developer"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["spawned"] == 42
    assert "issue-42" in data["worktree"]
    assert data["branch"] == "feat/issue-42"
    assert "ISSUE_NUMBER=42" in data["agent_task"]
    assert "BRANCH=feat/issue-42" in data["agent_task"]
    assert "ROLE=python-developer" in data["agent_task"]
    # Verify the .agent-task file was actually written to disk.
    task_file = expected_worktree / ".agent-task"
    assert task_file.exists()
    assert "ISSUE_NUMBER=42" in task_file.read_text()


# ── POST /api/control/spawn — already claimed → 409 ──────────────────────────


def test_spawn_already_claimed_returns_409(client: TestClient) -> None:
    """POST /api/control/spawn must return 409 when the issue already has agent:wip."""
    with patch(
        "agentception.routes.api.get_issue",
        return_value=_open_issue(42, "Fix the thing", labels=["agent:wip", "enhancement"]),
    ):
        response = client.post(
            "/api/control/spawn",
            json={"issue_number": 42},
        )

    assert response.status_code == 409
    assert "already claimed" in response.json()["detail"]


# ── POST /api/control/spawn — issue not found → 404 ──────────────────────────


def test_spawn_invalid_issue_returns_404(client: TestClient) -> None:
    """POST /api/control/spawn must return 404 when gh cannot find the issue."""
    with patch(
        "agentception.routes.api.get_issue",
        side_effect=RuntimeError("issue not found"),
    ):
        response = client.post(
            "/api/control/spawn",
            json={"issue_number": 99999},
        )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_spawn_closed_issue_returns_404(client: TestClient) -> None:
    """POST /api/control/spawn must return 404 when the issue is closed."""
    closed = _open_issue(42)
    closed["state"] = "CLOSED"

    with patch("agentception.routes.api.get_issue", return_value=closed):
        response = client.post(
            "/api/control/spawn",
            json={"issue_number": 42},
        )

    assert response.status_code == 404
    assert "not open" in response.json()["detail"]


# ── POST /api/control/spawn — invalid role → 422 ─────────────────────────────


def test_spawn_invalid_role_returns_422(client: TestClient) -> None:
    """POST /api/control/spawn must return 422 for an unrecognised role."""
    response = client.post(
        "/api/control/spawn",
        json={"issue_number": 42, "role": "chaos-monkey"},
    )
    assert response.status_code == 422


# ── POST /api/control/spawn — worktree already exists → 409 ──────────────────


def test_spawn_existing_worktree_returns_409(
    client: TestClient, tmp_path: Path
) -> None:
    """POST /api/control/spawn must return 409 when the worktree directory already exists."""
    worktrees = tmp_path / "worktrees" / "maestro"
    # Pre-create the issue worktree so the endpoint sees it exists
    existing = worktrees / "issue-42"
    existing.mkdir(parents=True)

    with (
        patch(
            "agentception.routes.api.get_issue",
            return_value=_open_issue(42),
        ),
        patch("agentception.routes.api.add_wip_label", new_callable=AsyncMock),
        patch("agentception.routes.api.settings.worktrees_dir", worktrees),
    ):
        response = client.post(
            "/api/control/spawn",
            json={"issue_number": 42},
        )

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


# ── GET /control/spawn — form renders ────────────────────────────────────────


def test_spawn_form_renders_issue_list(client: TestClient) -> None:
    """GET /control/spawn must render a form containing open, unclaimed issues."""
    fake_issues = [
        _open_issue_list(100, "Issue Alpha"),
        _open_issue_list(101, "Issue Beta"),
        _open_issue_list(102, "Issue Gamma — already claimed", ["agent:wip"]),
    ]

    with patch(
        "agentception.routes.ui.get_open_issues",
        return_value=fake_issues,
    ):
        response = client.get("/control/spawn")

    assert response.status_code == 200
    html = response.text
    assert "#100" in html
    assert "Issue Alpha" in html
    assert "#101" in html
    assert "Issue Beta" in html
    # Claimed issue must NOT appear in the picker
    assert "#102" not in html
    assert "Issue Gamma" not in html


def test_spawn_form_renders_role_options(client: TestClient) -> None:
    """GET /control/spawn form must include all valid role options."""
    with patch("agentception.routes.ui.get_open_issues", return_value=[]):
        response = client.get("/control/spawn")

    assert response.status_code == 200
    html = response.text
    for role in VALID_ROLES:
        assert role in html


def test_spawn_form_renders_empty_state_gracefully(client: TestClient) -> None:
    """GET /control/spawn must render without error when there are no unclaimed issues."""
    with patch("agentception.routes.ui.get_open_issues", return_value=[]):
        response = client.get("/control/spawn")

    assert response.status_code == 200
    assert "AgentCeption" in response.text


# ── SpawnRequest model validation ─────────────────────────────────────────────


def test_spawn_request_default_role() -> None:
    """SpawnRequest must default the role to python-developer."""
    req = SpawnRequest(issue_number=1)
    assert req.role == "python-developer"


def test_spawn_request_accepts_all_valid_roles() -> None:
    """SpawnRequest must accept every role in VALID_ROLES."""
    for role in VALID_ROLES:
        req = SpawnRequest(issue_number=1, role=role)
        assert req.role == role


def test_spawn_request_rejects_unknown_role() -> None:
    """SpawnRequest must raise ValueError for an unrecognised role."""
    with pytest.raises(ValueError):
        SpawnRequest(issue_number=1, role="hacker")
