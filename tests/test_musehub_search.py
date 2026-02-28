"""Tests for MuseHub search endpoints.

Covers semantic similarity search (issue #237):
- GET /musehub/search/similar?commit={sha} returns ranked results
- Private repos are excluded from results (public_only enforced)
- 404 when commit SHA is not found
- 503 when Qdrant is unavailable
- Results are sorted by descending score
- Unauthenticated requests return 401

Covers in-repo search (issue #235):
- test_search_page_renders              — GET /musehub/ui/{repo_id}/search → 200 HTML
- test_search_keyword_mode              — keyword search returns matching commits
- test_search_keyword_empty_query       — empty keyword query returns empty matches
- test_search_musical_property          — musical property filter works
- test_search_natural_language          — ask mode returns matching commits
- test_search_pattern_message           — pattern matches commit message
- test_search_pattern_branch            — pattern matches branch name
- test_search_json_response             — JSON search endpoint returns SearchResponse shape
- test_search_date_range_since          — since filter excludes old commits
- test_search_date_range_until          — until filter excludes future commits
- test_search_invalid_mode              — invalid mode returns 422
- test_search_unknown_repo              — unknown repo_id returns 404
- test_search_requires_auth             — unauthenticated request returns 401
- test_search_limit_respected           — limit caps result count

All tests use the shared ``client`` and ``auth_headers`` fixtures from
conftest.py.  Qdrant calls are mocked — no live vector database required.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot
from maestro.services.musehub_qdrant import SimilarCommitResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_similar_result(
    commit_id: str,
    repo_id: str = "repo-pub",
    score: float = 0.9,
    branch: str = "main",
    author: str = "composer",
) -> SimilarCommitResult:
    return SimilarCommitResult(
        commit_id=commit_id,
        repo_id=repo_id,
        score=score,
        branch=branch,
        author=author,
    )


async def _make_repo(db: AsyncSession) -> str:
    """Seed a minimal MuseHub repo; return repo_id."""
    repo = MusehubRepo(
        name="search-test-repo",
        visibility="private",
        owner_user_id="test-owner",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_snapshot(db: AsyncSession, snapshot_id: str) -> None:
    """Seed a minimal snapshot so FK constraint on commits is satisfied."""
    snap = MuseCliSnapshot(snapshot_id=snapshot_id, manifest={})
    db.add(snap)
    await db.flush()


async def _make_commit(
    db: AsyncSession,
    *,
    repo_id: str,
    message: str,
    branch: str = "main",
    author: str = "test-author",
    committed_at: datetime | None = None,
) -> MuseCliCommit:
    """Seed a single commit for search tests."""
    snap_id = "snap-" + str(uuid.uuid4()).replace("-", "")[:16]
    await _make_snapshot(db, snap_id)
    commit = MuseCliCommit(
        commit_id=str(uuid.uuid4()).replace("-", ""),
        repo_id=repo_id,
        branch=branch,
        snapshot_id=snap_id,
        message=message,
        author=author,
        committed_at=committed_at or datetime.now(timezone.utc),
    )
    db.add(commit)
    await db.flush()
    return commit


# ---------------------------------------------------------------------------
# Semantic similarity search — authentication
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_similar_search_requires_auth(client: AsyncClient) -> None:
    """GET /musehub/search/similar without token returns 401."""
    resp = await client.get("/api/v1/musehub/search/similar?commit=abc123")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Semantic similarity search — 404 for unknown commit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_similar_search_returns_404_for_unknown_commit(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """404 is returned when the query commit SHA does not exist in the Hub."""
    resp = await client.get(
        "/api/v1/musehub/search/similar?commit=nonexistent-sha",
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert "nonexistent-sha" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Semantic similarity search — successful search
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_similar_search_returns_results(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Successful search returns ranked SimilarCommitResponse list."""
    create_resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "jazz-ballad", "visibility": "public"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    repo_id = create_resp.json()["repoId"]

    commit_id = "abc-jazz-001"
    with patch("maestro.api.routes.musehub.sync.embed_push_commits"):
        push_resp = await client.post(
            f"/api/v1/musehub/repos/{repo_id}/push",
            json={
                "branch": "main",
                "headCommitId": commit_id,
                "commits": [
                    {
                        "commitId": commit_id,
                        "parentIds": [],
                        "message": "Jazz ballad in Db major at 72 BPM",
                        "timestamp": "2024-01-01T00:00:00Z",
                    }
                ],
                "objects": [],
                "force": False,
            },
            headers=auth_headers,
        )
    assert push_resp.status_code == 200

    mock_results = [
        _make_similar_result("similar-001", score=0.95),
        _make_similar_result("similar-002", score=0.87),
    ]
    with patch("maestro.api.routes.musehub.search._get_qdrant_client") as mock_get_client:
        mock_qdrant = MagicMock()
        mock_qdrant.search_similar.return_value = mock_results
        mock_get_client.return_value = mock_qdrant

        search_resp = await client.get(
            f"/api/v1/musehub/search/similar?commit={commit_id}&limit=5",
            headers=auth_headers,
        )

    assert search_resp.status_code == 200
    data = search_resp.json()
    assert data["queryCommit"] == commit_id
    assert len(data["results"]) == 2
    assert data["results"][0]["score"] >= data["results"][1]["score"]


