"""Tests for Muse Hub social layer — comments, reactions, follows, watches, forks, feed.

Covers the endpoints in maestro/api/routes/musehub/social.py:
- Comments: list, create, delete (auth guard, ownership, soft-delete)
- Reactions: list, toggle (add + remove idempotency)
- Follows: follow, unfollow, follower count
- Watches: watch, unwatch, watch count
- Notifications: list, mark read, mark all read
- Forks: fork, list forks
- Feed: requires auth
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(client: AsyncClient, auth_headers: dict[str, str],
                     name: str = "social-test-repo") -> str:
    """Create a public repo and return its repoId."""
    resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": name, "owner": "testuser", "visibility": "public"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["repoId"])


async def _make_commit(client: AsyncClient, auth_headers: dict[str, str],
                       repo_id: str, message: str = "initial",
                       commit_id: str = "abc00001") -> str:
    """Push a commit to a repo via the sync push endpoint and return commit_id."""
    from datetime import datetime, timezone
    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/push",
        json={
            "branch": "main",
            "headCommitId": commit_id,
            "commits": [{
                "commitId": commit_id,
                "parentIds": [],
                "message": message,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }],
            "objects": [],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return commit_id


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_comment_returns_201(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/comments returns 201 with the comment payload."""
    repo_id = await _make_repo(client, auth_headers, "comments-test-1")
    commit_id = await _make_commit(client, auth_headers, repo_id)

    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        json={"target_type": "commit", "target_id": commit_id, "body": "Great take!"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["body"] == "Great take!"
    assert data["target_type"] == "commit"
    assert data["target_id"] == commit_id
    assert data["is_deleted"] is False


@pytest.mark.anyio
async def test_create_comment_requires_auth(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/comments returns 401 without a Bearer token."""
    repo_id = await _make_repo(client, auth_headers, "comments-test-auth")
    commit_id = await _make_commit(client, auth_headers, repo_id)

    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        json={"target_type": "commit", "target_id": commit_id, "body": "No auth"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_list_comments_returns_posted_comments(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """GET /repos/{id}/comments returns comments for the given target."""
    repo_id = await _make_repo(client, auth_headers, "comments-list-test")
    commit_id = await _make_commit(client, auth_headers, repo_id)

    await client.post(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        json={"target_type": "commit", "target_id": commit_id, "body": "Comment 1"},
        headers=auth_headers,
    )
    await client.post(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        json={"target_type": "commit", "target_id": commit_id, "body": "Comment 2"},
        headers=auth_headers,
    )

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        params={"target_type": "commit", "target_id": commit_id},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 2
    bodies = [c["body"] for c in data]
    assert "Comment 1" in bodies
    assert "Comment 2" in bodies


@pytest.mark.anyio
async def test_delete_comment_soft_deletes(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """DELETE /repos/{id}/comments/{cid} soft-deletes; comment disappears from list."""
    repo_id = await _make_repo(client, auth_headers, "comments-delete-test")
    commit_id = await _make_commit(client, auth_headers, repo_id)

    create_resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        json={"target_type": "commit", "target_id": commit_id, "body": "To be deleted"},
        headers=auth_headers,
    )
    comment_id = create_resp.json()["comment_id"]

    del_resp = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/comments/{comment_id}",
        headers=auth_headers,
    )
    assert del_resp.status_code == 204

    # Soft-deleted comments are excluded from list
    list_resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        params={"target_type": "commit", "target_id": commit_id},
    )
    assert list_resp.status_code == 200
    assert list_resp.json() == []


@pytest.mark.anyio
async def test_delete_comment_requires_auth(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """DELETE /repos/{id}/comments/{cid} returns 401 without a Bearer token."""
    repo_id = await _make_repo(client, auth_headers, "comments-del-auth-test")
    commit_id = await _make_commit(client, auth_headers, repo_id)

    create_resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        json={"target_type": "commit", "target_id": commit_id, "body": "Owned"},
        headers=auth_headers,
    )
    comment_id = create_resp.json()["comment_id"]

    del_resp = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/comments/{comment_id}",
    )
    assert del_resp.status_code == 401


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_toggle_reaction_adds_then_removes(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/reactions toggles: first call adds, second removes."""
    repo_id = await _make_repo(client, auth_headers, "reactions-toggle-test")
    commit_id = await _make_commit(client, auth_headers, repo_id)

    payload = {"target_type": "commit", "target_id": commit_id, "emoji": "👍"}

    r1 = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/reactions",
        json=payload,
        headers=auth_headers,
    )
    assert r1.status_code == 201
    assert r1.json()["added"] is True

    r2 = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/reactions",
        json=payload,
        headers=auth_headers,
    )
    assert r2.status_code == 201
    assert r2.json()["added"] is False


