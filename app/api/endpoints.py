"""
FinGuru application endpoints.

**PHASE 0 -- what changed and why.**

This module previously exposed::

    POST   /analyze/budget/{user_id}
    GET    /analyze/summary/{user_id}
    POST   /analyze/forecast/{user_id}
    DELETE /user/{user_id}
    POST   /ingest?user_id=...
    POST   /chat            { "user_id": ..., "query": ... }

Every one of those took the user identifier straight from the client, with no
authentication behind it. Passing someone else's id returned their complete
financial history; ``DELETE /user/{user_id}`` erased their account. That is
IDOR -- OWASP API Security #1 -- and in a financial application it is the most
serious class of bug there is.

Every route below now derives the user from a verified bearer token via
:func:`app.auth.dependencies.get_current_user`. There is no path, query or body
parameter anywhere in this application that carries a user identifier, and
``tests/test_phase0_idor.py`` proves it by walking the live OpenAPI schema
rather than by inspection.

Defence is layered, because "we remembered to check" is not an architecture:

1. the token is the only source of ``user_id``;
2. every query runs on a session bound to that user, so PostgreSQL Row-Level
   Security filters foreign rows at the SQL layer even if a handler forgets a
   ``WHERE`` clause.
"""

from __future__ import annotations

import io
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import denylist
from app.auth.dependencies import Principal, client_ip, get_current_user, get_tenant_session
from app.auth.service import logout_everywhere
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Account, Anomaly, Category, Consent, FiSession, Transaction
from app.db.session import system_session
from app.llm import budget as llm_budget
from app.llm.safe_client import TransactionContext, get_safe_llm_client
from app.llm.budget import BudgetExceeded
from app.llm.redaction import PIIEgressError
from app.ml.anomaly_eval import DETECTOR_VERSION, score_transactions
from app.ml.categorizer import get_categorizer
from app.models.schemas import (
    AnomalyItem,
    BudgetResponse,
    ChatRequest,
    ChatResponse,
    DeleteAccountResponse,
    HealthResponse,
    IngestResponse,
    SummaryResponse,
    TransactionItem,
)
from app.ops.audit import AuditAction, write_audit
from app.ops.idempotency import run_idempotent

logger = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_transactions(
    session: AsyncSession, months: int = 12, limit: int = 5000
) -> pd.DataFrame:
    """
    Load the caller's transactions.

    Note the absence of a ``user_id`` filter. It is not an oversight: the
    session is bound to the caller and RLS applies the predicate. Written this
    way on purpose -- if the isolation ever regressed, this query would start
    returning other users' rows and `tests/test_rls_isolation.py` would fail
    immediately.
    """
    since = datetime.now(timezone.utc).date() - timedelta(days=30 * months)
    rows = (
        await session.execute(
            select(
                Transaction.id,
                Transaction.txn_date,
                Transaction.amount_minor,
                Transaction.narration,
                Transaction.category_id,
                Transaction.model_version,
                Transaction.confidence,
                Transaction.account_id,
                Category.slug,
            )
            .join(Category, Category.id == Transaction.category_id, isouter=True)
            .where(Transaction.txn_date >= since)
            .order_by(Transaction.txn_date.desc())
            .limit(limit)
        )
    ).all()

    if not rows:
        return pd.DataFrame(
            columns=[
                "id",
                "txn_date",
                "amount_minor",
                "narration",
                "category_id",
                "model_version",
                "confidence",
                "account_id",
                "category",
            ]
        )

    return pd.DataFrame(
        [
            {
                "id": str(r[0]),
                "txn_date": r[1],
                "amount_minor": int(r[2]),
                "narration": r[3],
                "category_id": str(r[4]) if r[4] else None,
                "model_version": r[5],
                "confidence": r[6],
                "account_id": str(r[7]),
                "category": r[8] or "uncategorised",
            }
            for r in rows
        ]
    )


