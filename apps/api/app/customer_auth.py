"""Tenant-scoped customer OTP identity without plaintext phone persistence."""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.capabilities import capability_digest, issue_capability
from app.checkout import require_checkout_session
from app.database import Database, PublicStorefrontNotFound, PublicTenantTransaction
from app.sms import SmsProvider

customer_bearer = HTTPBearer(auto_error=False)
CUSTOMER_TOKEN_PATTERN = re.compile(r"^pc_[A-Za-z0-9_-]{32,128}$")


class OtpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_e164: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")


class OtpChallenge(BaseModel):
    model_config = ConfigDict(frozen=True)

    challenge_id: UUID
    expires_at: datetime
    phone_masked: str
    delivery_mode: Literal["development_memory"] = "development_memory"
    identity_verified: Literal[False] = False


class OtpVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: UUID
    code: str = Field(pattern=r"^[0-9]{6}$")


class CustomerSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    customer_id: UUID
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - protocol type
    expires_at: datetime
    phone_masked: str
    identity_verified: Literal[True] = True
    replayed: bool


class CustomerIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    customer_id: UUID
    phone_masked: str
    identity_verified: Literal[True] = True


@dataclass(frozen=True, slots=True)
class VerificationFailure:
    status_code: int
    detail: str


def phone_lookup_digest(tenant_id: UUID, phone_e164: str, pepper: str) -> bytes:
    return hmac.new(pepper.encode(), tenant_id.bytes + phone_e164.encode(), hashlib.sha256).digest()


def otp_digest(challenge_id: UUID, code: str, pepper: str) -> bytes:
    return hmac.new(pepper.encode(), challenge_id.bytes + code.encode(), hashlib.sha256).digest()


def mask_phone(last4: str) -> str:
    return f"+••• ••• •• {last4}"


async def _expire_challenges(transaction: PublicTenantTransaction) -> None:
    await transaction.session.execute(
        text(
            "UPDATE customer_auth_challenges SET state = 'expired', updated_at = now() "
            "WHERE tenant_id = :tenant_id AND state = 'pending' AND expires_at <= now()"
        ),
        {"tenant_id": transaction.tenant_id},
    )


async def request_customer_otp(
    database: Database,
    provider: SmsProvider,
    slug: str,
    command: OtpRequest,
    checkout_token: str,
    *,
    allow_demo: bool,
    pepper: str,
    ttl_seconds: int,
    max_attempts: int,
) -> OtpChallenge:
    if not provider.enabled:
        raise HTTPException(status_code=503, detail="SMS verification is not configured")
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge_id = uuid4()
    try:
        async with database.public_tenant_transaction(slug, allow_demo=allow_demo) as transaction:
            await _expire_challenges(transaction)
            checkout_session_id = await require_checkout_session(transaction, checkout_token)
            await transaction.session.execute(
                text(
                    "UPDATE customer_auth_challenges SET state = 'superseded', "
                    "updated_at = now() WHERE tenant_id = :tenant_id "
                    "AND checkout_session_id = :checkout_session_id AND state = 'pending'"
                ),
                {
                    "tenant_id": transaction.tenant_id,
                    "checkout_session_id": checkout_session_id,
                },
            )
            created = (
                await transaction.session.execute(
                    text(
                        "INSERT INTO customer_auth_challenges "
                        "(id, tenant_id, checkout_session_id, phone_lookup_hash, phone_last4, "
                        "code_hash, attempts_remaining, expires_at) VALUES "
                        "(:id, :tenant_id, :checkout_session_id, :phone_hash, :phone_last4, "
                        ":code_hash, :max_attempts, "
                        "now() + make_interval(secs => :ttl_seconds)) RETURNING expires_at"
                    ),
                    {
                        "id": challenge_id,
                        "tenant_id": transaction.tenant_id,
                        "checkout_session_id": checkout_session_id,
                        "phone_hash": phone_lookup_digest(
                            transaction.tenant_id, command.phone_e164, pepper
                        ),
                        "phone_last4": command.phone_e164[-4:],
                        "code_hash": otp_digest(challenge_id, code, pepper),
                        "max_attempts": max_attempts,
                        "ttl_seconds": ttl_seconds,
                    },
                )
            ).one()
    except PublicStorefrontNotFound:
        raise HTTPException(status_code=404, detail="Storefront not found") from None
    try:
        await provider.send_otp(challenge_id, command.phone_e164, code)
    except Exception:
        await _invalidate_undelivered_challenge(database, slug, challenge_id, allow_demo=allow_demo)
        raise HTTPException(status_code=503, detail="SMS delivery is unavailable") from None
    return OtpChallenge(
        challenge_id=challenge_id,
        expires_at=created.expires_at,
        phone_masked=mask_phone(command.phone_e164[-4:]),
    )


