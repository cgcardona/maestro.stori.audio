"""Per-user concurrent composition limiter.

Tracks active compositions per user_id in memory. Use as an async context
manager around the composition lifecycle to automatically acquire/release
slots.  Raises ``CompositionLimitExceeded`` when a user exceeds their quota.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.config import settings

logger = logging.getLogger(__name__)


class CompositionLimitExceeded(Exception):
    """Raised when a user has too many concurrent compositions."""

    def __init__(self, user_id: str, limit: int, active: int):
        self.user_id = user_id
        self.limit = limit
        self.active = active
        super().__init__(
            f"User {user_id} has {active} active compositions "
            f"(limit: {limit})"
        )


class CompositionLimiter:
    """In-memory per-user composition concurrency tracker."""

    def __init__(self, max_per_user: int = 0):
        self._max = max_per_user
        self._active: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, user_id: str | None) -> AsyncIterator[None]:
        """Acquire a composition slot. Raises CompositionLimitExceeded if full."""
        if not user_id or self._max <= 0:
            yield
            return

        async with self._lock:
            current = self._active[user_id]
            if current >= self._max:
                raise CompositionLimitExceeded(user_id, self._max, current)
            self._active[user_id] += 1

        try:
            yield
        finally:
            async with self._lock:
                self._active[user_id] = max(0, self._active[user_id] - 1)
                if self._active[user_id] == 0:
                    del self._active[user_id]

    def active_count(self, user_id: str) -> int:
        """Return the number of in-flight compositions for ``user_id``."""
        return self._active.get(user_id, 0)

    def snapshot(self) -> dict[str, int]:
        """Return a point-in-time copy of all active counts (for health/debug endpoints)."""
        return dict(self._active)


_limiter: CompositionLimiter | None = None


def get_composition_limiter() -> CompositionLimiter:
    """Return the process-wide singleton ``CompositionLimiter``, creating it if needed.

    Configured from ``STORI_MAX_CONCURRENT_COMPOSITIONS_PER_USER`` via
    ``app.config.settings``.  Setting the value to ``0`` disables per-user
    limits entirely (the ``acquire`` context manager becomes a no-op).
    """
    global _limiter
    if _limiter is None:
        _limiter = CompositionLimiter(
            max_per_user=settings.max_concurrent_compositions_per_user,
        )
    return _limiter
