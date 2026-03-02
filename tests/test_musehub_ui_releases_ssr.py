"""SSR tests for Muse Hub releases UI pages — issue #572.

Validates that release data is rendered server-side into HTML (not deferred
to client JS) and that HTMX fragment requests return bare HTML without the
full page shell.

Covers GET /musehub/ui/{owner}/{repo_slug}/releases:
- test_releases_list_renders_tag_server_side           — release tag in HTML
- test_releases_list_shows_prerelease_badge            — pre-release badge in HTML
- test_releases_list_htmx_fragment_path                — HX-Request: true → bare fragment
- test_releases_list_empty_state_when_no_releases      — no releases → empty state

Covers GET /musehub/ui/{owner}/{repo_slug}/releases/{tag}:
- test_release_detail_renders_tag_server_side          — tag in HTML
- test_release_detail_shows_audio_player_container     — audioUrl → #release-audio-player div
- test_release_detail_unknown_tag_404                  — unknown tag → 404
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import (
    MusehubRelease,
    MusehubRepo,
)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _make_repo(db: AsyncSession, owner: str = "musician", slug: str = "ssr-album") -> str:
    """Seed a public repo and return its repo_id string."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="uid-ssr-musician",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return str(repo.repo_id)


async def _make_release(
    db: AsyncSession,
    repo_id: str,
    *,
    tag: str = "v1.0",
    title: str = "Version 1.0",
    body: str = "Release notes here.",
    author: str = "musician",
    is_prerelease: bool = False,
    is_draft: bool = False,
    mp3_url: str | None = None,
) -> MusehubRelease:
    """Seed a release and return the ORM object."""
    download_urls: dict[str, str] = {}
    if mp3_url:
        download_urls["mp3"] = mp3_url
    release = MusehubRelease(
        repo_id=repo_id,
        tag=tag,
        title=title,
        body=body,
        author=author,
        is_prerelease=is_prerelease,
        is_draft=is_draft,
        download_urls=download_urls,
    )
    db.add(release)
    await db.commit()
    await db.refresh(release)
    return release


# ---------------------------------------------------------------------------
# Releases list SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_releases_list_renders_tag_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Release tag is in the HTML response server-side (no JS required)."""
    repo_id = await _make_repo(db_session)
    await _make_release(db_session, repo_id, tag="v2.5.0", title="Groove update 2.5")
    response = await client.get("/musehub/ui/musician/ssr-album/releases")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "v2.5.0" in response.text


@pytest.mark.anyio
async def test_releases_list_shows_prerelease_badge(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Pre-release flag renders the 'tag-prerelease' CSS class server-side."""
    repo_id = await _make_repo(db_session)
    await _make_release(db_session, repo_id, tag="v1.0-beta", is_prerelease=True)
    response = await client.get("/musehub/ui/musician/ssr-album/releases")
    assert response.status_code == 200
    # The fragment uses the tag-prerelease CSS class for pre-release badges.
    assert "tag-prerelease" in response.text
    assert "v1.0-beta" in response.text


@pytest.mark.anyio
async def test_releases_list_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """HX-Request: true returns a bare HTML fragment without the full page shell."""
    repo_id = await _make_repo(db_session)
    await _make_release(db_session, repo_id, tag="v3.0", title="Major release")
    response = await client.get(
        "/musehub/ui/musician/ssr-album/releases",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    # Fragment must NOT include the full HTML shell.
    assert "<html" not in response.text
    assert "<head" not in response.text
    # But must include release content.
    assert "v3.0" in response.text


@pytest.mark.anyio
async def test_releases_list_empty_state_when_no_releases(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Empty release list renders the empty state message server-side."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/musician/ssr-album/releases")
    assert response.status_code == 200
    # empty_state macro renders "No releases yet" when the list is empty.
    assert "No releases yet" in response.text


# ---------------------------------------------------------------------------
# Release detail SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_release_detail_renders_tag_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Release tag appears in the detail page HTML server-side."""
    repo_id = await _make_repo(db_session)
    await _make_release(db_session, repo_id, tag="v1.2.3", title="Polished mix")
    response = await client.get("/musehub/ui/musician/ssr-album/releases/v1.2.3")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "v1.2.3" in response.text
    # Title should appear too.
    assert "Polished mix" in response.text


@pytest.mark.anyio
async def test_release_detail_shows_audio_player_container(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """When MP3 URL is set, the audio player container div is rendered server-side."""
    repo_id = await _make_repo(db_session)
    await _make_release(
        db_session,
        repo_id,
        tag="v1.0-audio",
        mp3_url="https://cdn.example.com/album-v1.0.mp3",
    )
    response = await client.get("/musehub/ui/musician/ssr-album/releases/v1.0-audio")
    assert response.status_code == 200
    # The audio player container div must be present in the HTML.
    assert 'id="release-audio-player"' in response.text
    assert "cdn.example.com/album-v1.0.mp3" in response.text


@pytest.mark.anyio
async def test_release_detail_unknown_tag_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Non-existent release tag returns 404."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/musician/ssr-album/releases/vNOPE")
    assert response.status_code == 404
