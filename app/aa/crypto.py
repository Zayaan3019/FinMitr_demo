"""
End-to-end encryption for Account Aggregator payloads (PHASE 3).

ReBIT specifies ECDH over Curve25519 with HKDF and AES-GCM. The property that
matters: the FIP encrypts to a key **FinGuru** generated, so the Account
Aggregator -- which relays the payload -- holds ciphertext it cannot read. The
AA is a consent broker, not a data custodian, and this is the mechanism that
makes that architectural claim true rather than merely stated.

This is a genuine implementation, not a stub. The mock transport in
``client.py`` performs the FIP side of the same exchange, so the sandbox
round-trip exercises real key agreement, real derivation and real
authenticated decryption.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Tuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from app.core.logging import get_logger

logger = get_logger(__name__)

_HKDF_INFO = b"finguru-aa-fi-payload-v1"


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


@dataclass
class EphemeralKeyPair:
    """One session's X25519 key pair plus the nonce sent with it."""

    private_key: X25519PrivateKey
    public_key_b64: str
    nonce_b64: str

    @property
    def nonce(self) -> bytes:
        return _b64d(self.nonce_b64)


def generate_key_pair() -> EphemeralKeyPair:
    """
    Fresh key material per FI session.

    Ephemeral per session, never reused: reuse would let a compromise of one
    session's key decrypt every historical payload, forfeiting forward secrecy.
    """
    private_key = X25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw
    )
    return EphemeralKeyPair(
        private_key=private_key,
        public_key_b64=_b64e(public_bytes),
        nonce_b64=_b64e(os.urandom(32)),
    )


def derive_shared_key(
    private_key: X25519PrivateKey,
    peer_public_key_b64: str,
    our_nonce_b64: str,
    peer_nonce_b64: str,
) -> bytes:
    """
    Derive the 256-bit AES key from the ECDH shared secret.

    Both nonces are XORed into the HKDF salt so the derived key depends on
    contributions from *both* parties. If only one side supplied entropy, that
    side could force key reuse across sessions.
    """
    peer_public = X25519PublicKey.from_public_bytes(_b64d(peer_public_key_b64))
    shared_secret = private_key.exchange(peer_public)

    our_nonce = _b64d(our_nonce_b64)
    peer_nonce = _b64d(peer_nonce_b64)
    length = max(len(our_nonce), len(peer_nonce))
    salt = bytes(
        a ^ b
        for a, b in zip(
            our_nonce.ljust(length, b"\x00"),
            peer_nonce.ljust(length, b"\x00"),
            strict=True,  # both padded to `length`; asserts that stays true
        )
    )

    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=_HKDF_INFO).derive(
        shared_secret
    )


def encrypt_payload(key: bytes, payload: Dict[str, Any]) -> str:
    """Encrypt an FI payload (the FIP side; used by the mock transport)."""
    iv = os.urandom(12)
    plaintext = json.dumps(payload, default=str).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(iv, plaintext, None)
    return _b64e(iv + ciphertext)


def decrypt_payload(key: bytes, encrypted_b64: str) -> Dict[str, Any]:
    """
    Decrypt an FI payload (the FIU side; the real path).

    AES-GCM is authenticated, so a payload tampered with in transit -- by a
    compromised AA or anyone on the path -- fails to decrypt rather than
    yielding altered transactions.
    """
    blob = _b64d(encrypted_b64)
    iv, ciphertext = blob[:12], blob[12:]
    plaintext = AESGCM(key).decrypt(iv, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))


def simulate_fip_encryption(
    fiu_public_key_b64: str, fiu_nonce_b64: str, payload: Dict[str, Any]
) -> Tuple[str, str, str]:
    """
    Perform the FIP half of the exchange.

    Used by the mock transport so the sandbox round-trip is a real ECDH
    agreement rather than a passthrough. Returns
    ``(encrypted_payload, fip_public_key_b64, fip_nonce_b64)``.
    """
    fip_keys = generate_key_pair()
    shared = derive_shared_key(
        fip_keys.private_key,
        peer_public_key_b64=fiu_public_key_b64,
        our_nonce_b64=fip_keys.nonce_b64,
        peer_nonce_b64=fiu_nonce_b64,
    )
    return (
        encrypt_payload(shared, payload),
        fip_keys.public_key_b64,
        fip_keys.nonce_b64,
    )