async def _invalidate_undelivered_challenge(
    database: Database, slug: str, challenge_id: UUID, *, allow_demo: bool
) -> None:
    try:
        async with database.public_tenant_transaction(slug, allow_demo=allow_demo) as transaction:
            await transaction.session.execute(
                text(
                    "UPDATE customer_auth_challenges SET state = 'expired', updated_at = now() "
                    "WHERE tenant_id = :tenant_id AND id = :challenge_id AND state = 'pending'"
                ),
                {"tenant_id": transaction.tenant_id, "challenge_id": challenge_id},
            )
    except PublicStorefrontNotFound:
        return


async def _verify_in_transaction(
    transaction: PublicTenantTransaction,
    command: OtpVerification,
    checkout_token: str,
    *,
    pepper: str,
    customer_session_ttl_seconds: int,
) -> CustomerSession | VerificationFailure:
    await _expire_challenges(transaction)
    checkout_session_id = await require_checkout_session(transaction, checkout_token)
    challenge = (
        await transaction.session.execute(
            text(
                "SELECT id, customer_session_id, phone_lookup_hash, phone_last4, code_hash, "
                "state, attempts_remaining, expires_at, expires_at > now() AS active "
                "FROM customer_auth_challenges "
                "WHERE tenant_id = :tenant_id AND id = :challenge_id "
                "AND checkout_session_id = :checkout_session_id FOR UPDATE"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "challenge_id": command.challenge_id,
                "checkout_session_id": checkout_session_id,
            },
        )
    ).one_or_none()
    if challenge is None:
        return VerificationFailure(404, "Verification challenge not found")
    if not challenge.active:
        return VerificationFailure(409, "Verification challenge is no longer active")
    if challenge.state in {"expired", "superseded"}:
        return VerificationFailure(409, "Verification challenge is no longer active")
    if challenge.state == "locked":
        return VerificationFailure(429, "Verification challenge is locked")
    matches = secrets.compare_digest(
        challenge.code_hash, otp_digest(command.challenge_id, command.code, pepper)
    )
    if not matches:
        remaining = max(int(challenge.attempts_remaining) - 1, 0)
        await transaction.session.execute(
            text(
                "UPDATE customer_auth_challenges SET attempts_remaining = :remaining, "
                "state = CASE WHEN :remaining = 0 THEN 'locked' ELSE state END, "
                "updated_at = now() WHERE tenant_id = :tenant_id AND id = :challenge_id"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "challenge_id": command.challenge_id,
                "remaining": remaining,
            },
        )
        return VerificationFailure(422, "Invalid verification code")

    token = f"pc_{issue_capability()[0]}"
    token_hash = capability_digest(token)
    if challenge.state == "verified":
        session = (
            await transaction.session.execute(
                text(
                    "UPDATE customer_sessions SET token_hash = :token_hash, state = 'active', "
                    "expires_at = now() + make_interval(secs => :ttl_seconds), "
                    "updated_at = now() WHERE tenant_id = :tenant_id "
                    "AND id = :session_id RETURNING customer_id, expires_at"
                ),
                {
                    "tenant_id": transaction.tenant_id,
                    "session_id": challenge.customer_session_id,
                    "token_hash": token_hash,
                    "ttl_seconds": customer_session_ttl_seconds,
                },
            )
        ).one_or_none()
        if session is None:
            return VerificationFailure(409, "Customer session is no longer available")
        return CustomerSession(
            customer_id=session.customer_id,
            access_token=token,
            expires_at=session.expires_at,
            phone_masked=mask_phone(challenge.phone_last4),
            replayed=True,
        )

    customer_id = await transaction.session.scalar(
        text(
            "INSERT INTO customers "
            "(id, tenant_id, phone_lookup_hash, phone_last4, phone_verified_at) "
            "VALUES (:id, :tenant_id, :phone_hash, :phone_last4, now()) "
            "ON CONFLICT (tenant_id, phone_lookup_hash) DO UPDATE "
            "SET phone_last4 = EXCLUDED.phone_last4, phone_verified_at = now(), "
            "updated_at = now() RETURNING id"
        ),
        {
            "id": uuid4(),
            "tenant_id": transaction.tenant_id,
            "phone_hash": challenge.phone_lookup_hash,
            "phone_last4": challenge.phone_last4,
        },
    )
    customer_session_id = uuid4()
    customer_session = (
        await transaction.session.execute(
            text(
                "INSERT INTO customer_sessions "
                "(id, tenant_id, customer_id, token_hash, expires_at) VALUES "
                "(:id, :tenant_id, :customer_id, :token_hash, "
                "now() + make_interval(secs => :ttl_seconds)) RETURNING expires_at"
            ),
            {
                "id": customer_session_id,
                "tenant_id": transaction.tenant_id,
                "customer_id": customer_id,
                "token_hash": token_hash,
                "ttl_seconds": customer_session_ttl_seconds,
            },
        )
    ).one()
    await transaction.session.execute(
        text(
            "UPDATE customer_auth_challenges SET state = 'verified', attempts_remaining = 0, "
            "customer_session_id = :customer_session_id, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :challenge_id"
        ),
        {
            "tenant_id": transaction.tenant_id,
            "challenge_id": command.challenge_id,
            "customer_session_id": customer_session_id,
        },
    )
    await transaction.session.execute(
        text(
            "UPDATE checkout_sessions SET customer_id = :customer_id, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :checkout_session_id"
        ),
        {
            "tenant_id": transaction.tenant_id,
            "checkout_session_id": checkout_session_id,
            "customer_id": customer_id,
        },
    )
    await transaction.session.execute(
        text(
            "UPDATE orders SET customer_id = :customer_id, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND checkout_session_id = :checkout_session_id"
        ),
        {
            "tenant_id": transaction.tenant_id,
            "checkout_session_id": checkout_session_id,
            "customer_id": customer_id,
        },
    )
    return CustomerSession(
        customer_id=UUID(str(customer_id)),
        access_token=token,
        expires_at=customer_session.expires_at,
        phone_masked=mask_phone(challenge.phone_last4),
        replayed=False,
    )


