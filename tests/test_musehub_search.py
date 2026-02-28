"""Tests for MuseHub semantic similarity search endpoint.

Covers acceptance criteria from issue #237:
- GET /musehub/search/similar?commit={sha} returns ranked results
- Private repos are excluded from results (public_only enforced)
- 404 when commit SHA is not found
- 503 when Qdrant is unavailable
- Results are sorted by descending score
- Unauthenticated requests return 401

All tests use the shared ``client`` and ``auth_headers`` fixtures from
conftest.py.  Qdrant calls are mocked — no live vector database required.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

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


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_similar_search_requires_auth(client: AsyncClient) -> None:
    """GET /musehub/search/similar without token returns 401."""
    resp = await client.get("/api/v1/musehub/search/similar?commit=abc123")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 404 for unknown commit
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
# Successful search
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_similar_search_returns_results(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Successful search returns ranked SimilarCommitResponse list."""
    # 1. Create a public repo via the API
    create_resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "jazz-ballad", "visibility": "public"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    repo_id = create_resp.json()["repoId"]

    # 2. Push a commit to the repo (embedding mocked — no live Qdrant)
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

    # 3. Search for similar commits — mock Qdrant
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
    # Results are sorted descending by score
    assert data["results"][0]["score"] >= data["results"][1]["score"]


@pytest.mark.anyio
async def test_similar_search_public_only_enforced(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """search_similar is called with public_only=True — private commits excluded.

    We verify that the Qdrant client receives public_only=True rather than
    testing live Qdrant filtering (which is integration-tested separately).
    """
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
    # Verify public_only was passed as True
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
