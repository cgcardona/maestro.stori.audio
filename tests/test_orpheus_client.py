"""Tests for app.services.orpheus.OrpheusClient (mocked HTTP)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.orpheus import OrpheusClient


@pytest.fixture
def client():
    with patch("app.services.orpheus.settings") as m:
        m.orpheus_base_url = "http://orpheus:10002"
        m.orpheus_timeout = 30
        m.hf_api_key = None
        m.orpheus_max_concurrent = 2
        yield OrpheusClient()


@pytest.mark.asyncio
async def test_health_check_returns_true_when_200(client):
    import httpx
    client._client = MagicMock()
    client._client.get = AsyncMock(return_value=MagicMock(status_code=200))
    result = await client.health_check()
    assert result is True
    # health_check uses a short probe timeout, not the default generation timeout
    client._client.get.assert_called_once_with(
        "http://orpheus:10002/health",
        timeout=httpx.Timeout(connect=3.0, read=3.0, write=3.0, pool=3.0),
    )


@pytest.mark.asyncio
async def test_health_check_returns_false_when_not_200(client):
    client._client = MagicMock()
    client._client.get = AsyncMock(return_value=MagicMock(status_code=503))
    result = await client.health_check()
    assert result is False


@pytest.mark.asyncio
async def test_health_check_returns_false_on_exception(client):
    client._client = MagicMock()
    client._client.get = AsyncMock(side_effect=Exception("connection refused"))
    result = await client.health_check()
    assert result is False


@pytest.mark.asyncio
async def test_generate_success_returns_notes(client):
    client._client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"success": True, "notes": [{"pitch": 60, "start": 0}], "tool_calls": []}
    mock_resp.raise_for_status = MagicMock()
    client._client.post = AsyncMock(return_value=mock_resp)
    result = await client.generate(genre="boom_bap", tempo=90, bars=4)
    assert result["success"] is True
    assert len(result["notes"]) == 1
    assert result["notes"][0]["pitch"] == 60


@pytest.mark.asyncio
async def test_generate_default_instruments(client):
    client._client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"success": True, "notes": [], "tool_calls": []}
    mock_resp.raise_for_status = MagicMock()
    client._client.post = AsyncMock(return_value=mock_resp)
    await client.generate(genre="lofi", tempo=85)
    call_args = client._client.post.call_args
    payload = call_args[1]["json"]
    assert payload["instruments"] == ["drums", "bass"]
    assert payload["genre"] == "lofi"
    assert payload["tempo"] == 85


@pytest.mark.asyncio
async def test_generate_includes_key_when_provided(client):
    client._client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"success": True, "notes": [], "tool_calls": []}
    mock_resp.raise_for_status = MagicMock()
    client._client.post = AsyncMock(return_value=mock_resp)
    await client.generate(genre="jazz", tempo=120, key="Cm")
    payload = client._client.post.call_args[1]["json"]
    assert payload["key"] == "Cm"


@pytest.mark.asyncio
async def test_generate_http_error_returns_error_dict(client):
    import httpx
    client._client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal error"
    client._client.post = AsyncMock(side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=mock_resp))
    result = await client.generate(genre="x", tempo=90)
    assert result["success"] is False
    assert "error" in result
    assert "500" in result["error"]


@pytest.mark.asyncio
async def test_generate_connect_error_returns_service_not_available(client):
    import httpx
    client._client = MagicMock()
    client._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    result = await client.generate(genre="x", tempo=90)
    assert result["success"] is False
    assert result.get("error") == "Orpheus service not available" or "not available" in result.get("error", "")


@pytest.mark.asyncio
async def test_close_clears_client(client):
    client._client = MagicMock()
    client._client.aclose = AsyncMock()
    await client.close()
    assert client._client is None


# =============================================================================
# OrpheusClient singleton and warmup
# =============================================================================

def test_get_orpheus_client_returns_singleton():
    """get_orpheus_client() returns the same instance on repeated calls (sync — no loop needed)."""
    import app.services.orpheus as orpheus_module
    from app.services.orpheus import get_orpheus_client
    orpheus_module._shared_client = None  # reset
    c1 = get_orpheus_client()
    c2 = get_orpheus_client()
    assert c1 is c2
    orpheus_module._shared_client = None  # cleanup without awaiting close


def test_close_orpheus_client_resets_singleton():
    """close_orpheus_client() clears the singleton so the next call makes a fresh one."""
    import app.services.orpheus as orpheus_module
    from app.services.orpheus import get_orpheus_client
    orpheus_module._shared_client = None  # start clean
    c1 = get_orpheus_client()
    # Directly reset the module-level variable (avoids async close complications)
    orpheus_module._shared_client = None
    c2 = get_orpheus_client()
    assert c1 is not c2
    orpheus_module._shared_client = None  # cleanup


@pytest.mark.asyncio
async def test_warmup_succeeds_when_healthy():
    """warmup() logs success when health_check returns True."""
    from app.services.orpheus import get_orpheus_client, close_orpheus_client
    await close_orpheus_client()
    c = get_orpheus_client()
    mock_inner = MagicMock()
    mock_inner.get = AsyncMock(return_value=MagicMock(status_code=200))
    mock_inner.aclose = AsyncMock()
    c._client = mock_inner
    # Should not raise
    await c.warmup()
    await close_orpheus_client()


@pytest.mark.asyncio
async def test_warmup_tolerates_connection_failure():
    """warmup() does not raise even when Orpheus is unreachable."""
    from app.services.orpheus import get_orpheus_client, close_orpheus_client
    await close_orpheus_client()
    c = get_orpheus_client()
    import httpx
    mock_inner = MagicMock()
    mock_inner.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_inner.aclose = AsyncMock()
    c._client = mock_inner
    # Must not raise
    await c.warmup()
    await close_orpheus_client()


def test_connection_limits_configured():
    """The OrpheusClient passes explicit connection limits when creating the AsyncClient."""
    import httpx
    import app.services.orpheus as orpheus_module
    from app.services.orpheus import get_orpheus_client, close_orpheus_client

    created_kwargs: dict = {}

    original_init = httpx.AsyncClient.__init__

    def capturing_init(self, **kwargs):
        created_kwargs.update(kwargs)
        original_init(self, **kwargs)

    orpheus_module._shared_client = None  # force fresh
    with patch("app.services.orpheus.settings") as m:
        m.orpheus_base_url = "http://orpheus:10002"
        m.orpheus_timeout = 30
        m.hf_api_key = None
        m.orpheus_max_concurrent = 4
        orpheus_module._shared_client = None
        c = get_orpheus_client()
        with patch.object(httpx.AsyncClient, "__init__", capturing_init):
            c._client = None  # force re-creation through the property
            _ = c.client
        limits = created_kwargs.get("limits")
        assert limits is not None
        assert limits.max_connections == 20
        assert limits.max_keepalive_connections == 10


# =============================================================================
# OrpheusBackend — emotion vector mapping
# =============================================================================

class TestOrpheusBackendEmotionMapping:
    """Tests that OrpheusBackend correctly maps EmotionVector to OrpheusClient fields."""

    def _make_backend(self, mock_client):
        from app.services.backends.orpheus import OrpheusBackend
        import app.services.orpheus as orpheus_module
        orpheus_module._shared_client = mock_client
        backend = OrpheusBackend()
        return backend

    def _make_mock_client(self, notes=None):
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
    async def test_no_emotion_vector_uses_defaults(self):
        """Without an emotion_vector kwarg, tone values default to zero but
        heuristic-derived musical_goals may still be present."""
        mock_client = self._make_mock_client()
        backend = self._make_backend(mock_client)

        await backend.generate("drums", "lofi", 85, 4, key="Am")

        call_kwargs = mock_client.generate.call_args[1]
        assert call_kwargs["tone_brightness"] == 0.0
        assert call_kwargs["energy_intensity"] == 0.0
        assert call_kwargs["tone_warmth"] == 0.0
        assert call_kwargs["energy_excitement"] == 0.0

    @pytest.mark.asyncio
    async def test_dark_emotion_vector_sets_negative_brightness(self):
        """High negative valence → negative tone_brightness."""
        from app.core.emotion_vector import EmotionVector
        mock_client = self._make_mock_client()
        backend = self._make_backend(mock_client)

        ev = EmotionVector(energy=0.3, valence=-0.8, tension=0.5, intimacy=0.7, motion=0.2)
        await backend.generate("bass", "lofi", 85, 4, emotion_vector=ev)

        call_kwargs = mock_client.generate.call_args[1]
        assert call_kwargs["tone_brightness"] < 0
        assert "dark" in (call_kwargs.get("musical_goals") or [])

    @pytest.mark.asyncio
    async def test_euphoric_emotion_vector_sets_positive_values(self):
        """High energy/valence → positive brightness and intensity, energetic goal."""
        from app.core.emotion_vector import EmotionVector
        mock_client = self._make_mock_client()
        backend = self._make_backend(mock_client)

        ev = EmotionVector(energy=0.95, valence=0.9, motion=0.9, intimacy=0.2)
        await backend.generate("lead", "edm", 140, 4, emotion_vector=ev)

        call_kwargs = mock_client.generate.call_args[1]
        assert call_kwargs["tone_brightness"] > 0
        assert call_kwargs["energy_intensity"] > 0
        goals = call_kwargs.get("musical_goals") or []
        assert "energetic" in goals
        assert "bright" in goals
        assert "driving" in goals

    @pytest.mark.asyncio
    async def test_intimate_sparse_emotion_vector(self):
        """High intimacy + low motion → intimate and sustained goals."""
        from app.core.emotion_vector import EmotionVector
        mock_client = self._make_mock_client()
        backend = self._make_backend(mock_client)

        ev = EmotionVector(energy=0.2, valence=0.0, motion=0.15, intimacy=0.9)
        await backend.generate("piano", "ambient", 60, 4, emotion_vector=ev)

        call_kwargs = mock_client.generate.call_args[1]
        goals = call_kwargs.get("musical_goals") or []
        assert "intimate" in goals
        assert "sustained" in goals

    @pytest.mark.asyncio
    async def test_quality_preset_forwarded(self):
        """quality_preset kwarg reaches OrpheusClient.generate."""
        from app.core.emotion_vector import EmotionVector
        mock_client = self._make_mock_client()
        backend = self._make_backend(mock_client)

        await backend.generate("drums", "boom_bap", 90, 4, quality_preset="fast")

        call_kwargs = mock_client.generate.call_args[1]
        assert call_kwargs["quality_preset"] == "fast"


# =============================================================================
# OrpheusBackend — note key normalization (snake_case → camelCase)
# =============================================================================

class TestOrpheusNoteNormalization:
    """Notes from Orpheus may use snake_case keys; backend must normalize to camelCase."""

    def _make_backend(self, mock_client):
        from app.services.backends.orpheus import OrpheusBackend
        import app.services.orpheus as orpheus_module
        orpheus_module._shared_client = mock_client
        return OrpheusBackend()

    @pytest.mark.asyncio
    async def test_snake_case_keys_normalized_to_camel(self):
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
    async def test_camel_case_keys_pass_through(self):
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
# OrpheusBackend — beat rescaling
# =============================================================================

class TestOrpheusBeatRescaling:
    """Notes compressed into a short window must be rescaled to the target bars."""

    def _make_backend(self, mock_client):
        from app.services.backends.orpheus import OrpheusBackend
        import app.services.orpheus as orpheus_module
        orpheus_module._shared_client = mock_client
        return OrpheusBackend()

    @pytest.mark.asyncio
    async def test_compressed_notes_rescaled_to_target(self):
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
    async def test_full_range_notes_not_rescaled(self):
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
    async def test_cc_events_rescaled_with_notes(self):
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
# GPU cold-start retry logic (regression: orpheus previously had no retry)
# =============================================================================

class TestGpuColdStartRetry:
    """OrpheusClient.generate retries up to 3× on Gradio GPU cold-start errors."""

    @pytest.fixture
    def client(self):
        with patch("app.services.orpheus.settings") as m:
            m.orpheus_base_url = "http://orpheus:10002"
            m.orpheus_timeout = 30
            m.hf_api_key = None
            m.orpheus_max_concurrent = 2
            yield OrpheusClient()

    @pytest.mark.asyncio
    async def test_gpu_error_triggers_retry_and_succeeds_on_second_attempt(self, client):
        """When first attempt returns GPU cold-start error, second attempt succeeds."""
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {
            "success": True,
            "notes": [{"pitch": 60, "startBeat": 0, "durationBeats": 1, "velocity": 80}],
            "tool_calls": [
                {"tool": "addNotes", "params": {"notes": [{"pitch": 60, "startBeat": 0, "durationBeats": 1, "velocity": 80}]}}
            ],
            "metadata": {},
        }
        gpu_resp = MagicMock()
        gpu_resp.raise_for_status = MagicMock()
        gpu_resp.json.return_value = {
            "success": False,
            "error": "No GPU was available after 60s. Retry later",
        }

        client._client = MagicMock()
        client._client.post = AsyncMock(side_effect=[gpu_resp, ok_resp])

        with patch("app.services.orpheus.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.generate(genre="reggaeton", tempo=96, bars=24)

        assert result["success"] is True
        assert mock_sleep.await_count == 1
        assert mock_sleep.call_args[0][0] == 2  # first retry delay is 2s

    @pytest.mark.asyncio
    async def test_gpu_error_all_retries_exhausted_returns_structured_error(self, client):
        """After 3 GPU cold-start failures the error is gpu_unavailable (not a crash)."""
        gpu_resp = MagicMock()
        gpu_resp.raise_for_status = MagicMock()
        gpu_resp.json.return_value = {
            "success": False,
            "error": "No GPU was available after 60s. Retry later",
        }

        client._client = MagicMock()
        client._client.post = AsyncMock(return_value=gpu_resp)

        with patch("app.services.orpheus.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.generate(genre="trap", tempo=140, bars=8)

        assert result["success"] is False
        assert result.get("error") == "gpu_unavailable"
        assert "GPU unavailable" in result.get("message", "")
        assert mock_sleep.await_count == 3  # delays before attempts 2, 3, and 4 (not after final)

    @pytest.mark.asyncio
    async def test_gpu_error_in_http_body_also_retries(self, client):
        """GPU cold-start text in an HTTP error body also triggers retry."""
        import httpx

        gpu_http_resp = MagicMock()
        gpu_http_resp.status_code = 503
        gpu_http_resp.text = "No GPU was available after 60s. Retry later"

        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"success": True, "notes": [], "tool_calls": [], "metadata": {}}

        client._client = MagicMock()
        client._client.post = AsyncMock(side_effect=[
            httpx.HTTPStatusError("503", request=MagicMock(), response=gpu_http_resp),
            ok_resp,
        ])

        with patch("app.services.orpheus.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.generate(genre="house", tempo=128, bars=4)

        assert result["success"] is True
        assert mock_sleep.await_count == 1

    @pytest.mark.asyncio
    async def test_non_gpu_error_does_not_retry(self, client):
        """Non-GPU failures (e.g. auth error) return immediately without retrying."""
        error_resp = MagicMock()
        error_resp.raise_for_status = MagicMock()
        error_resp.json.return_value = {
            "success": False,
            "error": "Invalid API key",
        }

        client._client = MagicMock()
        client._client.post = AsyncMock(return_value=error_resp)

        with patch("app.services.orpheus.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.generate(genre="jazz", tempo=120, bars=4)

        assert result["success"] is False
        assert result.get("error") == "Invalid API key"
        assert mock_sleep.await_count == 0  # no retry for non-GPU errors

    @pytest.mark.asyncio
    async def test_retry_count_in_metadata_on_success(self, client):
        """Successful result after a retry records retry_count in metadata."""
        gpu_resp = MagicMock()
        gpu_resp.raise_for_status = MagicMock()
        gpu_resp.json.return_value = {"success": False, "error": "No GPU was available after 60s."}

        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {
            "success": True,
            "notes": [],
            "tool_calls": [],
            "metadata": {"source": "orpheus"},
        }

        client._client = MagicMock()
        client._client.post = AsyncMock(side_effect=[gpu_resp, ok_resp])

        with patch("app.services.orpheus.asyncio.sleep", new_callable=AsyncMock):
            result = await client.generate(genre="lofi", tempo=85, bars=4)

        assert result["success"] is True
        assert result["metadata"]["retry_count"] == 1  # succeeded on attempt index 1


# ---------------------------------------------------------------------------
# Semaphore tests
# ---------------------------------------------------------------------------


class TestSemaphore:
    """Tests for the GPU concurrency semaphore in OrpheusClient."""

    def test_semaphore_configurable(self):
        """max_concurrent param controls semaphore capacity."""
        with patch("app.services.orpheus.settings") as m:
            m.orpheus_base_url = "http://orpheus:10002"
            m.orpheus_timeout = 30
            m.hf_api_key = None
            m.orpheus_max_concurrent = 5
            c = OrpheusClient()
            assert c._max_concurrent == 5
            assert c._semaphore._value == 5

    def test_semaphore_explicit_override(self):
        """Explicit max_concurrent kwarg overrides the config value."""
        with patch("app.services.orpheus.settings") as m:
            m.orpheus_base_url = "http://orpheus:10002"
            m.orpheus_timeout = 30
            m.hf_api_key = None
            m.orpheus_max_concurrent = 2
            c = OrpheusClient(max_concurrent=7)
            assert c._max_concurrent == 7
            assert c._semaphore._value == 7

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent_calls(self):
        """Only max_concurrent generate() calls run simultaneously."""
        import asyncio

        max_concurrent = 2
        with patch("app.services.orpheus.settings") as m:
            m.orpheus_base_url = "http://orpheus:10002"
            m.orpheus_timeout = 30
            m.hf_api_key = None
            m.orpheus_max_concurrent = max_concurrent
            c = OrpheusClient()

        peak = 0
        active = 0
        gate = asyncio.Event()

        async def slow_post(*args, **kwargs):
            nonlocal peak, active
            active += 1
            if active > peak:
                peak = active
            await gate.wait()
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"success": True, "notes": [], "metadata": {}}
            active -= 1
            return resp

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
    async def test_semaphore_releases_on_error(self):
        """Semaphore slot is released even when generate() hits an error."""
        with patch("app.services.orpheus.settings") as m:
            m.orpheus_base_url = "http://orpheus:10002"
            m.orpheus_timeout = 30
            m.hf_api_key = None
            m.orpheus_max_concurrent = 1
            c = OrpheusClient()

        c._client = MagicMock()
        c._client.post = AsyncMock(side_effect=Exception("boom"))

        result = await c.generate(genre="jazz", tempo=100, bars=4)
        assert result["success"] is False
        assert c._semaphore._value == 1, "Semaphore must be released after error"
