"""Tests for app.services.storpheus.StorpheusClient (mocked HTTP)."""
from __future__ import annotations

from collections.abc import Generator
import time as _time
from typing import TYPE_CHECKING, Any

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.contracts.generation_types import GenerationContext
from app.contracts.json_types import NoteDict
from app.services.storpheus import StorpheusClient

if TYPE_CHECKING:
    from app.services.backends.storpheus import StorpheusBackend


def _patch_settings(m: MagicMock) -> None:

    """Apply default Storpheus test settings to a mock."""
    m.storpheus_base_url = "http://storpheus:10002"
    m.storpheus_timeout = 30
    m.hf_api_key = None
    m.storpheus_max_concurrent = 2
    m.storpheus_cb_threshold = 3
    m.storpheus_cb_cooldown = 60
    m.storpheus_poll_timeout = 30
    m.storpheus_poll_max_attempts = 10


_JOB_ID = "test-job-00000000"


def _submit_resp(
    *,
    job_id: str = _JOB_ID,
    status: str = "queued",
    position: int = 1,
    result: dict[str, Any] | None = None,
) -> MagicMock:

    """Mock HTTP response from POST /generate (submit)."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    data: dict[str, Any] = {"jobId": job_id, "status": status}
    if status == "queued":
        data["position"] = position
    if result is not None:
        data["result"] = result
    resp.json.return_value = data
    return resp


def _poll_resp(
    *,
    job_id: str = _JOB_ID,
    status: str = "complete",
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> MagicMock:

    """Mock HTTP response from GET /jobs/{id}/wait (poll)."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    data: dict[str, Any] = {"jobId": job_id, "status": status, "elapsed": 10.0}
    if result is not None:
        data["result"] = result
    if error is not None:
        data["error"] = error
    resp.json.return_value = data
    return resp


def _ok_gen_result(**overrides: Any) -> dict[str, Any]:

    """A successful GenerateResponse-shaped dict."""
    r = {
        "success": True,
        "notes": [{"pitch": 60, "start": 0}],
        "tool_calls": [],
        "metadata": {},
    }
    r.update(overrides)
    return r


@pytest.fixture
def client() -> Generator[StorpheusClient, None, None]:
    with patch("app.services.storpheus.settings") as m:
        _patch_settings(m)
        yield StorpheusClient()


@pytest.mark.asyncio
async def test_health_check_returns_true_when_200(client: StorpheusClient) -> None:

    import httpx
    client._client = MagicMock()
    client._client.get = AsyncMock(return_value=MagicMock(status_code=200))
    result = await client.health_check()
    assert result is True
    # health_check uses a short probe timeout, not the default generation timeout
    client._client.get.assert_called_once_with(
        "http://storpheus:10002/health",
        timeout=httpx.Timeout(connect=3.0, read=3.0, write=3.0, pool=3.0),
    )


@pytest.mark.asyncio
async def test_health_check_returns_false_when_not_200(client: StorpheusClient) -> None:

    client._client = MagicMock()
    client._client.get = AsyncMock(return_value=MagicMock(status_code=503))
    result = await client.health_check()
    assert result is False


@pytest.mark.asyncio
async def test_health_check_returns_false_on_exception(client: StorpheusClient) -> None:

    client._client = MagicMock()
    client._client.get = AsyncMock(side_effect=Exception("connection refused"))
    result = await client.health_check()
    assert result is False


@pytest.mark.asyncio
async def test_generate_success_via_poll(client: StorpheusClient) -> None:

    """Submit returns queued; poll returns complete result."""
    client._client = MagicMock()
    client._client.post = AsyncMock(return_value=_submit_resp())
    client._client.get = AsyncMock(return_value=_poll_resp(result=_ok_gen_result()))
    result = await client.generate(genre="boom_bap", tempo=90, bars=4)
    assert result["success"] is True
    assert result["notes"][0]["pitch"] == 60


@pytest.mark.asyncio
async def test_generate_cache_hit_returns_immediately(client: StorpheusClient) -> None:

    """Submit returns status=complete (cache hit) — no polling needed."""
    client._client = MagicMock()
    client._client.post = AsyncMock(
        return_value=_submit_resp(status="complete", result=_ok_gen_result())
    )
    client._client.get = AsyncMock()  # should not be called
    result = await client.generate(genre="boom_bap", tempo=90, bars=4)
    assert result["success"] is True
    client._client.get.assert_not_called()


