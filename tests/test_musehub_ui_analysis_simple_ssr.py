"""SSR tests for the analysis dashboard and five simple dimension pages.

Covers the migration from client-side JS data fetching to server-side Jinja2
rendering per issue #578 — analysis dashboard + key, tempo, meter, groove, form.

Tests:
- test_analysis_dashboard_renders_dimension_links — GET dashboard, dimension links in HTML
- test_analysis_dashboard_no_auth_required — accessible without JWT
- test_analysis_dashboard_renders_key_data_server_side — key tonic in HTML without JS fetch
- test_key_analysis_renders_tonic_server_side — tonic rendered in HTML by Jinja2
- test_key_analysis_renders_distribution_bars — confidence bar present as CSS
- test_key_analysis_htmx_fragment_path — HX-Request:true returns fragment (no extends)
- test_tempo_analysis_renders_bpm_server_side — BPM value in HTML
- test_tempo_analysis_renders_stability_bar — stability bar present as CSS
- test_meter_analysis_renders_time_signature — time signature in HTML
- test_meter_analysis_renders_beat_strength_bars — beat strength bars in HTML
- test_groove_analysis_renders_pattern — groove style in HTML
- test_groove_analysis_renders_score_gauge — groove score gauge present
- test_form_analysis_renders_sections — section names in HTML
- test_form_analysis_renders_timeline — form timeline present
- test_analysis_pages_no_js_chart_lib — no ChartJS or D3 references
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
        name="analysis-ssr-beats",
        owner="analysisuser",
        slug="analysis-ssr-beats",
        visibility="private",
        owner_user_id="analysis-owner",
    )
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return str(repo.repo_id)


_REF = "abc1234def5678"
_BASE = "/musehub/ui/analysisuser/analysis-ssr-beats"
_DASHBOARD_URL = f"{_BASE}/analysis/{_REF}"
_KEY_URL = f"{_BASE}/analysis/{_REF}/key"
_TEMPO_URL = f"{_BASE}/analysis/{_REF}/tempo"
_METER_URL = f"{_BASE}/analysis/{_REF}/meter"
_GROOVE_URL = f"{_BASE}/analysis/{_REF}/groove"
_FORM_URL = f"{_BASE}/analysis/{_REF}/form"


# ---------------------------------------------------------------------------
# Dashboard tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_analysis_dashboard_renders_dimension_links(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET analysis dashboard returns HTML with links to all dimension pages."""
    await _make_repo(db_session)
    response = await client.get(_DASHBOARD_URL)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Key" in body
    assert "Tempo" in body
    assert "Meter" in body
    assert "Groove" in body
    assert "Form" in body
    assert f"/analysis/{_REF}/key" in body


