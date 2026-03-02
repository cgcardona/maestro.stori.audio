"""SSR tests for the MuseHub collaborators settings page (issue #564).

Covers GET /musehub/ui/{owner}/{repo_slug}/settings/collaborators after SSR migration:

- test_collaborators_page_renders_collaborator_server_side
    Seed a collaborator, GET the page, assert the user_id appears in the HTML body
    — confirming server-side render rather than client-side JS fetch.

- test_collaborators_page_invite_form_has_hx_post
    The invite form carries ``hx-post`` pointing to the collaborators API.

- test_collaborators_page_remove_form_has_hx_delete
    Each non-owner collaborator row's remove form carries ``hx-delete``.

- test_collaborators_page_htmx_request_returns_fragment
    GET with ``HX-Request: true`` returns only the bare fragment (no <html> wrapper).
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_collaborator_models import MusehubCollaborator
from maestro.db.musehub_models import MusehubRepo

pytestmark = pytest.mark.anyio

_OWNER = "ssr-owner"
_SLUG = "ssr-collab-repo"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(db: AsyncSession) -> str:
    """Seed a minimal public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=_SLUG,
        owner=_OWNER,
        slug=_SLUG,
        visibility="public",
        owner_user_id="ssr-owner-uid",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _add_collaborator(
    db: AsyncSession,
    repo_id: str,
    *,
    user_id: str | None = None,
    permission: str = "write",
    invited_by: str | None = None,
) -> MusehubCollaborator:
    """Seed a collaborator record and return it."""
    collab = MusehubCollaborator(
        id=str(uuid.uuid4()),
        repo_id=repo_id,
        user_id=user_id or str(uuid.uuid4()),
        permission=permission,
        invited_by=invited_by,
    )
    db.add(collab)
    await db.commit()
    await db.refresh(collab)
    return collab


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_collaborators_page_renders_collaborator_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Seed a collaborator, GET the page, assert user_id is in the HTML body.

    The SSR migration means collaborators must be rendered server-side.
    This test fails if the handler omits ``collaborators`` from the template
    context or the template requires a client-side fetch to populate the list.
    """
    repo_id = await _make_repo(db_session)
    known_user_id = str(uuid.uuid4())
    await _add_collaborator(db_session, repo_id, user_id=known_user_id, permission="write")

    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/settings/collaborators")
    assert resp.status_code == 200
    assert known_user_id in resp.text


async def test_collaborators_page_invite_form_has_hx_post(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The invite form uses HTMX ``hx-post`` to call the collaborators API.

    The SSR migration replaces the inline JS inviteCollab() function with an
    HTMX form that posts directly to the JSON API endpoint.
    """
    await _make_repo(db_session)
    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/settings/collaborators")
    assert resp.status_code == 200
    assert "hx-post" in resp.text
    assert "/collaborators" in resp.text


async def test_collaborators_page_remove_form_has_hx_delete(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Non-owner collaborator rows carry ``hx-delete`` on the remove form.

    The SSR migration replaces the JS removeCollab() function with an HTMX
    form targeting the collaborators API endpoint for the specific user.
    """
    repo_id = await _make_repo(db_session)
    target_user_id = str(uuid.uuid4())
    await _add_collaborator(db_session, repo_id, user_id=target_user_id, permission="write")

    resp = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/settings/collaborators")
    assert resp.status_code == 200
    assert "hx-delete" in resp.text
    assert target_user_id in resp.text


async def test_collaborators_page_htmx_request_returns_fragment(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET with ``HX-Request: true`` returns only the bare collaborator fragment.

    The fragment must not contain a full HTML document shell (<html>, <head>)
    — it is swapped directly into ``#collaborator-rows`` by HTMX.
    """
    repo_id = await _make_repo(db_session)
    known_user_id = str(uuid.uuid4())
    await _add_collaborator(db_session, repo_id, user_id=known_user_id)

    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/settings/collaborators",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    body = resp.text
    # Fragment must contain the seeded collaborator
    assert known_user_id in body
    # Fragment must NOT be a full HTML document
    assert "<html" not in body
    assert "<head" not in body
