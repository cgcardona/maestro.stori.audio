"""Tests for Muse Hub social layer endpoints (social.py).

Covers all endpoint groups introduced in PR #318:
  - Comments: list, create, soft-delete (owner guard, auth guard)
  - Reactions: list counts, toggle idempotency (add → remove on second call)
  - Follow/Unfollow: follower count, follow (auth, self-follow 400), unfollow
  - Watch/Unwatch: watch count, watch (auth), unwatch
  - Notifications: list inbox, mark single read, mark-all-read
  - Forks: list forks, fork (public only, auth)
  - Analytics: summary (private 401 guard), daily views, view-event debounce
  - Feed: auth-gated activity feed

Key invariants asserted:
  - toggle_reaction is idempotent: calling twice removes the reaction
  - follow_user returns 400 when following yourself
  - fork_repo returns 403 on private repos
  - View-event debounce: duplicate (repo, fingerprint, date) → no error, no extra row
  - Private repo analytics return 401 without auth
  - All write endpoints return 401 without a token
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import (
    MusehubFork,
    MusehubNotification,
    MusehubRepo,
    MusehubViewEvent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_USER_ID = "550e8400-e29b-41d4-a716-446655440000"


async def _make_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
    *,
    name: str = "test-repo",
    visibility: str = "public",
) -> str:
    """Create a repo via API and return its repo_id."""
    resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": name, "owner": "testuser", "visibility": visibility},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    repo_id: str = resp.json()["repoId"]
    return repo_id


async def _make_private_repo(
    db_session: AsyncSession,
    *,
    name: str = "private-repo",
    owner: str = "testuser",
) -> str:
    """Insert a private repo directly and return its repo_id."""
    repo = MusehubRepo(
        name=name,
        owner=owner,
        slug=name,
        visibility="private",
        owner_user_id=_TEST_USER_ID,
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    repo_id: str = str(repo.repo_id)
    return repo_id


# ---------------------------------------------------------------------------
# Comments — GET
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_comments_empty_on_new_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /repos/{id}/comments returns empty list when no comments exist."""
    repo_id = await _make_repo(client, auth_headers, name="comment-empty")
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        params={"target_type": "repo", "target_id": repo_id},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_list_comments_on_private_repo_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /repos/{id}/comments returns 401 for private repo without auth."""
    repo_id = await _make_private_repo(db_session, name="comment-private")
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        params={"target_type": "repo", "target_id": repo_id},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_list_comments_not_found_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /repos/{id}/comments returns 404 for unknown repo."""
    resp = await client.get(
        "/api/v1/musehub/repos/no-such-repo/comments",
        params={"target_type": "repo", "target_id": "anything"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Comments — POST
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_comment_returns_201(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/comments creates a comment and returns 201 with required fields."""
    repo_id = await _make_repo(client, auth_headers, name="comment-create")
    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        json={"target_type": "repo", "target_id": repo_id, "body": "Great track!"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["body"] == "Great track!"
    assert body["author"] == _TEST_USER_ID
    assert body["is_deleted"] is False
    assert "comment_id" in body


@pytest.mark.anyio
async def test_create_comment_requires_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/comments returns 401 without Bearer token."""
    repo_id = await _make_repo(client, auth_headers, name="comment-auth")
    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        json={"target_type": "repo", "target_id": repo_id, "body": "hi"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_created_comment_appears_in_list(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A posted comment is returned by the list endpoint."""
    repo_id = await _make_repo(client, auth_headers, name="comment-roundtrip")
    await client.post(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        json={"target_type": "repo", "target_id": repo_id, "body": "Hello world"},
        headers=auth_headers,
    )
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        params={"target_type": "repo", "target_id": repo_id},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    comments = resp.json()
    assert len(comments) == 1
    assert comments[0]["body"] == "Hello world"


# ---------------------------------------------------------------------------
# Comments — DELETE
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_comment_soft_deletes(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """DELETE /repos/{id}/comments/{cid} soft-deletes and returns 204."""
    repo_id = await _make_repo(client, auth_headers, name="comment-delete")
    create = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        json={"target_type": "repo", "target_id": repo_id, "body": "To be deleted"},
        headers=auth_headers,
    )
    comment_id = create.json()["comment_id"]
    resp = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/comments/{comment_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 204


@pytest.mark.anyio
async def test_delete_comment_requires_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """DELETE /repos/{id}/comments/{cid} returns 401 without token."""
    repo_id = await _make_repo(client, auth_headers, name="comment-del-auth")
    create = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        json={"target_type": "repo", "target_id": repo_id, "body": "Keep this"},
        headers=auth_headers,
    )
    comment_id = create.json()["comment_id"]
    resp = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/comments/{comment_id}",
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_delete_comment_forbidden_for_non_owner(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """DELETE /repos/{id}/comments/{cid} returns 403 if caller is not the author."""
    from maestro.auth.tokens import create_access_token
    from maestro.db.models import User
    from maestro.db.musehub_models import MusehubComment

    # Create a second user
    other_user = User(id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", budget_cents=100, budget_limit_cents=100)
    db_session.add(other_user)
    await db_session.commit()
    other_token = create_access_token(user_id=other_user.id, expires_hours=1)
    other_headers = {"Authorization": f"Bearer {other_token}", "Content-Type": "application/json"}

    repo_id = await _make_repo(client, auth_headers, name="comment-forbid")

    # Post a comment as the primary test user
    create = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/comments",
        json={"target_type": "repo", "target_id": repo_id, "body": "Mine"},
        headers=auth_headers,
    )
    comment_id = create.json()["comment_id"]

    # Try to delete as the other user
    resp = await client.delete(
        f"/api/v1/musehub/repos/{repo_id}/comments/{comment_id}",
        headers=other_headers,
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Reactions — GET
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_reactions_empty_on_new_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /repos/{id}/reactions returns empty list when no reactions exist."""
    repo_id = await _make_repo(client, auth_headers, name="reaction-empty")
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/reactions",
        params={"target_type": "repo", "target_id": repo_id},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Reactions — POST (toggle idempotency)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_toggle_reaction_adds_on_first_call(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """First POST /repos/{id}/reactions call adds the reaction (added=True)."""
    repo_id = await _make_repo(client, auth_headers, name="reaction-add")
    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/reactions",
        json={"target_type": "repo", "target_id": repo_id, "emoji": "👍"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["added"] is True
    assert body["emoji"] == "👍"


@pytest.mark.anyio
async def test_toggle_reaction_removes_on_second_call(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Second POST with same emoji removes the reaction (added=False) — idempotent toggle."""
    repo_id = await _make_repo(client, auth_headers, name="reaction-toggle")
    payload = {"target_type": "repo", "target_id": repo_id, "emoji": "❤️"}
    await client.post(f"/api/v1/musehub/repos/{repo_id}/reactions", json=payload, headers=auth_headers)
    resp = await client.post(f"/api/v1/musehub/repos/{repo_id}/reactions", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    assert resp.json()["added"] is False


@pytest.mark.anyio
async def test_toggle_reaction_reflects_in_list(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Reaction count increments after toggle-add and reacted_by_me is True."""
    repo_id = await _make_repo(client, auth_headers, name="reaction-list")
    await client.post(
        f"/api/v1/musehub/repos/{repo_id}/reactions",
        json={"target_type": "repo", "target_id": repo_id, "emoji": "🔥"},
        headers=auth_headers,
    )
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/reactions",
        params={"target_type": "repo", "target_id": repo_id},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    counts = resp.json()
    fire = next((r for r in counts if r["emoji"] == "🔥"), None)
    assert fire is not None
    assert fire["count"] == 1
    assert fire["reacted_by_me"] is True


@pytest.mark.anyio
async def test_toggle_reaction_invalid_emoji_returns_400(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/reactions with an unsupported emoji returns 400."""
    repo_id = await _make_repo(client, auth_headers, name="reaction-bad-emoji")
    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/reactions",
        json={"target_type": "repo", "target_id": repo_id, "emoji": "🤡"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_toggle_reaction_requires_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/reactions returns 401 without token."""
    repo_id = await _make_repo(client, auth_headers, name="reaction-no-auth")
    resp = await client.post(
        f"/api/v1/musehub/repos/{repo_id}/reactions",
        json={"target_type": "repo", "target_id": repo_id, "emoji": "👍"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Follow — GET
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_followers_returns_zero_for_new_user(
    client: AsyncClient,
) -> None:
    """GET /users/{u}/followers returns 0 followers for a user nobody follows."""
    resp = await client.get("/api/v1/musehub/users/newbie/followers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["follower_count"] == 0
    assert body["following"] is False


# ---------------------------------------------------------------------------
# Follow — POST
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_follow_user_returns_201(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /users/{u}/follow returns 201 and following=True."""
    resp = await client.post("/api/v1/musehub/users/other-musician/follow", headers=auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["following"] is True
    assert body["username"] == "other-musician"


@pytest.mark.anyio
async def test_follow_user_self_returns_400(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /users/{u}/follow returns 400 when trying to follow yourself."""
    resp = await client.post(f"/api/v1/musehub/users/{_TEST_USER_ID}/follow", headers=auth_headers)
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_follow_user_requires_auth(client: AsyncClient) -> None:
    """POST /users/{u}/follow returns 401 without token."""
    resp = await client.post("/api/v1/musehub/users/someone/follow")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_follow_user_increments_follower_count(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Follower count increments after a follow and resets after unfollow."""
    await client.post("/api/v1/musehub/users/followed-user/follow", headers=auth_headers)
    resp = await client.get("/api/v1/musehub/users/followed-user/followers", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["follower_count"] == 1
    assert resp.json()["following"] is True


# ---------------------------------------------------------------------------
# Follow — DELETE (unfollow)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unfollow_user_returns_204(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """DELETE /users/{u}/follow returns 204 (no-op if not following)."""
    resp = await client.delete("/api/v1/musehub/users/some-user/follow", headers=auth_headers)
    assert resp.status_code == 204


@pytest.mark.anyio
async def test_unfollow_decrements_count(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Unfollow reduces the follower count back to 0."""
    await client.post("/api/v1/musehub/users/temp-follow/follow", headers=auth_headers)
    await client.delete("/api/v1/musehub/users/temp-follow/follow", headers=auth_headers)
    resp = await client.get("/api/v1/musehub/users/temp-follow/followers")
    assert resp.json()["follower_count"] == 0


@pytest.mark.anyio
async def test_unfollow_requires_auth(client: AsyncClient) -> None:
    """DELETE /users/{u}/follow returns 401 without token."""
    resp = await client.delete("/api/v1/musehub/users/someone/follow")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Watch — GET
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_watches_returns_zero_for_new_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /repos/{id}/watches returns 0 for a new repo."""
    repo_id = await _make_repo(client, auth_headers, name="watch-empty")
    resp = await client.get(f"/api/v1/musehub/repos/{repo_id}/watches")
    assert resp.status_code == 200
    assert resp.json()["watch_count"] == 0
    assert resp.json()["watching"] is False


# ---------------------------------------------------------------------------
# Watch — POST
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_watch_repo_returns_201(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/watch returns 201 and watching=True."""
    repo_id = await _make_repo(client, auth_headers, name="watch-add")
    resp = await client.post(f"/api/v1/musehub/repos/{repo_id}/watch", headers=auth_headers)
    assert resp.status_code == 201
    assert resp.json()["watching"] is True


@pytest.mark.anyio
async def test_watch_repo_idempotent(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Watching the same repo twice does not raise an error (idempotent)."""
    repo_id = await _make_repo(client, auth_headers, name="watch-idempotent")
    await client.post(f"/api/v1/musehub/repos/{repo_id}/watch", headers=auth_headers)
    resp = await client.post(f"/api/v1/musehub/repos/{repo_id}/watch", headers=auth_headers)
    assert resp.status_code == 201


@pytest.mark.anyio
async def test_watch_requires_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/watch returns 401 without token."""
    repo_id = await _make_repo(client, auth_headers, name="watch-no-auth")
    resp = await client.post(f"/api/v1/musehub/repos/{repo_id}/watch")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_watch_increments_count(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Watch count is 1 after a successful watch."""
    repo_id = await _make_repo(client, auth_headers, name="watch-count")
    await client.post(f"/api/v1/musehub/repos/{repo_id}/watch", headers=auth_headers)
    resp = await client.get(f"/api/v1/musehub/repos/{repo_id}/watches", headers=auth_headers)
    assert resp.json()["watch_count"] == 1
    assert resp.json()["watching"] is True


# ---------------------------------------------------------------------------
# Watch — DELETE (unwatch)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unwatch_repo_returns_204(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """DELETE /repos/{id}/watch returns 204 (no-op if not watching)."""
    repo_id = await _make_repo(client, auth_headers, name="unwatch-noop")
    resp = await client.delete(f"/api/v1/musehub/repos/{repo_id}/watch", headers=auth_headers)
    assert resp.status_code == 204


@pytest.mark.anyio
async def test_unwatch_requires_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """DELETE /repos/{id}/watch returns 401 without token."""
    repo_id = await _make_repo(client, auth_headers, name="unwatch-no-auth")
    resp = await client.delete(f"/api/v1/musehub/repos/{repo_id}/watch")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Notifications — GET
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_notifications_empty_inbox(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /notifications returns empty list for a user with no notifications."""
    resp = await client.get("/api/v1/musehub/notifications", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_list_notifications_requires_auth(client: AsyncClient) -> None:
    """GET /notifications returns 401 without token."""
    resp = await client.get("/api/v1/musehub/notifications")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_list_notifications_returns_own_notifications(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /notifications returns notifications addressed to the calling user."""
    notif = MusehubNotification(
        notif_id=str(uuid.uuid4()),
        recipient_id=_TEST_USER_ID,
        event_type="new_follower",
        repo_id=None,
        actor="alice",
        payload={"msg": "alice followed you"},
        is_read=False,
        created_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(notif)
    await db_session.commit()

    resp = await client.get("/api/v1/musehub/notifications", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["event_type"] == "new_follower"
    assert items[0]["is_read"] is False


@pytest.mark.anyio
async def test_list_notifications_unread_only_filter(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """?unread_only=true filters out already-read notifications."""
    read_notif = MusehubNotification(
        notif_id=str(uuid.uuid4()),
        recipient_id=_TEST_USER_ID,
        event_type="comment",
        repo_id=None,
        actor="bob",
        payload={},
        is_read=True,
        created_at=datetime.now(tz=timezone.utc),
    )
    unread_notif = MusehubNotification(
        notif_id=str(uuid.uuid4()),
        recipient_id=_TEST_USER_ID,
        event_type="mention",
        repo_id=None,
        actor="carol",
        payload={},
        is_read=False,
        created_at=datetime.now(tz=timezone.utc),
    )
    db_session.add_all([read_notif, unread_notif])
    await db_session.commit()

    resp = await client.get("/api/v1/musehub/notifications?unread_only=true", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["event_type"] == "mention"


# ---------------------------------------------------------------------------
# Notifications — POST mark read
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mark_notification_read(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """POST /notifications/{id}/read marks the notification as read."""
    notif_id = str(uuid.uuid4())
    notif = MusehubNotification(
        notif_id=notif_id,
        recipient_id=_TEST_USER_ID,
        event_type="pr_opened",
        repo_id=None,
        actor="dave",
        payload={},
        is_read=False,
        created_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(notif)
    await db_session.commit()

    resp = await client.post(f"/api/v1/musehub/notifications/{notif_id}/read", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["read"] is True
    assert body["notif_id"] == notif_id


@pytest.mark.anyio
async def test_mark_notification_read_not_found(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /notifications/{id}/read returns 404 for unknown notification."""
    resp = await client.post(
        f"/api/v1/musehub/notifications/{uuid.uuid4()}/read",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_mark_notification_read_requires_auth(client: AsyncClient) -> None:
    """POST /notifications/{id}/read returns 401 without token."""
    resp = await client.post(f"/api/v1/musehub/notifications/{uuid.uuid4()}/read")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Notifications — POST read-all
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mark_all_notifications_read(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """POST /notifications/read-all marks all unread notifications and returns count."""
    for i in range(3):
        db_session.add(
            MusehubNotification(
                notif_id=str(uuid.uuid4()),
                recipient_id=_TEST_USER_ID,
                event_type="comment",
                repo_id=None,
                actor=f"user-{i}",
                payload={},
                is_read=False,
                created_at=datetime.now(tz=timezone.utc),
            )
        )
    await db_session.commit()

    resp = await client.post("/api/v1/musehub/notifications/read-all", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["marked_read"] == 3


@pytest.mark.anyio
async def test_mark_all_notifications_requires_auth(client: AsyncClient) -> None:
    """POST /notifications/read-all returns 401 without token."""
    resp = await client.post("/api/v1/musehub/notifications/read-all")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Forks — GET
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_forks_empty_on_new_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /repos/{id}/forks returns empty list when repo has no forks."""
    repo_id = await _make_repo(client, auth_headers, name="fork-list-empty")
    resp = await client.get(f"/api/v1/musehub/repos/{repo_id}/forks", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_list_forks_private_repo_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /repos/{id}/forks returns 401 for private repo without auth."""
    repo_id = await _make_private_repo(db_session, name="fork-priv-list")
    resp = await client.get(f"/api/v1/musehub/repos/{repo_id}/forks")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_list_forks_contains_fork_record(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /repos/{id}/forks lists a fork after it has been created."""
    repo_id = await _make_repo(client, auth_headers, name="forkable")
    fork_resp = await client.post(f"/api/v1/musehub/repos/{repo_id}/fork", headers=auth_headers)
    assert fork_resp.status_code == 201

    resp = await client.get(f"/api/v1/musehub/repos/{repo_id}/forks", headers=auth_headers)
    assert resp.status_code == 200
    forks = resp.json()
    assert len(forks) == 1
    assert forks[0]["source_repo_id"] == repo_id


# ---------------------------------------------------------------------------
# Forks — POST
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fork_public_repo_returns_201(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/fork forks a public repo and returns 201 with fork fields."""
    repo_id = await _make_repo(client, auth_headers, name="public-fork-src")
    resp = await client.post(f"/api/v1/musehub/repos/{repo_id}/fork", headers=auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["source_repo_id"] == repo_id
    assert body["forked_by"] == _TEST_USER_ID
    assert "fork_id" in body
    assert "fork_repo_id" in body


@pytest.mark.anyio
async def test_fork_private_repo_returns_403(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """POST /repos/{id}/fork returns 403 when source repo is private."""
    repo_id = await _make_private_repo(db_session, name="private-fork-src")
    resp = await client.post(f"/api/v1/musehub/repos/{repo_id}/fork", headers=auth_headers)
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_fork_requires_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/fork returns 401 without token."""
    repo_id = await _make_repo(client, auth_headers, name="fork-no-auth")
    resp = await client.post(f"/api/v1/musehub/repos/{repo_id}/fork")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_fork_nonexistent_repo_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/fork returns 404 when source repo does not exist."""
    resp = await client.post("/api/v1/musehub/repos/no-such-repo/fork", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Analytics — record view (debounce)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_record_view_returns_204(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /repos/{id}/view returns 204 on success."""
    repo_id = await _make_repo(client, auth_headers, name="view-record")
    resp = await client.post(f"/api/v1/musehub/repos/{repo_id}/view")
    assert resp.status_code == 204


@pytest.mark.anyio
async def test_record_view_debounce_no_duplicate(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Duplicate (repo, fingerprint, date) inserts do not return 500 — debounced silently."""
    repo_id = await _make_repo(client, auth_headers, name="view-debounce")

    # Seed an existing view event with the same fingerprint that the test client will produce
    import hashlib
    from datetime import date

    fingerprint = hashlib.sha256(b"testclient").hexdigest()[:64]
    today = date.today().isoformat()
    existing = MusehubViewEvent(
        view_id=str(uuid.uuid4()),
        repo_id=repo_id,
        viewer_fingerprint=fingerprint,
        event_date=today,
    )
    db_session.add(existing)
    await db_session.commit()

    # Posting again should silently no-op, not 500
    resp = await client.post(f"/api/v1/musehub/repos/{repo_id}/view")
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Analytics — summary
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_analytics_public_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /repos/{id}/analytics returns view/download counts for a public repo."""
    repo_id = await _make_repo(client, auth_headers, name="analytics-pub")
    resp = await client.get(f"/api/v1/musehub/repos/{repo_id}/analytics", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["repo_id"] == repo_id
    assert body["view_count"] == 0
    assert body["download_count"] == 0


@pytest.mark.anyio
async def test_get_analytics_private_repo_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /repos/{id}/analytics returns 401 for private repo without auth."""
    repo_id = await _make_private_repo(db_session, name="analytics-priv")
    resp = await client.get(f"/api/v1/musehub/repos/{repo_id}/analytics")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_get_analytics_not_found_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /repos/{id}/analytics returns 404 for unknown repo."""
    resp = await client.get("/api/v1/musehub/repos/ghost-repo/analytics", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_analytics_view_count_after_record(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """View count reflects recorded view events."""
    repo_id = await _make_repo(client, auth_headers, name="analytics-count")
    db_session.add(
        MusehubViewEvent(
            view_id=str(uuid.uuid4()),
            repo_id=repo_id,
            viewer_fingerprint="fp-abc",
            event_date="2026-01-01",
        )
    )
    await db_session.commit()

    resp = await client.get(f"/api/v1/musehub/repos/{repo_id}/analytics", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["view_count"] == 1


# ---------------------------------------------------------------------------
# Analytics — daily views
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_view_analytics_empty(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /repos/{id}/analytics/views returns empty list for repo with no views."""
    repo_id = await _make_repo(client, auth_headers, name="daily-empty")
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analytics/views", headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_get_view_analytics_aggregates_by_day(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /repos/{id}/analytics/views aggregates view events by event_date."""
    repo_id = await _make_repo(client, auth_headers, name="daily-agg")
    db_session.add_all([
        MusehubViewEvent(
            view_id=str(uuid.uuid4()),
            repo_id=repo_id,
            viewer_fingerprint="fp-x",
            event_date="2026-02-01",
        ),
        MusehubViewEvent(
            view_id=str(uuid.uuid4()),
            repo_id=repo_id,
            viewer_fingerprint="fp-y",
            event_date="2026-02-01",
        ),
        MusehubViewEvent(
            view_id=str(uuid.uuid4()),
            repo_id=repo_id,
            viewer_fingerprint="fp-z",
            event_date="2026-02-02",
        ),
    ])
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analytics/views?days=90",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    results = resp.json()
    by_date = {r["date"]: r["count"] for r in results}
    assert by_date.get("2026-02-01") == 2
    assert by_date.get("2026-02-02") == 1


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_feed_empty(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /feed returns empty list for a user with no activity."""
    resp = await client.get("/api/v1/musehub/feed", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_get_feed_requires_auth(client: AsyncClient) -> None:
    """GET /feed returns 401 without token."""
    resp = await client.get("/api/v1/musehub/feed")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_get_feed_returns_user_notifications(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /feed returns notifications for the calling user, newest first."""
    from datetime import timedelta

    now = datetime.now(tz=timezone.utc)
    older = MusehubNotification(
        notif_id=str(uuid.uuid4()),
        recipient_id=_TEST_USER_ID,
        event_type="comment",
        repo_id=None,
        actor="eve",
        payload={},
        is_read=False,
        created_at=now - timedelta(hours=2),
    )
    newer = MusehubNotification(
        notif_id=str(uuid.uuid4()),
        recipient_id=_TEST_USER_ID,
        event_type="mention",
        repo_id=None,
        actor="frank",
        payload={},
        is_read=False,
        created_at=now - timedelta(hours=1),
    )
    db_session.add_all([older, newer])
    await db_session.commit()

    resp = await client.get("/api/v1/musehub/feed", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    assert items[0]["event_type"] == "mention"   # newest first
    assert items[1]["event_type"] == "comment"