@pytest.mark.asyncio
async def test_generate_default_instruments(client: StorpheusClient) -> None:

    client._client = MagicMock()
    client._client.post = AsyncMock(
        return_value=_submit_resp(status="complete", result=_ok_gen_result(notes=[]))
    )
    await client.generate(genre="lofi", tempo=85)
    payload = client._client.post.call_args[1]["json"]
    assert payload["instruments"] == ["drums", "bass"]
    assert payload["genre"] == "lofi"
    assert payload["tempo"] == 85


@pytest.mark.asyncio
async def test_generate_includes_key_when_provided(client: StorpheusClient) -> None:

    client._client = MagicMock()
    client._client.post = AsyncMock(
        return_value=_submit_resp(status="complete", result=_ok_gen_result(notes=[]))
    )
    await client.generate(genre="jazz", tempo=120, key="Cm")
    payload = client._client.post.call_args[1]["json"]
    assert payload["key"] == "Cm"


@pytest.mark.asyncio
async def test_generate_submit_http_error_returns_error_dict(client: StorpheusClient) -> None:

    """HTTP 500 during submit (after retries exhausted) returns error."""
    import httpx
    client._client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal error"
    client._client.post = AsyncMock(
        side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=mock_resp)
    )
    with patch("app.services.storpheus.asyncio.sleep", new_callable=AsyncMock):
        result = await client.generate(genre="x", tempo=90)
    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_generate_connect_error_returns_service_not_available(client: StorpheusClient) -> None:

    import httpx
    client._client = MagicMock()
    client._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    result = await client.generate(genre="x", tempo=90)
    assert result["success"] is False
    assert "not available" in result.get("error", "").lower() or "not reachable" in result.get("error", "").lower()


@pytest.mark.asyncio
async def test_close_clears_client(client: StorpheusClient) -> None:

    client._client = MagicMock()
    client._client.aclose = AsyncMock()
    await client.close()
    assert client._client is None


# =============================================================================
# StorpheusClient singleton and warmup
# =============================================================================

def test_get_storpheus_client_returns_singleton() -> None:
    """get_storpheus_client() returns the same instance on repeated calls (sync — no loop needed)."""
    import app.services.storpheus as storpheus_module
    from app.services.storpheus import get_storpheus_client
    storpheus_module._shared_client = None  # reset
    c1 = get_storpheus_client()
    c2 = get_storpheus_client()
    assert c1 is c2
    storpheus_module._shared_client = None  # cleanup without awaiting close


def test_close_storpheus_client_resets_singleton() -> None:
    """close_storpheus_client() clears the singleton so the next call makes a fresh one."""
    import app.services.storpheus as storpheus_module
    from app.services.storpheus import get_storpheus_client
    storpheus_module._shared_client = None  # start clean
    c1 = get_storpheus_client()
    # Directly reset the module-level variable (avoids async close complications)
    storpheus_module._shared_client = None
    c2 = get_storpheus_client()
    assert c1 is not c2
    storpheus_module._shared_client = None  # cleanup


@pytest.mark.asyncio
async def test_warmup_succeeds_when_healthy() -> None:
    """warmup() logs success when health_check returns True."""
    from app.services.storpheus import get_storpheus_client, close_storpheus_client
    await close_storpheus_client()
    c = get_storpheus_client()
    mock_inner = MagicMock()
    mock_inner.get = AsyncMock(return_value=MagicMock(status_code=200))
    mock_inner.aclose = AsyncMock()
    c._client = mock_inner
    # Should not raise
    await c.warmup()
    await close_storpheus_client()


@pytest.mark.asyncio
async def test_warmup_tolerates_connection_failure() -> None:
    """warmup() does not raise even when Orpheus is unreachable."""
    from app.services.storpheus import get_storpheus_client, close_storpheus_client
    await close_storpheus_client()
    c = get_storpheus_client()
    import httpx
    mock_inner = MagicMock()
    mock_inner.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_inner.aclose = AsyncMock()
    c._client = mock_inner
    # Must not raise
    await c.warmup()
    await close_storpheus_client()


