"""Tests for Muse Hub repo, branch, and commit endpoints.

Covers every acceptance criterion from issue #39:
- POST /musehub/repos returns 201 with correct fields
- POST requires auth — unauthenticated requests return 401
- GET /musehub/repos/{repo_id} returns 200; 404 for unknown repo
- GET /musehub/repos/{repo_id}/branches returns empty list on new repo
- GET /musehub/repos/{repo_id}/commits returns newest first, respects ?limit

All tests use the shared ``client`` and ``auth_headers`` fixtures from conftest.py.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubCommit, MusehubRepo
from maestro.services import musehub_repository


# ---------------------------------------------------------------------------
# POST /musehub/repos
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_repo_returns_201(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /musehub/repos creates a repo and returns all required fields."""
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "my-beats", "visibility": "private"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "my-beats"
    assert body["visibility"] == "private"
    assert "repoId" in body
    assert "cloneUrl" in body
    assert "ownerUserId" in body
    assert "createdAt" in body


@pytest.mark.anyio
async def test_create_repo_requires_auth(client: AsyncClient) -> None:
    """POST /musehub/repos returns 401 without a Bearer token."""
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "my-beats"},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_create_repo_default_visibility_is_private(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Omitting visibility defaults to 'private'."""
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "silent-sessions"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    assert response.json()["visibility"] == "private"


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_repo_returns_200(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /musehub/repos/{repo_id} returns the repo after creation."""
    create = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "jazz-sessions"},
        headers=auth_headers,
    )
    assert create.status_code == 201
    repo_id = create.json()["repoId"]

    response = await client.get(f"/api/v1/musehub/repos/{repo_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["repoId"] == repo_id
    assert response.json()["name"] == "jazz-sessions"


@pytest.mark.anyio
async def test_get_repo_not_found_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /musehub/repos/{repo_id} returns 404 for unknown repo."""
    response = await client.get(
        "/api/v1/musehub/repos/does-not-exist",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_repo_requires_auth(client: AsyncClient) -> None:
    """GET /musehub/repos/{repo_id} returns 401 without auth."""
    response = await client.get("/api/v1/musehub/repos/any-id")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/branches
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_branches_empty_on_new_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A newly created repo has an empty branches list."""
    create = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "drum-patterns"},
        headers=auth_headers,
    )
    repo_id = create.json()["repoId"]

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/branches",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["branches"] == []


@pytest.mark.anyio
async def test_list_branches_not_found_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /branches returns 404 when the repo doesn't exist."""
    response = await client.get(
        "/api/v1/musehub/repos/ghost-repo/branches",
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/commits
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_commits_empty_on_new_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A new repo has no commits."""
    create = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "empty-repo"},
        headers=auth_headers,
    )
    repo_id = create.json()["repoId"]

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["commits"] == []
    assert body["total"] == 0


@pytest.mark.anyio
async def test_list_commits_returns_newest_first(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Commits are returned newest-first after being pushed."""
    from datetime import datetime, timezone, timedelta

    # Create repo via API
    create = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "ordered-commits"},
        headers=auth_headers,
    )
    repo_id = create.json()["repoId"]

    # Insert two commits directly with known timestamps
    now = datetime.now(tz=timezone.utc)
    older = MusehubCommit(
        commit_id="aaa111",
        repo_id=repo_id,
        branch="main",
        parent_ids=[],
        message="first",
        author="gabriel",
        timestamp=now - timedelta(hours=1),
    )
    newer = MusehubCommit(
        commit_id="bbb222",
        repo_id=repo_id,
        branch="main",
        parent_ids=["aaa111"],
        message="second",
        author="gabriel",
        timestamp=now,
    )
    db_session.add_all([older, newer])
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits",
        headers=auth_headers,
    )
    assert response.status_code == 200
    commits = response.json()["commits"]
    assert len(commits) == 2
    assert commits[0]["commitId"] == "bbb222"
    assert commits[1]["commitId"] == "aaa111"


@pytest.mark.anyio
async def test_list_commits_limit_param(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """?limit=1 returns exactly 1 commit."""
    from datetime import datetime, timezone, timedelta

    create = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "limited-repo"},
        headers=auth_headers,
    )
    repo_id = create.json()["repoId"]

    now = datetime.now(tz=timezone.utc)
    for i in range(3):
        db_session.add(
            MusehubCommit(
                commit_id=f"commit-{i}",
                repo_id=repo_id,
                branch="main",
                parent_ids=[],
                message=f"commit {i}",
                author="gabriel",
                timestamp=now + timedelta(seconds=i),
            )
        )
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/commits?limit=1",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["commits"]) == 1
    assert body["total"] == 3


# ---------------------------------------------------------------------------
# Service layer — direct DB tests (no HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_repo_service_persists_to_db(db_session: AsyncSession) -> None:
    """musehub_repository.create_repo() persists the row."""
    repo = await musehub_repository.create_repo(
        db_session,
        name="service-test-repo",
        visibility="public",
        owner_user_id="user-abc",
    )
    await db_session.commit()

    fetched = await musehub_repository.get_repo(db_session, repo.repo_id)
    assert fetched is not None
    assert fetched.name == "service-test-repo"
    assert fetched.visibility == "public"


@pytest.mark.anyio
async def test_get_repo_returns_none_when_missing(db_session: AsyncSession) -> None:
    """get_repo() returns None for an unknown repo_id."""
    result = await musehub_repository.get_repo(db_session, "nonexistent-id")
    assert result is None


