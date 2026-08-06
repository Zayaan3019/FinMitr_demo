"""
FIU client for the Account Aggregator ecosystem (PHASE 3).

Two transports behind one interface:

* :class:`HttpAATransport` -- talks to a real AA sandbox (Setu, Finvu or
  OneMoney). All three publish free developer tiers with the same ReBIT
  endpoints; the differences are authentication headers and base URLs, which is
  what ``aa_provider`` selects.

* :class:`MockAATransport` -- a local FIP+AA simulator. It is not a stub: it
  performs the real FIP side of the X25519/HKDF/AES-GCM exchange and emits
  realistic Indian narrations, so the consent -> fetch -> ingest round trip is
  genuinely exercised in tests and in CI, where no sandbox credential exists.

**Ceiling, stated plainly:** becoming a registered FIU requires an entity
regulated by (or contracted to) an RBI-licensed NBFC-AA. A student cannot
obtain that registration. Everything here therefore runs against sandbox
endpoints and simulated FIPs. The protocol implementation, the consent
lifecycle, the encryption and the ingestion path are real; the counterparty is
not production. This is stated in the README and should be stated in
interviews -- an owned limitation is credible, a silent one is not.
"""

from __future__ import annotations

import abc
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.aa.crypto import (
    EphemeralKeyPair,
    decrypt_payload,
    derive_shared_key,
    generate_key_pair,
    simulate_fip_encryption,
)
from app.aa.schemas import (
    AAAccountData,
    AATransaction,
    ConsentArtifact,
    ConsentRequest,
    ConsentStatus,
    EncryptedFIData,
    FIFetchResponse,
    FIRequestResponse,
    KeyMaterial,
    PURPOSE_TEXT,
    SessionStatus,
)
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class AAError(Exception):
    """An Account Aggregator interaction failed."""


class ConsentNotLive(AAError):
    """The consent is missing, expired, revoked or not yet approved."""


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


class AATransport(abc.ABC):
    """Provider-agnostic transport interface."""

    @abc.abstractmethod
    async def create_consent(self, request: ConsentRequest) -> ConsentArtifact: ...

    @abc.abstractmethod
    async def get_consent(self, consent_id: str) -> ConsentArtifact: ...

    @abc.abstractmethod
    async def revoke_consent(self, consent_id: str) -> bool: ...

    @abc.abstractmethod
    async def create_fi_request(
        self, consent_id: str, from_date: date, to_date: date, key_material: KeyMaterial
    ) -> FIRequestResponse: ...

    @abc.abstractmethod
    async def fetch_fi(self, session_id: str) -> FIFetchResponse: ...


