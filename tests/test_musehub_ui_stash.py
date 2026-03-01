"""Tests for Muse Hub stash UI endpoints (issue #433).

Covers GET /musehub/ui/{owner}/{repo_slug}/stash:
- test_stash_list_page_auth_required                 — unauthenticated GET → 401
- test_stash_list_page_returns_200_with_token        — authenticated GET → 200 HTML
- test_stash_list_page_shows_ref_labels              — HTML includes stash@{0} refs
- test_stash_list_page_action_buttons_present        — Apply / Pop / Drop buttons present
- test_stash_list_page_drop_confirm_present          — Drop form has confirmation dialog
- test_stash_list_page_json_response                 — ?format=json returns JSON with stashes key
- test_stash_list_page_json_fields                   — JSON stash items have required fields
- test_stash_list_page_empty_stash                   — empty stash returns 200 with 0 total
- test_stash_list_unknown_repo_404                   — unknown owner/slug → 404
- test_stash_list_isolates_by_user                   — only caller's stash is shown
- test_stash_list_pagination_query_params            — page/page_size accepted without error

Covers POST /musehub/ui/{owner}/{repo_slug}/stash/{stash_ref}/apply:
- test_stash_apply_auth_required                     — unauthenticated POST → 401
- test_stash_apply_redirects_to_stash_list           — authenticated POST → 303 redirect
- test_stash_apply_preserves_stash_entry             — stash entry still exists after apply

Covers POST /musehub/ui/{owner}/{repo_slug}/stash/{stash_ref}/pop:
- test_stash_pop_auth_required                       — unauthenticated POST → 401
- test_stash_pop_redirects_to_stash_list             — authenticated POST → 303 redirect
- test_stash_pop_deletes_stash_entry                 — stash entry removed after pop

Covers POST /musehub/ui/{owner}/{repo_slug}/stash/{stash_ref}/drop:
- test_stash_drop_auth_required                      — unauthenticated POST → 401
- test_stash_drop_redirects_to_stash_list            — authenticated POST → 303 redirect
- test_stash_drop_deletes_stash_entry                — stash entry removed after drop
- test_stash_drop_wrong_user_404                     — another user's stash → 404
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo
from maestro.db.musehub_stash_models import MusehubStash, MusehubStashEntry

_OWNER = "artist"
_SLUG = "album-one"
_USER_ID = "550e8400-e29b-41d4-a716-446655440000"  # matches test_user fixture


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(db: AsyncSession, owner: str = _OWNER, slug: str = _SLUG) -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id=_USER_ID,
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_stash(
    db: AsyncSession,
    repo_id: str,
    *,
    user_id: str = _USER_ID,
    branch: str = "main",
    message: str | None = "WIP: brass arrangement",
    num_entries: int = 2,
) -> MusehubStash:
    """Seed a stash entry with ``num_entries`` file entries and return it."""
    stash = MusehubStash(
        repo_id=repo_id,
        user_id=user_id,
        branch=branch,
        message=message,
    )
    db.add(stash)
    await db.flush()

    for i in range(num_entries):
        entry = MusehubStashEntry(
            stash_id=stash.id,
            path=f"tracks/track_{i}.mid",
            object_id=f"sha256:{'a' * 64}",
            position=i,
        )
        db.add(entry)

    await db.commit()
    await db.refresh(stash)
    return stash


# ---------------------------------------------------------------------------
# GET — stash list page
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stash_list_page_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Unauthenticated GET returns 401 — stash is always private."""
    await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{_OWNER}/{_SLUG}/stash")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_stash_list_page_returns_200_with_token(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """Authenticated GET returns 200 HTML."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_stash_list_page_shows_ref_labels(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """HTML page contains stash@{0} ref label for the first stash entry."""
    repo_id = await _make_repo(db_session)
    await _make_stash(db_session, repo_id)
    response = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert "stash@{0}" in response.text


@pytest.mark.anyio
async def test_stash_list_page_action_buttons_present(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """HTML page exposes Apply, Pop, and Drop action buttons."""
    repo_id = await _make_repo(db_session)
    await _make_stash(db_session, repo_id)
    response = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.text
    assert "Apply" in body
    assert "Pop" in body
    assert "Drop" in body


@pytest.mark.anyio
async def test_stash_list_page_drop_confirm_present(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """Drop button form has a JavaScript confirm() dialog to prevent accidents."""
    repo_id = await _make_repo(db_session)
    await _make_stash(db_session, repo_id)
    response = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert "confirm(" in response.text


@pytest.mark.anyio
async def test_stash_list_page_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """?format=json returns JSON with HTTP 200."""
    repo_id = await _make_repo(db_session)
    await _make_stash(db_session, repo_id)
    headers = {**auth_headers, "Content-Type": "application/json"}
    response = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash?format=json",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.anyio
async def test_stash_list_page_json_fields(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """JSON stash items include ref, branch, message, createdAt, entryCount."""
    repo_id = await _make_repo(db_session)
    await _make_stash(db_session, repo_id, branch="feat/bass", message="WIP brass", num_entries=3)
    response = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash?format=json",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert "stashes" in data
    assert "total" in data
    assert data["total"] == 1
    item = data["stashes"][0]
    assert item["ref"] == "stash@{0}"
    assert item["branch"] == "feat/bass"
    assert item["message"] == "WIP brass"
    assert item["entryCount"] == 3
    assert "createdAt" in item


@pytest.mark.anyio
async def test_stash_list_page_empty_stash(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """Stash list page with no entries returns 200 and total=0."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash?format=json",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["stashes"] == []


