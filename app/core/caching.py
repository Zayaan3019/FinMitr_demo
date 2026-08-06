"""
Advanced caching module with TTL, LRU eviction, and in-memory storage.
Improves performance by reducing redundant computations and API calls.
"""

from typing import Any, Optional, Dict, Callable
from datetime import datetime, timedelta
from collections import OrderedDict
import hashlib
import json
import functools
import asyncio

from app.core.logging import get_logger

logger = get_logger(__name__)


class CacheEntry:
    """Represents a single cache entry with metadata."""

    def __init__(self, value: Any, ttl_seconds: int):
        self.value = value
        self.created_at = datetime.now()
        self.expires_at = self.created_at + timedelta(seconds=ttl_seconds)
        self.access_count = 0
        self.last_accessed = self.created_at

    def is_expired(self) -> bool:
        """Check if cache entry has expired."""
        return datetime.now() > self.expires_at

    def access(self) -> Any:
        """Record access and return value."""
        self.access_count += 1
        self.last_accessed = datetime.now()
        return self.value


class LRUCache:
    """
    LRU (Least Recently Used) cache with TTL support.
    Thread-safe in-memory cache implementation.
    """

    def __init__(self, max_size: int = 1000, default_ttl: int = 300):
        """
        Initialize LRU cache.

        Args:
            max_size: Maximum number of entries
            default_ttl: Default time-to-live in seconds
        """
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()

        # Statistics
        self.stats = {"hits": 0, "misses": 0, "evictions": 0, "expired": 0, "sets": 0}

        logger.info(f"LRU Cache initialized: max_size={max_size}, default_ttl={default_ttl}s")

    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/expired
        """
        async with self._lock:
            if key not in self.cache:
                self.stats["misses"] += 1
                return None

            entry = self.cache[key]

            # Check expiration
            if entry.is_expired():
                del self.cache[key]
                self.stats["expired"] += 1
                self.stats["misses"] += 1
                return None

            # Move to end (most recently used)
            self.cache.move_to_end(key)

            self.stats["hits"] += 1
            return entry.access()

    async def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (uses default if None)
        """
        async with self._lock:
            ttl = ttl or self.default_ttl

            # Remove existing entry if present
            if key in self.cache:
                del self.cache[key]

            # Evict oldest if at capacity
            if len(self.cache) >= self.max_size:
                oldest_key = next(iter(self.cache))
                del self.cache[oldest_key]
                self.stats["evictions"] += 1

            # Add new entry
            self.cache[key] = CacheEntry(value, ttl)
            self.stats["sets"] += 1

    async def delete(self, key: str) -> bool:
        """
        Delete entry from cache.

        Args:
            key: Cache key

        Returns:
            True if key existed and was deleted
        """
        async with self._lock:
            if key in self.cache:
                del self.cache[key]
                return True
            return False

    async def clear(self):
        """Clear all cache entries."""
        async with self._lock:
            self.cache.clear()
            logger.info("Cache cleared")

    async def cleanup_expired(self):
        """Remove all expired entries."""
        async with self._lock:
            expired_keys = [key for key, entry in self.cache.items() if entry.is_expired()]

            for key in expired_keys:
                del self.cache[key]
                self.stats["expired"] += 1

            if expired_keys:
                logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total_requests = self.stats["hits"] + self.stats["misses"]
        hit_rate = (self.stats["hits"] / total_requests * 100) if total_requests > 0 else 0

        return {
            **self.stats,
            "size": len(self.cache),
            "max_size": self.max_size,
            "hit_rate": round(hit_rate, 2),
            "total_requests": total_requests,
        }

    def get_info(self) -> Dict[str, Any]:
        """Get detailed cache information."""
        stats = self.get_stats()

        # Get entry details
        entries_info = []
        for key, entry in list(self.cache.items())[:10]:  # Show top 10
            entries_info.append(
                {
                    "key": key[:50] + "..." if len(key) > 50 else key,
                    "created_at": entry.created_at.isoformat(),
                    "expires_at": entry.expires_at.isoformat(),
                    "access_count": entry.access_count,
                    "last_accessed": entry.last_accessed.isoformat(),
                }
            )

        return {"stats": stats, "sample_entries": entries_info}


# Global cache instance
_global_cache: Optional[LRUCache] = None


def get_cache() -> LRUCache:
    """Get the global cache instance."""
    global _global_cache

    if _global_cache is None:
        _global_cache = LRUCache(max_size=1000, default_ttl=300)

    return _global_cache


def generate_cache_key(*args, **kwargs) -> str:
    """
    Generate a cache key from function arguments.

    Args:
        *args: Positional arguments
        **kwargs: Keyword arguments

    Returns:
        SHA256 hash of serialized arguments
    """
    try:
        # Create a deterministic string representation
        key_data = {"args": args, "kwargs": kwargs}

        # Serialize to JSON (sorted keys for consistency)
        key_str = json.dumps(key_data, sort_keys=True, default=str)

        # Hash for fixed length and privacy
        return hashlib.sha256(key_str.encode()).hexdigest()

    except Exception as e:
        logger.warning(f"Failed to generate cache key: {e}")
        # Fallback to simple string representation
        return hashlib.sha256(str((args, kwargs)).encode()).hexdigest()


def cached(ttl: int = 300, key_prefix: str = ""):
    """
    Decorator for caching function results.

    Args:
        ttl: Time-to-live in seconds
        key_prefix: Prefix for cache keys

    Example:
        @cached(ttl=600, key_prefix="user_data")
        async def get_user_data(user_id: str):
            # Expensive operation
            return data
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            cache = get_cache()

            # Generate cache key
            cache_key = f"{key_prefix}:{func.__name__}:{generate_cache_key(*args, **kwargs)}"

            # Try to get from cache
            cached_value = await cache.get(cache_key)
            if cached_value is not None:
                logger.debug(f"Cache HIT: {func.__name__}")
                return cached_value

            # Execute function
            logger.debug(f"Cache MISS: {func.__name__}")
            result = await func(*args, **kwargs)

            # Store in cache
            await cache.set(cache_key, result, ttl)

            return result

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            # For synchronous functions, use asyncio.run
            cache = get_cache()

            # Generate cache key
            cache_key = f"{key_prefix}:{func.__name__}:{generate_cache_key(*args, **kwargs)}"

            # Try to get from cache (synchronous)
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            cached_value = loop.run_until_complete(cache.get(cache_key))

            if cached_value is not None:
                logger.debug(f"Cache HIT: {func.__name__}")
                return cached_value

            # Execute function
            logger.debug(f"Cache MISS: {func.__name__}")
            result = func(*args, **kwargs)

            # Store in cache
            loop.run_until_complete(cache.set(cache_key, result, ttl))

            return result

        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


async def start_cache_cleanup_task():
    """Background task to periodically clean up expired cache entries."""
    cache = get_cache()

    while True:
        try:
            await asyncio.sleep(60)  # Run every minute
            await cache.cleanup_expired()
        except Exception as e:
            logger.error(f"Cache cleanup task error: {e}")


# Specialized caches for different data types
class QueryCache(LRUCache):
    """Cache specifically for user queries and responses."""

    def __init__(self):
        super().__init__(max_size=500, default_ttl=600)  # 10 minutes


class TransactionCache(LRUCache):
    """Cache for transaction data."""

    def __init__(self):
        super().__init__(max_size=200, default_ttl=300)  # 5 minutes


class AnalysisCache(LRUCache):
    """Cache for analysis results (anomalies, forecasts, etc.)."""

    def __init__(self):
        super().__init__(max_size=100, default_ttl=1800)  # 30 minutes