@pytest.mark.anyio
async def test_toggle_reaction_invalid_emoji_returns_400(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/reactions returns 400 for an emoji not in the allowed set."""
    repo_id = await _make_repo(client, auth_headers, "reactions-bad-emoji")
    commit_id = await _make_commit(client, auth_headers, repo_id)

    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/reactions",
        json={"target_type": "commit", "target_id": commit_id, "emoji": "🍕"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_list_reactions_counts_correctly(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """GET /repos/{id}/reactions returns per-emoji counts after a toggle."""
    repo_id = await _make_repo(client, auth_headers, "reactions-count-test")
    commit_id = await _make_commit(client, auth_headers, repo_id)

    await client.post(
        f"/api/v1/musehub/repos/{repo_id}/reactions",
        json={"target_type": "commit", "target_id": commit_id, "emoji": "🔥"},
        headers=auth_headers,
    )

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/reactions",
        params={"target_type": "commit", "target_id": commit_id},
    )
    assert resp.status_code == 200
    data = resp.json()
    fire = next((r for r in data if r["emoji"] == "🔥"), None)
    assert fire is not None
    assert fire["count"] == 1
    assert fire["reacted_by_me"] is False  # unauthenticated list request


@pytest.mark.anyio
async def test_toggle_reaction_requires_auth(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/reactions returns 401 without a Bearer token."""
    repo_id = await _make_repo(client, auth_headers, "reactions-auth-test")
    commit_id = await _make_commit(client, auth_headers, repo_id)

    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/reactions",
        json={"target_type": "commit", "target_id": commit_id, "emoji": "👍"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Follows
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_follow_user_increases_follower_count(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """POST /users/{username}/follow increases follower count from 0 to 1."""
    count_before = (await client.get("/api/v1/musehub/users/alice/followers")).json()
    assert count_before["follower_count"] == 0

    resp = await client.post("/api/v1/musehub/users/alice/follow", headers=auth_headers)
    assert resp.status_code == 201
    assert resp.json()["following"] is True

    count_after = (await client.get("/api/v1/musehub/users/alice/followers")).json()
    assert count_after["follower_count"] == 1


@pytest.mark.anyio
async def test_follow_self_returns_400(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """POST /users/{username}/follow returns 400 when following yourself."""
    # test_user's sub is "550e8400-e29b-41d4-a716-446655440000"
    resp = await client.post(
        "/api/v1/musehub/users/550e8400-e29b-41d4-a716-446655440000/follow",
        headers=auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_follow_is_idempotent(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """Posting follow twice does not create duplicate entries (unique constraint)."""
    await client.post("/api/v1/musehub/users/bob/follow", headers=auth_headers)
    resp = await client.post("/api/v1/musehub/users/bob/follow", headers=auth_headers)
    assert resp.status_code == 201  # idempotent — no 409

    count = (await client.get("/api/v1/musehub/users/bob/followers")).json()
    assert count["follower_count"] == 1


@pytest.mark.anyio
async def test_unfollow_user_decreases_count(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """DELETE /users/{username}/follow decreases follower count to 0."""
    await client.post("/api/v1/musehub/users/carol/follow", headers=auth_headers)
    del_resp = await client.delete("/api/v1/musehub/users/carol/follow", headers=auth_headers)
    assert del_resp.status_code == 204

    count = (await client.get("/api/v1/musehub/users/carol/followers")).json()
    assert count["follower_count"] == 0


@pytest.mark.anyio
async def test_follow_requires_auth(client: AsyncClient) -> None:
    """POST /users/{username}/follow returns 401 without a Bearer token."""
    resp = await client.post("/api/v1/musehub/users/dave/follow")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Watches
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_watch_repo_increases_count(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/watch increases watch count from 0 to 1."""
    repo_id = await _make_repo(client, auth_headers, "watch-count-test")

    count_before = (await client.get(f"/api/v1/musehub/repos/{repo_id}/watches")).json()
    assert count_before["watch_count"] == 0

    resp = await client.post(f"/api/v1/musehub/repos/{repo_id}/watch", headers=auth_headers)
    assert resp.status_code == 201
    assert resp.json()["watching"] is True

    count_after = (await client.get(f"/api/v1/musehub/repos/{repo_id}/watches")).json()
    assert count_after["watch_count"] == 1


@pytest.mark.anyio
async def test_unwatch_repo_decreases_count(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """DELETE /repos/{id}/watch decreases watch count back to 0."""
    repo_id = await _make_repo(client, auth_headers, "unwatch-test")

    await client.post(f"/api/v1/musehub/repos/{repo_id}/watch", headers=auth_headers)
    del_resp = await client.delete(f"/api/v1/musehub/repos/{repo_id}/watch", headers=auth_headers)
    assert del_resp.status_code == 204

    count = (await client.get(f"/api/v1/musehub/repos/{repo_id}/watches")).json()
    assert count["watch_count"] == 0


@pytest.mark.anyio
async def test_watch_requires_auth(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/watch returns 401 without a Bearer token."""
    repo_id = await _make_repo(client, auth_headers, "watch-auth-test")
    resp = await client.post(f"/api/v1/musehub/repos/{repo_id}/watch")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_notifications_inbox_empty_for_new_user(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """GET /musehub/notifications returns empty list for a user with no events."""
    resp = await client.get("/api/v1/musehub/notifications", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_notifications_requires_auth(client: AsyncClient) -> None:
    """GET /musehub/notifications returns 401 without a Bearer token."""
    resp = await client.get("/api/v1/musehub/notifications")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_mark_all_notifications_read(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """POST /musehub/notifications/read-all returns 200 with marked_read count (idempotent)."""
    resp = await client.post("/api/v1/musehub/notifications/read-all", headers=auth_headers)
    assert resp.status_code == 200
    assert "marked_read" in resp.json()


# ---------------------------------------------------------------------------
# Forks
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fork_repo_creates_fork(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/fork returns 201 with fork metadata."""
    source_id = await _make_repo(client, auth_headers, "fork-source-repo")

    resp = await client.post(
        f"/api/v1/musehub/repos/{source_id}/fork",
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["source_repo_id"] == source_id
    assert "fork_repo_id" in data


@pytest.mark.anyio
async def test_list_forks_returns_created_fork(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """GET /repos/{id}/forks shows the repo after forking."""
    source_id = await _make_repo(client, auth_headers, "fork-list-source")

    await client.post(f"/api/v1/musehub/repos/{source_id}/fork", headers=auth_headers)

    resp = await client.get(f"/api/v1/musehub/repos/{source_id}/forks")
    assert resp.status_code == 200
    forks = resp.json()
    assert len(forks) == 1
    assert forks[0]["source_repo_id"] == source_id


@pytest.mark.anyio
async def test_fork_requires_auth(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/fork returns 401 without a Bearer token."""
    source_id = await _make_repo(client, auth_headers, "fork-auth-source")
    resp = await client.post(f"/api/v1/musehub/repos/{source_id}/fork")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Activity feed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_feed_requires_auth(client: AsyncClient) -> None:
    """GET /musehub/feed returns 401 without a Bearer token."""
    resp = await client.get("/api/v1/musehub/feed")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_feed_returns_list_for_authed_user(
    client: AsyncClient, auth_headers: dict[str, str],
) -> None:
    """GET /musehub/feed returns a list (possibly empty) for an authenticated user."""
    resp = await client.get("/api/v1/musehub/feed", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
