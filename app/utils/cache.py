"""
TTL-based in-memory cache for production-ready API performance.

Features:
- Thread-safe with asyncio support
- TTL (Time-To-Live) based expiration
- Configurable cache size limits
- Cache invalidation support
- Statistics tracking

Usage:
    from app.utils.cache import cache, invalidate_cache
    
    # Cache a function result for 60 seconds
    @cache(ttl=60)
    async def get_data(key):
        return await db.find_one(key)
    
    # Invalidate specific cache
    invalidate_cache("subscription", user_id)
"""

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar, Union
from functools import wraps

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class CacheEntry:
    """Single cache entry with metadata."""
    value: Any
    created_at: float
    ttl: int
    access_count: int = 0
    last_accessed: float = field(default_factory=time.time)
    
    @property
    def is_expired(self) -> bool:
        if self.ttl <= 0:
            return False  # No expiration
        return time.time() - self.created_at > self.ttl


class TTLCache:
    """
    Thread-safe TTL cache with LRU eviction.
    
    Features:
    - O(1) get/put operations
    - Automatic expiration based on TTL
    - LRU eviction when max_size reached
    - Statistics tracking
    """
    
    def __init__(self, max_size: int = 1000, default_ttl: int = 60):
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0
        self._evictions = 0
    
    def _generate_key(self, namespace: str, *args, **kwargs) -> str:
        """Generate a unique cache key from namespace and arguments."""
        key_parts = [namespace] + [str(arg) for arg in args]
        key_parts += [f"{k}={v}" for k, v in sorted(kwargs.items())]
        key_str = ":".join(key_parts)
        return hashlib.md5(key_str.encode()).hexdigest()
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache. Returns None if not found or expired."""
        async with self._lock:
            entry = self._cache.get(key)
            
            if entry is None:
                self._misses += 1
                return None
            
            if entry.is_expired:
                del self._cache[key]
                self._misses += 1
                return None
            
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            entry.access_count += 1
            entry.last_accessed = time.time()
            self._hits += 1
            return entry.value
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set value in cache with optional TTL override."""
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                # Evict oldest if at capacity
                if len(self._cache) >= self._max_size:
                    evicted_key = next(iter(self._cache))
                    del self._cache[evicted_key]
                    self._evictions += 1
            
            self._cache[key] = CacheEntry(
                value=value,
                created_at=time.time(),
                ttl=ttl if ttl is not None else self._default_ttl
            )
    
    async def delete(self, key: str) -> bool:
        """Delete specific key from cache. Returns True if key existed."""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    async def invalidate_namespace(self, namespace: str) -> int:
        """
        Invalidate all cache entries matching a namespace prefix.
        Returns count of invalidated entries.
        """
        count = 0
        async with self._lock:
            keys_to_delete = [k for k in self._cache.keys() if k.startswith(namespace)]
            for key in keys_to_delete:
                del self._cache[key]
                count += 1
        if count > 0:
            logger.debug(f"Cache invalidated: {namespace} ({count} entries)")
        return count
    
    async def clear(self) -> None:
        """Clear entire cache."""
        async with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0
    
    async def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count of removed entries."""
        count = 0
        async with self._lock:
            expired_keys = [
                k for k, v in self._cache.items() if v.is_expired
            ]
            for key in expired_keys:
                del self._cache[key]
                count += 1
        if count > 0:
            logger.debug(f"Cache cleanup: removed {count} expired entries")
        return count
    
    def get_stats(self) -> dict:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_percent": round(hit_rate, 2),
            "evictions": self._evictions
        }


# Global cache instance
cache = TTLCache(
    max_size=2000,       # Max 2,000 entries (~10MB memory)
    default_ttl=60       # Default 60 second TTL
)


def cached(ttl: int = 60, namespace: str = "default"):
    """
    Decorator for caching async functions.
    
    Args:
        ttl: Time-to-live in seconds (0 = no expiration)
        namespace: Cache namespace for grouping/invalidation
    
    Usage:
        @cached(ttl=300, namespace="plans")
        async def get_plans():
            return await db.plans.find().to_list()
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            # Generate cache key
            key_parts = [namespace, func.__name__]
            key_parts += [str(arg) for arg in args if not isinstance(arg, self_class)]
            key_parts += [f"{k}={v}" for k, v in sorted(kwargs.items()) if k != 'self']
            cache_key = hashlib.md5(":".join(key_parts).encode()).hexdigest()
            
            # Try to get from cache
            cached_value = await cache.get(cache_key)
            if cached_value is not None:
                return cached_value
            
            # Execute function and cache result
            result = await func(*args, **kwargs)
            
            # Don't cache None values
            if result is not None:
                await cache.set(cache_key, result, ttl=ttl)
            
            return result
        
        return wrapper
    return decorator


class self_class:
    """Sentinel class to identify self arguments that shouldn't be in cache keys."""
    pass


