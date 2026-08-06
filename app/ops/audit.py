"""
Append-only audit trail (PHASE 6).

What must be recorded, and why:

* **auth events** -- who logged in, from where, and every failure. Without
  this, credential-stuffing looks identical to a forgetful user.
* **consent grants and revocations** -- under DEPA the consent artifact is the
  legal basis for holding someone's financial data. If you cannot show when
  consent started and ended, you cannot show the fetch was lawful.
* **data access** -- every read of transaction data, including reads performed
  on the user's behalf by an agent.
* **model decisions** -- which model version categorised which transaction, so
  a disputed categorisation months later is answerable.

Audit writes are deliberately *not* rolled back with the business transaction:
a failed operation is exactly the event you most want recorded. Each write
therefore uses its own session.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import AuditLog
from app.db.session import system_session

logger = get_logger(__name__)


class AuditAction:
    """Canonical action names. Strings drift; constants do not."""

    # Identity
    USER_REGISTERED = "user.registered"
    LOGIN_SUCCEEDED = "auth.login.succeeded"
    LOGIN_FAILED = "auth.login.failed"
    LOGIN_LOCKED = "auth.login.locked"
    LOGOUT = "auth.logout"
    TOKEN_REFRESHED = "auth.token.refreshed"
    TOKEN_REUSE_DETECTED = "auth.token.reuse_detected"
    MFA_ENROLLED = "auth.mfa.enrolled"
    MFA_CONFIRMED = "auth.mfa.confirmed"
    MFA_VERIFIED = "auth.mfa.verified"
    MFA_FAILED = "auth.mfa.failed"

    # Consent (DEPA)
    CONSENT_REQUESTED = "consent.requested"
    CONSENT_ACTIVATED = "consent.activated"
    CONSENT_REJECTED = "consent.rejected"
    CONSENT_REVOKED = "consent.revoked"
    CONSENT_STATUS_CHANGED = "consent.status_changed"

    # Data
    FI_FETCH_STARTED = "fi.fetch.started"
    FI_FETCH_COMPLETED = "fi.fetch.completed"
    FI_FETCH_FAILED = "fi.fetch.failed"
    FI_NOTIFICATION_RECEIVED = "fi.notification.received"
    DATA_ACCESSED = "data.accessed"
    DATA_DELETED = "data.deleted"
    DATA_INGESTED = "data.ingested"

    # Models & LLM
    MODEL_DECISION = "model.decision"
    LLM_CALL = "llm.call"
    LLM_BLOCKED = "llm.blocked"
    PII_REDACTED = "llm.pii_redacted"
    PROMPT_INJECTION_BLOCKED = "llm.prompt_injection_blocked"


async def write_audit(
    action: str,
    resource: str,
    actor: str = "system",
    actor_user_id: Optional[uuid.UUID] = None,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    request_id: Optional[str] = None,
    session: Optional[AsyncSession] = None,
) -> None:
    """
    Append one audit record.

    Never raises: an audit failure must not take down the request it is
    describing. It is logged loudly instead, and monitoring alerts on it.
    """
    entry = AuditLog(
        actor=actor[:128],
        actor_user_id=actor_user_id,
        action=action[:64],
        resource=resource[:128],
        before=before,
        after=after,
        ip_address=ip_address,
        request_id=request_id,
    )
    try:
        if session is not None:
            session.add(entry)
            await session.flush()
        else:
            async with system_session() as own:
                own.add(entry)
    except Exception as exc:
        logger.error(
            f"AUDIT WRITE FAILED action={action} resource={resource}: {exc}",
            exc_info=True,
        )


def scrub(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Remove secrets before they reach the audit log.

    The audit trail is widely readable inside an organisation; it must record
    that something happened without becoming a second copy of the credential.
    """
    if not payload:
        return payload
    redacted_keys = {
        "password",
        "new_password",
        "current_password",
        "token",
        "access_token",
        "refresh_token",
        "mfa_secret",
        "mfa_secret_enc",
        "secret",
        "argon2_hash",
        "code",
        "totp_code",
        "client_secret",
        "authorization",
    }
    return {k: ("***" if k.lower() in redacted_keys else v) for k, v in payload.items()}