def _rupees(minor: int) -> float:
    """Render paise as rupees. Storage stays integer; only display divides."""
    return round(minor / 100.0, 2)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check() -> HealthResponse:
    """Unauthenticated liveness summary. Discloses no user data."""
    from app.core.redis_client import is_fallback
    from app.db.session import check_database_health
    from app.ml.registry import get_registry

    components: Dict[str, str] = {}

    db_health = await check_database_health()
    components["database"] = db_health["status"]

    components["redis"] = "in-process-fallback" if is_fallback() else "operational"

    active = get_registry().active_version("transaction-categoriser")
    components["categoriser"] = active or "not-registered (rule fallback)"
    components["aa_transport"] = "mock" if settings.aa_use_mock_transport else settings.aa_provider

    healthy = db_health["status"] == "healthy"
    return HealthResponse(
        status="healthy" if healthy else "degraded",
        version=settings.app_version,
        components=components,
    )


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


@router.get(
    "/transactions",
    response_model=List[TransactionItem],
    summary="List the caller's transactions",
)
async def list_transactions(
    principal: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    months: int = Query(default=6, ge=1, le=24),
    limit: int = Query(default=200, ge=1, le=1000),
) -> List[TransactionItem]:
    df = await _load_transactions(session, months=months, limit=limit)

    await write_audit(
        AuditAction.DATA_ACCESSED,
        resource=f"transactions:{principal.user_id}",
        actor=principal.email,
        actor_user_id=principal.user_id,
        after={"rows": len(df), "months": months},
    )

    return [
        TransactionItem(
            id=row["id"],
            txn_date=str(row["txn_date"]),
            amount=_rupees(row["amount_minor"]),
            amount_minor=row["amount_minor"],
            narration=row["narration"],
            category=row["category"],
            model_version=row["model_version"],
            confidence=row["confidence"],
        )
        for _, row in df.iterrows()
    ]


