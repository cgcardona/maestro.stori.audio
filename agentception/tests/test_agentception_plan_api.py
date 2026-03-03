"""Tests for POST /api/plan/draft (issue #872) and POST /api/plan/launch (issue #873).

POST /api/plan/draft covers:
- Valid dump returns 200 with status=pending and a uuid4 draft_id.
- Empty dump returns 422.
- Whitespace-only dump returns 422.
- After a valid POST the .agent-task file is written with WORKFLOW=plan-spec
  and the dump text.
- asyncio.create_subprocess_exec is called with ``git worktree add``.

POST /api/plan/launch covers:
- Valid YAML manifest → 200 with worktree, branch, agent_task_path, batch_id.
- Malformed YAML (syntax error) → 422 with error detail.
- YAML with cyclic depends_on → 422 with cycle description.
- plan_spawn_coordinator called with correct manifest JSON.

All git subprocess calls and plan_spawn_coordinator are mocked so these tests
do not require a live git repository or network access.

Boundary: zero imports from maestro/, muse/, kly/, or storpheus/.
"""
from __future__ import annotations

import json
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
    assert "plan-draft-" in body["output_path"]
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


# ---------------------------------------------------------------------------
# POST /api/plan/launch — issue #873
# ---------------------------------------------------------------------------

_VALID_YAML = """\
batch_id: plan-p2-20260303
issues:
  - number: 870
    title: "Dump spec to file"
    depends_on: []
  - number: 871
    title: "Enrich spec"
    depends_on: [870]
  - number: 872
    title: "POST /api/plan/draft"
    depends_on: [871]
  - number: 873
    title: "POST /api/plan/launch"
    depends_on: [872]
"""

_SPAWN_RESULT = {
    "worktree": "/tmp/worktrees/coordinator-20260303-142201",
    "branch": "coordinator/20260303-142201",
    "agent_task_path": "/tmp/worktrees/coordinator-20260303-142201/.agent-task",
    "batch_id": "coordinator-20260303-142201",
}


@pytest.mark.anyio
async def test_post_valid_yaml_returns_200_with_worktree(
    async_client: AsyncClient,
) -> None:
    """POST a valid YAML manifest → 200 with worktree, branch, agent_task_path, batch_id."""
    with patch(
        "agentception.routes.api.plan.plan_spawn_coordinator",
        new=AsyncMock(return_value=_SPAWN_RESULT),
    ):
        response = await async_client.post(
            "/api/plan/launch",
            json={"yaml_text": _VALID_YAML},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["worktree"] == _SPAWN_RESULT["worktree"]
    assert body["branch"] == _SPAWN_RESULT["branch"]
    assert body["agent_task_path"] == _SPAWN_RESULT["agent_task_path"]
    assert body["batch_id"] == _SPAWN_RESULT["batch_id"]


@pytest.mark.anyio
async def test_post_malformed_yaml_returns_422(async_client: AsyncClient) -> None:
    """POST a malformed YAML string (unclosed sequence) → 422 with error detail."""
    with patch(
        "agentception.routes.api.plan.plan_spawn_coordinator",
        new=AsyncMock(return_value=_SPAWN_RESULT),
    ):
        response = await async_client.post(
            "/api/plan/launch",
            json={"yaml_text": "key: [unclosed"},
        )

    assert response.status_code == 422
    body = response.json()
    assert "YAML" in body["detail"] or "yaml" in body["detail"].lower()


@pytest.mark.anyio
async def test_post_yaml_with_cyclic_deps_returns_422(async_client: AsyncClient) -> None:
    """POST YAML where issue A depends on B and B depends on A → 422 with cycle description."""
    cyclic_yaml = """\
batch_id: cyclic-test
issues:
  - number: 1
    title: "Issue A"
    depends_on: [2]
  - number: 2
    title: "Issue B"
    depends_on: [1]
"""
    with patch(
        "agentception.routes.api.plan.plan_spawn_coordinator",
        new=AsyncMock(return_value=_SPAWN_RESULT),
    ):
        response = await async_client.post(
            "/api/plan/launch",
            json={"yaml_text": cyclic_yaml},
        )

    assert response.status_code == 422
    body = response.json()
    assert "Cycle" in body["detail"] or "cycle" in body["detail"].lower()


@pytest.mark.anyio
async def test_plan_spawn_coordinator_called_with_correct_manifest(
    async_client: AsyncClient,
) -> None:
    """plan_spawn_coordinator must be called with the serialised PlanSpec JSON."""
    spawn_mock = AsyncMock(return_value=_SPAWN_RESULT)

    with patch(
        "agentception.routes.api.plan.plan_spawn_coordinator",
        new=spawn_mock,
    ):
        response = await async_client.post(
            "/api/plan/launch",
            json={"yaml_text": _VALID_YAML},
        )

    assert response.status_code == 200
    spawn_mock.assert_called_once()

    # Verify the argument is valid JSON containing the batch_id and issues
    call_args = spawn_mock.call_args
    assert call_args is not None
    manifest_json_arg: str = call_args[0][0]
    parsed = json.loads(manifest_json_arg)
    assert parsed["batch_id"] == "plan-p2-20260303"
    issue_numbers = [i["number"] for i in parsed["issues"]]
    assert issue_numbers == [870, 871, 872, 873]
