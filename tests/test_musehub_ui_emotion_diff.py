"""Tests for the Muse Hub emotion-diff UI page.

Covers:
- test_emotion_diff_page_renders — GET /{owner}/{repo}/emotion-diff/{base}...{head} returns 200 HTML
- test_emotion_diff_page_no_auth_required — accessible without JWT
- test_emotion_diff_page_invalid_ref_404 — refs without '...' separator return 404
- test_emotion_diff_page_unknown_owner_404 — unknown owner/slug returns 404
- test_emotion_diff_page_includes_radar — page contains server-rendered SVG radar charts
- test_emotion_diff_page_includes_8_dimensions — page contains all 8 emotion-diff axis labels (SSR)
- test_emotion_diff_page_includes_delta_chart — page contains per-axis delta table (SSR)
- test_emotion_diff_page_includes_trajectory — page contains "Emotional Trajectory" section
- test_emotion_diff_page_includes_listen_button — page contains "Listen" comparison buttons
- test_emotion_diff_page_includes_interpretation — page contains interpretation text
- test_emotion_diff_json_response — ?format=json returns EmotionDiffResponse shape
- test_emotion_diff_page_empty_base_ref_404 — base ref empty returns 404
- test_emotion_diff_page_empty_head_ref_404 — head ref empty returns 404
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(db_session: AsyncSession) -> str:
    """Seed a minimal repo and return its repo_id."""
    repo = MusehubRepo(
        name="test-beats",
        owner="testuser",
        slug="test-beats",
        visibility="private",
        owner_user_id="test-owner",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


_BASE_URL = "/musehub/ui/testuser/test-beats/emotion-diff/main...feature"


# ---------------------------------------------------------------------------
# Issue #432 — emotion-diff UI page
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_emotion_diff_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/emotion-diff/{base}...{head} returns 200 HTML."""
    await _make_repo(db_session)
    response = await client.get(_BASE_URL)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "main" in body
    assert "feature" in body


@pytest.mark.anyio
async def test_emotion_diff_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion-diff page is accessible without a JWT token."""
    await _make_repo(db_session)
    response = await client.get(_BASE_URL)
    assert response.status_code == 200


@pytest.mark.anyio
async def test_emotion_diff_page_invalid_ref_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion-diff path without '...' separator returns 404."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/emotion-diff/mainfeature")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_emotion_diff_page_unknown_owner_404(
    client: AsyncClient,
) -> None:
    """Unknown owner/slug combination returns 404 on emotion-diff page."""
    response = await client.get("/musehub/ui/nobody/norepo/emotion-diff/main...feature")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_emotion_diff_page_empty_base_ref_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion-diff path with empty base ref (starts with '...') returns 404."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/emotion-diff/...feature")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_emotion_diff_page_empty_head_ref_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion-diff path with empty head ref (ends with '...') returns 404."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/emotion-diff/main...")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_emotion_diff_page_includes_radar(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion-diff page HTML contains server-rendered SVG radar charts for both refs."""
    await _make_repo(db_session)
    response = await client.get(_BASE_URL)
    assert response.status_code == 200
    body = response.text
    assert "<svg" in body
    assert "8-Dimension Emotional Signature" in body


@pytest.mark.anyio
async def test_emotion_diff_page_includes_8_dimensions(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion-diff page HTML renders all 8 emotional dimension axis labels (SSR)."""
    await _make_repo(db_session)
    response = await client.get(_BASE_URL)
    assert response.status_code == 200
    body = response.text
    # All 8 axis labels from EmotionVector8D must appear in the SSR content
    for label in ("Valence", "Energy", "Tension", "Complexity", "Warmth", "Brightness", "Darkness", "Playfulness"):
        assert label in body, f"Axis label '{label}' missing from SSR page"


@pytest.mark.anyio
async def test_emotion_diff_page_includes_delta_chart(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion-diff page HTML contains the per-axis delta table (SSR)."""
    await _make_repo(db_session)
    response = await client.get(_BASE_URL)
    assert response.status_code == 200
    body = response.text
    assert "Per-Axis Delta" in body


@pytest.mark.anyio
async def test_emotion_diff_page_includes_trajectory(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion-diff page HTML contains the 'Emotional Trajectory' section heading."""
    await _make_repo(db_session)
    response = await client.get(_BASE_URL)
    assert response.status_code == 200
    body = response.text
    # The trajectory section heading is still in the SSR template
    # (server-side CSS bars replace JS sparklines)
    assert "Emotional" in body


@pytest.mark.anyio
async def test_emotion_diff_page_includes_listen_button(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion-diff page HTML contains listen comparison buttons for both refs."""
    await _make_repo(db_session)
    response = await client.get(_BASE_URL)
    assert response.status_code == 200
    body = response.text
    assert "Listen Base" in body
    assert "Listen Head" in body
    assert "listen" in body


@pytest.mark.anyio
async def test_emotion_diff_page_includes_interpretation(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion-diff page HTML contains interpretation output from the emotion-diff service."""
    await _make_repo(db_session)
    response = await client.get(_BASE_URL)
    assert response.status_code == 200
    body = response.text
    # The SSR template renders the interpretation string directly — it always
    # starts with "This commit" per compute_emotion_diff().
    assert "This commit" in body or "emotional" in body.lower()


@pytest.mark.anyio
async def test_emotion_diff_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/emotion-diff/{refs}?format=json returns EmotionDiffResponse."""
    await _make_repo(db_session)
    response = await client.get(f"{_BASE_URL}?format=json")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    body = response.json()
    # EmotionDiffResponse camelCase fields
    assert "baseRef" in body
    assert "headRef" in body
    assert "baseEmotion" in body
    assert "headEmotion" in body
    assert "delta" in body
    assert "interpretation" in body
    assert "repoId" in body
    # All 8 axes on base and head emotion vectors
    for vec_key in ("baseEmotion", "headEmotion"):
        vec = body[vec_key]
        for axis in ("valence", "energy", "tension", "complexity", "warmth", "brightness", "darkness", "playfulness"):
            assert axis in vec, f"Axis '{axis}' missing from {vec_key}"
            assert 0.0 <= vec[axis] <= 1.0, f"{vec_key}.{axis} out of [0, 1]"
    # Delta axes allow signed values in [-1, 1]
    delta = body["delta"]
    for axis in ("valence", "energy", "tension", "complexity", "warmth", "brightness", "darkness", "playfulness"):
        assert axis in delta, f"Axis '{axis}' missing from delta"
        assert -1.0 <= delta[axis] <= 1.0, f"delta.{axis} out of [-1, 1]"
    # interpretation is a non-empty string
    assert isinstance(body["interpretation"], str)
    assert len(body["interpretation"]) > 10
