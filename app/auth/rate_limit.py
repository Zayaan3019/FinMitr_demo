"""
Auth-specific rate limiting (PHASE 1).

The general middleware limiter allows 60 req/min per client, which is right for
browsing but far too generous for a credential endpoint: 60 guesses a minute is
86,400 a day against a single account.

This limiter is applied only to ``/auth/*`` and is deliberately much harder:
10/min and 60/hour. It is keyed on **both** the client IP and the submitted
email, so an attacker rotating IPs against one account is throttled by the
email key, and an attacker spraying many accounts from one IP is throttled by
the IP key.
"""

from __future__ import annotations

import hashlib
import time
from typing import Optional, Tuple

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)

_PREFIX = "finguru:authrl:"


def _bucket_key(scope: str, identity: str, window: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    slot = int(time.time() // (60 if window == "m" else 3600))
    return f"{_PREFIX}{scope}:{window}:{digest}:{slot}"


async def _hit(scope: str, identity: str) -> Tuple[bool, int]:
    """
    Register one attempt. Returns ``(allowed, retry_after_seconds)``.

    Fixed windows rather than a sliding log: one INCR + one EXPIRE per check,
    with memory bounded by the number of active identities, which is what a
    login endpoint under attack needs.
    """
    client = await get_redis()

    minute_key = _bucket_key(scope, identity, "m")
    minute_count = await client.incrby(minute_key, 1)
    if minute_count == 1:
        await client.expire(minute_key, 120)
    if minute_count > settings.auth_rate_limit_per_minute:
        return False, 60 - int(time.time() % 60)

    hour_key = _bucket_key(scope, identity, "h")
    hour_count = await client.incrby(hour_key, 1)
    if hour_count == 1:
        await client.expire(hour_key, 7200)
    if hour_count > settings.auth_rate_limit_per_hour:
        return False, 3600 - int(time.time() % 3600)

    return True, 0


async def enforce_auth_rate_limit(
    ip_address: Optional[str], email: Optional[str] = None, scope: str = "auth"
) -> None:
    """Raise HTTP 429 when either the IP or the email bucket is exhausted."""
    checks = []
    if ip_address:
        checks.append(("ip", ip_address))
    if email:
        checks.append(("email", email.strip().lower()))

    for kind, identity in checks:
        allowed, retry_after = await _hit(f"{scope}:{kind}", identity)
        if not allowed:
            logger.warning(
                f"Auth rate limit hit: scope={scope} kind={kind} "
                f"identity={hashlib.sha256(identity.encode()).hexdigest()[:8]}"
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many authentication attempts. Please try again later.",
                headers={"Retry-After": str(max(1, retry_after))},
            )


async def reset_auth_rate_limits() -> None:
    """Test-support: clear all buckets."""
    client = await get_redis()
    await client.flushdb()
