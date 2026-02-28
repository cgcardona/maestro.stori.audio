"""Tests for Muse Hub pull request endpoints.

Covers every acceptance criterion from issues #41 and #215:
- POST /musehub/repos/{repo_id}/pull-requests creates PR in open state
- 422 when from_branch == to_branch
- 404 when from_branch does not exist
- GET /pull-requests returns all PRs (open + merged + closed)
- GET /pull-requests/{pr_id} returns full PR detail; 404 if not found
- GET /pull-requests/{pr_id}/diff returns five-dimension musical diff scores
- GET /pull-requests/{pr_id}/diff graceful degradation when branches have no commits
- POST /pull-requests/{pr_id}/merge creates merge commit, sets state merged
- POST /pull-requests/{pr_id}/merge accepts squash and rebase strategies
- 409 when merging an already-merged PR
- All endpoints require valid JWT

All tests use the shared ``client``, ``auth_headers``, and ``db_session``
fixtures from conftest.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubBranch, MusehubCommit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
    name: str = "neo-soul-repo",
) -> str:
    """Create a repo via the API and return its repo_id."""
    response = await client.post(
        "/api/v1/musehub/repos",
        json={"name": name, "owner": "testuser"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    return str(response.json()["repoId"])


async def _push_branch(
    db: AsyncSession,
    repo_id: str,
    branch_name: str,
) -> str:
    """Insert a branch with one commit so the branch exists and has a head commit.

    Returns the commit_id so callers can reference it if needed.
    """
    commit_id = uuid.uuid4().hex
    commit = MusehubCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch_name,
        parent_ids=[],
        message=f"Initial commit on {branch_name}",
        author="rene",
        timestamp=datetime.now(tz=timezone.utc),
    )
    branch = MusehubBranch(
        repo_id=repo_id,
        name=branch_name,
        head_commit_id=commit_id,
    )
    db.add(commit)
    db.add(branch)
    await db.commit()
    return commit_id


async def _create_pr(
    client: AsyncClient,
    auth_headers: dict[str, str],
    repo_id: str,
    *,
    title: str = "Add neo-soul keys variation",
    from_branch: str = "feature",
    to_branch: str = "main",
    body: str = "",
) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests",
        json={
            "title": title,
            "fromBranch": from_branch,
            "toBranch": to_branch,
            "body": body,
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


# ---------------------------------------------------------------------------
# POST /musehub/repos/{repo_id}/pull-requests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_pr_returns_open_state(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """PR created via POST returns state='open' with all required fields."""
    repo_id = await _create_repo(client, auth_headers, "pr-open-state-repo")
    await _push_branch(db_session, repo_id, "feature")

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests",
        json={
            "title": "Add neo-soul keys variation",
            "fromBranch": "feature",
            "toBranch": "main",
            "body": "Adds dreamy chord voicings.",
        },
        headers=auth_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "open"
    assert body["title"] == "Add neo-soul keys variation"
    assert body["fromBranch"] == "feature"
    assert body["toBranch"] == "main"
    assert body["body"] == "Adds dreamy chord voicings."
    assert "prId" in body
    assert "createdAt" in body
    assert body["mergeCommitId"] is None


@pytest.mark.anyio
async def test_create_pr_same_branch_returns_422(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Creating a PR with from_branch == to_branch returns HTTP 422."""
    repo_id = await _create_repo(client, auth_headers, "same-branch-repo")

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests",
        json={"title": "Bad PR", "fromBranch": "main", "toBranch": "main"},
        headers=auth_headers,
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_create_pr_missing_from_branch_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Creating a PR when from_branch does not exist returns HTTP 404."""
    repo_id = await _create_repo(client, auth_headers, "no-branch-repo")

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests",
        json={"title": "Ghost PR", "fromBranch": "nonexistent", "toBranch": "main"},
        headers=auth_headers,
    )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_create_pr_requires_auth(client: AsyncClient) -> None:
    """POST /pull-requests returns 401 without a Bearer token."""
    response = await client.post(
        "/api/v1/musehub/repos/any-id/pull-requests",
        json={"title": "Unauthorized", "fromBranch": "feat", "toBranch": "main"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/pull-requests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_prs_returns_all_states(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /pull-requests returns open AND merged PRs by default."""
    repo_id = await _create_repo(client, auth_headers, "list-all-states-repo")
    await _push_branch(db_session, repo_id, "feature-a")
    await _push_branch(db_session, repo_id, "feature-b")
    await _push_branch(db_session, repo_id, "main")

    pr_a = await _create_pr(
        client, auth_headers, repo_id, title="Open PR", from_branch="feature-a"
    )
    pr_b = await _create_pr(
        client, auth_headers, repo_id, title="Merged PR", from_branch="feature-b"
    )

    # Merge pr_b
    await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_b['prId']}/merge",
        json={"mergeStrategy": "merge_commit"},
        headers=auth_headers,
    )

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests",
        headers=auth_headers,
    )
    assert response.status_code == 200
    prs = response.json()["pullRequests"]
    assert len(prs) == 2
    states = {p["state"] for p in prs}
    assert "open" in states
    assert "merged" in states


