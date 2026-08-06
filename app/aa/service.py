"""
Account Aggregator orchestration and ingestion (PHASE 3 + PHASE 6).

The full round trip:

    consent request -> (user approves at the AA) -> consent notification
      -> FI request -> FI notification -> FI fetch -> decrypt -> ingest

Ingestion is where PHASE 6 earns its place. An AA notification is delivered
at-least-once, and the AA will retry on any non-2xx or timeout. Three
mechanisms make that safe:

* ``transactions.dedupe_hash`` is UNIQUE, so re-ingesting the same fetch
  inserts nothing the second time -- enforced by the database, not by an
  application-level "have I seen this?" check that races under concurrency.
* the **outbox** couples the ingest to its downstream effects in one
  transaction, so a crash between the two cannot lose work.
* an **idempotency key** on the manual fetch endpoint means a client timeout
  and retry cannot start a second billed FI session.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.aa.client import AAError, ConsentNotLive, FIUClient, get_fiu_client
from app.aa.schemas import (
    AAAccountData,
    ConsentRequest,
    ConsentStatus,
    SessionStatus,
)
from app.core.logging import get_logger
from app.db.models import Account, Category, Consent, FiSession, Transaction
from app.db.session import system_session, tenant_session
from app.ml.categorizer import get_categorizer
from app.ops import outbox
from app.ops.audit import AuditAction, write_audit

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


async def create_consent(
    session: AsyncSession,
    user_id: uuid.UUID,
    request: ConsentRequest,
    actor: str,
    ip_address: Optional[str] = None,
    client: Optional[FIUClient] = None,
) -> Consent:
    """Request a consent artifact and persist it against the caller."""
    client = client or get_fiu_client()
    artifact = await client.request_consent(request)

    row = Consent(
        user_id=user_id,
        aa_consent_id=artifact.consent_id,
        aa_consent_handle=artifact.consent_handle,
        purpose_code=artifact.purpose_code,
        purpose_text=artifact.purpose_text,
        scope=request.scope(),
        expiry=artifact.consent_expiry,
        status=str(artifact.status),
        aa_provider=artifact.aa_provider,
    )
    session.add(row)
    await session.flush()

    await write_audit(
        AuditAction.CONSENT_REQUESTED,
        resource=f"consent:{artifact.consent_id}",
        actor=actor,
        actor_user_id=user_id,
        after={
            "purpose_code": artifact.purpose_code,
            "purpose_text": artifact.purpose_text,
            "scope": request.scope(),
            "expiry": artifact.consent_expiry.isoformat(),
            "aa_provider": artifact.aa_provider,
        },
        ip_address=ip_address,
    )
    return row


async def sync_consent_status(
    session: AsyncSession,
    user_id: uuid.UUID,
    consent_row: Consent,
    actor: str,
    client: Optional[FIUClient] = None,
) -> Consent:
    """
    Refresh our copy from the AA.

    The AA is the authority on consent state. A locally-cached ``ACTIVE`` that
    the user revoked an hour ago is not consent, and fetching on it would be
    unlawful processing.
    """
    client = client or get_fiu_client()
    try:
        artifact = await client.get_consent(consent_row.aa_consent_id)
    except (AAError, ConsentNotLive) as exc:
        logger.warning(f"Consent {consent_row.aa_consent_id} sync failed: {exc}")
        return consent_row

    previous = consent_row.status
    new_status = str(artifact.status)
    if previous != new_status:
        consent_row.status = new_status
        consent_row.expiry = artifact.consent_expiry
        await session.flush()

        action = {
            ConsentStatus.ACTIVE.value: AuditAction.CONSENT_ACTIVATED,
            ConsentStatus.REJECTED.value: AuditAction.CONSENT_REJECTED,
            ConsentStatus.REVOKED.value: AuditAction.CONSENT_REVOKED,
        }.get(new_status, AuditAction.CONSENT_REQUESTED)

        await write_audit(
            action,
            resource=f"consent:{consent_row.aa_consent_id}",
            actor=actor,
            actor_user_id=user_id,
            before={"status": previous},
            after={"status": new_status},
        )
    return consent_row


async def revoke_consent(
    session: AsyncSession,
    user_id: uuid.UUID,
    consent_row: Consent,
    actor: str,
    client: Optional[FIUClient] = None,
) -> bool:
    """Revoke at the AA and locally."""
    client = client or get_fiu_client()
    ok = await client.revoke_consent(consent_row.aa_consent_id)
    previous = consent_row.status
    consent_row.status = ConsentStatus.REVOKED.value
    await session.flush()

    await write_audit(
        AuditAction.CONSENT_REVOKED,
        resource=f"consent:{consent_row.aa_consent_id}",
        actor=actor,
        actor_user_id=user_id,
        before={"status": previous},
        after={"status": ConsentStatus.REVOKED.value, "aa_acknowledged": ok},
    )
    return ok


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


def compute_dedupe_hash(
    account_id: uuid.UUID, txn_date: date, amount_minor: int, narration: str
) -> str:
    """
    Stable identity for a transaction.

    Deliberately derived from the *content* rather than the FIP's ``txn_id``:
    FIPs are inconsistent about id stability across fetches, and a changed id
    on the same transaction would produce a duplicate ledger entry. Content
    hashing makes the same transaction the same row no matter how often it is
    fetched.
    """
    payload = f"{account_id}|{txn_date.isoformat()}|{amount_minor}|{narration.strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _category_index(session: AsyncSession) -> Dict[str, uuid.UUID]:
    rows = (await session.execute(select(Category.slug, Category.id))).all()
    return {slug: cid for slug, cid in rows}


async def upsert_account(
    session: AsyncSession, user_id: uuid.UUID, account: AAAccountData
) -> Account:
    """Find or create the local account row for a linked FIP account."""
    aa_handle = f"{account.fip_id}:{account.link_ref_number or account.masked_account_number}"
    existing = (
        await session.execute(
            select(Account).where(Account.user_id == user_id, Account.aa_handle == aa_handle)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = Account(
        user_id=user_id,
        aa_handle=aa_handle,
        # Only the masked form is ever persisted.
        masked_number=account.masked_account_number[:32],
        fip_id=account.fip_id[:64],
        type=account.account_type[:32],
        currency=account.currency[:3],
    )
    session.add(row)
    await session.flush()
    return row


async def ingest_accounts(
    session: AsyncSession,
    user_id: uuid.UUID,
    accounts: List[AAAccountData],
    fi_session_id: Optional[uuid.UUID] = None,
) -> Tuple[int, int, int]:
    """
    Persist decrypted transactions.

    Returns ``(accounts_linked, inserted, skipped_duplicates)``.

    Categorisation runs here, on the *unredacted* narration, using FinGuru's
    own local model -- the merchant string never leaves the boundary. The
    resulting ``model_version`` and ``confidence`` are written onto every row.
    """
    categories = await _category_index(session)
    categorizer = get_categorizer()

    linked = 0
    inserted = 0
    skipped = 0

    for account in accounts:
        account_row = await upsert_account(session, user_id, account)
        linked += 1

        narrations = [t.narration for t in account.transactions]
        predictions = categorizer.predict(narrations) if narrations else []

        # strict=True: silently dropping the tail of a bank fetch would
        # leave the ledger incomplete with nothing to indicate it.
        for txn, prediction in zip(account.transactions, predictions, strict=True):
            amount_minor = txn.amount_minor()
            if amount_minor == 0:
                continue  # `ck_transactions_amount_nonzero` would reject it

            dedupe = compute_dedupe_hash(account_row.id, txn.txn_date, amount_minor, txn.narration)
            category_id = categories.get(prediction.label)

            stmt = (
                pg_insert(Transaction)
                .values(
                    id=uuid.uuid4(),
                    txn_date=txn.txn_date,
                    account_id=account_row.id,
                    user_id=user_id,
                    amount_minor=amount_minor,
                    currency=account.currency[:3],
                    narration=txn.narration,
                    category_id=category_id,
                    model_version=prediction.model_version,
                    confidence=prediction.confidence,
                    dedupe_hash=dedupe,
                    source="aa",
                )
                # The database enforces idempotency. A webhook retry lands here
                # and inserts nothing.
                .on_conflict_do_nothing(constraint="uq_transactions_dedupe")
                .returning(Transaction.id)
            )
            result = await session.execute(stmt)
            if result.scalar_one_or_none() is not None:
                inserted += 1
            else:
                skipped += 1

    logger.info(
        f"Ingest for user {user_id}: {linked} account(s), "
        f"{inserted} inserted, {skipped} duplicate(s) skipped"
    )
    return linked, inserted, skipped


# ---------------------------------------------------------------------------
# FI session orchestration
# ---------------------------------------------------------------------------


async def run_fi_session(
    user_id: uuid.UUID,
    consent_id: str,
    months: int,
    actor: str,
    ip_address: Optional[str] = None,
    client: Optional[FIUClient] = None,
) -> Dict[str, Any]:
    """
    Execute a full FI fetch and ingest.

    Everything that touches tenant data runs inside a tenant session, so Row-
    Level Security applies to the ingest path exactly as it does to reads.
    """
    client = client or get_fiu_client()
    today = datetime.now(timezone.utc).date()
    from_date = today - timedelta(days=30 * months)

    async with tenant_session(user_id) as session:
        consent_row = (
            await session.execute(select(Consent).where(Consent.aa_consent_id == consent_id))
        ).scalar_one_or_none()

        if consent_row is None:
            # RLS already scoped this query, so "not found" covers both
            # "no such consent" and "someone else's consent" -- and the
            # response is identical, which is what stops it being an oracle.
            raise ConsentNotLive("No such consent for this user")

        await sync_consent_status(session, user_id, consent_row, actor, client)
        if consent_row.status != ConsentStatus.ACTIVE.value:
            raise ConsentNotLive(
                f"Consent is {consent_row.status}; the user must approve it at "
                "their Account Aggregator before data can be fetched"
            )
        if consent_row.expiry <= datetime.now(timezone.utc):
            consent_row.status = ConsentStatus.EXPIRED.value
            await session.flush()
            raise ConsentNotLive("Consent has expired")

        response = await client.start_fi_session(consent_id, from_date, today)

        fi_row = FiSession(
            user_id=user_id,
            consent_id=consent_row.id,
            aa_session_id=response.session_id,
            status=SessionStatus.PENDING.value,
            from_date=from_date,
            to_date=today,
        )
        session.add(fi_row)
        await session.flush()

    await write_audit(
        AuditAction.FI_FETCH_STARTED,
        resource=f"fi_session:{response.session_id}",
        actor=actor,
        actor_user_id=user_id,
        after={"consent_id": consent_id, "from": str(from_date), "to": str(today)},
        ip_address=ip_address,
    )

    try:
        accounts = await client.fetch_and_decrypt(response.session_id)
    except Exception as exc:
        async with tenant_session(user_id) as session:
            row = (
                await session.execute(
                    select(FiSession).where(FiSession.aa_session_id == response.session_id)
                )
            ).scalar_one_or_none()
            if row is not None:
                row.status = SessionStatus.FAILED.value
                row.error = str(exc)[:2000]
                row.completed_at = datetime.now(timezone.utc)
        await write_audit(
            AuditAction.FI_FETCH_FAILED,
            resource=f"fi_session:{response.session_id}",
            actor=actor,
            actor_user_id=user_id,
            after={"error": str(exc)[:400]},
            ip_address=ip_address,
        )
        raise

    async with tenant_session(user_id) as session:
        linked, inserted, skipped = await ingest_accounts(session, user_id, accounts)

        row = (
            await session.execute(
                select(FiSession).where(FiSession.aa_session_id == response.session_id)
            )
        ).scalar_one()
        row.status = SessionStatus.COMPLETED.value
        row.records_ingested = inserted
        row.completed_at = datetime.now(timezone.utc)

        # Outbox event and ingested rows commit together. A crash after this
        # point cannot lose the downstream work, and a replay cannot duplicate
        # it -- `dedupe_key` is UNIQUE.
        await outbox.enqueue(
            session,
            aggregate_type="fi_session",
            aggregate_id=response.session_id,
            event_type="fi.ingested",
            payload={
                "user_id": str(user_id),
                "session_id": response.session_id,
                "consent_id": consent_id,
                "accounts": linked,
                "inserted": inserted,
            },
            dedupe_key=f"fi.ingested:{response.session_id}",
        )

    await write_audit(
        AuditAction.FI_FETCH_COMPLETED,
        resource=f"fi_session:{response.session_id}",
        actor=actor,
        actor_user_id=user_id,
        after={
            "accounts_linked": linked,
            "transactions_ingested": inserted,
            "duplicates_skipped": skipped,
        },
        ip_address=ip_address,
    )

    return {
        "session_id": response.session_id,
        "status": SessionStatus.COMPLETED.value,
        "accounts_linked": linked,
        "transactions_ingested": inserted,
        "transactions_skipped_duplicate": skipped,
        "consent_id": consent_id,
    }


# ---------------------------------------------------------------------------
# Webhook resolution
# ---------------------------------------------------------------------------


async def resolve_session_owner(aa_session_id: str) -> Optional[uuid.UUID]:
    """
    Map an FI session id to its owner.

    Uses the narrow SECURITY DEFINER function created in migration 0001. A
    webhook has no authenticated user, so it cannot open a tenant session until
    it knows whose data this is -- and RLS hides the very row it needs. The
    function discloses exactly one UUID and nothing else.
    """
    async with system_session() as session:
        result = await session.execute(
            text("SELECT resolve_fi_session_owner(:sid)"), {"sid": aa_session_id}
        )
        value = result.scalar()
        return uuid.UUID(str(value)) if value else None


async def resolve_consent_owner(aa_consent_id: str) -> Optional[uuid.UUID]:
    """Map a consent id or handle to its owner (same rationale as above)."""
    async with system_session() as session:
        result = await session.execute(
            text("SELECT resolve_consent_owner(:cid)"), {"cid": aa_consent_id}
        )
        value = result.scalar()
        return uuid.UUID(str(value)) if value else None