@pytest.mark.anyio
async def test_analysis_dashboard_no_auth_required(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Analysis dashboard is accessible without a JWT."""
    await _make_repo(db_session)
    response = await client.get(_DASHBOARD_URL)
    assert response.status_code == 200


@pytest.mark.anyio
async def test_analysis_dashboard_renders_key_data_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Dashboard renders key tonic data server-side — no client-side API fetch needed."""
    await _make_repo(db_session)
    response = await client.get(_DASHBOARD_URL)
    assert response.status_code == 200
    body = response.text
    # Key card shows tonic + mode directly in HTML (server-rendered, not 'loading...')
    # The stub always returns a tonic from the fixed list; just verify some key data is there
    assert "major" in body.lower() or "minor" in body.lower() or "BPM" in body


@pytest.mark.anyio
async def test_analysis_dashboard_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET dashboard with HX-Request: true returns fragment (no <html> wrapper)."""
    await _make_repo(db_session)
    response = await client.get(_DASHBOARD_URL, headers={"HX-Request": "true"})
    assert response.status_code == 200
    body = response.text
    assert "<html" not in body
    assert "Back to repo" in body or "Analysis" in body


# ---------------------------------------------------------------------------
# Key analysis tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_key_analysis_renders_tonic_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET key analysis page renders tonic in HTML via Jinja2 (not via JS fetch)."""
    await _make_repo(db_session)
    response = await client.get(_KEY_URL)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Key Detection" in body
    # Tonic is one of the standard pitch classes — verify some key content is there
    for note in ("C", "D", "E", "F", "G", "A", "B"):
        if note in body:
            break
    else:
        pytest.fail("No pitch class (key tonic) found in server-rendered key page")


@pytest.mark.anyio
async def test_key_analysis_renders_distribution_bars(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Key page renders confidence bar as inline CSS — no JS chart library required."""
    await _make_repo(db_session)
    response = await client.get(_KEY_URL)
    assert response.status_code == 200
    body = response.text
    # Confidence bar uses inline CSS width% — rendered server-side
    assert "Detection Confidence" in body
    assert "%" in body


@pytest.mark.anyio
async def test_key_analysis_renders_alternate_keys(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Key page renders alternate key candidates server-side."""
    await _make_repo(db_session)
    response = await client.get(_KEY_URL)
    assert response.status_code == 200
    body = response.text
    # Relative key is always rendered
    assert "Relative Key" in body


@pytest.mark.anyio
async def test_key_analysis_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET key page with HX-Request: true returns fragment (no <html> wrapper)."""
    await _make_repo(db_session)
    response = await client.get(_KEY_URL, headers={"HX-Request": "true"})
    assert response.status_code == 200
    body = response.text
    assert "<html" not in body
    assert "Key Detection" in body


# ---------------------------------------------------------------------------
# Tempo analysis tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tempo_analysis_renders_bpm_server_side(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET tempo analysis page renders BPM value in HTML (server-side, not JS)."""
    await _make_repo(db_session)
    response = await client.get(_TEMPO_URL)
    assert response.status_code == 200
    body = response.text
    assert "Tempo Analysis" in body
    assert "BPM" in body
    # BPM is a numeric value — at least one digit must appear
    assert any(c.isdigit() for c in body)


@pytest.mark.anyio
async def test_tempo_analysis_renders_stability_bar(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Tempo page renders stability bar as inline CSS — server-side only."""
    await _make_repo(db_session)
    response = await client.get(_TEMPO_URL)
    assert response.status_code == 200
    body = response.text
    assert "Stability" in body
    assert "Time Feel" in body
    assert "Tempo Changes" in body


@pytest.mark.anyio
async def test_tempo_analysis_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET tempo page with HX-Request: true returns fragment only."""
    await _make_repo(db_session)
    response = await client.get(_TEMPO_URL, headers={"HX-Request": "true"})
    assert response.status_code == 200
    body = response.text
    assert "<html" not in body
    assert "Tempo Analysis" in body


# ---------------------------------------------------------------------------
# Meter analysis tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_meter_analysis_renders_time_signature(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET meter analysis page renders time signature in HTML (server-side)."""
    await _make_repo(db_session)
    response = await client.get(_METER_URL)
    assert response.status_code == 200
    body = response.text
    assert "Meter Analysis" in body
    assert "Time Signature" in body
    # Time signature contains a slash like 4/4 or 3/4
    assert "/" in body


@pytest.mark.anyio
async def test_meter_analysis_renders_beat_strength_bars(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Meter page renders beat strength profile as CSS bars — server-side only."""
    await _make_repo(db_session)
    response = await client.get(_METER_URL)
    assert response.status_code == 200
    body = response.text
    assert "Beat Strength Profile" in body
    # The meter type badge (compound/simple) is rendered server-side
    assert "simple" in body or "compound" in body


@pytest.mark.anyio
async def test_meter_analysis_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET meter page with HX-Request: true returns fragment only."""
    await _make_repo(db_session)
    response = await client.get(_METER_URL, headers={"HX-Request": "true"})
    assert response.status_code == 200
    body = response.text
    assert "<html" not in body
    assert "Meter Analysis" in body


# ---------------------------------------------------------------------------
# Groove analysis tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_groove_analysis_renders_pattern(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET groove analysis page renders groove style in HTML (server-side)."""
    await _make_repo(db_session)
    response = await client.get(_GROOVE_URL)
    assert response.status_code == 200
    body = response.text
    assert "Groove Analysis" in body
    assert "Style" in body
    # One of the known groove styles should appear
    groove_styles = {"straight", "swing", "shuffled", "latin", "funk"}
    assert any(style in body.lower() for style in groove_styles), (
        "Expected a groove style name (straight/swing/shuffled/latin/funk) in response"
    )


@pytest.mark.anyio
async def test_groove_analysis_renders_score_gauge(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Groove page renders score gauge and swing factor as CSS bars — server-side."""
    await _make_repo(db_session)
    response = await client.get(_GROOVE_URL)
    assert response.status_code == 200
    body = response.text
    assert "Groove Score" in body
    assert "Swing Factor" in body
    assert "BPM" in body
    assert "Onset Deviation" in body


@pytest.mark.anyio
async def test_groove_analysis_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET groove page with HX-Request: true returns fragment only."""
    await _make_repo(db_session)
    response = await client.get(_GROOVE_URL, headers={"HX-Request": "true"})
    assert response.status_code == 200
    body = response.text
    assert "<html" not in body
    assert "Groove Analysis" in body


# ---------------------------------------------------------------------------
# Form analysis tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_form_analysis_renders_sections(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET form analysis page renders section labels in HTML (server-side)."""
    await _make_repo(db_session)
    response = await client.get(_FORM_URL)
    assert response.status_code == 200
    body = response.text
    assert "Form Analysis" in body
    assert "Form" in body
    # Sections table is always rendered; at least one section label must appear
    section_labels = {"intro", "verse", "chorus", "bridge", "outro"}
    assert any(label in body.lower() for label in section_labels), (
        "Expected a section label (intro/verse/chorus/bridge/outro) in response"
    )


@pytest.mark.anyio
async def test_form_analysis_renders_timeline(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Form page renders section timeline as CSS bars — server-side only."""
    await _make_repo(db_session)
    response = await client.get(_FORM_URL)
    assert response.status_code == 200
    body = response.text
    assert "Form Timeline" in body
    assert "Sections" in body
    assert "Total Beats" in body


@pytest.mark.anyio
async def test_form_analysis_htmx_fragment_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET form page with HX-Request: true returns fragment only."""
    await _make_repo(db_session)
    response = await client.get(_FORM_URL, headers={"HX-Request": "true"})
    assert response.status_code == 200
    body = response.text
    assert "<html" not in body
    assert "Form Analysis" in body


# ---------------------------------------------------------------------------
# No JS chart library tests (all pages)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_analysis_pages_no_js_chart_lib(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """None of the analysis SSR pages reference ChartJS or D3 chart libraries."""
    await _make_repo(db_session)
    urls = [_DASHBOARD_URL, _KEY_URL, _TEMPO_URL, _METER_URL, _GROOVE_URL, _FORM_URL]
    for url in urls:
        response = await client.get(url)
        assert response.status_code == 200, f"Expected 200 for {url}"
        body = response.text.lower()
        assert "chart.js" not in body, f"ChartJS found in {url}"
        assert "d3.js" not in body, f"D3 found in {url}"
        assert "cdn.jsdelivr.net/npm/chart" not in body, f"ChartJS CDN found in {url}"
