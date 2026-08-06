"""
Shared test fixtures.

**These tests run against a real PostgreSQL and a real Redis.** That is a
deliberate cost. The central claim of this codebase -- that cross-user access
is blocked at the SQL layer by Row-Level Security -- cannot be tested against
SQLite or a mock, because SQLite has no RLS and a mock would simply agree with
whatever the test asserted. A test that cannot fail is not evidence.

Bring the dependencies up with::

    docker compose up -d postgres redis
    alembic upgrade head

Tests needing the database are skipped, loudly, when it is unreachable --
never silently passed.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Set before any application module is imported, so Settings picks them up.
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-at-least-32-characters!")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "dGVzdC1maWVsZC1lbmNyeXB0aW9uLWtleS0zMmIh")
os.environ.setdefault("AA_USE_MOCK_TRANSPORT", "True")
# Argon2 at production cost (64 MiB, t=3) would make a suite with a few dozen
# logins take minutes. Lowered here only; application defaults are untouched.
os.environ.setdefault("ARGON2_MEMORY_COST", "8192")
os.environ.setdefault("ARGON2_TIME_COST", "1")


# ---------------------------------------------------------------------------
# Infrastructure availability
# ---------------------------------------------------------------------------
#
# The loop is session-scoped (see pytest.ini). There is no custom `event_loop`
# fixture: pytest-asyncio 1.x removed it, and redefining it is what produced
# the "Future attached to a different loop" teardown crash -- the probe below
# opened the pool on its own loop and the tests then borrowed from it on
# another.


@pytest_asyncio.fixture(scope="session")
async def database_available() -> bool:
    """Probe PostgreSQL once, on the same loop every test will use."""
    try:
        from app.db.session import check_database_health

        health = await check_database_health()
        if health.get("status") == "healthy":
            return True
        print(f"\n[conftest] PostgreSQL unhealthy: {health}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"\n[conftest] PostgreSQL unreachable: {type(exc).__name__}: {exc}", file=sys.stderr)
    return False


@pytest_asyncio.fixture(autouse=True)
async def _require_database(request, database_available):
    """
    Skip -- never silently pass -- database tests when Postgres is down.

    Applied automatically to anything marked ``@pytest.mark.db``. A suite that
    quietly reports green because its dependencies were missing is worse than
    a red one.
    """
    if request.node.get_closest_marker("db") and not database_available:
        pytest.skip(
            "PostgreSQL unreachable. Run: docker compose up -d postgres redis "
            "&& alembic upgrade head"
        )


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dispose_engine_at_end():
    """Close the pool inside the session loop, before it is torn down."""
    yield
    from app.core.redis_client import close_redis
    from app.db.session import dispose_engine

    await close_redis()
    await dispose_engine()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def clean_redis():
    from app.core.redis_client import reset_redis_for_tests

    await reset_redis_for_tests()
    yield
    await reset_redis_for_tests()


class TestUser:
    """A registered user plus the credentials needed to act as them."""

    __test__ = False  # not a pytest test class despite the name

    def __init__(self, user_id: uuid.UUID, email: str, password: str):
        self.user_id = user_id
        self.email = email
        self.password = password
        self.access_token: str | None = None
        self.refresh_token: str | None = None

    @property
    def auth_header(self) -> dict:
        assert self.access_token, "no access token issued for this user"
        return {"Authorization": f"Bearer {self.access_token}"}


async def make_user(email_prefix: str = "user") -> TestUser:
    """Register a user through the service layer and return their handle."""
    from app.auth.service import register_user
    from app.db.session import system_session

    # example.com, not example.test: pydantic's EmailStr rejects the
    # special-use .test TLD, so a fixture using it would fail body validation
    # at the login route (422) without ever reaching the authentication path
    # the test is trying to exercise.
    email = f"{email_prefix}-{uuid.uuid4().hex[:10]}@example.com"
    password = "Correct-Horse-Battery-Staple-9!"

    async with system_session() as session:
        user = await register_user(session, email=email, password=password)
        await session.commit()
        user_id = user.id

    return TestUser(user_id=user_id, email=email, password=password)


async def login(user: TestUser, mfa_satisfied: bool = True) -> TestUser:
    """
    Mint an access/refresh pair for an already-registered user.

    ``mfa_satisfied`` defaults to True so tests of *other* concerns are not
    forced through TOTP enrolment. Tests that care about the MFA gate pass
    False explicitly -- see ``test_auth_flows.py``.
    """
    from sqlalchemy import select

    from app.auth.service import issue_session
    from app.db.models import User
    from app.db.session import system_session

    async with system_session() as session:
        row = (await session.execute(select(User).where(User.id == user.user_id))).scalar_one()
        access, refresh, _expires = await issue_session(
            session, user=row, mfa_satisfied=mfa_satisfied
        )
        await session.commit()

    user.access_token = access
    user.refresh_token = refresh
    return user


@pytest_asyncio.fixture
async def alice() -> AsyncIterator[TestUser]:
    yield await make_user("alice")


@pytest_asyncio.fixture
async def bob() -> AsyncIterator[TestUser]:
    yield await make_user("bob")


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncIterator["httpx.AsyncClient"]:  # noqa: F821
    """
    ASGI client that bypasses lifespan.

    Startup spawns background tasks and refuses to run without every
    dependency; these tests exercise request handling, and the fixtures above
    manage their own connections.
    """
    import httpx

    from main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def pytest_configure(config):
    config.addinivalue_line("markers", "db: requires a live PostgreSQL")
    config.addinivalue_line("markers", "slow: slow test")
    config.addinivalue_line("markers", "integration: crosses component boundaries")