@pytest.mark.anyio
async def test_list_prs_filter_by_open(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /pull-requests?state=open returns only open PRs."""
    repo_id = await _create_repo(client, auth_headers, "filter-open-repo")
    await _push_branch(db_session, repo_id, "feat-open")
    await _push_branch(db_session, repo_id, "feat-merge")
    await _push_branch(db_session, repo_id, "main")

    await _create_pr(client, auth_headers, repo_id, title="Open PR", from_branch="feat-open")
    pr_to_merge = await _create_pr(
        client, auth_headers, repo_id, title="Will merge", from_branch="feat-merge"
    )
    await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_to_merge['prId']}/merge",
        json={"mergeStrategy": "merge_commit"},
        headers=auth_headers,
    )

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests?state=open",
        headers=auth_headers,
    )
    assert response.status_code == 200
    prs = response.json()["pullRequests"]
    assert len(prs) == 1
    assert prs[0]["state"] == "open"


@pytest.mark.anyio
async def test_list_prs_nonexistent_repo_returns_404_without_auth(client: AsyncClient) -> None:
    """GET /pull-requests returns 404 for non-existent repo without a token.

    Uses optional_token — auth is visibility-based; missing repo → 404.
    """
    response = await client.get("/api/v1/musehub/repos/non-existent-repo-id/pull-requests")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /musehub/repos/{repo_id}/pull-requests/{pr_id}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_pr_returns_full_detail(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /pull-requests/{pr_id} returns the full PR object."""
    repo_id = await _create_repo(client, auth_headers, "get-detail-repo")
    await _push_branch(db_session, repo_id, "keys-variation")

    created = await _create_pr(
        client,
        auth_headers,
        repo_id,
        title="Keys variation",
        from_branch="keys-variation",
        body="Dreamy neo-soul voicings",
    )

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{created['prId']}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["prId"] == created["prId"]
    assert body["title"] == "Keys variation"
    assert body["body"] == "Dreamy neo-soul voicings"
    assert body["state"] == "open"


@pytest.mark.anyio
async def test_get_pr_unknown_id_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /pull-requests/{unknown_pr_id} returns 404."""
    repo_id = await _create_repo(client, auth_headers, "get-404-repo")

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/does-not-exist",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_pr_nonexistent_returns_404_without_auth(client: AsyncClient) -> None:
    """GET /pull-requests/{pr_id} returns 404 for non-existent resource without a token.

    Uses optional_token — auth is visibility-based; missing repo/PR → 404.
    """
    response = await client.get("/api/v1/musehub/repos/non-existent-repo/pull-requests/non-existent-pr")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /musehub/repos/{repo_id}/pull-requests/{pr_id}/merge
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_pr_creates_merge_commit(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Merging a PR creates a merge commit and sets state to 'merged'."""
    repo_id = await _create_repo(client, auth_headers, "merge-commit-repo")
    await _push_branch(db_session, repo_id, "neo-soul")
    await _push_branch(db_session, repo_id, "main")

    pr = await _create_pr(
        client, auth_headers, repo_id, title="Neo-soul merge", from_branch="neo-soul"
    )

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr['prId']}/merge",
        json={"mergeStrategy": "merge_commit"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["merged"] is True
    assert "mergeCommitId" in body
    assert body["mergeCommitId"] is not None

    # Verify PR state changed to merged
    detail = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr['prId']}",
        headers=auth_headers,
    )
    assert detail.json()["state"] == "merged"
    assert detail.json()["mergeCommitId"] == body["mergeCommitId"]


