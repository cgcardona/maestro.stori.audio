"""Tests for the Muse Hub explore/discover API endpoints.

Covers acceptance criteria from issue #234:
- test_explore_page_renders             — GET /musehub/ui/explore returns 200 HTML
- test_trending_page_renders            — GET /musehub/ui/trending returns 200 HTML
- test_list_public_repos_empty          — no public repos → empty list
- test_explore_only_public_repos        — private repos are excluded from results
- test_explore_filters_by_genre         — genre tag filter works
- test_explore_filters_by_key           — key_signature exact filter works
- test_explore_filters_by_tempo         — tempo_min/tempo_max range filter works
- test_explore_filters_by_instrumentation — instrumentation tag filter works
- test_explore_sorts_by_stars           — star-count sort returns highest-starred first
- test_explore_sorts_by_created         — created sort returns newest first
- test_explore_pagination               — page 2 returns different repos
- test_star_repo_requires_auth          — POST /star returns 401 without JWT
- test_star_repo_adds_star              — star increments star_count
- test_star_repo_idempotent             — duplicate star is silent
- test_unstar_repo_removes_star         — unstar decrements star_count
- test_unstar_repo_idempotent           — unstarring twice is a no-op
- test_star_private_repo_returns_404    — cannot star a private repo
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo, MusehubStar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_public_repo(
    db_session: AsyncSession,
    *,
    name: str = "test-jazz-repo",
    tags: list[str] | None = None,
    key_signature: str | None = None,
    tempo_bpm: int | None = None,
    description: str = "",
) -> str:
    """Seed a public repo and return its repo_id."""
    repo = MusehubRepo(
        name=name,
        visibility="public",
        owner_user_id="test-owner",
        description=description,
        tags=tags or [],
        key_signature=key_signature,
        tempo_bpm=tempo_bpm,
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


async def _make_private_repo(db_session: AsyncSession, name: str = "private-beats") -> str:
    """Seed a private repo and return its repo_id."""
    repo = MusehubRepo(
        name=name,
        visibility="private",
        owner_user_id="test-owner",
        description="",
        tags=[],
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


# ---------------------------------------------------------------------------
# UI page tests (no auth required)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_explore_page_renders(client: AsyncClient) -> None:
    """GET /musehub/ui/explore returns 200 HTML with filter controls."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Explore" in body
    # Filter controls must be present
    assert "genre-inp" in body
    assert "key-inp" in body
    assert "tempo-min" in body
    assert "sort-sel" in body
    # Discover API endpoint must be referenced
    assert "discover/repos" in body


@pytest.mark.anyio
async def test_trending_page_renders(client: AsyncClient) -> None:
    """GET /musehub/ui/trending returns 200 HTML with stars sort pre-selected."""
    response = await client.get("/musehub/ui/trending")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Trending" in body
    # Stars sort option must be pre-selected on the trending page
    assert 'value="stars" selected' in body or "selected" in body
    assert "discover/repos" in body


@pytest.mark.anyio
async def test_explore_page_no_auth_required(client: AsyncClient) -> None:
    """GET /musehub/ui/explore must not return 401 — it is a public page."""
    response = await client.get("/musehub/ui/explore")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# JSON API tests — public browse endpoint (no auth)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_public_repos_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /api/v1/musehub/discover/repos returns empty list when no public repos exist."""
    response = await client.get("/api/v1/musehub/discover/repos")
    assert response.status_code == 200
    body = response.json()
    assert body["repos"] == []
    assert body["total"] == 0
    assert body["page"] == 1
    assert body["pageSize"] == 24


