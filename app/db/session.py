"""
Async SQLAlchemy engine/session management with Row-Level Security binding.

The critical invariant: **every session that touches tenant data sets the
``app.current_user_id`` GUC before running any statement, and clears it
afterwards.** RLS policies read that GUC, so a session that forgets to set it
sees zero rows rather than everyone's rows -- the failure mode is "no data",
never "someone else's data".
"""

from __future__ import annotations

import contextlib
import uuid
from typing import AsyncIterator, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None

# Session GUC read by every RLS policy.
RLS_GUC = "app.current_user_id"


def get_engine() -> AsyncEngine:
    """Lazily construct the process-wide async engine."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_pre_ping=True,
            # Server-side statement cache must be disabled when a pooler such
            # as PgBouncer sits in front; harmless otherwise.
            connect_args={"server_settings": {"application_name": "finguru"}},
        )
        logger.info("Async database engine created")
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def dispose_engine() -> None:
    """Close all pooled connections (shutdown hook)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        logger.info("Database engine disposed")
    _engine = None
    _sessionmaker = None


async def set_rls_user(session: AsyncSession, user_id: Optional[uuid.UUID]) -> None:
    """
    Bind the session to a tenant.

    ``set_config(..., false)`` sets it for the whole *session* rather than the
    transaction, because SQLAlchemy's async session may begin and commit
    several transactions on one pooled connection. :func:`reset_rls_user` is
    therefore mandatory on the way out, and is called from the
    ``tenant_session`` context manager's ``finally``.
    """
    value = str(user_id) if user_id else ""
    await session.execute(text("SELECT set_config(:k, :v, false)"), {"k": RLS_GUC, "v": value})


async def reset_rls_user(session: AsyncSession) -> None:
    """
    Clear the tenant binding so a recycled connection leaks nothing.

    **The commit is load-bearing.** ``set_config(..., is_local => false)`` sets
    the value for the session rather than the transaction, but it is still
    *transactional*: a rollback undoes it. After ``session.commit()`` there is
    no open transaction, so this statement opens a fresh implicit one -- and
    ``session.close()`` rolls that back, silently undoing the reset.

    The connection then returns to the pool still carrying the previous
    tenant's id in ``app.current_user_id``. Any later code that borrows it
    without calling :func:`set_rls_user` first -- a raw ``get_sessionmaker()()``,
    a health probe, a migration helper -- would read and write as that user.
    Caught by ``tests/test_rls_isolation.py::
    test_an_unset_session_variable_yields_no_rows``, which saw exactly one row
    it should never have seen.
    """
    await session.execute(text("SELECT set_config(:k, '', false)"), {"k": RLS_GUC})
    await session.commit()


async def current_rls_user(session: AsyncSession) -> str:
    """Read back the GUC (used by tests and diagnostics)."""
    result = await session.execute(text("SELECT current_setting(:k, true)"), {"k": RLS_GUC})
    return result.scalar() or ""


@contextlib.asynccontextmanager
async def bound_connection(
    user_id: Optional[uuid.UUID],
) -> AsyncIterator[AsyncSession]:
    """
    Yield a session pinned to **one** connection with the tenant GUC set on it.

    Pinning is the whole point, and it fixes a real bug.

    An ``AsyncSession`` created from a sessionmaker does not hold a connection
    across transaction boundaries -- it checks one out on first statement and
    releases it back to the pool on ``commit()``. So the previous
    implementation could set ``app.current_user_id`` on connection X, and then,
    after any intermediate commit, run the actual queries on connection Y. The
    observed symptom was a session reading ``''`` immediately after setting the
    GUC, then a *later* borrower of X seeing the stale tenant id -- which is a
    cross-tenant read waiting to happen under pool pressure.

    Checking the connection out explicitly keeps every statement on the same
    backend for the session's whole lifetime, so "set the GUC" and "run the
    query" cannot land on different connections.

    The GUC is committed immediately after it is set. ``set_config(...,
    is_local => false)`` is session-scoped but still transactional, so an
    uncommitted value would be discarded by any later rollback -- leaving a
    live session with no tenant bound. That fails closed (zero rows), but
    silently, and a handler that returns "no transactions" for a user with
    transactions is its own kind of broken.

    On the way out the GUC is cleared and committed. If that fails, the
    connection is **invalidated** rather than returned to the pool: a
    connection whose tenant binding is unknown is not safe to hand to the next
    caller.
    """
    engine = get_engine()
    conn = await engine.connect()
    try:
        await conn.execute(
            text("SELECT set_config(:k, :v, false)"),
            {"k": RLS_GUC, "v": str(user_id) if user_id else ""},
        )
        await conn.commit()

        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
    finally:
        try:
            await conn.execute(text("SELECT set_config(:k, '', false)"), {"k": RLS_GUC})
            await conn.commit()
            await conn.close()
        except Exception:  # pragma: no cover - connection already broken
            logger.warning(
                "Could not clear the tenant GUC; invalidating the connection "
                "rather than returning it to the pool"
            )
            await conn.invalidate()


@contextlib.asynccontextmanager
async def tenant_session(user_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """
    Yield a session pinned to ``user_id`` with RLS active.

    This is the only sanctioned way for request handlers to reach tenant data.
    """
    async with bound_connection(user_id) as session:
        yield session


@contextlib.asynccontextmanager
async def system_session() -> AsyncIterator[AsyncSession]:
    """
    Yield an *untenanted* session for tables without RLS.

    Used for the users table during login (there is no authenticated user
    yet), the outbox relay, the model registry and the audit log. It does not
    bypass RLS: the GUC is explicitly set to the empty string, so tenant tables
    return zero rows rather than inheriting whatever the previous borrower of
    this connection was doing.
    """
    async with bound_connection(None) as session:
        yield session


async def check_database_health() -> dict:
    """
    Connectivity probe for startup and ``/health/detailed``.

    Reports the server version because "which PostgreSQL is this actually
    talking to" is the first question during an incident, and because the
    schema requires 13+ for ``gen_random_uuid()`` in core and 12+ for the
    partitioning features this schema relies on.
    """
    try:
        async with get_sessionmaker()() as session:
            version = (await session.execute(text("SHOW server_version"))).scalar()
        return {
            "status": "healthy",
            "backend": "postgresql",
            "version": str(version),
        }
    except Exception as exc:
        return {"status": "unhealthy", "backend": "postgresql", "error": str(exc)}
