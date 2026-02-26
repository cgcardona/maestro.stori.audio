"""Tests for the assets service (app/services/assets.py).

Covers: list_drum_kits, list_soundfonts, get_drum_kit_download_url,
get_soundfont_download_url, get_bundle_download_url, check_s3_reachable,
_bucket, _get_object_json.
"""
from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError, NoCredentialsError

from app.services.assets import (
    list_drum_kits,
    list_soundfonts,
    get_drum_kit_download_url,
    get_soundfont_download_url,
    get_bundle_download_url,
    check_s3_reachable,
    AssetServiceUnavailableError,
    DEFAULT_DRUM_KITS_MANIFEST,
    DEFAULT_SOUNDFONTS_MANIFEST,
)


def _make_client_error(code: str = "NoSuchKey") -> ClientError:

    return ClientError(
        error_response={"Error": {"Code": code, "Message": "test"}},
        operation_name="GetObject",
    )


# ---------------------------------------------------------------------------
# list_drum_kits
# ---------------------------------------------------------------------------


class TestListDrumKits:

    @patch("app.services.assets.settings")
    def test_no_bucket_returns_empty(self, mock_settings: MagicMock) -> None:

        mock_settings.aws_s3_asset_bucket = ""
        assert list_drum_kits() == []

    @patch("app.services.assets._get_object_json")
    @patch("app.services.assets.settings")
    def test_manifest_found(self, mock_settings: MagicMock, mock_get: MagicMock) -> None:

        mock_settings.aws_s3_asset_bucket = "test-bucket"
        mock_get.return_value = {
            "kits": [{"id": "tr909", "name": "TR-909"}]
        }
        result = list_drum_kits()
        assert len(result) == 1
        assert result[0]["id"] == "tr909"

    @patch("app.services.assets._get_object_json")
    @patch("app.services.assets.settings")
    def test_manifest_missing_returns_defaults(self, mock_settings: MagicMock, mock_get: MagicMock) -> None:

        mock_settings.aws_s3_asset_bucket = "test-bucket"
        mock_get.return_value = None
        result = list_drum_kits()
        assert result == DEFAULT_DRUM_KITS_MANIFEST["kits"]


# ---------------------------------------------------------------------------
# list_soundfonts
# ---------------------------------------------------------------------------


class TestListSoundfonts:

    @patch("app.services.assets.settings")
    def test_no_bucket_returns_empty(self, mock_settings: MagicMock) -> None:

        mock_settings.aws_s3_asset_bucket = ""
        assert list_soundfonts() == []

    @patch("app.services.assets._get_object_json")
    @patch("app.services.assets.settings")
    def test_manifest_found(self, mock_settings: MagicMock, mock_get: MagicMock) -> None:

        mock_settings.aws_s3_asset_bucket = "test-bucket"
        mock_get.return_value = {
            "soundfonts": [{"id": "gm", "name": "GM", "filename": "GM.sf2"}]
        }
        result = list_soundfonts()
        assert len(result) == 1

    @patch("app.services.assets._get_object_json")
    @patch("app.services.assets.settings")
    def test_manifest_missing_returns_defaults(self, mock_settings: MagicMock, mock_get: MagicMock) -> None:

        mock_settings.aws_s3_asset_bucket = "test-bucket"
        mock_get.return_value = None
        result = list_soundfonts()
        assert result == DEFAULT_SOUNDFONTS_MANIFEST["soundfonts"]


# ---------------------------------------------------------------------------
# get_drum_kit_download_url
# ---------------------------------------------------------------------------


class TestGetDrumKitDownloadURL:

    @patch("app.services.assets._s3_client")
    @patch("app.services.assets._bucket", return_value="test-bucket")
    @patch("app.services.assets.list_drum_kits")
    @patch("app.services.assets.settings")
    def test_happy_path(self, mock_settings: MagicMock, mock_list: MagicMock, mock_bucket: MagicMock, mock_s3: MagicMock) -> None:

        mock_settings.presign_expiry_seconds = 3600
        mock_list.return_value = [{"id": "tr909"}]
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = "https://s3.example.com/tr909.zip"
        mock_s3.return_value = mock_client

        url, expires_at = get_drum_kit_download_url("tr909")
        assert "tr909" in url
        assert isinstance(expires_at, datetime)

    @patch("app.services.assets.list_drum_kits")
    def test_unknown_kit_raises(self, mock_list: MagicMock) -> None:

        mock_list.return_value = [{"id": "tr909"}]
        with pytest.raises(KeyError, match="unknown-kit"):
            get_drum_kit_download_url("unknown-kit")

    @patch("app.services.assets._s3_client")
    @patch("app.services.assets._bucket", return_value="test-bucket")
    @patch("app.services.assets.list_drum_kits")
    @patch("app.services.assets.settings")
    def test_no_credentials(self, mock_settings: MagicMock, mock_list: MagicMock, mock_bucket: MagicMock, mock_s3: MagicMock) -> None:

        mock_settings.presign_expiry_seconds = 3600
        mock_list.return_value = [{"id": "tr909"}]
        mock_client = MagicMock()
        mock_client.generate_presigned_url.side_effect = NoCredentialsError()
        mock_s3.return_value = mock_client

        with pytest.raises(AssetServiceUnavailableError):
            get_drum_kit_download_url("tr909")


# ---------------------------------------------------------------------------
# get_soundfont_download_url
# ---------------------------------------------------------------------------


