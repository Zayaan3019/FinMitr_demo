"""
Server-side revocation of access tokens via a Redis ``jti`` denylist (PHASE 1).

Stateless JWTs are fast but cannot be withdrawn, which is unacceptable for a
financial application: "log out everywhere" and "we detected token theft" both
have to take effect now, not in fifteen minutes.

Each denylist entry is stored with a TTL equal to the token's remaining
lifetime. After that the token is rejected by expiry anyway, so the entry is
pure overhead and Redis reclaims it automatically. Steady-state size is
therefore bounded by (revocations per 15 minutes), not by total tokens issued.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)

_JTI_PREFIX = "finguru:denylist:jti:"
# Session-level kill switch: revokes every access token minted for a session,
# including ones whose jti we never saw.
_SESSION_PREFIX = "finguru:denylist:sid:"
_USER_PREFIX = "finguru:denylist:uid:"


def _ttl_from(expires_at: datetime) -> int:
    remaining = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    return max(1, remaining)


async def revoke_jti(jti: str, expires_at: datetime, reason: str = "logout") -> None:
    """Deny a single access token until it would have expired."""
    client = await get_redis()
    await client.set(_JTI_PREFIX + jti, reason, ex=_ttl_from(expires_at))
    logger.info(f"Access token {jti[:8]}... denylisted ({reason})")


async def revoke_session(
    session_id: uuid.UUID, expires_at: datetime, reason: str = "logout"
) -> None:
    """Deny every access token carrying this session id."""
    client = await get_redis()
    await client.set(_SESSION_PREFIX + str(session_id), reason, ex=_ttl_from(expires_at))


async def revoke_user(user_id: uuid.UUID, ttl_seconds: int, reason: str) -> None:
    """
    Deny every access token issued to a user *before now*.

    Stores a cut-off timestamp rather than a boolean, so tokens minted after
    the revocation (e.g. the fresh pair from a password change) still work.
    """
    client = await get_redis()
    cutoff = int(datetime.now(timezone.utc).timestamp())
    await client.set(_USER_PREFIX + str(user_id), str(cutoff), ex=max(1, ttl_seconds))
    logger.warning(f"All access tokens for user {user_id} revoked ({reason})")


async def is_revoked(
    jti: str,
    session_id: Optional[uuid.UUID] = None,
    user_id: Optional[uuid.UUID] = None,
    issued_at: Optional[datetime] = None,
) -> bool:
    """True when any of the three denylist scopes covers this token."""
    client = await get_redis()

    if await client.exists(_JTI_PREFIX + jti):
        return True

    if session_id is not None and await client.exists(_SESSION_PREFIX + str(session_id)):
        return True

    if user_id is not None:
        cutoff = await client.get(_USER_PREFIX + str(user_id))
        if cutoff is not None:
            if issued_at is None:
                return True
            if issued_at.timestamp() <= float(cutoff):
                return True

    return False


async def clear_user_revocation(user_id: uuid.UUID) -> None:
    """Lift a user-wide revocation (used by tests and admin tooling)."""
    client = await get_redis()
    await client.delete(_USER_PREFIX + str(user_id))
