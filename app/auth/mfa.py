"""
TOTP multi-factor authentication (PHASE 1).

MFA is *mandatory before any bank linkage*: consent creation and FI fetch both
require an access token whose ``mfa`` claim is true. Rationale -- a password
alone should never be sufficient to attach a real bank account to an account
an attacker controls, because the AA consent flow then hands that attacker a
continuing feed of someone's financial life.

Replay protection: a valid TOTP code is single-use. RFC 6238 codes stay valid
for a whole step (plus the drift window), so without a used-code cache an
attacker who shoulder-surfs one code has ~60 seconds to reuse it.
"""

from __future__ import annotations

import uuid
from typing import Optional, Tuple

import pyotp

from app.core.config import settings
from app.core.crypto import decrypt_field, encrypt_field
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)

_USED_CODE_PREFIX = "finguru:mfa:used:"


def generate_secret() -> str:
    """Generate a fresh base32 TOTP seed."""
    return pyotp.random_base32()


def encrypt_secret(secret: str, user_id: uuid.UUID) -> str:
    """
    Encrypt the seed for storage, bound to the owning user id.

    The AAD binding means a ciphertext copied into another user's row fails to
    decrypt rather than silently authorising the wrong person.
    """
    return encrypt_field(secret, aad=str(user_id))


def decrypt_secret(ciphertext: str, user_id: uuid.UUID) -> str:
    return decrypt_field(ciphertext, aad=str(user_id))


def provisioning_uri(secret: str, email: str) -> str:
    """otpauth:// URI for authenticator apps / QR codes."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=settings.mfa_issuer)


def verify_code(secret: str, code: str) -> bool:
    """Verify a TOTP code with a +/- one-step drift window."""
    if not code or not code.strip().isdigit():
        return False
    try:
        return pyotp.TOTP(secret).verify(code.strip(), valid_window=settings.mfa_totp_window)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"TOTP verification error: {exc}")
        return False


async def verify_code_once(
    secret: str, code: str, user_id: uuid.UUID
) -> Tuple[bool, Optional[str]]:
    """
    Verify a code and burn it, so the same code cannot be replayed.

    Returns ``(ok, error_reason)``.
    """
    code = (code or "").strip()
    if not verify_code(secret, code):
        return False, "invalid_code"

    client = await get_redis()
    key = f"{_USED_CODE_PREFIX}{user_id}:{code}"
    if await client.exists(key):
        logger.warning(f"TOTP code replay blocked for user {user_id}")
        return False, "code_already_used"

    # Hold the code for two full steps plus the drift window.
    await client.set(key, "1", ex=30 * (2 * settings.mfa_totp_window + 2))
    return True, None


def current_code(secret: str) -> str:
    """Current TOTP value. Test-support and CLI enrolment aid only."""
    return pyotp.TOTP(secret).now()