@pytest.mark.anyio
async def test_stash_list_unknown_repo_404(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """Unknown owner/slug returns 404."""
    response = await client.get(
        "/musehub/ui/nobody/nonexistent/stash",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_stash_list_isolates_by_user(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """Stash list only returns entries owned by the authenticated user."""
    repo_id = await _make_repo(db_session)
    other_user_id = str(uuid.uuid4())
    # Create a stash for a different (non-existent for FK purposes) user using raw SQL
    # to avoid FK violation — we only care about the scoping logic, not DB integrity here.
    await db_session.execute(
        text(
            "INSERT INTO musehub_stash (id, repo_id, user_id, branch, message, is_applied, created_at) "
            "VALUES (:id, :repo_id, :user_id, :branch, :message, false, datetime('now'))"
        ),
        {
            "id": str(uuid.uuid4()),
            "repo_id": repo_id,
            "user_id": other_user_id,
            "branch": "main",
            "message": "someone else's stash",
        },
    )
    await db_session.commit()

    response = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash?format=json",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    # Our test user has no stash entries — the other user's stash is invisible
    assert data["total"] == 0


@pytest.mark.anyio
async def test_stash_list_pagination_query_params(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """page and page_size query params are accepted without error."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash?page=1&page_size=10",
        headers=auth_headers,
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# POST — apply stash
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stash_apply_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Unauthenticated POST to /apply returns 401."""
    repo_id = await _make_repo(db_session)
    stash = await _make_stash(db_session, repo_id)
    response = await client.post(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash/{stash.id}/apply",
        follow_redirects=False,
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_stash_apply_redirects_to_stash_list(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """Authenticated POST to /apply returns 303 redirect to the stash list."""
    repo_id = await _make_repo(db_session)
    stash = await _make_stash(db_session, repo_id)
    response = await client.post(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash/{stash.id}/apply",
        headers=auth_headers,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/musehub/ui/{_OWNER}/{_SLUG}/stash"


@pytest.mark.anyio
async def test_stash_apply_preserves_stash_entry(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """Apply does NOT delete the stash entry — it stays on the stack."""
    repo_id = await _make_repo(db_session)
    stash = await _make_stash(db_session, repo_id)
    stash_id = stash.id

    await client.post(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash/{stash_id}/apply",
        headers=auth_headers,
        follow_redirects=False,
    )

    # Verify the stash entry still exists
    result = await db_session.execute(
        text("SELECT id FROM musehub_stash WHERE id = :id"),
        {"id": stash_id},
    )
    row = result.mappings().first()
    assert row is not None, "Stash entry should NOT be deleted after apply"


# ---------------------------------------------------------------------------
# POST — pop stash
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stash_pop_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Unauthenticated POST to /pop returns 401."""
    repo_id = await _make_repo(db_session)
    stash = await _make_stash(db_session, repo_id)
    response = await client.post(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash/{stash.id}/pop",
        follow_redirects=False,
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_stash_pop_redirects_to_stash_list(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """Authenticated POST to /pop returns 303 redirect to the stash list."""
    repo_id = await _make_repo(db_session)
    stash = await _make_stash(db_session, repo_id)
    response = await client.post(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash/{stash.id}/pop",
        headers=auth_headers,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/musehub/ui/{_OWNER}/{_SLUG}/stash"


@pytest.mark.anyio
async def test_stash_pop_deletes_stash_entry(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """Pop deletes the stash entry from the stack."""
    repo_id = await _make_repo(db_session)
    stash = await _make_stash(db_session, repo_id)
    stash_id = stash.id

    await client.post(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash/{stash_id}/pop",
        headers=auth_headers,
        follow_redirects=False,
    )

    # Verify the stash entry is gone
    db_session.expire_all()
    result = await db_session.execute(
        text("SELECT id FROM musehub_stash WHERE id = :id"),
        {"id": stash_id},
    )
    row = result.mappings().first()
    assert row is None, "Stash entry SHOULD be deleted after pop"


# ---------------------------------------------------------------------------
# POST — drop stash
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stash_drop_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Unauthenticated POST to /drop returns 401."""
    repo_id = await _make_repo(db_session)
    stash = await _make_stash(db_session, repo_id)
    response = await client.post(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash/{stash.id}/drop",
        follow_redirects=False,
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_stash_drop_redirects_to_stash_list(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """Authenticated POST to /drop returns 303 redirect to the stash list."""
    repo_id = await _make_repo(db_session)
    stash = await _make_stash(db_session, repo_id)
    response = await client.post(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash/{stash.id}/drop",
        headers=auth_headers,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/musehub/ui/{_OWNER}/{_SLUG}/stash"


@pytest.mark.anyio
async def test_stash_drop_deletes_stash_entry(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """Drop permanently deletes the stash entry without applying it."""
    repo_id = await _make_repo(db_session)
    stash = await _make_stash(db_session, repo_id)
    stash_id = stash.id

    await client.post(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash/{stash_id}/drop",
        headers=auth_headers,
        follow_redirects=False,
    )

    db_session.expire_all()
    result = await db_session.execute(
        text("SELECT id FROM musehub_stash WHERE id = :id"),
        {"id": stash_id},
    )
    row = result.mappings().first()
    assert row is None, "Stash entry SHOULD be deleted after drop"


@pytest.mark.anyio
async def test_stash_drop_wrong_user_404(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    test_user: object,
) -> None:
    """Attempting to drop another user's stash returns 404."""
    repo_id = await _make_repo(db_session)
    other_stash_id = str(uuid.uuid4())
    other_user_id = str(uuid.uuid4())

    # Insert a stash owned by a different user using raw SQL
    await db_session.execute(
        text(
            "INSERT INTO musehub_stash (id, repo_id, user_id, branch, message, is_applied, created_at) "
            "VALUES (:id, :repo_id, :user_id, :branch, :message, false, datetime('now'))"
        ),
        {
            "id": other_stash_id,
            "repo_id": repo_id,
            "user_id": other_user_id,
            "branch": "main",
            "message": "other user stash",
        },
    )
    await db_session.commit()

    response = await client.post(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash/{other_stash_id}/drop",
        headers=auth_headers,
        follow_redirects=False,
    )
    assert response.status_code == 404
