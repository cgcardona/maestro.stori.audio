"""
Maestro Configuration

Environment-based configuration for the Maestro service.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _app_version_from_package() -> str:
    """Read version from the single source of truth (pyproject.toml via protocol.version)."""
    from app.protocol.version import MAESTRO_VERSION
    return MAESTRO_VERSION


# Models shown in the Maestro model picker.
# Update this list when new versions ship; slugs must match OpenRouter IDs exactly.
# Sorted cheapest-first by convention; the endpoint re-sorts by cost anyway.
ALLOWED_MODEL_IDS: list[str] = [
    "anthropic/claude-sonnet-4.6",  # Latest Claude Sonnet
    "anthropic/claude-opus-4.6",    # Latest Claude Opus
    # Add new versions here as they release
]

# Pricing catalogue (cost per 1M tokens in dollars, sourced from OpenRouter).
# Includes models not in ALLOWED_MODEL_IDS so internal LLM routing still works.
APPROVED_MODELS: dict[str, dict[str, str | float]] = {
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


# Context window sizes (input token capacity) per supported model.
# Maestro only supports the two models listed in ALLOWED_MODEL_IDS; anything else
# returns 0 so the frontend leaves the context-usage ring at its previous value.
CONTEXT_WINDOW_TOKENS: dict[str, int] = {
    "anthropic/claude-sonnet-4.6": 200_000,
    "anthropic/claude-opus-4.6":   200_000,
}


def get_context_window_tokens(model: str) -> int:
    """Return the context window size for a supported model, or 0 if unknown."""
    return CONTEXT_WINDOW_TOKENS.get(model, 0)


# Single source of truth for default tempo (BPM).  Referenced by the executor,
# Storpheus client, request models, and planner so they all agree.
DEFAULT_TEMPO: int = 120


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Service Info (app_version: single source is pyproject.toml when installed; else fallback)
    app_name: str = "Stori Maestro"
    app_version: str = _app_version_from_package()
    debug: bool = False
    
    # Server Configuration
    host: str = "0.0.0.0"
    port: int = 10001
    
    # Database Configuration
    # PostgreSQL: postgresql+asyncpg://user:pass@localhost:5432/stori
    # SQLite (dev): sqlite+aiosqlite:///./stori.db
    database_url: str | None = None
    db_password: str | None = None  # PostgreSQL password
    
    # Budget Configuration
    default_budget_cents: int = 500  # $5.00 default budget for new users
    
    # Cloud LLM Configuration (OpenRouter only)
    llm_provider: str = "openrouter"
    llm_model: str = "anthropic/claude-sonnet-4.6"  # Default model with reasoning enabled via API parameter
    llm_timeout: int = 120  # seconds
    llm_max_tokens: int = 4096
    
    # API Keys for Cloud Providers
    openrouter_api_key: str | None = None
    
    # Qdrant Vector Database (for RAG)
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    
    # Music Generation Service Configuration
    storpheus_base_url: str = "http://localhost:10002"
    storpheus_timeout: int = 180  # seconds — fallback max read timeout
    storpheus_max_concurrent: int = 2  # max parallel submit+poll cycles (serializes GPU access)
    storpheus_poll_timeout: int = 30   # seconds — long-poll timeout per /jobs/{id}/wait request
    storpheus_poll_max_attempts: int = 10  # max polls before giving up (~5 min total)
    storpheus_cb_threshold: int = 3   # consecutive failures before circuit breaker trips
    storpheus_cb_cooldown: int = 120  # seconds before tripped circuit allows a probe request
    storpheus_required: bool = True   # hard-gate: abort composition if pre-flight health check fails
    storpheus_preserve_all_channels: bool = True   # return all generated MIDI channels (DAW handles routing)
    storpheus_enable_beat_rescaling: bool = False   # disable beat rescaling to evaluate raw model timing
    storpheus_max_session_tokens: int = 4096        # token cap before session rotation
    storpheus_loops_space: str = ""                 # HF Space ID for Orpheus Loops model (e.g. "asigalov61/Orpheus-Music-Loops")
    storpheus_use_loops_model: bool = False         # feature flag: route short requests (<=8 bars) to Loops model
    skip_expressiveness: bool = True              # MVP: bypass post-processing until raw path is proven
    max_concurrent_compositions_per_user: int = 2  # per-user composition concurrency limit (0 = unlimited)
    
    hf_api_key: str | None = None  # HuggingFace API key
    hf_timeout: int = 120  # seconds (HF can be slow on cold starts)
    
    # Maestro Service Configuration
    maestro_host: str = "0.0.0.0"
    maestro_port: int = 10001
    
    # LLM Parameters
    llm_temperature: float = 0.7
    llm_top_p: float = 0.95

    # Orchestration (EDITING loop and tool-calling)
    orchestration_max_iterations: int = 5   # Max LLM turns per request in EDITING (non-composition)
    composition_max_iterations: int = 20    # Higher iteration limit for composition (1-2 tools per turn with reasoning models)
    orchestration_temperature: float = 0.1   # Low temp for deterministic tool selection
    composition_max_tokens: int = 32768      # Higher token budget for GENERATE_MUSIC in EDITING mode
    composition_reasoning_fraction: float = 0.08  # Keep reasoning tight for tool-calling; ~2,600 tokens on 32K budget
    agent_reasoning_fraction: float = 0.05        # Minimal reasoning — agents execute a fixed pipeline; Storpheus handles musical decisions

    # Agent watchdog timeouts (seconds) — prevents orphaned subagents
    section_child_timeout: int = 300     # 5 min per section child (region + generate + optional refinement)
    instrument_agent_timeout: int = 600  # 10 min per instrument agent (LLM + all sections + effect)
    bass_signal_wait_timeout: int = 240  # 4 min waiting for drum section signal before giving up
    
    # CORS Settings (fail closed: no default origins)
    # set CORS_ORIGINS (JSON array) in .env. Local dev: ["http://localhost:5173", "stori://"].
    # Production: exact origins only, e.g. ["https://your-domain.com", "stori://"]. Never use "*" in production.
    cors_origins: list[str] = []

    @model_validator(mode="after")
    def _warn_cors_wildcard_in_production(self) -> "Settings":
        """Warn when CORS allows all origins in non-debug (production) mode."""
        if not self.debug and self.cors_origins and "*" in self.cors_origins:
            logging.getLogger(__name__).warning(
                "CORS allows all origins (*) with DEBUG=false. "
                "Set CORS_ORIGINS to exact origins in production."
            )
        return self

    # Access Token Settings
    # Generate secret with: openssl rand -hex 32
    access_token_secret: str | None = None
    access_token_algorithm: str = "HS256"
    # In-memory revocation cache TTL (seconds). Reduces DB hits; revocation visible within at most this window.
    token_revocation_cache_ttl_seconds: int = 60
    
    # AWS S3 Asset Delivery (drum kits, GM soundfont)
    # Region MUST match the bucket's region (S3 returns 301 if URL uses wrong region).
    # Override with AWS_REGION if your bucket is in a different region.
    aws_region: str = "eu-west-1"  # stori-assets bucket region; set AWS_REGION if different
    aws_s3_asset_bucket: str | None = None  # e.g. stori-assets
    aws_cloudfront_domain: str | None = None  # e.g. assets.example.com (optional)
    presign_expiry_seconds: int = 1800  # 30 min default for presigned download URLs (leaked URLs die faster)
    
    # Asset endpoint rate limits (UUID-only auth, no JWT)
    # Per device (X-Device-ID) and per IP to prevent abuse
    asset_rate_limit_per_device: str = "30/minute"
    asset_rate_limit_per_ip: str = "120/minute"

    # Stdio MCP server: proxy DAW tools to Maestro backend (so Cursor sees the same DAW as the app)
    # When set, stdio server forwards DAW tool calls to this URL with the token; backend has the WebSocket.
    maestro_mcp_url: str | None = None  # e.g. http://localhost:10001
    mcp_token: str | None = None  # JWT for Authorization: Bearer when proxying

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Convenience access
settings = get_settings()
