"""
Per-user LLM token budgets (PHASE 5).

Two distinct risks, one control:

* **Cost.** An authenticated user (or a compromised client) can loop the chat
  endpoint. Rate limiting bounds requests per minute; it does not bound
  *tokens*, and one request with a large retrieved context can cost as much as
  fifty small ones.
* **Data exfiltration volume.** Every call ships redacted context to a third
  party. A hard daily ceiling caps how much of a user's financial history can
  leave the boundary in a day, whatever the caller does.

Budgets are per user per UTC day, held in Redis with a TTL that lands on the
day boundary, so the key expires itself and there is no reset job to fail.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Tuple

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)

_PREFIX = "finguru:llm:budget:"


class BudgetExceeded(Exception):
    """The user's daily token allowance is exhausted."""

    def __init__(self, used: int, limit: int, resets_in_seconds: int):
        super().__init__(
            f"Daily LLM token budget exhausted ({used}/{limit}). "
            f"Resets in {resets_in_seconds // 3600}h {(resets_in_seconds % 3600) // 60}m."
        )
        self.used = used
        self.limit = limit
        self.resets_in_seconds = resets_in_seconds


def _key(user_id: uuid.UUID) -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{_PREFIX}{day}:{user_id}"


def _seconds_to_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


def estimate_tokens(text: str) -> int:
    """
    Cheap token estimate (~4 characters per token).

    Deliberately an estimate: the budget is a guard rail, and paying for a
    tokeniser round trip on every pre-flight check would cost more than the
    precision is worth. Actual usage is reconciled by :func:`record_usage`
    when the provider reports it.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


async def check_budget(user_id: uuid.UUID, estimated_tokens: int) -> Tuple[int, int]:
    """
    Pre-flight check. Returns ``(used, limit)``; raises :class:`BudgetExceeded`.

    Checked *before* the call, so an over-budget user never causes provider
    spend.
    """
    limit = settings.llm_user_daily_token_budget
    if limit <= 0:
        return 0, 0

    client = await get_redis()
    used = int(await client.get(_key(user_id)) or 0)

    if used + estimated_tokens > limit:
        raise BudgetExceeded(used, limit, _seconds_to_utc_midnight())
    return used, limit


async def record_usage(user_id: uuid.UUID, tokens: int) -> int:
    """Add actual usage to the day's counter. Returns the new total."""
    if tokens <= 0:
        return 0
    client = await get_redis()
    key = _key(user_id)
    total = await client.incrby(key, int(tokens))
    if total == tokens:  # first write of the day
        await client.expire(key, _seconds_to_utc_midnight())
    return total


async def get_usage(user_id: uuid.UUID) -> dict:
    """Current budget state, for /me and for the metrics endpoint."""
    client = await get_redis()
    used = int(await client.get(_key(user_id)) or 0)
    limit = settings.llm_user_daily_token_budget
    return {
        "used_tokens": used,
        "limit_tokens": limit,
        "remaining_tokens": max(0, limit - used),
        "resets_in_seconds": _seconds_to_utc_midnight(),
    }


async def reset_budget(user_id: uuid.UUID) -> None:
    """Test-support / admin override."""
    client = await get_redis()
    await client.delete(_key(user_id))
