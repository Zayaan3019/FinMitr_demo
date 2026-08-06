"""
Transactional outbox (PHASE 6).

The problem it solves: an AA webhook says "session 123 has data ready". The
handler fetches, writes transactions, and then wants to trigger downstream work
(re-categorise, re-run anomaly detection, notify the user). If the process dies
between the database commit and the dispatch, the work is silently lost; if it
dispatches first and then the commit fails, the work runs against data that
does not exist.

The outbox removes the gap by making the dispatch *part of the same
transaction*: the event row and the transaction rows commit together or not at
all. A separate relay then drains the table.

Delivery is at-least-once, never exactly-once -- that is not achievable across
a database and a network. Consumers are made idempotent instead:
``outbox_events.dedupe_key`` is UNIQUE, so a replayed webhook enqueues nothing
new, and ``transactions.dedupe_hash`` is UNIQUE, so a replayed *fetch* ingests
nothing new. Those two constraints are what make a webhook retry safe.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import OutboxEvent
from app.db.session import system_session

logger = get_logger(__name__)

# Registered handlers, keyed by event_type.
_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]] = {}

MAX_ATTEMPTS = 8


def register_handler(event_type: str, handler: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
    """Bind an async handler to an event type."""
    _HANDLERS[event_type] = handler
    logger.info(f"Outbox handler registered for '{event_type}'")


async def enqueue(
    session: AsyncSession,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    payload: Dict[str, Any],
    dedupe_key: Optional[str] = None,
    delay_seconds: int = 0,
) -> bool:
    """
    Append an event **inside the caller's transaction**.

    Returns False when ``dedupe_key`` already exists -- i.e. this is a replay
    and the event is already queued or processed. ``ON CONFLICT DO NOTHING``
    keeps that path from aborting the caller's transaction, which a raw
    IntegrityError would.
    """
    key = dedupe_key or f"{event_type}:{aggregate_id}"
    available_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)

    stmt = (
        pg_insert(OutboxEvent)
        .values(
            id=uuid.uuid4(),
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            event_type=event_type,
            payload=payload,
            dedupe_key=key[:128],
            available_at=available_at,
        )
        .on_conflict_do_nothing(index_elements=[OutboxEvent.dedupe_key])
        .returning(OutboxEvent.id)
    )
    result = await session.execute(stmt)
    inserted = result.scalar_one_or_none() is not None
    if not inserted:
        logger.info(f"Outbox event '{key}' already queued; replay suppressed")
    return inserted


async def claim_batch(session: AsyncSession, limit: int = 50) -> List[OutboxEvent]:
    """
    Claim due events for this worker.

    ``FOR UPDATE SKIP LOCKED`` is what makes the relay horizontally scalable:
    concurrent workers take disjoint batches without blocking each other and
    without a distributed lock.
    """
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(OutboxEvent)
        .where(OutboxEvent.processed_at.is_(None), OutboxEvent.available_at <= now)
        .order_by(OutboxEvent.available_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(result.scalars().all())


def _PARKED_UNTIL() -> datetime:
    """
    Far-future availability: the row stops being claimable but is not deleted.

    Parking rather than deleting keeps the evidence. An event that could not be
    delivered is exactly the thing someone will need to look at, and a DELETE
    would leave only a log line.
    """
    return datetime.now(timezone.utc) + timedelta(days=3650)


async def process_once(limit: int = 50) -> Dict[str, int]:
    """Drain one batch. Returns counts for observability."""
    stats = {"claimed": 0, "processed": 0, "failed": 0, "skipped": 0}

    async with system_session() as session:
        events = await claim_batch(session, limit)
        stats["claimed"] = len(events)

        for event in events:
            handler = _HANDLERS.get(event.event_type)
            if handler is None:
                # Park it rather than leaving it queued.
                #
                # A missing handler is a deployment fact, not a transient
                # failure: retrying in two seconds will find it just as
                # missing. Leaving the row claimable made the relay re-select
                # and re-log the same events on every iteration -- a hot loop
                # that produced a warning line every two seconds per event,
                # forever, and kept the queue depth metric permanently non-zero
                # so it could never be used for alerting.
                #
                # Parked rows stay in the table for inspection and can be
                # released by hand once a handler is registered.
                event.attempts = (event.attempts or 0) + 1
                event.last_error = f"no handler registered for '{event.event_type}'"
                event.available_at = _PARKED_UNTIL()
                logger.warning(
                    f"No handler for outbox event '{event.event_type}' "
                    f"(id {event.id}); parked for inspection"
                )
                stats["skipped"] += 1
                continue

            try:
                await handler(dict(event.payload or {}))
                event.processed_at = datetime.now(timezone.utc)
                event.last_error = None
                stats["processed"] += 1
            except Exception as exc:
                event.attempts = (event.attempts or 0) + 1
                event.last_error = str(exc)[:2000]
                if event.attempts >= MAX_ATTEMPTS:
                    # Park it: stop retrying, keep the row for inspection.
                    event.available_at = _PARKED_UNTIL()
                    logger.error(
                        f"Outbox event {event.id} exhausted {MAX_ATTEMPTS} attempts; "
                        f"parked for manual review: {exc}"
                    )
                else:
                    # Exponential backoff with a ceiling.
                    backoff = min(300, 2**event.attempts)
                    event.available_at = datetime.now(timezone.utc) + timedelta(seconds=backoff)
                    logger.warning(
                        f"Outbox event {event.id} attempt {event.attempts} failed "
                        f"({exc}); retrying in {backoff}s"
                    )
                stats["failed"] += 1

    return stats


async def relay_loop(interval_seconds: float = 2.0) -> None:
    """Background relay. Started from the FastAPI lifespan."""
    logger.info("Outbox relay started")
    while True:
        try:
            stats = await process_once()
            if stats["processed"] or stats["failed"]:
                logger.info(f"Outbox relay: {stats}")
        except asyncio.CancelledError:
            logger.info("Outbox relay stopped")
            raise
        except Exception as exc:
            logger.error(f"Outbox relay iteration failed: {exc}", exc_info=True)
        await asyncio.sleep(interval_seconds)


async def pending_count() -> int:
    """Depth of the queue -- a useful alerting signal."""
    async with system_session() as session:
        result = await session.execute(
            select(OutboxEvent.id).where(OutboxEvent.processed_at.is_(None))
        )
        return len(result.fetchall())


async def mark_all_processed_for_tests() -> None:
    """Test-support helper."""
    async with system_session() as session:
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.processed_at.is_(None))
            .values(processed_at=datetime.now(timezone.utc))
        )
