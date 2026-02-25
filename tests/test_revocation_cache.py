"""Tests for app.auth.revocation_cache."""
from __future__ import annotations

import time
from collections.abc import Generator
from unittest.mock import patch
import pytest

from app.auth.revocation_cache import (
    get_revocation_status,
    set_revocation_status,
    clear_revocation_cache,
)


@pytest.fixture(autouse=True)
def clear_before_after() -> Generator[None, None, None]:
    clear_revocation_cache()
    yield
    clear_revocation_cache()


def test_get_revocation_status_miss_returns_none() -> None:
    assert get_revocation_status("nonexistent") is None


def test_set_and_get_revocation_status_revoked() -> None:
    with patch("app.auth.revocation_cache.settings") as m:
        m.token_revocation_cache_ttl_seconds = 60
        set_revocation_status("hash1", revoked=True)
    assert get_revocation_status("hash1") is True


def test_set_and_get_revocation_status_valid() -> None:
    with patch("app.auth.revocation_cache.settings") as m:
        m.token_revocation_cache_ttl_seconds = 60
        set_revocation_status("hash2", revoked=False)
    assert get_revocation_status("hash2") is False


def test_get_revocation_status_expired_returns_none() -> None:
    with patch("app.auth.revocation_cache.settings") as m:
        m.token_revocation_cache_ttl_seconds = 0
        set_revocation_status("hash3", revoked=True)
    time.sleep(0.01)
    assert get_revocation_status("hash3") is None


def test_clear_revocation_cache() -> None:
    with patch("app.auth.revocation_cache.settings") as m:
        m.token_revocation_cache_ttl_seconds = 60
        set_revocation_status("hash4", revoked=True)
    assert get_revocation_status("hash4") is True
    clear_revocation_cache()
    assert get_revocation_status("hash4") is None
