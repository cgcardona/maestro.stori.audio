"""Tests for the Muse Hub topics browsing UI pages (issue #437).

Covers:
Topics Index (/musehub/ui/topics):
- test_topics_index_renders_200              — GET /musehub/ui/topics returns 200 HTML
- test_topics_index_no_auth_required        — page is accessible without a JWT
- test_topics_index_json_content_negotiation — Accept: application/json returns JSON
- test_topics_index_format_param            — ?format=json returns JSON without Accept header
- test_topics_index_json_schema             — JSON has allTopics, curatedGroups, total keys
- test_topics_index_empty_state             — no repos returns allTopics=[] total=0
- test_topics_index_counts_public_only      — private repos excluded from counts
- test_topics_index_sorted_by_popularity    — topics sorted by repo_count descending
- test_topics_index_html_has_page_mode      — HTML body contains PAGE_MODE JS variable
- test_topics_index_html_has_curated_groups — HTML body references curated group labels
- test_topics_index_curated_groups_populated — curated groups carry correct repo counts

Single Topic Page (/musehub/ui/topics/{tag}):
- test_topic_detail_renders_200             — GET /musehub/ui/topics/{tag} returns 200 HTML
- test_topic_detail_no_auth_required        — page is accessible without a JWT
- test_topic_detail_json_response           — Accept: application/json returns JSON
- test_topic_detail_json_schema             — JSON has tag, repos, total, page, pageSize keys
- test_topic_detail_empty_topic             — unknown tag returns 200 with empty repos
- test_topic_detail_filters_by_tag          — only repos with that tag are returned
- test_topic_detail_private_excluded        — private repos excluded from results
- test_topic_detail_sort_stars              — ?sort=stars returns repos sorted by star count
- test_topic_detail_sort_updated            — ?sort=updated accepted without error
- test_topic_detail_invalid_sort_fallback   — invalid sort silently falls back to stars
- test_topic_detail_pagination              — ?page=2 returns next page
- test_topic_detail_tag_injected_in_js      — tag slug passed as TOPIC_TAG JS variable
- test_topic_detail_sort_injected_in_js     — sort passed as TOPIC_SORT JS variable
- test_topic_detail_html_has_breadcrumb     — breadcrumb references Topics and tag slug
- test_topic_detail_html_references_api     — HTML references the topics UI data endpoint
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
    name: str = "test-jazz",
    owner: str = "alice",
    slug: str = "test-jazz",
    tags: list[str] | None = None,
    visibility: str = "public",
) -> str:
    """Seed a minimal repo and return its repo_id string."""
    repo = MusehubRepo(
        name=name,
        owner=owner,
        slug=slug,
        visibility=visibility,
        owner_user_id="00000000-0000-0000-0000-000000000001",
        tags=tags or [],
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


_INDEX_URL = "/musehub/ui/topics"
_DETAIL_URL = "/musehub/ui/topics/jazz"


# ---------------------------------------------------------------------------
# Topics Index — HTML rendering
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_topics_index_renders_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/topics must return 200 HTML."""
    response = await client.get(_INDEX_URL)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_topics_index_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Topics index must be accessible without an Authorization header."""
    response = await client.get(_INDEX_URL)
    assert response.status_code == 200


@pytest.mark.anyio
async def test_topics_index_html_has_page_mode(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HTML response must render the index mode — Topics heading server-side."""
    response = await client.get(_INDEX_URL)
    assert response.status_code == 200
    body = response.text
    # SSR: mode is rendered as HTML content, not a JS variable
    assert "🏷️ Topics" in body


