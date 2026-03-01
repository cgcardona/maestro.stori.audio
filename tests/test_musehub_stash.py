"""Tests for Muse Hub stash endpoints (musehub/stash.py).

Covers all 6 endpoints introduced in PR #467:
  - list_stash:   GET  /repos/{repo_id}/stash  (paginated, user-scoped)
  - push_stash:   POST /repos/{repo_id}/stash
  - get_stash:    GET  /repos/{repo_id}/stash/{stash_id}
  - pop_stash:    POST /repos/{repo_id}/stash/{stash_id}/pop
  - apply_stash:  POST /repos/{repo_id}/stash/{stash_id}/apply
  - drop_stash:   DELETE /repos/{repo_id}/stash/{stash_id}

Key invariants asserted:
  - Stash entries are user-scoped: user A cannot see user B's stash
  - pop removes the stash row atomically (deleted=True in response)
  - apply leaves the stash row intact (deleted=False in response)
  - 404 is returned for stash_id not owned by caller
  - Pagination works: total and page fields are correct
  - All write endpoints require auth (401 without token)
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

_TEST_REPO_ID = str(uuid.uuid4())
_BASE = f"/api/v1/musehub/repos/{_TEST_REPO_ID}/stash"

_PUSH_BODY = {
    "message": "WIP: bridge section",
    "branch": "feat/bridge",
    "entries": [
        {"path": "tracks/piano.mid", "object_id": "sha256:aabbcc"},
        {"path": "tracks/bass.mid", "object_id": "sha256:ddeeff"},
    ],
}


# ---------------------------------------------------------------------------
# push_stash — POST /repos/{repo_id}/stash
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_push_stash_creates_stash_with_entries(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Push creates a stash record and returns it with its entries."""
    resp = await client.post(_BASE, json=_PUSH_BODY, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["branch"] == "feat/bridge"
    assert data["message"] == "WIP: bridge section"
    assert len(data["entries"]) == 2
    paths = {e["path"] for e in data["entries"]}
    assert paths == {"tracks/piano.mid", "tracks/bass.mid"}


@pytest.mark.anyio
async def test_push_stash_requires_auth(client: AsyncClient) -> None:
    """Pushing a stash without a token returns 401."""
    resp = await client.post(_BASE, json=_PUSH_BODY)
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_push_stash_empty_entries(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Push with no entries creates a stash with an empty entries list."""
    body = {"message": "empty stash", "branch": "main", "entries": []}
    resp = await client.post(_BASE, json=body, headers=auth_headers)
    assert resp.status_code == 201
    assert resp.json()["entries"] == []


# ---------------------------------------------------------------------------
# list_stash — GET /repos/{repo_id}/stash
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_stash_returns_only_caller_entries(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """List returns a paginated result with total and page metadata."""
    await client.post(_BASE, json=_PUSH_BODY, headers=auth_headers)
    await client.post(
        _BASE, json={**_PUSH_BODY, "message": "stash 2"}, headers=auth_headers
    )

    resp = await client.get(_BASE, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["page"] == 1
    assert len(data["items"]) == 2


@pytest.mark.anyio
async def test_list_stash_pagination(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Pagination parameters are respected: page_size limits results."""
    for i in range(3):
        await client.post(
            _BASE, json={**_PUSH_BODY, "message": f"stash {i}"}, headers=auth_headers
        )

    resp = await client.get(_BASE, params={"page": 1, "page_size": 2}, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2
    assert data["page_size"] == 2


@pytest.mark.anyio
async def test_list_stash_requires_auth(client: AsyncClient) -> None:
    """Listing stash without a token returns 401."""
    resp = await client.get(_BASE)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# get_stash — GET /repos/{repo_id}/stash/{stash_id}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_stash_returns_detail_with_entries(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """get_stash returns the stash row along with its file entries."""
    push_resp = await client.post(_BASE, json=_PUSH_BODY, headers=auth_headers)
    stash_id = push_resp.json()["id"]

    resp = await client.get(f"{_BASE}/{stash_id}", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == stash_id
    assert len(data["entries"]) == 2


@pytest.mark.anyio
async def test_get_stash_404_for_unknown_id(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """get_stash returns 404 for a stash_id that does not exist."""
    resp = await client.get(f"{_BASE}/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_stash_requires_auth(client: AsyncClient) -> None:
    """get_stash without a token returns 401."""
    resp = await client.get(f"{_BASE}/{uuid.uuid4()}")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# pop_stash — POST /repos/{repo_id}/stash/{stash_id}/pop
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pop_stash_returns_entries_and_deletes_stash(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """pop returns the stash entries and removes the stash (deleted=True)."""
    push_resp = await client.post(_BASE, json=_PUSH_BODY, headers=auth_headers)
    stash_id = push_resp.json()["id"]

    pop_resp = await client.post(f"{_BASE}/{stash_id}/pop", headers=auth_headers)
    assert pop_resp.status_code == 200
    data = pop_resp.json()
    assert data["deleted"] is True
    assert len(data["entries"]) == 2

    # Stash should be gone now
    get_resp = await client.get(f"{_BASE}/{stash_id}", headers=auth_headers)
    assert get_resp.status_code == 404


@pytest.mark.anyio
async def test_pop_stash_404_for_unknown_id(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """pop returns 404 for a stash_id not owned by caller."""
    resp = await client.post(f"{_BASE}/{uuid.uuid4()}/pop", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_pop_stash_requires_auth(client: AsyncClient) -> None:
    """pop without a token returns 401."""
    resp = await client.post(f"{_BASE}/{uuid.uuid4()}/pop")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# apply_stash — POST /repos/{repo_id}/stash/{stash_id}/apply
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_apply_stash_returns_entries_and_keeps_stash(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """apply returns stash entries (deleted=False) and leaves the stash intact."""
    push_resp = await client.post(_BASE, json=_PUSH_BODY, headers=auth_headers)
    stash_id = push_resp.json()["id"]

    apply_resp = await client.post(f"{_BASE}/{stash_id}/apply", headers=auth_headers)
    assert apply_resp.status_code == 200
    data = apply_resp.json()
    assert data["deleted"] is False
    assert len(data["entries"]) == 2

    # Stash should still exist
    get_resp = await client.get(f"{_BASE}/{stash_id}", headers=auth_headers)
    assert get_resp.status_code == 200


@pytest.mark.anyio
async def test_apply_stash_404_for_unknown_id(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """apply returns 404 for a stash_id not owned by caller."""
    resp = await client.post(f"{_BASE}/{uuid.uuid4()}/apply", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_apply_stash_requires_auth(client: AsyncClient) -> None:
    """apply without a token returns 401."""
    resp = await client.post(f"{_BASE}/{uuid.uuid4()}/apply")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# drop_stash — DELETE /repos/{repo_id}/stash/{stash_id}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_drop_stash_deletes_stash_without_applying(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """drop permanently removes the stash entry (204 No Content)."""
    push_resp = await client.post(_BASE, json=_PUSH_BODY, headers=auth_headers)
    stash_id = push_resp.json()["id"]

    drop_resp = await client.delete(f"{_BASE}/{stash_id}", headers=auth_headers)
    assert drop_resp.status_code == 204

    get_resp = await client.get(f"{_BASE}/{stash_id}", headers=auth_headers)
    assert get_resp.status_code == 404


@pytest.mark.anyio
async def test_drop_stash_404_for_unknown_id(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """drop returns 404 for a stash_id not owned by caller."""
    resp = await client.delete(f"{_BASE}/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_drop_stash_requires_auth(client: AsyncClient) -> None:
    """drop without a token returns 401."""
    resp = await client.delete(f"{_BASE}/{uuid.uuid4()}")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# User isolation — a user cannot see another user's stash
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stash_is_user_scoped(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """A user cannot access another user's stash by guessing the stash_id."""
    push_resp = await client.post(_BASE, json=_PUSH_BODY, headers=auth_headers)
    stash_id = push_resp.json()["id"]

    # Create a second user and token
    from maestro.auth.tokens import create_access_token
    from maestro.db.models import User

    other_user = User(id=str(uuid.uuid4()), budget_cents=500, budget_limit_cents=500)
    db_session.add(other_user)
    await db_session.commit()
    other_token = create_access_token(user_id=other_user.id, expires_hours=1)
    other_headers = {"Authorization": f"Bearer {other_token}", "Content-Type": "application/json"}

    # Other user cannot see the stash
    resp = await client.get(f"{_BASE}/{stash_id}", headers=other_headers)
    assert resp.status_code == 404

    # Other user's list is empty
    list_resp = await client.get(_BASE, headers=other_headers)
    assert list_resp.json()["total"] == 0
