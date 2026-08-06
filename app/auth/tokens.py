"""
JWT issuance/validation and the rotating refresh-token family (PHASE 1).

Access tokens are short-lived (15 min) and stateless, so the hot path costs no
database round trip. Revocation is still immediate because every access token
carries a ``jti`` that is checked against a Redis denylist, and the denylist
entry expires exactly when the token would have anyway -- so it cannot grow
without bound.

Refresh tokens are opaque 256-bit strings stored only as SHA-256 hashes. Each
use rotates: the presented token is marked used and a fresh child is issued in
the same *family*. Presenting an already-used token is the signature of theft
(the attacker replays a token the legitimate client already rotated, or vice
versa) and there is no way to tell attacker from victim -- so the entire family
is revoked and both parties are forced to re-authenticate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import hash_token, new_opaque_token
from app.core.logging import get_logger
from app.db.models import RefreshToken

logger = get_logger(__name__)


class TokenError(Exception):
    """Base class for token failures (mapped to HTTP 401 by the router)."""


class TokenExpired(TokenError):
    pass


class TokenRevoked(TokenError):
    pass


class RefreshReuseDetected(TokenError):
    """A refresh token was presented twice. The family has been revoked."""


@dataclass(frozen=True)
class AccessTokenClaims:
    """Validated claims of an access token."""

    user_id: uuid.UUID
    jti: str
    session_id: uuid.UUID
    mfa: bool
    issued_at: datetime
    expires_at: datetime
    scope: str


# ---------------------------------------------------------------------------
# Access tokens
# ---------------------------------------------------------------------------


def create_access_token(
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    mfa_satisfied: bool,
    scope: str = "user",
    ttl_seconds: Optional[int] = None,
) -> Tuple[str, str, datetime]:
    """
    Mint an access token.

    Returns ``(token, jti, expires_at)``. The ``jti`` is returned so the caller
    can denylist it on logout.
    """
    now = datetime.now(timezone.utc)
    ttl = ttl_seconds if ttl_seconds is not None else settings.access_token_ttl_seconds
    expires_at = now + timedelta(seconds=ttl)
    jti = str(uuid.uuid4())

    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": jti,
        "mfa": bool(mfa_satisfied),
        "scope": scope,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "typ": "access",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, jti, expires_at


def decode_access_token(token: str) -> AccessTokenClaims:
    """
    Validate signature, expiry, issuer, audience and token type.

    ``algorithms`` is pinned to a single value: accepting the ``alg`` header at
    face value is the classic JWT confusion attack (``alg: none``, or HMAC-
    verifying an RSA public key).
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iat", "sub", "jti", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired("Access token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"Invalid access token: {exc}") from exc

    if payload.get("typ") != "access":
        raise TokenError("Wrong token type")

    return AccessTokenClaims(
        user_id=uuid.UUID(payload["sub"]),
        jti=payload["jti"],
        session_id=uuid.UUID(payload["sid"]),
        mfa=bool(payload.get("mfa", False)),
        issued_at=datetime.fromtimestamp(payload["iat"], tz=timezone.utc),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        scope=payload.get("scope", "user"),
    )


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------


async def issue_refresh_token(
    session: AsyncSession,
    user_id: uuid.UUID,
    family_id: Optional[uuid.UUID] = None,
    parent_id: Optional[uuid.UUID] = None,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> Tuple[str, RefreshToken]:
    """Create and persist a new refresh token; returns ``(plaintext, row)``."""
    raw = new_opaque_token(32)
    now = datetime.now(timezone.utc)
    row = RefreshToken(
        user_id=user_id,
        family_id=family_id or uuid.uuid4(),
        parent_id=parent_id,
        token_hash=hash_token(raw),
        issued_at=now,
        expires_at=now + timedelta(seconds=settings.refresh_token_ttl_seconds),
        user_agent=(user_agent or "")[:256] or None,
        ip_address=ip_address,
    )
    session.add(row)
    await session.flush()
    return raw, row


async def revoke_family(session: AsyncSession, family_id: uuid.UUID, reason: str) -> int:
    """Revoke every live token in a family. Returns the number revoked."""
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason[:64])
        .returning(RefreshToken.id)
    )
    revoked = len(result.fetchall())
    if revoked:
        logger.warning(f"Revoked {revoked} refresh token(s) in family {family_id}: {reason}")
    return revoked


async def _revoke_family_durably(session: AsyncSession, family_id: uuid.UUID, reason: str) -> int:
    """
    Revoke a family and COMMIT immediately.

    The commit is the whole point. Reuse detection reports failure by raising,
    and the caller's session context manager rolls back on exception -- which
    would undo the revocation and leave the stolen family alive. Committing
    before the raise makes the security action durable independently of how
    the request ends.
    """
    revoked = await revoke_family(session, family_id, reason)
    await session.commit()
    return revoked


async def rotate_refresh_token(
    session: AsyncSession,
    presented_token: str,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> Tuple[str, RefreshToken]:
    """
    Exchange a refresh token for its successor.

    Raises :class:`RefreshReuseDetected` -- after revoking the whole family --
    when the presented token was already used or already revoked.
    """
    token_hash = hash_token(presented_token)
    row = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )
    ).scalar_one_or_none()

    if row is None:
        # Unknown token: nothing to revoke, and we must not disclose whether
        # the token ever existed.
        raise TokenError("Invalid refresh token")

    now = datetime.now(timezone.utc)

    if row.revoked_at is not None:
        # Already-dead family being probed. Keep it dead.
        await _revoke_family_durably(session, row.family_id, "reuse_after_revocation")
        raise RefreshReuseDetected(
            "Refresh token belongs to a revoked family; all sessions terminated"
        )

    if row.used_at is not None:
        # THE reuse case: this token was already exchanged. Either the client
        # replayed it or an attacker stole it -- indistinguishable, so burn the
        # family.
        await _revoke_family_durably(session, row.family_id, "refresh_token_reuse")
        raise RefreshReuseDetected(
            "Refresh token reuse detected; all sessions in this family terminated"
        )

    if row.expires_at <= now:
        await _revoke_family_durably(session, row.family_id, "expired")
        raise TokenExpired("Refresh token expired")

    # Mark used and issue the successor inside the same transaction.
    row.used_at = now
    new_raw, new_row = await issue_refresh_token(
        session,
        user_id=row.user_id,
        family_id=row.family_id,
        parent_id=row.id,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    return new_raw, new_row


async def revoke_all_user_tokens(session: AsyncSession, user_id: uuid.UUID, reason: str) -> int:
    """Kill every session for a user (password change, MFA reset, logout-all)."""
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason[:64])
        .returning(RefreshToken.id)
    )
    return len(result.fetchall())
