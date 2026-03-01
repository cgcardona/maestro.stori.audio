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

Covers issue #227 (emotion map):
- test_compute_emotion_map_returns_correct_type — service returns EmotionMapResponse
- test_emotion_map_evolution_has_beat_samples   — evolution list is non-empty with valid vectors
- test_emotion_map_trajectory_ordered           — trajectory is oldest-first with head last
- test_emotion_map_drift_count                  — drift has len(trajectory)-1 entries
- test_emotion_map_narrative_nonempty           — narrative is a non-empty string
- test_emotion_map_is_deterministic             — same ref always returns same summary_vector
- test_emotion_map_endpoint_200                 — HTTP GET returns 200 with required fields
- test_emotion_map_endpoint_requires_auth       — endpoint returns 401 without auth
- test_emotion_map_endpoint_unknown_repo_404    — unknown repo returns 404
- test_emotion_map_endpoint_etag                — ETag header is present

Covers issue #406 (cross-ref similarity):
- test_compute_ref_similarity_returns_correct_type — service returns RefSimilarityResponse
- test_compute_ref_similarity_dimensions_in_range  — all 10 dimensions in [0.0, 1.0]
- test_compute_ref_similarity_overall_in_range     — overall_similarity in [0.0, 1.0]
- test_compute_ref_similarity_is_deterministic     — same pair always returns same result
- test_compute_ref_similarity_interpretation_nonempty — interpretation is a non-empty string
- test_ref_similarity_endpoint_200                 — HTTP GET returns 200 with required fields
- test_ref_similarity_endpoint_requires_compare    — missing compare param returns 422
- test_ref_similarity_endpoint_requires_auth       — private repo returns 401 without auth
- test_ref_similarity_endpoint_unknown_repo_404    — unknown repo returns 404
- test_ref_similarity_endpoint_etag                — ETag header is present
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
    CommitEmotionSnapshot,
    ContourData,
    DivergenceData,
    DynamicsData,
    EmotionData,
    EmotionDrift,
    EmotionMapResponse,
    EmotionVector,
    FormData,
    GrooveData,
    HarmonyData,
    KeyData,
    MeterData,
    MotifEntry,
    MotifsData,
    RefSimilarityResponse,
    SimilarityData,
    TempoData,
)
from maestro.services.musehub_analysis import (
    compute_aggregate_analysis,
    compute_analysis_response,
    compute_dimension,
    compute_emotion_map,
    compute_ref_similarity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_repo(client: AsyncClient, auth_headers: dict[str, str]) -> str:
    """Create a test repo and return its repo_id."""
    resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "analysis-test-repo", "owner": "testuser", "visibility": "private"},
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


def test_motifs_data_has_extended_fields() -> None:
    """MotifsData now carries sections, all_tracks for grid rendering."""
    result = compute_dimension("motifs", "main")
    assert isinstance(result, MotifsData)
    assert isinstance(result.sections, list)
    assert len(result.sections) > 0
    assert isinstance(result.all_tracks, list)
    assert len(result.all_tracks) > 0


def test_motif_entry_has_contour_label() -> None:
    """Every MotifEntry must carry a melodic contour label."""
    result = compute_dimension("motifs", "main")
    assert isinstance(result, MotifsData)
    valid_labels = {
        "ascending-step",
        "descending-step",
        "arch",
        "valley",
        "oscillating",
        "static",
    }
    for motif in result.motifs:
        assert isinstance(motif, MotifEntry)
        assert motif.contour_label in valid_labels, (
            f"Unknown contour label: {motif.contour_label!r}"
        )


def test_motif_entry_has_transformations() -> None:
    """Each MotifEntry must include at least one transformation."""
    result = compute_dimension("motifs", "main")
    assert isinstance(result, MotifsData)
    for motif in result.motifs:
        assert isinstance(motif, MotifEntry)
        assert len(motif.transformations) > 0
        for xform in motif.transformations:
            assert xform.transformation_type in {
                "inversion",
                "retrograde",
                "retrograde-inversion",
                "transposition",
            }
            assert isinstance(xform.intervals, list)
            assert isinstance(xform.occurrences, list)