@router.post(
    "/transactions/import",
    response_model=IngestResponse,
    summary="Import transactions from a CSV file",
    description=(
        "Ingests a CSV into the caller's own ledger. The account is derived "
        "from the token; there is no way to import into someone else's."
    ),
)
async def import_csv(
    request: Request,
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> IngestResponse:
    started = time.time()

    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")

    contents = await file.read()
    if len(contents) / (1024 * 1024) > settings.max_file_size_mb:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {settings.max_file_size_mb}MB limit",
        )

    try:
        df = pd.read_csv(io.BytesIO(contents))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid CSV: {exc}")

    required = {"date", "amount", "description"}
    missing = required - set(df.columns)
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required columns: {sorted(missing)}")

    try:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {exc}")

    async def _operation():
        from app.aa.service import compute_dedupe_hash

        # A synthetic account for manually imported data, kept distinct from
        # AA-linked accounts so provenance stays visible in the ledger.
        account = (
            await session.execute(select(Account).where(Account.aa_handle == "manual:csv-import"))
        ).scalar_one_or_none()
        if account is None:
            account = Account(
                user_id=principal.user_id,
                aa_handle="manual:csv-import",
                masked_number="MANUAL",
                type="IMPORT",
                currency="INR",
            )
            session.add(account)
            await session.flush()

        categories = {
            slug: cid
            for slug, cid in (await session.execute(select(Category.slug, Category.id))).all()
        }
        categorizer = get_categorizer()
        narrations = df["description"].astype(str).tolist()
        predictions = categorizer.predict(narrations)

        inserted = skipped = 0
        from decimal import Decimal, ROUND_HALF_UP

        # strict=True: a short `predictions` list would otherwise silently
        # truncate the import and drop the user's remaining transactions
        # without an error anywhere. Loud failure is correct here -- the
        # alternative is a ledger that is quietly incomplete.
        for (_, row), prediction in zip(df.iterrows(), predictions, strict=True):
            # Parsed via Decimal: float("2599.99") * 100 is 259998.99999999997.
            amount_minor = int(
                (Decimal(str(row["amount"])) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            if amount_minor == 0:
                skipped += 1
                continue

            narration = str(row["description"])
            dedupe = compute_dedupe_hash(account.id, row["date"], amount_minor, narration)
            stmt = (
                pg_insert(Transaction)
                .values(
                    id=uuid.uuid4(),
                    txn_date=row["date"],
                    account_id=account.id,
                    user_id=principal.user_id,
                    amount_minor=amount_minor,
                    currency="INR",
                    narration=narration,
                    category_id=categories.get(prediction.label),
                    model_version=prediction.model_version,
                    confidence=prediction.confidence,
                    dedupe_hash=dedupe,
                    source="csv",
                )
                .on_conflict_do_nothing(constraint="uq_transactions_dedupe")
                .returning(Transaction.id)
            )
            if (await session.execute(stmt)).scalar_one_or_none() is not None:
                inserted += 1
            else:
                skipped += 1

        await write_audit(
            AuditAction.DATA_INGESTED,
            resource=f"transactions:{principal.user_id}",
            actor=principal.email,
            actor_user_id=principal.user_id,
            after={"inserted": inserted, "skipped": skipped, "source": "csv"},
            ip_address=client_ip(request),
        )

        return 200, {
            "success": True,
            "message": f"Imported {inserted} transaction(s)",
            "transactions_ingested": inserted,
            "transactions_skipped_duplicate": skipped,
            "processing_time_seconds": round(time.time() - started, 3),
        }

    _code, body = await run_idempotent(
        key=idempotency_key,
        scope="transactions.import",
        request_payload={"filename": file.filename, "size": len(contents)},
        operation=_operation,
        user_id=principal.user_id,
    )
    return IngestResponse(**body)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


@router.get(
    "/analysis/summary",
    response_model=SummaryResponse,
    summary="Financial summary for the caller",
)
async def financial_summary(
    principal: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    months: int = Query(default=6, ge=1, le=24),
) -> SummaryResponse:
    df = await _load_transactions(session, months=months)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail="No transactions found. Link an account via /api/v1/aa or "
            "import a CSV via /api/v1/transactions/import.",
        )

    expenses = df[df["amount_minor"] < 0]
    income = df[df["amount_minor"] > 0]

    by_category = (
        expenses.groupby("category")["amount_minor"].sum().abs().sort_values(ascending=False)
    )
    monthly = (
        df.assign(month=pd.to_datetime(df["txn_date"]).dt.to_period("M").astype(str))
        .groupby("month")["amount_minor"]
        .sum()
    )

    await write_audit(
        AuditAction.DATA_ACCESSED,
        resource=f"summary:{principal.user_id}",
        actor=principal.email,
        actor_user_id=principal.user_id,
        after={"months": months, "rows": len(df)},
    )

    return SummaryResponse(
        transactions=len(df),
        expenses=len(expenses),
        income=len(income),
        date_range_start=str(df["txn_date"].min()),
        date_range_end=str(df["txn_date"].max()),
        total_income=_rupees(int(income["amount_minor"].sum())),
        total_expenses=_rupees(int(abs(expenses["amount_minor"].sum()))),
        net_cashflow=_rupees(int(df["amount_minor"].sum())),
        largest_expense=_rupees(int(abs(expenses["amount_minor"].min()))) if len(expenses) else 0.0,
        category_breakdown={k: _rupees(int(v)) for k, v in by_category.head(12).items()},
        monthly_trend={k: _rupees(int(v)) for k, v in monthly.tail(12).items()},
    )


@router.get(
    "/analysis/budget",
    response_model=BudgetResponse,
    summary="Suggested monthly budgets for the caller",
)
async def budget(
    principal: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    months: int = Query(default=6, ge=2, le=24),
) -> BudgetResponse:
    df = await _load_transactions(session, months=months)
    if df.empty:
        raise HTTPException(status_code=404, detail="No transactions found")

    expenses = df[df["amount_minor"] < 0].copy()
    if expenses.empty:
        raise HTTPException(status_code=404, detail="No expenses to budget against")

    expenses["month"] = pd.to_datetime(expenses["txn_date"]).dt.to_period("M").astype(str)
    monthly_by_category = (
        expenses.groupby(["category", "month"])["amount_minor"].sum().abs().reset_index()
    )

    suggestions: Dict[str, Dict[str, float]] = {}
    for category, group in monthly_by_category.groupby("category"):
        values = group["amount_minor"]
        median = float(values.median())
        # Median, not mean: one Diwali splurge should not permanently inflate
        # the budget for the whole year.
        suggestions[str(category)] = {
            "median_monthly": _rupees(int(median)),
            "max_monthly": _rupees(int(values.max())),
            "suggested_budget": _rupees(int(median * 1.1)),
            "months_observed": int(len(values)),
        }

    total = sum(v["suggested_budget"] for v in suggestions.values())
    return BudgetResponse(
        months_analysed=months,
        categories=suggestions,
        total_suggested_monthly_budget=round(total, 2),
        method="median monthly spend per category, plus a 10% buffer",
    )


@router.get(
    "/analysis/anomalies",
    response_model=List[AnomalyItem],
    summary="Highest-scoring unusual transactions",
    description=(
        "Ranks transactions by an Isolation Forest score. The count alone is "
        "not a metric -- see scripts/evaluate_anomalies.py for precision@k "
        "against a labelled window and the base rate it must be read against."
    ),
)
async def anomalies(
    principal: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    months: int = Query(default=6, ge=1, le=24),
    top_k: int = Query(default=10, ge=1, le=100),
) -> List[AnomalyItem]:
    df = await _load_transactions(session, months=months)
    if len(df) < 10:
        raise HTTPException(
            status_code=422,
            detail="At least 10 transactions are required to fit a detector",
        )

    scores = score_transactions(df)
    df = df.assign(score=scores).sort_values("score", ascending=False).head(top_k)

    for _, row in df.iterrows():
        stmt = (
            pg_insert(Anomaly)
            .values(
                id=uuid.uuid4(),
                txn_id=uuid.UUID(row["id"]),
                txn_date=row["txn_date"],
                user_id=principal.user_id,
                score=float(row["score"]),
                detector_version=DETECTOR_VERSION,
            )
            .on_conflict_do_nothing(constraint="uq_anomalies_txn_detector")
        )
        await session.execute(stmt)

    return [
        AnomalyItem(
            transaction_id=row["id"],
            txn_date=str(row["txn_date"]),
            amount=_rupees(row["amount_minor"]),
            narration=row["narration"],
            category=row["category"],
            score=round(float(row["score"]), 4),
            detector_version=DETECTOR_VERSION,
        )
        for _, row in df.iterrows()
    ]


# ---------------------------------------------------------------------------
# Advisory chat
# ---------------------------------------------------------------------------


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Ask the advisor a question about your finances",
    description=(
        "Runs the guarded LLM pipeline: budget check -> PII redaction -> egress "
        "assertion -> injection-fenced prompt -> schema validation -> grounding "
        "check -> re-hydration. No account number or merchant identifier "
        "reaches the model provider."
    ),
)
async def chat(
    payload: ChatRequest,
    principal: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> ChatResponse:
    started = time.time()
    df = await _load_transactions(session, months=payload.months)

    if df.empty:
        raise HTTPException(
            status_code=404,
            detail="No transactions found. Link an account or import a CSV first.",
        )

    contexts = [
        TransactionContext(
            ref=f"T{i + 1}",
            txn_date=str(row["txn_date"]),
            amount_minor=int(row["amount_minor"]),
            narration=str(row["narration"]),
            category=str(row["category"]),
        )
        for i, (_, row) in enumerate(df.head(settings.llm_max_context_transactions).iterrows())
    ]
    ref_to_id = {c.ref: str(df.iloc[i]["id"]) for i, c in enumerate(contexts)}

    expenses = df[df["amount_minor"] < 0]
    insights = {
        "transactions_analysed": len(df),
        "total_outflow_rupees": _rupees(int(abs(expenses["amount_minor"].sum()))),
        "total_inflow_rupees": _rupees(int(df[df["amount_minor"] > 0]["amount_minor"].sum())),
        "top_categories": {
            str(k): _rupees(int(v))
            for k, v in expenses.groupby("category")["amount_minor"].sum().abs().nlargest(6).items()
        },
    }

    try:
        result = await get_safe_llm_client().advise(
            user_id=principal.user_id,
            query=payload.query,
            transactions=contexts,
            insights=insights,
        )
    except BudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except PIIEgressError as exc:
        # Fail closed. The request is refused rather than sent unredacted.
        logger.error(f"Chat blocked by PII egress guard: {exc}")
        raise HTTPException(
            status_code=500,
            detail="The request was blocked before reaching the model provider "
            "because sensitive data could not be fully redacted.",
        )

    return ChatResponse(
        answer=result.answer_markdown,
        grounded_transaction_ids=[ref_to_id.get(r, r) for r in result.grounded_refs],
        suspicious_narrations=result.suspicious_narrations,
        context_transactions=len(contexts),
        redactions=result.redaction_counts,
        degraded=result.degraded,
        degraded_reason=result.degraded_reason,
        processing_time_seconds=round(time.time() - started, 3),
    )


@router.get("/chat/budget", summary="Remaining daily LLM token budget")
async def chat_budget(
    principal: Principal = Depends(get_current_user),
) -> Dict[str, Any]:
    return await llm_budget.get_usage(principal.user_id)


# ---------------------------------------------------------------------------
# Account deletion (DPDP right to erasure)
# ---------------------------------------------------------------------------


@router.delete(
    "/me",
    response_model=DeleteAccountResponse,
    summary="Delete the caller's own account and all data",
    description=(
        "Erases the caller's account. Replaces the former "
        "`DELETE /user/{user_id}`, which let anyone delete anyone. There is no "
        "way to name a different user: the subject is the bearer token."
    ),
)
async def delete_me(
    request: Request,
    principal: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> DeleteAccountResponse:
    counts: Dict[str, int] = {}

    for label, model in [
        ("anomalies", Anomaly),
        ("transactions", Transaction),
        ("fi_sessions", FiSession),
        ("consents", Consent),
        ("accounts", Account),
    ]:
        result = await session.execute(delete(model).returning(model.id))
        counts[label] = len(result.fetchall())

    # Commit before touching `users`, and the ordering is not optional.
    #
    # The deletes above hold row locks on `accounts`, `transactions` and
    # friends until this transaction ends. Every one of those tables has a
    # foreign key to `users`, so deleting the user row requires PostgreSQL to
    # take a conflicting lock to validate the constraint. Opening a *second*
    # connection to do that while the first still holds the locks is a
    # deadlock against ourselves: the second connection waits for a transaction
    # that cannot commit until the handler returns, and the request hangs until
    # the client times out.
    #
    # Found by running the flow end to end -- it never showed up in a unit
    # test, because a single-session test does not reproduce the two-connection
    # interleaving.
    await session.commit()

    await write_audit(
        AuditAction.DATA_DELETED,
        resource=f"user:{principal.user_id}",
        actor=principal.email,
        actor_user_id=principal.user_id,
        after=counts,
        ip_address=client_ip(request),
    )

    # Kill every live session, then remove the identity row itself. The audit
    # record above survives -- audit_log is append-only and is the evidence
    # that the erasure happened.
    async with system_session() as sys_session:
        from app.db.models import User

        await logout_everywhere(sys_session, principal.user_id, "account_deleted")
        await sys_session.execute(delete(User).where(User.id == principal.user_id))

    await denylist.revoke_user(
        principal.user_id, settings.access_token_ttl_seconds, "account_deleted"
    )

    return DeleteAccountResponse(
        success=True,
        message="Account and all associated financial data have been deleted",
        deleted=counts,
    )


# ---------------------------------------------------------------------------
# System statistics (no per-user data)
# ---------------------------------------------------------------------------


@router.get("/stats", summary="Aggregate, non-identifying system statistics")
async def statistics(
    principal: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
) -> Dict[str, Any]:
    """
    Deliberately scoped to the caller.

    The previous ``/stats`` returned a global document count. Even a count is a
    disclosure in a single-tenant-per-user product, so this reports only what
    RLS lets the caller see.
    """
    txn_count = (await session.execute(select(func.count(Transaction.id)))).scalar() or 0
    account_count = (await session.execute(select(func.count(Account.id)))).scalar() or 0
    consent_count = (await session.execute(select(func.count(Consent.id)))).scalar() or 0

    from app.ml.registry import get_registry

    return {
        "transactions": int(txn_count),
        "linked_accounts": int(account_count),
        "consents": int(consent_count),
        "active_categoriser": get_registry().active_version("transaction-categoriser"),
        "llm_budget": await llm_budget.get_usage(principal.user_id),
    }
