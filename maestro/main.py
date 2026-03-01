"""
Maestro API

FastAPI application for AI-powered music composition.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Awaitable, Callable

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from maestro.config import settings
from maestro.api.routes import maestro, maestro_ui, health, users, conversations, assets, variation, muse, musehub
from maestro.api.routes.musehub import ui as musehub_ui_routes
from maestro.api.routes.musehub import ui_milestones as musehub_ui_milestones_routes
from maestro.api.routes.musehub import ui_blame as musehub_ui_blame_routes
from maestro.api.routes.musehub import ui_notifications as musehub_ui_notifications_routes
from maestro.api.routes.musehub import ui_collaborators as musehub_ui_collab_routes
from maestro.api.routes.musehub import ui_settings as musehub_ui_settings_routes
from maestro.api.routes.musehub import ui_similarity as musehub_ui_similarity_routes
from maestro.api.routes.musehub import ui_emotion_diff as musehub_ui_emotion_diff_routes
from maestro.api.routes.musehub import ui_user_profile as musehub_ui_profile_routes
from maestro.api.routes.musehub import discover as musehub_discover_routes
from maestro.api.routes.musehub import users as musehub_user_routes
from maestro.api.routes.musehub import oembed as musehub_oembed_routes
from maestro.api.routes.musehub import raw as musehub_raw_routes
from maestro.api.routes.musehub import sitemap as musehub_sitemap_routes
from maestro.api.routes import mcp as mcp_routes
from maestro.db import init_db, close_db
from maestro.services.storpheus import get_storpheus_client, close_storpheus_client


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""
    
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        
        # Prevent clickjacking. Embed routes set ALLOWALL explicitly;
        # only apply the DENY default when no value has been set by the handler.
        if "X-Frame-Options" not in response.headers:
            response.headers["X-Frame-Options"] = "DENY"
        
        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        
        # Enable XSS filter
        response.headers["X-XSS-Protection"] = "1; mode=block"
        
        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # CSP is set by nginx in production (allows /docs Swagger UI CDN). Do not set here
        # or the browser would enforce both headers and block CDN resources.
        
        # Permissions policy (disable unnecessary features)
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), "
            "gyroscope=(), magnetometer=(), microphone=(), "
            "payment=(), usb=()"
        )
        
        # HSTS (only when behind HTTPS proxy)
        # set via nginx/reverse proxy in production
        
        return response

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Rate limiter - uses IP address as key
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler."""
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"LLM provider: {settings.llm_provider}")
    logger.info(f"LLM model: {settings.llm_model}")
    logger.info(f"Storpheus service: {settings.storpheus_base_url}")
    
    # Initialize database
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

    # Production: refuse weak or missing DB password (R1)
    if not settings.debug and settings.database_url and "postgres" in settings.database_url:
        pw = (settings.db_password or "").strip()
        if not pw or pw == "changeme123":
            raise RuntimeError(
                "Production requires DB_PASSWORD set to a strong value. "
                "Do not use 'changeme123' or leave it unset. Generate with: openssl rand -hex 16"
            )

    # Warm up Storpheus connection pool so the first generation request incurs
    # no cold-start TCP/TLS handshake cost.
    await get_storpheus_client().warmup()

    yield

    # Cleanup
    logger.info("Shutting down...")
    await close_db()
    await close_storpheus_client()


