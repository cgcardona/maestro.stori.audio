"""
Stori Composer Configuration

Environment-based configuration for the composer service.
"""
import logging
import os
from functools import lru_cache
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _app_version_from_package() -> str:
    """Read version from pyproject.toml — the single source of truth."""
    try:
        from importlib.metadata import version
        return version("composer-stori")
    except Exception:
        pass
    # Fallback: parse pyproject.toml directly (dev / non-installed mode)
    try:
        from pathlib import Path
        import re
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.MULTILINE)
        if match:
            return match.group(1)
    except Exception:
        pass
    return "0.0.0-unknown"


# Models shown in the Stori composer model picker.
# Update this list when new versions ship; slugs must match OpenRouter IDs exactly.
# Sorted cheapest-first by convention; the endpoint re-sorts by cost anyway.
ALLOWED_MODEL_IDS: list[str] = [
    "anthropic/claude-sonnet-4.6",  # Latest Claude Sonnet
    "anthropic/claude-opus-4.6",    # Latest Claude Opus
    # Add new versions here as they release
]

# Pricing catalogue (cost per 1M tokens in dollars, sourced from OpenRouter).
# Includes models not in ALLOWED_MODEL_IDS so internal LLM routing still works.
APPROVED_MODELS: dict[str, dict] = {
    # Anthropic Claude models (reasoning enabled via API parameter)
    "anthropic/claude-sonnet-4.6": {
        "name": "Claude Sonnet 4.6",
        "input_cost": 3.0,
        "output_cost": 15.0,
    },
    "anthropic/claude-opus-4.6": {
        "name": "Claude Opus 4.6",
        "input_cost": 5.0,
        "output_cost": 25.0,
    },
    # Kept for internal LLM routing; not exposed in the picker
    "anthropic/claude-sonnet-4.5": {
        "name": "Claude Sonnet 4.5",
        "input_cost": 3.0,
        "output_cost": 15.0,
    },
    "anthropic/claude-opus-4.5": {
        "name": "Claude Opus 4.5",
        "input_cost": 15.0,
        "output_cost": 75.0,
    },
    "anthropic/claude-3.7-sonnet": {
        "name": "Claude 3.7 Sonnet",
        "input_cost": 3.0,
        "output_cost": 15.0,
    },
}


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Service Info (app_version: single source is pyproject.toml when installed; else fallback)
    app_name: str = "Stori Composer"
    app_version: str = _app_version_from_package()
    debug: bool = False
    
    # Server Configuration
    stori_host: str = "0.0.0.0"
    stori_port: int = 10001
    
    # Database Configuration
    # PostgreSQL: postgresql+asyncpg://user:pass@localhost:5432/stori
    # SQLite (dev): sqlite+aiosqlite:///./stori.db
    database_url: Optional[str] = None
    stori_db_password: Optional[str] = None  # PostgreSQL password
    
    # Budget Configuration
    default_budget_cents: int = 500  # $5.00 default budget for new users
    
    # Cloud LLM Configuration (OpenRouter only)
    llm_provider: str = "openrouter"
    llm_model: str = "anthropic/claude-sonnet-4.6"  # Default model with reasoning enabled via API parameter
    llm_timeout: int = 120  # seconds
    llm_max_tokens: int = 4096
    
    # API Keys for Cloud Providers
    openrouter_api_key: Optional[str] = None
    
    # Qdrant Vector Database (for RAG)
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    
    # Music Generation Service Configuration
    orpheus_base_url: str = "http://localhost:10002"
    orpheus_timeout: int = 60  # seconds
    
    hf_api_key: Optional[str] = None  # HuggingFace API key
    hf_timeout: int = 120  # seconds (HF can be slow on cold starts)
    
    # Composer Service Configuration
    composer_host: str = "0.0.0.0"
    composer_port: int = 10001
    
    # LLM Parameters
    llm_temperature: float = 0.7
    llm_top_p: float = 0.95

    # Orchestration (EDITING loop and tool-calling)
    orchestration_max_iterations: int = 5   # Max LLM turns per request in EDITING (non-composition)
    composition_max_iterations: int = 15    # Higher iteration limit for composition (1-2 tools per turn with reasoning models)
    orchestration_temperature: float = 0.1   # Low temp for deterministic tool selection
    composition_max_tokens: int = 32768      # Higher token budget for GENERATE_MUSIC in EDITING mode
    composition_reasoning_fraction: float = 0.4  # Reasoning share for composition (tool calls need more tokens than thinking)
    
    # CORS Settings (fail closed: no default origins)
    # Set STORI_CORS_ORIGINS (JSON array) in .env. Local dev: ["http://localhost:5173", "stori://"].
    # Production: exact origins only, e.g. ["https://your-domain.com", "stori://"]. Never use "*" in production.
    cors_origins: list[str] = []

    @model_validator(mode="after")
    def _warn_cors_wildcard_in_production(self) -> "Settings":
        """Warn when CORS allows all origins in non-debug (production) mode."""
        if not self.debug and self.cors_origins and "*" in self.cors_origins:
            logging.getLogger(__name__).warning(
                "CORS allows all origins (*) with STORI_DEBUG=false. "
                "Set STORI_CORS_ORIGINS to exact origins in production."
            )
        return self

    # Access Token Settings
    # Generate secret with: openssl rand -hex 32
    access_token_secret: Optional[str] = None
    access_token_algorithm: str = "HS256"
    # In-memory revocation cache TTL (seconds). Reduces DB hits; revocation visible within at most this window.
    token_revocation_cache_ttl_seconds: int = 60
    
    # AWS S3 Asset Delivery (drum kits, GM soundfont)
    # Region MUST match the bucket's region (S3 returns 301 if URL uses wrong region).
    # Override with STORI_AWS_REGION if your bucket is in a different region.
    aws_region: str = "eu-west-1"  # stori-assets bucket region; set STORI_AWS_REGION if different
    aws_s3_asset_bucket: Optional[str] = None  # e.g. stori-assets
    aws_cloudfront_domain: Optional[str] = None  # e.g. assets.example.com (optional)
    presign_expiry_seconds: int = 1800  # 30 min default for presigned download URLs (leaked URLs die faster)
    
    # Asset endpoint rate limits (UUID-only auth, no JWT)
    # Per device (X-Device-ID) and per IP to prevent abuse
    asset_rate_limit_per_device: str = "30/minute"
    asset_rate_limit_per_ip: str = "120/minute"

    # Stdio MCP server: proxy DAW tools to Composer backend (so Cursor sees the same DAW as the app)
    # When set, stdio server forwards DAW tool calls to this URL with the token; backend has the WebSocket.
    composer_mcp_url: Optional[str] = None  # e.g. http://localhost:10001
    mcp_token: Optional[str] = None  # JWT for Authorization: Bearer when proxying

    model_config = SettingsConfigDict(
        env_prefix="STORI_",
        env_file=".env",
        env_file_encoding="utf-8",
    )


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Convenience access
settings = get_settings()
