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
