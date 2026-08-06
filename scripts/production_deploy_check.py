#!/usr/bin/env python
"""
Pre-flight checks before a production deploy.

This is not a test suite -- `pytest tests/` is. It answers a different
question: *is this particular deployment configured safely?* The test suite
runs against a development database with development secrets and would pass
happily on a deployment with `JWT_SECRET` unset and CORS wide open.

Every check below corresponds to a way the deployment can be wrong while all
the code is right. Checks are grouped by severity:

    FATAL     refuse to deploy
    WARNING   deploy is possible but something is degraded
    INFO      recorded so the deploy log says what was true at the time

Exit code is non-zero if any FATAL check fails.

Usage::

    ENVIRONMENT=production python scripts/production_deploy_check.py
    python scripts/production_deploy_check.py --skip-network
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

FATAL, WARNING, INFO = "FATAL", "WARNING", "INFO"

Result = Tuple[str, str, str]  # (severity, check name, detail)
results: List[Result] = []


def record(severity: str, name: str, detail: str) -> None:
    results.append((severity, name, detail))


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def check_python() -> None:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 10):
        record(FATAL, "python", f"3.10+ required, found {major}.{minor}")
    else:
        record(INFO, "python", f"{major}.{minor}")


def check_dependencies() -> None:
    """
    Every import the running application actually needs.

    Deliberately derived from what the code imports, not from
    requirements.txt: a stale requirements file is exactly the thing this
    should catch.
    """
    required = {
        "fastapi": "web framework",
        "uvicorn": "ASGI server",
        "pydantic": "schemas",
        "sqlalchemy": "ORM",
        "asyncpg": "async PostgreSQL driver",
        "alembic": "migrations",
        "redis": "denylist / rate limits / budgets",
        "argon2": "Argon2id password hashing",
        "jwt": "access tokens",
        "pyotp": "TOTP second factor",
        "cryptography": "AA payload encryption, field encryption",
        "torch": "categoriser inference",
        "transformers": "categoriser inference",
        "pandas": "analysis",
        "numpy": "analysis",
        "sklearn": "anomaly detection",
        "loguru": "logging",
        "psutil": "health checks",
        "httpx": "AA transport",
    }
    missing = [f"{p} ({why})" for p, why in required.items() if importlib.util.find_spec(p) is None]
    if missing:
        record(FATAL, "dependencies", f"missing: {', '.join(missing)}")
    else:
        record(INFO, "dependencies", f"all {len(required)} present")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def check_settings() -> None:
    """Delegates to the same validator the application runs at startup."""
    from app.core.config import settings

    record(INFO, "environment", settings.environment)

    if not settings.is_production:
        record(
            WARNING,
            "environment",
            f"ENVIRONMENT is '{settings.environment}', not production -- the "
            f"production-only validations below are inert",
        )

    for problem in settings.validate_production_settings():
        record(FATAL, "configuration", problem)

    if settings.is_production and not settings.validate_production_settings():
        record(INFO, "configuration", "all production settings validated")

    # Not covered by validate_production_settings, but worth stating.
    if settings.access_token_ttl_seconds > 3600:
        record(
            WARNING,
            "token ttl",
            f"access tokens live {settings.access_token_ttl_seconds}s; "
            f"revocation latency is bounded by this",
        )
    if settings.argon2_memory_cost < 32768:
        record(
            WARNING,
            "argon2",
            f"memory cost {settings.argon2_memory_cost} KiB is below 32 MiB; "
            f"memory-hardness is the entire reason for choosing Argon2id over "
            f"bcrypt, and a low setting forfeits it",
        )
    if not settings.redis_required:
        record(
            WARNING,
            "redis",
            "REDIS_REQUIRED is false -- with more than one worker, a token "
            "revoked on one is still accepted by the others",
        )


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


async def check_database() -> None:
    from sqlalchemy import text

    from app.db.session import check_database_health, system_session, tenant_session

    health = await check_database_health()
    if health.get("status") != "healthy":
        record(FATAL, "database", f"unreachable: {health}")
        return
    record(INFO, "database", f"PostgreSQL {health.get('version')}")

    # The single most important deployment check in this file. If the
    # application role can bypass RLS, every tenancy guarantee in the schema is
    # decorative and the test suite would still pass -- because the tests use
    # whatever role DATABASE_URL points at.
    import uuid

    async with tenant_session(uuid.uuid4()) as session:
        role, is_super, bypasses = (
            await session.execute(
                text(
                    "SELECT current_user, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()

    if is_super:
        record(FATAL, "rls", f"application connects as SUPERUSER '{role}' -- RLS is inert")
    elif bypasses:
        record(FATAL, "rls", f"application role '{role}' has BYPASSRLS -- RLS is inert")
    else:
        record(INFO, "rls", f"application role '{role}' is nosuperuser, nobypassrls")

    async with system_session() as session:
        from app.db.models import RLS_TABLES

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

        for table in RLS_TABLES:
            if table not in seen:
                record(FATAL, "rls", f"table '{table}' does not exist")
            elif not seen[table][0]:
                record(FATAL, "rls", f"'{table}': RLS is not ENABLEd")
            elif not seen[table][1]:
                record(FATAL, "rls", f"'{table}': RLS is not FORCEd (the owner reads through it)")
        if all(seen.get(t, (False, False))[1] for t in RLS_TABLES):
            record(INFO, "rls", f"enabled and forced on all {len(RLS_TABLES)} tenant tables")

    # Schema version, read on a *separate* session. The application role has
    # no privileges on `alembic_version` -- correctly, since it must never run
    # DDL -- so this query is expected to fail on that connection and the
    # failure must not abort the transaction the checks above are using.
    async with system_session() as session:
        try:
            version = (
                await session.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar()
            record(INFO, "migrations", f"at {version}")
        except Exception:
            await session.rollback()
            record(
                INFO,
                "migrations",
                "not readable by the application role (expected: it holds no "
                "privileges on alembic_version). Check with the migration role.",
            )

    # Partition coverage. Rows landing in DEFAULT still work but forfeit
    # pruning, so this is a warning rather than a failure.
    async with system_session() as session:
        covered = (
            await session.execute(
                text(
                    "SELECT count(*) FROM pg_class c "
                    "JOIN pg_inherits i ON i.inhrelid = c.oid "
                    "JOIN pg_class p ON p.oid = i.inhparent "
                    "WHERE p.relname = 'transactions' "
                    "AND c.relname = 'transactions_' || to_char(CURRENT_DATE, 'YYYY_MM')"
                )
            )
        ).scalar()
        if not covered:
            record(
                WARNING,
                "partitions",
                "no partition covers the current month; rows will land in "
                "DEFAULT and forfeit partition pruning",
            )
        else:
            record(INFO, "partitions", "current month covered")


async def check_redis() -> None:
    from app.core.redis_client import ping_redis
    from app.core.config import settings

    if await ping_redis():
        record(INFO, "redis", "reachable")
    elif settings.redis_required:
        record(FATAL, "redis", "REDIS_REQUIRED is set but Redis is unreachable")
    else:
        record(WARNING, "redis", "unreachable; running on the in-process fallback")


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------


def check_model() -> None:
    import json

    from app.core.config import settings
    from app.ml.registry import get_registry

    version = get_registry().active_version("transaction-categoriser")
    if not version:
        record(
            WARNING,
            "categoriser",
            "no model registered; serving keyword rules (rules-v0). Train one "
            "with scripts/train_categorizer.py.",
        )
        return

    card_path = (
        Path(settings.model_registry_dir) / "transaction-categoriser" / version / "model_card.json"
    )
    if not card_path.exists():
        record(FATAL, "categoriser", f"ACTIVE is '{version}' but its model card is missing")
        return

    card = json.loads(card_path.read_text(encoding="utf-8"))
    macro_f1 = card.get("metrics", {}).get("temporal_test_macro_f1")

    if macro_f1 is None:
        record(FATAL, "categoriser", f"{version} has no temporal-split macro-F1 recorded")
    elif macro_f1 < settings.categorizer_min_macro_f1:
        record(
            FATAL,
            "categoriser",
            f"{version} scores {macro_f1:.4f} macro-F1, below the "
            f"{settings.categorizer_min_macro_f1} floor",
        )
    else:
        record(INFO, "categoriser", f"{version}, macro-F1 {macro_f1:.4f} (temporal split)")


# ---------------------------------------------------------------------------
# Route surface
# ---------------------------------------------------------------------------


def check_routes() -> None:
    """
    Definition-of-done #1, checked against the deployed application object.

    The test suite covers this, but it is worth re-running here: a bad merge
    that reintroduces a `{user_id}` route would be caught before it serves
    traffic rather than by the next test run.
    """
    import re

    from main import app

    suspect = re.compile(r"user[_-]?id|^uid$", re.IGNORECASE)
    offenders = []

    for path, item in app.openapi().get("paths", {}).items():
        if suspect.search(path):
            offenders.append(f"path {path}")
        for method, operation in item.items():
            if not isinstance(operation, dict):
                continue
            for param in operation.get("parameters", []) or []:
                if suspect.match(param.get("name", "")):
                    offenders.append(f"{method.upper()} {path} -> {param['name']}")

    if offenders:
        record(FATAL, "idor", f"user identifiers in the route surface: {offenders}")
    else:
        record(INFO, "idor", f"{len(app.openapi()['paths'])} paths, no user identifier in any")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(skip_network: bool) -> int:
    check_python()
    check_dependencies()
    check_settings()
    check_model()
    check_routes()

    if skip_network:
        record(WARNING, "database", "skipped (--skip-network)")
        record(WARNING, "redis", "skipped (--skip-network)")
    else:
        await check_database()
        await check_redis()
        from app.db.session import dispose_engine

        await dispose_engine()

    print("\n" + "=" * 72)
    print("PRE-DEPLOY CHECK")
    print("=" * 72)
    for severity in (FATAL, WARNING, INFO):
        rows = [r for r in results if r[0] == severity]
        if not rows:
            continue
        print(f"\n{severity}")
        for _, name, detail in rows:
            print(f"  [{name}] {detail}")

    fatal = [r for r in results if r[0] == FATAL]
    warnings = [r for r in results if r[0] == WARNING]
    print("\n" + "=" * 72)
    if fatal:
        print(f"REFUSE TO DEPLOY: {len(fatal)} fatal issue(s), {len(warnings)} warning(s)")
        return 1
    print(f"SAFE TO DEPLOY: 0 fatal issues, {len(warnings)} warning(s)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-network",
        action="store_true",
        help="skip PostgreSQL and Redis checks (CI lint stage)",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.skip_network))


if __name__ == "__main__":
    raise SystemExit(main())
