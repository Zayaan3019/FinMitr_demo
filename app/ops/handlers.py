"""
Outbox event handlers.

Registering these is not optional bookkeeping. ``process_once`` parks an event
whose type has no handler, so an unregistered type means the work described by
that event silently never happens -- which is exactly the failure the outbox
pattern exists to prevent. Every ``event_type`` passed to
:func:`app.ops.outbox.enqueue` anywhere in the codebase must appear here, and
``tests/test_outbox.py::test_every_enqueued_event_type_has_a_handler`` fails if
one does not.

Handlers must be **idempotent**. Delivery is at-least-once: a relay that dies
between doing the work and marking the row processed will do the work again on
restart. Every handler below either performs a naturally idempotent write
(scoring anomalies with ``ON CONFLICT DO NOTHING``) or is a pure notification.

Handlers must also be **fast**. They run on the relay loop, so a slow handler
delays every other event behind it.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict

from app.core.logging import get_logger
from app.ops import outbox
from app.ops.audit import AuditAction, write_audit

logger = get_logger(__name__)


async def on_fi_ingested(payload: Dict[str, Any]) -> None:
    """
    Fresh bank data landed. Score it for anomalies.

    Deliberately deferred rather than done inline in the fetch handler: fitting
    a detector over a year of transactions takes seconds, and the AA is waiting
    on the HTTP response. Doing it here keeps the fetch fast while guaranteeing
    the scoring still happens -- the event committed alongside the transaction
    rows, so it cannot be lost.
    """
    user_id = uuid.UUID(payload["user_id"])
    inserted = int(payload.get("inserted") or 0)

    if inserted == 0:
        logger.info(
            f"fi.ingested for session {payload.get('session_id')} inserted no "
            f"new rows (replayed fetch); nothing to score"
        )
        return

    from app.api.endpoints import _load_transactions
    from app.db.session import tenant_session

    async with tenant_session(user_id) as session:
        df = await _load_transactions(session, months=12)
        if len(df) < 10:
            logger.info(
                f"Only {len(df)} transactions for user {user_id}; too few to fit "
                f"a detector. Skipping rather than reporting a meaningless score."
            )
            return

        import uuid as _uuid

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.db.models import Anomaly
        from app.ml.anomaly_eval import DETECTOR_VERSION, score_transactions

        scores = score_transactions(df)
        ranked = df.assign(score=scores).nlargest(20, "score")

        flagged = 0
        for _, row in ranked.iterrows():
            # ON CONFLICT DO NOTHING is what makes re-delivery harmless: the
            # same (transaction, detector version) pair scores once.
            result = await session.execute(
                pg_insert(Anomaly)
                .values(
                    id=_uuid.uuid4(),
                    txn_id=_uuid.UUID(row["id"]),
                    txn_date=row["txn_date"],
                    user_id=user_id,
                    score=float(row["score"]),
                    detector_version=DETECTOR_VERSION,
                )
                .on_conflict_do_nothing(constraint="uq_anomalies_txn_detector")
                .returning(Anomaly.id)
            )
            if result.scalar_one_or_none() is not None:
                flagged += 1

    logger.info(
        f"fi.ingested: scored {len(df)} transactions for user {user_id}, "
        f"{flagged} newly flagged"
    )


async def on_fi_ready(payload: Dict[str, Any]) -> None:
    """
    The AA says a session's data is available.

    Recorded, not acted on. The actual fetch is user-initiated via
    ``POST /aa/fi/fetch``: pulling automatically on a webhook would mean acting
    on an unauthenticated party's say-so about when to process someone's
    financial data, and the notification carries no proof the user is present.
    """
    await write_audit(
        AuditAction.FI_NOTIFICATION_RECEIVED,
        resource=f"fi_session:{payload.get('session_id')}",
        actor="account-aggregator",
        actor_user_id=uuid.UUID(payload["user_id"]) if payload.get("user_id") else None,
        after={"status": payload.get("status")},
    )


async def on_consent_status_changed(payload: Dict[str, Any]) -> None:
    """
    A consent changed state at the AA.

    The transition that matters is *away from* ACTIVE. Consent is the lawful
    basis for holding this data; once it is revoked, expired or paused, further
    fetching is unlawful processing. The local row is already updated by the
    webhook handler -- this records the transition durably so the history is
    reconstructable, and warns loudly on revocation.
    """
    user_id = uuid.UUID(payload["user_id"]) if payload.get("user_id") else None
    to_status = str(payload.get("to", "")).upper()

    if to_status in {"REVOKED", "EXPIRED", "PAUSED", "REJECTED"}:
        logger.warning(
            f"Consent {payload.get('consent_id')} moved "
            f"{payload.get('from')} -> {to_status}; no further FI fetch is "
            f"permitted against it"
        )

    await write_audit(
        AuditAction.CONSENT_STATUS_CHANGED,
        resource=f"consent:{payload.get('consent_id')}",
        actor="account-aggregator",
        actor_user_id=user_id,
        before={"status": payload.get("from")},
        after={"status": payload.get("to")},
    )


#: Every event type this application enqueues. The test suite cross-checks this
#: against a grep of ``enqueue(...)`` call sites, so adding an ``event_type``
#: without a handler fails CI rather than silently parking in production.
HANDLERS = {
    "fi.ingested": on_fi_ingested,
    "fi.ready": on_fi_ready,
    "consent.status_changed": on_consent_status_changed,
}


def register_all() -> None:
    """Register every handler. Called from the application lifespan."""
    for event_type, handler in HANDLERS.items():
        outbox.register_handler(event_type, handler)
