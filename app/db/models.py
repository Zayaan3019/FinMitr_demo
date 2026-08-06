"""
Relational schema for FinGuru (PHASE 2).

Design notes that matter, and why:

* **Money is ``amount_minor BIGINT``** -- paise, never rupees, never float.
  ``0.1 + 0.2 != 0.3`` in IEEE-754; a ledger that sums floats will disagree
  with itself. BIGINT paise covers +/- 9.2e16 paise (~Rs 92 trillion) which is
  comfortably beyond any personal-finance use.

* **``transactions`` is RANGE-partitioned on ``txn_date``.** Access is
  overwhelmingly time-clustered ("last 90 days", "this month"), so partition
  pruning turns most queries into a single-partition scan, and retiring old
  data becomes ``DETACH PARTITION`` (metadata-only) instead of a mass DELETE
  plus VACUUM. PostgreSQL requires the partition key to be part of every
  unique constraint, hence the composite ``(id, txn_date)`` primary key.

* **``user_id`` is denormalised onto ``transactions`` and ``anomalies``.**
  Row-Level Security predicates must be cheap and must not require a join to
  ``accounts`` on every row. Correctness is preserved by the composite foreign
  key ``(account_id, user_id) -> accounts(id, user_id)``: it is not possible to
  attach a transaction to an account owned by a different user.

* **RLS, not application filtering.** Every tenant table carries a policy
  keyed on the ``app.current_user_id`` session GUC. This makes the IDOR class
  structurally impossible: even a query that forgets its ``WHERE user_id = ...``
  returns zero foreign rows.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow

# ===========================================================================
# Identity
# ===========================================================================


class User(Base):
    """A FinGuru account holder."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Case-insensitive email. Stored already-normalised (lowercased, trimmed)
    # with a plain UNIQUE constraint -- deterministic and index-friendly,
    # without depending on the citext extension being installed.
    email_ci: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    email_display: Mapped[str] = mapped_column(String(320), nullable=False)
    argon2_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # AES-256-GCM ciphertext of the TOTP seed; never the seed itself.
    mfa_secret_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    mfa_confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    accounts: Mapped[List["Account"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("email_ci = lower(email_ci)", name="email_ci_lowercase"),
        Index("ix_users_locked_until", "locked_until"),
    )


class RefreshToken(Base):
    """
    One node of a refresh-token *family* (PHASE 1).

    Rotation: every use issues a new token and marks the presented one used.
    Reuse detection: presenting a token whose ``used_at`` is already set proves
    either theft or a replay, and there is no way to tell which -- so the whole
    ``family_id`` is revoked and the session dies. This is the standard OAuth
    2.1 / BCP recommendation for public clients.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    user_agent: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(INET, nullable=True)

    __table_args__ = (
        Index("ix_refresh_tokens_family_id", "family_id"),
        Index("ix_refresh_tokens_user_id", "user_id"),
        # Expired-token sweeper reads only live rows.
        Index(
            "ix_refresh_tokens_live",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )


# ===========================================================================
# Financial data
# ===========================================================================


class Category(Base):
    """Self-referencing category hierarchy (``Food`` -> ``Dining out``)."""

    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    is_income: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    parent: Mapped[Optional["Category"]] = relationship(
        remote_side="Category.id", back_populates="children"
    )
    children: Mapped[List["Category"]] = relationship(back_populates="parent")

    __table_args__ = (Index("ix_categories_parent_id", "parent_id"),)


class Account(Base):
    """A bank account linked through the Account Aggregator framework."""

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    aa_handle: Mapped[str] = mapped_column(String(128), nullable=False)
    # Only the last four digits are ever persisted. The full number never
    # enters FinGuru's storage or its LLM prompts.
    masked_number: Mapped[str] = mapped_column(String(32), nullable=False)
    fip_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="SAVINGS")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    user: Mapped["User"] = relationship(back_populates="accounts")

    __table_args__ = (
        # Required so `transactions` can carry a composite FK that pins the
        # denormalised user_id to the true owner of the account.
        UniqueConstraint("id", "user_id", name="uq_accounts_id_user_id"),
        UniqueConstraint("user_id", "aa_handle", name="uq_accounts_user_id_aa_handle"),
        Index("ix_accounts_user_id", "user_id"),
        CheckConstraint("char_length(currency) = 3", name="currency_iso4217"),
    )


class Transaction(Base):
    """
    A single ledger entry. RANGE-partitioned by month on ``txn_date``.

    ``amount_minor`` is signed: negative is money out, positive is money in.
    """

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Part of the primary key because PostgreSQL requires the partition key in
    # every unique index on a partitioned table.
    txn_date: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False)

    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")
    narration: Mapped[str] = mapped_column(Text, nullable=False)

    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    # Persisted so "why was this categorised that way in March?" is answerable
    # three model releases later.
    model_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Stable hash of (account, date, amount, narration). UNIQUE, so a webhook
    # retry that replays the same FI fetch cannot double-ingest.
    dedupe_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="aa")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["accounts.id", "accounts.user_id"],
            ondelete="CASCADE",
            name="fk_transactions_account_user",
        ),
        UniqueConstraint("dedupe_hash", "txn_date", name="uq_transactions_dedupe"),
        CheckConstraint("amount_minor <> 0", name="amount_nonzero"),
        # The covering index. `(account_id, txn_date DESC)` matches the shape of
        # every statement query ("this account, most recent first"); INCLUDE
        # carries the payload columns so the common listing query is answered
        # index-only, without heap fetches.
        Index(
            "ix_transactions_account_date",
            "account_id",
            text("txn_date DESC"),
            postgresql_include=["amount_minor", "narration", "category_id"],
        ),
        # RLS predicate is `user_id = current_setting(...)`, so user_id must be
        # indexed or every policy check degrades to a seq scan.
        Index("ix_transactions_user_date", "user_id", text("txn_date DESC")),
        Index("ix_transactions_category_id", "category_id"),
        {"postgresql_partition_by": "RANGE (txn_date)"},
    )


class Anomaly(Base):
    """A flagged transaction, with the detector version that flagged it."""

    __tablename__ = "anomalies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    txn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    score: Mapped[float] = mapped_column(Float, nullable=False)
    detector_version: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    review_outcome: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["txn_id", "txn_date"],
            ["transactions.id", "transactions.txn_date"],
            ondelete="CASCADE",
            name="fk_anomalies_transaction",
        ),
        UniqueConstraint("txn_id", "detector_version", name="uq_anomalies_txn_detector"),
        # Partial index on the review queue. Reviewed anomalies accumulate
        # forever and are never queried by this path, so keeping them out of
        # the index keeps it small enough to stay cached.
        Index(
            "ix_anomalies_unreviewed",
            "user_id",
            text("score DESC"),
            postgresql_where=text("reviewed_at IS NULL"),
        ),
        Index("ix_anomalies_user_id", "user_id"),
    )


class Consent(Base):
    """
    An Account Aggregator consent artifact (PHASE 3).

    Under DEPA the consent artifact -- not a password -- is the authorisation
    to read financial information. Purpose code, scope and expiry are all
    attributes of the artifact and are reproduced here so the FIU can prove,
    per fetch, that it was operating inside a live consent.
    """

    __tablename__ = "consents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    aa_consent_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    aa_consent_handle: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    purpose_code: Mapped[str] = mapped_column(String(8), nullable=False)
    purpose_text: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    aa_provider: Mapped[str] = mapped_column(String(32), nullable=False, server_default="setu")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','ACTIVE','REJECTED','REVOKED','PAUSED','EXPIRED')",
            name="consent_status_enum",
        ),
        Index("ix_consents_user_id", "user_id"),
        Index(
            "ix_consents_active",
            "user_id",
            "expiry",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )


class FiSession(Base):
    """A Financial Information fetch session against an active consent."""

    __tablename__ = "fi_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    consent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("consents.id", ondelete="CASCADE"), nullable=False
    )
    aa_session_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    from_date: Mapped[date] = mapped_column(Date, nullable=False)
    to_date: Mapped[date] = mapped_column(Date, nullable=False)
    records_ingested: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_fi_sessions_user_id", "user_id"),)


# ===========================================================================
# Operations (PHASE 6)
# ===========================================================================


class AuditLog(Base):
    """
    Append-only audit trail.

    Append-only is enforced at two levels: a BEFORE UPDATE OR DELETE trigger
    that raises, and the absence of UPDATE/DELETE grants for the application
    role. Application code cannot rewrite history even with SQL injection.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource: Mapped[str] = mapped_column(String(128), nullable=False)
    before: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    after: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(INET, nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_audit_log_actor_user_id_at", "actor_user_id", text("at DESC")),
        Index("ix_audit_log_action_at", "action", text("at DESC")),
    )


class OutboxEvent(Base):
    """
    Transactional outbox (PHASE 6).

    An AA fetch writes transactions *and* an outbox row in one transaction, so
    a crash between "data persisted" and "side effect dispatched" cannot leave
    the two out of sync. The relay is at-least-once; consumers are made
    idempotent by ``Transaction.dedupe_hash``.
    """

    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Deduplicates the *event*, so a replayed webhook enqueues nothing new.
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # The relay's only query: unprocessed and due, oldest first.
        Index(
            "ix_outbox_pending",
            "available_at",
            postgresql_where=text("processed_at IS NULL"),
        ),
    )


