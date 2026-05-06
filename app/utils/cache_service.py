# In-memory cache using a simple dictionary with TTL
import time
import logging

logger = logging.getLogger(__name__)

_cache = {}

class InMemoryCache:
    """Simple in-memory cache with TTL."""
    
    @classmethod
    async def get(cls, key: str):
        """Get value from cache, return None if expired or not found."""
        item = _cache.get(key)
        if item and item["expires_at"] > time.time():
            return item["value"]
        # Remove expired item if found
        if item:
            del _cache[key]
        return None

    @classmethod
    async def set(cls, key: str, value: any, ttl: int):
        """Set value in cache with TTL in seconds."""
        _cache[key] = {"value": value, "expires_at": time.time() + ttl}

    @classmethod
    async def delete(cls, key: str):
        """Delete item from cache."""
        if key in _cache:
            del _cache[key]

    @classmethod
    async def clear(cls):
        """Clear the entire cache."""
        _cache.clear()
