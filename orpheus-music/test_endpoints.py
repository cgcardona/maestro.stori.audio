"""
Tests for Orpheus HTTP endpoint contracts.

Validates response shapes, status codes, and API behavior for all endpoints
beyond the basic /generate flow (already tested in test_job_queue.py).
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

import music_service
from music_service import (
    app,
    _result_cache,
    JobQueue,
    GenerateRequest,
    GenerateResponse,
    cache_result,
    get_cache_key,
    CacheEntry,
)


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """ASGI test client with a mocked job queue."""
    queue = JobQueue(max_queue=16, max_workers=1)
    await queue.start()
    music_service._job_queue = queue
    try:
        with patch("music_service._do_generate", new_callable=AsyncMock, return_value=GenerateResponse(
            success=True, tool_calls=[], metadata={"mocked": True},
        )):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                yield c
    finally:
        await queue.shutdown()
        music_service._job_queue = None


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    """Clear caches before each test."""
    _result_cache.clear()


# =============================================================================
# /health
# =============================================================================


class TestHealthEndpoint:
    """Contract tests for /health."""

    @pytest.mark.asyncio
    async def test_health_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_response_shape(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "orpheus-music"


# =============================================================================
# /diagnostics
# =============================================================================


@pytest.mark.skip(reason="requires live HF Gradio Space (paused in CI)")
class TestDiagnosticsEndpoint:
    """Contract tests for /diagnostics.

    Skipped unless the HF Gradio Space is running — the endpoint attempts
    a real Gradio client connection on each call.
    """

    @pytest.mark.asyncio
    async def test_diagnostics_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/diagnostics")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_diagnostics_response_shape(self, client: AsyncClient) -> None:
        data = (await client.get("/diagnostics")).json()
        assert "service" in data
        assert data["service"] == "orpheus-music"
        assert "space_id" in data
        assert "gradio_client" in data
        assert "hf_space" in data
        assert "active_generations" in data
        assert "queue_depth" in data


# =============================================================================
# /cache/stats
# =============================================================================


class TestCacheStatsEndpoint:
    """Contract tests for /cache/stats."""

    @pytest.mark.asyncio
    async def test_cache_stats_empty(self, client: AsyncClient) -> None:
        data = (await client.get("/cache/stats")).json()
        assert data["result_cache_size"] == 0
        assert data["result_cache_max"] > 0
        assert "utilization" in data
        assert "policy_version" in data

    @pytest.mark.asyncio
    async def test_cache_stats_with_entries(self, client: AsyncClient) -> None:
        cache_result("key1", {"success": True, "tool_calls": []})
        cache_result("key2", {"success": True, "tool_calls": []})
        data = (await client.get("/cache/stats")).json()
        assert data["result_cache_size"] == 2
        assert data["total_hits"] == 0


# =============================================================================
# /cache/clear
# =============================================================================


class TestCacheClearEndpoint:
    """Contract tests for /cache/clear."""

    @pytest.mark.asyncio
    async def test_clear_empties_caches(self, client: AsyncClient) -> None:
        cache_result("key1", {"success": True, "tool_calls": []})
        assert len(_result_cache) == 1

        resp = await client.delete("/cache/clear")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert len(_result_cache) == 0


# =============================================================================
# /quality/evaluate
# =============================================================================


class TestQualityEvaluateEndpoint:
    """Contract tests for /quality/evaluate."""

    @pytest.mark.asyncio
    async def test_evaluate_returns_metrics(self, client: AsyncClient) -> None:
        resp = await client.post("/quality/evaluate", json={
            "tool_calls": [
                {
                    "tool": "addNotes",
                    "params": {
                        "notes": [
                            {"pitch": 60, "start_beat": 0.0, "duration_beats": 0.5, "velocity": 80},
                            {"pitch": 64, "start_beat": 1.0, "duration_beats": 0.5, "velocity": 90},
                        ],
                    },
                }
            ],
            "bars": 4,
            "tempo": 120,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "metrics" in data
        assert "note_count" in data

    @pytest.mark.asyncio
    async def test_evaluate_empty_notes(self, client: AsyncClient) -> None:
        resp = await client.post("/quality/evaluate", json={
            "tool_calls": [],
            "bars": 4,
            "tempo": 120,
        })
        assert resp.status_code == 200


# =============================================================================
# /queue/status
# =============================================================================


class TestQueueStatusEndpoint:
    """Contract tests for /queue/status."""

    @pytest.mark.asyncio
    async def test_queue_status_shape(self, client: AsyncClient) -> None:
        data = (await client.get("/queue/status")).json()
        assert "depth" in data
        assert "running" in data
        assert "max_concurrent" in data
        assert "max_queue" in data


# =============================================================================
# /generate contract
# =============================================================================


class TestGenerateContract:
    """API contract tests for /generate beyond basic queueing."""

    @pytest.mark.asyncio
    async def test_generate_accepts_full_intent(self, client: AsyncClient) -> None:
        """Full canonical intent payload is accepted without error."""
        with (
            patch("music_service.get_cached_result", return_value=None),
            patch("music_service.fuzzy_cache_lookup", return_value=None),
        ):
            resp = await client.post("/generate", json={
                "genre": "trap",
                "tempo": 140,
                "instruments": ["drums", "bass", "synth lead"],
                "bars": 8,
                "key": "Dm",
                "emotion_vector": {
                    "energy": 0.8,
                    "valence": -0.7,
                    "tension": 0.6,
                    "intimacy": 0.35,
                    "motion": 0.7,
                },
                "intent_goals": [
                    {"name": "dark", "weight": 1.0},
                    {"name": "energetic", "weight": 0.8},
                ],
                "quality_preset": "quality",
                "trace_id": "test-trace-001",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("queued", "complete")

    @pytest.mark.asyncio
    async def test_generate_minimal_payload(self, client: AsyncClient) -> None:
        """Minimal payload (just genre) is accepted with defaults."""
        with (
            patch("music_service.get_cached_result", return_value=None),
            patch("music_service.fuzzy_cache_lookup", return_value=None),
        ):
            resp = await client.post("/generate", json={"genre": "jazz"})
        assert resp.status_code == 200