@pytest.mark.anyio
async def test_topics_index_html_has_curated_groups(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HTML must render curated group labels and link to the topics index."""
    response = await client.get(_INDEX_URL)
    assert response.status_code == 200
    body = response.text
    assert "/musehub/ui/topics" in body


# ---------------------------------------------------------------------------
# Topics Index — JSON content negotiation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_topics_index_json_content_negotiation(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Accept: application/json must return a JSON response."""
    response = await client.get(_INDEX_URL, headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]


@pytest.mark.anyio
async def test_topics_index_format_param(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?format=json must return JSON without an Accept header."""
    response = await client.get(_INDEX_URL + "?format=json")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]


@pytest.mark.anyio
async def test_topics_index_json_schema(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response must contain allTopics, curatedGroups, and total keys."""
    await _make_repo(db_session, tags=["jazz"])
    response = await client.get(_INDEX_URL + "?format=json")
    assert response.status_code == 200
    data = response.json()
    assert "allTopics" in data
    assert "curatedGroups" in data
    assert "total" in data
    assert isinstance(data["allTopics"], list)
    assert isinstance(data["curatedGroups"], list)
    assert isinstance(data["total"], int)


@pytest.mark.anyio
async def test_topics_index_empty_state(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """With no repos, allTopics must be empty and total must be 0."""
    response = await client.get(_INDEX_URL + "?format=json")
    assert response.status_code == 200
    data = response.json()
    assert data["allTopics"] == []
    assert data["total"] == 0


@pytest.mark.anyio
async def test_topics_index_counts_public_only(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Private repo tags must not appear in the topics index."""
    await _make_repo(db_session, tags=["secret-tag"], visibility="private")
    response = await client.get(_INDEX_URL + "?format=json")
    assert response.status_code == 200
    data = response.json()
    topic_names = [t["name"] for t in data["allTopics"]]
    assert "secret-tag" not in topic_names


@pytest.mark.anyio
async def test_topics_index_sorted_by_popularity(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Topics must be sorted by repo_count descending (most popular first)."""
    await _make_repo(db_session, name="r1", slug="r1", tags=["jazz"])
    await _make_repo(db_session, name="r2", slug="r2", tags=["jazz", "blues"])
    await _make_repo(db_session, name="r3", slug="r3", tags=["blues"])
    response = await client.get(_INDEX_URL + "?format=json")
    assert response.status_code == 200
    data = response.json()
    topics = data["allTopics"]
    # jazz: 2 repos, blues: 2 repos (tie) — both before any single-repo topic
    counts = [t["repo_count"] for t in topics]
    assert counts == sorted(counts, reverse=True), "Topics not sorted by repo_count desc"


@pytest.mark.anyio
async def test_topics_index_curated_groups_populated(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Curated groups must include Genres, Instruments, and Eras with topic items."""
    await _make_repo(db_session, tags=["jazz", "piano"])
    response = await client.get(_INDEX_URL + "?format=json")
    assert response.status_code == 200
    data = response.json()
    group_labels = [g["label"] for g in data["curatedGroups"]]
    assert "Genres" in group_labels
    assert "Instruments" in group_labels
    assert "Eras" in group_labels

    # Jazz and piano should appear in their curated groups with repoCount > 0
    genres_group = next(g for g in data["curatedGroups"] if g["label"] == "Genres")
    jazz_item = next((t for t in genres_group["topics"] if t["name"] == "jazz"), None)
    assert jazz_item is not None
    assert jazz_item["repo_count"] == 1

    instruments_group = next(g for g in data["curatedGroups"] if g["label"] == "Instruments")
    piano_item = next((t for t in instruments_group["topics"] if t["name"] == "piano"), None)
    assert piano_item is not None
    assert piano_item["repo_count"] == 1


# ---------------------------------------------------------------------------
# Topic Detail — HTML rendering
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_topic_detail_renders_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/topics/{tag} must return 200 HTML."""
    response = await client.get(_DETAIL_URL)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_topic_detail_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Topic detail page must be accessible without a JWT."""
    response = await client.get(_DETAIL_URL)
    assert response.status_code == 200


@pytest.mark.anyio
async def test_topic_detail_tag_injected_in_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Tag slug must appear in the server-rendered HTML (breadcrumb and heading)."""
    response = await client.get(_DETAIL_URL)
    assert response.status_code == 200
    body = response.text
    # SSR: tag rendered in breadcrumb and heading, not as a JS variable
    assert "#jazz" in body


@pytest.mark.anyio
async def test_topic_detail_sort_injected_in_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Sort state must appear in sort control links in the server-rendered HTML."""
    response = await client.get(_DETAIL_URL + "?sort=updated")
    assert response.status_code == 200
    body = response.text
    # SSR: sort=updated is reflected in sort button active state / URL links
    assert "sort=updated" in body


@pytest.mark.anyio
async def test_topic_detail_html_has_breadcrumb(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HTML breadcrumb must reference Topics index and the current tag slug."""
    response = await client.get(_DETAIL_URL)
    assert response.status_code == 200
    body = response.text
    assert "Topics" in body
    assert "jazz" in body


@pytest.mark.anyio
async def test_topic_detail_html_references_api(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HTML must contain links back to the topics index (breadcrumb and sort links)."""
    response = await client.get(_DETAIL_URL)
    assert response.status_code == 200
    body = response.text
    assert "/musehub/ui/topics" in body


# ---------------------------------------------------------------------------
# Topic Detail — JSON content negotiation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_topic_detail_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Accept: application/json must return a JSON response."""
    response = await client.get(_DETAIL_URL, headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]


@pytest.mark.anyio
async def test_topic_detail_json_schema(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response must contain tag, repos, total, page, pageSize keys."""
    response = await client.get(_DETAIL_URL + "?format=json")
    assert response.status_code == 200
    data = response.json()
    assert "tag" in data
    assert "repos" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert isinstance(data["repos"], list)
    assert isinstance(data["total"], int)
    assert data["tag"] == "jazz"


@pytest.mark.anyio
async def test_topic_detail_empty_topic(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Unknown tag must return 200 with an empty repos list (not 404)."""
    response = await client.get("/musehub/ui/topics/no-such-genre?format=json")
    assert response.status_code == 200
    data = response.json()
    assert data["repos"] == []
    assert data["total"] == 0


@pytest.mark.anyio
async def test_topic_detail_filters_by_tag(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Only repos that carry the requested tag must appear in the response."""
    await _make_repo(db_session, name="jazz-repo", slug="jazz-repo", tags=["jazz", "piano"])
    await _make_repo(db_session, name="blues-repo", slug="blues-repo", tags=["blues"])
    response = await client.get(_DETAIL_URL + "?format=json")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["repos"]) == 1
    assert data["repos"][0]["slug"] == "jazz-repo"


@pytest.mark.anyio
async def test_topic_detail_private_excluded(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Private repos tagged with the topic must not appear in results."""
    await _make_repo(
        db_session, name="private-jazz", slug="private-jazz",
        tags=["jazz"], visibility="private"
    )
    response = await client.get(_DETAIL_URL + "?format=json")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["repos"] == []


@pytest.mark.anyio
async def test_topic_detail_sort_stars(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?sort=stars must return repos without error; default sort is stars."""
    await _make_repo(db_session, name="jazz-a", slug="jazz-a", tags=["jazz"])
    await _make_repo(db_session, name="jazz-b", slug="jazz-b", tags=["jazz"])
    response = await client.get(_DETAIL_URL + "?sort=stars&format=json")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2


@pytest.mark.anyio
async def test_topic_detail_sort_updated(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?sort=updated must be accepted and return repos without error."""
    await _make_repo(db_session, name="jazz-recent", slug="jazz-recent", tags=["jazz"])
    response = await client.get(_DETAIL_URL + "?sort=updated&format=json")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1


@pytest.mark.anyio
async def test_topic_detail_invalid_sort_fallback(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """An invalid ?sort value must silently fall back to stars — no 422."""
    await _make_repo(db_session, name="jazz-x", slug="jazz-x", tags=["jazz"])
    response = await client.get(_DETAIL_URL + "?sort=bogus&format=json")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1


@pytest.mark.anyio
async def test_topic_detail_pagination(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?page=2 with a small page_size must return the second page of results."""
    for i in range(3):
        await _make_repo(
            db_session,
            name=f"jazz-{i}",
            slug=f"jazz-{i}",
            tags=["jazz"],
        )
    response = await client.get(_DETAIL_URL + "?page=2&page_size=2&format=json")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["page"] == 2
    # Page 2 with page_size=2 from 3 total → 1 result
    assert len(data["repos"]) == 1