class IdempotencyKey(Base):
    """
    Client-supplied idempotency keys for mutating ingestion endpoints.

    The UNIQUE constraint on ``key`` is the concurrency control: two racing
    replays both try to INSERT, one loses, and the loser returns the winner's
    stored response instead of executing the side effect twice.
    """

    __tablename__ = "idempotency_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_idempotency_keys_created_at", "created_at"),)


class ModelRegistryEntry(Base):
    """Model registry (PHASE 4) -- one row per trained artifact."""

    __tablename__ = "model_registry"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    task: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    training_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (UniqueConstraint("name", "version", name="uq_model_registry_name_version"),)


# Tables that carry per-user rows and therefore need Row-Level Security.
#
# `refresh_tokens` is deliberately NOT here. Its lookup key is the token hash,
# which is itself the capability -- rotation must find a row *before* it knows
# which user owns it, and reuse detection specifically has to find tokens the
# presenter is not entitled to. A user-scoped policy would turn every reuse
# attempt into an indistinguishable "invalid token" and silently disable the
# family-revocation guarantee. Confidentiality there comes from the token being
# a 256-bit secret stored only as SHA-256, never from row visibility.
RLS_TABLES = {
    "accounts": "user_id",
    "transactions": "user_id",
    "anomalies": "user_id",
    "consents": "user_id",
    "fi_sessions": "user_id",
}
