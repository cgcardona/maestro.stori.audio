"""
In-memory token revocation cache.

Reduces DB hits on every authenticated request. Cache key is token hash;
value is whether the token is revoked. TTL is configurable; on revoke
we clear the cache so revocation is visible on the next request.
"""
import time
from typing import Optional

from app.config import settings

# token_hash -> (revoked: bool, expires_at: float)
_cache: dict[str, tuple[bool, float]] = {}


def get_revocation_status(token_hash: str) -> Optional[bool]:
    """
    Return cached revocation status if present and not expired.

    Returns:
        True if cached as revoked, False if cached as valid, None if miss or expired.
    """
    entry = _cache.get(token_hash)
    if entry is None:
        return None
    revoked, expires_at = entry
    if time.monotonic() > expires_at:
        del _cache[token_hash]
        return None
    return revoked


def set_revocation_status(token_hash: str, revoked: bool) -> None:
    """Cache revocation status for token_hash for the configured TTL."""
    ttl = settings.token_revocation_cache_ttl_seconds
    _cache[token_hash] = (revoked, time.monotonic() + ttl)


def clear_revocation_cache() -> None:
    """Clear the entire cache. Call after revoking token(s) so next request sees DB state."""
    _cache.clear()
