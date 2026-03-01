"""Tests for the Muse Hub topics/tag browse API endpoints.

Covers acceptance criteria from issue #408:
- test_list_topics_empty                   — no public repos → empty topics list
- test_list_topics_aggregates_counts       — counts reflect public repos only
- test_list_topics_excludes_private_repos  — private repo tags are not counted
- test_list_topics_sorted_by_count_desc    — most popular topic appears first
- test_repos_by_topic_empty               — unknown tag → empty list (not 404)
- test_repos_by_topic_returns_tagged_repos — only repos with exact tag returned
- test_repos_by_topic_excludes_private     — private repos are hidden
- test_repos_by_topic_sort_by_stars        — stars sort returns most-starred first
- test_repos_by_topic_sort_by_updated      — updated sort returns most-recently-committed first
- test_repos_by_topic_invalid_sort         — invalid sort param returns 422
- test_repos_by_topic_pagination           — page 2 returns different repos
- test_set_topics_requires_auth            — POST without JWT returns 401
- test_set_topics_owner_only               — non-owner gets 403
- test_set_topics_replaces_list            — new list replaces old list entirely
- test_set_topics_deduplicates             — duplicate slugs are collapsed
- test_set_topics_invalid_slug             — bad slug characters return 422
- test_set_topics_too_many                 — more than 20 topics returns 422
- test_set_topics_clears_list              — empty body clears all topics
- test_set_topics_repo_not_found           — unknown repo_id returns 404
"""
from __future__ import annotations

import re

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubCommit, MusehubRepo, MusehubStar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(
    db_session: AsyncSession,
    *,
    name: str,
    visibility: str = "public",
    tags: list[str] | None = None,
    owner: str = "testuser",
    owner_user_id: str = "test-owner",
) -> str:
    """Seed a repo and return its repo_id."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64].strip("-") or "repo"
    repo = MusehubRepo(
        name=name,
        owner=owner,
        slug=f"{slug}-{visibility[:3]}",
        visibility=visibility,
        owner_user_id=owner_user_id,
        description="",
        tags=tags or [],
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


async def _add_star(db_session: AsyncSession, repo_id: str, user_id: str) -> None:
    star = MusehubStar(repo_id=repo_id, user_id=user_id)
    db_session.add(star)
    await db_session.commit()


async def _add_commit(
    db_session: AsyncSession,
    repo_id: str,
    *,
    sha: str,
    timestamp: str,
) -> None:
    from datetime import datetime, timezone
    commit = MusehubCommit(
        commit_id=sha,
        repo_id=repo_id,
        branch="main",
        author="tester",
        message="test commit",
        timestamp=datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc),
        parent_ids=[],
    )
    db_session.add(commit)
    await db_session.commit()


# ---------------------------------------------------------------------------
# GET /api/v1/musehub/topics
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_topics_empty(client: AsyncClient) -> None:
    """No public repos → topics list is empty."""
    response = await client.get("/api/v1/musehub/topics")
    assert response.status_code == 200
    assert response.json() == {"topics": []}


@pytest.mark.anyio
async def test_list_topics_aggregates_counts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Topics are aggregated across all public repos with correct counts."""
    await _make_repo(db_session, name="repo-a", tags=["jazz", "piano"])
    await _make_repo(db_session, name="repo-b", tags=["jazz", "ambient"])
    await _make_repo(db_session, name="repo-c", tags=["ambient"])

    response = await client.get("/api/v1/musehub/topics")
    assert response.status_code == 200

    topics = {t["name"]: t["repo_count"] for t in response.json()["topics"]}
    assert topics["jazz"] == 2
    assert topics["ambient"] == 2
    assert topics["piano"] == 1


