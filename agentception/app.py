"""AgentCeption FastAPI application factory.

Entry point: ``uvicorn agentception.app:app --port 7777 --reload``

Architecture:
- ``lifespan`` starts a background poller task that periodically refreshes
  the ``PipelineState`` from the filesystem and GitHub API.
- Static files are served from ``agentception/static/``.
- HTML pages are rendered via Jinja2 from ``agentception/templates/``.
- JSON API routes live in ``agentception/routes/`` (stubbed for now).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from agentception.config import settings

logger = logging.getLogger(__name__)

# Resolve paths relative to this file so the app works regardless of cwd.
_HERE = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_HERE / "templates"))


async def _poll_loop() -> None:
    """Background task: refresh pipeline state on a fixed interval.

    The initial implementation is a stub — subsequent issues will fill in the
    actual GitHub + filesystem collection logic. The loop runs until cancelled.
    """
    while True:
        try:
            await asyncio.sleep(settings.poll_interval_seconds)
            logger.debug("⏱️  Poll tick (stub — no readers wired yet)")
        except asyncio.CancelledError:
            logger.info("✅ Poller stopped cleanly")
            return
        except Exception as exc:
            logger.warning("⚠️  Poller error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start the background poller on startup; cancel it on shutdown."""
    poller = asyncio.create_task(_poll_loop(), name="agentception-poller")
    logger.info("✅ AgentCeption poller started (interval=%ds)", settings.poll_interval_seconds)
    try:
        yield
    finally:
        poller.cancel()
        try:
            await poller
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="AgentCeption",
    description="Maestro pipeline agent dashboard",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount static assets — CSS, future JS bundles.
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness probe — returns ``{"status": "ok"}`` when the service is up."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse, tags=["ui"])
async def index(request: Request) -> HTMLResponse:
    """Dashboard home — renders the base template with nav skeleton."""
    return _TEMPLATES.TemplateResponse(request, "base.html")
