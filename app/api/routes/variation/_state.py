"""Shared module-level state for the variation route package."""
from __future__ import annotations

import asyncio

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

# Background generation tasks keyed by variation_id (for cancellation)
_generation_tasks: dict[str, asyncio.Task[None]] = {}


def _sse_headers() -> dict[str, str]:
    """Standard SSE response headers."""
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
