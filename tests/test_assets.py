"""
Tests for asset API (drum kits, soundfonts, bundle).

Ensures X-Device-ID is required and validated; rate limiting is applied.
"""
from __future__ import annotations

import pytest
import uuid
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.fixture
def asset_client() -> AsyncClient:
    """Client for asset endpoints (no auth required, but X-Device-ID required)."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_drum_kits_missing_x_device_id(asset_client: AsyncClient) -> None:

    """GET /api/v1/assets/drum-kits without X-Device-ID returns 400."""
    response = await asset_client.get("/api/v1/assets/drum-kits")
    assert response.status_code == 400
    data = response.json()
    assert "X-Device-ID" in data.get("detail", "")


@pytest.mark.asyncio
async def test_drum_kits_invalid_x_device_id(asset_client: AsyncClient) -> None:

    """GET /api/v1/assets/drum-kits with non-UUID X-Device-ID returns 400."""
    response = await asset_client.get(
        "/api/v1/assets/drum-kits",
        headers={"X-Device-ID": "not-a-uuid"},
    )
    assert response.status_code == 400
    data = response.json()
    assert "Invalid" in data.get("detail", "") or "X-Device-ID" in data.get("detail", "")


@pytest.mark.asyncio
async def test_drum_kits_valid_x_device_id(asset_client: AsyncClient) -> None:

    """GET /api/v1/assets/drum-kits with valid UUID passes dependency (503 if bucket not set)."""
    device_id = str(uuid.uuid4())
    response = await asset_client.get(
        "/api/v1/assets/drum-kits",
        headers={"X-Device-ID": device_id},
    )
    # 400 = bad device id, 429 = rate limit, 503 = not configured, 200 = success
    assert response.status_code in (200, 503)
    if response.status_code == 503:
        assert "Asset service not configured" in response.json().get("detail", "")


@pytest.mark.asyncio
async def test_soundfonts_missing_x_device_id(asset_client: AsyncClient) -> None:

    """GET /api/v1/assets/soundfonts without X-Device-ID returns 400."""
    response = await asset_client.get("/api/v1/assets/soundfonts")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_soundfont_download_url_missing_x_device_id(asset_client: AsyncClient) -> None:

    """GET download-url without X-Device-ID returns 400."""
    response = await asset_client.get("/api/v1/assets/soundfonts/some-id/download-url")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_bundle_download_url_valid_uuid(asset_client: AsyncClient) -> None:

    """GET bundle download-url with valid X-Device-ID passes (503 or 200)."""
    response = await asset_client.get(
        "/api/v1/assets/bundle/download-url",
        headers={"X-Device-ID": str(uuid.uuid4())},
    )
    assert response.status_code in (200, 404, 503)


@pytest.mark.asyncio
async def test_asset_endpoint_returns_429_when_rate_limited(asset_client: AsyncClient) -> None:

    """When rate limit is exceeded, asset endpoint returns 429."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from slowapi.errors import RateLimitExceeded

    # RateLimitExceeded(limit) expects a Limit-like object with .error_message and .limit
    fake_limit = SimpleNamespace(error_message="1 per minute", limit="1/minute")
    # Patch the service call used by the route so the handler raises and app returns 429
    with patch(
        "app.services.assets.list_drum_kits",
        side_effect=RateLimitExceeded(fake_limit),  # type: ignore[arg-type]  # duck-typed fake; SimpleNamespace matches Limit protocol
    ):
        response = await asset_client.get(
            "/api/v1/assets/drum-kits",
            headers={"X-Device-ID": str(uuid.uuid4())},
        )
    assert response.status_code == 429
