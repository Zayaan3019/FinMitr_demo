"""
Account Aggregator endpoints (PHASE 3).

Every route derives ``user_id`` from the bearer token. Bank-linkage routes
additionally require ``require_mfa``, so a password-only session cannot attach
a real bank account.

Webhook routes are the exception to bearer auth -- they are called by the AA,
not by a user -- and are authenticated by HMAC signature instead.
"""

from __future__ import annotations

from datetime import timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aa.client import AAError, ConsentNotLive, MockAATransport, get_fiu_client
from app.aa.schemas import (
    ConsentNotification,
    ConsentResponse,
    ConsentStatus,
    CreateConsentRequest,
    FIFetchRequestBody,
    FIFetchResult,
    FINotification,
)
from app.aa.service import (
    create_consent,
    resolve_consent_owner,
    resolve_session_owner,
    revoke_consent,
    run_fi_session,
    sync_consent_status,
)
from app.auth.dependencies import Principal, client_ip, get_tenant_session, require_mfa
from app.core.config import settings
from app.core.crypto import verify_signature
from app.core.logging import get_logger
from app.db.models import Consent, FiSession
from app.db.session import tenant_session
from app.ops import outbox
from app.ops.idempotency import run_idempotent

logger = get_logger(__name__)

router = APIRouter(prefix="/aa", tags=["Account Aggregator"])

SANDBOX_NOTICE = (
    "FinGuru operates against Account Aggregator SANDBOX endpoints only. "
    "Becoming a registered FIU requires an RBI-licensed entity; this deployment "
    "is not one. See the README section 'FIU sandbox limitation'."
)


class ConsentListItem(BaseModel):
    consent_id: str
    status: str
    purpose_code: str
    purpose_text: Optional[str] = None
    expiry: str
    created_at: str
    aa_provider: str


class SandboxApproveRequest(BaseModel):
    consent_id: str
    approve: bool = True


# ---------------------------------------------------------------------------
# Consent lifecycle
# ---------------------------------------------------------------------------


@router.post(
    "/consents",
    response_model=ConsentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request an AA consent artifact",
    description=(
        "Creates a consent request with the Account Aggregator. FinGuru never "
        "sees a bank credential -- the user authenticates and approves at their "
        "AA. Requires MFA: a password alone must not be able to attach a bank "
        "account. " + SANDBOX_NOTICE
    ),
)
async def request_consent(
    payload: CreateConsentRequest,
    request: Request,
    principal: Principal = Depends(require_mfa),
    session: AsyncSession = Depends(get_tenant_session),
) -> ConsentResponse:
    if not settings.aa_enabled:
        raise HTTPException(status_code=503, detail="Account Aggregator is disabled")

    try:
        row = await create_consent(
            session,
            user_id=principal.user_id,
            request=payload.to_consent_request(),
            actor=principal.email,
            ip_address=client_ip(request),
        )
    except AAError as exc:
        raise HTTPException(status_code=502, detail=f"Account Aggregator error: {exc}")

    return ConsentResponse(
        consent_id=row.aa_consent_id,
        consent_handle=row.aa_consent_handle or "",
        status=row.status,
        purpose_code=row.purpose_code,
        purpose_text=row.purpose_text or "",
        expiry=row.expiry.astimezone(timezone.utc).isoformat(),
        approval_url=(
            f"{settings.aa_base_url}/consent/{row.aa_consent_handle}"
            if row.aa_consent_handle
            else None
        ),
        sandbox_only=settings.aa_sandbox_only,
    )


@router.get(
    "/consents",
    response_model=List[ConsentListItem],
    summary="List the caller's consents",
)
async def list_consents(
    principal: Principal = Depends(require_mfa),
    session: AsyncSession = Depends(get_tenant_session),
) -> List[ConsentListItem]:
    # No WHERE user_id clause is needed: RLS scopes this to the caller. It is
    # written without one deliberately, as a live demonstration that the
    # isolation is enforced at the SQL layer.
    rows = (
        (await session.execute(select(Consent).order_by(Consent.created_at.desc()))).scalars().all()
    )

    return [
        ConsentListItem(
            consent_id=row.aa_consent_id,
            status=row.status,
            purpose_code=row.purpose_code,
            purpose_text=row.purpose_text,
            expiry=row.expiry.astimezone(timezone.utc).isoformat(),
            created_at=row.created_at.astimezone(timezone.utc).isoformat(),
            aa_provider=row.aa_provider,
        )
        for row in rows
    ]


