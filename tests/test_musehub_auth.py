"""Auth guard tests for all Muse Hub routes.

Verifies that every ``/musehub/`` endpoint rejects unauthenticated requests
with HTTP 401 — the router-level ``Depends(require_valid_token)`` dependency
is the sole mechanism; individual endpoints need not repeat it.

Covers acceptance criterion from issue #47:
- All /musehub/ routes reject unauthenticated requests with 401.

All tests use the shared ``client`` fixture from conftest.py.
No auth headers are sent — every request must return 401.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# POST /musehub/repos
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hub_routes_require_auth_create_repo(client: AsyncClient) -> None:
    """POST /musehub/repos returns 401 without a Bearer token."""
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "beats", "owner": "testuser"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hub_routes_require_auth_get_repo(client: AsyncClient) -> None:
    """GET /musehub/repos/{repo_id} returns 401 without a Bearer token."""
    response = await client.get("/api/v1/musehub/repos/any-repo-id")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/branches
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hub_routes_require_auth_list_branches(client: AsyncClient) -> None:
    """GET /musehub/repos/{repo_id}/branches returns 401 without a Bearer token."""
    response = await client.get("/api/v1/musehub/repos/any-repo-id/branches")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/commits
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hub_routes_require_auth_list_commits(client: AsyncClient) -> None:
    """GET /musehub/repos/{repo_id}/commits returns 401 without a Bearer token."""
    response = await client.get("/api/v1/musehub/repos/any-repo-id/commits")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /musehub/repos/{repo_id}/issues
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hub_routes_require_auth_create_issue(client: AsyncClient) -> None:
    """POST /musehub/repos/{repo_id}/issues returns 401 without a Bearer token."""
    response = await client.post(
        "/api/v1/musehub/repos/any-repo-id/issues",
        json={"title": "Bug report"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/issues
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hub_routes_require_auth_list_issues(client: AsyncClient) -> None:
    """GET /musehub/repos/{repo_id}/issues returns 401 without a Bearer token."""
    response = await client.get("/api/v1/musehub/repos/any-repo-id/issues")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/issues/{issue_number}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hub_routes_require_auth_get_issue(client: AsyncClient) -> None:
    """GET /musehub/repos/{repo_id}/issues/{n} returns 401 without a Bearer token."""
    response = await client.get("/api/v1/musehub/repos/any-repo-id/issues/1")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /musehub/repos/{repo_id}/issues/{issue_number}/close
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hub_routes_require_auth_close_issue(client: AsyncClient) -> None:
    """POST /musehub/repos/{repo_id}/issues/{n}/close returns 401 without a Bearer token."""
    response = await client.post("/api/v1/musehub/repos/any-repo-id/issues/1/close")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Sanity check — authenticated requests are NOT blocked
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hub_routes_accept_valid_token(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /musehub/repos succeeds (201) with a valid Bearer token.

    Ensures the auth dependency passes through valid tokens — guards against
    accidentally blocking all traffic.
    """
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "auth-sanity-repo", "owner": "testuser"},
        headers=auth_headers,
    )
    assert response.status_code == 201
