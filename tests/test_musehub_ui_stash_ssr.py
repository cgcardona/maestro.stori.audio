"""SSR tests for the Muse Hub stash page (issue #556).

Verifies that ``GET /musehub/ui/{owner}/{repo_slug}/stash`` renders stash
data server-side rather than relying on client-side JavaScript fetches.

Tests:
- test_stash_page_renders_stash_entry_server_side
  — Seed a stash entry, GET the page, assert branch name in HTML without JS
- test_stash_page_shows_total_count
  — Total count badge present in server-rendered HTML
- test_stash_page_apply_form_uses_post
  — Apply button is a <form method="post">
- test_stash_page_drop_has_hx_confirm
  — Drop button form has hx-confirm attribute (HTMX-native confirmation)
- test_stash_page_htmx_request_returns_fragment
  — HX-Request: true → no <html> in response, but stash data is present
- test_stash_page_empty_state_when_no_stashes
  — Empty stash list → empty state rendered without JS fetch
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo
from maestro.db.musehub_stash_models import MusehubStash, MusehubStashEntry

_OWNER = "bandleader"
_SLUG = "concert-setlist"
_USER_ID = "550e8400-e29b-41d4-a716-446655440000"  # matches test_user fixture


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _make_repo(db: AsyncSession) -> str:
    """Seed a repo and return its repo_id string."""
    repo = MusehubRepo(
        name=_SLUG,
        owner=_OWNER,
        slug=_SLUG,
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
    branch: str = "main",
    message: str | None = "WIP: horn section",
    num_entries: int = 1,
) -> MusehubStash:
    """Seed a stash entry with file entries and return it."""
    stash = MusehubStash(
        repo_id=repo_id,
        user_id=_USER_ID,
        branch=branch,
        message=message,
    )
    db.add(stash)
    await db.flush()

    for i in range(num_entries):
        db.add(
            MusehubStashEntry(
                stash_id=stash.id,
                path=f"tracks/track_{i}.mid",
                object_id=f"sha256:{'b' * 64}",
                position=i,
            )
        )

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
    """Branch name appears in the HTML response without a JS round-trip.

    The handler queries the DB during the request and inlines the branch
    name into the Jinja2 template so browsers receive a complete page
    on the first load.
    """
    repo_id = await _make_repo(db_session)
    await _make_stash(db_session, repo_id, branch="feat/ssr-stash")
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "feat/ssr-stash" in body
    assert "stash-row" in body


@pytest.mark.anyio
async def test_stash_page_shows_total_count(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Total stash entry count badge is present in the server-rendered HTML."""
    repo_id = await _make_repo(db_session)
    await _make_stash(db_session, repo_id)
    await _make_stash(db_session, repo_id, branch="feat/second")
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "2 stash entries" in body or "stash-count" in body


@pytest.mark.anyio
async def test_stash_page_apply_form_uses_post(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Apply action is rendered as a <form method="post"> for HTMX hx-boost compatibility."""
    repo_id = await _make_repo(db_session)
    await _make_stash(db_session, repo_id)
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert 'method="post"' in body or "method='post'" in body
    assert "/apply" in body


@pytest.mark.anyio
async def test_stash_page_drop_has_hx_confirm(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Drop form has hx-confirm attribute for HTMX-native confirmation before destructive action."""
    repo_id = await _make_repo(db_session)
    await _make_stash(db_session, repo_id)
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "hx-confirm" in body
    assert "/drop" in body


@pytest.mark.anyio
async def test_stash_page_htmx_request_returns_fragment(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """GET with HX-Request: true returns the rows fragment, not the full page.

    When HTMX issues a partial swap request it sends this header.  The response
    must NOT contain the full page chrome and MUST contain the stash row markup.
    """
    repo_id = await _make_repo(db_session)
    await _make_stash(db_session, repo_id, branch="htmx-branch")
    htmx_headers = {**auth_headers, "HX-Request": "true"}
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash", headers=htmx_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert "htmx-branch" in body
    assert "<!DOCTYPE html>" not in body
    assert "<html" not in body


@pytest.mark.anyio
async def test_stash_page_empty_state_when_no_stashes(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    test_user: object,
) -> None:
    """Empty stash list renders an empty-state component server-side (no JS fetch needed)."""
    await _make_repo(db_session)
    resp = await client.get(
        f"/musehub/ui/{_OWNER}/{_SLUG}/stash", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert '<div class="stash-row"' not in body
    assert "No stashed changes" in body or "empty-state" in body
