"""
Tests for LRU+TTL cache system.

The cache is critical for performance (475x speedup) and must be bounded
to prevent memory issues in production.
"""
import pytest
from time import time, sleep
from music_service import (
    get_cache_key,
    get_cached_result,
    cache_result,
    _result_cache,
    MAX_CACHE_SIZE,
    CACHE_TTL_SECONDS,
    GenerateRequest,
    CacheEntry,
)


def setup_function():
    """Clear cache before each test."""
    _result_cache.clear()


def test_cache_key_generation():
    """Test that cache keys are deterministic."""
    from music_service import IntentGoal, EmotionVectorPayload
    req1 = GenerateRequest(
        genre="trap",
        tempo=140,
        bars=4,
        instruments=["drums"],
        intent_goals=[IntentGoal(name="dark")],
        emotion_vector=EmotionVectorPayload(valence=-0.7),
    )
    
    req2 = GenerateRequest(
        genre="trap",
        tempo=140,
        bars=4,
        instruments=["drums"],
        intent_goals=[IntentGoal(name="dark")],
        emotion_vector=EmotionVectorPayload(valence=-0.7),
    )
    
    key1 = get_cache_key(req1)
    key2 = get_cache_key(req2)
    
    # Same inputs should produce same key
    assert key1 == key2


def test_cache_key_differences():
    """Test that different inputs produce different keys."""
    req1 = GenerateRequest(genre="trap", tempo=140)
    req2 = GenerateRequest(genre="trap", tempo=141)  # Different tempo
    req3 = GenerateRequest(genre="house", tempo=140)  # Different genre
    
    key1 = get_cache_key(req1)
    key2 = get_cache_key(req2)
    key3 = get_cache_key(req3)
    
    assert key1 != key2
    assert key1 != key3
    assert key2 != key3


def test_cache_hit_and_miss():
    """Test basic cache hit/miss behavior."""
    key = "test_key_123"
    
    # Miss
    result = get_cached_result(key)
    assert result is None
    
    # Cache it
    data = {"success": True, "tool_calls": []}
    cache_result(key, data)
    
    # Hit
    result = get_cached_result(key)
    assert result is not None
    assert result["success"] is True


def test_cache_lru_ordering():
    """Test that LRU moves accessed items to end."""
    # Add 3 entries
    cache_result("key1", {"data": 1})
    cache_result("key2", {"data": 2})
    cache_result("key3", {"data": 3})
    
    # Access key1 (should move to end)
    get_cached_result("key1")
    
    # Check order (most recently used should be last)
    keys = list(_result_cache.keys())
    assert keys[-1] == "key1"  # Last accessed
    assert keys[0] == "key2"   # Oldest


def test_cache_eviction_at_capacity():
    """Test that cache evicts oldest when full."""
    # Fill cache to capacity
    for i in range(MAX_CACHE_SIZE):
        cache_result(f"key_{i}", {"data": i})
    
    assert len(_result_cache) == MAX_CACHE_SIZE
    
    # Add one more - should evict key_0
    cache_result("key_new", {"data": "new"})
    
    assert len(_result_cache) == MAX_CACHE_SIZE
    assert "key_0" not in _result_cache
    assert "key_new" in _result_cache


def test_cache_hit_counter():
    """Test that hit counter increments."""
    key = "test_key"
    cache_result(key, {"data": 1})
    
    # First hit
    get_cached_result(key)
    assert _result_cache[key].hits == 1
    
    # Second hit
    get_cached_result(key)
    assert _result_cache[key].hits == 2
    
    # Third hit
    get_cached_result(key)
    assert _result_cache[key].hits == 3


def test_cache_ttl_expiration():
    """Test that expired entries are removed."""
    key = "test_key"
    
    # Manually create expired entry
    _result_cache[key] = CacheEntry(
        result={"data": 1},
        timestamp=time() - (CACHE_TTL_SECONDS + 100),  # Expired
        hits=0
    )
    
    # Should return None and remove from cache
    result = get_cached_result(key)
    assert result is None
    assert key not in _result_cache


def test_cache_ttl_not_expired():
    """Test that fresh entries are returned."""
    key = "test_key"
    
    # Create recent entry
    _result_cache[key] = CacheEntry(
        result={"data": 1},
        timestamp=time() - 60,  # 1 minute ago (not expired)
        hits=0
    )
    
    # Should return result
    result = get_cached_result(key)
    assert result is not None
    assert result["data"] == 1


def test_cache_key_rounding():
    """Test that similar continuous values produce same cache key."""
    from music_service import EmotionVectorPayload
    req1 = GenerateRequest(emotion_vector=EmotionVectorPayload(valence=-0.61))
    req2 = GenerateRequest(emotion_vector=EmotionVectorPayload(valence=-0.59))
    
    key1 = get_cache_key(req1)
    key2 = get_cache_key(req2)
    
    assert key1 == key2


def test_cache_intent_goals_order():
    """Test that intent goal order doesn't affect cache key."""
    from music_service import IntentGoal
    req1 = GenerateRequest(intent_goals=[IntentGoal(name="dark"), IntentGoal(name="energetic")])
    req2 = GenerateRequest(intent_goals=[IntentGoal(name="energetic"), IntentGoal(name="dark")])
    
    key1 = get_cache_key(req1)
    key2 = get_cache_key(req2)
    
    assert key1 == key2


def test_cache_preserves_data():
    """Test that cached data is not mutated."""
    key = "test_key"
    original_data = {"success": True, "tool_calls": [{"tool": "test"}]}
    
    cache_result(key, original_data)
    
    # Retrieve and modify
    cached = get_cached_result(key)
    assert cached is not None
    cached["success"] = False
    
    # Original cache should be unchanged
    cached_again = get_cached_result(key)
    assert cached_again is not None
    assert cached_again["success"] is True  # Not modified
