"""Tests for the enhanced Muse Hub user profile page (issue #459).

Covers:
- test_profile_page_html_returns_200              — GET /musehub/ui/users/{username} returns 200 HTML
- test_profile_page_no_auth_required              — accessible without JWT
- test_profile_page_unknown_user_still_renders    — unknown username still returns 200 HTML shell
- test_profile_page_html_contains_heatmap_js      — page includes heatmap rendering JavaScript
- test_profile_page_html_contains_badge_js        — page includes badge rendering JavaScript
- test_profile_page_html_contains_pinned_js       — page includes pinned repos JavaScript
- test_profile_page_html_contains_activity_tab    — page includes Activity tab
- test_profile_page_json_returns_200              — ?format=json returns 200 JSON
- test_profile_page_json_unknown_user_404         — ?format=json returns 404 for unknown user
- test_profile_page_json_heatmap_structure        — JSON response has heatmap with days/stats
- test_profile_page_json_badges_structure         — JSON response has 8 badges with expected fields
- test_profile_page_json_pinned_repos             — JSON response includes pinned repo cards
- test_profile_page_json_activity_empty           — JSON response returns empty activity for new user
- test_profile_page_json_activity_filter          — ?tab=commits filters activity to commits only
- test_profile_page_json_badge_first_commit_earned — first_commit badge earned after seeding a commit
- test_profile_page_json_camel_case_keys          — JSON keys are camelCase
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubCommit, MusehubProfile, MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_profile(
    db: AsyncSession,
    *,
    username: str = "testuser",
    user_id: str = "user-profile-test-001",
    bio: str | None = "Test bio",
) -> MusehubProfile:
    """Seed a minimal MusehubProfile."""
    profile = MusehubProfile(
        user_id=user_id,
        username=username,
        bio=bio,
        avatar_url=None,
        pinned_repo_ids=[],
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


async def _make_repo(
    db: AsyncSession,
    *,
    owner_user_id: str = "user-profile-test-001",
    owner: str = "testuser",
    name: str = "test-beats",
    slug: str = "test-beats",
    visibility: str = "public",
) -> MusehubRepo:
    """Seed a minimal MusehubRepo."""
    repo = MusehubRepo(
        name=name,
        owner=owner,
        slug=slug,
        visibility=visibility,
        owner_user_id=owner_user_id,
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return repo


# ---------------------------------------------------------------------------
# HTML path tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_profile_page_html_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/users/{username} returns 200 HTML for any username."""
    await _make_profile(db_session)
    response = await client.get("/musehub/ui/users/testuser")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_profile_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Profile page is publicly accessible without a JWT token."""
    await _make_profile(db_session)
    response = await client.get("/musehub/ui/users/testuser")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_profile_page_unknown_user_still_renders(
    client: AsyncClient,
) -> None:
    """HTML shell renders even for unknown users — data fetched client-side."""
    response = await client.get("/musehub/ui/users/nobody-exists-xyzzy")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.anyio
async def test_profile_page_html_contains_heatmap_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HTML includes heatmap rendering JavaScript."""
    await _make_profile(db_session)
    response = await client.get("/musehub/ui/users/testuser")
    assert response.status_code == 200
    body = response.text
    assert "renderHeatmap" in body
    assert "heatmap-cell" in body


@pytest.mark.anyio
async def test_profile_page_html_contains_badge_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HTML includes badge rendering JavaScript."""
    await _make_profile(db_session)
    response = await client.get("/musehub/ui/users/testuser")
    assert response.status_code == 200
    body = response.text
    assert "renderBadges" in body
    assert "badge-card" in body


@pytest.mark.anyio
async def test_profile_page_html_contains_pinned_js(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HTML includes pinned repos rendering JavaScript."""
    await _make_profile(db_session)
    response = await client.get("/musehub/ui/users/testuser")
    assert response.status_code == 200
    body = response.text
    assert "renderPinned" in body
    assert "pinned-grid" in body


@pytest.mark.anyio
async def test_profile_page_html_contains_activity_tab(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HTML includes a fourth Activity tab in the tab navigation."""
    await _make_profile(db_session)
    response = await client.get("/musehub/ui/users/testuser")
    assert response.status_code == 200
    body = response.text
    assert "Activity" in body
    assert "loadActivityTab" in body


# ---------------------------------------------------------------------------
# JSON path tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_profile_page_json_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/users/{username}?format=json returns 200 JSON."""
    await _make_profile(db_session)
    response = await client.get("/musehub/ui/users/testuser?format=json")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]


