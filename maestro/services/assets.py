"""
On-demand asset delivery service (drum kits, GM soundfont).

Uses S3 presigned URLs only; no file bytes stream through the app.
Assets are stored under:
  - assets/drum-kits/{kit_id}.zip (one zip per kit)
  - assets/drum-kits/manifest.json (list of kits with name, version, etc.)
  - assets/soundfonts/{filename}.sf2
  - assets/soundfonts/manifest.json (list of soundfonts)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol, cast

from typing_extensions import TypedDict


class _S3StreamingBody(Protocol):
    """Structural interface for the streaming body returned by S3 get_object."""

    def read(self) -> bytes: ...


class _GetObjectResponse(TypedDict):
    """Typed subset of the boto3 get_object response that we actually use."""

    Body: _S3StreamingBody


class _S3Client(Protocol):
    """Structural interface for the boto3 S3 client methods used in this module."""

    def get_object(self, *, Bucket: str, Key: str) -> _GetObjectResponse: ...
    def generate_presigned_url(self, operation: str, /, **kwargs: object) -> str: ...
    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]: ...
    def head_bucket(self, *, Bucket: str) -> dict[str, object]: ...


class DrumKitInfo(TypedDict, total=False):
    """Metadata for a single drum kit from the S3 manifest."""

    id: str
    name: str
    version: str


class SoundFontInfo(TypedDict, total=False):
    """Metadata for a single soundfont from the S3 manifest."""

    id: str
    name: str
    filename: str

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, BotoCoreError, NoCredentialsError

from maestro.config import settings

logger = logging.getLogger(__name__)

# Use Signature Version 4 for presigned URLs. SigV2 (legacy) can cause 403 from S3.
S3_CONFIG = Config(signature_version="s3v4")


class AssetServiceUnavailableError(Exception):
    """Raised when S3/credentials are unavailable; map to HTTP 503."""
    pass

# S3 key prefixes
DRUM_KITS_PREFIX = "assets/drum-kits/"
DRUM_KITS_MANIFEST_KEY = "assets/drum-kits/manifest.json"
SOUNDFONTS_PREFIX = "assets/soundfonts/"
SOUNDFONTS_MANIFEST_KEY = "assets/soundfonts/manifest.json"
BUNDLE_KEY = "assets/bundle/all-assets.zip"

# Default manifest when S3 manifest is missing (so app can still list)
DEFAULT_DRUM_KITS: list[DrumKitInfo] = [
    DrumKitInfo(id="cr78", name="CR-78", version="1.0"),
    DrumKitInfo(id="linndrum", name="LinnDrum", version="1.0"),
    DrumKitInfo(id="pearl", name="Pearl", version="1.0"),
    DrumKitInfo(id="tr505", name="TR-505", version="1.0"),
    DrumKitInfo(id="tr909", name="TR-909", version="1.0"),
    DrumKitInfo(id="template", name="Template Kit", version="1.0"),
]

DEFAULT_SOUNDFONTS: list[SoundFontInfo] = [
    SoundFontInfo(id="fluidr3_gm", name="Fluid R3 GM", filename="FluidR3_GM.sf2"),
]


def _s3_client() -> _S3Client:
    """
    Create S3 client with SigV4 and regional endpoint.
    Using the regional endpoint (e.g. s3.us-east-1.amazonaws.com) avoids redirects from the
    global endpoint that can cause 400 Bad Request when the client follows redirects and
    the signature no longer matches.
    """
    region = settings.aws_region
    endpoint_url = f"https://s3.{region}.amazonaws.com"
    # boto3 has no type stubs — cast to our Protocol at the untyped library boundary.
    return cast(
        _S3Client,
        boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            config=S3_CONFIG,
        ),
    )


def _bucket() -> str:
    """Return the configured S3 bucket name, raising if unset.

    Raises:
        ValueError: If ``AWS_S3_ASSET_BUCKET`` is not configured.
    """
    if not settings.aws_s3_asset_bucket:
        raise ValueError("AWS_S3_ASSET_BUCKET is not set")
    return settings.aws_s3_asset_bucket


def _get_object_json(key: str) -> dict[str, object] | None:
    """Fetch a JSON object from S3. Returns None if missing or on error."""
    try:
        client = _s3_client()
        resp = client.get_object(Bucket=_bucket(), Key=key)
        body = resp["Body"].read().decode("utf-8")
        parsed = json.loads(body)
        assert isinstance(parsed, dict)
        return parsed
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            logger.debug("Manifest not found: %s", key)
            return None
        logger.warning("S3 get_object failed for %s: %s", key, e)
        return None
    except Exception as e:
        logger.warning("Failed to load manifest %s: %s", key, e)
        return None


def _to_drum_kit(k: dict[str, object]) -> DrumKitInfo:
    """Coerce a raw S3 manifest entry to a typed ``DrumKitInfo``.

    Accepts only string values for known fields; ignores or drops
    anything that does not type-check, keeping the result safe to
    pass to callers expecting ``DrumKitInfo``.
    """
    info: DrumKitInfo = {}
    if isinstance(_id := k.get("id"), str):
        info["id"] = _id
    if isinstance(_name := k.get("name"), str):
        info["name"] = _name
    if isinstance(_ver := k.get("version"), str):
        info["version"] = _ver
    return info


def _to_soundfont(s: dict[str, object]) -> SoundFontInfo:
    """Coerce a raw S3 manifest entry to a typed ``SoundFontInfo``.

    Accepts only string values for known fields; ignores or drops
    anything that does not type-check, keeping the result safe to
    pass to callers expecting ``SoundFontInfo``.
    """
    info: SoundFontInfo = {}
    if isinstance(_id := s.get("id"), str):
        info["id"] = _id
    if isinstance(_name := s.get("name"), str):
        info["name"] = _name
    if isinstance(_fn := s.get("filename"), str):
        info["filename"] = _fn
    return info


def list_drum_kits() -> list[DrumKitInfo]:
    """
    Return list of available drum kits.
    Uses manifest.json if present; otherwise returns default list (no size hints).
    """
    if not settings.aws_s3_asset_bucket:
        return []
    data = _get_object_json(DRUM_KITS_MANIFEST_KEY)
    kits = data.get("kits") if data else None
    if isinstance(kits, list):
        return [_to_drum_kit(k) for k in kits if isinstance(k, dict)]
    return DEFAULT_DRUM_KITS


def list_soundfonts() -> list[SoundFontInfo]:
    """
    Return list of available soundfonts.
    Uses manifest.json if present; otherwise returns default list.
    """
    if not settings.aws_s3_asset_bucket:
        return []
    data = _get_object_json(SOUNDFONTS_MANIFEST_KEY)
    soundfonts = data.get("soundfonts") if data else None
    if isinstance(soundfonts, list):
        return [_to_soundfont(s) for s in soundfonts if isinstance(s, dict)]
    return DEFAULT_SOUNDFONTS


def get_drum_kit_download_url(
    kit_id: str,
    expires_in: int | None = None,
) -> tuple[str, datetime]:
    """
    Generate presigned URL for a drum kit zip.
    Key: assets/drum-kits/{kit_id}.zip
    Returns (url, expires_at).
    Uses the same source of truth as list_drum_kits(): if kit_id is in that list, we return a URL.
    No S3 head_object check — list and download-url stay in sync. If the object is missing in S3,
    the client GET on the presigned URL will get 404; upload the zip to fix step 2.
    Raises KeyError if kit_id not in list; AssetServiceUnavailableError on S3/credential failure.
    """
    # Single source of truth: same list as list_drum_kits(). If list says kit exists, we accept it.
    known = {k.get("id") for k in list_drum_kits() if k.get("id")}
    if kit_id not in known:
        raise KeyError(f"Drum kit not found: {kit_id}")

    expires_in = expires_in or settings.presign_expiry_seconds
    key = f"{DRUM_KITS_PREFIX.rstrip('/')}/{kit_id}.zip"
    bucket = _bucket()
    try:
        client = _s3_client()
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        return url, expires_at
    except NoCredentialsError as e:
        logger.warning("AWS credentials not configured: %s", e)
        raise AssetServiceUnavailableError("AWS credentials not configured") from e
    except BotoCoreError as e:
        logger.warning("S3 unreachable for drum kit %s: %s", kit_id, e)
        raise AssetServiceUnavailableError("Unable to reach asset storage") from e
    except ClientError as e:
        logger.error("S3 error for drum kit %s: %s", kit_id, e)
        raise AssetServiceUnavailableError("Unable to generate download URL") from e


def get_soundfont_download_url(
    soundfont_id: str,
    expires_in: int | None = None,
) -> tuple[str, datetime]:
    """
    Generate presigned URL for a soundfont file.
    Resolves soundfont_id to filename via manifest; key is assets/soundfonts/{filename}.
    Returns (url, expires_at).
    """
    expires_in = expires_in or settings.presign_expiry_seconds
    soundfonts = list_soundfonts()
    filename = None
    for sf in soundfonts:
        if sf.get("id") == soundfont_id:
            filename = sf.get("filename") or f"{soundfont_id}.sf2"
            break
    if not filename:
        raise KeyError(f"SoundFont not found: {soundfont_id}")
    key = f"{SOUNDFONTS_PREFIX}{filename}"
    bucket = _bucket()
    try:
        client = _s3_client()
        client.head_object(Bucket=bucket, Key=key)
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        return url, expires_at
    except NoCredentialsError as e:
        logger.warning("AWS credentials not configured: %s", e)
        raise AssetServiceUnavailableError("AWS credentials not configured") from e
    except BotoCoreError as e:
        logger.warning("S3 unreachable for soundfont %s: %s", soundfont_id, e)
        raise AssetServiceUnavailableError("Unable to reach asset storage") from e
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "403"):
            raise KeyError(f"SoundFont not found: {soundfont_id}") from e
        logger.error("S3 error for soundfont %s: %s", soundfont_id, e)
        raise AssetServiceUnavailableError("Unable to generate download URL") from e


def get_bundle_download_url(
    expires_in: int | None = None,
) -> tuple[str, datetime]:
    """
    Generate presigned URL for the full bundle zip (all drum kits + GM soundfont).
    Key: assets/bundle/all-assets.zip
    Returns (url, expires_at). Raises if bundle not present.
    """
    expires_in = expires_in or settings.presign_expiry_seconds
    bucket = _bucket()
    try:
        client = _s3_client()
        client.head_object(Bucket=bucket, Key=BUNDLE_KEY)
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": BUNDLE_KEY},
            ExpiresIn=expires_in,
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        return url, expires_at
    except NoCredentialsError as e:
        logger.warning("AWS credentials not configured: %s", e)
        raise AssetServiceUnavailableError("AWS credentials not configured") from e
    except BotoCoreError as e:
        logger.warning("S3 unreachable for bundle: %s", e)
        raise AssetServiceUnavailableError("Unable to reach asset storage") from e
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "403"):
            raise KeyError("Bundle not found") from e
        logger.error("S3 error for bundle: %s", e)
        raise AssetServiceUnavailableError("Unable to generate download URL") from e


def check_s3_reachable() -> bool:
    """Verify we can reach S3 (e.g. head bucket or get manifest). Returns True if OK."""
    if not settings.aws_s3_asset_bucket:
        return False
    try:
        client = _s3_client()
        client.head_bucket(Bucket=_bucket())
        return True
    except Exception as e:
        logger.debug("S3 health check failed: %s", e)
        return False
