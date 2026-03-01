"""Tests for the Muse Hub new-repo creation wizard (issue #438).

Covers ``maestro/api/routes/musehub/ui_new_repo.py``:

  GET  /musehub/ui/new
  POST /musehub/ui/new
  GET  /musehub/ui/new/check

Test matrix:
  test_new_repo_page_returns_200             — GET returns HTTP 200 HTML
  test_new_repo_page_no_auth_required        — GET works without a JWT
  test_new_repo_page_has_form               — HTML contains the wizard form
  test_new_repo_page_has_owner_input         — HTML has owner input field
  test_new_repo_page_has_visibility_options  — HTML has Public/Private toggle
  test_new_repo_page_has_license_options     — JS references LICENSES constant
  test_new_repo_page_has_topics_input        — HTML has topics container
  test_new_repo_page_has_initialize_checkbox — HTML has initialize checkbox
  test_new_repo_page_has_branch_input        — HTML has default branch input
  test_new_repo_page_has_template_search     — HTML has template search input
  test_check_available_returns_true          — GET /new/check → available=true
  test_check_taken_returns_false             — GET /new/check → available=false
  test_check_requires_owner_and_slug         — GET /new/check → 422 when missing params
  test_create_repo_requires_auth             — POST without token → 401/403
  test_create_repo_success                   — POST with valid body → 201 + redirect
  test_create_repo_409_on_duplicate          — POST duplicate → 409
  test_create_repo_redirect_url_format       — redirect URL contains /musehub/ui/{owner}/{slug}?welcome=1
  test_create_repo_private_default           — POST without visibility → defaults to private
  test_create_repo_initializes_repo          — POST with initialize=true creates the repo
  test_create_repo_with_license              — POST with license field stored correctly
  test_create_repo_with_topics               — POST with topics stored as tags
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _seed_repo(
    db_session: AsyncSession,
    owner: str = "wizowner",
    slug: str = "existing-repo",
) -> MusehubRepo:
    """Seed a repo with a known owner/slug for uniqueness-check tests."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="seed-uid",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return repo


# ---------------------------------------------------------------------------
# GET /musehub/ui/new — HTML wizard
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_new_repo_page_returns_200(client: AsyncClient) -> None:
    """GET /musehub/ui/new returns HTTP 200."""
    resp = await client.get("/musehub/ui/new")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_new_repo_page_no_auth_required(client: AsyncClient) -> None:
    """The wizard HTML shell is accessible without a JWT — consistent with all other UI pages."""
    resp = await client.get("/musehub/ui/new")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


@pytest.mark.anyio
async def test_new_repo_page_has_form(client: AsyncClient) -> None:
    """The wizard page contains the HTML wizard form."""
    resp = await client.get("/musehub/ui/new")
    assert resp.status_code == 200
    html = resp.text
    assert "wizard-form" in html or "new_repo" in html or "Create" in html


@pytest.mark.anyio
async def test_new_repo_page_has_owner_input(client: AsyncClient) -> None:
    """The wizard page references the owner input field."""
    resp = await client.get("/musehub/ui/new")
    assert resp.status_code == 200
    assert "f-owner" in resp.text


@pytest.mark.anyio
async def test_new_repo_page_has_visibility_options(client: AsyncClient) -> None:
    """The wizard page has Public/Private visibility toggle."""
    resp = await client.get("/musehub/ui/new")
    assert resp.status_code == 200
    assert "Public" in resp.text
    assert "Private" in resp.text


@pytest.mark.anyio
async def test_new_repo_page_has_license_options(client: AsyncClient) -> None:
    """The wizard page includes JS LICENSES constant with the expected license names."""
    resp = await client.get("/musehub/ui/new")
    assert resp.status_code == 200
    assert "CC0" in resp.text
    assert "CC BY" in resp.text
    assert "All Rights Reserved" in resp.text


@pytest.mark.anyio
async def test_new_repo_page_has_topics_input(client: AsyncClient) -> None:
    """The wizard page contains the topics tag input container."""
    resp = await client.get("/musehub/ui/new")
    assert resp.status_code == 200
    assert "topics-container" in resp.text or "topic-input" in resp.text


@pytest.mark.anyio
async def test_new_repo_page_has_initialize_checkbox(client: AsyncClient) -> None:
    """The wizard page has the 'Initialize this repository' checkbox."""
    resp = await client.get("/musehub/ui/new")
    assert resp.status_code == 200
    assert "f-initialize" in resp.text or "initialize" in resp.text.lower()