@pytest.mark.anyio
async def test_profile_page_json_unknown_user_404(
    client: AsyncClient,
) -> None:
    """?format=json returns 404 for an unknown username."""
    response = await client.get("/musehub/ui/users/nobody-exists-xyzzy?format=json")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_profile_page_json_heatmap_structure(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response contains heatmap with days list and aggregate stats."""
    await _make_profile(db_session)
    response = await client.get("/musehub/ui/users/testuser?format=json")
    assert response.status_code == 200
    body = response.json()

    assert "heatmap" in body
    heatmap = body["heatmap"]
    assert "days" in heatmap
    assert "totalContributions" in heatmap
    assert "longestStreak" in heatmap
    assert "currentStreak" in heatmap

    # Should have ~364 days (52 weeks × 7 days)
    assert len(heatmap["days"]) >= 360

    # Each day has date, count, intensity
    first_day = heatmap["days"][0]
    assert "date" in first_day
    assert "count" in first_day
    assert "intensity" in first_day
    assert first_day["intensity"] in (0, 1, 2, 3)


@pytest.mark.anyio
async def test_profile_page_json_badges_structure(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response contains exactly 8 badges with required fields."""
    await _make_profile(db_session)
    response = await client.get("/musehub/ui/users/testuser?format=json")
    assert response.status_code == 200
    body = response.json()

    assert "badges" in body
    badges = body["badges"]
    assert len(badges) == 8

    for badge in badges:
        assert "id" in badge
        assert "name" in badge
        assert "description" in badge
        assert "icon" in badge
        assert "earned" in badge
        assert isinstance(badge["earned"], bool)


@pytest.mark.anyio
async def test_profile_page_json_pinned_repos(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response includes pinned repo cards when pinned_repo_ids are set."""
    profile = await _make_profile(db_session)
    repo = await _make_repo(db_session)

    # Pin the repo
    profile.pinned_repo_ids = [repo.repo_id]
    db_session.add(profile)
    await db_session.commit()

    response = await client.get("/musehub/ui/users/testuser?format=json")
    assert response.status_code == 200
    body = response.json()

    assert "pinnedRepos" in body
    pinned = body["pinnedRepos"]
    assert len(pinned) == 1
    card = pinned[0]
    assert card["name"] == "test-beats"
    assert card["slug"] == "test-beats"
    assert "starCount" in card
    assert "forkCount" in card


@pytest.mark.anyio
async def test_profile_page_json_activity_empty(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response returns empty activity list for a new user with no events."""
    await _make_profile(db_session)
    response = await client.get("/musehub/ui/users/testuser?format=json")
    assert response.status_code == 200
    body = response.json()

    assert "activity" in body
    assert isinstance(body["activity"], list)
    assert body["totalEvents"] == 0
    assert body["page"] == 1
    assert body["perPage"] == 20


@pytest.mark.anyio
async def test_profile_page_json_activity_filter(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """?tab=commits filters activity response to commits-only event types."""
    await _make_profile(db_session)
    response = await client.get("/musehub/ui/users/testuser?format=json&tab=commits")
    assert response.status_code == 200
    body = response.json()
    assert body["activityFilter"] == "commits"


@pytest.mark.anyio
async def test_profile_page_json_badge_first_commit_earned(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """first_commit badge is earned after the user has at least one commit."""
    from datetime import datetime, timezone

    profile = await _make_profile(db_session)
    repo = await _make_repo(db_session)

    # Seed one commit owned by this user's repo
    commit = MusehubCommit(
        commit_id="abc123def456abc123def456abc123def456abc1",
        repo_id=repo.repo_id,
        branch="main",
        parent_ids=[],
        message="initial commit",
        author="testuser",
        timestamp=datetime.now(tz=timezone.utc),
    )
    db_session.add(commit)
    await db_session.commit()

    response = await client.get("/musehub/ui/users/testuser?format=json")
    assert response.status_code == 200
    body = response.json()

    badges = {b["id"]: b for b in body["badges"]}
    assert "first_commit" in badges
    assert badges["first_commit"]["earned"] is True


@pytest.mark.anyio
async def test_profile_page_json_camel_case_keys(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """JSON response uses camelCase keys throughout (no snake_case at top level)."""
    await _make_profile(db_session)
    response = await client.get("/musehub/ui/users/testuser?format=json")
    assert response.status_code == 200
    body = response.json()

    # Top-level camelCase keys
    assert "avatarUrl" in body
    assert "totalEvents" in body
    assert "activityFilter" in body
    assert "pinnedRepos" in body

    # No snake_case variants
    assert "avatar_url" not in body
    assert "total_events" not in body
    assert "pinned_repos" not in body
