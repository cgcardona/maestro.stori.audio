"""Integration tests for asset API routes (app/api/routes/assets.py).

Covers: list_drum_kits, list_soundfonts, get_drum_kit_download_url,
get_soundfont_download_url, get_bundle_download_url, require_device_id.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from datetime import datetime, timezone

DEVICE_ID = str(uuid.uuid4())
DEVICE_HEADERS = {"X-Device-ID": DEVICE_ID}


# ---------------------------------------------------------------------------
# Device ID validation
# ---------------------------------------------------------------------------


class TestDeviceIDValidation:

    @pytest.mark.anyio
    async def test_missing_device_id(self, client: AsyncClient) -> None:

        resp = await client.get("/api/v1/assets/drum-kits")
        assert resp.status_code == 400
        assert "X-Device-ID" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_invalid_device_id(self, client: AsyncClient) -> None:

        resp = await client.get(
            "/api/v1/assets/drum-kits",
            headers={"X-Device-ID": "not-a-uuid"},
        )
        assert resp.status_code == 400

    @pytest.mark.anyio
    async def test_empty_device_id(self, client: AsyncClient) -> None:

        resp = await client.get(
            "/api/v1/assets/drum-kits",
            headers={"X-Device-ID": ""},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# list drum kits
# ---------------------------------------------------------------------------


class TestListDrumKitsRoute:

    @pytest.mark.anyio
    @patch("app.api.routes.assets.settings")
    async def test_no_bucket_503(self, mock_settings: MagicMock, client: AsyncClient) -> None:
        mock_settings.aws_s3_asset_bucket = ""
        mock_settings.asset_rate_limit_per_ip = "100/minute"
        mock_settings.asset_rate_limit_per_device = "100/minute"
        resp = await client.get("/api/v1/assets/drum-kits", headers=DEVICE_HEADERS)
        assert resp.status_code == 503

    @pytest.mark.anyio
    @patch("app.api.routes.assets.asset_service.list_drum_kits")
    @patch("app.api.routes.assets.settings")
    async def test_happy_path(
        self, mock_settings: MagicMock, mock_list: MagicMock, client: AsyncClient
    ) -> None:
        mock_settings.aws_s3_asset_bucket = "bucket"
        mock_settings.asset_rate_limit_per_ip = "100/minute"
        mock_settings.asset_rate_limit_per_device = "100/minute"
        mock_list.return_value = [{"id": "tr909", "name": "TR-909"}]
        resp = await client.get("/api/v1/assets/drum-kits", headers=DEVICE_HEADERS)
        assert resp.status_code == 200
        assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# list soundfonts
# ---------------------------------------------------------------------------


class TestListSoundfontsRoute:

    @pytest.mark.anyio
    @patch("app.api.routes.assets.settings")
    async def test_no_bucket_503(self, mock_settings: MagicMock, client: AsyncClient) -> None:

        mock_settings.aws_s3_asset_bucket = ""
        mock_settings.asset_rate_limit_per_ip = "100/minute"
        mock_settings.asset_rate_limit_per_device = "100/minute"
        resp = await client.get("/api/v1/assets/soundfonts", headers=DEVICE_HEADERS)
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Drum kit download URL
# ---------------------------------------------------------------------------


class TestDrumKitDownloadURL:

    @pytest.mark.anyio
    @patch("app.api.routes.assets.settings")
    async def test_no_bucket_503(self, mock_settings: MagicMock, client: AsyncClient) -> None:

        mock_settings.aws_s3_asset_bucket = ""
        mock_settings.asset_rate_limit_per_ip = "100/minute"
        mock_settings.asset_rate_limit_per_device = "100/minute"
        resp = await client.get(
            "/api/v1/assets/drum-kits/tr909/download-url",
            headers=DEVICE_HEADERS,
        )
        assert resp.status_code == 503

    @pytest.mark.anyio
    @patch("app.api.routes.assets.asset_service.get_drum_kit_download_url")
    @patch("app.api.routes.assets.settings")
    async def test_happy_path(self, mock_settings: MagicMock, mock_url: MagicMock, client: AsyncClient) -> None:

        mock_settings.aws_s3_asset_bucket = "bucket"
        mock_settings.asset_rate_limit_per_ip = "100/minute"
        mock_settings.asset_rate_limit_per_device = "100/minute"
        mock_settings.presign_expiry_seconds = 3600
        mock_url.return_value = ("https://s3.example.com/tr909.zip", datetime.now(timezone.utc))
        resp = await client.get(
            "/api/v1/assets/drum-kits/tr909/download-url",
            headers=DEVICE_HEADERS,
        )
        assert resp.status_code == 200
        assert "url" in resp.json()

    @pytest.mark.anyio
    @patch("app.api.routes.assets.asset_service.get_drum_kit_download_url")
    @patch("app.api.routes.assets.settings")
    async def test_not_found(self, mock_settings: MagicMock, mock_url: MagicMock, client: AsyncClient) -> None:

        mock_settings.aws_s3_asset_bucket = "bucket"
        mock_settings.asset_rate_limit_per_ip = "100/minute"
        mock_settings.asset_rate_limit_per_device = "100/minute"
        mock_settings.presign_expiry_seconds = 3600
        mock_url.side_effect = KeyError("not found")
        resp = await client.get(
            "/api/v1/assets/drum-kits/nonexistent/download-url",
            headers=DEVICE_HEADERS,
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    @patch("app.api.routes.assets.asset_service.get_drum_kit_download_url")
    @patch("app.api.routes.assets.settings")
    async def test_s3_unavailable(self, mock_settings: MagicMock, mock_url: MagicMock, client: AsyncClient) -> None:

        from app.services.assets import AssetServiceUnavailableError
        mock_settings.aws_s3_asset_bucket = "bucket"
        mock_settings.asset_rate_limit_per_ip = "100/minute"
        mock_settings.asset_rate_limit_per_device = "100/minute"
        mock_settings.presign_expiry_seconds = 3600
        mock_url.side_effect = AssetServiceUnavailableError("no creds")
        resp = await client.get(
            "/api/v1/assets/drum-kits/tr909/download-url",
            headers=DEVICE_HEADERS,
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Soundfont download URL
# ---------------------------------------------------------------------------


class TestSoundfontDownloadURL:

    @pytest.mark.anyio
    @patch("app.api.routes.assets.asset_service.get_soundfont_download_url")
    @patch("app.api.routes.assets.settings")
    async def test_happy_path(self, mock_settings: MagicMock, mock_url: MagicMock, client: AsyncClient) -> None:

        mock_settings.aws_s3_asset_bucket = "bucket"
        mock_settings.asset_rate_limit_per_ip = "100/minute"
        mock_settings.asset_rate_limit_per_device = "100/minute"
        mock_settings.presign_expiry_seconds = 3600
        mock_url.return_value = ("https://s3.example.com/GM.sf2", datetime.now(timezone.utc))
        resp = await client.get(
            "/api/v1/assets/soundfonts/fluidr3_gm/download-url",
            headers=DEVICE_HEADERS,
        )
        assert resp.status_code == 200

    @pytest.mark.anyio
    @patch("app.api.routes.assets.asset_service.get_soundfont_download_url")
    @patch("app.api.routes.assets.settings")
    async def test_not_found(self, mock_settings: MagicMock, mock_url: MagicMock, client: AsyncClient) -> None:

        mock_settings.aws_s3_asset_bucket = "bucket"
        mock_settings.asset_rate_limit_per_ip = "100/minute"
        mock_settings.asset_rate_limit_per_device = "100/minute"
        mock_settings.presign_expiry_seconds = 3600
        mock_url.side_effect = KeyError("not found")
        resp = await client.get(
            "/api/v1/assets/soundfonts/unknown/download-url",
            headers=DEVICE_HEADERS,
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    @patch("app.api.routes.assets.asset_service.get_soundfont_download_url")
    @patch("app.api.routes.assets.settings")
    async def test_s3_unavailable(self, mock_settings: MagicMock, mock_url: MagicMock, client: AsyncClient) -> None:

        from app.services.assets import AssetServiceUnavailableError
        mock_settings.aws_s3_asset_bucket = "bucket"
        mock_settings.asset_rate_limit_per_ip = "100/minute"
        mock_settings.asset_rate_limit_per_device = "100/minute"
        mock_settings.presign_expiry_seconds = 3600
        mock_url.side_effect = AssetServiceUnavailableError("unavailable")
        resp = await client.get(
            "/api/v1/assets/soundfonts/fluidr3_gm/download-url",
            headers=DEVICE_HEADERS,
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Bundle download URL
# ---------------------------------------------------------------------------


class TestBundleDownloadURL:

    @pytest.mark.anyio
    @patch("app.api.routes.assets.asset_service.get_bundle_download_url")
    @patch("app.api.routes.assets.settings")
    async def test_happy_path(self, mock_settings: MagicMock, mock_url: MagicMock, client: AsyncClient) -> None:

        mock_settings.aws_s3_asset_bucket = "bucket"
        mock_settings.asset_rate_limit_per_ip = "100/minute"
        mock_settings.asset_rate_limit_per_device = "100/minute"
        mock_settings.presign_expiry_seconds = 3600
        mock_url.return_value = ("https://s3.example.com/bundle.zip", datetime.now(timezone.utc))
        resp = await client.get(
            "/api/v1/assets/bundle/download-url",
            headers=DEVICE_HEADERS,
        )
        assert resp.status_code == 200

    @pytest.mark.anyio
    @patch("app.api.routes.assets.asset_service.get_bundle_download_url")
    @patch("app.api.routes.assets.settings")
    async def test_not_found(self, mock_settings: MagicMock, mock_url: MagicMock, client: AsyncClient) -> None:

        mock_settings.aws_s3_asset_bucket = "bucket"
        mock_settings.asset_rate_limit_per_ip = "100/minute"
        mock_settings.asset_rate_limit_per_device = "100/minute"
        mock_settings.presign_expiry_seconds = 3600
        mock_url.side_effect = KeyError("Bundle not found")
        resp = await client.get(
            "/api/v1/assets/bundle/download-url",
            headers=DEVICE_HEADERS,
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    @patch("app.api.routes.assets.asset_service.get_bundle_download_url")
    @patch("app.api.routes.assets.settings")
    async def test_s3_unavailable(self, mock_settings: MagicMock, mock_url: MagicMock, client: AsyncClient) -> None:

        from app.services.assets import AssetServiceUnavailableError
        mock_settings.aws_s3_asset_bucket = "bucket"
        mock_settings.asset_rate_limit_per_ip = "100/minute"
        mock_settings.asset_rate_limit_per_device = "100/minute"
        mock_settings.presign_expiry_seconds = 3600
        mock_url.side_effect = AssetServiceUnavailableError("unavailable")
        resp = await client.get(
            "/api/v1/assets/bundle/download-url",
            headers=DEVICE_HEADERS,
        )
        assert resp.status_code == 503
