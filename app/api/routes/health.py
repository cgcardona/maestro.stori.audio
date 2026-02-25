"""Health check endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.config import settings
from app.services.orpheus import OrpheusClient
from app.services.assets import check_s3_reachable

router = APIRouter()


def _llm_configured() -> bool:
    """True if the configured LLM provider has an API key set (OpenRouter)."""
    return settings.llm_provider == "openrouter" and bool(settings.openrouter_api_key)


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Basic health check."""
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "tagline": "the infinite music machine",
    }


@router.get("/health/full")
async def full_health_check() -> dict[str, Any]:
    """
    Full health check including dependencies.

    Reports:
    - LLM: configured (OpenRouter API key present)
    - Orpheus music service (if reachable)
    - S3 asset delivery (if configured)
    """
    orpheus = OrpheusClient()
    try:
        llm_ok = _llm_configured()
        orpheus_ok = await orpheus.health_check()
        s3_ok = check_s3_reachable() if settings.aws_s3_asset_bucket else None  # None = not configured

        all_ok = llm_ok and orpheus_ok

        deps = {
            "llm": {
                "status": "ok" if llm_ok else "unconfigured",
                "provider": settings.llm_provider,
            },
            "orpheus": {
                "status": "ok" if orpheus_ok else "unavailable",
                "url": settings.orpheus_base_url,
            },
        }
        if settings.aws_s3_asset_bucket:
            deps["s3_assets"] = {
                "status": "ok" if s3_ok else "error",
                "bucket": settings.aws_s3_asset_bucket,
            }

        return {
            "status": "ok" if all_ok else "degraded",
            "service": settings.app_name,
            "version": settings.app_version,
            "tagline": "the infinite music machine",
            "dependencies": deps,
        }
    finally:
        await orpheus.close()
