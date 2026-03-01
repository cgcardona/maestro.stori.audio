"""Tests for Muse Hub stash UI page (ui_stash.py).

Covers the acceptance criteria from issue #433:
- test_stash_page_returns_200_html          — GET /musehub/ui/{owner}/{slug}/stash returns HTML
- test_stash_page_no_auth_required_for_html — HTML shell accessible without JWT (JS handles auth)
- test_stash_page_unknown_repo_404          — unknown owner/slug returns 404
- test_stash_page_contains_stash_ref_ui     — stash@ reference format present in JS
- test_stash_page_contains_pop_action       — Pop action handler present
- test_stash_page_contains_apply_action     — Apply action handler present
- test_stash_page_contains_drop_action      — Drop action handler present
- test_stash_page_contains_confirmation     — Confirmation dialog guard present
- test_stash_page_json_empty_without_auth   — JSON without JWT returns empty stash list
- test_stash_page_json_returns_user_stash   — JSON with JWT returns authenticated user's stash
- test_stash_page_json_pagination           — JSON supports page/page_size query params
- test_stash_page_json_user_isolated        — JSON alternate shows only caller's stash

The HTML path requires no JWT (consistent with other MuseHub UI pages).
The JSON path uses optional_token and returns the caller's stash list.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo
from maestro.db.models import User

_OWNER = "testuser"
_SLUG = "test-beats"
_UI_BASE = f"/musehub/ui/{_OWNER}/{_SLUG}"
_UI_STASH = f"{_UI_BASE}/stash"

_STASH_BODY = {
    "message": "WIP: chorus section",
    "branch": "feat/chorus",
    "entries": [
        {"path": "tracks/piano.mid", "object_id": "sha256:aabbcc"},
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_repo(db_session: AsyncSession) -> str:
    """Seed a minimal MusehubRepo and return its repo_id."""
    repo = MusehubRepo(
        name="test-beats",
        owner=_OWNER,
        slug=_SLUG,
        visibility="private",
        owner_user_id="550e8400-e29b-41d4-a716-446655440000",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


# ---------------------------------------------------------------------------
# HTML page — no auth required
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stash_page_returns_200_html(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /musehub/ui/{owner}/{slug}/stash returns a 200 HTML response."""
    await _seed_repo(db_session)
    resp = await client.get(_UI_STASH)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.anyio
async def test_stash_page_no_auth_required_for_html(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The HTML shell is accessible without a JWT — auth is handled client-side."""
    await _seed_repo(db_session)
    resp = await client.get(_UI_STASH)
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_stash_page_unknown_repo_404(client: AsyncClient) -> None:
    """Unknown owner/slug → 404, consistent with all other UI repo pages."""
    resp = await client.get("/musehub/ui/nobody/does-not-exist/stash")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# HTML content — structural markers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stash_page_contains_stash_ref_ui(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Page includes stash@ reference format used by 'muse stash list'."""
    await _seed_repo(db_session)
    resp = await client.get(_UI_STASH)
    assert resp.status_code == 200
    assert "stash@{" in resp.text


@pytest.mark.anyio
async def test_stash_page_contains_pop_action(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Page includes a Pop action button for applying + deleting a stash entry."""
    await _seed_repo(db_session)
    resp = await client.get(_UI_STASH)
    assert resp.status_code == 200
    assert "pop" in resp.text.lower()


@pytest.mark.anyio
async def test_stash_page_contains_apply_action(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Page includes an Apply action button for applying without deleting."""
    await _seed_repo(db_session)
    resp = await client.get(_UI_STASH)
    assert resp.status_code == 200
    assert "apply" in resp.text.lower()


@pytest.mark.anyio
async def test_stash_page_contains_drop_action(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Page includes a Drop action button for discarding without applying."""
    await _seed_repo(db_session)
    resp = await client.get(_UI_STASH)
    assert resp.status_code == 200
    assert "drop" in resp.text.lower()


@pytest.mark.anyio
async def test_stash_page_contains_confirmation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Page includes a confirmation guard before executing destructive actions."""
    await _seed_repo(db_session)
    resp = await client.get(_UI_STASH)
    assert resp.status_code == 200
    assert "confirm(" in resp.text


# ---------------------------------------------------------------------------
# JSON alternate — content negotiation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stash_page_json_empty_without_auth(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """JSON response without JWT returns an empty stash list (not 401).

    The stash page uses optional_token so unauthenticated browsers still get
    an HTML shell; the JSON path returns an empty payload rather than 401.
    """
    await _seed_repo(db_session)
    resp = await client.get(_UI_STASH, params={"format": "json"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.anyio
async def test_stash_page_json_returns_user_stash(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: User,
) -> None:
    """JSON with a valid JWT returns the authenticated user's stash entries.

    Seed a stash entry via the JSON API, then verify the UI page's JSON
    alternate exposes it under the same owner/slug URL.
    """
    repo_id = await _seed_repo(db_session)
    api_base = f"/api/v1/musehub/repos/{repo_id}/stash"

    # Push one stash entry via the API
    push_resp = await client.post(api_base, json=_STASH_BODY, headers=auth_headers)
    assert push_resp.status_code == 201, push_resp.text

    # Fetch via the UI JSON alternate
    resp = await client.get(
        _UI_STASH,
        params={"format": "json"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["branch"] == "feat/chorus"
    assert data["items"][0]["message"] == "WIP: chorus section"


@pytest.mark.anyio
async def test_stash_page_json_pagination(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: User,
) -> None:
    """JSON alternate respects page and page_size query parameters."""
    repo_id = await _seed_repo(db_session)
    api_base = f"/api/v1/musehub/repos/{repo_id}/stash"

    for i in range(3):
        await client.post(api_base, json={**_STASH_BODY, "message": f"stash {i}"}, headers=auth_headers)

    resp = await client.get(
        _UI_STASH,
        params={"format": "json", "page": 1, "page_size": 2},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["page_size"] == 2
    assert len(data["items"]) == 2


@pytest.mark.anyio
async def test_stash_page_json_user_isolated(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: User,
) -> None:
    """JSON alternate shows only the caller's stash — not other users' entries."""
    repo_id = await _seed_repo(db_session)
    api_base = f"/api/v1/musehub/repos/{repo_id}/stash"

    # Push one entry as the test user
    await client.post(api_base, json=_STASH_BODY, headers=auth_headers)

    # Create a second user and verify they see an empty stash list
    from maestro.auth.tokens import create_access_token

    other_user = User(id=str(uuid.uuid4()), budget_cents=500, budget_limit_cents=500)
    db_session.add(other_user)
    await db_session.commit()
    other_token = create_access_token(user_id=other_user.id, expires_hours=1)
    other_headers = {"Authorization": f"Bearer {other_token}"}

    resp = await client.get(
        _UI_STASH,
        params={"format": "json"},
        headers=other_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []
