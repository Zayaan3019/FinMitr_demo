"""
Account Aggregator data models, following the ReBIT NBFC-AA specification
(PHASE 3).

FinGuru acts as an **FIU (Financial Information User)** under the RBI Account
Aggregator framework, which implements DEPA's consent layer. The flow is:

    consent request -> consent artifact -> FI request -> notification -> FI fetch

and the thing that makes it different from every screen-scraping "bank
aggregator" is that **FinGuru never sees a credential**. The user authenticates
to the AA, the AA obtains their consent, and the FIP releases data encrypted to
a key FinGuru supplied. There is no password to phish, store or leak, and the
user can revoke at the AA at any time without changing anything at their bank.

Purpose codes are from the ReBIT taxonomy; FinGuru uses **101 (Wealth
management service)** and **105 (Personal finance)**. The purpose code is not
decoration -- it bounds what the data may lawfully be used for, and it is
recorded on the consent artifact and in the audit log.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConsentStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    PAUSED = "PAUSED"
    EXPIRED = "EXPIRED"


class SessionStatus(str, Enum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class PurposeCode(str, Enum):
    """ReBIT purpose taxonomy (subset FinGuru is entitled to use)."""

    WEALTH_MANAGEMENT = "101"
    CUSTOMER_SPENDING = "102"
    ACCOUNT_AGGREGATION = "103"
    EXPLICIT_ONETIME = "104"
    PERSONAL_FINANCE = "105"


PURPOSE_TEXT: Dict[str, str] = {
    "101": "Wealth management service",
    "102": "Customer spending patterns, budget or other reportings",
    "103": "Aggregated statement",
    "104": "Explicit one-time access",
    "105": "Personal finance",
}


class FIType(str, Enum):
    DEPOSIT = "DEPOSIT"
    TERM_DEPOSIT = "TERM_DEPOSIT"
    RECURRING_DEPOSIT = "RECURRING_DEPOSIT"
    MUTUAL_FUNDS = "MUTUAL_FUNDS"
    EQUITIES = "EQUITIES"


class ConsentMode(str, Enum):
    VIEW = "VIEW"
    STORE = "STORE"
    QUERY = "QUERY"
    STREAM = "STREAM"


class FetchType(str, Enum):
    ONETIME = "ONETIME"
    PERIODIC = "PERIODIC"


# ---------------------------------------------------------------------------
# Consent artifact
# ---------------------------------------------------------------------------


class DataLife(BaseModel):
    """How long the FIU may retain the data after fetching it."""

    unit: str = "MONTH"
    value: int = 6


class Frequency(BaseModel):
    """How often the FIU may fetch under this consent."""

    unit: str = "DAY"
    value: int = 1


class ConsentRequest(BaseModel):
    """What FinGuru asks the AA for."""

    purpose_code: PurposeCode = PurposeCode.PERSONAL_FINANCE
    fi_types: List[FIType] = Field(default_factory=lambda: [FIType.DEPOSIT])
    consent_mode: ConsentMode = ConsentMode.STORE
    fetch_type: FetchType = FetchType.PERIODIC
    # Window of history requested.
    from_date: date
    to_date: date
    consent_expiry_days: int = Field(default=180, ge=1, le=365)
    data_life: DataLife = Field(default_factory=DataLife)
    frequency: Frequency = Field(default_factory=Frequency)
    # The user's AA handle, e.g. "9876543210@onemoney". Never a credential.
    customer_aa_id: str = Field(min_length=3, max_length=128)

    @field_validator("to_date")
    @classmethod
    def range_ordered(cls, v: date, info) -> date:
        start = info.data.get("from_date")
        if start and v < start:
            raise ValueError("to_date must not precede from_date")
        return v

    @property
    def purpose_text(self) -> str:
        return PURPOSE_TEXT.get(self.purpose_code.value, "Personal finance")

    def scope(self) -> Dict[str, Any]:
        """The scope object persisted on ``consents.scope``."""
        return {
            "fi_types": [f.value for f in self.fi_types],
            "consent_mode": self.consent_mode.value,
            "fetch_type": self.fetch_type.value,
            "from_date": self.from_date.isoformat(),
            "to_date": self.to_date.isoformat(),
            "data_life": self.data_life.model_dump(),
            "frequency": self.frequency.model_dump(),
        }


class ConsentArtifact(BaseModel):
    """The signed artifact the AA returns once the user approves."""

    consent_id: str
    consent_handle: str
    status: ConsentStatus
    purpose_code: str
    purpose_text: str
    customer_aa_id: str
    fi_types: List[str] = Field(default_factory=list)
    consent_start: datetime
    consent_expiry: datetime
    data_range_from: date
    data_range_to: date
    signature: Optional[str] = None
    aa_provider: str = "setu"

    model_config = ConfigDict(use_enum_values=True)

    @property
    def is_live(self) -> bool:
        """Active *and* unexpired. Both checks, every fetch."""
        return self.status == ConsentStatus.ACTIVE.value and self.consent_expiry > datetime.now(
            timezone.utc
        )


# ---------------------------------------------------------------------------
# FI request / fetch
# ---------------------------------------------------------------------------


class KeyMaterial(BaseModel):
    """
    ECDH key material FinGuru supplies so the FIP can encrypt to it.

    This is what "encrypted end to end" means concretely: the FIP derives a
    shared secret from its private key and FinGuru's public key, so the
    Account Aggregator itself -- which relays the payload -- cannot read the
    financial data it is carrying. The AA is a consent broker, not a data
    custodian.
    """

    curve: str = "Curve25519"
    public_key: str
    nonce: str
    key_id: Optional[str] = None
    expiry: Optional[datetime] = None


class FIRequest(BaseModel):
    consent_id: str
    from_date: date
    to_date: date
    key_material: KeyMaterial


class FIRequestResponse(BaseModel):
    session_id: str
    consent_id: str
    status: SessionStatus = SessionStatus.PENDING


class EncryptedFIData(BaseModel):
    """One FIP's encrypted payload within an FI fetch response."""

    fip_id: str
    masked_account_number: str
    link_ref_number: str
    encrypted_fi: str
    # FIP's ephemeral public key + nonce, needed to derive the shared secret.
    key_material: KeyMaterial


