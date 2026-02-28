"""Tests for MuseHub search endpoints.

Covers semantic similarity search (issue #237):
- GET /musehub/search/similar?commit={sha} returns ranked results
- Private repos are excluded from results (public_only enforced)
- 404 when commit SHA is not found
- 503 when Qdrant is unavailable
- Results are sorted by descending score
- Unauthenticated requests return 401

Covers cross-repo global search (issue #236):
- test_global_search_page_renders          — GET /musehub/ui/search returns 200 HTML
- test_global_search_results_grouped       — JSON results are grouped by repo
- test_global_search_public_only           — private repos are excluded
- test_global_search_json                  — JSON content-type returned
- test_global_search_empty_query_handled   — graceful response for empty result set
- test_global_search_requires_auth         — 401 without JWT
- test_global_search_keyword_mode          — keyword mode matches across message terms
- test_global_search_pattern_mode          — pattern mode uses SQL LIKE
- test_global_search_pagination            — page/page_size params respected

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

from maestro.db.musehub_models import MusehubCommit, MusehubRepo
from maestro.main import app
from maestro.muse_cli.models import MuseCliCommit, MuseCliSnapshot
from maestro.services.musehub_qdrant import SimilarCommitResult, get_qdrant_client


# ---------------------------------------------------------------------------
# Helpers — similarity search
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
# Helpers — global search (uses MusehubCommit / MusehubRepo directly)
# ---------------------------------------------------------------------------


async def _make_repo(
    db_session: AsyncSession,
    *,
    name: str = "test-repo",
    visibility: str = "public",
    owner: str = "test-owner",
) -> str:
    """Seed a MuseHub repo and return its repo_id."""
    import re as _re
    slug = _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64].strip("-") or "repo"
    repo = MusehubRepo(name=name, owner="testuser", slug=slug, visibility=visibility, owner_user_id=owner)
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


async def _make_commit(
    db_session: AsyncSession,
    repo_id: str,
    *,
    commit_id: str,
    message: str,
    author: str = "alice",
    branch: str = "main",
) -> None:
    """Seed a MusehubCommit for global search tests."""
    commit = MusehubCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch,
        parent_ids=[],
        message=message,
        author=author,
        timestamp=datetime.now(tz=timezone.utc),
    )
    db_session.add(commit)
    await db_session.commit()


# ---------------------------------------------------------------------------
# Helpers — in-repo search (uses MuseCliCommit / MuseCliSnapshot)
# ---------------------------------------------------------------------------


async def _make_search_repo(db: AsyncSession) -> str:
    """Seed a minimal MuseHub repo for in-repo search tests; return repo_id."""
    repo = MusehubRepo(
        name="search-test-repo",
        owner="testuser",
        slug="search-test-repo",
        visibility="private",
        owner_user_id="test-owner",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_snapshot(db: AsyncSession, snapshot_id: str) -> None:
    """Seed a minimal snapshot so FK constraint on MuseCliCommit is satisfied."""
    snap = MuseCliSnapshot(snapshot_id=snapshot_id, manifest={})
    db.add(snap)
    await db.flush()


async def _make_search_commit(
    db: AsyncSession,
    *,
    repo_id: str,
    message: str,
    branch: str = "main",
    author: str = "test-author",
    committed_at: datetime | None = None,
) -> MuseCliCommit:
    """Seed a MuseCliCommit for in-repo search tests."""
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
# Similarity search — authentication
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_similar_search_unknown_commit_returns_404_without_auth(
    client: AsyncClient,
) -> None:
    """GET /musehub/search/similar returns 404 for an unknown commit without a token.

    Uses optional_token — the endpoint is public; a non-existent commit → 404.
    """
    resp = await client.get("/api/v1/musehub/search/similar?commit=non-existent-commit-id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Similarity search — 404 for unknown commit
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
# Similarity search — successful search
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_similar_search_returns_results(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Successful search returns ranked SimilarCommitResponse list."""
    create_resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "jazz-ballad", "owner": "testuser", "visibility": "public"},
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
    mock_qdrant = MagicMock()
    mock_qdrant.search_similar.return_value = mock_results
    app.dependency_overrides[get_qdrant_client] = lambda: mock_qdrant
    try:
        search_resp = await client.get(
            f"/api/v1/musehub/search/similar?commit={commit_id}&limit=5",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_qdrant_client, None)

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
        json={"name": "public-jazz", "owner": "testuser", "visibility": "public"},
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

    mock_qdrant = MagicMock()
    mock_qdrant.search_similar.return_value = []
    app.dependency_overrides[get_qdrant_client] = lambda: mock_qdrant
    try:
        resp = await client.get(
            f"/api/v1/musehub/search/similar?commit={commit_id}",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_qdrant_client, None)

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
        json={"name": "self-exclude-test", "owner": "testuser", "visibility": "public"},
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

    mock_qdrant = MagicMock()
    mock_qdrant.search_similar.return_value = []
    app.dependency_overrides[get_qdrant_client] = lambda: mock_qdrant
    try:
        await client.get(
            f"/api/v1/musehub/search/similar?commit={commit_id}",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_qdrant_client, None)

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
        json={"name": "qdrant-fail-test", "owner": "testuser", "visibility": "public"},
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

    mock_qdrant = MagicMock()
    mock_qdrant.search_similar.side_effect = ConnectionError("Qdrant down")
    app.dependency_overrides[get_qdrant_client] = lambda: mock_qdrant
    try:
        resp = await client.get(
            f"/api/v1/musehub/search/similar?commit={commit_id}",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_qdrant_client, None)

    assert resp.status_code == 503