@router.get(
    "/consents/{consent_id}",
    response_model=ConsentListItem,
    summary="Refresh one consent's status from the AA",
)
async def get_consent(
    consent_id: str,
    principal: Principal = Depends(require_mfa),
    session: AsyncSession = Depends(get_tenant_session),
) -> ConsentListItem:
    # `consent_id` is an AA-issued identifier, not a FinGuru user id. RLS makes
    # another user's consent invisible here, so this is not an IDOR surface:
    # guessing a valid id yields the same 404 as an invented one.
    row = (
        await session.execute(select(Consent).where(Consent.aa_consent_id == consent_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Consent not found")

    await sync_consent_status(session, principal.user_id, row, principal.email)
    return ConsentListItem(
        consent_id=row.aa_consent_id,
        status=row.status,
        purpose_code=row.purpose_code,
        purpose_text=row.purpose_text,
        expiry=row.expiry.astimezone(timezone.utc).isoformat(),
        created_at=row.created_at.astimezone(timezone.utc).isoformat(),
        aa_provider=row.aa_provider,
    )


@router.delete(
    "/consents/{consent_id}",
    summary="Revoke a consent",
    description="Revokes at the Account Aggregator and locally. Data already "
    "fetched is retained only for the consent's declared data-life.",
)
async def delete_consent(
    consent_id: str,
    principal: Principal = Depends(require_mfa),
    session: AsyncSession = Depends(get_tenant_session),
) -> Dict[str, Any]:
    row = (
        await session.execute(select(Consent).where(Consent.aa_consent_id == consent_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Consent not found")

    acknowledged = await revoke_consent(session, principal.user_id, row, principal.email)
    return {
        "success": True,
        "consent_id": consent_id,
        "status": ConsentStatus.REVOKED.value,
        "aa_acknowledged": acknowledged,
    }


# ---------------------------------------------------------------------------
# FI fetch
# ---------------------------------------------------------------------------


@router.post(
    "/fi/fetch",
    response_model=FIFetchResult,
    summary="Fetch and ingest financial information",
    description=(
        "Runs consent check -> FI request -> FI fetch -> decrypt -> ingest. "
        "Supply an `Idempotency-Key` header so a client timeout and retry "
        "cannot start a second FI session."
    ),
)
async def fetch_fi(
    payload: FIFetchRequestBody,
    request: Request,
    principal: Principal = Depends(require_mfa),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> FIFetchResult:
    ip = client_ip(request)

    async def _operation():
        try:
            result = await run_fi_session(
                user_id=principal.user_id,
                consent_id=payload.consent_id,
                months=payload.months,
                actor=principal.email,
                ip_address=ip,
            )
        except ConsentNotLive as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
        except AAError as exc:
            raise HTTPException(status_code=502, detail=f"Account Aggregator error: {exc}")
        return 200, result

    _status, body = await run_idempotent(
        key=idempotency_key,
        scope="aa.fi.fetch",
        request_payload=payload.model_dump(),
        operation=_operation,
        user_id=principal.user_id,
    )
    return FIFetchResult(**body)


@router.get("/fi/sessions", summary="List the caller's FI fetch sessions")
async def list_sessions(
    principal: Principal = Depends(require_mfa),
    session: AsyncSession = Depends(get_tenant_session),
) -> List[Dict[str, Any]]:
    rows = (
        (await session.execute(select(FiSession).order_by(FiSession.created_at.desc()).limit(50)))
        .scalars()
        .all()
    )
    return [
        {
            "session_id": row.aa_session_id,
            "status": row.status,
            "from_date": row.from_date.isoformat(),
            "to_date": row.to_date.isoformat(),
            "records_ingested": row.records_ingested,
            "error": row.error,
            "created_at": row.created_at.astimezone(timezone.utc).isoformat(),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Webhooks (called by the AA, not by a user)
# ---------------------------------------------------------------------------


async def _verify_webhook(request: Request, signature: Optional[str]) -> bytes:
    """
    Authenticate an AA callback by HMAC.

    Without this, anyone who learns a session id could POST a fake "data ready"
    notification. Verified against the raw body -- re-serialising JSON before
    checking a signature is how signature checks get quietly bypassed, because
    any difference in whitespace or key order changes the bytes that were
    actually signed.
    """
    body = await request.body()
    if not verify_signature(body, signature or "", settings.aa_webhook_secret):
        logger.warning("Rejected AA webhook with invalid signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )
    return body


async def _authenticated_payload(request: Request, model):
    """
    Verify the HMAC **first**, then parse.

    Declaring the body as a Pydantic parameter would have FastAPI validate it
    before the handler runs, so a forged request with a malformed body got a
    422 -- schema feedback on an unauthenticated request, and the signature
    check never ran at all. Nothing leaked, but the ordering was wrong: an
    unauthenticated caller should not be able to reach the parser, let alone
    learn the shape it expects.

    Now every forged webhook gets exactly one answer -- 401 -- regardless of
    what it sends.
    """
    body = await _verify_webhook(request, request.headers.get("X-Signature"))
    try:
        return model.model_validate_json(body)
    except ValidationError as exc:
        # Reached only by an authenticated caller, so echoing the detail is
        # safe: it is the AA's own malformed payload.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed AA notification: {exc.error_count()} field error(s)",
        )


@router.post("/webhooks/consent", summary="AA consent status notification")
async def consent_webhook(request: Request) -> Dict[str, Any]:
    payload: ConsentNotification = await _authenticated_payload(request, ConsentNotification)

    identifier = payload.consent_id or payload.consent_handle
    if not identifier:
        raise HTTPException(status_code=400, detail="Notification lacks a consent id")

    user_id = await resolve_consent_owner(identifier)
    if user_id is None:
        # Do not disclose whether the consent exists.
        logger.info(f"Consent notification for unknown consent {identifier}")
        return {"received": True}

    async with tenant_session(user_id) as session:
        row = (
            await session.execute(select(Consent).where(Consent.aa_consent_id == identifier))
        ).scalar_one_or_none()
        if row is not None:
            previous = row.status
            row.status = str(payload.status)
            await session.flush()

            # Enqueued rather than acted on inline: the AA needs a fast 200 or
            # it retries, and the retry would arrive mid-processing.
            await outbox.enqueue(
                session,
                aggregate_type="consent",
                aggregate_id=identifier,
                event_type="consent.status_changed",
                payload={
                    "user_id": str(user_id),
                    "consent_id": identifier,
                    "from": previous,
                    "to": str(payload.status),
                },
                dedupe_key=f"consent.status:{identifier}:{payload.status}:"
                f"{payload.notification_id or ''}",
            )

    return {"received": True}


@router.post("/webhooks/fi", summary="AA FI-ready notification")
async def fi_webhook(request: Request) -> Dict[str, Any]:
    payload: FINotification = await _authenticated_payload(request, FINotification)

    user_id = await resolve_session_owner(payload.session_id)
    if user_id is None:
        logger.info(f"FI notification for unknown session {payload.session_id}")
        return {"received": True}

    async with tenant_session(user_id) as session:
        row = (
            await session.execute(
                select(FiSession).where(FiSession.aa_session_id == payload.session_id)
            )
        ).scalar_one_or_none()
        if row is not None:
            row.status = str(payload.session_status)

        # The dedupe key includes the notification id, so the AA's at-least-once
        # retry enqueues nothing the second time.
        await outbox.enqueue(
            session,
            aggregate_type="fi_session",
            aggregate_id=payload.session_id,
            event_type="fi.ready",
            payload={
                "user_id": str(user_id),
                "session_id": payload.session_id,
                "status": str(payload.session_status),
            },
            dedupe_key=f"fi.ready:{payload.session_id}:"
            f"{payload.notification_id or payload.session_status}",
        )

    return {"received": True}


# ---------------------------------------------------------------------------
# Sandbox helper
# ---------------------------------------------------------------------------


@router.post(
    "/sandbox/approve",
    summary="Simulate the user approving consent at the AA (sandbox only)",
    description=(
        "Approval genuinely happens at the Account Aggregator with the user "
        "present. This endpoint exists so the sandbox round-trip is testable "
        "end to end and is refused outside a mock transport."
    ),
)
async def sandbox_approve(
    payload: SandboxApproveRequest,
    principal: Principal = Depends(require_mfa),
    session: AsyncSession = Depends(get_tenant_session),
) -> Dict[str, Any]:
    client = get_fiu_client()
    if not isinstance(client.transport, MockAATransport):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sandbox approval is only available with the mock AA transport",
        )

    row = (
        await session.execute(select(Consent).where(Consent.aa_consent_id == payload.consent_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Consent not found")

    if payload.approve:
        client.transport.approve_consent(payload.consent_id)
    else:
        client.transport.reject_consent(payload.consent_id)

    await sync_consent_status(session, principal.user_id, row, principal.email)
    return {"consent_id": payload.consent_id, "status": row.status}


@router.get("/info", summary="AA integration status and stated limitations")
async def aa_info() -> Dict[str, Any]:
    return {
        "framework": "RBI Account Aggregator (NBFC-AA) under DEPA",
        "role": "FIU (Financial Information User)",
        "provider": settings.aa_provider,
        "mock_transport": settings.aa_use_mock_transport,
        "sandbox_only": settings.aa_sandbox_only,
        "limitation": SANDBOX_NOTICE,
        "flow": [
            "consent request",
            "user approves at the AA",
            "consent notification",
            "FI request (FIU supplies ECDH public key)",
            "FI notification",
            "FI fetch (payload encrypted to the FIU's key)",
            "decrypt, categorise locally, ingest",
        ],
        "encryption": "X25519 ECDH -> HKDF-SHA256 -> AES-256-GCM",
        "credentials_handled": "none -- FinGuru never sees a bank credential",
    }