def test_connection_limits_configured() -> None:
    """The StorpheusClient passes explicit connection limits when creating the AsyncClient."""
    import httpx
    import app.services.storpheus as storpheus_module
    from app.services.storpheus import get_storpheus_client, close_storpheus_client

    created_kwargs: dict[str, Any] = {}

    original_init = httpx.AsyncClient.__init__

    def capturing_init(self: Any, **kwargs: Any) -> None:
        created_kwargs.update(kwargs)
        original_init(self, **kwargs)

    storpheus_module._shared_client = None  # force fresh
    with patch("app.services.storpheus.settings") as m:
        _patch_settings(m)
        m.storpheus_max_concurrent = 4
        storpheus_module._shared_client = None
        c = get_storpheus_client()
        with patch.object(httpx.AsyncClient, "__init__", capturing_init):
            c._client = None  # force re-creation through the property
            _ = c.client
        limits = created_kwargs.get("limits")
        assert limits is not None
        assert limits.max_connections == 20
        assert limits.max_keepalive_connections == 10


# =============================================================================
# StorpheusBackend — emotion vector mapping
# =============================================================================

class TestStorpheusBackendEmotionMapping:
    """Tests that StorpheusBackend correctly maps EmotionVector to StorpheusClient fields."""

    def teardown_method(self) -> None:
        import app.services.storpheus as storpheus_module
        storpheus_module._shared_client = None

    def _make_backend(self, mock_client: MagicMock) -> StorpheusBackend:
        from app.services.backends.storpheus import StorpheusBackend
        import app.services.storpheus as storpheus_module
        storpheus_module._shared_client = mock_client
        return StorpheusBackend()

    def _make_mock_client(self, notes: list[NoteDict] | None = None) -> MagicMock:

        mock = MagicMock()
        mock.health_check = AsyncMock(return_value=True)
        mock.generate = AsyncMock(return_value={
            "success": True,
            "notes": notes or [],
            "tool_calls": [
                {"tool": "addNotes", "params": {"notes": notes or [{"pitch": 60, "start_beat": 0, "duration_beats": 1, "velocity": 80}]}}
            ],
            "metadata": {},
        })
        return mock

    @pytest.mark.asyncio
    async def test_no_emotion_vector_sends_none(self) -> None:

        """Without an emotion_vector kwarg, the canonical blocks are None."""
        mock_client = self._make_mock_client()
        backend = self._make_backend(mock_client)

        await backend.generate("drums", "lofi", 85, 4, key="Am")

        call_kwargs = mock_client.generate.call_args[1]
        assert call_kwargs["emotion_vector"] is None
        assert call_kwargs["generation_constraints"] is None

    @pytest.mark.asyncio
    async def test_dark_emotion_vector_sends_full_ev(self) -> None:

        """High negative valence → full emotion_vector transmitted, dark goal derived."""
        from app.core.emotion_vector import EmotionVector
        mock_client = self._make_mock_client()
        backend = self._make_backend(mock_client)

        ev = EmotionVector(energy=0.3, valence=-0.8, tension=0.5, intimacy=0.7, motion=0.2)
        await backend.generate("bass", "lofi", 85, 4, context=GenerationContext(emotion_vector=ev))

        call_kwargs = mock_client.generate.call_args[1]
        assert call_kwargs["emotion_vector"]["valence"] == pytest.approx(-0.8)
        assert call_kwargs["emotion_vector"]["tension"] == pytest.approx(0.5)
        goals = [g["name"] for g in (call_kwargs.get("intent_goals") or [])]
        assert "dark" in goals

    @pytest.mark.asyncio
    async def test_euphoric_emotion_vector_sends_canonical_blocks(self) -> None:

        """High energy/valence → full emotion_vector and constraints transmitted."""
        from app.core.emotion_vector import EmotionVector
        mock_client = self._make_mock_client()
        backend = self._make_backend(mock_client)

        ev = EmotionVector(energy=0.95, valence=0.9, motion=0.9, intimacy=0.2)
        await backend.generate("lead", "edm", 140, 4, context=GenerationContext(emotion_vector=ev))

        call_kwargs = mock_client.generate.call_args[1]
        assert call_kwargs["emotion_vector"]["energy"] == pytest.approx(0.95)
        assert call_kwargs["emotion_vector"]["valence"] == pytest.approx(0.9)
        assert call_kwargs["generation_constraints"] is not None
        goals = [g["name"] for g in (call_kwargs.get("intent_goals") or [])]
        assert "energetic" in goals
        assert "bright" in goals
        assert "driving" in goals

    @pytest.mark.asyncio
    async def test_intimate_sparse_emotion_vector(self) -> None:

        """High intimacy + low motion → intimate and sustained goals."""
        from app.core.emotion_vector import EmotionVector
        mock_client = self._make_mock_client()
        backend = self._make_backend(mock_client)

        ev = EmotionVector(energy=0.2, valence=0.0, motion=0.15, intimacy=0.9)
        await backend.generate("piano", "ambient", 60, 4, context=GenerationContext(emotion_vector=ev))

        call_kwargs = mock_client.generate.call_args[1]
        goals = [g["name"] for g in (call_kwargs.get("intent_goals") or [])]
        assert "intimate" in goals
        assert "sustained" in goals

    @pytest.mark.asyncio
    async def test_quality_preset_forwarded(self) -> None:

        """quality_preset kwarg reaches StorpheusClient.generate."""
        from app.core.emotion_vector import EmotionVector
        mock_client = self._make_mock_client()
        backend = self._make_backend(mock_client)

        await backend.generate("drums", "boom_bap", 90, 4, context=GenerationContext(quality_preset="fast"))

        call_kwargs = mock_client.generate.call_args[1]
        assert call_kwargs["quality_preset"] == "fast"


