"""
Outbox and idempotency (PHASE 6).

The property under test is the one the pattern exists for: **a webhook retry
cannot double-ingest.** Delivery is at-least-once by construction -- a relay
that dies between doing the work and marking the row processed will redo it --
so correctness rests on the consumers being idempotent, not on the delivery
being exact.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# Handler coverage
# ---------------------------------------------------------------------------


def test_every_enqueued_event_type_has_a_handler():
    """
    A missing handler is a silent failure, so it must be a loud one here.

    ``process_once`` parks any event whose type is unregistered. That is the
    right runtime behaviour -- retrying a missing handler will not find it --
    but it means the downstream work described by that event simply never
    happens, with nothing but a log line to say so. This test greps the source
    for every ``event_type=`` passed to ``enqueue`` and requires a handler for
    each.

    It caught ``fi.ingested``, which had no handler at all: anomaly scoring
    after a bank fetch never ran, and the relay re-selected the same rows every
    two seconds forever.
    """
    from app.ops.handlers import HANDLERS

    enqueued: set[str] = set()
    for path in Path("app").rglob("*.py"):
        if path.name in {"outbox.py", "handlers.py"}:
            continue
        source = path.read_text(encoding="utf-8")
        enqueued.update(re.findall(r'event_type=["\']([\w.]+)["\']', source))

    assert enqueued, "found no enqueue call sites -- the grep pattern is broken"

    missing = enqueued - set(HANDLERS)
    assert not missing, (
        f"these event types are enqueued but have no handler, so their work "
        f"never runs: {sorted(missing)}"
    )


async def test_an_unhandled_event_is_parked_not_retried_forever(clean_redis):
    """
    Regression test for a hot loop.

    An unregistered type used to be left claimable, so the relay re-selected
    and re-logged it on every 2-second iteration indefinitely -- and the queue
    depth never returned to zero, making it useless as an alerting signal.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.db.models import OutboxEvent
    from app.db.session import system_session
    from app.ops import outbox

    key = f"test.unhandled:{uuid.uuid4().hex}"
    async with system_session() as session:
        await outbox.enqueue(
            session,
            aggregate_type="test",
            aggregate_id=uuid.uuid4().hex,
            event_type="test.no.such.handler",
            payload={"x": 1},
            dedupe_key=key,
        )
        await session.commit()

    # Drain rather than assume a single pass reaches our row. `claim_batch`
    # takes 50 rows at a time, and earlier tests in the suite leave their own
    # events queued -- so a fixed number of passes makes this order-dependent.
    for _ in range(20):
        stats = await outbox.process_once()
        if stats["claimed"] == 0:
            break
    else:
        pytest.fail("outbox never drained; process_once is not making progress")

    async with system_session() as session:
        row = (
            await session.execute(select(OutboxEvent).where(OutboxEvent.dedupe_key == key))
        ).scalar_one()

    parked_horizon = datetime.now(timezone.utc) + timedelta(days=365 * 5)
    assert row.available_at > parked_horizon, (
        f"unhandled event is claimable again at {row.available_at}; it must be "
        f"parked, not retried"
    )
    assert row.last_error and "no handler" in row.last_error
    assert row.processed_at is None, "a parked event must not be marked processed"

    # And the drained queue stays drained: nothing is claimable any more.
    assert (await outbox.process_once())[
        "claimed"
    ] == 0, "the relay re-claimed a parked event -- this is the hot loop returning"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


async def test_enqueueing_the_same_dedupe_key_twice_inserts_once():
    """
    The AA retries when it does not see a fast 200. Without this, one webhook
    delivered three times becomes three downstream jobs.
    """
    from app.db.session import system_session
    from app.ops import outbox

    key = f"test.dupe:{uuid.uuid4().hex}"

    async with system_session() as session:
        first = await outbox.enqueue(
            session,
            aggregate_type="test",
            aggregate_id="a",
            event_type="fi.ready",
            payload={"n": 1},
            dedupe_key=key,
        )
        second = await outbox.enqueue(
            session,
            aggregate_type="test",
            aggregate_id="a",
            event_type="fi.ready",
            payload={"n": 2},
            dedupe_key=key,
        )
        await session.commit()

    assert first is True, "the first enqueue did not insert"
    assert second is False, "a duplicate dedupe_key was inserted a second time"


