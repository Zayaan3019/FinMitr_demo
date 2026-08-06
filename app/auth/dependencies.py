"""
FastAPI dependencies that turn a bearer token into a verified principal.

**This module is the fix for PHASE 0.** Before it, ``user_id`` arrived as a URL
path parameter with nothing behind it, so ``GET /analyze/summary/{user_id}``
would happily read any stranger's finances and ``DELETE /user/{user_id}`` would
delete their account -- textbook IDOR, OWASP API Security #1.

After it, the only source of ``user_id`` in the entire request-handling path is
:func:`get_current_user`, which derives it from a signed, unexpired,
non-denylisted JWT. There is no code path that accepts a user identifier from
the client, and `tests/test_phase0_idor.py` asserts that by walking the live
OpenAPI schema.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import denylist
from app.auth.tokens import (
    AccessTokenClaims,
    TokenError,
    TokenExpired,
    decode_access_token,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import User
from app.db.session import system_session, tenant_session

logger = get_logger(__name__)

bearer_scheme = HTTPBearer(auto_error=False, description="Bearer access token")

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. The single source of truth for ``user_id``."""

    user_id: uuid.UUID
    email: str
    mfa_satisfied: bool
    session_id: uuid.UUID
    jti: str
    scope: str
    token_expires_at: object

    @property
    def id(self) -> uuid.UUID:  # convenience alias
        return self.user_id


async def _claims_from_request(
    credentials: Optional[HTTPAuthorizationCredentials],
) -> AccessTokenClaims:
    if credentials is None or not credentials.credentials:
        raise _UNAUTHORIZED
    try:
        claims = decode_access_token(credentials.credentials)
    except TokenExpired:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token expired",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    except TokenError:
        raise _UNAUTHORIZED

    # Server-side revocation. Checked before any database work so a revoked
    # token cannot even cause load.
    if await denylist.is_revoked(
        claims.jti,
        session_id=claims.session_id,
        user_id=claims.user_id,
        issued_at=claims.issued_at,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    return claims


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Principal:
    """
    Resolve the caller from the bearer token.

    Rejects ``mfa_pending`` tokens: those exist only to complete the second
    factor and must not authorise anything else.
    """
    claims = await _claims_from_request(credentials)

    if claims.scope == "mfa_pending":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Multi-factor authentication required",
            headers={"WWW-Authenticate": 'Bearer error="mfa_required"'},
        )

    async with system_session() as session:
        user = (
            await session.execute(select(User).where(User.id == claims.user_id))
        ).scalar_one_or_none()

    if user is None or not user.is_active:
        raise _UNAUTHORIZED

    return Principal(
        user_id=user.id,
        email=user.email_ci,
        mfa_satisfied=claims.mfa,
        session_id=claims.session_id,
        jti=claims.jti,
        scope=claims.scope,
        token_expires_at=claims.expires_at,
    )


async def get_mfa_pending_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Principal:
    """Accept *only* the short-lived challenge token from /auth/login."""
    claims = await _claims_from_request(credentials)
    if claims.scope != "mfa_pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This endpoint requires an MFA challenge token",
        )
    async with system_session() as session:
        user = (
            await session.execute(select(User).where(User.id == claims.user_id))
        ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise _UNAUTHORIZED
    return Principal(
        user_id=user.id,
        email=user.email_ci,
        mfa_satisfied=False,
        session_id=claims.session_id,
        jti=claims.jti,
        scope=claims.scope,
        token_expires_at=claims.expires_at,
    )


async def require_mfa(
    principal: Principal = Depends(get_current_user),
) -> Principal:
    """
    Gate for bank-linkage routes.

    A password alone must never be enough to attach a real bank account: the
    AA consent it creates is a standing subscription to someone's financial
    life.
    """
    if settings.mfa_required_for_bank_linkage and not principal.mfa_satisfied:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Multi-factor authentication must be enabled before linking a "
                "bank account. Enrol at POST /api/v1/auth/mfa/enrol."
            ),
        )
    return principal


async def get_tenant_session(
    principal: Principal = Depends(get_current_user),
) -> AsyncIterator[AsyncSession]:
    """
    Yield a database session already bound to the caller's tenant.

    Handlers that use this dependency cannot read another user's rows even if
    they forget a ``WHERE`` clause: Row-Level Security filters at the SQL
    layer. That is the structural half of the PHASE 0 fix -- the token check is
    the procedural half.

    Delegates to :func:`app.db.session.tenant_session` rather than repeating
    the set/reset dance. The duplicated version here had the same
    connection-pinning bug: an ``AsyncSession`` from a sessionmaker releases
    its connection on every commit, so the GUC could end up on a different
    backend from the queries that depend on it.
    """
    async with tenant_session(principal.user_id) as session:
        yield session


async def get_system_session() -> AsyncIterator[AsyncSession]:
    """Untenanted session for auth flows (no authenticated user yet)."""
    async with system_session() as session:
        yield session


def client_ip(request: Request) -> Optional[str]:
    """
    Best-effort client IP.

    ``X-Forwarded-For`` is only honoured when the app is known to sit behind a
    trusted proxy; otherwise a client can forge it and defeat both the rate
    limiter and the audit trail.
    """
    if settings.is_production:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
