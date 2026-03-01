"""SSR tests for the Muse Hub stash list page (issue #556).

Verifies that stash data is rendered server-side — i.e., branch names,
messages, and counts appear in the raw HTML response without requiring
JavaScript execution.

Auth required: all stash endpoints require a valid JWT Bearer token.

Covers:
- test_stash_page_renders_stash_entry_server_side   — branch name in HTML (no JS)
- test_stash_page_shows_total_count                 — total count badge in HTML
- test_stash_page_apply_form_uses_post              — Apply button is a <form method="post">
- test_stash_page_drop_has_hx_confirm               — Drop button has hx-confirm attribute
- test_stash_page_htmx_request_returns_fragment     — HX-Request: true → no <html> in response
- test_stash_page_empty_state_when_no_stashes       — empty list → empty-state message
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo
from maestro.db.musehub_stash_models import MusehubStash

_OWNER = "stash_artist"
_SLUG = "stash-album"
_UI_PATH = f"/musehub/ui/{_OWNER}/{_SLUG}/stash"


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _make_repo(db: AsyncSession, user_id: str) -> str:
    """Seed a public repo owned by ``user_id`` and return its repo_id."""
    repo = MusehubRepo(
        name=_SLUG,
        owner=_OWNER,
        slug=_SLUG,
        visibility="public",
        owner_user_id=user_id,
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_stash(
    db: AsyncSession,
    repo_id: str,
    user_id: str,
    *,
    branch: str = "main",
    message: str | None = "WIP: chorus reverb tweak",
) -> MusehubStash:
    """Seed a stash entry and return the ORM instance."""
    stash = MusehubStash(
        repo_id=repo_id,
        user_id=user_id,
        branch=branch,
        message=message,
        created_at=datetime.now(tz=timezone.utc),
    )
    db.add(stash)
    await db.commit()
    await db.refresh(stash)
    return stash


# ---------------------------------------------------------------------------
# SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stash_page_renders_stash_entry_server_side(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Branch name from a seeded stash entry appears in HTML without JS.

    The SSR handler queries the DB in the request and inlines the branch
    name directly into the HTML so the browser receives a complete page on
    first load — no JS fetch loop required.
    """
    user_id = "550e8400-e29b-41d4-a716-446655440000"
    repo_id = await _make_repo(db_session, user_id)
    await _make_stash(db_session, repo_id, user_id, branch="feat/ssr-bass-line")

    resp = await client.get(_UI_PATH, headers=auth_headers)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "feat/ssr-bass-line" in resp.text


@pytest.mark.anyio
async def test_stash_page_shows_total_count(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """The total stash count is rendered in the page header by the server.

    Seeding two stash entries; the badge must contain "2" without any JS.
    """
    user_id = "550e8400-e29b-41d4-a716-446655440000"
    repo_id = await _make_repo(db_session, user_id)
    await _make_stash(db_session, repo_id, user_id, branch="main", message="First save")
    await _make_stash(db_session, repo_id, user_id, branch="dev", message="Second save")

    resp = await client.get(_UI_PATH, headers=auth_headers)
    assert resp.status_code == 200
    assert "2 stash entries" in resp.text


@pytest.mark.anyio
async def test_stash_page_apply_form_uses_post(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Apply button is a <form method=\"post\"> — HTMX-compatible via hx-boost."""
    user_id = "550e8400-e29b-41d4-a716-446655440000"
    repo_id = await _make_repo(db_session, user_id)
    await _make_stash(db_session, repo_id, user_id)

    resp = await client.get(_UI_PATH, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.text
    assert 'method="post"' in body.lower() or 'method="POST"' in body
    assert "/apply" in body


@pytest.mark.anyio
async def test_stash_page_drop_has_hx_confirm(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Drop button carries hx-confirm to prompt before destructive action."""
    user_id = "550e8400-e29b-41d4-a716-446655440000"
    repo_id = await _make_repo(db_session, user_id)
    await _make_stash(db_session, repo_id, user_id)

    resp = await client.get(_UI_PATH, headers=auth_headers)
    assert resp.status_code == 200
    assert "hx-confirm" in resp.text
    assert "/drop" in resp.text


@pytest.mark.anyio
async def test_stash_page_htmx_request_returns_fragment(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """HX-Request: true header causes the handler to return only the rows fragment.

    The fragment must not contain the full <html> shell — HTMX swaps only
    the inner #stash-rows section, leaving the navigation and layout intact.
    """
    user_id = "550e8400-e29b-41d4-a716-446655440000"
    repo_id = await _make_repo(db_session, user_id)
    await _make_stash(db_session, repo_id, user_id, branch="htmx-branch")

    resp = await client.get(
        _UI_PATH,
        headers={**auth_headers, "HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "<html" not in resp.text
    assert "htmx-branch" in resp.text


@pytest.mark.anyio
async def test_stash_page_empty_state_when_no_stashes(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Empty stash list renders an empty-state message, not a JS loading skeleton."""
    user_id = "550e8400-e29b-41d4-a716-446655440000"
    await _make_repo(db_session, user_id)

    resp = await client.get(_UI_PATH, headers=auth_headers)
    assert resp.status_code == 200
    assert "No stashed changes" in resp.text
