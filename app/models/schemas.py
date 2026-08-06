"""
Request and response schemas for the FinGuru API.

**One rule governs this entire module: no schema carries a user identifier.**

The previous version of this file defined ``Transaction.user_id``,
``IngestRequest.user_id``, ``ChatRequest.user_id`` and ``ChatResponse.user_id``.
Those fields were the IDOR. A request body that names its own subject is a
request body that can name somebody else's, and nothing downstream was checking.

The subject of every request is now the bearer token, resolved by
:func:`app.auth.dependencies.get_current_user`. ``tests/test_phase0_idor.py``
walks the generated OpenAPI document and fails if any parameter or body property
anywhere in the application looks like a user identifier -- so this rule is
enforced by test, not by memory.

Money crosses the wire twice: ``amount`` in rupees for display, and
``amount_minor`` in paise as the authoritative integer. Storage and arithmetic
are always the integer; the float is a rendering.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


def _now() -> datetime:
    """Timezone-aware. ``datetime.utcnow()`` returns a naive value and is
    deprecated from Python 3.12."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


class TransactionCategory(str, Enum):
    """
    The label space of the trained categoriser.

    Kept in lockstep with :data:`app.ml.dataset.LABELS`; the enum exists so the
    OpenAPI document advertises the closed set rather than a bare string.
    ``tests/test_categorizer_metrics.py`` asserts the two agree.
    """

    GROCERIES = "groceries"
    DINING = "dining"
    TRANSPORT = "transport"
    UTILITIES = "utilities"
    RENT = "rent"
    SHOPPING = "shopping"
    ENTERTAINMENT = "entertainment"
    HEALTHCARE = "healthcare"
    SALARY = "salary"
    INVESTMENT = "investment"
    INSURANCE = "insurance"
    LOAN_EMI = "loan_emi"
    TRANSFER = "transfer"
    FEES_CHARGES = "fees_charges"

    # Not a model output. Assigned when the top softmax score falls below
    # `categorizer_confidence_floor`, so a low-confidence guess is surfaced as
    # "unknown" instead of being presented as a categorisation.
    UNCATEGORISED = "uncategorised"


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


class TransactionItem(BaseModel):
    """
    A single transaction as returned to its owner.

    No ``user_id``: the caller is the owner by construction. Emitting it would
    be redundant at best, and at worst an oracle for id enumeration.
    """

    id: str = Field(description="Server-assigned transaction UUID")
    txn_date: str = Field(description="Value date, ISO-8601 (YYYY-MM-DD)")
    amount: float = Field(
        description="Signed amount in rupees for display. Negative is an outflow."
    )
    amount_minor: int = Field(
        description="Signed amount in paise. This is the authoritative value; "
        "``amount`` is derived from it for display only."
    )
    narration: str = Field(description="Bank narration, verbatim")
    category: str = Field(description="Predicted category slug")
    model_version: Optional[str] = Field(
        default=None,
        description="Registry version of the model that assigned the category. "
        "Persisted per row so a prediction can always be traced to the "
        "artefact that produced it.",
    )
    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Softmax confidence of the label"
    )

    model_config = {
        "protected_namespaces": (),
        "json_schema_extra": {
            "example": {
                "id": "0a3f1c2e-6b7d-4f10-9a2c-5e8b1d4f7a03",
                "txn_date": "2026-03-14",
                "amount": -412.0,
                "amount_minor": -41200,
                "narration": "UPI/P2M/417293856201/SWIGGY",
                "category": "food_delivery",
                "model_version": "transaction-categoriser@2026-03-01T09-12-44Z",
                "confidence": 0.94,
            }
        },
    }


class IngestResponse(BaseModel):
    """Outcome of a CSV import."""

    success: bool
    message: str
    transactions_ingested: int = Field(description="Rows newly written")
    transactions_skipped_duplicate: int = Field(
        description="Rows rejected by the dedupe constraint or with a zero "
        "amount. Re-importing the same file yields zero ingested and is "
        "therefore safe."
    )
    processing_time_seconds: float


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


class SummaryResponse(BaseModel):
    """Aggregate view of the caller's own ledger. All money in rupees."""

    transactions: int
    expenses: int
    income: int
    date_range_start: str
    date_range_end: str
    total_income: float
    total_expenses: float = Field(description="Absolute value of outflows")
    net_cashflow: float = Field(description="Signed: income minus expenses")
    largest_expense: float
    category_breakdown: Dict[str, float] = Field(
        description="Absolute outflow per category, largest first"
    )
    monthly_trend: Dict[str, float] = Field(description="Signed net cashflow keyed by YYYY-MM")