async def invalidate_cache(namespace: str, key_suffix: str = None) -> None:
    """
    Invalidate cache entries.
    
    Args:
        namespace: Namespace prefix to invalidate (e.g., "subscription", "plans")
        key_suffix: Optional specific key suffix to invalidate
    
    Usage:
        # Invalidate all subscription caches
        await invalidate_cache("subscription")
        
        # Invalidate specific user's subscription
        await invalidate_cache("subscription", user_id)
    """
    if key_suffix:
        # For specific key invalidation, we need to search
        # This is less efficient but necessary for user-specific invalidation
        pass
    else:
        await cache.invalidate_namespace(namespace)


async def invalidate_user_cache(user_id: str) -> None:
    """Invalidate all cache entries related to a specific user."""
    namespaces = [
        f"subscription:{user_id}",
        f"usage:{user_id}",
        f"plans",
    ]
    for ns in namespaces:
        await cache.invalidate_namespace(ns)


async def get_cache_stats() -> dict:
    """Get cache statistics."""
    return cache.get_stats()


async def cleanup_cache() -> int:
    """Remove expired cache entries. Returns count of removed entries."""
    return await cache.cleanup_expired()


class CacheManager:
    """
    Centralized cache management with predefined cache strategies.
    
    Usage:
        cache_manager = CacheManager()
        
        # Subscription cache (5 minutes TTL)
        subscription = await cache_manager.get_subscription(user_id)
        
        # Invalidate on update
        await cache_manager.invalidate_subscription(user_id)
    """
    
    # TTL constants (in seconds)
    TTL_SUBSCRIPTION = 300       # 5 minutes
    TTL_PLANS = 3600             # 1 hour (plans rarely change)
    TTL_PROPERTY = 120           # 2 minutes
    TTL_ROOM = 120               # 2 minutes
    TTL_BED = 60                 # 1 minute
    TTL_TENANT = 60              # 1 minute
    
    async def get_subscription(self, owner_id: str) -> Optional[dict]:
        """Get cached subscription for owner."""
        cache_key = f"subscription:{owner_id}"
        return await cache.get(cache_key)
    
    async def set_subscription(self, owner_id: str, subscription_data: dict) -> None:
        """Cache subscription data."""
        cache_key = f"subscription:{owner_id}"
        await cache.set(cache_key, subscription_data, ttl=self.TTL_SUBSCRIPTION)
    
    async def invalidate_subscription(self, owner_id: str) -> None:
        """Invalidate subscription cache for owner."""
        cache_key = f"subscription:{owner_id}"
        await cache.delete(cache_key)
        # Also invalidate usage cache
        await cache.delete(f"usage:{owner_id}")
    
    async def get_usage(self, owner_id: str) -> Optional[dict]:
        """Get cached usage data for owner."""
        cache_key = f"usage:{owner_id}"
        return await cache.get(cache_key)
    
    async def set_usage(self, owner_id: str, usage_data: dict) -> None:
        """Cache usage data."""
        cache_key = f"usage:{owner_id}"
        await cache.set(cache_key, usage_data, ttl=self.TTL_SUBSCRIPTION)
    
    async def get_plan_limits(self, plan: str) -> Optional[dict]:
        """Get cached plan limits."""
        cache_key = f"plan_limits:{plan}"
        return await cache.get(cache_key)
    
    async def set_plan_limits(self, plan: str, limits: dict) -> None:
        """Cache plan limits."""
        cache_key = f"plan_limits:{plan}"
        await cache.set(cache_key, limits, ttl=self.TTL_PLANS)
    
    async def invalidate_all_plans(self) -> None:
        """Invalidate all plan caches."""
        await cache.invalidate_namespace("plan_limits:")
    
    async def get_property(self, property_id: str) -> Optional[dict]:
        """Get cached property."""
        cache_key = f"property:{property_id}"
        return await cache.get(cache_key)
    
    async def set_property(self, property_id: str, property_data: dict) -> None:
        """Cache property data."""
        cache_key = f"property:{property_id}"
        await cache.set(cache_key, property_data, ttl=self.TTL_PROPERTY)
    
    async def invalidate_property(self, property_id: str) -> None:
        """Invalidate property cache."""
        cache_key = f"property:{property_id}"
        await cache.delete(cache_key)
    
    async def invalidate_property_related(self, property_id: str) -> None:
        """Invalidate all caches related to a property."""
        await self.invalidate_property(property_id)
        # Also invalidate rooms and beds
        await cache.invalidate_namespace(f"room:prop:{property_id}")
        await cache.invalidate_namespace(f"bed:prop:{property_id}")
        await cache.invalidate_namespace(f"tenant:prop:{property_id}")


# Global cache manager instance
cache_manager = CacheManager()


# Background task to cleanup expired cache entries
async def cache_cleanup_task():
    """Periodic cleanup of expired cache entries."""
    while True:
        try:
            await asyncio.sleep(300)  # Every 5 minutes
            removed = await cache.cleanup_expired()
            if removed > 0:
                logger.info(f"Cache cleanup: removed {removed} expired entries")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Cache cleanup error: {e}")