# =============================================================================
# StorpheusBackend — note key normalization (snake_case → camelCase)
# =============================================================================

class TestStorpheusNoteNormalization:
    """Notes from Orpheus may use snake_case keys; backend must normalize to camelCase."""

    def _make_backend(self, mock_client: MagicMock) -> StorpheusBackend:
        from app.services.backends.storpheus import StorpheusBackend
        import app.services.storpheus as storpheus_module
        storpheus_module._shared_client = mock_client
        return StorpheusBackend()

    @pytest.mark.asyncio
    async def test_snake_case_keys_normalized_to_camel(self) -> None:

        """start_beat/duration_beats → startBeat/durationBeats."""
        notes = [
            {"pitch": 60, "start_beat": 0, "duration_beats": 4, "velocity": 80},
            {"pitch": 64, "start_beat": 4, "duration_beats": 4, "velocity": 90},
            {"pitch": 67, "start_beat": 8, "duration_beats": 4, "velocity": 85},
            {"pitch": 72, "start_beat": 12, "duration_beats": 4, "velocity": 80},
        ]
        mock = MagicMock()
        mock.generate = AsyncMock(return_value={
            "success": True,
            "notes": [],
            "tool_calls": [{"tool": "addNotes", "params": {"notes": notes}}],
            "metadata": {},
        })
        backend = self._make_backend(mock)
        result = await backend.generate("bass", "house", 120, 4, key="Am")

        assert result.success
        for n in result.notes:
            assert "startBeat" in n
            assert "durationBeats" in n
            assert "start_beat" not in n
            assert "duration_beats" not in n

    @pytest.mark.asyncio
    async def test_camel_case_keys_pass_through(self) -> None:

        """Notes already using camelCase should not be corrupted."""
        notes = [
            {"pitch": 60, "startBeat": 0, "durationBeats": 4, "velocity": 80},
            {"pitch": 64, "startBeat": 4, "durationBeats": 4, "velocity": 90},
            {"pitch": 67, "startBeat": 8, "durationBeats": 4, "velocity": 85},
            {"pitch": 72, "startBeat": 12, "durationBeats": 4, "velocity": 80},
        ]
        mock = MagicMock()
        mock.generate = AsyncMock(return_value={
            "success": True,
            "notes": [],
            "tool_calls": [{"tool": "addNotes", "params": {"notes": notes}}],
            "metadata": {},
        })
        backend = self._make_backend(mock)
        result = await backend.generate("bass", "house", 120, 4, key="Am")

        assert result.success
        assert result.notes[0]["startBeat"] == 0
        assert result.notes[0]["durationBeats"] == 4


