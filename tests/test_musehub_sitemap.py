"""Tests for the MuseHub sitemap.xml and robots.txt endpoints.

Covers acceptance criteria from issue #439:
- test_sitemap_returns_xml                   — GET /sitemap.xml returns 200 with XML content-type
- test_sitemap_contains_static_pages        — static explore/trending/topics URLs are always present
- test_sitemap_contains_public_repo         — a seeded public repo appears in the sitemap
- test_sitemap_excludes_private_repo        — private repos do NOT appear in the sitemap
- test_sitemap_contains_user_profile        — seeded user profile URL appears in sitemap
- test_sitemap_contains_topic_urls          — repo tags generate /topics/{tag} entries
- test_sitemap_contains_release_url         — a release URL appears for repos with releases
- test_sitemap_xml_well_formed              — sitemap can be parsed as valid XML
- test_sitemap_loc_uses_request_host        — loc entries use the base URL from the request
- test_robots_txt_returns_plain_text        — GET /robots.txt returns 200 text/plain
- test_robots_txt_allows_musehub_ui         — Allow: /musehub/ui/ is present
- test_robots_txt_disallows_settings        — settings path is disallowed
- test_robots_txt_disallows_notifications   — notifications path is disallowed
- test_robots_txt_disallows_api             — /api/ directory is disallowed
- test_robots_txt_contains_sitemap_url      — Sitemap: directive points to /sitemap.xml
- test_robots_txt_names_known_agents        — known AI bots appear with explicit Allow
- test_robots_txt_no_auth_required          — endpoint is accessible without JWT
- test_sitemap_no_auth_required             — sitemap is accessible without JWT
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from xml.etree import ElementTree as ET

from maestro.db.musehub_models import (
    MusehubProfile,
    MusehubRelease,
    MusehubRepo,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_public_repo(
    db_session: AsyncSession,
    *,
    owner: str = "sitemap-user",
    slug: str = "sitemap-repo",
    tags: list[str] | None = None,
    visibility: str = "public",
) -> MusehubRepo:
    """Seed a repo and return the ORM object."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility=visibility,
        owner_user_id="sitemap-user-id",
        description="test repo for sitemap",
        tags=tags or [],
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return repo


