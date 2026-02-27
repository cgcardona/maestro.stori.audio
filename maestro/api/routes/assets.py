"""
On-demand asset delivery API (drum kits, GM soundfont).

Returns presigned S3 URLs only; no file bytes stream through FastAPI.
All routes require X-Device-ID header (UUID only; no JWT). Rate limited by device ID and IP.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

from maestro.auth.dependencies import require_device_id
from maestro.config import settings
from maestro.services import assets as asset_service
from maestro.services.assets import AssetServiceUnavailableError
from slowapi.errors import RateLimitExceeded

router = APIRouter()
logger = logging.getLogger(__name__)


def _asset_rate_limit_key(request: Request) -> str:
    """Rate limit key: by X-Device-ID when present, else by IP."""
    device_id = (request.headers.get("X-Device-ID") or "").strip()
    if device_id:
        return f"device:{device_id}"
    return get_remote_address(request)


limiter_by_device = Limiter(key_func=_asset_rate_limit_key)
limiter_by_ip = Limiter(key_func=get_remote_address)


@router.get(
    "/assets/drum-kits",
    response_model=None,
    responses={
        200: {"description": "list of available drum kits"},
        400: {"description": "Missing or invalid X-Device-ID"},
        429: {"description": "Rate limit exceeded"},
        503: {"description": "Asset service not configured"},
    },
)
@limiter_by_ip.limit(settings.asset_rate_limit_per_ip)
@limiter_by_device.limit(settings.asset_rate_limit_per_device)
async def list_drum_kits(
    request: Request,
    device_id: str = Depends(require_device_id),
) -> JSONResponse:
    """
    list available drum kits.

    Each item includes: id, name, version, and optionally fileCount/sizeBytes.
    Use the id in GET /api/assets/drum-kits/{kit_id}/download-url to get a download URL.
    """
    if not settings.aws_s3_asset_bucket:
        return JSONResponse(
            status_code=503,
            content={"detail": "Asset service not configured (AWS_S3_ASSET_BUCKET not set)."},
            headers={"Cache-Control": "no-store"},
        )
    try:
        kits = asset_service.list_drum_kits()
        logger.info("Listed %d drum kits", len(kits))
        return JSONResponse(
            content=kits,
            headers={"Cache-Control": "public, max-age=300"},  # 5 min cache for list
        )
    except RateLimitExceeded:
        raise
    except Exception as e:
        logger.exception("Failed to list drum kits: %s", e)
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to list drum kits."},
            headers={"Cache-Control": "no-store"},
        )


@router.get(
    "/assets/soundfonts",
    response_model=None,
    responses={
        200: {"description": "list of available soundfonts"},
        400: {"description": "Missing or invalid X-Device-ID"},
        429: {"description": "Rate limit exceeded"},
        503: {"description": "Asset service not configured"},
    },
)
@limiter_by_ip.limit(settings.asset_rate_limit_per_ip)
@limiter_by_device.limit(settings.asset_rate_limit_per_device)
async def list_soundfonts(
    request: Request,
    device_id: str = Depends(require_device_id),
) -> JSONResponse:
    """
    list available soundfonts (e.g. GM SoundFont).

    Each item includes: id, name, filename.
    Use the id in GET /api/assets/soundfonts/{soundfont_id}/download-url to get a download URL.
    """
    if not settings.aws_s3_asset_bucket:
        return JSONResponse(
            status_code=503,
            content={"detail": "Asset service not configured (AWS_S3_ASSET_BUCKET not set)."},
            headers={"Cache-Control": "no-store"},
        )
    try:
        soundfonts = asset_service.list_soundfonts()
        logger.info("Listed %d soundfonts", len(soundfonts))
        return JSONResponse(
            content=soundfonts,
            headers={"Cache-Control": "public, max-age=300"},
        )
    except RateLimitExceeded:
        raise
    except Exception as e:
        logger.exception("Failed to list soundfonts: %s", e)
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to list soundfonts."},
            headers={"Cache-Control": "no-store"},
        )


@router.get(
    "/assets/drum-kits/{kit_id}/download-url",
    response_model=None,
    responses={
        200: {"description": "Presigned URL for kit zip"},
        400: {"description": "Missing or invalid X-Device-ID"},
        404: {"description": "Kit not found"},
        429: {"description": "Rate limit exceeded"},
        503: {"description": "Asset service not configured"},
    },
)
@limiter_by_ip.limit(settings.asset_rate_limit_per_ip)
@limiter_by_device.limit(settings.asset_rate_limit_per_device)
async def get_drum_kit_download_url(
    request: Request,
    kit_id: str,
    device_id: str = Depends(require_device_id),
    expires_in: int | None = Query(None, ge=60, le=86400, description="URL expiry in seconds (default from config)"),
) -> JSONResponse:
    """
    Get a presigned download URL for a drum kit zip.

    The URL points to a single zip containing kit.json and all .wav files.
    The app should GET this URL with a normal HTTP client (e.g. URLSession), download the zip,
    and unzip to e.g. ~/Library/Application Support/Stori/DrumKits/{kit_id}/.
    """
    if not settings.aws_s3_asset_bucket:
        return JSONResponse(
            status_code=503,
            content={"detail": "Asset service not configured (AWS_S3_ASSET_BUCKET not set)."},
            headers={"Cache-Control": "no-store"},
        )
    try:
        url, expires_at = asset_service.get_drum_kit_download_url(
            kit_id,
            expires_in=expires_in or settings.presign_expiry_seconds,
        )
        logger.info("Generated download URL for drum kit %s, expires_in=%s", kit_id, expires_in)
        return JSONResponse(
            content={
                "url": url,
                "expiresAt": expires_at.isoformat(),
            },
            headers={"Cache-Control": "no-store"},
        )
    except KeyError:
        logger.warning("Drum kit not found: %s", kit_id)
        return JSONResponse(
            status_code=404,
            content={"detail": f"Drum kit not found: {kit_id}"},
            headers={"Cache-Control": "no-store"},
        )
    except AssetServiceUnavailableError as e:
        logger.warning("Asset service unavailable for drum kit %s: %s", kit_id, e)
        return JSONResponse(
            status_code=503,
            content={"detail": "Unable to generate download URL."},
            headers={"Cache-Control": "no-store"},
        )
    except RateLimitExceeded:
        raise
    except Exception as e:
        logger.exception("Failed to generate drum kit URL for %s: %s", kit_id, e)
        return JSONResponse(
            status_code=500,
            content={"detail": "Unable to generate download URL."},
            headers={"Cache-Control": "no-store"},
        )


@router.get(
    "/assets/soundfonts/{soundfont_id}/download-url",
    response_model=None,
    responses={
        200: {"description": "Presigned URL for .sf2 file"},
        400: {"description": "Missing or invalid X-Device-ID"},
        404: {"description": "SoundFont not found"},
        429: {"description": "Rate limit exceeded"},
        503: {"description": "Asset service not configured"},
    },
)
@limiter_by_ip.limit(settings.asset_rate_limit_per_ip)
@limiter_by_device.limit(settings.asset_rate_limit_per_device)
async def get_soundfont_download_url(
    request: Request,
    soundfont_id: str,
    device_id: str = Depends(require_device_id),
    expires_in: int | None = Query(None, ge=60, le=86400, description="URL expiry in seconds (default from config)"),
) -> JSONResponse:
    """
    Get a presigned download URL for a soundfont (.sf2) file.

    The app should GET this URL, download the file, and save to
    e.g. ~/Library/Application Support/Stori/SoundFonts/{filename}.
    """
    if not settings.aws_s3_asset_bucket:
        return JSONResponse(
            status_code=503,
            content={"detail": "Asset service not configured (AWS_S3_ASSET_BUCKET not set)."},
            headers={"Cache-Control": "no-store"},
        )
    try:
        url, expires_at = asset_service.get_soundfont_download_url(
            soundfont_id,
            expires_in=expires_in or settings.presign_expiry_seconds,
        )
        logger.info("Generated download URL for soundfont %s, expires_in=%s", soundfont_id, expires_in)
        return JSONResponse(
            content={
                "url": url,
                "expiresAt": expires_at.isoformat(),
            },
            headers={"Cache-Control": "no-store"},
        )
    except KeyError:
        logger.warning("SoundFont not found: %s", soundfont_id)
        return JSONResponse(
            status_code=404,
            content={"detail": f"SoundFont not found: {soundfont_id}"},
            headers={"Cache-Control": "no-store"},
        )
    except AssetServiceUnavailableError:
        logger.warning("Asset service unavailable for soundfont %s", soundfont_id)
        return JSONResponse(
            status_code=503,
            content={"detail": "Unable to generate download URL."},
            headers={"Cache-Control": "no-store"},
        )
    except RateLimitExceeded:
        raise
    except Exception as e:
        logger.exception("Failed to generate soundfont URL for %s: %s", soundfont_id, e)
        return JSONResponse(
            status_code=500,
            content={"detail": "Unable to generate download URL."},
            headers={"Cache-Control": "no-store"},
        )


@router.get(
    "/assets/bundle/download-url",
    response_model=None,
    responses={
        200: {"description": "Presigned URL for full bundle zip"},
        400: {"description": "Missing or invalid X-Device-ID"},
        404: {"description": "Bundle not found"},
        429: {"description": "Rate limit exceeded"},
        503: {"description": "Asset service not configured"},
    },
)
@limiter_by_ip.limit(settings.asset_rate_limit_per_ip)
@limiter_by_device.limit(settings.asset_rate_limit_per_device)
async def get_bundle_download_url(
    request: Request,
    device_id: str = Depends(require_device_id),
    expires_in: int | None = Query(None, ge=60, le=86400, description="URL expiry in seconds (default from config)"),
) -> JSONResponse:
    """
    Get a presigned download URL for the full bundle (all drum kits + GM soundfont in one zip).

    Optional. Zip layout is documented in ASSETS_API.md so the app can unpack to correct local paths.
    """
    if not settings.aws_s3_asset_bucket:
        return JSONResponse(
            status_code=503,
            content={"detail": "Asset service not configured (AWS_S3_ASSET_BUCKET not set)."},
            headers={"Cache-Control": "no-store"},
        )
    try:
        url, expires_at = asset_service.get_bundle_download_url(
            expires_in=expires_in or settings.presign_expiry_seconds,
        )
        logger.info("Generated download URL for bundle, expires_in=%s", expires_in)
        return JSONResponse(
            content={
                "url": url,
                "expiresAt": expires_at.isoformat(),
            },
            headers={"Cache-Control": "no-store"},
        )
    except KeyError:
        logger.warning("Bundle not found")
        return JSONResponse(
            status_code=404,
            content={"detail": "Bundle not found."},
            headers={"Cache-Control": "no-store"},
        )
    except AssetServiceUnavailableError:
        logger.warning("Asset service unavailable for bundle")
        return JSONResponse(
            status_code=503,
            content={"detail": "Unable to generate download URL."},
            headers={"Cache-Control": "no-store"},
        )
    except RateLimitExceeded:
        raise
    except Exception as e:
        logger.exception("Failed to generate bundle URL: %s", e)
        return JSONResponse(
            status_code=500,
            content={"detail": "Unable to generate download URL."},
            headers={"Cache-Control": "no-store"},
        )
