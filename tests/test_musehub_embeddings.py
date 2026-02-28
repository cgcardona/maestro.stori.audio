"""Tests for MuseHub musical feature vector extraction.

Covers acceptance criteria from issue #237:
- Musical feature extraction from commit messages
- Deterministic, reproducible embeddings for the same input
- Correct feature parsing (key, tempo, mode, chord complexity)
- Vector dimensionality and normalisation
- push triggers feature extraction (embed_push_commits integration)
"""
from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

from maestro.services.musehub_embeddings import (
    VECTOR_DIM,
    MusicalFeatures,
    _encode_text_fingerprint,
    _l2_normalise,
    compute_embedding,
    extract_features_from_message,
    features_to_vector,
)


# ---------------------------------------------------------------------------
# extract_features_from_message
# ---------------------------------------------------------------------------


def test_embedding_computed_on_push_extracts_key() -> None:
    """Push commit with key info in message extracts the correct key index."""
    features = extract_features_from_message("Jazz ballad in Db major at 72 BPM")
    assert features.key_index == 1  # Db is index 1 in _CHROMATIC


def test_extract_features_major_mode_score() -> None:
    """Major key commit yields mode_score ≥ 0.5."""
    features = extract_features_from_message("Composition in G major 120 BPM")
    assert features.key_index == 7  # G
    assert features.mode_score >= 0.5


def test_extract_features_minor_mode_score() -> None:
    """Minor key commit yields mode_score < 0.5."""
    features = extract_features_from_message("Dark theme in A minor 80 BPM")
    assert features.mode_score < 0.5


def test_extract_features_tempo_normalisation() -> None:
    """Tempo is normalised into [0, 1] range."""
    features = extract_features_from_message("Fast tempo at 180 BPM")
    assert 0.0 <= features.tempo_norm <= 1.0
    # 180 BPM: (180-20)/280 ≈ 0.571
    assert abs(features.tempo_norm - (180 - 20) / 280.0) < 0.01


def test_extract_features_tempo_clamped() -> None:
    """Tempo outside valid range is clamped to [0, 1]."""
    features = extract_features_from_message("Extreme tempo 350 BPM")
    assert features.tempo_norm == 1.0


def test_extract_features_chord_complexity_extended() -> None:
    """Commit mentioning extended chords yields higher chord_complexity."""
    simple = extract_features_from_message("Simple triads in C major")
    extended = extract_features_from_message("Jazz chords: 7th 9th 11th 13th in C major")
    assert extended.chord_complexity > simple.chord_complexity


def test_extract_features_chroma_populated_for_known_key() -> None:
    """Chroma histogram is non-zero for commits with a known key."""
    features = extract_features_from_message("Piece in C major 120 BPM")
    assert features.chroma[0] > 0  # C is tonic
    assert features.chroma[7] > 0  # G is perfect fifth


def test_extract_features_unknown_message() -> None:
    """Non-musical commit message produces neutral defaults without crashing."""
    features = extract_features_from_message("fix: typo in README")
    assert features.key_index == -1
    assert 0.0 <= features.mode_score <= 1.0
    assert 0.0 <= features.valence <= 1.0


def test_extract_features_valence_matches_mode() -> None:
    """Valence equals mode_score — derived from mode, not independently computed."""
    features = extract_features_from_message("Piece in F major 120 BPM")
    assert features.valence == features.mode_score


# ---------------------------------------------------------------------------
# features_to_vector
# ---------------------------------------------------------------------------


def test_features_to_vector_correct_dimension() -> None:
    """Output vector has exactly VECTOR_DIM dimensions."""
    features = MusicalFeatures()
    vector = features_to_vector(features)
    assert len(vector) == VECTOR_DIM


def test_features_to_vector_l2_unit_norm() -> None:
    """Non-zero vector is L2-normalised (norm ≈ 1.0)."""
    features = MusicalFeatures(key_index=0, mode_score=0.7, tempo_norm=0.5)
    vector = features_to_vector(features)
    norm = math.sqrt(sum(v * v for v in vector))
    assert abs(norm - 1.0) < 1e-6


def test_features_to_vector_all_finite() -> None:
    """All vector components are finite (no NaN or Inf)."""
    features = extract_features_from_message("F# minor at 90 BPM with sus chords")
    vector = features_to_vector(features)
    assert all(math.isfinite(v) for v in vector)


# ---------------------------------------------------------------------------
# compute_embedding
# ---------------------------------------------------------------------------