class HttpAATransport(AATransport):
    """Talks to a real AA sandbox over HTTPS."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        self.base_url = (base_url or settings.aa_base_url).rstrip("/")
        self.client_id = client_id or settings.aa_client_id
        self.client_secret = client_secret or settings.aa_client_secret
        self.provider = provider or settings.aa_provider

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "x-jws-signature": "",  # populated by the signing proxy in deployment
        }
        if self.provider == "setu":
            headers.update(
                {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "x-product-instance-id": settings.aa_product_instance_id,
                }
            )
        else:  # finvu / onemoney use bearer tokens
            headers["Authorization"] = f"Bearer {self.client_secret}"
        return headers

    async def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=settings.aa_request_timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}{path}", json=payload, headers=self._headers()
            )
            if response.status_code >= 400:
                raise AAError(
                    f"AA {path} failed with {response.status_code}: {response.text[:400]}"
                )
            return response.json()

    async def _get(self, path: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=settings.aa_request_timeout_seconds) as client:
            response = await client.get(f"{self.base_url}{path}", headers=self._headers())
            if response.status_code >= 400:
                raise AAError(
                    f"AA {path} failed with {response.status_code}: {response.text[:400]}"
                )
            return response.json()

    async def create_consent(self, request: ConsentRequest) -> ConsentArtifact:
        now = datetime.now(timezone.utc)
        expiry = now + timedelta(days=request.consent_expiry_days)
        body = {
            "ConsentDetail": {
                "consentStart": now.isoformat(),
                "consentExpiry": expiry.isoformat(),
                "consentMode": request.consent_mode.value,
                "fetchType": request.fetch_type.value,
                "consentTypes": ["TRANSACTIONS", "PROFILE", "SUMMARY"],
                "fiTypes": [f.value for f in request.fi_types],
                "DataConsumer": {"id": settings.aa_fiu_id, "type": "FIU"},
                "Customer": {"id": request.customer_aa_id},
                "Purpose": {
                    "code": request.purpose_code.value,
                    "refUri": "https://api.rebit.org.in/aa/purpose/105.xml",
                    "text": request.purpose_text,
                    "Category": {"type": "Personal Finance"},
                },
                "FIDataRange": {
                    "from": request.from_date.isoformat(),
                    "to": request.to_date.isoformat(),
                },
                "DataLife": request.data_life.model_dump(),
                "Frequency": request.frequency.model_dump(),
            }
        }
        data = await self._post("/Consent", body)
        return ConsentArtifact(
            consent_id=data.get("id") or data.get("ConsentHandle", str(uuid.uuid4())),
            consent_handle=data.get("ConsentHandle") or data.get("id", ""),
            status=ConsentStatus.PENDING,
            purpose_code=request.purpose_code.value,
            purpose_text=request.purpose_text,
            customer_aa_id=request.customer_aa_id,
            fi_types=[f.value for f in request.fi_types],
            consent_start=now,
            consent_expiry=expiry,
            data_range_from=request.from_date,
            data_range_to=request.to_date,
            aa_provider=self.provider,
        )

    async def get_consent(self, consent_id: str) -> ConsentArtifact:
        data = await self._get(f"/Consent/{consent_id}")
        detail = data.get("ConsentDetail", {})
        return ConsentArtifact(
            consent_id=consent_id,
            consent_handle=data.get("ConsentHandle", consent_id),
            status=ConsentStatus(data.get("status", "PENDING")),
            purpose_code=detail.get("Purpose", {}).get("code", "105"),
            purpose_text=detail.get("Purpose", {}).get("text", "Personal finance"),
            customer_aa_id=detail.get("Customer", {}).get("id", ""),
            fi_types=detail.get("fiTypes", ["DEPOSIT"]),
            consent_start=datetime.now(timezone.utc),
            consent_expiry=datetime.now(timezone.utc) + timedelta(days=180),
            data_range_from=date.today() - timedelta(days=365),
            data_range_to=date.today(),
            aa_provider=self.provider,
        )

    async def revoke_consent(self, consent_id: str) -> bool:
        await self._post(f"/Consent/{consent_id}/revoke", {})
        return True

    async def create_fi_request(
        self, consent_id: str, from_date: date, to_date: date, key_material: KeyMaterial
    ) -> FIRequestResponse:
        body = {
            "FIDataRange": {"from": from_date.isoformat(), "to": to_date.isoformat()},
            "Consent": {"id": consent_id},
            "KeyMaterial": {
                "cryptoAlg": "ECDH",
                "curve": key_material.curve,
                "params": "cipher=AES/GCM/NoPadding;KeyPairGenerator=ECDH",
                "DHPublicKey": {
                    "expiry": (
                        key_material.expiry or datetime.now(timezone.utc) + timedelta(hours=1)
                    ).isoformat(),
                    "Parameters": "",
                    "KeyValue": key_material.public_key,
                },
                "Nonce": key_material.nonce,
            },
        }
        data = await self._post("/FI/request", body)
        return FIRequestResponse(
            session_id=data.get("sessionId", str(uuid.uuid4())),
            consent_id=consent_id,
            status=SessionStatus.PENDING,
        )

    async def fetch_fi(self, session_id: str) -> FIFetchResponse:
        data = await self._get(f"/FI/fetch/{session_id}")
        payload = []
        for item in data.get("FI", []):
            for record in item.get("data", []):
                payload.append(
                    EncryptedFIData(
                        fip_id=item.get("fipID", "unknown"),
                        masked_account_number=record.get("maskedAccNumber", "XXXX0000"),
                        link_ref_number=record.get("linkRefNumber", ""),
                        encrypted_fi=record.get("encryptedFI", ""),
                        key_material=KeyMaterial(
                            public_key=item.get("KeyMaterial", {})
                            .get("DHPublicKey", {})
                            .get("KeyValue", ""),
                            nonce=item.get("KeyMaterial", {}).get("Nonce", ""),
                        ),
                    )
                )
        return FIFetchResponse(
            session_id=session_id,
            status=SessionStatus(data.get("status", "COMPLETED")),
            payload=payload,
        )


class MockAATransport(AATransport):
    """
    Local AA + FIP simulator.

    Real protocol, real crypto, simulated counterparty. Consents start PENDING
    and must be approved via :meth:`approve_consent`, mirroring the fact that
    approval happens at the AA with the user present -- never inside FinGuru.
    """

    def __init__(self, auto_approve: bool = False, seed: int = 20260801):
        self._consents: Dict[str, ConsentArtifact] = {}
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self.auto_approve = auto_approve
        self.seed = seed

    async def create_consent(self, request: ConsentRequest) -> ConsentArtifact:
        now = datetime.now(timezone.utc)
        consent_id = f"cons-{uuid.uuid4()}"
        artifact = ConsentArtifact(
            consent_id=consent_id,
            consent_handle=f"handle-{uuid.uuid4()}",
            status=ConsentStatus.ACTIVE if self.auto_approve else ConsentStatus.PENDING,
            purpose_code=request.purpose_code.value,
            purpose_text=PURPOSE_TEXT.get(request.purpose_code.value, "Personal finance"),
            customer_aa_id=request.customer_aa_id,
            fi_types=[f.value for f in request.fi_types],
            consent_start=now,
            consent_expiry=now + timedelta(days=request.consent_expiry_days),
            data_range_from=request.from_date,
            data_range_to=request.to_date,
            aa_provider="mock",
        )
        self._consents[consent_id] = artifact
        self._consents[artifact.consent_handle] = artifact
        logger.info(f"[mock AA] consent {consent_id} created ({artifact.status})")
        return artifact

    async def get_consent(self, consent_id: str) -> ConsentArtifact:
        artifact = self._consents.get(consent_id)
        if artifact is None:
            raise ConsentNotLive(f"Unknown consent {consent_id}")
        return artifact

    def approve_consent(self, consent_id: str) -> ConsentArtifact:
        """Simulate the user approving at the AA."""
        artifact = self._consents[consent_id]
        artifact.status = ConsentStatus.ACTIVE.value
        return artifact

    def reject_consent(self, consent_id: str) -> ConsentArtifact:
        artifact = self._consents[consent_id]
        artifact.status = ConsentStatus.REJECTED.value
        return artifact

    async def revoke_consent(self, consent_id: str) -> bool:
        artifact = self._consents.get(consent_id)
        if artifact is None:
            return False
        artifact.status = ConsentStatus.REVOKED.value
        return True

    async def create_fi_request(
        self, consent_id: str, from_date: date, to_date: date, key_material: KeyMaterial
    ) -> FIRequestResponse:
        artifact = self._consents.get(consent_id)
        if artifact is None:
            raise ConsentNotLive(f"Unknown consent {consent_id}")
        if artifact.status != ConsentStatus.ACTIVE.value:
            raise ConsentNotLive(f"Consent {consent_id} is {artifact.status}, not ACTIVE")

        session_id = f"sess-{uuid.uuid4()}"
        self._sessions[session_id] = {
            "consent_id": consent_id,
            "from_date": from_date,
            "to_date": to_date,
            "key_material": key_material,
        }
        logger.info(f"[mock AA] FI session {session_id} created")
        return FIRequestResponse(
            session_id=session_id, consent_id=consent_id, status=SessionStatus.PENDING
        )

    async def fetch_fi(self, session_id: str) -> FIFetchResponse:
        session = self._sessions.get(session_id)
        if session is None:
            raise AAError(f"Unknown FI session {session_id}")

        key_material: KeyMaterial = session["key_material"]
        payload_records: List[EncryptedFIData] = []

        for index, (fip_id, masked) in enumerate(
            [("HDFC-FIP", "XXXXXX4172"), ("SBI-FIP", "XXXXXX9931")]
        ):
            account_payload = _simulate_fip_payload(
                fip_id=fip_id,
                masked=masked,
                from_date=session["from_date"],
                to_date=session["to_date"],
                seed=self.seed + index,
            )
            encrypted, fip_public, fip_nonce = simulate_fip_encryption(
                key_material.public_key, key_material.nonce, account_payload
            )
            payload_records.append(
                EncryptedFIData(
                    fip_id=fip_id,
                    masked_account_number=masked,
                    # Stable across sessions, deliberately. In the ReBIT
                    # protocol `linkRefNumber` is the AA's persistent handle for
                    # a linked account -- it identifies the same bank account
                    # every time, which is what lets an FIU recognise a refetch.
                    #
                    # Deriving it from the session id (as this did) gave every
                    # fetch a fresh handle, so `upsert_account` created a new
                    # account row each time and the content dedupe hash --
                    # which includes account_id -- never collided. A user who
                    # refreshed twice would have seen their entire transaction
                    # history duplicated.
                    link_ref_number=f"link-{fip_id}-{masked}",
                    encrypted_fi=encrypted,
                    key_material=KeyMaterial(public_key=fip_public, nonce=fip_nonce),
                )
            )

        return FIFetchResponse(
            session_id=session_id,
            status=SessionStatus.COMPLETED,
            payload=payload_records,
        )


def _simulate_fip_payload(
    fip_id: str, masked: str, from_date: date, to_date: date, seed: int
) -> Dict[str, Any]:
    """Generate a realistic FIP account payload using the shared narration corpus."""
    import random

    from app.ml.dataset import AMOUNT_RANGES, MERCHANTS, _render

    rng = random.Random(seed)
    span = max(1, (to_date - from_date).days)
    n = min(180, max(20, span // 3))

    transactions = []
    for i in range(n):
        label = rng.choice(list(MERCHANTS.keys()))
        merchant = rng.choice(MERCHANTS[label])
        narration = _render(rng, merchant, label)
        low, high = AMOUNT_RANGES[label]
        paise = rng.randint(low, high)
        txn_date = from_date + timedelta(days=rng.randint(0, span))
        is_credit = label == "salary"
        transactions.append(
            {
                "txn_id": f"{fip_id}-{seed}-{i}",
                "txn_date": txn_date.isoformat(),
                "value_date": txn_date.isoformat(),
                # FIPs send rupees as a decimal *string*, never a float.
                "amount": f"{paise / 100:.2f}",
                "type": "CREDIT" if is_credit else "DEBIT",
                "narration": narration,
                "reference": f"REF{rng.randint(10**9, 10**10 - 1)}",
                "mode": rng.choice(["UPI", "CARD", "NEFT", "ACH", "ATM"]),
            }
        )

    return {
        "fip_id": fip_id,
        "masked_account_number": masked,
        "account_type": "SAVINGS",
        "currency": "INR",
        "transactions": sorted(transactions, key=lambda t: t["txn_date"]),
    }


# ---------------------------------------------------------------------------
# FIU client
# ---------------------------------------------------------------------------


class FIUClient:
    """
    The FIU role: request consent, request FI, fetch, decrypt.

    Holds the per-session key pair, so the private key never leaves the
    process and is discarded when the session completes.
    """

    def __init__(self, transport: Optional[AATransport] = None):
        self.transport = transport or self._default_transport()
        self._keys: Dict[str, EphemeralKeyPair] = {}

    @staticmethod
    def _default_transport() -> AATransport:
        if settings.aa_use_mock_transport or settings.aa_provider == "mock":
            return MockAATransport()
        return HttpAATransport()

    async def request_consent(self, request: ConsentRequest) -> ConsentArtifact:
        return await self.transport.create_consent(request)

    async def get_consent(self, consent_id: str) -> ConsentArtifact:
        return await self.transport.get_consent(consent_id)

    async def revoke_consent(self, consent_id: str) -> bool:
        return await self.transport.revoke_consent(consent_id)

    async def start_fi_session(
        self, consent_id: str, from_date: date, to_date: date
    ) -> FIRequestResponse:
        """
        Open an FI session, generating fresh key material for it.

        The consent is re-checked here rather than trusted from the database:
        the user may have revoked it at the AA since the last fetch, and the AA
        is the authority on consent state, not our copy of it.
        """
        artifact = await self.transport.get_consent(consent_id)
        if not artifact.is_live:
            raise ConsentNotLive(
                f"Consent {consent_id} is {artifact.status} "
                f"(expires {artifact.consent_expiry}); refusing to fetch"
            )

        keys = generate_key_pair()
        response = await self.transport.create_fi_request(
            consent_id,
            from_date,
            to_date,
            KeyMaterial(public_key=keys.public_key_b64, nonce=keys.nonce_b64),
        )
        self._keys[response.session_id] = keys
        return response

    async def fetch_and_decrypt(self, session_id: str) -> List[AAAccountData]:
        """Fetch the session payload and decrypt each FIP record."""
        keys = self._keys.get(session_id)
        if keys is None:
            raise AAError(
                f"No key material held for session {session_id}. Key material is "
                "per-process and per-session; a restarted worker cannot decrypt "
                "a session it did not open."
            )

        response = await self.transport.fetch_fi(session_id)
        if response.status not in (SessionStatus.COMPLETED, SessionStatus.PARTIAL):
            raise AAError(f"FI session {session_id} is {response.status}")

        accounts: List[AAAccountData] = []
        for record in response.payload:
            shared = derive_shared_key(
                keys.private_key,
                peer_public_key_b64=record.key_material.public_key,
                our_nonce_b64=keys.nonce_b64,
                peer_nonce_b64=record.key_material.nonce,
            )
            decrypted = decrypt_payload(shared, record.encrypted_fi)
            accounts.append(
                AAAccountData(
                    fip_id=decrypted.get("fip_id", record.fip_id),
                    masked_account_number=decrypted.get(
                        "masked_account_number", record.masked_account_number
                    ),
                    link_ref_number=record.link_ref_number,
                    account_type=decrypted.get("account_type", "SAVINGS"),
                    currency=decrypted.get("currency", "INR"),
                    transactions=[AATransaction(**t) for t in decrypted.get("transactions", [])],
                )
            )

        # Forward secrecy: drop the private key as soon as it is no longer needed.
        self._keys.pop(session_id, None)
        return accounts


_client: Optional[FIUClient] = None


def get_fiu_client() -> FIUClient:
    global _client
    if _client is None:
        _client = FIUClient()
    return _client


def set_fiu_client(client: Optional[FIUClient]) -> None:
    """Test-support override."""
    global _client
    _client = client