@pytest.mark.anyio
async def test_list_topics_excludes_private_repos(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Private repo tags do not contribute to topic counts."""
    await _make_repo(db_session, name="pub-jazz", tags=["jazz"], visibility="public")
    await _make_repo(db_session, name="priv-jazz", tags=["jazz", "secret-tag"], visibility="private")

    response = await client.get("/api/v1/musehub/topics")
    assert response.status_code == 200

    topics = {t["name"]: t["repo_count"] for t in response.json()["topics"]}
    assert topics.get("jazz") == 1  # only the public repo
    assert "secret-tag" not in topics


@pytest.mark.anyio
async def test_list_topics_sorted_by_count_desc(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Topics are sorted by repo_count descending — most popular first."""
    await _make_repo(db_session, name="r1", tags=["baroque"])
    await _make_repo(db_session, name="r2", tags=["jazz", "baroque"])
    await _make_repo(db_session, name="r3", tags=["jazz", "baroque"])

    response = await client.get("/api/v1/musehub/topics")
    assert response.status_code == 200

    topics = response.json()["topics"]
    assert topics[0]["name"] == "baroque"  # 3 repos
    assert topics[1]["name"] == "jazz"  # 2 repos


# ---------------------------------------------------------------------------
# GET /api/v1/musehub/topics/{tag}/repos
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_repos_by_topic_empty(client: AsyncClient) -> None:
    """Unknown/unused tag → empty repos list, not 404."""
    response = await client.get("/api/v1/musehub/topics/nonexistent-tag/repos")
    assert response.status_code == 200
    body = response.json()
    assert body["repos"] == []
    assert body["total"] == 0
    assert body["tag"] == "nonexistent-tag"


@pytest.mark.anyio
async def test_repos_by_topic_returns_tagged_repos(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Only repos with the exact tag are returned."""
    await _make_repo(db_session, name="jazz-repo", tags=["jazz", "piano"])
    await _make_repo(db_session, name="piano-only-repo", tags=["piano"])
    await _make_repo(db_session, name="unrelated-repo", tags=["ambient"])

    response = await client.get("/api/v1/musehub/topics/jazz/repos")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["tag"] == "jazz"
    assert body["repos"][0]["name"] == "jazz-repo"


@pytest.mark.anyio
async def test_repos_by_topic_excludes_private(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Private repos are not exposed even when they carry the tag."""
    await _make_repo(db_session, name="pub", tags=["classical"], visibility="public")
    await _make_repo(db_session, name="priv", tags=["classical"], visibility="private")

    response = await client.get("/api/v1/musehub/topics/classical/repos")
    assert response.status_code == 200
    assert response.json()["total"] == 1  # only the public repo


@pytest.mark.anyio
async def test_repos_by_topic_sort_by_stars(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """sort=stars returns most-starred repo first."""
    id_low = await _make_repo(db_session, name="low-star", tags=["edm"])
    id_high = await _make_repo(db_session, name="high-star", tags=["edm"])

    await _add_star(db_session, id_high, "user1")
    await _add_star(db_session, id_high, "user2")
    await _add_star(db_session, id_low, "user3")

    response = await client.get("/api/v1/musehub/topics/edm/repos?sort=stars")
    assert response.status_code == 200
    names = [r["name"] for r in response.json()["repos"]]
    assert names.index("high-star") < names.index("low-star")


@pytest.mark.anyio
async def test_repos_by_topic_sort_by_updated(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """sort=updated returns most-recently-committed repo first."""
    id_old = await _make_repo(db_session, name="old-commits", tags=["ambient"])
    id_new = await _make_repo(db_session, name="new-commits", tags=["ambient"])

    await _add_commit(db_session, id_old, sha="sha-old", timestamp="2023-01-01T00:00:00")
    await _add_commit(db_session, id_new, sha="sha-new", timestamp="2024-06-01T00:00:00")

    response = await client.get("/api/v1/musehub/topics/ambient/repos?sort=updated")
    assert response.status_code == 200
    names = [r["name"] for r in response.json()["repos"]]
    assert names.index("new-commits") < names.index("old-commits")


@pytest.mark.anyio
async def test_repos_by_topic_invalid_sort(client: AsyncClient, db_session: AsyncSession) -> None:
    """Invalid sort parameter returns 422."""
    await _make_repo(db_session, name="any-repo", tags=["jazz"])
    response = await client.get("/api/v1/musehub/topics/jazz/repos?sort=invalid")
    assert response.status_code == 422


@pytest.mark.anyio
async def test_repos_by_topic_pagination(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Pagination works: page 2 returns a different set of repos."""
    for i in range(5):
        await _make_repo(db_session, name=f"cinematic-{i}", tags=["cinematic"])

    page1 = await client.get("/api/v1/musehub/topics/cinematic/repos?page=1&page_size=2")
    page2 = await client.get("/api/v1/musehub/topics/cinematic/repos?page=2&page_size=2")

    assert page1.status_code == 200
    assert page2.status_code == 200
    ids1 = {r["repoId"] for r in page1.json()["repos"]}
    ids2 = {r["repoId"] for r in page2.json()["repos"]}
    assert ids1.isdisjoint(ids2)
    assert page1.json()["total"] == 5


# ---------------------------------------------------------------------------
# POST /api/v1/musehub/repos/{repo_id}/topics
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_topics_requires_auth(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST without a JWT returns 401."""
    repo_id = await _make_repo(db_session, name="auth-test")
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/topics",
        json={"topics": ["jazz"]},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_set_topics_owner_only(
    client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
) -> None:
    """A user who is not the repo owner receives 403."""
    repo_id = await _make_repo(db_session, name="owned-elsewhere", owner_user_id="different-owner")
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/topics",
        json={"topics": ["jazz"]},
        headers=auth_headers,
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_set_topics_replaces_list(
    client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
) -> None:
    """Posting a new list replaces the existing tags entirely."""
    repo_id = await _make_repo(
        db_session,
        name="replace-me",
        tags=["old-tag"],
        owner_user_id="550e8400-e29b-41d4-a716-446655440000",
    )
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/topics",
        json={"topics": ["jazz", "piano"]},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["repo_id"] == repo_id
    assert body["topics"] == ["jazz", "piano"]


@pytest.mark.anyio
async def test_set_topics_deduplicates(
    client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
) -> None:
    """Duplicate topic slugs in the request are silently collapsed."""
    repo_id = await _make_repo(
        db_session,
        name="dedup-test",
        owner_user_id="550e8400-e29b-41d4-a716-446655440000",
    )
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/topics",
        json={"topics": ["jazz", "jazz", "piano", "jazz"]},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["topics"] == ["jazz", "piano"]


@pytest.mark.anyio
async def test_set_topics_invalid_slug(
    client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
) -> None:
    """Topic slugs with invalid characters return 422."""
    repo_id = await _make_repo(
        db_session,
        name="slug-test",
        owner_user_id="550e8400-e29b-41d4-a716-446655440000",
    )
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/topics",
        json={"topics": ["Valid-slug", "BAD SLUG!", "ok-slug"]},
        headers=auth_headers,
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_set_topics_too_many(
    client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
) -> None:
    """Submitting more than 20 topics returns 422."""
    repo_id = await _make_repo(
        db_session,
        name="too-many",
        owner_user_id="550e8400-e29b-41d4-a716-446655440000",
    )
    many_topics = [f"topic-{i}" for i in range(21)]
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/topics",
        json={"topics": many_topics},
        headers=auth_headers,
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_set_topics_clears_list(
    client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
) -> None:
    """Sending an empty list removes all topics."""
    repo_id = await _make_repo(
        db_session,
        name="clear-me",
        tags=["jazz", "piano"],
        owner_user_id="550e8400-e29b-41d4-a716-446655440000",
    )
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/topics",
        json={"topics": []},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["topics"] == []


@pytest.mark.anyio
async def test_set_topics_repo_not_found(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Unknown repo_id returns 404."""
    response = await client.post(
        "/api/v1/musehub/repos/nonexistent-repo-id/topics",
        json={"topics": ["jazz"]},
        headers=auth_headers,
    )
    assert response.status_code == 404