def test_embedding_stored_in_qdrant_deterministic() -> None:
    """Same commit message always produces the same embedding vector."""
    message = "Jazz ballad in Db major at 72 BPM"
    v1 = compute_embedding(message)
    v2 = compute_embedding(message)
    assert v1 == v2


def test_compute_embedding_different_messages_differ() -> None:
    """Distinct commit messages produce distinct embeddings."""
    v1 = compute_embedding("Piece in C major 120 BPM")
    v2 = compute_embedding("Piece in F# minor 60 BPM with 9th 11th chords")
    assert v1 != v2


def test_compute_embedding_returns_vector_dim() -> None:
    """compute_embedding returns a vector of the expected dimensionality."""
    vector = compute_embedding("Test composition")
    assert len(vector) == VECTOR_DIM


# ---------------------------------------------------------------------------
# embed_push_commits — integration with MusehubQdrantClient
# ---------------------------------------------------------------------------


def test_embedding_computed_on_push_calls_upsert() -> None:
    """embed_push_commits calls qdrant upsert for each commit in the push payload."""
    from maestro.models.musehub import CommitInput
    from datetime import datetime, timezone
    from maestro.services.musehub_sync import embed_push_commits

    commits = [
        CommitInput(
            commit_id="abc123",
            parent_ids=[],
            message="Jazz ballad in Db major at 72 BPM",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            snapshot_id=None,
            author=None,
        ),
        CommitInput(
            commit_id="def456",
            parent_ids=["abc123"],
            message="Variation in A minor at 90 BPM",
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            snapshot_id=None,
            author=None,
        ),
    ]

    mock_client = MagicMock()
    with patch(
        "maestro.services.musehub_sync.MusehubQdrantClient",
        return_value=mock_client,
    ):
        embed_push_commits(
            commits=commits,
            repo_id="repo-001",
            branch="main",
            author="composer@stori",
            is_public=True,
        )

    mock_client.ensure_collection.assert_called_once()
    assert mock_client.upsert_embedding.call_count == 2


def test_embedding_computed_on_push_empty_commits_is_noop() -> None:
    """embed_push_commits with empty list makes no Qdrant calls."""
    from maestro.services.musehub_sync import embed_push_commits

    mock_client = MagicMock()
    with patch(
        "maestro.services.musehub_sync.MusehubQdrantClient",
        return_value=mock_client,
    ):
        embed_push_commits(
            commits=[],
            repo_id="repo-001",
            branch="main",
            author="composer@stori",
            is_public=True,
        )

    mock_client.ensure_collection.assert_not_called()
    mock_client.upsert_embedding.assert_not_called()


def test_embedding_computed_on_push_qdrant_error_does_not_raise() -> None:
    """embed_push_commits logs errors from Qdrant without propagating exceptions.

    The push response must not be blocked or failed by an embedding error.
    """
    from datetime import datetime, timezone
    from maestro.models.musehub import CommitInput
    from maestro.services.musehub_sync import embed_push_commits

    commits = [
        CommitInput(
            commit_id="fail-commit",
            parent_ids=[],
            message="C major 120 BPM",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            snapshot_id=None,
            author=None,
        )
    ]

    mock_client = MagicMock()
    mock_client.upsert_embedding.side_effect = RuntimeError("Qdrant unavailable")

    with patch(
        "maestro.services.musehub_sync.MusehubQdrantClient",
        return_value=mock_client,
    ):
        embed_push_commits(
            commits=commits,
            repo_id="repo-001",
            branch="main",
            author="composer@stori",
            is_public=False,
        )
    # No exception raised — test passes by reaching this line


# ---------------------------------------------------------------------------
# Private helper unit tests
# ---------------------------------------------------------------------------


def test_text_fingerprint_length() -> None:
    """Text fingerprint always produces 16 floats."""
    fp = _encode_text_fingerprint("anything goes")
    assert len(fp) == 16


def test_text_fingerprint_values_in_range() -> None:
    """All fingerprint values are in [0, 1]."""
    fp = _encode_text_fingerprint("jazz composition in C major")
    assert all(0.0 <= v <= 1.0 for v in fp)


def test_l2_normalise_unit_vector() -> None:
    """l2_normalise produces a unit vector."""
    v = [3.0, 4.0]
    result = _l2_normalise(v)
    assert abs(math.sqrt(sum(x * x for x in result)) - 1.0) < 1e-9


def test_l2_normalise_zero_vector_unchanged() -> None:
    """l2_normalise returns zero vector unchanged (no divide-by-zero)."""
    v = [0.0] * 10
    result = _l2_normalise(v)
    assert result == v
