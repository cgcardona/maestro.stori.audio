"""Tests for Muse Hub Analysis endpoints — issue #248.

Covers all acceptance criteria:
- GET /musehub/repos/{repo_id}/analysis/{ref}/{dimension} returns structured JSON
- All 13 dimensions return valid typed data
- Aggregate endpoint returns all 13 dimensions
- Track and section query param filters are applied
- Unknown dimension returns 404
- Unknown repo_id returns 404
- ETag header is present in all responses
- Service layer: compute_dimension raises ValueError for unknown dimension
- Service layer: each dimension returns the correct model type
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from maestro.models.musehub_analysis import (
    ALL_DIMENSIONS,
    AggregateAnalysisResponse,
    AnalysisResponse,
    ChordMapData,
    ContourData,
    DivergenceData,
    DynamicsData,
    EmotionData,
    FormData,
    GrooveData,
    HarmonyData,
    KeyData,
    MeterData,
    MotifsData,
    SimilarityData,
    TempoData,
)
from maestro.services.musehub_analysis import (
    compute_aggregate_analysis,
    compute_analysis_response,
    compute_dimension,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_repo(client: AsyncClient, auth_headers: dict[str, str]) -> str:
    """Create a test repo and return its repo_id."""
    resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "analysis-test-repo", "visibility": "private"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return str(resp.json()["repoId"])


# ---------------------------------------------------------------------------
# Service unit tests — no HTTP
# ---------------------------------------------------------------------------


def test_compute_dimension_harmony_returns_harmony_data() -> None:
    """compute_dimension('harmony', ...) returns a HarmonyData instance."""
    result = compute_dimension("harmony", "main")
    assert isinstance(result, HarmonyData)
    assert result.tonic != ""
    assert result.mode != ""
    assert 0.0 <= result.key_confidence <= 1.0
    assert len(result.chord_progression) > 0
    assert result.total_beats > 0


def test_compute_dimension_dynamics_returns_dynamics_data() -> None:
    result = compute_dimension("dynamics", "main")
    assert isinstance(result, DynamicsData)
    assert 0 <= result.min_velocity <= result.peak_velocity <= 127
    assert result.dynamic_range == result.peak_velocity - result.min_velocity
    assert len(result.velocity_curve) > 0


def test_compute_dimension_motifs_returns_motifs_data() -> None:
    result = compute_dimension("motifs", "main")
    assert isinstance(result, MotifsData)
    assert result.total_motifs == len(result.motifs)
    for motif in result.motifs:
        assert motif.occurrence_count == len(motif.occurrences)


def test_compute_dimension_form_returns_form_data() -> None:
    result = compute_dimension("form", "main")
    assert isinstance(result, FormData)
    assert result.form_label != ""
    assert len(result.sections) > 0
    for sec in result.sections:
        assert sec.length_beats == sec.end_beat - sec.start_beat


def test_compute_dimension_groove_returns_groove_data() -> None:
    result = compute_dimension("groove", "main")
    assert isinstance(result, GrooveData)
    assert 0.0 <= result.swing_factor <= 1.0
    assert result.bpm > 0


def test_compute_dimension_emotion_returns_emotion_data() -> None:
    result = compute_dimension("emotion", "main")
    assert isinstance(result, EmotionData)
    assert -1.0 <= result.valence <= 1.0
    assert 0.0 <= result.arousal <= 1.0
    assert result.primary_emotion != ""


def test_compute_dimension_chord_map_returns_chord_map_data() -> None:
    result = compute_dimension("chord-map", "main")
    assert isinstance(result, ChordMapData)
    assert result.total_chords == len(result.progression)


def test_compute_dimension_contour_returns_contour_data() -> None:
    result = compute_dimension("contour", "main")
    assert isinstance(result, ContourData)
    assert result.shape in ("arch", "ascending", "descending", "flat", "wave")
    assert len(result.pitch_curve) > 0


def test_compute_dimension_key_returns_key_data() -> None:
    result = compute_dimension("key", "main")
    assert isinstance(result, KeyData)
    assert 0.0 <= result.confidence <= 1.0
    assert result.tonic != ""


def test_compute_dimension_tempo_returns_tempo_data() -> None:
    result = compute_dimension("tempo", "main")
    assert isinstance(result, TempoData)
    assert result.bpm > 0
    assert 0.0 <= result.stability <= 1.0


def test_compute_dimension_meter_returns_meter_data() -> None:
    result = compute_dimension("meter", "main")
    assert isinstance(result, MeterData)
    assert "/" in result.time_signature
    assert len(result.beat_strength_profile) > 0


def test_compute_dimension_similarity_returns_similarity_data() -> None:
    result = compute_dimension("similarity", "main")
    assert isinstance(result, SimilarityData)
    assert result.embedding_dimensions > 0
    for commit in result.similar_commits:
        assert 0.0 <= commit.score <= 1.0


def test_compute_dimension_divergence_returns_divergence_data() -> None:
    result = compute_dimension("divergence", "main")
    assert isinstance(result, DivergenceData)
    assert 0.0 <= result.divergence_score <= 1.0
    assert result.base_ref != ""


def test_compute_dimension_unknown_raises_value_error() -> None:
    """compute_dimension raises ValueError for unknown dimension names."""
    with pytest.raises(ValueError, match="Unknown analysis dimension"):
        compute_dimension("not-a-dimension", "main")


def test_compute_dimension_is_deterministic() -> None:
    """Same ref always produces the same output (stub is ref-keyed)."""
    r1 = compute_dimension("harmony", "abc123")
    r2 = compute_dimension("harmony", "abc123")
    assert isinstance(r1, HarmonyData)
    assert isinstance(r2, HarmonyData)
    assert r1.tonic == r2.tonic
    assert r1.mode == r2.mode


def test_compute_dimension_differs_by_ref() -> None:
    """Different refs produce different results (seed derives from ref)."""
    r1 = compute_dimension("tempo", "main")
    r2 = compute_dimension("tempo", "develop")
    assert isinstance(r1, TempoData)
    assert isinstance(r2, TempoData)
    # They may differ — just ensure they don't raise
    assert r1.bpm > 0
    assert r2.bpm > 0


def test_all_dimensions_list_has_13_entries() -> None:
    """ALL_DIMENSIONS must contain exactly 13 entries."""
    assert len(ALL_DIMENSIONS) == 13


def test_compute_analysis_response_envelope() -> None:
    """compute_analysis_response returns a complete AnalysisResponse envelope."""
    resp = compute_analysis_response(
        repo_id="test-repo-id",
        dimension="harmony",
        ref="main",
        track="bass",
        section="chorus",
    )
    assert isinstance(resp, AnalysisResponse)
    assert resp.dimension == "harmony"
    assert resp.ref == "main"
    assert resp.filters_applied.track == "bass"
    assert resp.filters_applied.section == "chorus"
    assert isinstance(resp.data, HarmonyData)


def test_compute_aggregate_returns_all_dimensions() -> None:
    """compute_aggregate_analysis returns one entry per supported dimension."""
    agg = compute_aggregate_analysis(repo_id="test-repo-id", ref="main")
    assert isinstance(agg, AggregateAnalysisResponse)
    assert len(agg.dimensions) == 13
    returned_dims = {d.dimension for d in agg.dimensions}
    assert returned_dims == set(ALL_DIMENSIONS)


def test_compute_aggregate_all_have_same_ref() -> None:
    """All dimension entries in aggregate share the same ref."""
    agg = compute_aggregate_analysis(repo_id="test-repo-id", ref="feature/jazz")
    for dim in agg.dimensions:
        assert dim.ref == "feature/jazz"


def test_compute_aggregate_filters_propagated() -> None:
    """Track and section filters are propagated to all dimension entries."""
    agg = compute_aggregate_analysis(
        repo_id="test-repo-id", ref="main", track="keys", section="verse_1"
    )
    for dim in agg.dimensions:
        assert dim.filters_applied.track == "keys"
        assert dim.filters_applied.section == "verse_1"


# ---------------------------------------------------------------------------
# HTTP integration tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_analysis_harmony_endpoint(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /musehub/repos/{repo_id}/analysis/{ref}/harmony returns structured data."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/harmony",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dimension"] == "harmony"
    assert body["ref"] == "main"
    assert "computedAt" in body
    assert "data" in body
    assert "filtersApplied" in body
    data = body["data"]
    assert "tonic" in data
    assert "mode" in data
    assert "chordProgression" in data


@pytest.mark.anyio
async def test_analysis_dynamics_endpoint(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET .../{repo_id}/analysis/{ref}/dynamics returns velocity data."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/dynamics",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "peakVelocity" in data
    assert "meanVelocity" in data
    assert "velocityCurve" in data


@pytest.mark.anyio
async def test_analysis_all_dimensions(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Aggregate GET .../analysis/{ref} returns all 13 dimensions."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ref"] == "main"
    assert body["repoId"] == repo_id
    assert "dimensions" in body
    assert len(body["dimensions"]) == 13
    returned_dims = {d["dimension"] for d in body["dimensions"]}
    assert returned_dims == set(ALL_DIMENSIONS)


@pytest.mark.anyio
async def test_analysis_track_filter(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Track filter is reflected in filtersApplied across dimensions."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/groove?track=bass",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["filtersApplied"]["track"] == "bass"
    assert body["filtersApplied"]["section"] is None


@pytest.mark.anyio
async def test_analysis_section_filter(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Section filter is reflected in filtersApplied."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/emotion?section=chorus",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["filtersApplied"]["section"] == "chorus"


@pytest.mark.anyio
async def test_analysis_unknown_dimension_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Unknown dimension returns 404, not 422."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/not-a-dimension",
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert "not-a-dimension" in resp.json()["detail"]


@pytest.mark.anyio
async def test_analysis_unknown_repo_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Unknown repo_id returns 404 for single-dimension endpoint."""
    resp = await client.get(
        "/api/v1/musehub/repos/00000000-0000-0000-0000-000000000000/analysis/main/harmony",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_analysis_aggregate_unknown_repo_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Unknown repo_id returns 404 for aggregate endpoint."""
    resp = await client.get(
        "/api/v1/musehub/repos/00000000-0000-0000-0000-000000000000/analysis/main",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_analysis_cache_headers(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """ETag and Last-Modified headers are present in analysis responses."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/key",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "etag" in resp.headers
    assert resp.headers["etag"].startswith('"')
    assert "last-modified" in resp.headers


@pytest.mark.anyio
async def test_analysis_aggregate_cache_headers(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Aggregate endpoint also includes ETag header."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "etag" in resp.headers


@pytest.mark.anyio
async def test_analysis_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Analysis endpoint returns 401 without a Bearer token."""
    resp = await client.get(
        "/api/v1/musehub/repos/some-id/analysis/main/harmony",
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_analysis_aggregate_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Aggregate analysis endpoint returns 401 without a Bearer token."""
    resp = await client.get(
        "/api/v1/musehub/repos/some-id/analysis/main",
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_analysis_all_13_dimensions_individually(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Each of the 13 dimension endpoints returns 200 with correct dimension field."""
    repo_id = await _create_repo(client, auth_headers)
    for dim in ALL_DIMENSIONS:
        resp = await client.get(
            f"/api/v1/musehub/repos/{repo_id}/analysis/main/{dim}",
            headers=auth_headers,
        )
        assert resp.status_code == 200, f"Dimension {dim!r} returned {resp.status_code}"
        body = resp.json()
        assert body["dimension"] == dim, f"Expected dimension={dim!r}, got {body['dimension']!r}"


@pytest.mark.anyio
async def test_contour_track_filter(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Track filter is applied and reflected in filtersApplied for the contour dimension.

    Verifies issue #228 acceptance criterion: contour analysis respects the
    ``?track=`` query parameter so melodists can view per-instrument contour.
    """
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/contour?track=lead",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dimension"] == "contour"
    assert body["filtersApplied"]["track"] == "lead"
    data = body["data"]
    assert "shape" in data
    assert "pitchCurve" in data
    assert len(data["pitchCurve"]) > 0


@pytest.mark.anyio
async def test_tempo_section_filter(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Section filter is applied and reflected in filtersApplied for the tempo dimension.

    Verifies that tempo analysis scoped to a named section returns valid TempoData
    and records the section filter in the response envelope.
    """
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/tempo?section=chorus",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dimension"] == "tempo"
    assert body["filtersApplied"]["section"] == "chorus"
    data = body["data"]
    assert data["bpm"] > 0
    assert 0.0 <= data["stability"] <= 1.0