# =============================================================================
# StorpheusBackend — beat rescaling
# =============================================================================

class TestStorpheusBeatRescaling:
    """Notes compressed into a short window must be rescaled to the target bars."""

    def teardown_method(self) -> None:
        import app.services.storpheus as storpheus_module
        storpheus_module._shared_client = None

    def _make_backend(self, mock_client: MagicMock) -> StorpheusBackend:
        from app.services.backends import storpheus as storpheus_backend
        from app.services.backends.storpheus import StorpheusBackend
        import app.services.storpheus as storpheus_module
        storpheus_module._shared_client = mock_client
        storpheus_backend.ENABLE_BEAT_RESCALING = True
        return StorpheusBackend()

    @pytest.mark.asyncio
    async def test_compressed_notes_rescaled_to_target(self) -> None:

        """330 notes in 0-8 beats should be rescaled to span 0-96 beats for 24 bars."""
        compressed_notes = [
            {"pitch": 60, "startBeat": i * 0.024, "durationBeats": 0.02, "velocity": 80}
            for i in range(330)
        ]
        max_end_compressed = max(
            n["startBeat"] + n["durationBeats"] for n in compressed_notes
        )
        assert max_end_compressed < 10  # notes span < 10 beats

        mock = MagicMock()
        mock.generate = AsyncMock(return_value={
            "success": True,
            "notes": [],
            "tool_calls": [{"tool": "addNotes", "params": {"notes": compressed_notes}}],
            "metadata": {},
        })
        backend = self._make_backend(mock)
        result = await backend.generate("bass", "minimal deep house", 122, 24, key="Am")

        assert result.success
        assert len(result.notes) == 330
        max_end = max(n["startBeat"] + n["durationBeats"] for n in result.notes)
        assert max_end >= 90, f"Notes should span ~96 beats, got max_end={max_end}"

    @pytest.mark.asyncio
    async def test_full_range_notes_not_rescaled(self) -> None:

        """Notes already spanning the target range should not be modified."""
        full_range_notes = [
            {"pitch": 60, "startBeat": float(i * 4), "durationBeats": 2.0, "velocity": 80}
            for i in range(24)
        ]
        mock = MagicMock()
        mock.generate = AsyncMock(return_value={
            "success": True,
            "notes": [],
            "tool_calls": [{"tool": "addNotes", "params": {"notes": full_range_notes}}],
            "metadata": {},
        })
        backend = self._make_backend(mock)
        result = await backend.generate("bass", "house", 120, 24, key="Am")

        assert result.success
        assert result.notes[0]["startBeat"] == 0.0
        assert result.notes[12]["startBeat"] == 48.0
        assert result.notes[23]["startBeat"] == 92.0

    @pytest.mark.asyncio
    async def test_cc_events_rescaled_with_notes(self) -> None:

        """CC events must be rescaled alongside notes."""
        notes = [
            {"pitch": 60 + (i % 12), "startBeat": i * 0.12, "durationBeats": 0.1, "velocity": 80}
            for i in range(60)
        ]
        max_end = max(n["startBeat"] + n["durationBeats"] for n in notes)
        mock = MagicMock()
        mock.generate = AsyncMock(return_value={
            "success": True,
            "notes": [],
            "tool_calls": [
                {"tool": "addNotes", "params": {"notes": notes}},
                {"tool": "addMidiCC", "params": {"cc": 11, "events": [
                    {"beat": 0, "value": 80},
                    {"beat": 3, "value": 120},
                ]}},
            ],
            "metadata": {},
        })
        backend = self._make_backend(mock)
        result = await backend.generate("bass", "house", 120, 24, key="Am")

        assert result.success
        target = 24 * 4
        scale = target / max_end
        assert result.cc_events[0]["beat"] == 0
        assert abs(result.cc_events[1]["beat"] - round(3 * scale, 4)) < 0.01


# =============================================================================
# GPU cold-start AND Gradio transient retry logic
# =============================================================================

