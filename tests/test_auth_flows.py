"""
DEFINITION OF DONE #3 -- replaying a refresh token revokes the whole family.

Plus the rest of the PHASE 1 identity surface: Argon2id, constant-time
failure, lockout, MFA gating, and server-side revocation.

The reuse-detection test is the important one and it deserves its rationale
spelled out. Refresh tokens are long-lived bearer credentials; if one is
stolen, both the attacker and the legitimate user hold a valid copy. Rotation
alone does not help -- it just means whoever refreshes second gets rejected,
and there is no way to tell which one that was. So rotation must be paired with
*reuse detection*: the moment a token that has already been rotated is
presented again, one of the two holders is an attacker, and the correct
response is to distrust both. The entire family is revoked and both parties are
forced back to a password (and MFA) login, where the attacker fails.

This is the OAuth 2.1 / BCP guidance for public clients, and it converts a
silent, indefinite compromise into a bounded one that the user notices.
"""

from __future__ import annotations

import time
import uuid

import pytest

from tests.conftest import login, make_user

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# DoD #3: refresh rotation and reuse detection
# ---------------------------------------------------------------------------


async def test_refresh_rotates_and_retires_the_presented_token(alice):
    """A rotated token is single-use: presenting it twice is the trigger."""
    from app.auth.service import refresh_session
    from app.db.session import system_session

    await login(alice)
    original = alice.refresh_token

    async with system_session() as session:
        _user, access2, refresh2, _ttl = await refresh_session(session, original)
        await session.commit()

    assert refresh2 != original, "refresh token was not rotated"
    assert access2, "no access token minted on refresh"


async def test_replaying_a_refresh_token_revokes_the_entire_family(alice):
    """
    **The definition-of-done test.**

    Timeline:
      1. Alice logs in            -> R1
      2. Alice refreshes normally -> R2   (R1 is now retired)
      3. An attacker replays R1          <- reuse detected
      4. R2 must also be dead.

    Step 4 is the whole point. If only R1 were rejected, the attacker's copy
    of R2 -- or the user's -- would keep working and the theft would continue
    undetected.
    """
    from app.auth.service import refresh_session
    from app.auth.tokens import TokenError
    from app.db.session import system_session

    await login(alice)
    r1 = alice.refresh_token

    async with system_session() as session:
        _u, _a, r2, _t = await refresh_session(session, r1)
        await session.commit()

    # 3. Replay the retired token.
    with pytest.raises((TokenError, Exception)) as exc_info:
        async with system_session() as session:
            await refresh_session(session, r1)
    assert (
        "reuse" in str(exc_info.value).lower() or "invalid" in str(exc_info.value).lower()
    ), f"replay was rejected, but not as reuse: {exc_info.value}"

    # 4. The descendant must be dead too. This is the assertion that failed
    #    during development: revocation was rolled back along with the
    #    exception that reported it, leaving the stolen family alive.
    with pytest.raises(Exception) as exc_info:
        async with system_session() as session:
            await refresh_session(session, r2)
            await session.commit()

    assert exc_info.value is not None, (
        "FAMILY NOT REVOKED: R2 still works after R1 was replayed. Reuse "
        "detection must revoke the family durably -- commit before raising."
    )