@pytest.mark.anyio
async def test_similar_search_public_only_enforced(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """search_similar is called with public_only=True — private commits excluded."""
    create_resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "public-jazz", "visibility": "public"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    repo_id = create_resp.json()["repoId"]
    commit_id = "pub-commit-001"

    with patch("maestro.api.routes.musehub.sync.embed_push_commits"):
        await client.post(
            f"/api/v1/musehub/repos/{repo_id}/push",
            json={
                "branch": "main",
                "headCommitId": commit_id,
                "commits": [
                    {
                        "commitId": commit_id,
                        "parentIds": [],
                        "message": "C major 120 BPM",
                        "timestamp": "2024-01-01T00:00:00Z",
                    }
                ],
                "objects": [],
                "force": False,
            },
            headers=auth_headers,
        )

    with patch("maestro.api.routes.musehub.search._get_qdrant_client") as mock_get:
        mock_qdrant = MagicMock()
        mock_qdrant.search_similar.return_value = []
        mock_get.return_value = mock_qdrant

        resp = await client.get(
            f"/api/v1/musehub/search/similar?commit={commit_id}",
            headers=auth_headers,
        )

    assert resp.status_code == 200
    call_kwargs = mock_qdrant.search_similar.call_args.kwargs
    assert call_kwargs.get("public_only") is True


@pytest.mark.anyio
async def test_similar_search_excludes_query_commit(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """The query commit itself is passed as exclude_commit_id to avoid self-match."""
    create_resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "self-exclude-test", "visibility": "public"},
        headers=auth_headers,
    )
    repo_id = create_resp.json()["repoId"]
    commit_id = "self-excl-001"

    with patch("maestro.api.routes.musehub.sync.embed_push_commits"):
        await client.post(
            f"/api/v1/musehub/repos/{repo_id}/push",
            json={
                "branch": "main",
                "headCommitId": commit_id,
                "commits": [
                    {
                        "commitId": commit_id,
                        "parentIds": [],
                        "message": "G minor 85 BPM",
                        "timestamp": "2024-01-01T00:00:00Z",
                    }
                ],
                "objects": [],
                "force": False,
            },
            headers=auth_headers,
        )

    with patch("maestro.api.routes.musehub.search._get_qdrant_client") as mock_get:
        mock_qdrant = MagicMock()
        mock_qdrant.search_similar.return_value = []
        mock_get.return_value = mock_qdrant

        await client.get(
            f"/api/v1/musehub/search/similar?commit={commit_id}",
            headers=auth_headers,
        )

    call_kwargs = mock_qdrant.search_similar.call_args.kwargs
    assert call_kwargs.get("exclude_commit_id") == commit_id


