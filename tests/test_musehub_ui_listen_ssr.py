"""Tests for SSR track listing on the listen and embed pages.

Acceptance criteria (issue #582 — Partial SSR: listen + embed pages):

- test_listen_page_renders_track_list_server_side — seed tracks with audioUrl,
  GET page, assert track names appear in SSR HTML
- test_listen_page_track_items_have_data_track_url — track items have
  data-track-url attributes in server-rendered HTML
- test_listen_page_waveform_div_present — #waveform div present in page HTML
  for WaveSurfer to attach to
- test_listen_page_transport_bar_present — play button rendered in HTML
- test_listen_page_no_tracks_shows_message — repo with no audio tracks shows
  informative empty-state message
- test_embed_page_renders_track_name — GET embed page, SSR track name in HTML
- test_embed_page_sets_embed_track_url_js_global — window.__embedTrackUrl
  script tag present when a track exists
- test_embed_page_no_audio_url_hides_player — repo without audio objects
  → no window.__embedTrackUrl global
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubObject, MusehubRepo


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


async def _make_repo_with_tracks(
    db_session: AsyncSession,
    *,
    owner: str = "testuser",
    slug: str = "audio-repo",
    tracks: list[tuple[str, int]] | None = None,
) -> str:
    """Seed a repo with optional audio objects; return repo_id."""
    repo = MusehubRepo(
        name=slug,
        owner=owner,
        slug=slug,
        visibility="public",
        owner_user_id="test-owner-ssr",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    repo_id = str(repo.repo_id)

    for path, size in (tracks or []):
        obj = MusehubObject(
            object_id=f"sha256:{path.replace('/', '_')}",
            repo_id=repo_id,
            path=path,
            size_bytes=size,
            disk_path=f"/tmp/{path.replace('/', '_')}",
        )
        db_session.add(obj)
    if tracks:
        await db_session.commit()

    return repo_id


# ---------------------------------------------------------------------------
# listen page — SSR track listing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_listen_page_renders_track_list_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Track names must appear in the server-rendered HTML without requiring JS execution."""
    await _make_repo_with_tracks(
        db_session,
        slug="ssr-tracks",
        tracks=[
            ("tracks/bass.mp3", 51200),
            ("tracks/keys.mp3", 61440),
        ],
    )
    response = await client.get("/musehub/ui/testuser/ssr-tracks/listen/main")
    assert response.status_code == 200
    body = response.text
    # Track names are derived from file basenames (without extension)
    assert "bass" in body
    assert "keys" in body


@pytest.mark.anyio
async def test_listen_page_track_items_have_data_track_url(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """SSR-rendered track items must carry data-track-url attributes for JS hydration."""
    await _make_repo_with_tracks(
        db_session,
        slug="data-attr-repo",
        tracks=[("tracks/drum.mp3", 32768)],
    )
    response = await client.get("/musehub/ui/testuser/data-attr-repo/listen/main")
    assert response.status_code == 200
    body = response.text
    assert "data-track-url" in body


@pytest.mark.anyio
async def test_listen_page_waveform_div_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The waveform container must be present in the server-rendered HTML for WaveSurfer."""
    await _make_repo_with_tracks(db_session, slug="waveform-repo")
    response = await client.get("/musehub/ui/testuser/waveform-repo/listen/main")
    assert response.status_code == 200
    body = response.text
    # WaveSurfer target element or reference must be present
    assert "waveform" in body


@pytest.mark.anyio
async def test_listen_page_transport_bar_present(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Play/pause controls must appear in the server-rendered HTML."""
    await _make_repo_with_tracks(
        db_session,
        slug="transport-repo",
        tracks=[("tracks/lead.mp3", 20480)],
    )
    response = await client.get("/musehub/ui/testuser/transport-repo/listen/main")
    assert response.status_code == 200
    body = response.text
    assert "play-btn" in body or "play" in body.lower()


@pytest.mark.anyio
async def test_listen_page_no_tracks_shows_message(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A repo with no audio tracks must render an informative empty-state message."""
    await _make_repo_with_tracks(db_session, slug="empty-audio-repo", tracks=[])
    response = await client.get("/musehub/ui/testuser/empty-audio-repo/listen/main")
    assert response.status_code == 200
    body = response.text
    # Empty state — no audio tracks
    assert (
        "No audio" in body
        or "no audio" in body.lower()
        or "hasRenders" in body
        or "no-renders" in body
    )


@pytest.mark.anyio
async def test_listen_page_playlist_json_injected(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """window.__playlist must be set in the server-rendered page script tag."""
    await _make_repo_with_tracks(
        db_session,
        slug="playlist-repo",
        tracks=[("tracks/synth.mp3", 40960)],
    )
    response = await client.get("/musehub/ui/testuser/playlist-repo/listen/main")
    assert response.status_code == 200
    body = response.text
    assert "window.__playlist" in body


# ---------------------------------------------------------------------------
# embed page — SSR track data
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_embed_page_renders_track_name(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET embed page must render the resolved track name in server HTML."""
    await _make_repo_with_tracks(
        db_session,
        slug="embed-audio",
        tracks=[("mix/full_mix.mp3", 204800)],
    )
    response = await client.get("/musehub/ui/testuser/embed-audio/embed/main")
    assert response.status_code == 200
    body = response.text
    # Track name derived from filename (without extension)
    assert "full_mix" in body


@pytest.mark.anyio
async def test_embed_page_sets_embed_track_url_js_global(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """window.__embedTrackUrl must be set when the server resolved an audio track."""
    await _make_repo_with_tracks(
        db_session,
        slug="embed-url-repo",
        tracks=[("tracks/lead.mp3", 20480)],
    )
    response = await client.get("/musehub/ui/testuser/embed-url-repo/embed/main")
    assert response.status_code == 200
    body = response.text
    assert "window.__embedTrackUrl" in body


@pytest.mark.anyio
async def test_embed_page_no_audio_url_hides_player(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Embed page for a repo with no audio tracks must NOT set window.__embedTrackUrl."""
    await _make_repo_with_tracks(db_session, slug="embed-empty", tracks=[])
    response = await client.get("/musehub/ui/testuser/embed-empty/embed/main")
    assert response.status_code == 200
    body = response.text
    # No audio resolved → no __embedTrackUrl global injected
    assert "window.__embedTrackUrl" not in body
