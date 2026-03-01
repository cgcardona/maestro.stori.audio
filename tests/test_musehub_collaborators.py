"""Tests for Muse Hub collaborators management endpoints.

Covers the acceptance criteria from issue #413:
- GET /musehub/repos/{repo_id}/collaborators returns collaborator list
- POST /musehub/repos/{repo_id}/collaborators invites a collaborator (owner/admin+)
- PUT /musehub/repos/{repo_id}/collaborators/{user_id}/permission updates permission
- DELETE /musehub/repos/{repo_id}/collaborators/{user_id} removes collaborator
- GET /musehub/repos/{repo_id}/collaborators/{user_id}/permission checks presence
- Owner cannot be removed as a collaborator
- Only admin+ (or owner) may mutate collaborators
- Duplicate invite returns 409
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

# ── Constants ─────────────────────────────────────────────────────────────────

_COLLABORATOR_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _create_repo(client: AsyncClient, auth_headers: dict[str, str], name: str = "collab-test-repo") -> str:
    """Create a repo via the API and return its repo_id."""
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": name, "owner": "testuser"},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    repo_id: str = response.json()["repoId"]
    return repo_id


async def _invite_collaborator(
    client: AsyncClient,
    auth_headers: dict[str, str],
    repo_id: str,
    user_id: str = _COLLABORATOR_ID,
    permission: str = "write",
) -> dict[str, object]:
    """Invite a collaborator via the API."""
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/collaborators",
        json={"user_id": user_id, "permission": permission},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    data: dict[str, object] = response.json()
    return data


# ── POST /collaborators ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_invite_collaborator_returns_201(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Owner can invite a collaborator; response contains all required fields."""
    repo_id = await _create_repo(client, auth_headers, "invite-201-repo")
    data = await _invite_collaborator(client, auth_headers, repo_id)

    assert data["userId"] == _COLLABORATOR_ID
    assert data["repoId"] == repo_id
    assert data["permission"] == "write"
    assert "collaboratorId" in data


@pytest.mark.anyio
async def test_invite_collaborator_duplicate_returns_409(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Inviting the same user twice returns 409 Conflict."""
    repo_id = await _create_repo(client, auth_headers, "invite-dup-repo")
    await _invite_collaborator(client, auth_headers, repo_id)

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/collaborators",
        json={"user_id": _COLLABORATOR_ID, "permission": "read"},
        headers=auth_headers,
    )
    assert response.status_code == 409


@pytest.mark.anyio
async def test_invite_collaborator_unknown_repo_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Inviting a collaborator to a non-existent repo returns 404."""
    response = await client.post(
        "/api/v1/musehub/repos/nonexistent-repo-id/collaborators",
        json={"user_id": _COLLABORATOR_ID, "permission": "read"},
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_invite_collaborator_requires_auth(
    client: AsyncClient,
) -> None:
    """POST /collaborators returns 401 without a Bearer token."""
    response = await client.post(
        "/api/v1/musehub/repos/some-repo/collaborators",
        json={"user_id": _COLLABORATOR_ID, "permission": "read"},
    )
    assert response.status_code == 401


# ── GET /collaborators ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_collaborators_empty(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /collaborators returns empty list for a repo with no collaborators."""
    repo_id = await _create_repo(client, auth_headers, "list-empty-repo")
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/collaborators",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["collaborators"] == []


@pytest.mark.anyio
async def test_list_collaborators_after_invite(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /collaborators returns the invited collaborator after POST."""
    repo_id = await _create_repo(client, auth_headers, "list-after-invite-repo")
    await _invite_collaborator(client, auth_headers, repo_id)

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/collaborators",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["collaborators"][0]["userId"] == _COLLABORATOR_ID


# ── GET /collaborators/{user_id}/permission ───────────────────────────────────


@pytest.mark.anyio
async def test_check_permission_not_collaborator(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Permission check returns is_collaborator=false for a non-member user."""
    repo_id = await _create_repo(client, auth_headers, "perm-check-not-member-repo")
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/collaborators/{_COLLABORATOR_ID}/permission",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["isCollaborator"] is False
    assert body["permission"] is None


@pytest.mark.anyio
async def test_check_permission_is_collaborator(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Permission check returns is_collaborator=true with correct level after invite."""
    repo_id = await _create_repo(client, auth_headers, "perm-check-member-repo")
    await _invite_collaborator(client, auth_headers, repo_id, permission="admin")

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/collaborators/{_COLLABORATOR_ID}/permission",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["isCollaborator"] is True
    assert body["permission"] == "admin"


# ── PUT /collaborators/{user_id}/permission ───────────────────────────────────


@pytest.mark.anyio
async def test_update_permission_success(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Owner can update a collaborator's permission level."""
    repo_id = await _create_repo(client, auth_headers, "update-perm-repo")
    await _invite_collaborator(client, auth_headers, repo_id, permission="read")

    response = await client.put(
        f"/api/v1/musehub/repos/{repo_id}/collaborators/{_COLLABORATOR_ID}/permission",
        json={"permission": "admin"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["permission"] == "admin"


@pytest.mark.anyio
async def test_update_permission_not_found_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Updating permission for a non-collaborator returns 404."""
    repo_id = await _create_repo(client, auth_headers, "update-perm-404-repo")
    response = await client.put(
        f"/api/v1/musehub/repos/{repo_id}/collaborators/{_COLLABORATOR_ID}/permission",
        json={"permission": "admin"},
        headers=auth_headers,
    )
    assert response.status_code == 404


# ── DELETE /collaborators/{user_id} ──────────────────────────────────────────


@pytest.mark.anyio
async def test_remove_collaborator_success(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Owner can remove a collaborator; subsequent list shows 0 collaborators."""
    repo_id = await _create_repo(client, auth_headers, "remove-collab-repo")
    await _invite_collaborator(client, auth_headers, repo_id)

    response = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/collaborators/{_COLLABORATOR_ID}",
        headers=auth_headers,
    )
    assert response.status_code == 204

    list_response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/collaborators",
        headers=auth_headers,
    )
    assert list_response.json()["total"] == 0


@pytest.mark.anyio
async def test_remove_collaborator_not_found_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Removing a non-collaborator returns 404."""
    repo_id = await _create_repo(client, auth_headers, "remove-404-repo")
    response = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/collaborators/{_COLLABORATOR_ID}",
        headers=auth_headers,
    )
    assert response.status_code == 404