async def test_a_duplicate_enqueue_does_not_abort_the_caller_transaction(alice):
    """
    ``ON CONFLICT DO NOTHING``, not a caught ``IntegrityError``.

    A raw integrity violation poisons the PostgreSQL transaction: every
    subsequent statement fails until rollback. Since ``enqueue`` runs *inside*
    the caller's transaction -- that is the entire point of the outbox -- a
    replayed webhook would take the transaction's real work down with it.
    """
    from sqlalchemy import text

    from app.db.session import system_session
    from app.ops import outbox

    key = f"test.abort:{uuid.uuid4().hex}"

    async with system_session() as session:
        await outbox.enqueue(
            session,
            aggregate_type="test",
            aggregate_id="a",
            event_type="fi.ready",
            payload={},
            dedupe_key=key,
        )
        await outbox.enqueue(
            session,
            aggregate_type="test",
            aggregate_id="a",
            event_type="fi.ready",
            payload={},
            dedupe_key=key,
        )
        # If the duplicate had aborted the transaction, this raises.
        alive = (await session.execute(text("SELECT 1"))).scalar()
        await session.commit()

    assert alive == 1, "the transaction was aborted by the duplicate enqueue"


# ---------------------------------------------------------------------------
# Idempotency keys
# ---------------------------------------------------------------------------


async def test_the_same_idempotency_key_runs_the_operation_once(alice):
    """A retried request replays the stored response instead of acting again."""
    from app.ops.idempotency import run_idempotent

    calls = []

    async def operation():
        calls.append(1)
        return 200, {"ran": len(calls)}

    key = uuid.uuid4().hex
    payload = {"amount": 100}

    first = await run_idempotent(
        key=key,
        scope="test",
        request_payload=payload,
        operation=operation,
        user_id=alice.user_id,
    )
    second = await run_idempotent(
        key=key,
        scope="test",
        request_payload=payload,
        operation=operation,
        user_id=alice.user_id,
    )

    assert len(calls) == 1, f"the operation ran {len(calls)} times"
    assert first == second, f"replay returned a different response: {first} vs {second}"


async def test_reusing_a_key_with_a_different_body_is_rejected(alice):
    """
    Key reuse with a changed payload is a client bug or an attack, never a
    retry. Replaying the old response would hide the mismatch; running the new
    body under the old key would defeat the guarantee. So it is refused.
    """
    from app.ops.idempotency import run_idempotent

    async def operation():
        return 200, {"ok": True}

    key = uuid.uuid4().hex
    await run_idempotent(
        key=key,
        scope="test",
        request_payload={"amount": 100},
        operation=operation,
        user_id=alice.user_id,
    )

    with pytest.raises(Exception) as exc_info:
        await run_idempotent(
            key=key,
            scope="test",
            request_payload={"amount": 999},
            operation=operation,
            user_id=alice.user_id,
        )

    assert "422" in str(exc_info.value) or "mismatch" in str(exc_info.value).lower(), (
        f"key reuse with a different body was not rejected as a mismatch: " f"{exc_info.value}"
    )


async def test_no_key_means_no_idempotency_and_that_is_explicit(alice):
    """
    A missing ``Idempotency-Key`` runs the operation normally rather than
    failing. Requiring the header would break every simple client; the
    guarantee is opt-in, and its absence is not silently treated as present.
    """
    from app.ops.idempotency import run_idempotent

    calls = []

    async def operation():
        calls.append(1)
        return 200, {"ran": len(calls)}

    for _ in range(2):
        await run_idempotent(
            key=None,
            scope="test",
            request_payload={},
            operation=operation,
            user_id=alice.user_id,
        )

    assert len(calls) == 2, "operations without a key must not be deduplicated"