async def verify_customer_otp(
    database: Database,
    slug: str,
    command: OtpVerification,
    checkout_token: str,
    *,
    allow_demo: bool,
    pepper: str,
    customer_session_ttl_seconds: int,
) -> CustomerSession:
    try:
        async with database.public_tenant_transaction(slug, allow_demo=allow_demo) as transaction:
            result = await _verify_in_transaction(
                transaction,
                command,
                checkout_token,
                pepper=pepper,
                customer_session_ttl_seconds=customer_session_ttl_seconds,
            )
    except PublicStorefrontNotFound:
        raise HTTPException(status_code=404, detail="Storefront not found") from None
    if isinstance(result, VerificationFailure):
        raise HTTPException(status_code=result.status_code, detail=result.detail)
    return result


def customer_token_digest(credentials: HTTPAuthorizationCredentials | None) -> bytes:
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not CUSTOMER_TOKEN_PATTERN.fullmatch(credentials.credentials)
    ):
        raise HTTPException(
            status_code=401,
            detail="Valid customer session required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return capability_digest(credentials.credentials)


async def customer_session_digest(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(customer_bearer)],
) -> bytes:
    return customer_token_digest(credentials)


async def load_customer_identity(
    database: Database,
    slug: str,
    token_hash: bytes,
    *,
    allow_demo: bool,
) -> CustomerIdentity:
    try:
        async with database.public_tenant_transaction(slug, allow_demo=allow_demo) as transaction:
            identity = await resolve_customer_identity(transaction, token_hash)
    except PublicStorefrontNotFound:
        raise HTTPException(status_code=404, detail="Storefront not found") from None
    if identity is None:
        raise invalid_customer_session()
    return identity


async def resolve_customer_identity(
    transaction: PublicTenantTransaction, token_hash: bytes
) -> CustomerIdentity | None:
    """Resolve a customer inside a tenant transaction and expire stale sessions."""
    await transaction.session.execute(
        text(
            "UPDATE customer_sessions SET state = 'expired', updated_at = now() "
            "WHERE tenant_id = :tenant_id AND token_hash = :token_hash "
            "AND state = 'active' AND expires_at <= now()"
        ),
        {"tenant_id": transaction.tenant_id, "token_hash": token_hash},
    )
    identity = (
        await transaction.session.execute(
            text(
                "SELECT c.id AS customer_id, c.phone_last4 FROM customer_sessions cs "
                "JOIN customers c ON c.tenant_id = cs.tenant_id AND c.id = cs.customer_id "
                "WHERE cs.tenant_id = :tenant_id AND cs.token_hash = :token_hash "
                "AND cs.state = 'active' AND cs.expires_at > now()"
            ),
            {"tenant_id": transaction.tenant_id, "token_hash": token_hash},
        )
    ).one_or_none()
    if identity is None:
        return None
    return CustomerIdentity(
        customer_id=identity.customer_id,
        phone_masked=mask_phone(identity.phone_last4),
    )


def invalid_customer_session() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Valid customer session required",
        headers={"WWW-Authenticate": "Bearer"},
    )
