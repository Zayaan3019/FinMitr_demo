"""
Redis access with an in-process fallback.

Redis backs three things: the JWT ``jti`` denylist (server-side revocation),
the auth rate limiter, and per-user LLM token budgets. All three must keep
working in local development and CI where no Redis is running, so this module
degrades to an in-process implementation with identical semantics for a single
worker.

The fallback is explicitly *not* safe across processes -- ``settings.
redis_required = True`` makes a missing Redis a hard startup failure, which is
what production should set.
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional, Tuple

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

try:  # pragma: no cover - import guard
    import redis.asyncio as aioredis

    _REDIS_AVAILABLE = True
except Exception:  # pragma: no cover
    aioredis = None  # type: ignore
    _REDIS_AVAILABLE = False


class InMemoryKeyValue:
    """Minimal async subset of the Redis commands FinGuru actually uses."""

    def __init__(self) -> None:
        self._data: Dict[str, Tuple[str, Optional[float]]] = {}
        self._lock = asyncio.Lock()

    def _alive(self, key: str) -> Optional[str]:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and expires_at <= time.time():
            self._data.pop(key, None)
            return None
        return value

    async def get(self, key: str) -> Optional[str]:
        async with self._lock:
            return self._alive(key)

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        async with self._lock:
            self._data[key] = (str(value), time.time() + ex if ex else None)
            return True

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        return await self.set(key, value, ex=ttl)

    async def exists(self, key: str) -> int:
        async with self._lock:
            return 1 if self._alive(key) is not None else 0

    async def delete(self, *keys: str) -> int:
        async with self._lock:
            return sum(1 for k in keys if self._data.pop(k, None) is not None)

    async def incrby(self, key: str, amount: int = 1) -> int:
        async with self._lock:
            current = self._alive(key)
            _, expires_at = self._data.get(key, ("0", None))
            new = int(current or 0) + amount
            self._data[key] = (str(new), expires_at)
            return new

    async def incr(self, key: str) -> int:
        return await self.incrby(key, 1)

    async def expire(self, key: str, ttl: int) -> bool:
        async with self._lock:
            value = self._alive(key)
            if value is None:
                return False
            self._data[key] = (value, time.time() + ttl)
            return True

    async def ttl(self, key: str) -> int:
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return -2
            _, expires_at = entry
            if expires_at is None:
                return -1
            return max(0, int(expires_at - time.time()))

    async def ping(self) -> bool:
        return True

    async def flushdb(self) -> bool:
        async with self._lock:
            self._data.clear()
            return True

    async def aclose(self) -> None:
        self._data.clear()


_client = None
_is_fallback = False


async def get_redis():
    """Return the shared Redis client (or the in-process fallback)."""
    global _client, _is_fallback
    if _client is not None:
        return _client

    if _REDIS_AVAILABLE:
        try:
            candidate = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await candidate.ping()
            _client = candidate
            _is_fallback = False
            logger.info(f"Redis connected: {settings.redis_url}")
            return _client
        except Exception as exc:
            if settings.redis_required:
                raise RuntimeError(f"REDIS_REQUIRED is set but Redis is unreachable: {exc}")
            logger.warning(
                f"Redis unavailable ({exc}); falling back to in-process store. "
                "This is single-worker only -- set REDIS_REQUIRED=True in production."
            )
    elif settings.redis_required:
        raise RuntimeError("REDIS_REQUIRED is set but the redis package is not installed")

    _client = InMemoryKeyValue()
    _is_fallback = True
    return _client


def is_fallback() -> bool:
    """True when the in-process store is in use (surfaced on /health/detailed)."""
    return _is_fallback


async def ping_redis() -> bool:
    """
    True only when a *real* Redis answered.

    Deliberately distinct from ``get_redis()`` succeeding: the fallback always
    succeeds, so treating a live client as proof of connectivity would report
    the degraded single-worker mode as healthy. Startup and readiness need to
    tell those apart.
    """
    client = await get_redis()
    if _is_fallback:
        return False
    try:
        await client.ping()
        return True
    except Exception:
        return False


async def close_redis() -> None:
    global _client, _is_fallback
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:  # pragma: no cover
            # Best effort. This runs during shutdown; a connection that is
            # already broken is exactly the case where close() throws, and
            # re-raising here would mask whatever caused the shutdown.
            pass
    _client = None
    _is_fallback = False


async def reset_redis_for_tests() -> None:
    """Flush all keys. Test-support only."""
    client = await get_redis()
    await client.flushdb()