@pytest.mark.anyio
async def test_similar_search_limit_respected(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """The limit query parameter is forwarded to Qdrant search_similar."""
    create_resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "limit-test", "owner": "testuser", "visibility": "public"},
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

    mock_qdrant = MagicMock()
    mock_qdrant.search_similar.return_value = []
    app.dependency_overrides[get_qdrant_client] = lambda: mock_qdrant
    try:
        await client.get(
            f"/api/v1/musehub/search/similar?commit={commit_id}&limit=3",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_qdrant_client, None)

    call_kwargs = mock_qdrant.search_similar.call_args.kwargs
    assert call_kwargs.get("limit") == 3


# ---------------------------------------------------------------------------
# Similarity search — DI override (regression for issue #272)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_similar_search_qdrant_injected_via_dependency_override(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Qdrant client is injected via FastAPI dependency_overrides, not module patching.

    Regression test for issue #272: confirms that get_qdrant_client is a proper
    FastAPI dependency so tests can override it without patching module internals.
    """
    create_resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "di-test-repo", "owner": "testuser", "visibility": "public"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    repo_id = create_resp.json()["repoId"]
    commit_id = "di-test-commit-001"

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
                        "message": "DI refactor test commit in Cmaj",
                        "timestamp": "2024-01-01T00:00:00Z",
                    }
                ],
                "objects": [],
                "force": False,
            },
            headers=auth_headers,
        )

    mock_qdrant = MagicMock()
    mock_qdrant.search_similar.return_value = [_make_similar_result("di-result-001", score=0.88)]
    app.dependency_overrides[get_qdrant_client] = lambda: mock_qdrant
    try:
        resp = await client.get(
            f"/api/v1/musehub/search/similar?commit={commit_id}&limit=5",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_qdrant_client, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["queryCommit"] == commit_id
    assert len(data["results"]) == 1
    assert data["results"][0]["commitId"] == "di-result-001"
    assert mock_qdrant.search_similar.called


# ---------------------------------------------------------------------------
# Global search — UI page
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_global_search_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/search returns 200 HTML with a search form (no auth required)."""
    response = await client.get("/musehub/ui/search")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Global Search" in body
    assert "Muse Hub" in body
    assert "q-input" in body
    assert "mode-sel" in body