async def test_family_revocation_is_durable_across_sessions(alice):
    """
    Verifies the fix directly at the storage layer.

    Reuse detection reports failure by raising, and the caller's session
    context manager rolls back on exception. If revocation shares that
    transaction it is undone -- the row goes back to ``revoked_at IS NULL`` and
    the stolen family lives on. So it must be committed on its own before the
    exception propagates. Here we check the persisted rows, not the exception.
    """
    from sqlalchemy import select

    from app.auth.service import refresh_session
    from app.db.models import RefreshToken
    from app.db.session import system_session

    await login(alice)
    r1 = alice.refresh_token

    async with system_session() as session:
        _u, _a, _r2, _t = await refresh_session(session, r1)
        await session.commit()

    try:
        async with system_session() as session:
            await refresh_session(session, r1)
    except Exception:
        pass

    async with system_session() as session:
        rows = (
            (
                await session.execute(
                    select(RefreshToken).where(RefreshToken.user_id == alice.user_id)
                )
            )
            .scalars()
            .all()
        )

    assert rows, "no refresh tokens found for the user"
    live = [r for r in rows if r.revoked_at is None]
    assert not live, (
        f"{len(live)} refresh token(s) survived family revocation: " f"{[str(r.id) for r in live]}"
    )


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def test_passwords_are_hashed_with_argon2id():
    """
    Argon2id, not bcrypt -- and the parameter string proves which.

    bcrypt is compute-hard but *memory-light*: an attacker can run tens of
    thousands of parallel guesses on a GPU because each costs ~4 KiB of state.
    Argon2id forces each guess to hold ``m`` KiB simultaneously, so parallelism
    is bounded by memory bandwidth rather than core count. At m=64 MiB a 24 GiB
    GPU fits roughly 380 concurrent guesses instead of tens of thousands. The
    'id' variant combines Argon2i's side-channel resistance for the first pass
    with Argon2d's stronger time-memory tradeoff resistance afterwards.
    """
    from app.core.crypto import hash_password, verify_password

    digest = hash_password("Correct-Horse-Battery-Staple-9!")
    assert digest.startswith("$argon2id$"), f"not Argon2id: {digest[:32]}"
    assert verify_password(digest, "Correct-Horse-Battery-Staple-9!")
    assert not verify_password(digest, "wrong-password-entirely")


def test_the_same_password_produces_different_hashes():
    """Per-hash salt: identical passwords must not collide in the database."""
    from app.core.crypto import hash_password

    assert hash_password("Correct-Horse-Battery-Staple-9!") != hash_password(
        "Correct-Horse-Battery-Staple-9!"
    )


def test_verifying_against_a_missing_hash_still_does_the_work():
    """
    Constant-time failure path.

    ``verify_password(None, ...)`` verifies against a dummy hash instead of
    returning early. Without that, an unknown email returns in microseconds
    while a known one takes ~100 ms, and the login endpoint becomes a free
    account-enumeration oracle.
    """
    from app.core.crypto import hash_password, verify_password

    known = hash_password("Correct-Horse-Battery-Staple-9!")

    start = time.perf_counter()
    assert verify_password(known, "wrong-password") is False
    known_elapsed = time.perf_counter() - start

    start = time.perf_counter()
    assert verify_password(None, "wrong-password") is False
    unknown_elapsed = time.perf_counter() - start

    # Generous bound: this asserts the work happens at all, not a hardened
    # timing guarantee. A short-circuit return would be orders of magnitude
    # faster and fails comfortably.
    ratio = max(known_elapsed, unknown_elapsed) / max(min(known_elapsed, unknown_elapsed), 1e-9)
    assert ratio < 10, (
        f"unknown-user path took {unknown_elapsed*1000:.2f}ms vs "
        f"{known_elapsed*1000:.2f}ms for a known user (ratio {ratio:.1f}x) -- "
        f"this leaks which emails are registered"
    )


# ---------------------------------------------------------------------------
# Login, lockout and enumeration
# ---------------------------------------------------------------------------


async def test_login_with_an_unknown_email_and_a_wrong_password_look_identical(client):
    """The response must not distinguish 'no such user' from 'wrong password'."""
    known = await make_user("enum")

    unknown = await client.post(
        "/api/v1/auth/login",
        json={
            "email": f"nobody-{uuid.uuid4().hex[:8]}@example.com",
            "password": "whatever-long-enough",
        },
    )
    wrong = await client.post(
        "/api/v1/auth/login",
        json={"email": known.email, "password": "definitely-not-the-password"},
    )

    assert (
        unknown.status_code == wrong.status_code
    ), f"status differs: unknown={unknown.status_code} wrong={wrong.status_code}"
    assert unknown.json().get("detail") == wrong.json().get(
        "detail"
    ), f"message differs:\n  unknown: {unknown.json()}\n  wrong:   {wrong.json()}"


