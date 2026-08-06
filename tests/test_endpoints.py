"""
Request-level behaviour of the application endpoints.

These exercise the HTTP surface rather than the components underneath it,
because several defects in this codebase only appeared once a real request ran
through the real dependency graph. The clearest example is
``test_delete_me_does_not_deadlock``: the handler held row locks in the
request-scoped tenant session while opening a *second* connection to delete the
``users`` row those locks referenced. Every unit test passed. The request hung
until the client gave up.
"""

from __future__ import annotations

import io
import uuid

import pytest

from tests.conftest import login

pytestmark = pytest.mark.db


CSV = b"""date,amount,description
2026-03-01,-412.00,UPI/P2M/417293856201/SWIGGY
2026-03-02,-2599.99,POS 4532015112830366 AMAZON RETAIL IN
2026-03-03,85000.00,NEFT-HDFC0001234-987654321012345-SALARY CREDIT
2026-03-04,-1250.50,BIL/BBPS/ADANIELEC/100200300400/MAR
2026-03-05,-320.00,UPI/P2M/223344556677/ZOMATO
"""


async def import_csv(client, user, content: bytes = CSV, key: str | None = None):
    headers = dict(user.auth_header)
    if key:
        headers["Idempotency-Key"] = key
    return await client.post(
        "/api/v1/transactions/import",
        headers=headers,
        files={"file": ("statement.csv", io.BytesIO(content), "text/csv")},
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


async def test_health_is_public_and_discloses_no_user_data(client):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] in ("healthy", "degraded")
    # Components report *state*, never contents. A document or row count here
    # would leak usage volume to an unauthenticated caller.
    assert set(body["components"]) <= {"database", "redis", "categoriser", "aa_transport"}
    assert "user" not in response.text.lower() or "user_id" not in response.text


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


async def test_csv_import_categorises_and_stores_paise(client, alice):
    await login(alice)
    response = await import_csv(client, alice)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["transactions_ingested"] == 5, body

    listing = await client.get("/api/v1/transactions?months=24", headers=alice.auth_header)
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 5

    by_narration = {r["narration"]: r for r in rows}
    amazon = by_narration["POS 4532015112830366 AMAZON RETAIL IN"]

    # 2599.99 rupees. float("2599.99") * 100 is 259998.99999999997, so a float
    # pipeline would store 259998 paise and lose a paisa on every such row.
    assert amazon["amount_minor"] == -259999, (
        f"amount stored as {amazon['amount_minor']} paise; expected -259999. "
        f"Money must be parsed through Decimal, never float."
    )
    assert amazon["amount"] == -2599.99

    for row in rows:
        assert row["model_version"], f"{row['narration']} has no model_version"


async def test_reimporting_the_same_file_creates_no_duplicates(client, alice):
    """
    Dedupe is content-based, so a user who uploads the same statement twice --
    which they will -- does not double their balance.
    """
    await login(alice)
    first = await import_csv(client, alice)
    assert first.json()["transactions_ingested"] == 5

    second = await import_csv(client, alice)
    body = second.json()
    assert body["transactions_ingested"] == 0, body
    assert body["transactions_skipped_duplicate"] == 5, body


async def test_idempotency_key_replays_the_stored_response(client, alice):
    """
    A retried request must not re-run the operation.

    Network timeouts do not tell the client whether the server acted, so
    clients retry. Without idempotency keys, a retried import is a second
    import.
    """
    await login(alice)
    key = uuid.uuid4().hex

    first = await import_csv(client, alice, key=key)
    assert first.status_code == 200, first.text

    second = await import_csv(client, alice, key=key)
    assert second.status_code == 200, second.text
    assert second.json() == first.json(), (
        "the replayed request returned a different body, so the operation ran "
        "again instead of replaying"
    )


async def test_a_non_csv_upload_is_rejected(client, alice):
    await login(alice)
    response = await client.post(
        "/api/v1/transactions/import",
        headers=alice.auth_header,
        files={"file": ("payload.exe", io.BytesIO(b"MZ\x90\x00"), "application/octet-stream")},
    )
    assert response.status_code == 400


async def test_a_csv_missing_required_columns_is_rejected(client, alice):
    await login(alice)
    response = await import_csv(client, alice, content=b"foo,bar\n1,2\n")
    assert response.status_code == 400
    assert "missing required columns" in response.text.lower()


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


