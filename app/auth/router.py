"""
Authentication endpoints (PHASE 1).

Note what is *absent*: no route here, or anywhere else in the application,
takes a user identifier from the client. Registration takes an email, and every
subsequent route derives the user from the bearer token.
"""

from __future__ import annotations

from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import denylist
from app.auth.dependencies import (
    Principal,
    client_ip,
    get_current_user,
    get_mfa_pending_principal,
    get_system_session,
)
from app.auth.rate_limit import enforce_auth_rate_limit
from app.auth.schemas import (
    LoginRequest,
    MFAChallengeResponse,
    MFAEnrolResponse,
    MFAVerifyRequest,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    SimpleResponse,
    TokenResponse,
)
from app.auth.service import (
    AccountLocked,
    AuthError,
    MFARequired,
    authenticate,
    begin_mfa_enrolment,
    confirm_mfa_enrolment,
    logout_everywhere,
    refresh_session,
    register_user,
    verify_mfa_challenge,
)
from app.core.logging import get_logger
from app.db.models import User
from app.ops.audit import AuditAction, write_audit

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _auth_error(exc: AuthError) -> HTTPException:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else {}
    if isinstance(exc, AccountLocked):
        headers = {"Retry-After": str(exc.retry_after_seconds)}
    return HTTPException(status_code=exc.status_code, detail=exc.message, headers=headers)


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
async def register(
    payload: RegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_system_session),
) -> TokenResponse:
    ip = client_ip(request)
    await enforce_auth_rate_limit(ip, payload.email, scope="register")

    try:
        user = await register_user(session, payload.email, payload.password, ip)
    except AuthError as exc:
        raise _auth_error(exc)

    from app.auth.service import issue_session

    access, refresh, expires_in = await issue_session(
        session,
        user,
        mfa_satisfied=False,
        user_agent=request.headers.get("user-agent"),
        ip_address=ip,
    )
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
        mfa_enabled=False,
        mfa_satisfied=False,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={401: {"model": MFAChallengeResponse}},
    summary="Exchange credentials for tokens",
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_system_session),
):
    ip = client_ip(request)
    await enforce_auth_rate_limit(ip, payload.email, scope="login")

    try:
        user, access, refresh, expires_in = await authenticate(
            session,
            email=payload.email,
            password=payload.password,
            totp_code=payload.totp_code,
            user_agent=request.headers.get("user-agent"),
            ip_address=ip,
        )
    except MFARequired as exc:
        response.status_code = status.HTTP_401_UNAUTHORIZED
        return MFAChallengeResponse(mfa_token=exc.mfa_token)
    except AuthError as exc:
        raise _auth_error(exc)

    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
        mfa_enabled=bool(user.mfa_enabled),
        mfa_satisfied=bool(user.mfa_enabled),
    )


@router.post(
    "/mfa/verify",
    response_model=TokenResponse,
    summary="Complete a login that stopped at the second factor",
)
async def mfa_verify(
    payload: MFAVerifyRequest,
    request: Request,
    principal: Principal = Depends(get_mfa_pending_principal),
    session: AsyncSession = Depends(get_system_session),
) -> TokenResponse:
    ip = client_ip(request)
    await enforce_auth_rate_limit(ip, principal.email, scope="mfa")

    user = (await session.execute(select(User).where(User.id == principal.user_id))).scalar_one()

    try:
        access, refresh, expires_in = await verify_mfa_challenge(
            session,
            user,
            payload.code,
            user_agent=request.headers.get("user-agent"),
            ip_address=ip,
        )
    except AuthError as exc:
        raise _auth_error(exc)

    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
        mfa_enabled=True,
        mfa_satisfied=True,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate a refresh token",
    description=(
        "Refresh tokens rotate on every use. Presenting a token that has "
        "already been exchanged revokes the entire token family, because "
        "theft and replay are indistinguishable from the server's side."
    ),
)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    session: AsyncSession = Depends(get_system_session),
) -> TokenResponse:
    ip = client_ip(request)
    await enforce_auth_rate_limit(ip, None, scope="refresh")

    try:
        user, access, new_refresh, expires_in = await refresh_session(
            session,
            payload.refresh_token,
            user_agent=request.headers.get("user-agent"),
            ip_address=ip,
        )
    except AuthError as exc:
        raise _auth_error(exc)

    return TokenResponse(
        access_token=access,
        refresh_token=new_refresh,
        expires_in=expires_in,
        mfa_enabled=bool(user.mfa_enabled),
        mfa_satisfied=bool(user.mfa_enabled),
    )


@router.post("/logout", response_model=SimpleResponse, summary="Revoke this session")
async def logout(
    request: Request,
    principal: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_system_session),
) -> SimpleResponse:
    # Denylist the presented access token immediately; revoke the refresh
    # family so it cannot be used to mint a replacement.
    await denylist.revoke_jti(principal.jti, principal.token_expires_at, "logout")
    revoked = await logout_everywhere(session, principal.user_id, "logout")

    await write_audit(
        AuditAction.LOGOUT,
        resource=f"user:{principal.user_id}",
        actor=principal.email,
        actor_user_id=principal.user_id,
        after={"refresh_tokens_revoked": revoked},
        ip_address=client_ip(request),
    )
    return SimpleResponse(detail=f"Signed out; {revoked} refresh token(s) revoked")


@router.post(
    "/mfa/enrol",
    response_model=MFAEnrolResponse,
    summary="Begin TOTP enrolment",
)
async def mfa_enrol(
    principal: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_system_session),
) -> MFAEnrolResponse:
    user = (await session.execute(select(User).where(User.id == principal.user_id))).scalar_one()
    secret, uri = await begin_mfa_enrolment(session, user)
    return MFAEnrolResponse(secret=secret, provisioning_uri=uri)


@router.post(
    "/mfa/confirm",
    response_model=TokenResponse,
    summary="Activate TOTP and receive an MFA-satisfied token pair",
)
async def mfa_confirm(
    payload: MFAVerifyRequest,
    request: Request,
    principal: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_system_session),
) -> TokenResponse:
    user = (await session.execute(select(User).where(User.id == principal.user_id))).scalar_one()

    try:
        await confirm_mfa_enrolment(session, user, payload.code)
    except AuthError as exc:
        raise _auth_error(exc)

    # Re-issue with mfa=True so the caller can proceed to bank linkage without
    # a second round trip through /login.
    from app.auth.service import issue_session

    access, refresh_token, expires_in = await issue_session(
        session,
        user,
        mfa_satisfied=True,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip(request),
    )
    return TokenResponse(
        access_token=access,
        refresh_token=refresh_token,
        expires_in=expires_in,
        mfa_enabled=True,
        mfa_satisfied=True,
    )


@router.get("/me", response_model=MeResponse, summary="Current principal")
async def me(
    principal: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_system_session),
) -> MeResponse:
    user = (await session.execute(select(User).where(User.id == principal.user_id))).scalar_one()
    return MeResponse(
        user_id=str(user.id),
        email=user.email_display,
        mfa_enabled=bool(user.mfa_enabled),
        mfa_satisfied=principal.mfa_satisfied,
        created_at=user.created_at.astimezone(timezone.utc).isoformat(),
    )
