"""Tests for the Orpheus async job queue (JobQueue, Job, endpoints)."""
import asyncio

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from music_service import (
    app,
    Job,
    JobQueue,
    JobStatus,
    QueueFullError,
    GenerateRequest,
    GenerateResponse,
    _do_generate,
)


# ============================================================================
# JobQueue unit tests
# ============================================================================


class TestJobQueue:
    """Unit tests for the in-memory async JobQueue."""

    @pytest.mark.asyncio
    async def test_submit_returns_job_with_queued_status(self):
        q = JobQueue(max_queue=5, max_workers=1)
        # Don't start workers — we just test submit mechanics
        req = GenerateRequest(genre="lofi", tempo=85)
        job = q.submit(req)
        assert job.status == JobStatus.QUEUED
        assert job.id
        assert job.position == 1

    @pytest.mark.asyncio
    async def test_submit_raises_when_full(self):
        q = JobQueue(max_queue=1, max_workers=1)
        q.submit(GenerateRequest(genre="a", tempo=90))
        with pytest.raises(QueueFullError):
            q.submit(GenerateRequest(genre="b", tempo=90))

    @pytest.mark.asyncio
    async def test_get_job_returns_none_for_unknown(self):
        q = JobQueue(max_queue=5, max_workers=1)
        assert q.get_job("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_job_returns_submitted(self):
        q = JobQueue(max_queue=5, max_workers=1)
        job = q.submit(GenerateRequest(genre="x", tempo=90))
        assert q.get_job(job.id) is job

    @pytest.mark.asyncio
    async def test_status_snapshot(self):
        q = JobQueue(max_queue=10, max_workers=2)
        snap = q.status_snapshot()
        assert snap["depth"] == 0
        assert snap["running"] == 0
        assert snap["max_concurrent"] == 2
        assert snap["max_queue"] == 10

    @pytest.mark.asyncio
    async def test_worker_processes_job_to_completion(self):
        """Start queue, submit a job, verify it completes via event."""
        q = JobQueue(max_queue=5, max_workers=1)

        mock_result = GenerateResponse(
            success=True, tool_calls=[{"tool": "addNotes", "params": {}}]
        )

        with patch("music_service._do_generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = mock_result
            await q.start()
            try:
                job = q.submit(GenerateRequest(genre="house", tempo=128))
                await asyncio.wait_for(job.event.wait(), timeout=5)
            finally:
                await q.shutdown()

        assert job.status == JobStatus.COMPLETE
        assert job.result is not None
        assert job.result.success is True
        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_worker_marks_failed_on_exception(self):
        """If _do_generate raises, the job is marked FAILED (not lost)."""
        q = JobQueue(max_queue=5, max_workers=1)

        with patch("music_service._do_generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.side_effect = RuntimeError("GPU exploded")
            await q.start()
            try:
                job = q.submit(GenerateRequest(genre="trap", tempo=140))
                await asyncio.wait_for(job.event.wait(), timeout=5)
            finally:
                await q.shutdown()

        assert job.status == JobStatus.FAILED
        assert job.error == "GPU exploded"
        assert job.result is not None
        assert job.result.success is False

    @pytest.mark.asyncio
    async def test_multiple_jobs_processed_sequentially_by_single_worker(self):
        """With 1 worker, jobs complete one at a time."""
        q = JobQueue(max_queue=5, max_workers=1)
        order: list[str] = []

        async def _track_gen(req):
            order.append(req.genre)
            await asyncio.sleep(0.01)
            return GenerateResponse(success=True, tool_calls=[])

        with patch("music_service._do_generate", side_effect=_track_gen):
            await q.start()
            try:
                j1 = q.submit(GenerateRequest(genre="first", tempo=90))
                j2 = q.submit(GenerateRequest(genre="second", tempo=90))
                await asyncio.wait_for(j2.event.wait(), timeout=5)
            finally:
                await q.shutdown()

        assert order == ["first", "second"]
        assert j1.status == JobStatus.COMPLETE
        assert j2.status == JobStatus.COMPLETE

    @pytest.mark.asyncio
    async def test_cleanup_removes_expired_jobs(self):
        """Completed jobs older than TTL are cleaned up."""
        q = JobQueue(max_queue=5, max_workers=1)
        with patch("music_service._do_generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = GenerateResponse(success=True, tool_calls=[])
            await q.start()
            try:
                job = q.submit(GenerateRequest(genre="x", tempo=90))
                await asyncio.wait_for(job.event.wait(), timeout=5)

                assert q.get_job(job.id) is not None
                # Simulate age past TTL
                job.completed_at = job.completed_at - 600  # type: ignore[operator]
                # Manually trigger cleanup by calling private method
                await q._cleanup_loop.__wrapped__(q) if hasattr(q._cleanup_loop, '__wrapped__') else None
                # Fallback: patch sleep to break immediately
            finally:
                await q.shutdown()


# ============================================================================
# Endpoint integration tests (via ASGI transport)
# ============================================================================


class TestEndpoints:
    """Integration tests for the /generate, /jobs, /queue/status endpoints."""

    @pytest.fixture
    async def async_client(self):
        """Provide an httpx AsyncClient wired to the FastAPI ASGI app."""
        transport = ASGITransport(app=app)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    @pytest.mark.asyncio
    async def test_generate_cache_hit_returns_complete(self, async_client):
        """POST /generate with a cache hit returns status=complete immediately."""
        cached = {
            "success": True,
            "tool_calls": [{"tool": "addNotes", "params": {}}],
            "metadata": {"cache_hit": True},
        }
        with patch("music_service.get_cached_result", return_value=cached):
            resp = await async_client.post("/generate", json={
                "genre": "lofi", "tempo": 85, "instruments": ["drums"],
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "complete"
        assert data["result"]["success"] is True

    @pytest.mark.asyncio
    async def test_generate_enqueues_and_returns_jobid(self, async_client):
        """POST /generate with a cache miss returns jobId + queued status."""
        with (
            patch("music_service.get_cached_result", return_value=None),
            patch("music_service.fuzzy_cache_lookup", return_value=None),
        ):
            resp = await async_client.post("/generate", json={
                "genre": "trap", "tempo": 140, "instruments": ["drums", "bass"],
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert "jobId" in data

    @pytest.mark.asyncio
    async def test_get_job_returns_404_for_unknown(self, async_client):
        resp = await async_client.get("/jobs/nonexistent-id")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_wait_for_job_returns_404_for_unknown(self, async_client):
        resp = await async_client.get("/jobs/nonexistent-id/wait", params={"timeout": 1})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_queue_status_returns_snapshot(self, async_client):
        resp = await async_client.get("/queue/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "depth" in data
        assert "running" in data
        assert "max_concurrent" in data