@pytest.mark.anyio
async def test_similar_search_503_when_qdrant_unavailable(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """503 is returned when Qdrant raises an exception."""
    create_resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "qdrant-fail-test", "visibility": "public"},
        headers=auth_headers,
    )
    repo_id = create_resp.json()["repoId"]
    commit_id = "qdrant-fail-001"

    with patch("maestro.api.routes.musehub.sync.embed_push_commits"):
        await client.post(
            f"/api/v1/musehub/repos/{repo_id}/push",
            json={
                "branch": "main",
                "headCommitId": commit_id,
                "commits": [
                    {
                        "commitId": commit_id,
                        "parentIds": [],
                        "message": "A minor 95 BPM",
                        "timestamp": "2024-01-01T00:00:00Z",
                    }
                ],
                "objects": [],
                "force": False,
            },
            headers=auth_headers,
        )

    with patch("maestro.api.routes.musehub.search._get_qdrant_client") as mock_get:
        mock_qdrant = MagicMock()
        mock_qdrant.search_similar.side_effect = ConnectionError("Qdrant down")
        mock_get.return_value = mock_qdrant

        resp = await client.get(
            f"/api/v1/musehub/search/similar?commit={commit_id}",
            headers=auth_headers,
        )

    assert resp.status_code == 503


@pytest.mark.anyio
async def test_similar_search_limit_respected(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """The limit query parameter is forwarded to Qdrant search_similar."""
    create_resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "limit-test", "visibility": "public"},
        headers=auth_headers,
    )
    repo_id = create_resp.json()["repoId"]
    commit_id = "limit-test-001"

    with patch("maestro.api.routes.musehub.sync.embed_push_commits"):
        await client.post(
            f"/api/v1/musehub/repos/{repo_id}/push",
            json={
                "branch": "main",
                "headCommitId": commit_id,
                "commits": [
                    {
                        "commitId": commit_id,
                        "parentIds": [],
                        "message": "E major 140 BPM",
                        "timestamp": "2024-01-01T00:00:00Z",
                    }
                ],
                "objects": [],
                "force": False,
            },
            headers=auth_headers,
        )

    with patch("maestro.api.routes.musehub.search._get_qdrant_client") as mock_get:
        mock_qdrant = MagicMock()
        mock_qdrant.search_similar.return_value = []
        mock_get.return_value = mock_qdrant

        await client.get(
            f"/api/v1/musehub/search/similar?commit={commit_id}&limit=3",
            headers=auth_headers,
        )

    call_kwargs = mock_qdrant.search_similar.call_args.kwargs
    assert call_kwargs.get("limit") == 3


# ---------------------------------------------------------------------------
# In-repo search — UI page
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/search returns 200 HTML with mode tabs."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/search")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "Search Commits" in body
    assert "Keyword" in body
    assert "Natural Language" in body
    assert "Pattern" in body
    assert "Musical Properties" in body
    assert "inp-since" in body
    assert "inp-until" in body


