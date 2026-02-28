"""Auth guard tests for Muse Hub routes.

Auth model (updated in Phase 0–4 UX overhaul):
- GET endpoints use ``optional_token`` — public repos are accessible
  unauthenticated; private repos return 401.
- POST / DELETE / write endpoints always use ``require_valid_token``.
- Non-existent repos return 404 regardless of auth status (no auth
  pre-filter that exposes 401 before a DB lookup for GET routes).

Covers:
- Write endpoints (POST/DELETE) always return 401 without a token.
- GET endpoints return 404 (not 401) for non-existent repos without a token,
  because the auth check is deferred to the visibility guard.
- GET endpoints return 401 for real private repos without a token.
- Valid tokens are accepted on write endpoints.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# Write endpoints — always require auth (401 without token)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hub_routes_require_auth_create_repo(client: AsyncClient) -> None:
    """POST /musehub/repos returns 401 without a Bearer token."""
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "beats", "owner": "testuser"},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_hub_routes_require_auth_create_issue(client: AsyncClient) -> None:
    """POST /musehub/repos/{id}/issues returns 401 without a Bearer token."""
    response = await client.post(
        "/api/v1/musehub/repos/any-repo-id/issues",
        json={"title": "Bug report"},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_hub_routes_require_auth_close_issue(client: AsyncClient) -> None:
    """POST /musehub/repos/{id}/issues/{n}/close returns 401 without a Bearer token."""
    response = await client.post("/api/v1/musehub/repos/any-repo-id/issues/1/close")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET endpoints — non-existent repos return 404 (not 401) without token
#
# Rationale: optional_token + visibility guard — unauthenticated requests
# reach the DB; a non-existent repo returns 404 before the auth check fires.
# The old test expectation of 401 was incorrect for the new auth model.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hub_get_nonexistent_repo_returns_404_without_auth(
    client: AsyncClient,
) -> None:
    """GET /musehub/repos/{id} returns 404 for a non-existent repo without auth.

    The auth check is now visibility-based: the DB lookup happens first,
    so a missing repo returns 404 regardless of auth status.
    """
    response = await client.get("/api/v1/musehub/repos/non-existent-repo-id")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_hub_get_nonexistent_branches_returns_404_without_auth(
    client: AsyncClient,
) -> None:
    """GET /musehub/repos/{id}/branches returns 404 for a non-existent repo without auth."""
    response = await client.get("/api/v1/musehub/repos/non-existent-repo-id/branches")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_hub_get_nonexistent_commits_returns_404_without_auth(
    client: AsyncClient,
) -> None:
    """GET /musehub/repos/{id}/commits returns 404 for a non-existent repo without auth."""
    response = await client.get("/api/v1/musehub/repos/non-existent-repo-id/commits")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_hub_get_nonexistent_issues_returns_404_without_auth(
    client: AsyncClient,
) -> None:
    """GET /musehub/repos/{id}/issues returns 404 for a non-existent repo without auth."""
    response = await client.get("/api/v1/musehub/repos/non-existent-repo-id/issues")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_hub_get_nonexistent_issue_returns_404_without_auth(
    client: AsyncClient,
) -> None:
    """GET /musehub/repos/{id}/issues/1 returns 404 for a non-existent repo without auth."""
    response = await client.get("/api/v1/musehub/repos/non-existent-repo-id/issues/1")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Private repo visibility — GET returns 401 for private repos without token
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_private_repo_returns_401_without_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /musehub/repos/{id} returns 401 for a private repo without a token.

    Creates a private repo, then verifies unauthenticated access returns 401.
    """
    # Create a private repo
    create_resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "private-auth-test", "owner": "authtest", "visibility": "private"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    repo_id = create_resp.json()["repoId"]

    # Unauthenticated access should return 401
    unauth_resp = await client.get(f"/api/v1/musehub/repos/{repo_id}")
    assert unauth_resp.status_code == 401, (
        f"Expected 401 for private repo, got {unauth_resp.status_code}"
    )


@pytest.mark.anyio
async def test_public_repo_accessible_without_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /musehub/repos/{id} returns 200 for a public repo without a token.

    Verifies the new auth model: public repos are browseable anonymously.
    """
    # Create a public repo
    create_resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "public-auth-test", "owner": "authtest", "visibility": "public"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    repo_id = create_resp.json()["repoId"]

    # Unauthenticated access should return 200
    unauth_resp = await client.get(f"/api/v1/musehub/repos/{repo_id}")
    assert unauth_resp.status_code == 200, (
        f"Expected 200 for public repo, got {unauth_resp.status_code}: {unauth_resp.text}"
    )


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
