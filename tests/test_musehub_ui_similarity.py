"""Tests for the Muse Hub musical similarity page (issue #427).

Covers:
- test_similarity_page_renders              — GET /{owner}/{repo}/similarity/{base}...{head} returns 200 HTML
- test_similarity_page_no_auth_required     — accessible without JWT
- test_similarity_page_invalid_ref_404      — refs without '...' separator return 404
- test_similarity_page_unknown_owner_404    — unknown owner/slug returns 404
- test_similarity_page_includes_radar       — page contains radar chart JavaScript
- test_similarity_page_includes_dimensions  — page contains 10-dimension table JS
- test_similarity_page_includes_overall_badge — page contains overall % badge JS
- test_similarity_page_includes_diff_button — page contains "Open Full Diff" link
- test_similarity_page_includes_create_pr   — page contains "Create Pull Request" CTA
- test_similarity_json_response             — ?format=json returns RefSimilarityResponse shape
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


# ---------------------------------------------------------------------------
# Issue #427 — musical similarity page
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_similarity_page_renders(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/similarity/{base}...{head} returns 200 HTML."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/similarity/main...feature")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Muse Hub" in body
    assert "main" in body
    assert "feature" in body


@pytest.mark.anyio
async def test_similarity_page_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Similarity page is accessible without a JWT token."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/similarity/main...feature")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_similarity_page_invalid_ref_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Similarity path without '...' separator returns 404."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/similarity/mainfeature")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_similarity_page_unknown_owner_404(
    client: AsyncClient,
) -> None:
    """Unknown owner/slug combination returns 404 on similarity page."""
    response = await client.get("/musehub/ui/nobody/norepo/similarity/main...feature")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_similarity_page_includes_radar(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Similarity page HTML contains radar chart JavaScript."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/similarity/main...feature")
    assert response.status_code == 200
    body = response.text
    assert "radarSvg" in body
    assert "DIMENSIONS" in body


@pytest.mark.anyio
async def test_similarity_page_includes_dimensions(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Similarity page HTML contains 10-dimension breakdown table JavaScript."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/similarity/main...feature")
    assert response.status_code == 200
    body = response.text
    assert "dimRow" in body
    assert "Dimension Breakdown" in body


@pytest.mark.anyio
async def test_similarity_page_includes_overall_badge(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Similarity page HTML contains overall similarity badge and interpretation logic."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/similarity/main...feature")
    assert response.status_code == 200
    body = response.text
    assert "interpretBadge" in body
    assert "overall musical similarity" in body


@pytest.mark.anyio
async def test_similarity_page_includes_diff_button(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Similarity page HTML contains an 'Open Full Diff' button linking to the compare page."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/similarity/main...feature")
    assert response.status_code == 200
    body = response.text
    assert "Open Full Diff" in body
    assert "compare" in body


@pytest.mark.anyio
async def test_similarity_page_includes_create_pr(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Similarity page HTML contains a 'Create Pull Request' call-to-action."""
    await _make_repo(db_session)
    response = await client.get("/musehub/ui/testuser/test-beats/similarity/main...feature")
    assert response.status_code == 200
    body = response.text
    assert "Create Pull Request" in body


@pytest.mark.anyio
async def test_similarity_json_response(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /musehub/ui/{owner}/{slug}/similarity/{refs}?format=json returns RefSimilarityResponse."""
    await _make_repo(db_session)
    response = await client.get(
        "/musehub/ui/testuser/test-beats/similarity/main...feature?format=json"
    )
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    body = response.json()
    # RefSimilarityResponse camelCase fields
    assert "overallSimilarity" in body
    assert "dimensions" in body
    assert "interpretation" in body
    assert "baseRef" in body
    assert "compareRef" in body
    # Dimensions sub-object should have all 10 axes
    dims = body["dimensions"]
    assert "pitchDistribution" in dims
    assert "rhythmPattern" in dims
    assert "tempo" in dims
    assert "dynamics" in dims
    assert "harmonicContent" in dims
    assert "form" in dims
    assert "instrumentBlend" in dims
    assert "groove" in dims
    assert "contour" in dims
    assert "emotion" in dims
    # Scores are in [0, 1]
    assert 0.0 <= body["overallSimilarity"] <= 1.0
