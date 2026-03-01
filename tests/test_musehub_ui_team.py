"""Tests for the MuseHub collaborators/team management UI page.

Covers issue #436 — GET /musehub/ui/{owner}/{repo_slug}/settings/collaborators

Test index:
- test_collaborators_settings_page_returns_200
    GET the settings/collaborators page returns 200 HTML without a JWT.
- test_collaborators_settings_page_no_auth_required
    The HTML shell is accessible without a Bearer token.
- test_collaborators_settings_page_unknown_repo_404
    Unknown owner/slug combination returns 404.
- test_collaborators_settings_page_has_invite_form_js
    The page embeds the invite-form JavaScript (inviteCollab function).
- test_collaborators_settings_page_has_permission_badges_js
    The page embeds permission badge rendering logic with colour coding.
- test_collaborators_settings_page_has_owner_crown_badge
    The page marks owner permission with a crown emoji (👑).
- test_collaborators_settings_page_has_remove_button_js
    The page embeds removeCollab() JavaScript for removing collaborators.
- test_collaborators_settings_json_response_empty
    ?format=json returns CollaboratorListResponse with empty list for new repo.
- test_collaborators_settings_json_response_with_collaborators
    ?format=json returns collaborators seeded in the DB.
- test_collaborators_settings_page_has_settings_tabs
    The page includes the settings tab navigation bar.
- test_collaborators_settings_page_has_pending_invites_section_js
    The page renders the pending invites section via JS.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_collaborator_models import MusehubCollaborator
from maestro.db.musehub_models import MusehubRepo

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OWNER = "testuser"
_SLUG = "collab-test-repo"


async def _make_repo(db_session: AsyncSession) -> str:
    """Seed a minimal repo for collaborator tests and return its repo_id."""
    repo = MusehubRepo(
        name=_SLUG,
        owner=_OWNER,
        slug=_SLUG,
        visibility="private",
        owner_user_id="owner-user-id",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


async def _add_collaborator(
    db_session: AsyncSession,
    repo_id: str,
    user_id: str,
    permission: str = "write",
    invited_by: str | None = None,
) -> MusehubCollaborator:
    """Seed a collaborator record and return it."""
    collab = MusehubCollaborator(
        id=str(uuid.uuid4()),
        repo_id=repo_id,
        user_id=user_id,
        permission=permission,
        invited_by=invited_by,
    )
    db_session.add(collab)
    await db_session.commit()
    await db_session.refresh(collab)
    return collab


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_collaborators_settings_page_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/settings/collaborators returns 200 HTML."""
    await _make_repo(db_session)
    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/settings/collaborators")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


async def test_collaborators_settings_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The HTML shell is accessible without a Bearer token.

    Auth is enforced client-side; the server must not demand a JWT to
    render the page shell.
    """
    await _make_repo(db_session)
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/settings/collaborators",
        headers={},  # explicit: no Authorization header
    )
    assert resp.status_code == 200


async def test_collaborators_settings_page_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Unknown owner/slug combination returns 404."""
    resp = await client.get("/musehub/ui/nobody/nonexistent-repo/settings/collaborators")
    assert resp.status_code == 404


async def test_collaborators_settings_page_has_invite_form_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The page embeds inviteCollab() JavaScript for the invite form."""
    await _make_repo(db_session)
    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/settings/collaborators")
    assert resp.status_code == 200
    assert "inviteCollab" in resp.text


async def test_collaborators_settings_page_has_permission_badges_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The page embeds colour-coded permission badge logic."""
    await _make_repo(db_session)
    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/settings/collaborators")
    assert resp.status_code == 200
    body = resp.text
    # Colour-coding for each permission level must be present.
    assert "badge-perm-read" in body
    assert "badge-perm-write" in body
    assert "badge-perm-admin" in body
    assert "badge-perm-owner" in body


async def test_collaborators_settings_page_has_owner_crown_badge(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The page marks owner permission with a crown emoji (👑)."""
    await _make_repo(db_session)
    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/settings/collaborators")
    assert resp.status_code == 200
    assert "👑" in resp.text


async def test_collaborators_settings_page_has_remove_button_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The page embeds removeCollab() JavaScript for removing collaborators."""
    await _make_repo(db_session)
    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/settings/collaborators")
    assert resp.status_code == 200
    assert "removeCollab" in resp.text


async def test_collaborators_settings_json_response_empty(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?format=json returns CollaboratorListResponse with empty list for a new repo."""
    await _make_repo(db_session)
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/settings/collaborators?format=json"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "collaborators" in data
    assert "total" in data
    assert data["total"] == 0
    assert data["collaborators"] == []


async def test_collaborators_settings_json_response_with_collaborators(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?format=json returns collaborators seeded in the DB."""
    repo_id = await _make_repo(db_session)
    collab_uid = str(uuid.uuid4())
    await _add_collaborator(
        db_session, repo_id, user_id=collab_uid, permission="write", invited_by="owner-user-id"
    )

    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/settings/collaborators?format=json"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["collaborators"]) == 1
    collab = data["collaborators"][0]
    # camelCase keys (Pydantic by_alias=True via negotiate_response)
    assert collab["userId"] == collab_uid
    assert collab["permission"] == "write"


async def test_collaborators_settings_page_has_settings_tabs(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The page includes the settings tab navigation bar."""
    await _make_repo(db_session)
    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/settings/collaborators")
    assert resp.status_code == 200
    body = resp.text
    assert "settings-tabs" in body
    assert "Collaborators" in body


async def test_collaborators_settings_page_has_pending_invites_section_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The page renders the pending invites / invite section via JavaScript."""
    await _make_repo(db_session)
    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/settings/collaborators")
    assert resp.status_code == 200
    body = resp.text
    # The invite form JavaScript key elements must be present.
    assert "invite-user-id" in body
    assert "invite-perm" in body
