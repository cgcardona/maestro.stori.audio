"""Tests for Muse Hub pull request endpoints.

Covers every acceptance criterion from issue #41:
- POST /musehub/repos/{repo_id}/pull-requests creates PR in open state
- 422 when from_branch == to_branch
- 404 when from_branch does not exist
- GET /pull-requests returns all PRs (open + merged + closed)
- GET /pull-requests/{pr_id} returns full PR detail; 404 if not found
- POST /pull-requests/{pr_id}/merge creates merge commit, sets state merged
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


# ---------------------------------------------------------------------------
# PR review comments — issue #216
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_pr_comment(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """POST /pull-requests/{pr_id}/comments creates a comment and returns threaded list."""
    repo_id = await _create_repo(client, auth_headers, "comment-create-repo")
    await _push_branch(db_session, repo_id, "feat/comment-test")
    pr = await _create_pr(client, auth_headers, repo_id, from_branch="feat/comment-test")

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr['prId']}/comments",
        json={"body": "The bass line feels stiff — add swing.", "targetType": "general"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert "comments" in data
    assert "total" in data
    assert data["total"] == 1
    comment = data["comments"][0]
    assert comment["body"] == "The bass line feels stiff — add swing."
    assert comment["targetType"] == "general"
    assert "commentId" in comment
    assert "createdAt" in comment


@pytest.mark.anyio
async def test_list_pr_comments_threaded(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /pull-requests/{pr_id}/comments returns top-level comments with nested replies."""
    repo_id = await _create_repo(client, auth_headers, "comment-list-repo")
    await _push_branch(db_session, repo_id, "feat/list-comments")
    pr = await _create_pr(client, auth_headers, repo_id, from_branch="feat/list-comments")
    pr_id = pr["prId"]

    # Create a top-level comment
    create_resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/comments",
        json={"body": "Top-level comment.", "targetType": "general"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    parent_id = create_resp.json()["comments"][0]["commentId"]

    # Reply to it
    reply_resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/comments",
        json={"body": "A reply.", "targetType": "general", "parentCommentId": parent_id},
        headers=auth_headers,
    )
    assert reply_resp.status_code == 201

    # Fetch threaded list
    list_resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/comments",
        headers=auth_headers,
    )
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["total"] == 2
    # Only one top-level comment
    assert len(data["comments"]) == 1
    top = data["comments"][0]
    assert len(top["replies"]) == 1
    assert top["replies"][0]["body"] == "A reply."


@pytest.mark.anyio
async def test_comment_targets_track(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """POST /comments with target_type=region stores track and beat range correctly."""
    repo_id = await _create_repo(client, auth_headers, "comment-track-repo")
    await _push_branch(db_session, repo_id, "feat/track-comment")
    pr = await _create_pr(client, auth_headers, repo_id, from_branch="feat/track-comment")

    response = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr['prId']}/comments",
        json={
            "body": "Beats 16-24 on bass feel rushed.",
            "targetType": "region",
            "targetTrack": "bass",
            "targetBeatStart": 16.0,
            "targetBeatEnd": 24.0,
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    comment = response.json()["comments"][0]
    assert comment["targetType"] == "region"
    assert comment["targetTrack"] == "bass"
    assert comment["targetBeatStart"] == 16.0
    assert comment["targetBeatEnd"] == 24.0


@pytest.mark.anyio
async def test_comment_requires_auth(client: AsyncClient) -> None:
    """POST /pull-requests/{pr_id}/comments returns 401 without a Bearer token."""
    response = await client.post(
        "/api/v1/musehub/repos/r/pull-requests/p/comments",
        json={"body": "Unauthorized attempt."},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_reply_to_comment(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Replying to a comment creates a threaded child visible in the list."""
    repo_id = await _create_repo(client, auth_headers, "comment-reply-repo")
    await _push_branch(db_session, repo_id, "feat/reply-test")
    pr = await _create_pr(client, auth_headers, repo_id, from_branch="feat/reply-test")
    pr_id = pr["prId"]

    parent_resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/comments",
        json={"body": "Original comment.", "targetType": "general"},
        headers=auth_headers,
    )
    parent_id = parent_resp.json()["comments"][0]["commentId"]

    reply_resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/pull-requests/{pr_id}/comments",
        json={"body": "Reply here.", "targetType": "general", "parentCommentId": parent_id},
        headers=auth_headers,
    )
    assert reply_resp.status_code == 201
    data = reply_resp.json()
    # Still only one top-level comment; total is 2
    assert data["total"] == 2
    assert len(data["comments"]) == 1
    reply = data["comments"][0]["replies"][0]
    assert reply["body"] == "Reply here."
    assert reply["parentCommentId"] == parent_id
