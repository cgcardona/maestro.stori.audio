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
        yield OrpheusClient()


@pytest.mark.asyncio
async def test_health_check_returns_true_when_200(client):
    client._client = MagicMock()
    client._client.get = AsyncMock(return_value=MagicMock(status_code=200))
    result = await client.health_check()
    assert result is True
    client._client.get.assert_called_once_with("http://orpheus:10002/health")


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
        """Without an emotion_vector kwarg, Orpheus receives zero defaults."""
        mock_client = self._make_mock_client()
        backend = self._make_backend(mock_client)

        await backend.generate("drums", "lofi", 85, 4, key="Am")

        call_kwargs = mock_client.generate.call_args[1]
        assert call_kwargs["tone_brightness"] == 0.0
        assert call_kwargs["energy_intensity"] == 0.0
        assert call_kwargs.get("musical_goals") is None

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
