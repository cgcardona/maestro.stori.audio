"""Tests for per-user concurrent composition limiter."""
from __future__ import annotations

import asyncio

import pytest

from app.core.composition_limiter import (
    CompositionLimiter,
    CompositionLimitExceeded,
)


class TestCompositionLimiter:
    @pytest.mark.asyncio
    async def test_allows_under_limit(self) -> None:

        """User can start compositions up to the limit."""
        lim = CompositionLimiter(max_per_user=2)
        async with lim.acquire("user-1"):
            assert lim.active_count("user-1") == 1
            async with lim.acquire("user-1"):
                assert lim.active_count("user-1") == 2
        assert lim.active_count("user-1") == 0

    @pytest.mark.asyncio
    async def test_rejects_over_limit(self) -> None:

        """Third concurrent composition raises CompositionLimitExceeded."""
        lim = CompositionLimiter(max_per_user=2)
        async with lim.acquire("user-1"):
            async with lim.acquire("user-1"):
                with pytest.raises(CompositionLimitExceeded) as exc:
                    async with lim.acquire("user-1"):
                        pass
                assert exc.value.limit == 2
                assert exc.value.active == 2

    @pytest.mark.asyncio
    async def test_releases_on_exception(self) -> None:

        """Slot is released even if the composition raises an error."""
        lim = CompositionLimiter(max_per_user=1)
        with pytest.raises(RuntimeError):
            async with lim.acquire("user-1"):
                raise RuntimeError("boom")
        assert lim.active_count("user-1") == 0
        async with lim.acquire("user-1"):
            assert lim.active_count("user-1") == 1

    @pytest.mark.asyncio
    async def test_independent_users(self) -> None:

        """Different users have independent quotas."""
        lim = CompositionLimiter(max_per_user=1)
        async with lim.acquire("user-a"):
            async with lim.acquire("user-b"):
                assert lim.active_count("user-a") == 1
                assert lim.active_count("user-b") == 1

    @pytest.mark.asyncio
    async def test_unlimited_when_zero(self) -> None:

        """max_per_user=0 means unlimited."""
        lim = CompositionLimiter(max_per_user=0)
        async with lim.acquire("user-1"):
            async with lim.acquire("user-1"):
                async with lim.acquire("user-1"):
                    assert lim.active_count("user-1") == 0  # not tracked

    @pytest.mark.asyncio
    async def test_none_user_bypasses(self) -> None:

        """None user_id bypasses the limiter entirely."""
        lim = CompositionLimiter(max_per_user=1)
        async with lim.acquire(None):
            async with lim.acquire(None):
                pass

    @pytest.mark.asyncio
    async def test_snapshot(self) -> None:

        """Snapshot returns current active counts."""
        lim = CompositionLimiter(max_per_user=5)
        async with lim.acquire("a"):
            async with lim.acquire("b"):
                snap = lim.snapshot()
                assert snap == {"a": 1, "b": 1}
        assert lim.snapshot() == {}

    @pytest.mark.asyncio
    async def test_concurrent_acquires_are_serialized(self) -> None:

        """Multiple concurrent acquire calls don't race past the limit."""
        lim = CompositionLimiter(max_per_user=1)
        results: list[str] = []

        async def try_acquire(label: str) -> None:

            try:
                async with lim.acquire("user-1"):
                    results.append(f"{label}-ok")
                    await asyncio.sleep(0.05)
            except CompositionLimitExceeded:
                results.append(f"{label}-rejected")

        await asyncio.gather(
            try_acquire("a"),
            try_acquire("b"),
        )
        assert results.count("a-rejected") + results.count("b-rejected") == 1