async def test_summary_arithmetic_is_correct(client, alice):
    await login(alice)
    await import_csv(client, alice)

    response = await client.get("/api/v1/analysis/summary?months=24", headers=alice.auth_header)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["transactions"] == 5
    assert body["income"] == 1
    assert body["expenses"] == 4
    assert body["total_income"] == 85000.00
    # 412.00 + 2599.99 + 1250.50 + 320.00
    assert body["total_expenses"] == 4582.49
    assert body["net_cashflow"] == round(85000.00 - 4582.49, 2)


async def test_analysis_on_an_empty_ledger_is_a_404_not_a_crash(client, alice):
    await login(alice)
    for path in ("/api/v1/analysis/summary", "/api/v1/analysis/budget"):
        response = await client.get(path, headers=alice.auth_header)
        assert response.status_code == 404, f"{path} returned {response.status_code}"


async def test_anomaly_detection_refuses_an_undersized_sample(client, alice):
    """
    Fitting a detector on five points and presenting the output as a finding
    would be worse than declining: the user cannot tell a real anomaly from an
    artefact of the sample size.
    """
    await login(alice)
    await import_csv(client, alice)

    response = await client.get("/api/v1/analysis/anomalies?months=24", headers=alice.auth_header)
    assert response.status_code == 422, response.text
    assert "10 transactions" in response.text


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


async def test_delete_me_does_not_deadlock(client, alice):
    """
    **Regression test for a self-deadlock.**

    ``DELETE /me`` deletes the caller's rows in the request-scoped tenant
    session, then opens a second connection to remove the ``users`` row. Every
    deleted table has a foreign key to ``users``, so the second connection
    needs a lock that conflicts with one the first still holds -- and the first
    cannot commit until the handler returns. The request hung until the client
    timed out.

    Fixed by committing the tenant session before opening the second. If that
    commit is ever removed, this test stops returning and the suite times out
    rather than failing cleanly, which is itself the signal.
    """
    import asyncio

    await login(alice)
    await import_csv(client, alice)

    response = await asyncio.wait_for(
        client.delete("/api/v1/me", headers=alice.auth_header), timeout=30.0
    )
    assert response.status_code == 200, response.text

    deleted = response.json()["deleted"]
    assert deleted["transactions"] == 5, deleted
    assert deleted["accounts"] == 1, deleted


async def test_the_token_dies_with_the_account(client, alice):
    """
    Erasure must revoke access immediately.

    A JWT outlives the row it refers to, so without denylisting the deleted
    user's token keeps authenticating until it expires -- against a user record
    that no longer exists.
    """
    await login(alice)
    await import_csv(client, alice)

    assert (await client.delete("/api/v1/me", headers=alice.auth_header)).status_code == 200

    after = await client.get("/api/v1/transactions", headers=alice.auth_header)
    assert (
        after.status_code == 401
    ), f"a deleted user's token still works (status {after.status_code})"


async def test_deletion_leaves_the_audit_record_behind(client, alice):
    """
    The evidence of erasure must survive the erasure.

    ``audit_log`` is append-only precisely so that "we deleted your data" is
    provable afterwards -- and so an attacker who triggers a deletion cannot
    also erase the trace.
    """
    from sqlalchemy import text

    from app.db.session import system_session

    await login(alice)
    await import_csv(client, alice)
    user_id = alice.user_id

    assert (await client.delete("/api/v1/me", headers=alice.auth_header)).status_code == 200

    async with system_session() as session:
        count = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit_log "
                    "WHERE actor_user_id = :uid AND action = 'data.deleted'"
                ),
                {"uid": user_id},
            )
        ).scalar()

    assert (
        count and count >= 1
    ), "no audit record of the deletion survives, so the erasure is unprovable"


# ---------------------------------------------------------------------------
# Scoping
# ---------------------------------------------------------------------------


async def test_stats_reports_only_the_callers_own_totals(client, alice, bob):
    """
    The previous ``/stats`` returned a global document count. In a product
    where each user is their own tenant, even a count discloses how many other
    people are using the system and how much.
    """
    await login(alice)
    await login(bob)
    await import_csv(client, alice)

    alice_stats = (await client.get("/api/v1/stats", headers=alice.auth_header)).json()
    bob_stats = (await client.get("/api/v1/stats", headers=bob.auth_header)).json()

    assert alice_stats["transactions"] == 5
    assert (
        bob_stats["transactions"] == 0
    ), f"Bob's /stats reports {bob_stats['transactions']} transactions; he has none"
