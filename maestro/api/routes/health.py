"""Health check endpoints."""
from __future__ import annotations

from typing import Required
from typing_extensions import TypedDict

from fastapi import APIRouter

from maestro.config import settings
from maestro.services.storpheus import StorpheusClient
from maestro.services.assets import check_s3_reachable

router = APIRouter()


class HealthDependencyDict(TypedDict, total=False):
    """Status entry for one external dependency in the full health check.

    ``status`` is always present.  Additional keys depend on the dependency:
    ``provider`` for LLM, ``url`` for Storpheus, ``bucket`` for S3.
    """

    status: Required[str]
    provider: str   # LLM only
    url: str        # Storpheus only
    bucket: str     # S3 only


class FullHealthCheckDict(TypedDict):
    """Response shape for ``GET /health/full``."""

    status: str             # "ok" | "degraded"
    service: str
    version: str
    tagline: str
    dependencies: dict[str, HealthDependencyDict]


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
async def full_health_check() -> FullHealthCheckDict:
    """Full health check including dependencies.

    Reports:
    - LLM: configured (OpenRouter API key present)
    - Storpheus music service (if reachable)
    - S3 asset delivery (if configured)
    """
    storpheus = StorpheusClient()
    try:
        llm_ok = _llm_configured()
        storpheus_ok = await storpheus.health_check()
        s3_ok = check_s3_reachable() if settings.aws_s3_asset_bucket else None

        all_ok = llm_ok and storpheus_ok

        deps: dict[str, HealthDependencyDict] = {
            "llm": {
                "status": "ok" if llm_ok else "unconfigured",
                "provider": settings.llm_provider,
            },
            "storpheus": {
                "status": "ok" if storpheus_ok else "unavailable",
                "url": settings.storpheus_base_url,
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
        await storpheus.close()