@pytest.mark.anyio
async def test_explore_only_public_repos(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Private repos must not appear in discover results."""
    await _make_public_repo(db_session, name="public-one")
    await _make_private_repo(db_session, name="private-one")

    response = await client.get("/api/v1/musehub/discover/repos")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    names = [r["name"] for r in body["repos"]]
    assert "public-one" in names
    assert "private-one" not in names


@pytest.mark.anyio
async def test_explore_filters_by_genre(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """genre= filter returns only repos whose tags contain the genre string."""
    await _make_public_repo(db_session, name="jazz-project", tags=["jazz", "swing"])
    await _make_public_repo(db_session, name="lofi-project", tags=["lo-fi", "chill"])

    response = await client.get("/api/v1/musehub/discover/repos?genre=jazz")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["repos"][0]["name"] == "jazz-project"


@pytest.mark.anyio
async def test_explore_filters_by_key(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """key= filter returns only repos with the matching key_signature."""
    await _make_public_repo(db_session, name="fsharp-minor", key_signature="F# minor")
    await _make_public_repo(db_session, name="c-major", key_signature="C major")

    response = await client.get("/api/v1/musehub/discover/repos?key=F%23+minor")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["repos"][0]["name"] == "fsharp-minor"


@pytest.mark.anyio
async def test_explore_filters_by_tempo(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """tempo_min and tempo_max filter repos by BPM range."""
    await _make_public_repo(db_session, name="slow", tempo_bpm=70)
    await _make_public_repo(db_session, name="mid", tempo_bpm=100)
    await _make_public_repo(db_session, name="fast", tempo_bpm=150)

    response = await client.get("/api/v1/musehub/discover/repos?tempo_min=90&tempo_max=120")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["repos"][0]["name"] == "mid"


@pytest.mark.anyio
async def test_explore_filters_by_instrumentation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """instrumentation= filter matches repos whose tags include the instrument."""
    await _make_public_repo(db_session, name="bass-heavy", tags=["jazz", "bass", "drums"])
    await _make_public_repo(db_session, name="keys-only", tags=["ambient", "keys"])

    response = await client.get("/api/v1/musehub/discover/repos?instrumentation=bass")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["repos"][0]["name"] == "bass-heavy"


@pytest.mark.anyio
async def test_explore_sorts_by_stars(
    client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
) -> None:
    """sort=stars returns the repo with more stars first."""
    repo_a = await _make_public_repo(db_session, name="repo-a")
    repo_b = await _make_public_repo(db_session, name="repo-b")

    # Star repo_b twice (from different users) so it has more stars
    star1 = MusehubStar(repo_id=repo_b, user_id="user-1")
    star2 = MusehubStar(repo_id=repo_b, user_id="user-2")
    star3 = MusehubStar(repo_id=repo_a, user_id="user-3")
    db_session.add_all([star1, star2, star3])
    await db_session.commit()

    response = await client.get("/api/v1/musehub/discover/repos?sort=stars")
    assert response.status_code == 200
    body = response.json()
    repos = body["repos"]
    assert len(repos) == 2
    # repo-b has 2 stars; must come first
    assert repos[0]["name"] == "repo-b"
    assert repos[0]["starCount"] == 2
    assert repos[1]["name"] == "repo-a"
    assert repos[1]["starCount"] == 1


@pytest.mark.anyio
async def test_explore_sorts_by_created(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """sort=created returns newest repos first (default sort)."""
    await _make_public_repo(db_session, name="first-created")
    await _make_public_repo(db_session, name="second-created")

    response = await client.get("/api/v1/musehub/discover/repos?sort=created")
    assert response.status_code == 200
    body = response.json()
    # Newest first — second-created was inserted last
    names = [r["name"] for r in body["repos"]]
    assert names.index("second-created") < names.index("first-created")


@pytest.mark.anyio
async def test_explore_pagination(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Page 2 returns a different set of repos than page 1."""
    for i in range(5):
        await _make_public_repo(db_session, name=f"repo-{i:02d}")

    page1 = (await client.get("/api/v1/musehub/discover/repos?page=1&page_size=3")).json()
    page2 = (await client.get("/api/v1/musehub/discover/repos?page=2&page_size=3")).json()

    assert page1["total"] == 5
    assert page2["total"] == 5
    page1_ids = {r["repoId"] for r in page1["repos"]}
    page2_ids = {r["repoId"] for r in page2["repos"]}
    # Pages must not overlap
    assert not page1_ids & page2_ids


@pytest.mark.anyio
async def test_explore_invalid_sort_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """sort= with an invalid value returns 422 Unprocessable Entity."""
    response = await client.get("/api/v1/musehub/discover/repos?sort=invalid")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Star / unstar tests (auth required)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_star_repo_requires_auth(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /api/v1/musehub/repos/{repo_id}/star returns 401 without a JWT."""
    repo_id = await _make_public_repo(db_session)
    response = await client.post(f"/api/v1/musehub/repos/{repo_id}/star")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_star_repo_adds_star(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /api/v1/musehub/repos/{repo_id}/star returns starred=True and correct count."""
    repo_id = await _make_public_repo(db_session)
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/star",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["starred"] is True
    assert body["starCount"] == 1


@pytest.mark.anyio
async def test_star_repo_idempotent(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Starring the same repo twice does not create duplicate stars."""
    repo_id = await _make_public_repo(db_session)
    await client.post(f"/api/v1/musehub/repos/{repo_id}/star", headers=auth_headers)
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/star", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["starCount"] == 1


@pytest.mark.anyio
async def test_unstar_repo_removes_star(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """DELETE .../star after starring reduces star_count to 0."""
    repo_id = await _make_public_repo(db_session)
    await client.post(f"/api/v1/musehub/repos/{repo_id}/star", headers=auth_headers)

    response = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/star", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["starred"] is False
    assert body["starCount"] == 0


@pytest.mark.anyio
async def test_unstar_repo_idempotent(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Unstarring a repo that was never starred returns 200 with star_count=0."""
    repo_id = await _make_public_repo(db_session)
    response = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/star", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["starCount"] == 0


@pytest.mark.anyio
async def test_star_private_repo_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """POST /star on a private repo returns 404 — private repos cannot be starred."""
    repo_id = await _make_private_repo(db_session)
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/star", headers=auth_headers
    )
    assert response.status_code == 404
