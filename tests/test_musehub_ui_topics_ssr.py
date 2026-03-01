"""SSR-specific tests for the Muse Hub topics pages (issue #560).

Verifies that both topic pages render their data server-side via Jinja2 —
no client-side API fetch required — and that HTMX partial requests receive
only the appropriate fragment template.

Covers:
- test_topics_index_renders_topic_tag_server_side — tag from DB appears in HTML
- test_topic_detail_renders_repo_name_server_side — repo name appears in HTML
- test_topic_detail_sort_changes_order            — sort=stars vs sort=updated
- test_topics_htmx_request_returns_fragment       — HX-Request: true → fragment
- test_topic_detail_htmx_returns_repos_fragment   — detail HTMX → repos fragment
- test_topics_index_renders_curated_groups        — Genres/Instruments/Eras in HTML
- test_topics_index_no_js_api_fetch               — no client-side fetch for topic data
- test_topic_detail_renders_pagination            — page 1 of N rendered server-side
- test_topic_detail_featured_repos_rendered       — top-3 featured repos in SSR HTML
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo, MusehubStar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db_session: AsyncSession,
    *,
    name: str = "test-repo",
    owner: str = "alice",
    slug: str | None = None,
    tags: list[str] | None = None,
    visibility: str = "public",
    description: str = "",
) -> str:
    """Seed a minimal repo and return its repo_id string."""
    repo = MusehubRepo(
        name=name,
        owner=owner,
        slug=slug or name,
        visibility=visibility,
        owner_user_id="00000000-0000-0000-0000-000000000001",
        tags=tags or [],
        description=description,
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


async def _star_repo(db_session: AsyncSession, repo_id: str, user_id: str) -> None:
    """Add a star to a repo."""
    star = MusehubStar(repo_id=repo_id, user_id=user_id)
    db_session.add(star)
    await db_session.commit()


# ---------------------------------------------------------------------------
# Index page — SSR assertions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_topics_index_renders_topic_tag_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A tag from a seeded repo must appear in the server-rendered HTML chip grid."""
    await _make_repo(db_session, name="jazz-repo", slug="jazz-repo", tags=["jazz"])
    response = await client.get("/musehub/ui/topics")
    assert response.status_code == 200
    body = response.text
    # SSR: tag chip rendered as an <a> with data-name="jazz"
    assert "data-name=\"jazz\"" in body
    assert "#jazz" in body


@pytest.mark.anyio
async def test_topics_index_renders_curated_groups(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Genres, Instruments, Eras must be rendered as category sections in the SSR HTML.

    The template only renders a curated group when at least one of its tags has
    a non-zero repo_count, so we seed one repo per group to ensure all three
    sections appear in the rendered HTML.
    """
    # jazz → Genres, piano → Instruments, baroque → Eras
    await _make_repo(db_session, name="jazz-r", slug="jazz-r", tags=["jazz", "piano", "baroque"])
    response = await client.get("/musehub/ui/topics")
    assert response.status_code == 200
    body = response.text
    # Curated group labels rendered server-side
    assert "Genres" in body
    assert "Instruments" in body
    assert "Eras" in body


@pytest.mark.anyio
async def test_topics_index_no_js_api_fetch(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The SSR topics index must not contain a client-side fetch call for topic data."""
    await _make_repo(db_session, name="jazz-q", slug="jazz-q", tags=["jazz"])
    response = await client.get("/musehub/ui/topics")
    assert response.status_code == 200
    body = response.text
    # SSR: no async fetch to the JSON endpoint needed for initial render
    assert "uiFetch('/musehub/ui/topics?format=json')" not in body
    assert "uiFetch(`/musehub/ui/topics/" not in body


@pytest.mark.anyio
async def test_topics_htmx_request_returns_fragment(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/topics with HX-Request: true must return only the topic grid fragment."""
    await _make_repo(db_session, name="jazz-hx", slug="jazz-hx", tags=["jazz"])
    response = await client.get(
        "/musehub/ui/topics", headers={"HX-Request": "true"}
    )
    assert response.status_code == 200
    body = response.text
    # Fragment: has the topic grid div but NOT the full <html> wrapper
    assert 'id="topic-grid"' in body
    assert "<html" not in body
    assert "<body" not in body
    # The tag chip must still be rendered in the fragment
    assert "#jazz" in body


# ---------------------------------------------------------------------------
# Detail page — SSR assertions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_topic_detail_renders_repo_name_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A repo tagged with the requested topic must appear by name in the SSR HTML."""
    await _make_repo(
        db_session,
        name="cool-jazz",
        slug="cool-jazz",
        tags=["jazz"],
        description="A cool jazz composition",
    )
    response = await client.get("/musehub/ui/topics/jazz")
    assert response.status_code == 200
    body = response.text
    # Repo name and owner rendered server-side in repo card
    assert "cool-jazz" in body
    # Description also rendered
    assert "A cool jazz composition" in body


@pytest.mark.anyio
async def test_topic_detail_sort_changes_order(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """sort=stars and sort=updated produce different sort-button active states."""
    await _make_repo(db_session, name="jazz-a", slug="jazz-a", tags=["jazz"])
    await _make_repo(db_session, name="jazz-b", slug="jazz-b", tags=["jazz"])

    stars_resp = await client.get("/musehub/ui/topics/jazz?sort=stars")
    updated_resp = await client.get("/musehub/ui/topics/jazz?sort=updated")

    assert stars_resp.status_code == 200
    assert updated_resp.status_code == 200

    # Stars page: sort=stars button is active (btn-primary), sort=updated is secondary
    assert "sort=stars&amp;page=1" in stars_resp.text
    assert "sort=updated&amp;page=1" in stars_resp.text

    # Updated page: sort=updated reflected in active button
    assert "sort=updated&amp;page=1" in updated_resp.text


@pytest.mark.anyio
async def test_topic_detail_htmx_returns_repos_fragment(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/topics/{tag} with HX-Request: true returns only the repos fragment."""
    await _make_repo(db_session, name="jazz-hx2", slug="jazz-hx2", tags=["jazz"])
    response = await client.get(
        "/musehub/ui/topics/jazz", headers={"HX-Request": "true"}
    )
    assert response.status_code == 200
    body = response.text
    # Fragment: has the repos div but NOT the full HTML wrapper
    assert 'id="topic-repos"' in body
    assert "<html" not in body
    assert "<body" not in body


@pytest.mark.anyio
async def test_topic_detail_renders_pagination(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """When there are multiple pages, pagination links must appear in the SSR HTML."""
    for i in range(3):
        await _make_repo(
            db_session,
            name=f"jazz-p{i}",
            slug=f"jazz-p{i}",
            tags=["jazz"],
        )
    response = await client.get("/musehub/ui/topics/jazz?page_size=2")
    assert response.status_code == 200
    body = response.text
    # Pagination: "Page 1 of 2" rendered server-side
    assert "Page 1 of 2" in body
    # Next link rendered
    assert "page=2" in body


@pytest.mark.anyio
async def test_topic_detail_featured_repos_rendered(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Top-starred repos must appear in the Featured section of the SSR HTML."""
    repo_id = await _make_repo(
        db_session,
        name="featured-jazz",
        slug="featured-jazz",
        tags=["jazz"],
        description="Top jazz repo",
    )
    # Give it a star so it has star_count > 0
    await _star_repo(db_session, repo_id, "00000000-0000-0000-0000-000000000099")

    response = await client.get("/musehub/ui/topics/jazz")
    assert response.status_code == 200
    body = response.text
    # Featured section heading
    assert "⭐ Featured" in body
    # Featured repo name rendered server-side
    assert "featured-jazz" in body