class FIFetchResponse(BaseModel):
    session_id: str
    status: SessionStatus
    payload: List[EncryptedFIData] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Decrypted financial information
# ---------------------------------------------------------------------------


class AATransaction(BaseModel):
    """A transaction as it appears in a decrypted FIP payload."""

    txn_id: str
    txn_date: date
    value_date: Optional[date] = None
    # FIPs send rupees as a decimal string. It is parsed to integer paise at
    # the boundary and never held as a float.
    amount: str
    type: str  # DEBIT | CREDIT
    narration: str
    reference: Optional[str] = None
    mode: Optional[str] = None
    current_balance: Optional[str] = None

    def amount_minor(self) -> int:
        """
        Convert to signed integer paise.

        Parsed via ``Decimal``, not ``float``: ``float("2599.99") * 100`` is
        259998.99999999997, which truncates to a one-paisa error on every
        transaction and a reconciliation failure at month end.
        """
        from decimal import Decimal, ROUND_HALF_UP

        value = Decimal(str(self.amount).replace(",", "").strip())
        minor = int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return -minor if self.type.upper() == "DEBIT" else minor


class AAAccountData(BaseModel):
    """Decrypted data for one linked account."""

    fip_id: str
    masked_account_number: str
    link_ref_number: str
    account_type: str = "SAVINGS"
    currency: str = "INR"
    transactions: List[AATransaction] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Webhook notifications
# ---------------------------------------------------------------------------


class ConsentNotification(BaseModel):
    """AA -> FIU callback when consent status changes."""

    consent_id: Optional[str] = None
    consent_handle: Optional[str] = None
    status: ConsentStatus
    notification_id: Optional[str] = None
    timestamp: Optional[datetime] = None

    model_config = ConfigDict(use_enum_values=True)


class FINotification(BaseModel):
    """AA -> FIU callback when a session's data is ready."""

    session_id: str
    session_status: SessionStatus
    notification_id: Optional[str] = None
    fip_id: Optional[str] = None
    timestamp: Optional[datetime] = None

    model_config = ConfigDict(use_enum_values=True)


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


class CreateConsentRequest(BaseModel):
    """Client-facing consent request. Note: no user identifier."""

    customer_aa_id: str = Field(min_length=3, max_length=128)
    purpose_code: PurposeCode = PurposeCode.PERSONAL_FINANCE
    months_of_history: int = Field(default=12, ge=1, le=24)
    consent_expiry_days: int = Field(default=180, ge=1, le=365)

    def to_consent_request(self) -> ConsentRequest:
        today = datetime.now(timezone.utc).date()
        return ConsentRequest(
            purpose_code=self.purpose_code,
            from_date=today - timedelta(days=30 * self.months_of_history),
            to_date=today,
            consent_expiry_days=self.consent_expiry_days,
            customer_aa_id=self.customer_aa_id,
        )


class ConsentResponse(BaseModel):
    consent_id: str
    consent_handle: str
    status: str
    purpose_code: str
    purpose_text: str
    expiry: str
    # Where the user goes to approve. Approval happens at the AA, never here.
    approval_url: Optional[str] = None
    sandbox_only: bool = True


class FIFetchRequestBody(BaseModel):
    consent_id: str
    months: int = Field(default=12, ge=1, le=24)


class FIFetchResult(BaseModel):
    session_id: str
    status: str
    accounts_linked: int
    transactions_ingested: int
    transactions_skipped_duplicate: int
    consent_id: str