@pytest.mark.anyio
async def test_search_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Search UI page is accessible without a JWT (HTML shell, JS handles auth)."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/musehub/ui/{repo_id}/search")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# In-repo search — authentication
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/search returns 401 without a token."""
    repo_id = await _make_repo(db_session)
    response = await client.get(f"/api/v1/musehub/repos/{repo_id}/search?mode=keyword&q=jazz")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_search_unknown_repo(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{unknown}/search returns 404."""
    response = await client.get(
        "/api/v1/musehub/repos/does-not-exist/search?mode=keyword&q=test",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_search_invalid_mode(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET search with an unknown mode returns 422."""
    repo_id = await _make_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=badmode&q=x",
        headers=auth_headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# In-repo search — keyword mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_keyword_mode(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Keyword search returns commits whose messages overlap with the query."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    await _make_commit(db_session, repo_id=repo_id, message="dark jazz bassline in Dm")
    await _make_commit(db_session, repo_id=repo_id, message="classical piano intro section")
    await _make_commit(db_session, repo_id=repo_id, message="hip hop drum fill pattern")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=keyword&q=jazz+bassline",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "keyword"
    assert data["query"] == "jazz bassline"
    assert any("jazz" in m["message"].lower() for m in data["matches"])


@pytest.mark.anyio
async def test_search_keyword_empty_query(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Empty keyword query returns empty matches (no tokens → no overlap)."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()
    await _make_commit(db_session, repo_id=repo_id, message="some commit")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=keyword&q=",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "keyword"
    assert data["matches"] == []


@pytest.mark.anyio
async def test_search_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Search response has the expected SearchResponse JSON shape."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()
    await _make_commit(db_session, repo_id=repo_id, message="piano chord progression F Bb Eb")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=keyword&q=piano",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()

    assert "mode" in data
    assert "query" in data
    assert "matches" in data
    assert "totalScanned" in data
    assert "limit" in data

    if data["matches"]:
        m = data["matches"][0]
        assert "commitId" in m
        assert "branch" in m
        assert "message" in m
        assert "author" in m
        assert "timestamp" in m
        assert "score" in m
        assert "matchSource" in m


# ---------------------------------------------------------------------------
# In-repo search — musical property mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_musical_property(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Property mode filters commits containing the harmony string."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    await _make_commit(db_session, repo_id=repo_id, message="add harmony=Eb bridge section")
    await _make_commit(db_session, repo_id=repo_id, message="drum groove tweak no harmony")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=property&harmony=Eb",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "property"
    assert len(data["matches"]) >= 1
    assert all("Eb" in m["message"] for m in data["matches"])


# ---------------------------------------------------------------------------
# In-repo search — natural language (ask) mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_natural_language(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Ask mode extracts keywords and returns relevant commits."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    await _make_commit(db_session, repo_id=repo_id, message="switched tempo to 140bpm for drop")
    await _make_commit(db_session, repo_id=repo_id, message="piano melody in minor key")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=ask&q=what+tempo+changes+did+I+make",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "ask"
    assert any("tempo" in m["message"].lower() for m in data["matches"])


# ---------------------------------------------------------------------------
# In-repo search — pattern mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_pattern_message(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Pattern mode matches substring in commit message."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    await _make_commit(db_session, repo_id=repo_id, message="add Cm7 chord voicing in bridge")
    await _make_commit(db_session, repo_id=repo_id, message="fix timing on verse drums")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=pattern&q=Cm7",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "pattern"
    assert len(data["matches"]) == 1
    assert "Cm7" in data["matches"][0]["message"]
    assert data["matches"][0]["matchSource"] == "message"


@pytest.mark.anyio
async def test_search_pattern_branch(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Pattern mode matches substring in branch name when message doesn't match."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    await _make_commit(
        db_session,
        repo_id=repo_id,
        message="rough cut",
        branch="feature/hip-hop-session",
    )
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=pattern&q=hip-hop",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "pattern"
    assert len(data["matches"]) == 1
    assert data["matches"][0]["matchSource"] == "branch"


# ---------------------------------------------------------------------------
# In-repo search — date range filters
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_date_range_since(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """since filter excludes commits committed before the given datetime."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    old_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    new_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)

    await _make_commit(db_session, repo_id=repo_id, message="old jazz commit", committed_at=old_ts)
    await _make_commit(db_session, repo_id=repo_id, message="new jazz commit", committed_at=new_ts)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=keyword&q=jazz&since=2025-06-01T00:00:00Z",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert all(m["message"] != "old jazz commit" for m in data["matches"])
    assert any(m["message"] == "new jazz commit" for m in data["matches"])


@pytest.mark.anyio
async def test_search_date_range_until(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """until filter excludes commits committed after the given datetime."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    old_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    new_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)

    await _make_commit(db_session, repo_id=repo_id, message="old piano commit", committed_at=old_ts)
    await _make_commit(db_session, repo_id=repo_id, message="new piano commit", committed_at=new_ts)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=keyword&q=piano&until=2025-06-01T00:00:00Z",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert any(m["message"] == "old piano commit" for m in data["matches"])
    assert all(m["message"] != "new piano commit" for m in data["matches"])


# ---------------------------------------------------------------------------
# In-repo search — limit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_limit_respected(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """The limit parameter caps the number of results returned."""
    repo_id = await _make_repo(db_session)
    await db_session.commit()

    for i in range(10):
        await _make_commit(db_session, repo_id=repo_id, message=f"bass groove iteration {i}")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=keyword&q=bass&limit=3",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["matches"]) <= 3
    assert data["limit"] == 3