async def _make_profile(
    db_session: AsyncSession,
    *,
    username: str = "sitemap-user",
    user_id: str = "sitemap-user-id",
) -> MusehubProfile:
    """Seed a user profile and return the ORM object."""
    profile = MusehubProfile(
        user_id=user_id,
        username=username,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


async def _make_release(
    db_session: AsyncSession,
    repo_id: str,
    *,
    tag: str = "v1.0",
) -> MusehubRelease:
    """Seed a release and return the ORM object."""
    release = MusehubRelease(
        repo_id=repo_id,
        tag=tag,
        title=f"Release {tag}",
        body="",
        author="sitemap-user",
    )
    db_session.add(release)
    await db_session.commit()
    await db_session.refresh(release)
    return release


# ---------------------------------------------------------------------------
# Sitemap tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sitemap_returns_xml(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /sitemap.xml returns 200 with an XML content-type."""
    response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "xml" in response.headers["content-type"]


@pytest.mark.anyio
async def test_sitemap_contains_static_pages(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Static explore, trending, and topics pages are always included in the sitemap."""
    response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    body = response.text
    assert "/musehub/ui/explore" in body
    assert "/musehub/ui/trending" in body
    assert "/musehub/ui/topics" in body


@pytest.mark.anyio
async def test_sitemap_contains_public_repo(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A seeded public repo's UI URL appears in the sitemap."""
    await _make_public_repo(db_session, owner="artist", slug="cool-track")
    response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    body = response.text
    assert "/musehub/ui/artist/cool-track" in body


@pytest.mark.anyio
async def test_sitemap_excludes_private_repo(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Private repos must not appear anywhere in the sitemap."""
    await _make_public_repo(db_session, owner="secretuser", slug="hidden-project", visibility="private")
    response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    body = response.text
    assert "hidden-project" not in body
    assert "secretuser" not in body


@pytest.mark.anyio
async def test_sitemap_contains_user_profile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A seeded user profile generates a /musehub/ui/users/{username} entry."""
    await _make_profile(db_session, username="jazzmaster", user_id="jazzmaster-uid")
    response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "/musehub/ui/users/jazzmaster" in response.text


@pytest.mark.anyio
async def test_sitemap_contains_topic_urls(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Tags on public repos generate /musehub/ui/topics/{tag} entries."""
    await _make_public_repo(db_session, owner="producer", slug="beats", tags=["lo-fi", "jazz"])
    response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    body = response.text
    assert "/musehub/ui/topics/lo-fi" in body
    assert "/musehub/ui/topics/jazz" in body


@pytest.mark.anyio
async def test_sitemap_contains_release_url(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A release on a public repo generates a /releases/{tag} sitemap entry."""
    repo = await _make_public_repo(db_session, owner="bandname", slug="debut-album")
    await _make_release(db_session, repo.repo_id, tag="v1.0")
    response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "/musehub/ui/bandname/debut-album/releases/v1.0" in response.text


@pytest.mark.anyio
async def test_sitemap_xml_well_formed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The sitemap response must be parseable as valid XML."""
    response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    # This raises if the document is not well-formed XML.
    root = ET.fromstring(response.content)
    assert root.tag.endswith("urlset")


@pytest.mark.anyio
async def test_sitemap_loc_uses_request_host(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """loc entries in the sitemap use the base URL from the incoming request."""
    await _make_public_repo(db_session, owner="testowner", slug="testrepo")
    response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    # The test client uses base_url="http://test" — every loc must start with http://test.
    body = response.text
    assert "<loc>http://test" in body


@pytest.mark.anyio
async def test_sitemap_no_auth_required(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Sitemap endpoint must be accessible without a JWT (crawlers don't authenticate)."""
    response = await client.get("/sitemap.xml")
    assert response.status_code != 401
    assert response.status_code == 200


@pytest.mark.anyio
async def test_sitemap_repo_commits_page_included(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Each public repo's /commits page also appears in the sitemap."""
    await _make_public_repo(db_session, owner="composer", slug="symphony-no1")
    response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "/musehub/ui/composer/symphony-no1/commits" in response.text


@pytest.mark.anyio
async def test_sitemap_repo_issues_page_included(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Each public repo's /issues page also appears in the sitemap."""
    await _make_public_repo(db_session, owner="composer", slug="symphony-no2")
    response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "/musehub/ui/composer/symphony-no2/issues" in response.text


# ---------------------------------------------------------------------------
# Robots.txt tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_robots_txt_returns_plain_text(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /robots.txt returns 200 with text/plain content-type."""
    response = await client.get("/robots.txt")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


@pytest.mark.anyio
async def test_robots_txt_allows_musehub_ui(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Allow: /musehub/ui/ is present for all crawlers."""
    response = await client.get("/robots.txt")
    assert response.status_code == 200
    assert "Allow: /musehub/ui/" in response.text


@pytest.mark.anyio
async def test_robots_txt_disallows_settings(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Settings paths are disallowed to prevent indexing of private user config pages."""
    response = await client.get("/robots.txt")
    assert response.status_code == 200
    assert "Disallow: /musehub/ui/*/settings" in response.text


@pytest.mark.anyio
async def test_robots_txt_disallows_notifications(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Notification pages are disallowed (user-private inbox content)."""
    response = await client.get("/robots.txt")
    assert response.status_code == 200
    assert "Disallow: /musehub/ui/notifications" in response.text


@pytest.mark.anyio
async def test_robots_txt_disallows_api(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """API paths are disallowed — crawlers should use the sitemap, not the REST API."""
    response = await client.get("/robots.txt")
    assert response.status_code == 200
    assert "Disallow: /api/" in response.text


@pytest.mark.anyio
async def test_robots_txt_contains_sitemap_url(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Sitemap: directive is present and points to /sitemap.xml."""
    response = await client.get("/robots.txt")
    assert response.status_code == 200
    assert "Sitemap:" in response.text
    assert "sitemap.xml" in response.text


@pytest.mark.anyio
async def test_robots_txt_names_known_agents(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Known AI discovery bots (GPTBot, ClaudeBot, etc.) appear with explicit Allow."""
    response = await client.get("/robots.txt")
    assert response.status_code == 200
    body = response.text
    for bot in ("GPTBot", "ClaudeBot", "Googlebot", "CursorBot"):
        assert bot in body


@pytest.mark.anyio
async def test_robots_txt_no_auth_required(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """robots.txt must be accessible without authentication."""
    response = await client.get("/robots.txt")
    assert response.status_code != 401
    assert response.status_code == 200
