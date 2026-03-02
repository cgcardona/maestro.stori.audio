"""Tests for complex analysis page SSR migration (issue #579).

Verifies that dynamics, emotion, chord-map, contour, and motifs pages
render their analysis data server-side — the HTML response must contain
the SVG/HTML chart elements without requiring a client-side fetch.

Covers:
- test_dynamics_page_renders_server_side — GET dynamics page, assert <svg or <rect in HTML
- test_dynamics_page_shows_velocity_bars — assert SVG velocity bar elements are present
- test_emotion_page_renders_svg_scatter — GET emotion page, assert <circle in SVG
- test_emotion_page_shows_summary_vector — assert energy/valence/tension/darkness bars in HTML
- test_chord_map_page_renders_progression — assert chord symbol in HTML
- test_chord_map_page_shows_chord_table — assert chord table present in HTML
- test_contour_page_renders_polyline — GET contour page, assert <polyline in HTML
- test_contour_page_shows_shape_label — assert shape label in HTML
- test_motifs_page_renders_patterns — assert interval pattern element in HTML
- test_motifs_page_shows_occurrence_grid — assert occurrences present in HTML
- test_complex_analysis_htmx_fragment_path — GET with HX-Request:true returns fragment (emotion)
- test_dynamics_htmx_fragment_path — GET dynamics with HX-Request:true returns fragment
- test_contour_htmx_fragment_path — GET contour with HX-Request:true returns fragment
- test_chord_map_htmx_fragment_path — GET chord-map with HX-Request:true returns fragment
- test_motifs_htmx_fragment_path — GET motifs with HX-Request:true returns fragment
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.db.musehub_models import MusehubRepo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ANALYSIS_REF = "deadbeef12345678"


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
# Dynamics page tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dynamics_page_renders_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET dynamics page must contain an SVG element rendered server-side."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/dynamics"
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "<svg" in body


@pytest.mark.anyio
async def test_dynamics_page_shows_velocity_bars(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Dynamics page must render SVG <rect> velocity bars server-side."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/dynamics"
    )
    assert response.status_code == 200
    body = response.text
    assert "<rect" in body
    assert "fill-opacity" in body


@pytest.mark.anyio
async def test_dynamics_page_shows_arc_badge(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Dynamics page must render arc classification badge server-side."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/dynamics"
    )
    assert response.status_code == 200
    body = response.text
    # At least one of the known arc types must appear
    arc_types = ["flat", "terraced", "crescendo", "decrescendo", "swell", "hairpin"]
    assert any(arc in body for arc in arc_types)


# ---------------------------------------------------------------------------
# Emotion page tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_emotion_page_renders_svg_scatter(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET emotion page must contain SVG <circle> elements for the scatter plot."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/emotion"
    )
    assert response.status_code == 200
    body = response.text
    assert "<circle" in body
    assert "<svg" in body


@pytest.mark.anyio
async def test_emotion_page_shows_summary_vector(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion page must render energy/valence/tension/darkness bars server-side."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/emotion"
    )
    assert response.status_code == 200
    body = response.text
    assert "Energy" in body
    assert "Valence" in body
    assert "Tension" in body
    assert "Darkness" in body


@pytest.mark.anyio
async def test_emotion_page_shows_narrative(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion page must render the narrative text server-side."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/emotion"
    )
    assert response.status_code == 200
    body = response.text
    assert "NARRATIVE" in body
    assert "TRAJECTORY" in body


# ---------------------------------------------------------------------------
# Chord map page tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_chord_map_page_renders_progression(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET chord-map page must contain chord symbols rendered server-side."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/chord-map"
    )
    assert response.status_code == 200
    body = response.text
    assert "PROGRESSION TIMELINE" in body


@pytest.mark.anyio
async def test_chord_map_page_shows_chord_table(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Chord map page must render a chord table with beat and function columns."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/chord-map"
    )
    assert response.status_code == 200
    body = response.text
    assert "CHORD TABLE" in body
    assert "Beat" in body
    assert "Function" in body
    assert "Tension" in body


# ---------------------------------------------------------------------------
# Contour page tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_contour_page_renders_polyline(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET contour page must contain SVG <polyline> rendered server-side."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/contour"
    )
    assert response.status_code == 200
    body = response.text
    assert "<polyline" in body
    assert "<svg" in body


@pytest.mark.anyio
async def test_contour_page_shows_shape_label(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Contour page must render the shape label and direction server-side."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/contour"
    )
    assert response.status_code == 200
    body = response.text
    assert "Shape" in body
    assert "Overall Direction" in body
    assert "Direction Changes" in body


# ---------------------------------------------------------------------------
# Motifs page tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_motifs_page_renders_patterns(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET motifs page must contain interval pattern elements rendered server-side."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/motifs"
    )
    assert response.status_code == 200
    body = response.text
    assert "INTERVAL PATTERN" in body


@pytest.mark.anyio
async def test_motifs_page_shows_occurrence_grid(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Motifs page must render occurrence beat markers server-side."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/motifs"
    )
    assert response.status_code == 200
    body = response.text
    assert "OCCURRENCES" in body


# ---------------------------------------------------------------------------
# HTMX fragment path tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_complex_analysis_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET emotion page with HX-Request:true returns bare fragment (no <html> wrapper)."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/emotion",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    body = response.text
    # Fragment must contain chart elements
    assert "<circle" in body or "Valence" in body
    # Fragment must NOT contain the full HTML shell
    assert "<!DOCTYPE html>" not in body
    assert "<html" not in body


@pytest.mark.anyio
async def test_dynamics_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET dynamics page with HX-Request:true returns bare fragment."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/dynamics",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    body = response.text
    assert "<rect" in body or "velocity" in body.lower()
    assert "<!DOCTYPE html>" not in body


@pytest.mark.anyio
async def test_contour_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET contour page with HX-Request:true returns bare fragment."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/contour",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    body = response.text
    assert "<polyline" in body or "Shape" in body
    assert "<!DOCTYPE html>" not in body


@pytest.mark.anyio
async def test_chord_map_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET chord-map page with HX-Request:true returns bare fragment."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/chord-map",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    body = response.text
    assert "PROGRESSION TIMELINE" in body or "Beat" in body
    assert "<!DOCTYPE html>" not in body


@pytest.mark.anyio
async def test_motifs_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET motifs page with HX-Request:true returns bare fragment."""
    await _make_repo(db_session)
    response = await client.get(
        f"/musehub/ui/testuser/test-beats/analysis/{_ANALYSIS_REF}/motifs",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    body = response.text
    assert "INTERVAL PATTERN" in body or "occurrences" in body.lower()
    assert "<!DOCTYPE html>" not in body