class TestGetSoundfontDownloadURL:

    @patch("app.services.assets._s3_client")
    @patch("app.services.assets._bucket", return_value="test-bucket")
    @patch("app.services.assets.list_soundfonts")
    @patch("app.services.assets.settings")
    def test_happy_path(self, mock_settings: MagicMock, mock_list: MagicMock, mock_bucket: MagicMock, mock_s3: MagicMock) -> None:

        mock_settings.presign_expiry_seconds = 3600
        mock_list.return_value = [
            {"id": "fluidr3_gm", "name": "Fluid", "filename": "FluidR3_GM.sf2"}
        ]
        mock_client = MagicMock()
        mock_client.head_object.return_value = {}
        mock_client.generate_presigned_url.return_value = "https://s3.example.com/FluidR3_GM.sf2"
        mock_s3.return_value = mock_client

        url, expires_at = get_soundfont_download_url("fluidr3_gm")
        assert "FluidR3_GM" in url

    @patch("app.services.assets.list_soundfonts")
    @patch("app.services.assets.settings")
    def test_unknown_soundfont_raises(self, mock_settings: MagicMock, mock_list: MagicMock) -> None:

        mock_settings.presign_expiry_seconds = 3600
        mock_list.return_value = [{"id": "gm", "filename": "GM.sf2"}]
        with pytest.raises(KeyError, match="unknown"):
            get_soundfont_download_url("unknown")

    @patch("app.services.assets._s3_client")
    @patch("app.services.assets._bucket", return_value="test-bucket")
    @patch("app.services.assets.list_soundfonts")
    @patch("app.services.assets.settings")
    def test_s3_404_raises_key_error(self, mock_settings: MagicMock, mock_list: MagicMock, mock_bucket: MagicMock, mock_s3: MagicMock) -> None:

        mock_settings.presign_expiry_seconds = 3600
        mock_list.return_value = [{"id": "gm", "filename": "GM.sf2"}]
        mock_client = MagicMock()
        mock_client.head_object.side_effect = _make_client_error("404")
        mock_s3.return_value = mock_client

        with pytest.raises(KeyError):
            get_soundfont_download_url("gm")


# ---------------------------------------------------------------------------
# get_bundle_download_url
# ---------------------------------------------------------------------------


class TestGetBundleDownloadURL:

    @patch("app.services.assets._s3_client")
    @patch("app.services.assets._bucket", return_value="test-bucket")
    @patch("app.services.assets.settings")
    def test_happy_path(self, mock_settings: MagicMock, mock_bucket: MagicMock, mock_s3: MagicMock) -> None:

        mock_settings.presign_expiry_seconds = 3600
        mock_client = MagicMock()
        mock_client.head_object.return_value = {}
        mock_client.generate_presigned_url.return_value = "https://s3.example.com/bundle.zip"
        mock_s3.return_value = mock_client

        url, expires_at = get_bundle_download_url()
        assert "bundle" in url

    @patch("app.services.assets._s3_client")
    @patch("app.services.assets._bucket", return_value="test-bucket")
    @patch("app.services.assets.settings")
    def test_bundle_missing(self, mock_settings: MagicMock, mock_bucket: MagicMock, mock_s3: MagicMock) -> None:

        mock_settings.presign_expiry_seconds = 3600
        mock_client = MagicMock()
        mock_client.head_object.side_effect = _make_client_error("404")
        mock_s3.return_value = mock_client

        with pytest.raises(KeyError, match="Bundle not found"):
            get_bundle_download_url()

    @patch("app.services.assets._s3_client")
    @patch("app.services.assets._bucket", return_value="test-bucket")
    @patch("app.services.assets.settings")
    def test_no_credentials(self, mock_settings: MagicMock, mock_bucket: MagicMock, mock_s3: MagicMock) -> None:

        mock_settings.presign_expiry_seconds = 3600
        mock_client = MagicMock()
        mock_client.head_object.side_effect = NoCredentialsError()
        mock_s3.return_value = mock_client

        with pytest.raises(AssetServiceUnavailableError):
            get_bundle_download_url()


# ---------------------------------------------------------------------------
# check_s3_reachable
# ---------------------------------------------------------------------------


class TestCheckS3Reachable:

    @patch("app.services.assets.settings")
    def test_no_bucket(self, mock_settings: MagicMock) -> None:

        mock_settings.aws_s3_asset_bucket = ""
        assert check_s3_reachable() is False

    @patch("app.services.assets._s3_client")
    @patch("app.services.assets._bucket", return_value="test-bucket")
    @patch("app.services.assets.settings")
    def test_reachable(self, mock_settings: MagicMock, mock_bucket: MagicMock, mock_s3: MagicMock) -> None:

        mock_settings.aws_s3_asset_bucket = "test-bucket"
        mock_client = MagicMock()
        mock_client.head_bucket.return_value = {}
        mock_s3.return_value = mock_client
        assert check_s3_reachable() is True

    @patch("app.services.assets._s3_client")
    @patch("app.services.assets._bucket", return_value="test-bucket")
    @patch("app.services.assets.settings")
    def test_unreachable(self, mock_settings: MagicMock, mock_bucket: MagicMock, mock_s3: MagicMock) -> None:

        mock_settings.aws_s3_asset_bucket = "test-bucket"
        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = Exception("timeout")
        mock_s3.return_value = mock_client
        assert check_s3_reachable() is False
