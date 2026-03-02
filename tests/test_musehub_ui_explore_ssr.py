"""SSR tests for Muse Hub explore + trending pages — issue #576.

Validates that repo data is rendered server-side into HTML (not deferred to
client JS) and that HTMX fragment requests return bare grid HTML without the
full page shell.

Covers GET /musehub/ui/explore:
- test_explore_page_renders_repo_name_server_side    — repo name in HTML
- test_explore_page_sort_filter_form_has_hx_get      — filter form has hx-get
- test_explore_page_genre_filter_narrows_repos        — ?topic=jazz → jazz-tagged
- test_explore_page_htmx_fragment_path               — HX-Request → fragment only
- test_explore_page_empty_state_when_no_repos        — no public repos → empty state

Covers GET /musehub/ui/trending:
- test_trending_page_renders_repo_server_side         — repo name in HTML
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _make_public_repo(
    db: AsyncSession,
    *,
    owner: str = "artist",
    slug: str = "cool-album",
    name: str = "cool-album",
    tags: list[str] | None = None,
    star_count: int = 0,
) -> MusehubRepo:
    """Seed a public repo and return the ORM object."""
    repo = MusehubRepo(
        name=name,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id=f"uid-{slug}",
        tags=tags or [],
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return repo


# ---------------------------------------------------------------------------
# Explore page SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_explore_page_renders_repo_name_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Repo name is in the HTML response without any client-side JS execution."""
    await _make_public_repo(db_session, slug="jazz-sessions", name="jazz-sessions")
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "jazz-sessions" in response.text


@pytest.mark.anyio
async def test_explore_page_sort_filter_form_has_hx_get(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Filter form has hx-get attribute enabling HTMX partial swap."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200
    assert 'hx-get="/musehub/ui/explore"' in response.text


@pytest.mark.anyio
async def test_explore_page_genre_filter_narrows_repos(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?topic=jazz returns only jazz-tagged repos (genre filter works SSR)."""
    await _make_public_repo(
        db_session, slug="jazz-album", name="jazz-album", tags=["jazz", "piano"]
    )
    await _make_public_repo(
        db_session, slug="rock-album", name="rock-album", tags=["rock", "guitar"]
    )
    response = await client.get("/musehub/ui/explore?topic=jazz")
    assert response.status_code == 200
    body = response.text
    assert "jazz-album" in body
    assert "rock-album" not in body


@pytest.mark.anyio
async def test_explore_page_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HX-Request: true returns a bare HTML fragment without the full page shell."""
    await _make_public_repo(db_session, slug="htmx-repo", name="htmx-repo")
    response = await client.get(
        "/musehub/ui/explore",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "<html" not in response.text
    assert "<head" not in response.text
    # Fragment should contain the repo or empty-state markup.
    assert "htmx-repo" in response.text or "No repositories found" in response.text


@pytest.mark.anyio
async def test_explore_page_empty_state_when_no_repos(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """When no public repos exist the empty-state message is rendered SSR."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200
    assert "No repositories found" in response.text


# ---------------------------------------------------------------------------
# Trending page SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_trending_page_renders_repo_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Trending page renders public repo name in HTML without client-side JS."""
    await _make_public_repo(
        db_session, slug="trending-hit", name="trending-hit", star_count=100
    )
    response = await client.get("/musehub/ui/trending")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "trending-hit" in response.text