@pytest.mark.anyio
async def test_list_branches_returns_empty_for_new_repo(db_session: AsyncSession) -> None:
    """list_branches() returns [] for a repo with no branches."""
    repo = await musehub_repository.create_repo(
        db_session,
        name="branchless",
        visibility="private",
        owner_user_id="user-x",
    )
    await db_session.commit()
    branches = await musehub_repository.list_branches(db_session, repo.repo_id)
    assert branches == []


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/timeline
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_timeline_data_endpoint_empty_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /musehub/repos/{repo_id}/timeline returns empty event streams for new repo."""
    create = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "empty-timeline"},
        headers=auth_headers,
    )
    assert create.status_code == 201
    repo_id = create.json()["repoId"]

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/timeline",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["commits"] == []
    assert body["emotion"] == []
    assert body["sections"] == []
    assert body["tracks"] == []
    assert body["totalCommits"] == 0


@pytest.mark.anyio
async def test_timeline_data_includes_commits_with_timestamps(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /musehub/repos/{repo_id}/timeline includes commits with timestamps."""
    from datetime import datetime, timezone, timedelta

    create = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "timeline-commits"},
        headers=auth_headers,
    )
    repo_id = create.json()["repoId"]

    now = datetime.now(tz=timezone.utc)
    for i in range(3):
        db_session.add(
            MusehubCommit(
                commit_id=f"abc{i:04d}ef1234567890abcdef1234",
                repo_id=repo_id,
                branch="main",
                parent_ids=[],
                message=f"commit {i}",
                author="musician",
                timestamp=now + timedelta(hours=i),
            )
        )
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/timeline",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["commits"]) == 3
    assert body["totalCommits"] == 3
    # Commits are returned oldest-first for temporal rendering
    commits = body["commits"]
    timestamps = [c["timestamp"] for c in commits]
    assert timestamps == sorted(timestamps)


@pytest.mark.anyio
async def test_timeline_json_response_structure(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Timeline JSON response includes all four event stream keys."""
    from datetime import datetime, timezone

    create = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "timeline-structure"},
        headers=auth_headers,
    )
    repo_id = create.json()["repoId"]

    db_session.add(
        MusehubCommit(
            commit_id="deadbeef12345678abcdef1234567890abcdef12",
            repo_id=repo_id,
            branch="main",
            parent_ids=[],
            message="added chorus",
            author="musician",
            timestamp=datetime.now(tz=timezone.utc),
        )
    )
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/timeline",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()

    # All four event stream keys present
    assert "commits" in body
    assert "emotion" in body
    assert "sections" in body
    assert "tracks" in body
    assert "totalCommits" in body

    # One commit, one emotion entry
    assert len(body["commits"]) == 1
    assert len(body["emotion"]) == 1

    # Emotion values are in [0, 1]
    emo = body["emotion"][0]
    assert 0.0 <= emo["valence"] <= 1.0
    assert 0.0 <= emo["energy"] <= 1.0
    assert 0.0 <= emo["tension"] <= 1.0

    # "added chorus" in commit message → section event with action "added"
    assert len(body["sections"]) >= 1
    section = next(s for s in body["sections"] if s["sectionName"] == "chorus")
    assert section["action"] == "added"


@pytest.mark.anyio
async def test_timeline_section_removed_action(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Commit message 'removed verse' yields section event with action='removed'."""
    from datetime import datetime, timezone

    create = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "section-removed"},
        headers=auth_headers,
    )
    repo_id = create.json()["repoId"]

    db_session.add(
        MusehubCommit(
            commit_id="cafe1234567890abcdef1234567890abcdef1234",
            repo_id=repo_id,
            branch="main",
            parent_ids=[],
            message="removed verse completely",
            author="musician",
            timestamp=datetime.now(tz=timezone.utc),
        )
    )
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/timeline",
        headers=auth_headers,
    )
    assert response.status_code == 200
    sections = response.json()["sections"]
    verse_events = [s for s in sections if s["sectionName"] == "verse"]
    assert len(verse_events) >= 1
    assert verse_events[0]["action"] == "removed"


@pytest.mark.anyio
async def test_timeline_track_events(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Commit message 'added bass' yields a track event with track_name='bass'."""
    from datetime import datetime, timezone

    create = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "track-events"},
        headers=auth_headers,
    )
    repo_id = create.json()["repoId"]

    db_session.add(
        MusehubCommit(
            commit_id="babe5678901234abcdef1234567890abcdef1234",
            repo_id=repo_id,
            branch="main",
            parent_ids=[],
            message="added bass line",
            author="musician",
            timestamp=datetime.now(tz=timezone.utc),
        )
    )
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/timeline",
        headers=auth_headers,
    )
    assert response.status_code == 200
    tracks = response.json()["tracks"]
    bass_events = [t for t in tracks if t["trackName"] == "bass"]
    assert len(bass_events) >= 1
    assert bass_events[0]["action"] == "added"


@pytest.mark.anyio
async def test_timeline_requires_auth(client: AsyncClient) -> None:
    """GET /musehub/repos/{repo_id}/timeline returns 401 without auth."""
    response = await client.get("/api/v1/musehub/repos/any-id/timeline")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_timeline_404_for_unknown_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /musehub/repos/{unknown}/timeline returns 404."""
    response = await client.get(
        "/api/v1/musehub/repos/does-not-exist/timeline",
        headers=auth_headers,
    )
    assert response.status_code == 404