app = FastAPI(
    title="Stori MuseHub API",
    version=settings.app_version,
    description=(
        "**MuseHub** — the music composition version control platform powering Stori DAW.\n\n"
        "MuseHub gives AI agents and human composers a GitHub-style workflow for music:\n"
        "push commits, open pull requests, track issues, and browse public repos via a "
        "machine-readable OpenAPI spec.\n\n"
        "## Authentication\n\n"
        "All write endpoints and private-repo reads require a **Bearer JWT** in the "
        "`Authorization` header:\n\n"
        "```\nAuthorization: Bearer <your-jwt>\n```\n\n"
        "Public repo read endpoints (`GET /repos/*`, `/discover/*`, `/search`) accept "
        "unauthenticated requests.\n\n"
        "## Tags\n\n"
        "Endpoints are grouped by resource type:\n"
        "- **Repos** — create, read, and manage music repositories\n"
        "- **Branches** — branch pointers and divergence\n"
        "- **Commits** — push / pull / inspect commit history\n"
        "- **Issues** — open, list, and close repo issues\n"
        "- **Pull Requests** — open, review, and merge branches\n"
        "- **Objects** — binary artifact storage (MIDI, MP3, WebP piano rolls)\n"
        "- **Analysis** — 13-dimension musical analysis per commit ref\n"
        "- **Sessions** — creative session tracking\n"
        "- **Search** — in-repo and cross-repo commit search\n"
        "- **Releases** — versioned release snapshots\n"
        "- **Webhooks** — event subscriptions\n"
        "- **Social** — stars, forks, follows, comments, reactions\n"
        "- **Users** — public profile management\n"
        "- **Discover** — explore public repos\n"
        "- **Sync** — CLI push/pull wire protocol\n"
    ),
    contact={
        "name": "Stori / Tellurstori",
        "url": "https://stori.com",
        "email": "hello@stori.com",
    },
    license_info={
        "name": "Proprietary",
        "url": "https://stori.com/terms",
    },
    lifespan=lifespan,
    # OpenAPI spec always available for agent SDK generation.
    # Swagger UI and ReDoc gated on DEBUG to avoid exposing interactive
    # docs on production without additional access control.
    openapi_url="/api/v1/openapi.json",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

# Adapter: FastAPI expects (Request, Exception) but slowapi's handler
# takes (Request, RateLimitExceeded). Narrow inside so the outer
# signature satisfies FastAPI's type contract.
def _handle_rate_limit(request: Request, exc: Exception) -> Response:
    if isinstance(exc, RateLimitExceeded):
        return _rate_limit_exceeded_handler(request, exc)
    raise exc

# Add rate limiter to app state and exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _handle_rate_limit)

# Security headers middleware (added first, runs last)
app.add_middleware(SecurityHeadersMiddleware)

# CORS middleware
if "*" in settings.cors_origins:
    logger.warning(
        "SECURITY WARNING: CORS allows all origins. "
        "Set CORS_ORIGINS to specific domains in production."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(maestro.router, prefix="/api/v1", tags=["maestro"])
app.include_router(maestro_ui.router, prefix="/api/v1", tags=["maestro-ui"])
app.include_router(variation.router, prefix="/api/v1", tags=["variation"])
app.include_router(users.router, prefix="/api/v1", tags=["users"])
app.include_router(conversations.router, prefix="/api/v1", tags=["conversations"])
app.include_router(assets.router, prefix="/api/v1", tags=["assets"])
app.include_router(muse.router, prefix="/api/v1", tags=["muse"])
# Fixed-prefix musehub subrouters are registered BEFORE the main musehub router
# so their concrete paths (/musehub/users/..., /musehub/explore, etc.) are matched
# first and are not shadowed by the wildcard /{owner}/{repo_slug} route declared
# last in repos.py.
app.include_router(musehub_user_routes.router, prefix="/api/v1/musehub", tags=["Users"])
app.include_router(musehub_discover_routes.router, prefix="/api/v1", tags=["Discover"])
app.include_router(musehub_discover_routes.star_router, prefix="/api/v1", tags=["Social"])
# Main musehub router — includes the /{owner}/{repo_slug} wildcard last.
app.include_router(musehub.router, prefix="/api/v1")
# UI routers: notifications first (concrete path) so it is not shadowed by the
# /{username} catch-all declared in fixed_router, then fixed-path routes, then wildcards.
app.include_router(musehub_ui_notifications_routes.router, tags=["musehub-ui-notifications"])
# Enhanced profile page: registered before fixed_router so it shadows the old stub route.
app.include_router(musehub_ui_profile_routes.router, tags=["musehub-ui"])
app.include_router(musehub_ui_routes.fixed_router, tags=["musehub-ui"])
# Milestones UI routes registered before the main UI wildcard router so the
# /{owner}/{repo_slug}/milestones paths are matched before /{owner}/{repo_slug}.
app.include_router(musehub_ui_milestones_routes.router, tags=["musehub-ui"])
app.include_router(musehub_ui_collab_routes.router, tags=["musehub-ui"])
app.include_router(musehub_ui_routes.router, tags=["musehub-ui"])
app.include_router(musehub_ui_blame_routes.router, tags=["musehub-ui"])
app.include_router(musehub_ui_settings_routes.router, tags=["musehub-ui-settings"])
app.include_router(musehub_ui_similarity_routes.router, tags=["musehub-ui"])
app.include_router(musehub_ui_emotion_diff_routes.router, tags=["musehub-ui"])
app.include_router(musehub_oembed_routes.router, tags=["musehub-oembed"])
app.include_router(musehub_raw_routes.router, prefix="/api/v1", tags=["musehub-raw"])
# Sitemap and robots.txt — top-level (no /api/v1 prefix), outside musehub auto-discovery.
app.include_router(musehub_sitemap_routes.router, tags=["musehub-sitemap"])
app.include_router(mcp_routes.router, prefix="/api/v1/mcp", tags=["mcp"])

from maestro.protocol.endpoints import router as protocol_router
app.include_router(protocol_router, prefix="/api/v1", tags=["protocol"])

# Mount Muse Hub static assets (design system CSS files).
# The directory lives inside the maestro package so it is bind-mounted in dev
# and COPY'd into the production image alongside the rest of the package.
_MUSEHUB_STATIC_DIR = Path(__file__).parent / "templates" / "musehub" / "static"
app.mount(
    "/musehub/static",
    StaticFiles(directory=str(_MUSEHUB_STATIC_DIR)),
    name="musehub-static",
)


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint with service info."""
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
    }
