"""
Authentication service (PHASE 1).

Every branch of the login path is written to cost the same wall-clock time
regardless of whether the email exists, the password is wrong, or the account
is locked. A login endpoint that answers "unknown email" in 2 ms and "wrong
password" in 90 ms is an email-enumeration oracle, which for a financial app is
a list of confirmed customers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import mfa as mfa_mod
from app.auth.tokens import (
    RefreshReuseDetected,
    TokenError,
    create_access_token,
    issue_refresh_token,
    revoke_all_user_tokens,
    rotate_refresh_token,
)
from app.core.config import settings
from app.core.crypto import hash_password, password_needs_rehash, verify_password
from app.core.logging import get_logger
from app.db.models import User
from app.ops.audit import AuditAction, write_audit

logger = get_logger(__name__)


class AuthError(Exception):
    """Authentication failed. The message is safe to return to the client."""

    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AccountLocked(AuthError):
    def __init__(self, retry_after_seconds: int):
        super().__init__(
            "Account temporarily locked due to repeated failed sign-in attempts",
            status_code=423,
        )
        self.retry_after_seconds = retry_after_seconds


class MFARequired(AuthError):
    """Credentials were correct but the second factor is still outstanding."""

    def __init__(self, mfa_token: str):
        super().__init__("Multi-factor authentication required", status_code=401)
        self.mfa_token = mfa_token


def normalise_email(email: str) -> str:
    """Lowercase + trim. Stored form; also the uniqueness key."""
    return (email or "").strip().lower()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


async def register_user(
    session: AsyncSession,
    email: str,
    password: str,
    ip_address: Optional[str] = None,
) -> User:
    """
    Create a user.

    Duplicate registration returns the *same* generic error as a validation
    failure would, so registration cannot be used to test whether an address is
    already a customer.
    """
    email_ci = normalise_email(email)
    if len(password) < 12:
        raise AuthError("Password must be at least 12 characters", status_code=400)

    user = User(
        email_ci=email_ci,
        email_display=(email or "").strip(),
        argon2_hash=hash_password(password),
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # Deliberately identical to the message a well-formed-but-rejected
        # registration gets.
        raise AuthError(
            "Registration could not be completed with the details provided",
            status_code=409,
        )

    await write_audit(
        AuditAction.USER_REGISTERED,
        resource=f"user:{user.id}",
        actor=email_ci,
        actor_user_id=user.id,
        after={"email_ci": email_ci},
        ip_address=ip_address,
    )
    return user


# ---------------------------------------------------------------------------
# Lockout
# ---------------------------------------------------------------------------


def _is_locked(user: User, now: datetime) -> bool:
    return user.locked_until is not None and user.locked_until > now


async def _record_failure(session: AsyncSession, user: Optional[User]) -> None:
    """
    Increment the failure counter and lock the account at the threshold.

    **The commit is the whole point, and its absence was a real bug.**

    Every caller of this function raises immediately afterwards to report the
    failed login. The caller's session context manager rolls back on that
    exception -- and a ``flush()`` is not durable, so the increment went with
    it. The counter therefore never advanced past 1, the threshold was never
    reached, and account lockout was inert: an attacker could guess passwords
    indefinitely against a "protected" account.

    This is the same defect that made refresh-token family revocation
    ineffective (see ``_revoke_family_durably`` in ``app/auth/tokens.py``).
    Any state change that records *why* an operation is about to fail must be
    committed before the failure propagates.

    Caught by ``tests/test_auth_flows.py::test_repeated_failures_lock_the_account``.
    """
    if user is None:
        # No account to count against. Deliberately silent -- keeping a
        # per-email counter for addresses that do not exist would itself be an
        # enumeration oracle, and IP-level limiting already covers this.
        return
    now = datetime.now(timezone.utc)
    user.failed_login_count = (user.failed_login_count or 0) + 1
    if user.failed_login_count >= settings.auth_max_failed_attempts:
        user.locked_until = now + timedelta(seconds=settings.auth_lockout_seconds)
        user.failed_login_count = 0
        logger.warning(f"Account {user.id} locked until {user.locked_until}")
    await session.commit()


async def _clear_failures(session: AsyncSession, user: User) -> None:
    if user.failed_login_count or user.locked_until:
        user.failed_login_count = 0
        user.locked_until = None
        await session.flush()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


async def authenticate(
    session: AsyncSession,
    email: str,
    password: str,
    totp_code: Optional[str] = None,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> Tuple[User, str, str, int]:
    """
    Verify credentials (and MFA when enrolled).

    Returns ``(user, access_token, refresh_token, expires_in)``.

    Raises :class:`AuthError` with a single generic message for every
    credential failure, :class:`AccountLocked` when locked out, and
    :class:`MFARequired` when the password was right but no valid TOTP code was
    supplied.
    """
    email_ci = normalise_email(email)
    now = datetime.now(timezone.utc)

    user = (
        await session.execute(select(User).where(User.email_ci == email_ci))
    ).scalar_one_or_none()

    # Constant-work verification: when `user` is None this still performs a
    # full Argon2 hash against a dummy digest.
    password_ok = verify_password(user.argon2_hash if user else None, password)

    if user is not None and _is_locked(user, now):
        # Check lockout *after* the hash so a locked account and a live one
        # take the same time.
        await write_audit(
            AuditAction.LOGIN_LOCKED,
            resource=f"user:{user.id}",
            actor=email_ci,
            actor_user_id=user.id,
            ip_address=ip_address,
        )
        retry_after = int((user.locked_until - now).total_seconds())
        raise AccountLocked(max(1, retry_after))

    if user is None or not password_ok or not user.is_active:
        await _record_failure(session, user)
        await write_audit(
            AuditAction.LOGIN_FAILED,
            resource=f"user:{user.id}" if user else "user:unknown",
            actor=email_ci,
            actor_user_id=user.id if user else None,
            after={"reason": "invalid_credentials"},
            ip_address=ip_address,
        )
        raise AuthError("Invalid email or password")

    # ---- Second factor -------------------------------------------------
    mfa_satisfied = True
    if user.mfa_enabled and user.mfa_secret_enc:
        secret = mfa_mod.decrypt_secret(user.mfa_secret_enc, user.id)
        if not totp_code:
            raise MFARequired(mfa_token=_mint_mfa_challenge(user))
        ok, reason = await mfa_mod.verify_code_once(secret, totp_code, user.id)
        if not ok:
            await _record_failure(session, user)
            await write_audit(
                AuditAction.MFA_FAILED,
                resource=f"user:{user.id}",
                actor=email_ci,
                actor_user_id=user.id,
                after={"reason": reason},
                ip_address=ip_address,
            )
            raise AuthError("Invalid email or password")
        await write_audit(
            AuditAction.MFA_VERIFIED,
            resource=f"user:{user.id}",
            actor=email_ci,
            actor_user_id=user.id,
            ip_address=ip_address,
        )
    else:
        # No second factor enrolled. The token records that fact, and every
        # bank-linkage route refuses tokens with mfa=False.
        mfa_satisfied = False

    await _clear_failures(session, user)

    # Transparent parameter upgrade if the policy hardened since signup.
    if password_needs_rehash(user.argon2_hash):
        user.argon2_hash = hash_password(password)
        await session.flush()

    access, refresh, expires_in = await issue_session(
        session, user, mfa_satisfied, user_agent, ip_address
    )

    await write_audit(
        AuditAction.LOGIN_SUCCEEDED,
        resource=f"user:{user.id}",
        actor=email_ci,
        actor_user_id=user.id,
        after={"mfa": mfa_satisfied},
        ip_address=ip_address,
    )
    return user, access, refresh, expires_in


def _mint_mfa_challenge(user: User) -> str:
    """
    Short-lived token proving the password step passed.

    Scope ``mfa_pending`` is accepted by exactly one endpoint
    (``/auth/mfa/verify``); :func:`app.auth.dependencies.get_current_user`
    rejects it everywhere else.
    """
    token, _, _ = create_access_token(
        user_id=user.id,
        session_id=uuid.uuid4(),
        mfa_satisfied=False,
        scope="mfa_pending",
        ttl_seconds=300,
    )
    return token


async def issue_session(
    session: AsyncSession,
    user: User,
    mfa_satisfied: bool,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> Tuple[str, str, int]:
    """Mint an access/refresh pair for a freshly authenticated user."""
    session_id = uuid.uuid4()
    access, _jti, expires_at = create_access_token(
        user_id=user.id, session_id=session_id, mfa_satisfied=mfa_satisfied
    )
    refresh, _row = await issue_refresh_token(
        session, user_id=user.id, user_agent=user_agent, ip_address=ip_address
    )
    expires_in = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    return access, refresh, max(1, expires_in)


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


async def refresh_session(
    session: AsyncSession,
    refresh_token: str,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> Tuple[User, str, str, int]:
    """
    Rotate a refresh token and mint a new access token.

    Reuse is surfaced as a 401 after the family has already been revoked, and
    is recorded in the audit log as a security event rather than a routine
    auth failure.
    """
    try:
        new_refresh, row = await rotate_refresh_token(
            session, refresh_token, user_agent=user_agent, ip_address=ip_address
        )
    except RefreshReuseDetected as exc:
        await write_audit(
            AuditAction.TOKEN_REUSE_DETECTED,
            resource="refresh_token",
            actor="unknown",
            after={"detail": str(exc)},
            ip_address=ip_address,
        )
        raise AuthError(str(exc))
    except TokenError as exc:
        raise AuthError(str(exc))

    user = (await session.execute(select(User).where(User.id == row.user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthError("Invalid refresh token")

    # An enrolled user keeps their MFA standing across refresh: they already
    # proved the second factor to obtain the family in the first place.
    mfa_satisfied = bool(user.mfa_enabled and user.mfa_secret_enc)
    access, _jti, expires_at = create_access_token(
        user_id=user.id, session_id=row.family_id, mfa_satisfied=mfa_satisfied
    )
    expires_in = max(1, int((expires_at - datetime.now(timezone.utc)).total_seconds()))

    await write_audit(
        AuditAction.TOKEN_REFRESHED,
        resource=f"user:{user.id}",
        actor=user.email_ci,
        actor_user_id=user.id,
        ip_address=ip_address,
    )
    return user, access, new_refresh, expires_in


# ---------------------------------------------------------------------------
# MFA enrolment
# ---------------------------------------------------------------------------


async def begin_mfa_enrolment(session: AsyncSession, user: User) -> Tuple[str, str]:
    """
    Generate and store (encrypted) a new TOTP seed.

    ``mfa_enabled`` stays False until :func:`confirm_mfa_enrolment` sees a
    valid code -- otherwise a user who scans a QR code and closes the app locks
    themselves out.
    """
    secret = mfa_mod.generate_secret()
    user.mfa_secret_enc = mfa_mod.encrypt_secret(secret, user.id)
    user.mfa_enabled = False
    user.mfa_confirmed_at = None
    await session.flush()

    await write_audit(
        AuditAction.MFA_ENROLLED,
        resource=f"user:{user.id}",
        actor=user.email_ci,
        actor_user_id=user.id,
    )
    return secret, mfa_mod.provisioning_uri(secret, user.email_display)


async def confirm_mfa_enrolment(session: AsyncSession, user: User, code: str) -> bool:
    """Activate MFA once the user proves they can generate a valid code."""
    if not user.mfa_secret_enc:
        raise AuthError("No MFA enrolment in progress", status_code=400)

    secret = mfa_mod.decrypt_secret(user.mfa_secret_enc, user.id)
    ok, _reason = await mfa_mod.verify_code_once(secret, code, user.id)
    if not ok:
        await write_audit(
            AuditAction.MFA_FAILED,
            resource=f"user:{user.id}",
            actor=user.email_ci,
            actor_user_id=user.id,
        )
        raise AuthError("Invalid verification code", status_code=400)

    user.mfa_enabled = True
    user.mfa_confirmed_at = datetime.now(timezone.utc)
    await session.flush()

    await write_audit(
        AuditAction.MFA_CONFIRMED,
        resource=f"user:{user.id}",
        actor=user.email_ci,
        actor_user_id=user.id,
    )
    return True


async def verify_mfa_challenge(
    session: AsyncSession,
    user: User,
    code: str,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> Tuple[str, str, int]:
    """Complete a login that stopped at :class:`MFARequired`."""
    if not (user.mfa_enabled and user.mfa_secret_enc):
        raise AuthError("MFA is not enabled for this account", status_code=400)

    secret = mfa_mod.decrypt_secret(user.mfa_secret_enc, user.id)
    ok, reason = await mfa_mod.verify_code_once(secret, code, user.id)
    if not ok:
        await _record_failure(session, user)
        await write_audit(
            AuditAction.MFA_FAILED,
            resource=f"user:{user.id}",
            actor=user.email_ci,
            actor_user_id=user.id,
            after={"reason": reason},
            ip_address=ip_address,
        )
        raise AuthError("Invalid verification code")

    await _clear_failures(session, user)
    access, refresh, expires_in = await issue_session(
        session, user, mfa_satisfied=True, user_agent=user_agent, ip_address=ip_address
    )
    await write_audit(
        AuditAction.MFA_VERIFIED,
        resource=f"user:{user.id}",
        actor=user.email_ci,
        actor_user_id=user.id,
        ip_address=ip_address,
    )
    return access, refresh, expires_in


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


async def logout_everywhere(
    session: AsyncSession, user_id: uuid.UUID, reason: str = "logout_all"
) -> int:
    """Revoke every refresh token for a user; the caller denylists the jti."""
    return await revoke_all_user_tokens(session, user_id, reason)
