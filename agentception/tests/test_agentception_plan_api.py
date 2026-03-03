"""Tests for POST /api/plan/draft (issue #872).

Covers:
- Valid dump returns 200 with status=pending and a uuid4 draft_id.
- Empty dump returns 422.
- Whitespace-only dump returns 422.
- After a valid POST the .agent-task file is written with WORKFLOW=plan-spec
  and the dump text.
- asyncio.create_subprocess_exec is called with ``git worktree add``.

All git subprocess calls are mocked so these tests do not require a live git
repository or any filesystem writes outside a tmp_path fixture.

Boundary: zero imports from maestro/, muse/, kly/, or storpheus/.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agentception.app import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proc_mock(returncode: int = 0, stderr: bytes = b"") -> MagicMock:
    """Return a mock that behaves like an asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    return proc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Async httpx client wrapping the FastAPI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_post_valid_dump_returns_200_pending(
    async_client: AsyncClient,
    tmp_path: Path,
) -> None:
    """POST with a valid dump string must return 200 and status='pending'."""
    proc_mock = _make_proc_mock(returncode=0)

    with (
        patch(
            "agentception.routes.api.plan.asyncio.create_subprocess_exec",
            return_value=proc_mock,
        ) as mock_exec,
        patch(
            "agentception.routes.api.plan._WORKTREES_BASE",
            tmp_path,
        ),
    ):
        response = await async_client.post(
            "/api/plan/draft",
            json={"dump": "I want a song about mountains"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["draft_id"]
    # draft_id must be a valid uuid4
    parsed = uuid.UUID(body["draft_id"], version=4)
    assert str(parsed) == body["draft_id"]
    # output_path must be a specific file (not the directory) so the poller
    # can watch for it and emit task_output_ready when it appears.
    assert "plan-draft-" in body["output_path"]
    assert body["output_path"].endswith(".plan-output.yaml")
    assert body["task_file"].endswith(".agent-task")
    mock_exec.assert_called_once()


@pytest.mark.anyio
async def test_agent_task_written_with_workflow_plan_spec(
    async_client: AsyncClient,
    tmp_path: Path,
) -> None:
    """After a valid POST the .agent-task must contain WORKFLOW=plan-spec and the dump."""
    dump_text = "Build a calm lo-fi track with piano and soft drums"
    proc_mock = _make_proc_mock(returncode=0)

    with (
        patch(
            "agentception.routes.api.plan.asyncio.create_subprocess_exec",
            return_value=proc_mock,
        ),
        patch(
            "agentception.routes.api.plan._WORKTREES_BASE",
            tmp_path,
        ),
    ):
        response = await async_client.post(
            "/api/plan/draft",
            json={"dump": dump_text},
        )

    assert response.status_code == 200
    body = response.json()
    task_file = Path(body["task_file"])
    assert task_file.exists(), ".agent-task file was not created"

    content = task_file.read_text(encoding="utf-8")
    assert "WORKFLOW=plan-spec" in content
    assert dump_text in content
    # Output path must be a specific file so the AgentCeption poller can watch
    # for it; the mcp_tools_hint guides the Cursor agent to call plan_get_schema.
    assert ".plan-output.yaml" in content
    assert "mcp_tools_hint=call plan_get_schema()" in content
    assert "output_schema=plan_get_schema" in content


@pytest.mark.anyio
async def test_git_worktree_add_called(
    async_client: AsyncClient,
    tmp_path: Path,
) -> None:
    """POST must call asyncio.create_subprocess_exec with 'git worktree add'."""
    proc_mock = _make_proc_mock(returncode=0)

    with (
        patch(
            "agentception.routes.api.plan.asyncio.create_subprocess_exec",
            return_value=proc_mock,
        ) as mock_exec,
        patch(
            "agentception.routes.api.plan._WORKTREES_BASE",
            tmp_path,
        ),
    ):
        response = await async_client.post(
            "/api/plan/draft",
            json={"dump": "Any valid dump text"},
        )

    assert response.status_code == 200
    # Verify the subprocess was called with the git worktree add arguments
    call_args = mock_exec.call_args
    assert call_args is not None
    args = call_args[0]
    assert args[0] == "git"
    assert args[1] == "worktree"
    assert args[2] == "add"
    # 4th arg is the worktree path
    assert "plan-draft-" in args[3]


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_post_empty_dump_returns_422(async_client: AsyncClient) -> None:
    """POST with an empty dump string must return 422."""
    response = await async_client.post(
        "/api/plan/draft",
        json={"dump": ""},
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_post_whitespace_dump_returns_422(async_client: AsyncClient) -> None:
    """POST with a whitespace-only dump string must return 422."""
    response = await async_client.post(
        "/api/plan/draft",
        json={"dump": "   \t\n  "},
    )
    assert response.status_code == 422
