"""
FinGuru -- application entry point.

Composition root. Nothing here implements policy; it wires the pieces and
refuses to start when the deployment is unsafe.

Three things changed from the previous version and each was a real defect:

1. **Startup could not fail.** The old lifespan warmed a vector store and an
   LLM handle and logged a tick for each. A production deployment with an unset
   ``JWT_SECRET`` and ``CORS=*`` started perfectly happily. Startup now calls
   :meth:`Settings.validate_production_settings` and refuses to serve if it
   returns anything.

2. **CORS was ``["*"]`` with ``allow_credentials=True``.** Browsers reject that
   combination outright, so the setting was simultaneously insecure in intent
   and broken in practice. Origins now come from configuration.

3. **The auth and AA routers did not exist,** so every route in the application
   was unauthenticated. They are mounted below, and the database engine and
   outbox relay now have an actual shutdown path.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.aa.router import router as aa_router
from app.api.endpoints import router as api_router
from app.auth.router import router as auth_router
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.core.monitoring import HealthChecker, get_metrics_collector
from app.core.security import setup_security_middleware
from app.models.schemas import ErrorResponse

setup_logging()
logger = get_logger(__name__)

metrics = get_metrics_collector()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


async def _ensure_partitions() -> None:
    """
    Keep monthly ``transactions`` partitions ahead of the clock.

    The migration seeds partitions through 2028 and a DEFAULT catch-all, so a
    missed run degrades to rows landing in DEFAULT rather than to insert
    failures. Idempotent, so running it on every boot is free.
    """
    from sqlalchemy import text

    from app.db.session import system_session

    async with system_session() as session:
        await session.execute(
            text("SELECT ensure_transaction_partitions(:months)"),
            {"months": settings.txn_partition_lookahead_months},
        )
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 68)
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"Environment: {settings.environment}")
    logger.info("=" * 68)

    # ---- Refuse to start on an unsafe production configuration -----------
    problems = settings.validate_production_settings()
    if problems:
        for problem in problems:
            logger.critical(f"FATAL CONFIGURATION: {problem}")
        raise RuntimeError(
            f"Refusing to start: {len(problems)} fatal production "
            f"misconfiguration(s). See the log above."
        )

    background: list[asyncio.Task] = []

    # ---- Database --------------------------------------------------------
    from app.db.session import check_database_health

    db_health = await check_database_health()
    if db_health["status"] != "healthy":
        # Fail loudly. A financial API with no database is not "degraded",
        # it is unable to answer a single meaningful request, and RLS -- the
        # tenancy guarantee -- lives in the database.
        raise RuntimeError(f"Database unavailable: {db_health}")
    logger.info(f"OK  PostgreSQL reachable ({db_health.get('version', 'unknown')})")

    await _ensure_partitions()
    logger.info(
        f"OK  Transaction partitions ensured "
        f"({settings.txn_partition_lookahead_months} months ahead)"
    )

    # ---- Redis -----------------------------------------------------------
    from app.core.redis_client import is_fallback, ping_redis

    redis_ok = await ping_redis()
    if redis_ok:
        logger.info("OK  Redis reachable (denylist, rate limits, token budgets)")
    elif settings.redis_required:
        raise RuntimeError("REDIS_REQUIRED is set but Redis is unreachable")
    else:
        logger.warning(
            "WARN Redis unreachable -- falling back to in-process state. Token "
            "revocation and rate limits will NOT be shared across workers. "
            "Set REDIS_REQUIRED=true to make this fatal."
        )
        _ = is_fallback()

    # ---- Outbox relay ----------------------------------------------------
    from app.ops import handlers, outbox

    # Registration must precede the relay. An event type with no handler is
    # parked on sight, so starting the loop first would park whatever is
    # already queued.
    handlers.register_all()
    background.append(asyncio.create_task(outbox.relay_loop(), name="outbox-relay"))
    logger.info(
        f"OK  Outbox relay started with {len(handlers.HANDLERS)} handler(s) "
        f"(at-least-once delivery)"
    )

    # ---- Cache sweeper ---------------------------------------------------
    from app.core.caching import get_cache, start_cache_cleanup_task

    background.append(asyncio.create_task(start_cache_cleanup_task(), name="cache-sweep"))

    # ---- Model registry --------------------------------------------------
    from app.ml.registry import get_registry

    active = get_registry().active_version("transaction-categoriser")
    if active:
        logger.info(f"OK  Categoriser: {active}")
    else:
        logger.warning(
            "WARN No categoriser registered -- falling back to keyword rules "
            "(rules-v0). Train one with scripts/train_categorizer.py."
        )

    if settings.aa_use_mock_transport:
        logger.warning(
            "WARN Account Aggregator running against the mock transport. "
            "Sandbox-only; FinGuru is not an RBI-registered FIU. See README."
        )

    logger.info("Startup complete")

    try:
        yield
    finally:
        logger.info("Initiating graceful shutdown...")

        for task in background:
            task.cancel()
        # Drain rather than abandon: an outbox iteration mid-flight holds a
        # database row locked with FOR UPDATE SKIP LOCKED.
        await asyncio.gather(*background, return_exceptions=True)
        logger.info("OK  Background tasks stopped")

        await get_cache().clear()

        from app.core.redis_client import close_redis
        from app.db.session import dispose_engine

        await close_redis()
        await dispose_engine()
        logger.info("OK  Connections disposed")
        logger.info(f"Final metrics: {metrics.get_summary()}")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Financial advisory API with token-derived identity, PostgreSQL "
        "Row-Level Security tenancy, RBI Account Aggregator connectivity "
        "(sandbox), a trained transaction categoriser, and a redacted, "
        "injection-fenced LLM path."
    ),
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Authentication", "description": "Registration, login, MFA, token rotation"},
        {"name": "FinGuru", "description": "Transactions, analysis and advisory"},
        {"name": "Account Aggregator", "description": "RBI AA consent and FI fetch (sandbox)"},
        {"name": "Health", "description": "Liveness, readiness and metrics"},
    ],
)

setup_security_middleware(app)

# Credentialed CORS with a wildcard origin is rejected by every browser, so the
# old `allow_origins=["*"] + allow_credentials=True` pairing was broken as well
# as unsafe. Credentials are only offered when the origin list is explicit.
_origins = settings.cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=bool(_origins) and "*" not in _origins,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Signature"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Assign a request id, time the request, and record metrics."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    request.state.request_id = request_id
    started = time.time()

    # Path, not URL: query strings can carry values we do not want in metric
    # labels or log lines.
    endpoint = request.url.path
    metrics.increment_counter(
        "http_requests_total", labels={"method": request.method, "endpoint": endpoint}
    )

    try:
        response = await call_next(request)
    except Exception as exc:
        metrics.increment_counter(
            "http_errors_total",
            labels={"endpoint": endpoint, "error_type": type(exc).__name__},
        )
        logger.error(
            f"[{request_id}] {request.method} {endpoint} raised " f"{type(exc).__name__}: {exc}",
            exc_info=True,
        )
        raise

    duration = time.time() - started
    metrics.increment_counter(
        "http_responses_total",
        labels={"endpoint": endpoint, "status": str(response.status_code)},
    )
    metrics.record_histogram("http_request_duration", duration, labels={"endpoint": endpoint})
    logger.info(
        f"[{request_id}] {request.method} {endpoint} " f"{response.status_code} {duration:.3f}s"
    )

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time"] = f"{duration:.3f}s"
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Last-resort handler.

    The response body never contains ``str(exc)`` outside debug: exception text
    from SQLAlchemy or asyncpg quotes the failing statement, which in this
    application means quoting somebody's transaction data back to whoever
    triggered the error. The request id correlates to the full server-side log.
    """
    request_id = getattr(request.state, "request_id", None)
    metrics.increment_counter(
        "unhandled_exceptions_total", labels={"exception_type": type(exc).__name__}
    )
    logger.error(
        f"[{request_id}] Unhandled {type(exc).__name__} on "
        f"{request.method} {request.url.path}: {exc}",
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="Internal server error",
            detail=str(exc) if settings.debug else "An unexpected error occurred",
            request_id=request_id,
        ).model_dump(mode="json"),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(auth_router, prefix="/api/v1")
