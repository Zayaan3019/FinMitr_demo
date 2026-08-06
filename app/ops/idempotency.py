"""
Idempotency keys for mutating endpoints (PHASE 6).

A client that times out on ``POST /aa/fi-requests`` cannot know whether the
request landed. Retrying without a key risks a duplicate fetch (and duplicate
charges, in a payments context); not retrying risks losing the operation. An
``Idempotency-Key`` header lets the client retry safely: the second call
returns the first call's stored response instead of executing anything.

Two subtleties this implementation handles:

* **Concurrency.** The UNIQUE constraint on ``key`` is the lock. Two racing
  replays both INSERT; exactly one wins and executes, the loser waits and
  replays the winner's response.
* **Key reuse with a different body.** Returning the old response for a
  *different* request would silently drop the new one, so the request body is
  hashed and a mismatch is a 422, per the IETF idempotency-key draft.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import IdempotencyKey
from app.db.session import system_session

logger = get_logger(__name__)

RETENTION_HOURS = 24
# How long to wait for a concurrent in-flight request holding the same key.
WAIT_TIMEOUT_SECONDS = 10.0
WAIT_POLL_SECONDS = 0.25


def hash_request(payload: Any) -> str:
    """Stable hash of a request body (key order must not matter)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _claim(
    session: AsyncSession,
    key: str,
    scope: str,
    request_hash: str,
    user_id: Optional[uuid.UUID],
) -> Tuple[bool, Optional[IdempotencyKey]]:
    """Try to claim the key. Returns ``(we_own_it, existing_row)``."""
    stmt = (
        pg_insert(IdempotencyKey)
        .values(
            id=uuid.uuid4(),
            key=key,
            user_id=user_id,
            scope=scope,
            request_hash=request_hash,
        )
        .on_conflict_do_nothing(index_elements=[IdempotencyKey.key])
        .returning(IdempotencyKey.id)
    )
    claimed = (await session.execute(stmt)).scalar_one_or_none() is not None
    await session.commit()

    existing = (
        await session.execute(select(IdempotencyKey).where(IdempotencyKey.key == key))
    ).scalar_one_or_none()
    return claimed, existing


async def run_idempotent(
    key: Optional[str],
    scope: str,
    request_payload: Any,
    operation: Callable[[], Awaitable[Tuple[int, Dict[str, Any]]]],
    user_id: Optional[uuid.UUID] = None,
) -> Tuple[int, Dict[str, Any]]:
    """
    Execute ``operation`` at most once per ``key``.

    When ``key`` is None the operation simply runs -- idempotency is opt-in,
    because forcing it on every caller would break legitimate repeat actions.
    """
    if not key:
        return await operation()

    key = key.strip()[:128]
    request_hash = hash_request(request_payload)

    async with system_session() as session:
        owned, existing = await _claim(session, key, scope, request_hash, user_id)

    if not owned and existing is not None:
        if existing.request_hash != request_hash:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Idempotency-Key was already used with a different request "
                    "body. Use a fresh key for a different request."
                ),
            )
        if existing.completed_at is not None:
            logger.info(f"Idempotent replay of '{scope}' key={key[:12]}...")
            return existing.status_code or 200, dict(existing.response_body or {})

        # A concurrent request owns the key and is still running.
        replayed = await _await_completion(key)
        if replayed is not None:
            return replayed
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A request with this Idempotency-Key is still in progress",
        )

    # We own the key -- execute for real.
    try:
        status_code, body = await operation()
    except HTTPException:
        # Client errors must not be cached: the caller may legitimately retry
        # the same key after fixing an upstream problem.
        await _release(key)
        raise
    except Exception:
        await _release(key)
        raise

    async with system_session() as session:
        row = (
            await session.execute(select(IdempotencyKey).where(IdempotencyKey.key == key))
        ).scalar_one_or_none()
        if row is not None:
            row.status_code = status_code
            row.response_body = body
            row.completed_at = datetime.now(timezone.utc)

    return status_code, body


async def _await_completion(key: str) -> Optional[Tuple[int, Dict[str, Any]]]:
    """Poll briefly for the concurrent owner to finish."""
    deadline = asyncio.get_event_loop().time() + WAIT_TIMEOUT_SECONDS
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(WAIT_POLL_SECONDS)
        async with system_session() as session:
            row = (
                await session.execute(select(IdempotencyKey).where(IdempotencyKey.key == key))
            ).scalar_one_or_none()
        if row is None:
            return None
        if row.completed_at is not None:
            return row.status_code or 200, dict(row.response_body or {})
    return None


async def _release(key: str) -> None:
    """Drop a claimed-but-failed key so the client can retry it."""
    async with system_session() as session:
        await session.execute(delete(IdempotencyKey).where(IdempotencyKey.key == key))


async def purge_expired(retention_hours: int = RETENTION_HOURS) -> int:
    """Delete keys older than the retention window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
    async with system_session() as session:
        result = await session.execute(
            delete(IdempotencyKey)
            .where(IdempotencyKey.created_at < cutoff)
            .returning(IdempotencyKey.id)
        )
        return len(result.fetchall())
