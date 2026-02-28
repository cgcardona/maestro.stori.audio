"""Tests for Muse Hub repo, branch, commit, and credits endpoints.

Covers every acceptance criterion from issue #39:
- POST /musehub/repos returns 201 with correct fields
- POST requires auth — unauthenticated requests return 401
- GET /musehub/repos/{repo_id} returns 200; 404 for unknown repo
- GET /musehub/repos/{repo_id}/branches returns empty list on new repo
- GET /musehub/repos/{repo_id}/commits returns newest first, respects ?limit
- GET /musehub/repos/{repo_id}/dag returns topologically sorted DAG (issue #229)

Covers issue #241 (credits aggregation):
- test_credits_aggregation             — contributors aggregated from session commits
- test_credits_sorted_by_count         — default sort is most prolific first
- test_credits_sorted_by_recency       — recency sort puts most recent first
- test_credits_sorted_by_alpha         — alpha sort is lexicographic by author name
- test_credits_404_for_unknown_repo    — credits returns 404 for non-existent repo
- test_credits_requires_auth           — credits JSON endpoint requires JWT

All tests use the shared ``client`` and ``auth_headers`` fixtures from conftest.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubCommit, MusehubRepo
from maestro.services import musehub_credits, musehub_repository


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
# Credits endpoint tests (issue #241)
# ---------------------------------------------------------------------------


async def _seed_credits_repo(db_session: AsyncSession) -> str:
    """Create a repo with commits from two distinct authors and return repo_id."""
    repo = MusehubRepo(name="liner-notes", visibility="public", owner_user_id="producer-1")
    db_session.add(repo)
    await db_session.flush()
    repo_id = str(repo.repo_id)
# GET /musehub/repos/{repo_id}/dag  (issue #229)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_dag_endpoint_returns_empty_for_new_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /dag returns empty nodes/edges for a repo with no commits."""
    create = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "dag-empty"},
        headers=auth_headers,
    )
    repo_id = create.json()["repoId"]

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/dag",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["nodes"] == []
    assert body["edges"] == []
    assert body["headCommitId"] is None


@pytest.mark.anyio
async def test_graph_dag_has_edges(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """DAG endpoint returns correct edges representing parent relationships."""
    from datetime import datetime, timezone, timedelta

    create = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "dag-edges"},
        headers=auth_headers,
    )
    repo_id = create.json()["repoId"]

    now = datetime.now(tz=timezone.utc)
    root = MusehubCommit(
        commit_id="root111",
        repo_id=repo_id,
        branch="main",
        parent_ids=[],
        message="root commit",
        author="gabriel",
        timestamp=now - timedelta(hours=2),
    )
    child = MusehubCommit(
        commit_id="child222",
        repo_id=repo_id,
        branch="main",
        parent_ids=["root111"],
        message="child commit",
        author="gabriel",
        timestamp=now - timedelta(hours=1),
    )
    db_session.add_all([root, child])
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/dag",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    nodes = body["nodes"]
    edges = body["edges"]

    assert len(nodes) == 2
    # Verify edge: child → root
    assert any(e["source"] == "child222" and e["target"] == "root111" for e in edges)


@pytest.mark.anyio
async def test_graph_dag_endpoint_topological_order(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """DAG endpoint returns nodes in topological order (oldest ancestor first)."""
    from datetime import datetime, timezone, timedelta

    create = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "dag-topo"},
        headers=auth_headers,
    )
    repo_id = create.json()["repoId"]

    now = datetime.now(tz=timezone.utc)
    commits = [
        MusehubCommit(
            commit_id="c001",
            repo_id=repo_id,
            branch="main",
            parent_ids=[],
            message="compose intro melody and theme",
            author="Alice",
            timestamp=now - timedelta(days=10),
        ),
        MusehubCommit(
            commit_id="c002",
            repo_id=repo_id,
            branch="main",
            parent_ids=["c001"],
            message="arrang strings for verse",
            author="Bob",
            timestamp=now - timedelta(days=5),
        ),
        MusehubCommit(
            commit_id="c003",
            repo_id=repo_id,
            branch="main",
            parent_ids=["c002"],
            message="mix and master final session",
            author="Alice",
            timestamp=now - timedelta(days=1),
            commit_id="topo-a",
            repo_id=repo_id,
            branch="main",
            parent_ids=[],
            message="root",
            author="gabriel",
            timestamp=now - timedelta(hours=3),
        ),
        MusehubCommit(
            commit_id="topo-b",
            repo_id=repo_id,
            branch="main",
            parent_ids=["topo-a"],
            message="second",
            author="gabriel",
            timestamp=now - timedelta(hours=2),
        ),
        MusehubCommit(
            commit_id="topo-c",
            repo_id=repo_id,
            branch="main",
            parent_ids=["topo-b"],
            message="third",
            author="gabriel",
            timestamp=now - timedelta(hours=1),
        ),
    ]
    db_session.add_all(commits)
    await db_session.commit()
    return repo_id


