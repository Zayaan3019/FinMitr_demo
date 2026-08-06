"""
Cryptographic primitives for FinGuru.

Two distinct concerns live here:

1. **Password hashing** -- Argon2id, not bcrypt.

   bcrypt is compute-hard but only ~4 KiB memory-hard, which is nothing to a
   modern GPU: thousands of bcrypt cores fit on one die. Argon2id forces each
   guess to allocate and randomly address ``memory_cost`` KiB (64 MiB by
   default here) for ``time_cost`` passes. An attacker's parallelism is then
   bounded by memory bandwidth and capacity rather than ALU count, which is the
   resource that scales worst for GPUs and ASICs. The ``id`` variant
   interleaves Argon2i's data-independent first pass (side-channel resistant)
   with Argon2d's data-dependent later passes (GPU-resistant), so it is the
   variant RFC 9106 recommends for password storage.

2. **Field-level encryption** -- AES-256-GCM for the TOTP seed
   (``users.mfa_secret_enc``). A TOTP seed is a bearer credential: a database
   dump containing plaintext seeds is a complete MFA bypass, so it is encrypted
   with a key that lives outside the database.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
from typing import Optional, Tuple

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Argon2id password hashing
# ---------------------------------------------------------------------------

_hasher = PasswordHasher(
    time_cost=settings.argon2_time_cost,
    memory_cost=settings.argon2_memory_cost,
    parallelism=settings.argon2_parallelism,
    hash_len=settings.argon2_hash_len,
    salt_len=settings.argon2_salt_len,
    type=Type.ID,  # Argon2id
)

# A pre-computed hash of a random password. Verifying against this on the
# "user does not exist" path costs the same as a real verification, so response
# time cannot be used to enumerate registered email addresses.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    """Hash a password with Argon2id. Returns a PHC-format string."""
    return _hasher.hash(password)


def verify_password(stored_hash: Optional[str], password: str) -> bool:
    """
    Verify ``password`` against ``stored_hash`` in constant work.

    When ``stored_hash`` is None (no such user) the same Argon2 work is still
    performed against a dummy hash before returning False, so the timing of a
    failed login does not reveal whether the account exists.
    """
    target = stored_hash or _DUMMY_HASH
    try:
        _hasher.verify(target, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"Password verification error: {exc}")
        return False
    # Never report success for the dummy path even in the (impossible) case of
    # a collision.
    return stored_hash is not None


def password_needs_rehash(stored_hash: str) -> bool:
    """True when the stored hash uses weaker parameters than current policy."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Opaque token hashing (refresh tokens, idempotency keys, API keys)
# ---------------------------------------------------------------------------


def hash_token(token: str) -> str:
    """
    SHA-256 of a high-entropy token.

    Refresh tokens are 256-bit random strings, not user-chosen passwords, so
    they are not brute-forceable and do not need a slow KDF. Using SHA-256
    keeps the token-rotation path fast enough to sit on the hot login path
    while still ensuring a database leak yields no usable tokens.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_opaque_token(nbytes: int = 32) -> str:
    """Generate a URL-safe, cryptographically random opaque token."""
    return secrets.token_urlsafe(nbytes)


def constant_time_compare(a: str, b: str) -> bool:
    """Timing-safe string comparison."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ---------------------------------------------------------------------------
# AES-256-GCM field encryption
# ---------------------------------------------------------------------------

_ENC_PREFIX = "v1:"


def _derive_key() -> bytes:
    """
    Resolve the 32-byte AES key.

    A configured key is used verbatim (base64 or raw 32 bytes). When unset we
    derive a deterministic development key from the JWT secret so the app is
    runnable locally -- production start-up refuses this via
    ``Settings.validate_production_settings``.
    """
    raw = settings.field_encryption_key
    if raw:
        try:
            decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            if len(decoded) == 32:
                return decoded
        except (ValueError, binascii.Error):
            # Not base64. Fall through to the raw-bytes and derived forms
            # below -- the key may legitimately have been supplied either way,
            # and there is nothing to log: this branch is a format probe, not
            # a failure.
            pass
        if len(raw.encode()) == 32:
            return raw.encode()
        return hashlib.sha256(raw.encode()).digest()
    return hashlib.sha256(b"finguru-field-encryption-dev/" + settings.jwt_secret.encode()).digest()


def encrypt_field(plaintext: str, aad: str = "") -> str:
    """
    Encrypt a sensitive field. Returns ``v1:<b64(nonce||ciphertext)>``.

    ``aad`` binds the ciphertext to a context (we pass the user id), so a
    ciphertext cannot be lifted from one row into another.
    """
    key = _derive_key()
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
    return _ENC_PREFIX + base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt_field(ciphertext: str, aad: str = "") -> str:
    """Decrypt a field produced by :func:`encrypt_field`."""
    if not ciphertext.startswith(_ENC_PREFIX):
        raise ValueError("Unrecognised ciphertext format")
    blob = base64.urlsafe_b64decode(ciphertext[len(_ENC_PREFIX) :])
    nonce, ct = blob[:12], blob[12:]
    key = _derive_key()
    return AESGCM(key).decrypt(nonce, ct, aad.encode("utf-8")).decode("utf-8")


def try_decrypt_field(ciphertext: Optional[str], aad: str = "") -> Optional[str]:
    """Decrypt, returning None on any failure rather than raising."""
    if not ciphertext:
        return None
    try:
        return decrypt_field(ciphertext, aad)
    except Exception as exc:
        logger.error(f"Field decryption failed: {type(exc).__name__}")
        return None


# ---------------------------------------------------------------------------
# Webhook signature verification (AA callbacks, PHASE 3)
# ---------------------------------------------------------------------------


def sign_payload(payload: bytes, secret: str) -> str:
    """HMAC-SHA256 hex signature of a webhook payload."""
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Timing-safe verification of a webhook signature."""
    expected = sign_payload(payload, secret)
    return hmac.compare_digest(expected, (signature or "").strip().lower())


def split_hash(value: str) -> Tuple[str, str]:  # pragma: no cover - helper
    """Split a PHC string into (params, digest) for diagnostics."""
    parts = value.rsplit("$", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (value, "")