class BudgetResponse(BaseModel):
    """Suggested per-category monthly budgets."""

    months_analysed: int
    categories: Dict[str, Dict[str, float]] = Field(
        description="Per category: median_monthly, max_monthly, "
        "suggested_budget, months_observed"
    )
    total_suggested_monthly_budget: float
    method: str = Field(
        description="Stated in the payload so the number is never mistaken for "
        "an opaque model output"
    )


class AnomalyItem(BaseModel):
    """
    One flagged transaction.

    ``score`` ranks; it is not a probability. Read it against the precision@k
    and base rate reported by ``scripts/evaluate_anomalies.py`` -- a count of
    anomalies on its own is not a metric.
    """

    transaction_id: str
    txn_date: str
    amount: float
    narration: str
    category: str
    score: float = Field(description="Isolation Forest score; higher is more unusual")
    detector_version: str


# ---------------------------------------------------------------------------
# Advisory chat
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """
    A question about the caller's own finances.

    The former ``user_id`` field is gone. ``query`` is attacker-controlled text
    and is treated as data throughout: fenced by nonce-delimited tags and
    scanned for injection before it reaches the model.
    """

    query: str = Field(min_length=5, max_length=1000, description="The question")
    months: int = Field(default=6, ge=1, le=24, description="How far back to draw context from")

    @field_validator("query")
    @classmethod
    def _strip(cls, v: str) -> str:
        stripped = v.strip()
        if len(stripped) < 5:
            raise ValueError("Query must be at least 5 characters")
        return stripped

    model_config = {
        "json_schema_extra": {
            "example": {
                "query": "Where did most of my money go last quarter?",
                "months": 3,
            }
        }
    }


class ChatResponse(BaseModel):
    """
    A grounded advisory answer.

    Every claim in ``answer`` is tied to at least one id in
    ``grounded_transaction_ids``; an ungrounded response is rejected by
    :func:`app.llm.schema_guard.enforce_grounding` before it gets here.
    """

    answer: str = Field(description="Markdown, re-hydrated after redaction")
    grounded_transaction_ids: List[str] = Field(
        description="Transaction UUIDs the answer actually cites"
    )
    suspicious_narrations: List[str] = Field(
        default_factory=list,
        description="Narrations that tripped the prompt-injection scanner. "
        "Surfaced rather than hidden: an injection attempt in a merchant name "
        "is itself a finding worth showing the user.",
    )
    context_transactions: int
    redactions: Dict[str, int] = Field(
        default_factory=dict,
        description="Count of PII entities replaced by type before egress",
    )
    degraded: bool = Field(
        default=False,
        description="True when the answer was computed locally instead of by "
        "the model -- budget exhausted, provider unavailable, or output that "
        "failed schema or grounding validation.",
    )
    degraded_reason: Optional[str] = None
    processing_time_seconds: float


# ---------------------------------------------------------------------------
# Account lifecycle and operations
# ---------------------------------------------------------------------------


class DeleteAccountResponse(BaseModel):
    """Result of the DPDP erasure request. The subject is always the caller."""

    success: bool
    message: str
    deleted: Dict[str, int] = Field(description="Rows removed, by table")


class HealthResponse(BaseModel):
    """Liveness summary. Unauthenticated, so it discloses no user data."""

    status: str = Field(description="healthy | degraded")
    version: str
    timestamp: datetime = Field(default_factory=_now)
    components: Dict[str, str]


class ErrorResponse(BaseModel):
    """
    Standard error envelope.

    ``detail`` is never populated from an exception string in production --
    see :mod:`app.core.error_handling`, which maps internal failures to generic
    text so stack traces and SQL fragments cannot leak.
    """

    success: bool = False
    error: str
    detail: Optional[str] = None
    request_id: Optional[str] = Field(
        default=None, description="Correlates the response with the server log line"
    )
    timestamp: datetime = Field(default_factory=_now)


__all__ = [
    "TransactionCategory",
    "TransactionItem",
    "IngestResponse",
    "SummaryResponse",
    "BudgetResponse",
    "AnomalyItem",
    "ChatRequest",
    "ChatResponse",
    "DeleteAccountResponse",
    "HealthResponse",
    "ErrorResponse",
]