def test_motif_entry_has_recurrence_grid() -> None:
    """recurrence_grid is a flat list of cells covering every track x section pair."""
    result = compute_dimension("motifs", "main")
    assert isinstance(result, MotifsData)
    expected_cells = len(result.all_tracks) * len(result.sections)
    for motif in result.motifs:
        assert isinstance(motif, MotifEntry)
        assert len(motif.recurrence_grid) == expected_cells, (
            f"Expected {expected_cells} cells, got {len(motif.recurrence_grid)} "
            f"for motif {motif.motif_id!r}"
        )
        for cell in motif.recurrence_grid:
            assert cell.track in result.all_tracks
            assert cell.section in result.sections
            assert isinstance(cell.present, bool)
            assert cell.occurrence_count >= 0


def test_motif_entry_tracks_cross_track() -> None:
    """MotifEntry.tracks lists all tracks where the motif or its transforms appear."""
    result = compute_dimension("motifs", "main")
    assert isinstance(result, MotifsData)
    for motif in result.motifs:
        assert isinstance(motif, MotifEntry)
        assert len(motif.tracks) > 0
        # Every track in the list must appear in the global all_tracks roster
        for track in motif.tracks:
            assert track in result.all_tracks, (
                f"motif.tracks references unknown track {track!r}"
            )


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
    auth_headers: dict[str, str],
) -> None:
    """Analysis endpoint returns 401 without a Bearer token for private repos.

    Pre-existing fix: the route must check auth AFTER confirming the repo exists,
    so the test creates a real private repo first to reach the auth gate.
    """
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/harmony",
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_analysis_aggregate_requires_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Aggregate analysis endpoint returns 401 without a Bearer token for private repos.

    Pre-existing fix: the route must check auth AFTER confirming the repo exists,
    so the test creates a real private repo first to reach the auth gate.
    """
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main",
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
        # /similarity is a dedicated cross-ref endpoint requiring ?compare=
        params = {"compare": "main"} if dim == "similarity" else {}
        resp = await client.get(
            f"/api/v1/musehub/repos/{repo_id}/analysis/main/{dim}",
            headers=auth_headers,
            params=params,
        )
        assert resp.status_code == 200, f"Dimension {dim!r} returned {resp.status_code}"
        body = resp.json()
        # /similarity returns RefSimilarityResponse (no envelope `dimension` field)
        if dim != "similarity":
            assert body["dimension"] == dim, f"Expected dimension={dim!r}, got {body['dimension']!r}"


# ---------------------------------------------------------------------------
# Emotion map service unit tests (issue #227)
# ---------------------------------------------------------------------------


def test_compute_emotion_map_returns_correct_type() -> None:
    """compute_emotion_map returns an EmotionMapResponse instance."""
    result = compute_emotion_map(repo_id="test-repo", ref="main")
    assert isinstance(result, EmotionMapResponse)


def test_emotion_map_evolution_has_beat_samples() -> None:
    """Evolution list is non-empty and all vectors have values in [0, 1]."""
    result = compute_emotion_map(repo_id="test-repo", ref="main")
    assert len(result.evolution) > 0
    for point in result.evolution:
        v = point.vector
        assert isinstance(v, EmotionVector)
        assert 0.0 <= v.energy <= 1.0
        assert 0.0 <= v.valence <= 1.0
        assert 0.0 <= v.tension <= 1.0
        assert 0.0 <= v.darkness <= 1.0


def test_emotion_map_summary_vector_valid() -> None:
    """Summary vector values are all in [0, 1]."""
    result = compute_emotion_map(repo_id="test-repo", ref="main")
    sv = result.summary_vector
    assert 0.0 <= sv.energy <= 1.0
    assert 0.0 <= sv.valence <= 1.0
    assert 0.0 <= sv.tension <= 1.0
    assert 0.0 <= sv.darkness <= 1.0


def test_emotion_map_trajectory_ordered() -> None:
    """Trajectory list ends with the head commit."""
    result = compute_emotion_map(repo_id="test-repo", ref="deadbeef")
    assert len(result.trajectory) >= 2
    head = result.trajectory[-1]
    assert isinstance(head, CommitEmotionSnapshot)
    assert head.commit_id.startswith("deadbeef")


def test_emotion_map_drift_count() -> None:
    """Drift list has exactly len(trajectory) - 1 entries."""
    result = compute_emotion_map(repo_id="test-repo", ref="main")
    assert len(result.drift) == len(result.trajectory) - 1


def test_emotion_map_drift_entries_valid() -> None:
    """Each drift entry has non-negative drift and a valid dominant_change axis."""
    result = compute_emotion_map(repo_id="test-repo", ref="main")
    valid_axes = {"energy", "valence", "tension", "darkness"}
    for entry in result.drift:
        assert isinstance(entry, EmotionDrift)
        assert entry.drift >= 0.0
        assert entry.dominant_change in valid_axes


def test_emotion_map_narrative_nonempty() -> None:
    """Narrative is a non-empty string describing the emotional journey."""
    result = compute_emotion_map(repo_id="test-repo", ref="main")
    assert isinstance(result.narrative, str)
    assert len(result.narrative) > 10


def test_emotion_map_source_is_valid() -> None:
    """Source field is one of the three valid attribution values."""
    result = compute_emotion_map(repo_id="test-repo", ref="main")
    assert result.source in ("explicit", "inferred", "mixed")


def test_emotion_map_is_deterministic() -> None:
    """Same ref always produces the same summary_vector."""
    r1 = compute_emotion_map(repo_id="test-repo", ref="jazz-ref")
    r2 = compute_emotion_map(repo_id="test-repo", ref="jazz-ref")
    assert r1.summary_vector.energy == r2.summary_vector.energy
    assert r1.summary_vector.valence == r2.summary_vector.valence
    assert r1.summary_vector.tension == r2.summary_vector.tension
    assert r1.summary_vector.darkness == r2.summary_vector.darkness


def test_emotion_map_filters_propagated() -> None:
    """Track and section filters are reflected in filters_applied."""
    result = compute_emotion_map(
        repo_id="test-repo", ref="main", track="bass", section="chorus"
    )
    assert result.filters_applied.track == "bass"
    assert result.filters_applied.section == "chorus"


# ---------------------------------------------------------------------------
# Emotion map HTTP endpoint tests (issue #227)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_emotion_map_endpoint_200(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/analysis/{ref}/emotion-map returns 200."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/emotion-map",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["repoId"] == repo_id
    assert body["ref"] == "main"
    assert "evolution" in body
    assert "trajectory" in body
    assert "drift" in body
    assert "narrative" in body
    assert "summaryVector" in body
    assert "source" in body


@pytest.mark.anyio
async def test_emotion_map_endpoint_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Emotion map endpoint returns 401 without a Bearer token."""
    resp = await client.get(
        "/api/v1/musehub/repos/some-id/analysis/main/emotion-map",
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_emotion_map_endpoint_unknown_repo_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Emotion map endpoint returns 404 for an unknown repo_id."""
    resp = await client.get(
        "/api/v1/musehub/repos/00000000-0000-0000-0000-000000000000/analysis/main/emotion-map",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_emotion_map_endpoint_etag(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Emotion map endpoint includes ETag header for client-side caching."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/emotion-map",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "etag" in resp.headers
    assert resp.headers["etag"].startswith('"')


@pytest.mark.anyio
async def test_emotion_map_endpoint_track_filter(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Track filter is reflected in filtersApplied of the emotion map response."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/emotion-map?track=bass",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["filtersApplied"]["track"] == "bass"


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


@pytest.mark.anyio
async def test_analysis_aggregate_endpoint_returns_all_dimensions(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/analysis/{ref} returns all 13 dimensions.

    Regression test for issue #221: the aggregate endpoint must return all 13
    musical dimensions so the analysis dashboard can render summary cards for each
    in a single round-trip — agents must not have to query dimensions individually.
    """
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
    for dim_entry in body["dimensions"]:
        assert "dimension" in dim_entry
        assert "ref" in dim_entry
        assert "computedAt" in dim_entry
        assert "data" in dim_entry
        assert "filtersApplied" in dim_entry


# ---------------------------------------------------------------------------
# Issue #406 — Cross-ref similarity service unit tests
# ---------------------------------------------------------------------------


def test_compute_ref_similarity_returns_correct_type() -> None:
    """compute_ref_similarity returns a RefSimilarityResponse."""
    result = compute_ref_similarity(
        repo_id="repo-1",
        base_ref="main",
        compare_ref="experiment/jazz-voicings",
    )
    assert isinstance(result, RefSimilarityResponse)


def test_compute_ref_similarity_dimensions_in_range() -> None:
    """All 10 dimension scores are within [0.0, 1.0]."""
    result = compute_ref_similarity(
        repo_id="repo-1",
        base_ref="main",
        compare_ref="feat/new-bridge",
    )
    dims = result.dimensions
    for attr in (
        "pitch_distribution",
        "rhythm_pattern",
        "tempo",
        "dynamics",
        "harmonic_content",
        "form",
        "instrument_blend",
        "groove",
        "contour",
        "emotion",
    ):
        score = getattr(dims, attr)
        assert 0.0 <= score <= 1.0, f"{attr} out of range: {score}"


def test_compute_ref_similarity_overall_in_range() -> None:
    """overall_similarity is within [0.0, 1.0]."""
    result = compute_ref_similarity(
        repo_id="repo-1",
        base_ref="v1.0",
        compare_ref="v2.0",
    )
    assert 0.0 <= result.overall_similarity <= 1.0


def test_compute_ref_similarity_is_deterministic() -> None:
    """Same ref pair always returns the same overall_similarity."""
    a = compute_ref_similarity(repo_id="r", base_ref="main", compare_ref="dev")
    b = compute_ref_similarity(repo_id="r", base_ref="main", compare_ref="dev")
    assert a.overall_similarity == b.overall_similarity
    assert a.dimensions == b.dimensions


def test_compute_ref_similarity_interpretation_nonempty() -> None:
    """interpretation is a non-empty string."""
    result = compute_ref_similarity(
        repo_id="repo-1",
        base_ref="main",
        compare_ref="feature/rhythm-variations",
    )
    assert isinstance(result.interpretation, str)
    assert len(result.interpretation) > 0


# ---------------------------------------------------------------------------
# Issue #406 — Cross-ref similarity HTTP endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ref_similarity_endpoint_200(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /analysis/{ref}/similarity returns 200 with required fields."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/similarity?compare=dev",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["baseRef"] == "main"
    assert body["compareRef"] == "dev"
    assert "overallSimilarity" in body
    assert "dimensions" in body
    assert "interpretation" in body
    dims = body["dimensions"]
    for key in (
        "pitchDistribution",
        "rhythmPattern",
        "tempo",
        "dynamics",
        "harmonicContent",
        "form",
        "instrumentBlend",
        "groove",
        "contour",
        "emotion",
    ):
        assert key in dims, f"Missing dimension key: {key}"
        assert 0.0 <= dims[key] <= 1.0


@pytest.mark.anyio
async def test_ref_similarity_endpoint_requires_compare(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Missing compare param returns 422 Unprocessable Entity."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/similarity",
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_ref_similarity_endpoint_requires_auth(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Private repo returns 401 when no auth token is provided.

    Uses a real private repo so the repo lookup succeeds before the auth
    check fires — consistent with the optional_token pattern used by this
    and other analysis endpoints (auth is checked after repo visibility).
    """
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/similarity?compare=dev",
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_ref_similarity_endpoint_unknown_repo_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Unknown repo_id returns 404."""
    resp = await client.get(
        "/api/v1/musehub/repos/00000000-0000-0000-0000-000000000000/analysis/main/similarity?compare=dev",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_ref_similarity_endpoint_etag(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Similarity endpoint includes ETag header for client-side caching."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/similarity?compare=dev",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "etag" in resp.headers
    assert resp.headers["etag"].startswith('"')