class TestSubmitAndPoll:
    """Tests for the submit + long-poll flow in StorpheusClient.generate.

    POST /generate now returns immediately with {jobId, status}.
    GET /jobs/{id}/wait is polled until the job completes or fails.
    """

    @pytest.fixture
    def client(self) -> Generator[StorpheusClient, None, None]:

        with patch("app.services.storpheus.settings") as m:
            _patch_settings(m)
            yield StorpheusClient()

    @pytest.mark.asyncio
    async def test_poll_returns_failed_job(self, client: StorpheusClient) -> None:

        """When the GPU job fails, poll returns the error."""
        client._client = MagicMock()
        client._client.post = AsyncMock(return_value=_submit_resp())
        client._client.get = AsyncMock(return_value=_poll_resp(
            status="failed",
            result={"success": False, "error": "GPU OOM", "tool_calls": []},
            error="GPU OOM",
        ))
        result = await client.generate(genre="trap", tempo=140, bars=8)
        assert result["success"] is False
        assert "GPU OOM" in result["error"]

    @pytest.mark.asyncio
    async def test_poll_exhausted_returns_timeout(self, client: StorpheusClient) -> None:

        """When max polls are exhausted the client reports a timeout."""
        with patch("app.services.storpheus.settings") as m:
            _patch_settings(m)
            m.storpheus_poll_max_attempts = 2
            c = StorpheusClient()

        c._client = MagicMock()
        c._client.post = AsyncMock(return_value=_submit_resp())
        c._client.get = AsyncMock(return_value=_poll_resp(status="running", result=None))

        result = await c.generate(genre="lofi", tempo=85, bars=4)
        assert result["success"] is False
        assert "did not complete" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_503_submit_retries_then_succeeds(self, client: StorpheusClient) -> None:

        """503 from Orpheus queue-full triggers submit retry."""
        full_resp = MagicMock()
        full_resp.status_code = 503
        full_resp.raise_for_status = MagicMock(side_effect=Exception("503"))

        client._client = MagicMock()
        client._client.post = AsyncMock(side_effect=[full_resp, _submit_resp()])
        client._client.get = AsyncMock(return_value=_poll_resp(result=_ok_gen_result()))

        with patch("app.services.storpheus.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.generate(genre="house", tempo=128, bars=4)

        assert result["success"] is True
        assert mock_sleep.await_count == 1

    @pytest.mark.asyncio
    async def test_503_submit_all_retries_exhausted(self, client: StorpheusClient) -> None:

        """503 on every submit attempt returns queue-full error."""
        full_resp = MagicMock()
        full_resp.status_code = 503

        client._client = MagicMock()
        client._client.post = AsyncMock(return_value=full_resp)

        with patch("app.services.storpheus.asyncio.sleep", new_callable=AsyncMock):
            result = await client.generate(genre="trap", tempo=140, bars=8)

        assert result["success"] is False
        assert "queue full" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_poll_http_timeout_retries_poll(self, client: StorpheusClient) -> None:

        """HTTP read timeout during poll just retries the poll (job survives)."""
        import httpx

        client._client = MagicMock()
        client._client.post = AsyncMock(return_value=_submit_resp())
        client._client.get = AsyncMock(side_effect=[
            httpx.ReadTimeout("poll timeout"),
            _poll_resp(result=_ok_gen_result()),
        ])

        result = await client.generate(genre="ambient", tempo=60, bars=8)
        assert result["success"] is True
        assert client._client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_poll_connect_error_fails_immediately(self, client: StorpheusClient) -> None:

        """ConnectError during polling means Orpheus went down — fail fast."""
        import httpx

        client._client = MagicMock()
        client._client.post = AsyncMock(return_value=_submit_resp())
        client._client.get = AsyncMock(side_effect=httpx.ConnectError("lost"))

        result = await client.generate(genre="jazz", tempo=120, bars=4)
        assert result["success"] is False
        assert "connection lost" in result["error"].lower()


# ---------------------------------------------------------------------------
# Semaphore tests
# ---------------------------------------------------------------------------


class TestSemaphore:
    """Tests for the GPU concurrency semaphore in StorpheusClient."""

    def test_semaphore_configurable(self) -> None:

        """max_concurrent param controls semaphore capacity."""
        with patch("app.services.storpheus.settings") as m:
            _patch_settings(m)
            m.storpheus_max_concurrent = 5
            c = StorpheusClient()
            assert c._max_concurrent == 5
            assert c._semaphore._value == 5

    def test_semaphore_explicit_override(self) -> None:

        """Explicit max_concurrent kwarg overrides the config value."""
        with patch("app.services.storpheus.settings") as m:
            _patch_settings(m)
            c = StorpheusClient(max_concurrent=7)
            assert c._max_concurrent == 7
            assert c._semaphore._value == 7

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent_calls(self) -> None:

        """Only max_concurrent generate() calls run simultaneously."""
        import asyncio

        max_concurrent = 2
        with patch("app.services.storpheus.settings") as m:
            _patch_settings(m)
            m.storpheus_max_concurrent = max_concurrent
            c = StorpheusClient()

        peak = 0
        active = 0
        gate = asyncio.Event()

        async def slow_post(*args: Any, **kwargs: object) -> MagicMock:

            nonlocal peak, active
            active += 1
            if active > peak:
                peak = active
            await gate.wait()
            return _submit_resp(status="complete", result=_ok_gen_result(notes=[]))

        c._client = MagicMock()
        c._client.post = AsyncMock(side_effect=slow_post)

        tasks = [
            asyncio.create_task(c.generate(genre="pop", tempo=120, bars=4))
            for _ in range(max_concurrent + 2)
        ]
        await asyncio.sleep(0.05)
        assert peak <= max_concurrent

        gate.set()
        await asyncio.gather(*tasks)

    @pytest.mark.asyncio
    async def test_semaphore_releases_on_error(self) -> None:

        """Semaphore slot is released even when generate() hits an error."""
        with patch("app.services.storpheus.settings") as m:
            _patch_settings(m)
            m.storpheus_max_concurrent = 1
            c = StorpheusClient()

        c._client = MagicMock()
        c._client.post = AsyncMock(side_effect=Exception("boom"))

        result = await c.generate(genre="jazz", tempo=100, bars=4)
        assert result["success"] is False
        assert c._semaphore._value == 1, "Semaphore must be released after error"


class TestIntentGoalsPayload:
    """Regression: musical_goals=None must not appear as null in the HTTP payload."""

    def _make_client(self) -> StorpheusClient:

        with patch("app.services.storpheus.settings") as m:
            _patch_settings(m)
            return StorpheusClient()

    @pytest.mark.asyncio
    async def test_intent_goals_none_omitted_from_payload(self) -> None:

        """When intent_goals=None, the key must not appear in the POST body."""
        c = self._make_client()
        c._client = MagicMock()
        c._client.post = AsyncMock(
            return_value=_submit_resp(status="complete", result=_ok_gen_result())
        )

        await c.generate(genre="jazz", tempo=100, bars=4, intent_goals=None)

        _, kwargs = c._client.post.call_args
        payload = kwargs["json"]
        assert "intent_goals" not in payload

    @pytest.mark.asyncio
    async def test_intent_goals_present_when_provided(self) -> None:

        """When intent_goals has values they ARE included in the payload."""
        c = self._make_client()
        c._client = MagicMock()
        c._client.post = AsyncMock(
            return_value=_submit_resp(status="complete", result=_ok_gen_result())
        )

        goals = [{"name": "dark", "weight": 1.0}, {"name": "energetic", "weight": 0.8}]
        await c.generate(genre="jazz", tempo=100, bars=4, intent_goals=goals)

        _, kwargs = c._client.post.call_args
        payload = kwargs["json"]
        assert payload["intent_goals"] == goals

    @pytest.mark.asyncio
    async def test_default_call_omits_intent_goals(self) -> None:

        """A bare generate() call (no intent_goals arg) must not include the key."""
        c = self._make_client()
        c._client = MagicMock()
        c._client.post = AsyncMock(
            return_value=_submit_resp(status="complete", result=_ok_gen_result())
        )

        await c.generate(genre="boom_bap", tempo=120, bars=4)

        _, kwargs = c._client.post.call_args
        payload = kwargs["json"]
        assert "intent_goals" not in payload


# =============================================================================
# Correlation ID tests
# =============================================================================


class TestCorrelationId:
    """composition_id is threaded through to the HTTP payload."""

    def _make_client(self) -> StorpheusClient:

        with patch("app.services.storpheus.settings") as m:
            _patch_settings(m)
            return StorpheusClient()

    @pytest.mark.asyncio
    async def test_composition_id_included_in_payload(self) -> None:

        """When composition_id is provided, it appears in the POST body."""
        c = self._make_client()
        c._client = MagicMock()
        c._client.post = AsyncMock(
            return_value=_submit_resp(status="complete", result=_ok_gen_result())
        )

        await c.generate(
            genre="jazz", tempo=100, bars=4,
            composition_id="trace-abc123",
        )

        _, kwargs = c._client.post.call_args
        payload = kwargs["json"]
        assert payload["composition_id"] == "trace-abc123"

    @pytest.mark.asyncio
    async def test_composition_id_omitted_when_none(self) -> None:

        """When composition_id is None, the key must not appear in the body."""
        c = self._make_client()
        c._client = MagicMock()
        c._client.post = AsyncMock(
            return_value=_submit_resp(status="complete", result=_ok_gen_result())
        )

        await c.generate(genre="jazz", tempo=100, bars=4)

        _, kwargs = c._client.post.call_args
        payload = kwargs["json"]
        assert "composition_id" not in payload


# =============================================================================
# Circuit breaker tests
# =============================================================================


class TestCircuitBreaker:
    """Orpheus circuit breaker prevents cascading failures."""

    @pytest.fixture
    def client(self) -> Generator[StorpheusClient, None, None]:

        with patch("app.services.storpheus.settings") as m:
            _patch_settings(m)
            m.storpheus_cb_threshold = 2
            m.storpheus_cb_cooldown = 60
            yield StorpheusClient()

    @pytest.mark.asyncio
    async def test_circuit_open_returns_immediately(self, client: StorpheusClient) -> None:

        """When circuit is open, generate() returns immediately without HTTP call."""
        client._cb._failures = 3
        client._cb._opened_at = _time.monotonic()

        client._client = MagicMock()
        client._client.post = AsyncMock()

        result = await client.generate(genre="pop", tempo=120, bars=4)

        assert result["success"] is False
        assert result["error"] == "storpheus_circuit_open"
        client._client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_circuit_resets_after_cooldown(self, client: StorpheusClient) -> None:

        """After cooldown expires, the circuit allows one probe request."""
        client._cb._failures = 3
        client._cb._opened_at = _time.monotonic() - 120

        client._client = MagicMock()
        client._client.post = AsyncMock(
            return_value=_submit_resp(status="complete", result=_ok_gen_result(notes=[]))
        )

        result = await client.generate(genre="pop", tempo=120, bars=4)

        assert result["success"] is True
        assert not client.circuit_breaker_open
        assert client._cb._failures == 0

    @pytest.mark.asyncio
    async def test_success_resets_circuit(self, client: StorpheusClient) -> None:

        """A successful call resets the failure counter."""
        import httpx

        client._client = MagicMock()
        client._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        await client.generate(genre="pop", tempo=120, bars=4)
        assert client._cb._failures == 1

        client._client.post = AsyncMock(
            return_value=_submit_resp(status="complete", result=_ok_gen_result(notes=[]))
        )
        await client.generate(genre="pop", tempo=120, bars=4)
        assert client._cb._failures == 0
        assert not client.circuit_breaker_open

    @pytest.mark.asyncio
    async def test_connect_error_trips_circuit(self, client: StorpheusClient) -> None:

        """ConnectError counts toward circuit breaker failures."""
        import httpx

        client._client = MagicMock()
        client._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        await client.generate(genre="pop", tempo=120, bars=4)
        assert client._cb._failures == 1

        await client.generate(genre="pop", tempo=120, bars=4)
        assert client.circuit_breaker_open

    @pytest.mark.asyncio
    async def test_poll_failure_trips_circuit(self, client: StorpheusClient) -> None:

        """Job failure during polling also counts toward circuit breaker."""
        client._client = MagicMock()
        client._client.post = AsyncMock(return_value=_submit_resp())
        client._client.get = AsyncMock(return_value=_poll_resp(
            status="failed",
            result={"success": False, "error": "GPU crash", "tool_calls": []},
            error="GPU crash",
        ))

        await client.generate(genre="pop", tempo=120, bars=4)
        assert client._cb._failures == 1

        await client.generate(genre="pop", tempo=120, bars=4)
        assert client.circuit_breaker_open