@pytest.mark.anyio
async def test_credits_aggregation(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/credits aggregates contributors from commits."""
    repo_id = await _seed_credits_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/credits",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["totalContributors"] == 2
    authors = {c["author"] for c in body["contributors"]}
    assert "Alice" in authors
    assert "Bob" in authors


@pytest.mark.anyio
async def test_credits_sorted_by_count(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Default sort (count) puts the most prolific contributor first."""
    repo_id = await _seed_credits_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/credits?sort=count",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    contributors = body["contributors"]
    assert contributors[0]["author"] == "Alice"
    assert contributors[0]["sessionCount"] == 2
    assert contributors[1]["author"] == "Bob"
    assert contributors[1]["sessionCount"] == 1


@pytest.mark.anyio
async def test_credits_sorted_by_recency(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """sort=recency puts the most recently active contributor first."""
    repo_id = await _seed_credits_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/credits?sort=recency",
        headers=auth_headers,
    )
    assert response.status_code == 200
    contributors = response.json()["contributors"]
    # Alice has a commit 1 day ago; Bob's last was 5 days ago
    assert contributors[0]["author"] == "Alice"


@pytest.mark.anyio
async def test_credits_sorted_by_alpha(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """sort=alpha returns contributors in alphabetical order."""
    repo_id = await _seed_credits_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/credits?sort=alpha",
        headers=auth_headers,
    )
    assert response.status_code == 200
    contributors = response.json()["contributors"]
    authors = [c["author"] for c in contributors]
    assert authors == sorted(authors, key=str.lower)


@pytest.mark.anyio
async def test_credits_contribution_types_inferred(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """Contribution types are inferred from commit messages."""
    repo_id = await _seed_credits_repo(db_session)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/credits",
        headers=auth_headers,
    )
    assert response.status_code == 200
    contributors = response.json()["contributors"]
    alice = next(c for c in contributors if c["author"] == "Alice")
    # Alice's commits mention "compose" and "mix"
    types = set(alice["contributionTypes"])
    assert "composer" in types or "mixer" in types


@pytest.mark.anyio
async def test_credits_404_for_unknown_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{unknown}/credits returns 404."""
    response = await client.get(
        "/api/v1/musehub/repos/does-not-exist/credits",

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/dag",
        headers=auth_headers,
    )
    assert response.status_code == 200
    node_ids = [n["commitId"] for n in response.json()["nodes"]]
    # Root must appear before children in topological order
    assert node_ids.index("topo-a") < node_ids.index("topo-b")
    assert node_ids.index("topo-b") < node_ids.index("topo-c")


@pytest.mark.anyio
async def test_graph_dag_requires_auth(client: AsyncClient) -> None:
    """GET /dag returns 401 without a Bearer token."""
    response = await client.get("/api/v1/musehub/repos/any-repo/dag")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_graph_dag_404_for_unknown_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /dag returns 404 for a non-existent repo."""
    response = await client.get(
        "/api/v1/musehub/repos/ghost-repo-dag/dag",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_credits_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/credits returns 401 without JWT."""
    repo = MusehubRepo(name="auth-test-repo", visibility="private", owner_user_id="u1")
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    response = await client.get(f"/api/v1/musehub/repos/{repo.repo_id}/credits")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_credits_invalid_sort_param(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/credits with invalid sort returns 422."""
    repo = MusehubRepo(name="sort-test", visibility="private", owner_user_id="u1")
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    response = await client.get(
        f"/api/v1/musehub/repos/{repo.repo_id}/credits?sort=invalid",
        headers=auth_headers,
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_credits_aggregation_service_direct(db_session: AsyncSession) -> None:
    """musehub_credits.aggregate_credits() returns correct data without HTTP layer."""
    repo = MusehubRepo(name="direct-test", visibility="private", owner_user_id="u1")
    db_session.add(repo)
    await db_session.flush()
    repo_id = str(repo.repo_id)

    now = datetime.now(tz=timezone.utc)
    db_session.add(
        MusehubCommit(
            commit_id="svc-001",
            repo_id=repo_id,
            branch="main",
            parent_ids=[],
            message="produce and mix the drop",
            author="Charlie",
            timestamp=now,
async def test_graph_json_response_has_required_fields(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """DAG JSON response includes nodes (with required fields) and edges arrays."""
    from datetime import datetime, timezone

    create = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "dag-fields"},
        headers=auth_headers,
    )
    repo_id = create.json()["repoId"]

    db_session.add(
        MusehubCommit(
            commit_id="fields-aaa",
            repo_id=repo_id,
            branch="main",
            parent_ids=[],
            message="check fields",
            author="tester",
            timestamp=datetime.now(tz=timezone.utc),
        )
    )
    await db_session.commit()

    result = await musehub_credits.aggregate_credits(db_session, repo_id)
    assert result.total_contributors == 1
    assert result.contributors[0].author == "Charlie"
    assert result.contributors[0].session_count == 1
    assert len(result.contributors[0].contribution_types) >= 1
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/dag",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert "nodes" in body
    assert "edges" in body
    assert "headCommitId" in body

    node = body["nodes"][0]
    for field in ("commitId", "message", "author", "timestamp", "branch", "parentIds", "isHead"):
        assert field in node, f"Missing field '{field}' in DAG node"