app.include_router(api_router, prefix="/api/v1", tags=["FinGuru"])
if settings.aa_enabled:
    app.include_router(aa_router, prefix="/api/v1")


@app.get("/health/live", tags=["Health"])
async def liveness_check():
    """Kubernetes liveness. Process is up; says nothing about dependencies."""
    return {"status": "alive", "timestamp": time.time()}


@app.get("/health/ready", tags=["Health"])
async def readiness_check():
    """
    Kubernetes readiness.

    Fails on an unreachable database, which is correct: without PostgreSQL
    there is no RLS, and serving requests without RLS is worse than serving
    none at all.
    """
    readiness = await HealthChecker.check_readiness()
    if not readiness["ready"]:
        return JSONResponse(status_code=503, content=readiness)
    return readiness


@app.get("/health/detailed", tags=["Health"])
async def detailed_health_check():
    health = await HealthChecker.check_system_health()
    if health["status"] == "unhealthy":
        return JSONResponse(status_code=503, content=health)
    return health


@app.get("/metrics", tags=["Health"])
async def get_metrics():
    """
    Process metrics.

    Aggregate counters only -- no per-user labels, so scraping this cannot
    reveal who is using the system or how much.
    """
    return {"summary": metrics.get_summary(), "detailed": metrics.get_metrics()}


@app.get("/")
async def root():
    """Service descriptor. Unauthenticated, so it carries no user data."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "operational",
        "uptime_seconds": round(time.time() - metrics.start_time, 2),
        "environment": settings.environment,
        "docs": "/docs" if not settings.is_production else None,
        "notice": (
            "Account Aggregator connectivity is sandbox-only. FinGuru is not "
            "an RBI-registered Financial Information User."
        ),
        "endpoints": {
            "register": "POST /api/v1/auth/register",
            "login": "POST /api/v1/auth/login",
            "refresh": "POST /api/v1/auth/refresh",
            "transactions": "GET /api/v1/transactions",
            "summary": "GET /api/v1/analysis/summary",
            "chat": "POST /api/v1/chat",
            "consent": "POST /api/v1/aa/consents",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