async def test_repeated_failures_lock_the_account(clean_redis):
    """
    Lockout bounds online guessing.

    The correct password is then rejected too -- deliberately. A lockout that
    still admits the right password protects nothing once it is guessed.
    """
    from app.auth.service import AccountLocked, AuthError, authenticate
    from app.core.config import settings
    from app.db.session import system_session

    user = await make_user("lockout")

    for _ in range(settings.auth_max_failed_attempts + 1):
        try:
            async with system_session() as session:
                await authenticate(session, email=user.email, password="wrong-password-here")
        except AuthError:
            # Includes AccountLocked once the threshold is crossed.
            pass

    # The correct password is now refused too. Deliberate: a lockout that still
    # admits the right password protects nothing once it has been guessed.
    with pytest.raises(AccountLocked):
        async with system_session() as session:
            await authenticate(session, email=user.email, password=user.password)


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


async def test_logout_everywhere_kills_a_live_access_token(client, alice, clean_redis):
    """
    JWTs are self-validating, so expiry alone means a stolen token stays good
    until it lapses. The ``jti``/``sid``/``uid`` denylist gives revocation
    real teeth, with a TTL equal to the token's remaining lifetime so the
    denylist cannot grow without bound.
    """
    await login(alice)

    before = await client.get("/api/v1/stats", headers=alice.auth_header)
    assert before.status_code == 200, f"token rejected before logout: {before.text}"

    # The real route, not the service call: `logout_everywhere` only revokes
    # refresh tokens. Killing the *access* token requires the jti denylist, and
    # the route is where the two are combined.
    out = await client.post("/api/v1/auth/logout", headers=alice.auth_header)
    assert out.status_code == 200, f"logout failed: {out.status_code} {out.text}"

    after = await client.get("/api/v1/stats", headers=alice.auth_header)
    assert after.status_code == 401, (
        f"revoked access token still works (status {after.status_code}) -- "
        f"the denylist is not being consulted"
    )


async def test_an_algorithm_confusion_token_is_rejected(alice):
    """
    ``alg: none`` and algorithm substitution are the classic JWT attacks.

    Defended by pinning ``algorithms=[...]`` at decode time; a decoder that
    trusts the token's own header lets an attacker mint anything.
    """
    import base64
    import json

    from app.auth.tokens import decode_access_token

    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(
        b"="
    )
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": str(alice.user_id), "jti": uuid.uuid4().hex, "scope": "access"}).encode()
    ).rstrip(b"=")
    forged = f"{header.decode()}.{payload.decode()}."

    with pytest.raises(Exception):
        await decode_access_token(forged)


# ---------------------------------------------------------------------------
# MFA
# ---------------------------------------------------------------------------


async def test_an_mfa_pending_token_cannot_reach_data_routes(client, alice):
    """
    The intermediate token issued between password and TOTP carries scope
    ``mfa_pending`` and must be useless for anything but completing the
    challenge -- otherwise MFA is decorative.
    """
    from app.auth.service import _mint_mfa_challenge
    from app.db.models import User
    from app.db.session import system_session
    from sqlalchemy import select

    async with system_session() as session:
        row = (await session.execute(select(User).where(User.id == alice.user_id))).scalar_one()
        challenge = _mint_mfa_challenge(row)

    response = await client.get(
        "/api/v1/transactions", headers={"Authorization": f"Bearer {challenge}"}
    )
    assert response.status_code in (401, 403), (
        f"an mfa_pending token reached a data route (status "
        f"{response.status_code}) -- the MFA gate can be skipped"
    )


async def test_totp_codes_are_single_use(clean_redis):
    """
    Replay protection.

    A TOTP code is valid for a 30-second step, so an attacker who observes one
    (shoulder-surfing, a phishing proxy, a leaked screenshot) can reuse it
    within that window unless the server remembers it was spent.
    """
    import pyotp

    from app.auth.mfa import verify_code_once

    secret = pyotp.random_base32()
    code = pyotp.TOTP(secret).now()
    user_id = uuid.uuid4()

    ok, reason = await verify_code_once(secret, code, user_id)
    assert ok, f"a freshly generated code was rejected: {reason}"

    ok, reason = await verify_code_once(secret, code, user_id)
    assert not ok, "the same TOTP code was accepted twice -- codes must be single-use"
    assert (
        reason == "code_already_used"
    ), f"the replay was rejected, but as '{reason}' rather than as a replay"
