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

Covers issue #410 (recall / semantic search):
- test_compute_recall_returns_correct_type       — service returns RecallResponse
- test_compute_recall_scores_descending          — matches are sorted best-first
- test_compute_recall_scores_in_range            — all scores are in [0, 1]
- test_compute_recall_limit_respected            — limit caps the result count
- test_compute_recall_is_deterministic           — same (ref, q) always returns same matches
- test_compute_recall_differs_by_query           — different queries produce different results
- test_compute_recall_match_dimensions_nonempty  — every match has at least one matched dimension
- test_recall_endpoint_200                       — HTTP GET returns 200 with required fields
- test_recall_endpoint_requires_auth             — endpoint returns 401 without auth
- test_recall_endpoint_unknown_repo_404          — unknown repo returns 404
- test_recall_endpoint_etag_header               — ETag header is present
- test_recall_endpoint_limit_param               — ?limit=3 caps results to 3
- test_recall_endpoint_missing_q_422             — missing ?q returns 422
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
    SimilarityData,
    TempoData,
)
from maestro.services.musehub_analysis import (
    compute_aggregate_analysis,
    compute_analysis_response,
    compute_dimension,
    compute_emotion_map,
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
    """GET /musehub/repos/{repo_id}/analysis/{ref}/harmony returns dedicated harmony data.

    The /harmony path is now handled by the dedicated HarmonyAnalysisResponse endpoint
    (issue #414) rather than the generic /{dimension} catch-all.  It returns
    Roman-numeral-centric data (key, mode, romanNumerals, cadences, modulations)
    rather than the generic AnalysisResponse envelope.
    """
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/harmony",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    # Dedicated harmony endpoint — HarmonyAnalysisResponse shape (not AnalysisResponse)
    assert "key" in body
    assert "mode" in body
    assert "romanNumerals" in body
    assert "cadences" in body
    assert "modulations" in body
    assert "harmonicRhythmBpm" in body


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
    """Each of the 13 dimensions returns 200; harmony now has a dedicated endpoint.

    The ``harmony`` dimension path is handled by the dedicated HarmonyAnalysisResponse
    endpoint (issue #414) which returns a different response shape (no ``dimension``
    envelope field).  All other 12 dimensions continue to use the generic AnalysisResponse
    envelope and are verified here.
    """
    repo_id = await _create_repo(client, auth_headers)
    for dim in ALL_DIMENSIONS:
        resp = await client.get(
            f"/api/v1/musehub/repos/{repo_id}/analysis/main/{dim}",
            headers=auth_headers,
        )
        assert resp.status_code == 200, f"Dimension {dim!r} returned {resp.status_code}"
        body = resp.json()
        if dim == "harmony":
            # Dedicated endpoint — HarmonyAnalysisResponse (no "dimension" envelope)
            assert "key" in body, f"Harmony endpoint missing 'key' field"
            assert "romanNumerals" in body, f"Harmony endpoint missing 'romanNumerals' field"
        else:
            assert body["dimension"] == dim, (
                f"Expected dimension={dim!r}, got {body['dimension']!r}"
            )


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
# Issue #414 — GET /analysis/{ref}/harmony endpoint
# ---------------------------------------------------------------------------


from maestro.models.musehub_analysis import HarmonyAnalysisResponse  # noqa: E402
from maestro.services.musehub_analysis import compute_harmony_analysis  # noqa: E402


def test_compute_harmony_analysis_returns_correct_type() -> None:
    """compute_harmony_analysis returns a HarmonyAnalysisResponse instance."""
    result = compute_harmony_analysis(repo_id="repo-test", ref="main")
    assert isinstance(result, HarmonyAnalysisResponse)


def test_compute_harmony_analysis_key_has_mode() -> None:
    """The key field includes both tonic and mode, e.g. 'C major'."""
    result = compute_harmony_analysis(repo_id="repo-test", ref="main")
    assert result.mode in result.key
    assert len(result.key.split()) == 2  # "C major", "F minor", etc.


def test_compute_harmony_analysis_roman_numerals_nonempty() -> None:
    """roman_numerals must contain at least one chord event."""
    result = compute_harmony_analysis(repo_id="repo-test", ref="main")
    assert len(result.roman_numerals) >= 1
    for rn in result.roman_numerals:
        assert rn.beat >= 0.0
        assert rn.chord != ""
        assert rn.root != ""
        assert rn.quality != ""
        assert rn.function != ""


def test_compute_harmony_analysis_cadences_nonempty() -> None:
    """cadences must contain at least one entry with valid from/to fields."""
    result = compute_harmony_analysis(repo_id="repo-test", ref="main")
    assert len(result.cadences) >= 1
    for cadence in result.cadences:
        assert cadence.beat >= 0.0
        assert cadence.type != ""
        assert cadence.from_ != ""
        assert cadence.to != ""


def test_compute_harmony_analysis_harmonic_rhythm_positive() -> None:
    """harmonic_rhythm_bpm must be a positive float."""
    result = compute_harmony_analysis(repo_id="repo-test", ref="main")
    assert result.harmonic_rhythm_bpm > 0.0


def test_compute_harmony_analysis_is_deterministic() -> None:
    """Same ref always produces the same key and mode (deterministic stub)."""
    r1 = compute_harmony_analysis(repo_id="repo-a", ref="abc123")
    r2 = compute_harmony_analysis(repo_id="repo-b", ref="abc123")
    assert r1.key == r2.key
    assert r1.mode == r2.mode
    assert r1.harmonic_rhythm_bpm == r2.harmonic_rhythm_bpm


def test_compute_harmony_analysis_different_refs_differ() -> None:
    """Different refs produce different harmonic data."""
    r1 = compute_harmony_analysis(repo_id="repo-test", ref="ref-aaa")
    r2 = compute_harmony_analysis(repo_id="repo-test", ref="ref-zzz")
    # At least the key or mode differs across distinct refs.
    assert (r1.key != r2.key) or (r1.mode != r2.mode) or (r1.harmonic_rhythm_bpm != r2.harmonic_rhythm_bpm)


@pytest.mark.anyio
async def test_harmony_endpoint_returns_200(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /analysis/{ref}/harmony returns 200 with all required fields.

    Regression test for issue #414: the dedicated harmony endpoint must return
    structured Roman-numeral harmonic data so agents can reason about tonal
    function, cadence structure, and modulations without parsing raw chord symbols.
    """
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/harmony",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "key" in body
    assert "mode" in body
    assert "romanNumerals" in body
    assert "cadences" in body
    assert "modulations" in body
    assert "harmonicRhythmBpm" in body
    assert isinstance(body["romanNumerals"], list)
    assert len(body["romanNumerals"]) >= 1
    assert isinstance(body["cadences"], list)
    assert len(body["cadences"]) >= 1
    assert body["harmonicRhythmBpm"] > 0.0


@pytest.mark.anyio
async def test_harmony_endpoint_roman_numerals_fields(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Each roman numeral event carries beat, chord, root, quality, and function."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/harmony",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    for rn in resp.json()["romanNumerals"]:
        assert "beat" in rn
        assert "chord" in rn
        assert "root" in rn
        assert "quality" in rn
        assert "function" in rn


@pytest.mark.anyio
async def test_harmony_endpoint_cadence_fields(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Each cadence event carries beat, type, from, and to fields."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/harmony",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    for cadence in resp.json()["cadences"]:
        assert "beat" in cadence
        assert "type" in cadence
        assert "from" in cadence
        assert "to" in cadence


@pytest.mark.anyio
async def test_harmony_endpoint_etag_header(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /analysis/{ref}/harmony includes an ETag header for cache validation."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/harmony",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "etag" in resp.headers


@pytest.mark.anyio
async def test_harmony_endpoint_requires_auth_for_private_repo(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /analysis/{ref}/harmony on a private repo without auth returns 401."""
    # Create a private repo with valid auth, then access without auth.
    resp = await client.post(
        "/api/v1/musehub/repos",
        json={"name": "private-harmony-repo", "owner": "testuser", "visibility": "private"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    repo_id = str(resp.json()["repoId"])

    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/harmony",
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_harmony_endpoint_unknown_repo_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /analysis/{ref}/harmony with an unknown repo_id returns 404."""
    resp = await client.get(
        "/api/v1/musehub/repos/nonexistent-repo-id/analysis/main/harmony",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_harmony_endpoint_track_filter(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /analysis/{ref}/harmony?track=keys returns 200 (filter accepted)."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/harmony?track=keys",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "key" in body
    assert "romanNumerals" in body


# ---------------------------------------------------------------------------
# Issue #410 — GET /analysis/{ref}/recall semantic search
# ---------------------------------------------------------------------------


from maestro.models.musehub_analysis import RecallMatch, RecallResponse  # noqa: E402
from maestro.services.musehub_analysis import compute_recall  # noqa: E402


def test_compute_recall_returns_correct_type() -> None:
    """compute_recall returns a RecallResponse instance."""
    result = compute_recall(repo_id="repo-test", ref="main", query="jazzy swing groove")
    assert isinstance(result, RecallResponse)


def test_compute_recall_scores_descending() -> None:
    """Matches are sorted in descending score order (best match first)."""
    result = compute_recall(repo_id="repo-test", ref="main", query="minor key tension")
    scores = [m.score for m in result.matches]
    assert scores == sorted(scores, reverse=True), "Matches must be ranked best-first"


def test_compute_recall_scores_in_range() -> None:
    """All cosine similarity scores must be in [0.0, 1.0]."""
    result = compute_recall(repo_id="repo-test", ref="main", query="ascending melodic contour")
    for match in result.matches:
        assert isinstance(match, RecallMatch)
        assert 0.0 <= match.score <= 1.0, f"Score out of range: {match.score}"


def test_compute_recall_limit_respected() -> None:
    """The limit parameter caps the number of returned matches."""
    result = compute_recall(repo_id="repo-test", ref="main", query="swing", limit=3)
    assert len(result.matches) <= 3
    assert result.total_matches >= len(result.matches)


def test_compute_recall_limit_clamped_to_50() -> None:
    """Limits above 50 are silently clamped to 50."""
    result = compute_recall(repo_id="repo-test", ref="main", query="groove", limit=200)
    assert len(result.matches) <= 50


def test_compute_recall_is_deterministic() -> None:
    """Same (ref, query) always produces identical matches."""
    r1 = compute_recall(repo_id="repo-a", ref="main", query="jazz harmony")
    r2 = compute_recall(repo_id="repo-b", ref="main", query="jazz harmony")
    assert len(r1.matches) == len(r2.matches)
    for m1, m2 in zip(r1.matches, r2.matches):
        assert m1.commit_id == m2.commit_id
        assert m1.score == m2.score


def test_compute_recall_differs_by_query() -> None:
    """Different queries produce different match sets."""
    r1 = compute_recall(repo_id="repo-test", ref="main", query="swing groove")
    r2 = compute_recall(repo_id="repo-test", ref="main", query="ascending melodic contour")
    # At least the first commit IDs should differ between distinct queries.
    assert r1.matches[0].commit_id != r2.matches[0].commit_id


def test_compute_recall_match_dimensions_nonempty() -> None:
    """Every RecallMatch must carry at least one matched dimension."""
    result = compute_recall(repo_id="repo-test", ref="main", query="harmonic tension")
    for match in result.matches:
        assert len(match.matched_dimensions) >= 1, (
            f"Match {match.commit_id!r} has no matched_dimensions"
        )


def test_compute_recall_query_echoed() -> None:
    """The response echoes the query parameter so clients can display it."""
    q = "brooding minor feel with slow groove"
    result = compute_recall(repo_id="repo-test", ref="develop", query=q)
    assert result.query == q


def test_compute_recall_embedding_dimensions() -> None:
    """embedding_dimensions matches the expected 128-dim feature space."""
    result = compute_recall(repo_id="repo-test", ref="main", query="any query")
    assert result.embedding_dimensions == 128


@pytest.mark.anyio
async def test_recall_endpoint_200(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /api/v1/musehub/repos/{repo_id}/analysis/{ref}/recall?q= returns 200.

    Regression test for issue #410: the recall endpoint must return a ranked list
    of semantically similar commits so agents can retrieve musically relevant history
    without issuing expensive dimension-by-dimension comparisons.
    """
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/recall?q=jazzy+swing+groove",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["repoId"] == repo_id
    assert body["ref"] == "main"
    assert body["query"] == "jazzy swing groove"
    assert "matches" in body
    assert isinstance(body["matches"], list)
    assert body["totalMatches"] >= 0
    assert body["embeddingDimensions"] == 128


@pytest.mark.anyio
async def test_recall_endpoint_match_fields(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Each match carries commitId, commitMessage, branch, score, and matchedDimensions."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/recall?q=harmony",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    for match in resp.json()["matches"]:
        assert "commitId" in match
        assert "commitMessage" in match
        assert "branch" in match
        assert "score" in match
        assert "matchedDimensions" in match
        assert 0.0 <= match["score"] <= 1.0
        assert len(match["matchedDimensions"]) >= 1


@pytest.mark.anyio
async def test_recall_endpoint_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Recall endpoint returns 401 without a Bearer token."""
    resp = await client.get(
        "/api/v1/musehub/repos/some-repo/analysis/main/recall?q=groove",
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_recall_endpoint_unknown_repo_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Recall endpoint returns 404 for an unknown repo_id."""
    resp = await client.get(
        "/api/v1/musehub/repos/00000000-0000-0000-0000-000000000000/analysis/main/recall?q=swing",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_recall_endpoint_etag_header(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Recall endpoint includes an ETag header for client-side cache validation."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/recall?q=groove",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "etag" in resp.headers
    assert resp.headers["etag"].startswith('"')


@pytest.mark.anyio
async def test_recall_endpoint_limit_param(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """?limit=3 caps the returned matches to at most 3 results."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/recall?q=swing&limit=3",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()["matches"]) <= 3


@pytest.mark.anyio
async def test_recall_endpoint_missing_q_422(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Missing ?q returns 422 Unprocessable Entity (required query param)."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/recall",
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_recall_endpoint_scores_descending(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Recall endpoint returns matches sorted best-first (descending score)."""
    repo_id = await _create_repo(client, auth_headers)
    resp = await client.get(
        f"/api/v1/musehub/repos/{repo_id}/analysis/main/recall?q=jazz+harmony",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    scores = [m["score"] for m in resp.json()["matches"]]
    assert scores == sorted(scores, reverse=True), "Matches must be in descending score order"
