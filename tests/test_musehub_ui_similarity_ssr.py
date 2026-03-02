"""SSR-specific tests for the similarity and emotion-diff pages.

Covers the migration from client-side JS chart rendering to server-side SVG
and Jinja2 templates per issue #567.

Tests:
- test_similarity_page_renders_svg_server_side — <svg> present in HTML response
- test_similarity_page_renders_dimension_labels — dimension labels in SVG text
- test_similarity_page_renders_correlation_values — score pct in breakdown table
- test_emotion_diff_page_renders_timeline_bars — delta bar divs in HTML
- test_emotion_diff_page_no_js_chart_lib — no ChartJS or D3 references in page
- test_similarity_page_no_js_chart_lib — no ChartJS or D3 references in page
- test_emotion_diff_page_renders_svg_server_side — <svg> elements in both radars
- test_emotion_diff_page_renders_dimension_labels — all 8 axis labels in SVG
- test_similarity_page_renders_overall_badge — overall pct badge in HTML
- test_emotion_diff_page_renders_delta_table — per-axis delta table present
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
        name="ssr-test-beats",
        owner="ssruser",
        slug="ssr-test-beats",
        visibility="private",
        owner_user_id="ssr-owner",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


_SIM_URL = "/musehub/ui/ssruser/ssr-test-beats/similarity/main...feature"
_EDIFF_URL = "/musehub/ui/ssruser/ssr-test-beats/emotion-diff/main...feature"


# ---------------------------------------------------------------------------
# Similarity SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_similarity_page_renders_svg_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET similarity page returns HTML containing a server-rendered <svg> element."""
    await _make_repo(db_session)
    response = await client.get(_SIM_URL)
    assert response.status_code == 200
    body = response.text
    assert "<svg" in body, "Expected server-rendered <svg> element in response body"
    assert "viewBox" in body, "Expected viewBox attribute on SVG (server-rendered)"


@pytest.mark.anyio
async def test_similarity_page_renders_dimension_labels(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Similarity page SVG contains all 10 dimension axis labels server-side."""
    await _make_repo(db_session)
    response = await client.get(_SIM_URL)
    assert response.status_code == 200
    body = response.text
    for label in ("Pitch", "Rhythm", "Tempo", "Dynamics", "Harmony",
                  "Form", "Blend", "Groove", "Contour", "Emotion"):
        assert label in body, f"Dimension label '{label}' missing from server-rendered page"


@pytest.mark.anyio
async def test_similarity_page_renders_correlation_values(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Similarity page contains numeric percentage values in the breakdown table."""
    await _make_repo(db_session)
    response = await client.get(_SIM_URL)
    assert response.status_code == 200
    body = response.text
    # The dimension breakdown table renders scores as "XX%" via server-side Jinja2
    assert "%" in body, "Expected percentage values in breakdown table"
    assert "Dimension Breakdown" in body
    assert "overall musical similarity" in body


@pytest.mark.anyio
async def test_similarity_page_renders_overall_badge(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Similarity page contains the overall percentage badge rendered server-side."""
    await _make_repo(db_session)
    response = await client.get(_SIM_URL)
    assert response.status_code == 200
    body = response.text
    assert "overall musical similarity" in body
    # Badge is rendered as a Jinja2 expression — should be a numeric %
    assert "%" in body


@pytest.mark.anyio
async def test_similarity_page_no_js_chart_lib(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Similarity page does not reference client-side chart libraries (ChartJS, D3)."""
    await _make_repo(db_session)
    response = await client.get(_SIM_URL)
    assert response.status_code == 200
    body = response.text
    assert "chart.js" not in body.lower(), "ChartJS must not be present in SSR page"
    assert "d3.js" not in body.lower(), "D3 must not be present in SSR page"
    assert "cdn.jsdelivr.net/npm/chart" not in body.lower()


# ---------------------------------------------------------------------------
# Emotion-diff SSR tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_emotion_diff_page_renders_timeline_bars(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion-diff page renders per-axis delta bar divs (CSS bars, no JS)."""
    await _make_repo(db_session)
    response = await client.get(_EDIFF_URL)
    assert response.status_code == 200
    body = response.text
    # Delta bar divs are rendered server-side with inline CSS width/left
    assert "Per-Axis Delta" in body
    assert "bar_left_pct" not in body, (
        "Jinja2 variable names must not leak into HTML — template may have a render error"
    )
    # The delta table rows contain direction labels rendered server-side
    for direction_label in ("increase", "decrease", "unchanged"):
        # At least one of these must appear (depends on computed delta)
        if direction_label in body:
            break
    else:
        pytest.fail("No direction label (increase/decrease/unchanged) found in delta table")


@pytest.mark.anyio
async def test_emotion_diff_page_no_js_chart_lib(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion-diff page does not reference ChartJS or D3 chart libraries."""
    await _make_repo(db_session)
    response = await client.get(_EDIFF_URL)
    assert response.status_code == 200
    body = response.text
    assert "chart.js" not in body.lower(), "ChartJS must not be present in SSR page"
    assert "d3.js" not in body.lower(), "D3 must not be present in SSR page"
    assert "cdn.jsdelivr.net/npm/chart" not in body.lower()


@pytest.mark.anyio
async def test_emotion_diff_page_renders_svg_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion-diff page returns HTML containing server-rendered <svg> radar elements."""
    await _make_repo(db_session)
    response = await client.get(_EDIFF_URL)
    assert response.status_code == 200
    body = response.text
    assert "<svg" in body, "Expected server-rendered <svg> elements in response body"
    # Side-by-side radars: both base (#58a6ff) and head (#f0883e) colors appear
    assert "#58a6ff" in body, "Base radar color missing — base SVG not rendered"
    assert "#f0883e" in body, "Head radar color missing — head SVG not rendered"


@pytest.mark.anyio
async def test_emotion_diff_page_renders_dimension_labels(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion-diff page SVG contains all 8 emotional axis labels server-side."""
    await _make_repo(db_session)
    response = await client.get(_EDIFF_URL)
    assert response.status_code == 200
    body = response.text
    for label in ("Valence", "Energy", "Tension", "Complexity",
                  "Warmth", "Brightness", "Darkness", "Playfulness"):
        assert label in body, f"Emotion axis label '{label}' missing from server-rendered page"


@pytest.mark.anyio
async def test_emotion_diff_page_renders_delta_table(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion-diff page contains the per-axis delta breakdown table."""
    await _make_repo(db_session)
    response = await client.get(_EDIFF_URL)
    assert response.status_code == 200
    body = response.text
    assert "Per-Axis Delta" in body
    assert "8-Dimension Emotional Signature" in body
    assert "Listen Base" in body
    assert "Listen Head" in body
