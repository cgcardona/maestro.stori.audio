"""Tests for app.auth.revocation_cache."""
import time
from unittest.mock import patch
import pytest

from app.auth.revocation_cache import (
    get_revocation_status,
    set_revocation_status,
    clear_revocation_cache,
)


@pytest.fixture(autouse=True)
def clear_before_after():
    clear_revocation_cache()
    yield
    clear_revocation_cache()


def test_get_revocation_status_miss_returns_none():
    assert get_revocation_status("nonexistent") is None


def test_set_and_get_revocation_status_revoked():
    with patch("app.auth.revocation_cache.settings") as m:
        m.token_revocation_cache_ttl_seconds = 60
        set_revocation_status("hash1", revoked=True)
    assert get_revocation_status("hash1") is True


def test_set_and_get_revocation_status_valid():
    with patch("app.auth.revocation_cache.settings") as m:
        m.token_revocation_cache_ttl_seconds = 60
        set_revocation_status("hash2", revoked=False)
    assert get_revocation_status("hash2") is False


def test_get_revocation_status_expired_returns_none():
    with patch("app.auth.revocation_cache.settings") as m:
        m.token_revocation_cache_ttl_seconds = 0
        set_revocation_status("hash3", revoked=True)
    time.sleep(0.01)
    assert get_revocation_status("hash3") is None


def test_clear_revocation_cache():
    with patch("app.auth.revocation_cache.settings") as m:
        m.token_revocation_cache_ttl_seconds = 60
        set_revocation_status("hash4", revoked=True)
    assert get_revocation_status("hash4") is True
    clear_revocation_cache()
    assert get_revocation_status("hash4") is None
