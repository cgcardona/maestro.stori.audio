"""
Stori Maestro API

FastAPI application for AI-powered music composition.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.api.routes import maestro, maestro_ui, health, users, conversations, assets, variation, muse
from app.api.routes import mcp as mcp_routes
from app.db import init_db, close_db
from app.services.orpheus import get_orpheus_client, close_orpheus_client


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""
    
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        
        # Prevent clickjacking
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
    logger.info(f"Orpheus service: {settings.orpheus_base_url}")
    
    # Initialize database
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

    # Production: refuse weak or missing DB password (R1)
    if not settings.debug and settings.database_url and "postgres" in settings.database_url:
        pw = (settings.stori_db_password or "").strip()
        if not pw or pw == "changeme123":
            raise RuntimeError(
                "Production requires STORI_DB_PASSWORD set to a strong value. "
                "Do not use 'changeme123' or leave it unset. Generate with: openssl rand -hex 16"
            )

    # Warm up Orpheus connection pool so the first generation request incurs
    # no cold-start TCP/TLS handshake cost.
    await get_orpheus_client().warmup()

    yield

    # Cleanup
    logger.info("Shutting down...")
    await close_db()
    await close_orpheus_client()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Stori — the infinite music machine.",
    lifespan=lifespan,
    # Disable public docs in production/stage; set STORI_DEBUG=true locally to enable
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    openapi_url="/openapi.json" if settings.debug else None,
)

# Add rate limiter to app state and exception handler
app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    _rate_limit_exceeded_handler,  # type: ignore[arg-type]  # slowapi handler signature is (Request, RateLimitExceeded) not (Request, Exception)
)

# Security headers middleware (added first, runs last)
app.add_middleware(SecurityHeadersMiddleware)

# CORS middleware
if "*" in settings.cors_origins:
    logger.warning(
        "SECURITY WARNING: CORS allows all origins. "
        "set STORI_CORS_ORIGINS to specific domains in production."
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
app.include_router(mcp_routes.router, prefix="/api/v1/mcp", tags=["mcp"])

from app.protocol.endpoints import router as protocol_router
app.include_router(protocol_router, prefix="/api/v1", tags=["protocol"])


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint with service info."""
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
    }