@pytest.mark.anyio
async def test_merge_already_merged_returns_409(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Merging an already-merged PR returns HTTP 409 Conflict."""
    repo_id = await _create_repo(client, auth_headers, "double-merge-repo")
    await _push_branch(db_session, repo_id, "feature-dup")
    await _push_branch(db_session, repo_id, "main")

    pr = await _create_pr(
        client, auth_headers, repo_id, title="Duplicate merge", from_branch="feature-dup"
    )

    # First merge succeeds
    first = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr['prId']}/merge",
        json={"mergeStrategy": "merge_commit"},
        headers=auth_headers,
    )
    assert first.status_code == 200

    # Second merge must 409
    second = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr['prId']}/merge",
        json={"mergeStrategy": "merge_commit"},
        headers=auth_headers,
    )
    assert second.status_code == 409


@pytest.mark.anyio
async def test_merge_pr_requires_auth(client: AsyncClient) -> None:
    """POST /pull-requests/{pr_id}/merge returns 401 without a Bearer token."""
    response = await client.post(
        "/api/v1/musehub/repos/r/pull-requests/p/merge",
        json={"mergeStrategy": "merge_commit"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Regression tests for issue #302 — author field on PR
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_pr_author_in_response(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """POST /pull-requests response includes the author field (JWT sub) — regression for #302."""
    repo_id = await _create_repo(client, auth_headers, "author-pr-repo")
    await _push_branch(db_session, repo_id, "feat/author-test")
    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests",
        json={
            "title": "Author field regression",
            "body": "",
            "fromBranch": "feat/author-test",
            "toBranch": "main",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert "author" in body
    assert isinstance(body["author"], str)


@pytest.mark.anyio
async def test_create_pr_author_persisted_in_list(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Author field is persisted and returned in the PR list endpoint — regression for #302."""
    repo_id = await _create_repo(client, auth_headers, "author-pr-list-repo")
    await _push_branch(db_session, repo_id, "feat/author-list-test")
    await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests",
        json={
            "title": "Authored PR",
            "body": "",
            "fromBranch": "feat/author-list-test",
            "toBranch": "main",
        },
        headers=auth_headers,
    )
    list_response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests",
        headers=auth_headers,
    )
    assert list_response.status_code == 200
    prs = list_response.json()["pullRequests"]
    assert len(prs) == 1
    assert "author" in prs[0]
    assert isinstance(prs[0]["author"], str)


@pytest.mark.anyio
async def test_pr_diff_endpoint_returns_five_dimensions(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /pull-requests/{pr_id}/diff returns per-dimension scores for the PR branches."""
    repo_id = await _create_repo(client, auth_headers, "diff-pr-repo")
    await _push_branch(db_session, repo_id, "feat/jazz-keys")
    pr_resp = await _create_pr(client, auth_headers, repo_id, from_branch="feat/jazz-keys", to_branch="main")
    pr_id = pr_resp["prId"]

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/diff",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert "dimensions" in data
    assert len(data["dimensions"]) == 5
    assert data["prId"] == pr_id
    assert data["fromBranch"] == "feat/jazz-keys"
    assert data["toBranch"] == "main"
    assert "overallScore" in data
    assert isinstance(data["overallScore"], float)

    # Every dimension must have the expected fields
    for dim in data["dimensions"]:
        assert "dimension" in dim
        assert dim["dimension"] in ("melodic", "harmonic", "rhythmic", "structural", "dynamic")
        assert "score" in dim
        assert 0.0 <= dim["score"] <= 1.0
        assert "level" in dim
        assert dim["level"] in ("NONE", "LOW", "MED", "HIGH")
        assert "deltaLabel" in dim
        assert "fromBranchCommits" in dim
        assert "toBranchCommits" in dim


@pytest.mark.anyio
async def test_pr_diff_endpoint_404_for_unknown_pr(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /pull-requests/{pr_id}/diff returns 404 when the PR does not exist."""
    repo_id = await _create_repo(client, auth_headers, "diff-404-repo")
    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/nonexistent-pr-id/diff",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_pr_diff_endpoint_graceful_when_no_commits(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Diff endpoint returns zero scores when branches have no commits (graceful degradation).

    When from_branch has commits but to_branch ('main') has none, compute_hub_divergence
    raises ValueError.  The diff endpoint must catch it and return zero-score placeholders
    so the PR detail page always renders.
    """
    from maestro.db.musehub_models import MusehubBranch, MusehubCommit, MusehubPullRequest

    repo_id = await _create_repo(client, auth_headers, "diff-empty-repo")

    # Seed from_branch with a commit so the PR can be created.
    commit_id = uuid.uuid4().hex
    commit = MusehubCommit(
        commit_id=commit_id,
        repo_id=repo_id,
        branch="feat/empty-grace",
        parent_ids=[],
        message="Initial commit on feat/empty-grace",
        author="musician",
        timestamp=datetime.now(tz=timezone.utc),
    )
    branch = MusehubBranch(
        repo_id=repo_id,
        name="feat/empty-grace",
        head_commit_id=commit_id,
    )
    db_session.add(commit)
    db_session.add(branch)

    # to_branch 'main' deliberately has NO commits — divergence will raise ValueError.
    pr = MusehubPullRequest(
        repo_id=repo_id,
        title="Grace PR",
        body="",
        state="open",
        from_branch="feat/empty-grace",
        to_branch="main",
        author="musician",
    )
    db_session.add(pr)
    await db_session.flush()
    await db_session.refresh(pr)
    pr_id = pr.pr_id
    await db_session.commit()

    response = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/diff",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["dimensions"]) == 5
    assert data["overallScore"] == 0.0
    for dim in data["dimensions"]:
        assert dim["score"] == 0.0
        assert dim["level"] == "NONE"
        assert dim["deltaLabel"] == "unchanged"


@pytest.mark.anyio
async def test_pr_merge_strategy_squash_accepted(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """POST /pull-requests/{pr_id}/merge accepts 'squash' as a valid mergeStrategy."""
    repo_id = await _create_repo(client, auth_headers, "strategy-squash-repo")
    await _push_branch(db_session, repo_id, "feat/squash-test")
    await _push_branch(db_session, repo_id, "main")
    pr_resp = await _create_pr(client, auth_headers, repo_id, from_branch="feat/squash-test", to_branch="main")
    pr_id = pr_resp["prId"]

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/merge",
        json={"mergeStrategy": "squash"},
        headers=auth_headers,
    )
    # squash is now a valid strategy in the Pydantic model; merge logic uses merge_commit internally
    assert response.status_code == 200
    data = response.json()
    assert data["merged"] is True


@pytest.mark.anyio
async def test_pr_merge_strategy_rebase_accepted(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """POST /pull-requests/{pr_id}/merge accepts 'rebase' as a valid mergeStrategy."""
    repo_id = await _create_repo(client, auth_headers, "strategy-rebase-repo")
    await _push_branch(db_session, repo_id, "feat/rebase-test")
    await _push_branch(db_session, repo_id, "main")
    pr_resp = await _create_pr(client, auth_headers, repo_id, from_branch="feat/rebase-test", to_branch="main")
    pr_id = pr_resp["prId"]

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/merge",
        json={"mergeStrategy": "rebase"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["merged"] is True
