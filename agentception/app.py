"""AgentCeption FastAPI application factory.

Entry point: ``uvicorn agentception.app:app --port 7777 --reload``

Architecture:
- ``lifespan`` starts the background ``polling_loop`` task from ``poller.py``
  that periodically refreshes the ``PipelineState`` from the filesystem and
  GitHub API.
- ``GET /events`` streams the live ``PipelineState`` as Server-Sent Events to
  connected dashboard clients.
- Static files are served from ``agentception/static/``.
- HTML pages are rendered via Jinja2 from ``agentception/templates/``.
- JSON API routes live in ``agentception/routes/``.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request

from agentception.poller import polling_loop, subscribe, unsubscribe

logger = logging.getLogger(__name__)

# Resolve paths relative to this file so the app works regardless of cwd.
_HERE = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_HERE / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start the background poller on startup; cancel it on shutdown."""
    poller = asyncio.create_task(polling_loop(), name="agentception-poller")
    logger.info("✅ AgentCeption poller started")
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


@app.get("/events", tags=["sse"])
async def sse_stream(request: Request) -> EventSourceResponse:
    """Stream live ``PipelineState`` snapshots as Server-Sent Events.

    Each connected dashboard client receives one event per polling tick
    (default every 5 s).  The connection is cleaned up automatically when
    the client disconnects.
    """
    q = subscribe()

    async def generator() -> AsyncIterator[dict[str, str]]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    state = await asyncio.wait_for(q.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Keep-alive: yield an empty comment so the connection
                    # stays open through proxies that close idle SSE streams.
                    yield {"comment": "ping"}
                    continue
                yield {"data": state.model_dump_json()}
        finally:
            unsubscribe(q)

    return EventSourceResponse(generator())


@app.get("/", response_class=HTMLResponse, tags=["ui"])
async def index(request: Request) -> HTMLResponse:
    """Dashboard home — renders the base template with nav skeleton."""
    return _TEMPLATES.TemplateResponse(request, "base.html")
