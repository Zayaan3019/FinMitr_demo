"""
DEFINITION OF DONE #2 -- cross-user access is blocked at the SQL layer.

Application-level checks are necessary but not sufficient: they hold only as
long as every handler remembers its ``WHERE user_id = ...``. The tests here
bypass the application entirely and issue raw SQL against PostgreSQL as the
application role, asserting that the database itself refuses.

Read them as the answer to "what happens when a developer forgets the filter?"
The answer must be "nothing leaks", not "code review would have caught it".

Everything here depends on the application connecting as ``finguru_app``, a
role created ``NOSUPERUSER NOBYPASSRLS`` and *not* owning the tables --
PostgreSQL exempts superusers and table owners from RLS, and ``FORCE ROW LEVEL
SECURITY`` in the migration closes the owner case. ``test_app_role_cannot_
bypass_rls`` asserts those role attributes directly, because if that ever
regressed every other test in this file would pass while proving nothing.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.db.session import system_session, tenant_session

pytestmark = pytest.mark.db


async def seed_transaction(user_id: uuid.UUID, marker: str) -> tuple[uuid.UUID, uuid.UUID]:
    """
    Insert one account + one transaction for a user, bypassing the API.

    Note this uses ``tenant_session``, not ``system_session``. That is not a
    convenience -- ``system_session`` leaves the GUC empty, and the ``WITH
    CHECK`` half of the policy rejects the insert outright. The test fixtures
    are subject to the same isolation as production code, which is the point:
    there is no privileged back door for tests to seed through.
    """
    account_id = uuid.uuid4()
    txn_id = uuid.uuid4()
    async with tenant_session(user_id) as session:
        await session.execute(
            text(
                "INSERT INTO accounts (id, user_id, aa_handle, masked_number, "
                "type, currency) VALUES (:aid, :uid, :h, 'XXXX1234', 'SAVINGS', 'INR')"
            ),
            {"aid": account_id, "uid": user_id, "h": f"test:{marker}"},
        )
        await session.execute(
            text(
                "INSERT INTO transactions (id, txn_date, account_id, user_id, "
                "amount_minor, currency, narration, dedupe_hash, source) VALUES "
                "(:tid, CURRENT_DATE, :aid, :uid, -123400, 'INR', :n, :d, 'test')"
            ),
            {"tid": txn_id, "aid": account_id, "uid": user_id, "n": marker, "d": marker},
        )
        await session.commit()
    return account_id, txn_id


# ---------------------------------------------------------------------------
# The role itself
# ---------------------------------------------------------------------------


async def test_app_role_cannot_bypass_rls():
    """
    Foundational. Every other assertion here is void if this fails.

    A superuser, a BYPASSRLS role, or the table owner all read straight through
    policies. Checked against ``pg_roles`` rather than assumed from the
    migration, because the deployed database is what matters.
    """
    async with tenant_session(uuid.uuid4()) as session:
        row = (
            await session.execute(
                text(
                    "SELECT current_user, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()

    role, is_super, bypasses = row
    assert not is_super, f"application role '{role}' is a SUPERUSER; RLS is inert"
    assert not bypasses, f"application role '{role}' has BYPASSRLS; RLS is inert"


async def test_rls_is_enabled_and_forced_on_every_tenant_table():
    """
    ``ENABLE`` alone leaves the table owner exempt. ``FORCE`` closes that.

    The table list comes from :data:`app.db.models.RLS_TABLES`, so adding a
    tenant table without a policy fails here rather than silently shipping.
    """
    from app.db.models import RLS_TABLES

    async with system_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE relname = ANY(:names) AND relkind IN ('r','p')"
                ),
                {"names": list(RLS_TABLES)},
            )
        ).all()

    seen = {r[0]: (r[1], r[2]) for r in rows}
    missing = set(RLS_TABLES) - set(seen)
    assert not missing, f"tables declared tenant-scoped but not found: {sorted(missing)}"

    for table, (enabled, forced) in sorted(seen.items()):
        assert enabled, f"{table}: RLS not ENABLEd"
        assert forced, f"{table}: RLS not FORCEd -- the owner would read through it"


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def test_select_without_a_where_clause_returns_only_own_rows(alice, bob):
    """
    The forgotten-``WHERE`` scenario, made concrete.

    ``SELECT * FROM transactions`` with no predicate at all. Under RLS this
    returns the caller's rows and nothing else -- which is why
    ``_load_transactions`` in the API layer deliberately omits the filter.
    """
    alice_marker = f"ALICE-{uuid.uuid4().hex[:8]}"
    bob_marker = f"BOB-{uuid.uuid4().hex[:8]}"
    await seed_transaction(alice.user_id, alice_marker)
    await seed_transaction(bob.user_id, bob_marker)

    async with tenant_session(alice.user_id) as session:
        narrations = [
            r[0] for r in (await session.execute(text("SELECT narration FROM transactions"))).all()
        ]

    assert alice_marker in narrations, "Alice cannot see her own transaction"
    assert bob_marker not in narrations, "RLS LEAK: Alice read Bob's transaction"


async def test_explicitly_naming_another_user_returns_zero_rows(alice, bob):
    """
    An attacker who knows Bob's UUID and injects it into the predicate still
    gets nothing: the policy is ANDed with whatever the query asks for.
    """
    bob_marker = f"BOB-{uuid.uuid4().hex[:8]}"
    await seed_transaction(bob.user_id, bob_marker)

    async with tenant_session(alice.user_id) as session:
        for table in ("transactions", "accounts", "anomalies", "consents", "fi_sessions"):
            count = (
                await session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE user_id = :uid"),
                    {"uid": bob.user_id},
                )
            ).scalar()
            assert count == 0, f"RLS LEAK: Alice saw {count} of Bob's rows in {table}"


async def test_reading_a_partition_directly_is_also_filtered(alice, bob):
    """
    Partitions are separate tables. If the policy lived only on the parent, a
    caller could name ``transactions_2026_03`` directly and walk straight past
    it -- so the migration revokes all privileges on the partitions.
    """
    bob_marker = f"BOB-{uuid.uuid4().hex[:8]}"
    await seed_transaction(bob.user_id, bob_marker)

    async with system_session() as session:
        partition = (
            await session.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_inherits i ON i.inhrelid = c.oid "
                    "JOIN pg_class p ON p.oid = i.inhparent "
                    "WHERE p.relname = 'transactions' AND c.relname <> 'transactions_default' "
                    "ORDER BY c.relname DESC LIMIT 1"
                )
            )
        ).scalar()
    assert partition, "transactions is not partitioned"

    leaked = None
    async with tenant_session(alice.user_id) as session:
        try:
            rows = (await session.execute(text(f"SELECT narration FROM {partition}"))).all()
            leaked = [r[0] for r in rows]
        except (ProgrammingError, DBAPIError) as exc:
            # Preferred outcome: permission denied on the partition.
            assert (
                "permission denied" in str(exc).lower()
            ), f"unexpected error reading partition {partition}: {exc}"
            return

    assert bob_marker not in (
        leaked or []
    ), f"RLS LEAK: reading partition {partition} directly exposed Bob's row"


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def test_inserting_a_row_owned_by_another_user_is_rejected(alice, bob):
    """
    Isolation must cover writes too. Without ``WITH CHECK``, Alice could plant
    rows in Bob's ledger -- invisible to her, fully visible to him.
    """
    account_id, _ = await seed_transaction(bob.user_id, f"BOB-{uuid.uuid4().hex[:8]}")

    async with tenant_session(alice.user_id) as session:
        with pytest.raises((ProgrammingError, DBAPIError)) as exc_info:
            await session.execute(
                text(
                    "INSERT INTO transactions (id, txn_date, account_id, user_id, "
                    "amount_minor, currency, narration, dedupe_hash, source) VALUES "
                    "(:tid, CURRENT_DATE, :aid, :uid, -1, 'INR', 'planted', :d, 'test')"
                ),
                {
                    "tid": uuid.uuid4(),
                    "aid": account_id,
                    "uid": bob.user_id,
                    "d": uuid.uuid4().hex,
                },
            )
        assert (
            "row-level security" in str(exc_info.value).lower()
        ), f"insert was rejected, but not by RLS: {exc_info.value}"


async def test_updating_and_deleting_another_users_rows_affects_nothing(alice, bob):
    """
    ``UPDATE``/``DELETE`` without a matching policy row are not errors -- they
    match zero rows. Assert the row survives rather than trusting the rowcount.
    """
    bob_marker = f"BOB-{uuid.uuid4().hex[:8]}"
    _, txn_id = await seed_transaction(bob.user_id, bob_marker)

    async with tenant_session(alice.user_id) as session:
        await session.execute(
            text("UPDATE transactions SET narration = 'HACKED' WHERE id = :tid"),
            {"tid": txn_id},
        )
        await session.execute(text("DELETE FROM transactions WHERE id = :tid"), {"tid": txn_id})
        await session.commit()

    async with tenant_session(bob.user_id) as session:
        narration = (
            await session.execute(
                text("SELECT narration FROM transactions WHERE id = :tid"), {"tid": txn_id}
            )
        ).scalar()

    assert (
        narration == bob_marker
    ), f"RLS LEAK: Alice modified or deleted Bob's row (narration is now {narration!r})"


# ---------------------------------------------------------------------------
# The session variable itself
# ---------------------------------------------------------------------------


async def test_an_unset_session_variable_yields_no_rows(alice):
    """
    Fail closed.

    The policy compares ``user_id::text`` to ``current_setting('app.
    current_user_id', true)``, which is NULL when unset. NULL never equals
    anything, so a connection that skipped ``set_rls_user`` sees nothing at all
    -- rather than everything.
    """
    marker = f"ALICE-{uuid.uuid4().hex[:8]}"
    await seed_transaction(alice.user_id, marker)

    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as session:  # no set_rls_user
        count = (await session.execute(text("SELECT count(*) FROM transactions"))).scalar()

    assert count == 0, f"a session with no tenant bound saw {count} rows; RLS must fail closed"


async def test_the_guc_does_not_leak_between_pooled_sessions(alice, bob):
    """
    Connections are pooled and reused. If ``app.current_user_id`` survived
    checkin, the next borrower would inherit the previous tenant's identity --
    a leak that only shows up under load, which is the worst kind.
    """
    from app.db.session import current_rls_user

    async with tenant_session(alice.user_id) as session:
        assert await current_rls_user(session) == str(alice.user_id)

    async with tenant_session(bob.user_id) as session:
        assert await current_rls_user(session) == str(
            bob.user_id
        ), "the tenant GUC leaked across pooled connections"


async def test_audit_log_is_append_only(alice):
    """
    Not isolation, but the same principle: enforced by the database, not by
    convention. An attacker who reaches the app role must not be able to erase
    the record of what they did.
    """
    from app.ops.audit import AuditAction, write_audit

    await write_audit(
        AuditAction.DATA_ACCESSED,
        resource=f"test:{uuid.uuid4().hex[:8]}",
        actor=alice.email,
        actor_user_id=alice.user_id,
    )

    async with tenant_session(alice.user_id) as session:
        for statement in (
            "DELETE FROM audit_log",
            "UPDATE audit_log SET action = 'tampered'",
        ):
            with pytest.raises((ProgrammingError, DBAPIError)) as exc_info:
                await session.execute(text(statement))
            assert "permission denied" in str(exc_info.value).lower() or (
                "append-only" in str(exc_info.value).lower()
            ), f"'{statement}' was not refused: {exc_info.value}"
            await session.rollback()