@pytest.mark.anyio
async def test_global_search_page_pre_fills_query(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/search?q=jazz pre-fills the search form with 'jazz'."""
    response = await client.get("/musehub/ui/search?q=jazz&mode=keyword")
    assert response.status_code == 200
    body = response.text
    assert "jazz" in body


# ---------------------------------------------------------------------------
# Global search — JSON API
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_global_search_accessible_without_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/search returns 200 without a JWT.

    Global search is a public endpoint — uses optional_token, so unauthenticated
    requests are allowed and return results for public repos.
    """
    response = await client.get("/api/v1/musehub/search?q=jazz")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_global_search_json(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/search returns JSON with correct content-type."""
    response = await client.get(
        "/api/v1/musehub/search?q=jazz",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    data = response.json()
    assert "groups" in data
    assert "query" in data
    assert data["query"] == "jazz"


@pytest.mark.anyio
async def test_global_search_public_only(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Private repos must not appear in global search results."""
    public_id = await _make_repo(db_session, name="public-beats", visibility="public")
    private_id = await _make_repo(db_session, name="secret-beats", visibility="private")

    await _make_commit(
        db_session, public_id, commit_id="pub001abc", message="jazz groove session"
    )
    await _make_commit(
        db_session, private_id, commit_id="priv001abc", message="jazz private session"
    )

    response = await client.get(
        "/api/v1/musehub/search?q=jazz",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    repo_ids_in_results = {g["repoId"] for g in data["groups"]}
    assert public_id in repo_ids_in_results
    assert private_id not in repo_ids_in_results


@pytest.mark.anyio
async def test_global_search_results_grouped(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Results are grouped by repo — each group has repoId, repoName, matches list."""
    repo_a = await _make_repo(db_session, name="repo-alpha", visibility="public")
    repo_b = await _make_repo(db_session, name="repo-beta", visibility="public")

    await _make_commit(
        db_session, repo_a, commit_id="a001abc123", message="bossa nova rhythm"
    )
    await _make_commit(
        db_session, repo_a, commit_id="a002abc123", message="bossa nova variation"
    )
    await _make_commit(
        db_session, repo_b, commit_id="b001abc123", message="bossa nova groove"
    )

    response = await client.get(
        "/api/v1/musehub/search?q=bossa+nova",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    groups = data["groups"]

    group_repo_ids = {g["repoId"] for g in groups}
    assert repo_a in group_repo_ids
    assert repo_b in group_repo_ids

    for group in groups:
        assert "repoId" in group
        assert "repoName" in group
        assert "repoOwner" in group
        assert "repoSlug" in group  # PR #282: slug required for UI link construction
        assert "repoVisibility" in group
        assert "matches" in group
        assert "totalMatches" in group
        assert isinstance(group["matches"], list)
        assert isinstance(group["repoSlug"], str)
        assert group["repoSlug"] != ""

    group_a = next(g for g in groups if g["repoId"] == repo_a)
    assert group_a["totalMatches"] == 2
    assert len(group_a["matches"]) == 2


@pytest.mark.anyio
async def test_global_search_empty_query_handled(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """A query that matches nothing returns empty groups and valid pagination metadata."""
    await _make_repo(db_session, name="silent-repo", visibility="public")

    response = await client.get(
        "/api/v1/musehub/search?q=zyxqwvutsr_no_match",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["groups"] == []
    assert data["page"] == 1
    assert "totalReposSearched" in data


@pytest.mark.anyio
async def test_global_search_keyword_mode(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Keyword mode matches any term in the query (OR logic, case-insensitive)."""
    repo_id = await _make_repo(db_session, name="jazz-lab", visibility="public")
    await _make_commit(
        db_session, repo_id, commit_id="kw001abcde", message="Blues Shuffle in E"
    )
    await _make_commit(
        db_session, repo_id, commit_id="kw002abcde", message="Jazz Waltz Trio"
    )

    response = await client.get(
        "/api/v1/musehub/search?q=blues&mode=keyword",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    group = next((g for g in data["groups"] if g["repoId"] == repo_id), None)
    assert group is not None
    messages = [m["message"] for m in group["matches"]]
    assert any("Blues" in msg for msg in messages)


@pytest.mark.anyio
async def test_global_search_pattern_mode(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Pattern mode applies a raw SQL LIKE pattern to commit messages."""
    repo_id = await _make_repo(db_session, name="pattern-lab", visibility="public")
    await _make_commit(
        db_session, repo_id, commit_id="pt001abcde", message="minor pentatonic run"
    )
    await _make_commit(
        db_session, repo_id, commit_id="pt002abcde", message="major scale exercise"
    )

    response = await client.get(
        "/api/v1/musehub/search?q=%25minor%25&mode=pattern",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    group = next((g for g in data["groups"] if g["repoId"] == repo_id), None)
    assert group is not None
    assert group["totalMatches"] == 1
    assert "minor" in group["matches"][0]["message"]


@pytest.mark.anyio
async def test_global_search_pagination(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """page and page_size parameters control repo-group pagination."""
    ids = []
    for i in range(3):
        rid = await _make_repo(
            db_session, name=f"paged-repo-{i}", visibility="public", owner=f"owner-{i}"
        )
        ids.append(rid)
        await _make_commit(
            db_session, rid, commit_id=f"pg{i:03d}abcde", message="paginate funk groove"
        )

    response = await client.get(
        "/api/v1/musehub/search?q=paginate&page=1&page_size=2",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["groups"]) <= 2
    assert data["page"] == 1
    assert data["pageSize"] == 2

    response2 = await client.get(
        "/api/v1/musehub/search?q=paginate&page=2&page_size=2",
        headers=auth_headers,
    )
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["page"] == 2


@pytest.mark.anyio
async def test_global_search_match_contains_required_fields(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Each match entry contains commitId, message, author, branch, timestamp, repoId."""
    repo_id = await _make_repo(db_session, name="fields-check", visibility="public")
    await _make_commit(
        db_session,
        repo_id,
        commit_id="fc001abcde",
        message="swing feel experiment",
        author="charlie",
        branch="main",
    )

    response = await client.get(
        "/api/v1/musehub/search?q=swing",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    group = next((g for g in data["groups"] if g["repoId"] == repo_id), None)
    assert group is not None
    match = group["matches"][0]
    assert match["commitId"] == "fc001abcde"
    assert match["message"] == "swing feel experiment"
    assert match["author"] == "charlie"
    assert match["branch"] == "main"
    assert "timestamp" in match
    assert match["repoId"] == repo_id


# ---------------------------------------------------------------------------
# In-repo search — UI page
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{repo_id}/search returns 200 HTML with mode tabs."""
    repo_id = await _make_search_repo(db_session)
    response = await client.get("/musehub/ui/testuser/search-test-repo/search")
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
    repo_id = await _make_search_repo(db_session)
    response = await client.get("/musehub/ui/testuser/search-test-repo/search")
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
    repo_id = await _make_search_repo(db_session)
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
    repo_id = await _make_search_repo(db_session)
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
    repo_id = await _make_search_repo(db_session)
    await db_session.commit()

    await _make_search_commit(db_session, repo_id=repo_id, message="dark jazz bassline in Dm")
    await _make_search_commit(db_session, repo_id=repo_id, message="classical piano intro section")
    await _make_search_commit(db_session, repo_id=repo_id, message="hip hop drum fill pattern")
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
    repo_id = await _make_search_repo(db_session)
    await db_session.commit()
    await _make_search_commit(db_session, repo_id=repo_id, message="some commit")
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
    repo_id = await _make_search_repo(db_session)
    await db_session.commit()
    await _make_search_commit(db_session, repo_id=repo_id, message="piano chord progression F Bb Eb")
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
    repo_id = await _make_search_repo(db_session)
    await db_session.commit()

    await _make_search_commit(db_session, repo_id=repo_id, message="add harmony=Eb bridge section")
    await _make_search_commit(db_session, repo_id=repo_id, message="drum groove tweak no harmony")
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
    repo_id = await _make_search_repo(db_session)
    await db_session.commit()

    await _make_search_commit(db_session, repo_id=repo_id, message="switched tempo to 140bpm for drop")
    await _make_search_commit(db_session, repo_id=repo_id, message="piano melody in minor key")
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
    repo_id = await _make_search_repo(db_session)
    await db_session.commit()

    await _make_search_commit(db_session, repo_id=repo_id, message="add Cm7 chord voicing in bridge")
    await _make_search_commit(db_session, repo_id=repo_id, message="fix timing on verse drums")
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
    repo_id = await _make_search_repo(db_session)
    await db_session.commit()

    await _make_search_commit(
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
    repo_id = await _make_search_repo(db_session)
    await db_session.commit()

    old_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    new_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)

    await _make_search_commit(db_session, repo_id=repo_id, message="old jazz commit", committed_at=old_ts)
    await _make_search_commit(db_session, repo_id=repo_id, message="new jazz commit", committed_at=new_ts)
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
    repo_id = await _make_search_repo(db_session)
    await db_session.commit()

    old_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    new_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)

    await _make_search_commit(db_session, repo_id=repo_id, message="old piano commit", committed_at=old_ts)
    await _make_search_commit(db_session, repo_id=repo_id, message="new piano commit", committed_at=new_ts)
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
    repo_id = await _make_search_repo(db_session)
    await db_session.commit()

    for i in range(10):
        await _make_search_commit(db_session, repo_id=repo_id, message=f"bass groove iteration {i}")
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/search?mode=keyword&q=bass&limit=3",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["matches"]) <= 3
    assert data["limit"] == 3
