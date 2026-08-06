"""
DEFINITION OF DONE #4 -- a full Account Aggregator round-trip.

The flow, as specified by ReBIT for an NBFC-AA under RBI's DEPA framework, and
as exercised end to end below:

    1. consent request    FIU -> AA        purpose code, scope, expiry
    2. user approval      user -> AA       (out of band; sandbox-approved here)
    3. FI request         FIU -> AA        session opened against live consent
    4. notification       AA  -> FIU       webhook: data ready
    5. FI fetch           FIU -> AA        encrypted payload retrieved
    6. decrypt + ingest   FIU              X25519 ECDH -> HKDF -> AES-256-GCM

The property that makes this architecture worth the complexity: **FinGuru never
holds a bank credential, and the Account Aggregator never sees the data.** The
FIP encrypts to an ephemeral public key the FIU generated for this session, so
the AA is a consent and routing layer moving ciphertext it cannot read. That is
the "data blind" requirement in the NBFC-AA Master Direction.

    ==============================================================
    THE CEILING, STATED PLAINLY
    ==============================================================
    These tests run against a **mock transport with real cryptography**.
    Becoming a registered Financial Information User requires an RBI-regulated
    entity -- a bank, NBFC, insurer or investment adviser. A student project
    cannot be one, and no amount of correct code changes that.

    What is real here: the ReBIT message shapes, the consent state machine,
    the X25519/HKDF/AES-GCM key exchange, the webhook HMAC, and the ingest
    path. What is simulated: the counterparty. The same `FIUClient` drives
    `HttpAATransport` against a Setu/Finvu sandbox unchanged.

    An owned limitation is credible; a silent one is not. This is also stated
    in the README (asserted by `test_readme_states_the_fiu_limitation`) and
    returned by `GET /api/v1/aa/info`.
    ==============================================================
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.db


def _consent_request(email: str):
    """
    A well-formed consent request.

    ``purpose_code`` 101 is "Wealth management service" in the ReBIT purpose
    taxonomy. It is not decoration: it is the lawful basis recorded in the
    artifact, and a FIP is entitled to refuse a fetch whose purpose does not
    match what the user approved.
    """
    from app.aa.schemas import ConsentRequest, FIType, PurposeCode

    today = date.today()
    return ConsentRequest(
        purpose_code=PurposeCode.PERSONAL_FINANCE,
        fi_types=[FIType.DEPOSIT],
        from_date=today - timedelta(days=180),
        to_date=today,
        # An AA handle, never a bank credential. FinGuru never sees one.
        customer_aa_id=f"{email.split('@')[0]}@onemoney",
    )


# ---------------------------------------------------------------------------
# Cryptography
# ---------------------------------------------------------------------------


def test_ecdh_produces_a_shared_key_both_sides_agree_on():
    """
    X25519 key agreement.

    Each side derives the same secret from its own private key and the other's
    public key; the private keys never travel. An observer holding both public
    keys and the whole transcript cannot compute it.
    """
    from app.aa.crypto import derive_shared_key, generate_key_pair

    fiu = generate_key_pair()
    fip = generate_key_pair()

    # Each side passes its own nonce first, then the peer's. The salt XORs
    # both, so neither party alone can force key reuse across sessions.
    fiu_side = derive_shared_key(fiu.private_key, fip.public_key_b64, fiu.nonce_b64, fip.nonce_b64)
    fip_side = derive_shared_key(fip.private_key, fiu.public_key_b64, fip.nonce_b64, fiu.nonce_b64)

    assert fiu_side == fip_side, "ECDH key agreement failed"
    assert len(fiu_side) == 32, f"expected a 256-bit key, got {len(fiu_side) * 8} bits"


def test_a_third_party_key_cannot_decrypt_the_payload():
    """The AA sits in the middle of this exchange and must not be able to read it."""
    from app.aa.crypto import decrypt_payload, derive_shared_key, encrypt_payload, generate_key_pair

    fiu, fip, eavesdropper = generate_key_pair(), generate_key_pair(), generate_key_pair()

    session_key = derive_shared_key(
        fip.private_key, fiu.public_key_b64, fip.nonce_b64, fiu.nonce_b64
    )
    ciphertext = encrypt_payload(session_key, {"balance": 123456, "account": "50100234567890"})

    # The legitimate recipient reads it.
    recovered = derive_shared_key(fiu.private_key, fip.public_key_b64, fiu.nonce_b64, fip.nonce_b64)
    assert decrypt_payload(recovered, ciphertext)["balance"] == 123456

    # An intermediary holding only its own key does not.
    wrong = derive_shared_key(
        eavesdropper.private_key, fip.public_key_b64, eavesdropper.nonce_b64, fip.nonce_b64
    )
    with pytest.raises(Exception):
        decrypt_payload(wrong, ciphertext)


def test_tampering_with_the_ciphertext_is_detected():
    """
    AES-GCM is authenticated encryption: a modified payload fails the tag check
    rather than decrypting to something plausible. Without that, a compromised
    AA could silently alter transaction amounts in transit.
    """
    import base64

    from app.aa.crypto import decrypt_payload, derive_shared_key, encrypt_payload, generate_key_pair

    a, b = generate_key_pair(), generate_key_pair()
    key = derive_shared_key(a.private_key, b.public_key_b64, a.nonce_b64, b.nonce_b64)
    ciphertext = encrypt_payload(key, {"amount": 100})

    raw = bytearray(base64.b64decode(ciphertext))
    raw[len(raw) // 2] ^= 0x01
    tampered = base64.b64encode(bytes(raw)).decode()

    with pytest.raises(Exception):
        decrypt_payload(key, tampered)


# ---------------------------------------------------------------------------
# The round-trip
# ---------------------------------------------------------------------------


async def test_full_consent_to_ingest_round_trip(alice):
    """
    **The definition-of-done test.** Steps 1-6, end to end.

    Asserts on the ingested rows rather than on a status code: the point is
    that decrypted FIP data lands in the caller's ledger, correctly scoped.
    """
    from sqlalchemy import func, select

    from app.aa.client import MockAATransport, FIUClient
    from app.aa.service import create_consent, run_fi_session
    from app.db.models import Account, Transaction
    from app.db.session import tenant_session

    client = FIUClient(transport=MockAATransport())

    # 1. Consent request.
    async with tenant_session(alice.user_id) as session:
        consent = await create_consent(
            session,
            user_id=alice.user_id,
            request=_consent_request(alice.email),
            actor=alice.email,
            client=client,
        )
        consent_id = consent.aa_consent_id
        assert consent.status in {"PENDING", "REQUESTED"}, (
            f"a freshly requested consent must not already be ACTIVE "
            f"(got {consent.status}) -- that would mean we minted our own "
            f"authorisation"
        )

    # 2. The user approves at their AA. Out of band in reality; the sandbox
    #    exposes it directly.
    client.transport.approve_consent(consent_id)

    # 3-6. FI request, notification, fetch, decrypt, ingest.
    result = await run_fi_session(
        user_id=alice.user_id,
        consent_id=consent_id,
        months=6,
        actor=alice.email,
        client=client,
    )

    assert result.get("accounts_linked", 0) >= 1, f"no accounts ingested: {result}"
    assert result.get("transactions_ingested", 0) >= 1, f"no transactions ingested: {result}"

    async with tenant_session(alice.user_id) as session:
        accounts = (await session.execute(select(func.count(Account.id)))).scalar()
        txns = (await session.execute(select(func.count(Transaction.id)))).scalar()

    assert accounts >= 1 and txns >= 1, (
        f"ingest reported success but the ledger is empty "
        f"(accounts={accounts}, transactions={txns})"
    )


async def test_fetching_on_an_unapproved_consent_is_refused(alice):
    """
    Consent is the lawful basis. Fetching before approval is not a bug in the
    happy path -- it is unlawful processing, so it must fail closed.
    """
    from app.aa.client import ConsentNotLive, FIUClient, MockAATransport
    from app.aa.service import create_consent, run_fi_session
    from app.db.session import tenant_session

    client = FIUClient(transport=MockAATransport())

    async with tenant_session(alice.user_id) as session:
        consent = await create_consent(
            session,
            user_id=alice.user_id,
            request=_consent_request(alice.email),
            actor=alice.email,
            client=client,
        )
        consent_id = consent.aa_consent_id

    with pytest.raises(ConsentNotLive):
        await run_fi_session(
            user_id=alice.user_id,
            consent_id=consent_id,
            months=6,
            actor=alice.email,
            client=client,
        )


async def test_another_users_consent_is_not_fetchable(alice, bob):
    """
    Cross-tenant check on the AA path specifically.

    Bob's consent id is not a secret -- it appears in webhook payloads and
    logs. Alice presenting it must get the same "no such consent" as if it did
    not exist, with no way to distinguish the two.
    """
    from app.aa.client import ConsentNotLive, FIUClient, MockAATransport
    from app.aa.service import create_consent, run_fi_session
    from app.db.session import tenant_session

    client = FIUClient(transport=MockAATransport())

    async with tenant_session(bob.user_id) as session:
        consent = await create_consent(
            session,
            user_id=bob.user_id,
            request=_consent_request(bob.email),
            actor=bob.email,
            client=client,
        )
        bob_consent_id = consent.aa_consent_id

    client.transport.approve_consent(bob_consent_id)

    with pytest.raises(ConsentNotLive):
        await run_fi_session(
            user_id=alice.user_id,
            consent_id=bob_consent_id,
            months=6,
            actor=alice.email,
            client=client,
        )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_replaying_the_same_fi_data_does_not_double_count(alice):
    """
    Webhooks are at-least-once. An AA that does not see our 200 in time will
    redeliver, and a duplicated salary credit is a wrong balance shown to a
    real person.

    Defended by a unique ``dedupe_hash`` over (account, date, amount,
    narration) with ``ON CONFLICT DO NOTHING`` -- so the consumer is idempotent
    rather than the delivery being exactly-once, which is not achievable.
    """
    from sqlalchemy import func, select

    from app.aa.client import FIUClient, MockAATransport
    from app.aa.service import create_consent, run_fi_session
    from app.db.models import Transaction
    from app.db.session import tenant_session

    client = FIUClient(transport=MockAATransport())

    async with tenant_session(alice.user_id) as session:
        consent = await create_consent(
            session,
            user_id=alice.user_id,
            request=_consent_request(alice.email),
            actor=alice.email,
            client=client,
        )
        consent_id = consent.aa_consent_id

    client.transport.approve_consent(consent_id)

    first = await run_fi_session(
        user_id=alice.user_id,
        consent_id=consent_id,
        months=6,
        actor=alice.email,
        client=client,
    )
    async with tenant_session(alice.user_id) as session:
        after_first = (await session.execute(select(func.count(Transaction.id)))).scalar()

    second = await run_fi_session(
        user_id=alice.user_id,
        consent_id=consent_id,
        months=6,
        actor=alice.email,
        client=client,
    )
    async with tenant_session(alice.user_id) as session:
        after_second = (await session.execute(select(func.count(Transaction.id)))).scalar()

    assert after_first == after_second, (
        f"replaying the same FI payload created {after_second - after_first} "
        f"duplicate transactions (first fetch: {first}, second: {second})"
    )
    assert (
        second.get("transactions_skipped_duplicate", 0) > 0
    ), "the second fetch reported no duplicates, so dedupe did not run"


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


async def test_webhook_rejects_forged_signature(client):
    """
    The webhook has no bearer token -- the caller is the AA, not a user. Its
    authentication is an HMAC over the **raw body**. Verifying over the parsed
    and re-serialised JSON instead would let an attacker exploit any
    whitespace or key-ordering difference to pass a body we never signed.
    """
    payload = {
        "type": "CONSENT_STATUS_UPDATE",
        "consentId": str(uuid.uuid4()),
        "consentStatus": "ACTIVE",
    }
    body = json.dumps(payload)

    forged = await client.post(
        "/api/v1/aa/webhooks/consent",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": "deadbeef"},
    )
    assert forged.status_code in (
        401,
        403,
    ), f"a forged webhook signature was accepted (status {forged.status_code})"

    missing = await client.post(
        "/api/v1/aa/webhooks/consent",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert missing.status_code in (
        401,
        403,
    ), f"an unsigned webhook was accepted (status {missing.status_code})"


async def test_webhook_accepts_a_correct_signature(client):
    """The positive case, so the test above is not passing for the wrong reason."""
    import hashlib
    import hmac

    from app.core.config import settings

    payload = {
        "type": "CONSENT_STATUS_UPDATE",
        "consentId": str(uuid.uuid4()),
        "consentStatus": "ACTIVE",
    }
    body = json.dumps(payload)
    signature = hmac.new(
        settings.aa_webhook_secret.encode(), body.encode(), hashlib.sha256
    ).hexdigest()

    response = await client.post(
        "/api/v1/aa/webhooks/consent",
        content=body,
        # Bare lowercase hex: `verify_signature` compares against
        # `hmac.hexdigest()` directly, with no "sha256=" prefix convention.
        headers={"Content-Type": "application/json", "X-Signature": signature},
    )
    # 200/202 accepted, or 404 for an unknown consent id -- both mean the
    # signature check passed, which is what this asserts.
    assert response.status_code not in (401, 403), (
        f"a correctly signed webhook was rejected: {response.status_code} " f"{response.text}"
    )


# ---------------------------------------------------------------------------
# The stated limitation
# ---------------------------------------------------------------------------


def test_the_sandbox_limitation_is_advertised_by_the_api():
    """``GET /aa/info`` must state the ceiling, not just the README."""
    from app.aa.router import SANDBOX_NOTICE

    lowered = SANDBOX_NOTICE.lower()
    assert "sandbox" in lowered, SANDBOX_NOTICE
    assert "fiu" in lowered or "registered" in lowered, SANDBOX_NOTICE