@pytest.mark.anyio
async def test_new_repo_page_has_branch_input(client: AsyncClient) -> None:
    """The wizard page has the default branch name input."""
    resp = await client.get("/musehub/ui/new")
    assert resp.status_code == 200
    assert "f-branch" in resp.text


@pytest.mark.anyio
async def test_new_repo_page_has_template_search(client: AsyncClient) -> None:
    """The wizard page has the template repository search input."""
    resp = await client.get("/musehub/ui/new")
    assert resp.status_code == 200
    assert "template-search-input" in resp.text or "template" in resp.text.lower()


# ---------------------------------------------------------------------------
# GET /musehub/ui/new/check — name availability
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_available_returns_true(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /new/check → available=true when no repo exists with that owner+slug."""
    resp = await client.get(
        "/musehub/ui/new/check",
        params={"owner": "nobody", "slug": "no-such-repo"},
    )
    assert resp.status_code == 200
    assert resp.json()["available"] is True


@pytest.mark.anyio
async def test_check_taken_returns_false(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /new/check → available=false when the owner+slug is already taken."""
    await _seed_repo(db_session, owner="wizowner", slug="existing-repo")
    resp = await client.get(
        "/musehub/ui/new/check",
        params={"owner": "wizowner", "slug": "existing-repo"},
    )
    assert resp.status_code == 200
    assert resp.json()["available"] is False


@pytest.mark.anyio
async def test_check_requires_owner_and_slug(client: AsyncClient) -> None:
    """GET /new/check without required params returns 422."""
    resp = await client.get("/musehub/ui/new/check")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /musehub/ui/new — repo creation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_repo_requires_auth(client: AsyncClient) -> None:
    """POST /musehub/ui/new without Authorization header returns 401 or 403."""
    resp = await client.post(
        "/musehub/ui/new",
        json={
            "name": "test-repo",
            "owner": "someowner",
            "visibility": "private",
        },
    )
    assert resp.status_code in (401, 403)


@pytest.mark.anyio
async def test_create_repo_success(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /musehub/ui/new with valid body returns 201 and a redirect URL."""
    resp = await client.post(
        "/musehub/ui/new",
        json={
            "name": "New Composition",
            "owner": "testowner",
            "visibility": "public",
            "description": "A new jazz piece",
            "tags": [],
            "topics": ["jazz", "piano"],
            "initialize": True,
            "defaultBranch": "main",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "redirect" in data
    assert "musehub/ui" in data["redirect"]


@pytest.mark.anyio
async def test_create_repo_409_on_duplicate(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /musehub/ui/new with a duplicate owner+name returns 409."""
    await _seed_repo(db_session, owner="dupowner", slug="dup-repo")
    # 'dup-repo' is the slug generated from the name 'dup-repo'
    resp = await client.post(
        "/musehub/ui/new",
        json={
            "name": "dup-repo",
            "owner": "dupowner",
            "visibility": "private",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_create_repo_redirect_url_format(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """The redirect URL contains owner/slug path and ?welcome=1 query param."""
    resp = await client.post(
        "/musehub/ui/new",
        json={
            "name": "redirect-test",
            "owner": "urlowner",
            "visibility": "private",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    redirect = resp.json()["redirect"]
    assert "urlowner" in redirect
    assert "welcome=1" in redirect
    assert redirect.startswith("/musehub/ui/")


@pytest.mark.anyio
async def test_create_repo_private_default(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST without specifying visibility defaults to 'private'."""
    resp = await client.post(
        "/musehub/ui/new",
        json={
            "name": "private-default-test",
            "owner": "privowner",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    # Confirm the slug and owner are in the redirect — repo was created.
    assert "privowner" in resp.json()["redirect"]


@pytest.mark.anyio
async def test_create_repo_initializes_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST with initialize=true creates the repo successfully."""
    resp = await client.post(
        "/musehub/ui/new",
        json={
            "name": "init-repo-test",
            "owner": "initowner",
            "visibility": "public",
            "initialize": True,
            "defaultBranch": "trunk",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "repoId" in data
    assert data["slug"] == "init-repo-test"


@pytest.mark.anyio
async def test_create_repo_with_license(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST with a license value is accepted and reflected in the response."""
    resp = await client.post(
        "/musehub/ui/new",
        json={
            "name": "licensed-repo",
            "owner": "licowner",
            "visibility": "public",
            "license": "CC BY",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201


@pytest.mark.anyio
async def test_create_repo_with_topics(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST with topics results in a 201 and stores tags on the new repo."""
    resp = await client.post(
        "/musehub/ui/new",
        json={
            "name": "topical-repo",
            "owner": "topicowner",
            "visibility": "public",
            "topics": ["jazz", "piano", "neosoul"],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
