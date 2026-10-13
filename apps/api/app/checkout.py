"""Anonymous, short-lived checkout capabilities without customer identity data."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from app.capabilities import capability_digest, issue_capability
from app.database import Database, PublicStorefrontNotFound, PublicTenantTransaction


class CheckoutSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: UUID
    checkout_token: str
    expires_at: datetime
    identity_verified: Literal[False] = False


async def _expire_checkout_sessions(transaction: PublicTenantTransaction) -> None:
    await transaction.session.execute(
        text(
            "UPDATE checkout_sessions SET state = 'expired', updated_at = now() "
            "WHERE tenant_id = :tenant_id AND state = 'active' AND expires_at <= now()"
        ),
        {"tenant_id": transaction.tenant_id},
    )


async def require_checkout_session(
    transaction: PublicTenantTransaction, checkout_token: str
) -> UUID:
    await _expire_checkout_sessions(transaction)
    session_id = await transaction.session.scalar(
        text(
            "SELECT id FROM checkout_sessions WHERE tenant_id = :tenant_id "
            "AND token_hash = :token_hash AND state = 'active' AND expires_at > now()"
        ),
        {
            "tenant_id": transaction.tenant_id,
            "token_hash": capability_digest(checkout_token),
        },
    )
    if session_id is None:
        raise HTTPException(status_code=401, detail="Valid checkout session required")
    return UUID(str(session_id))


async def create_checkout_session(
    database: Database,
    slug: str,
    *,
    allow_demo: bool,
    ttl_seconds: int,
) -> CheckoutSession:
    try:
        async with database.public_tenant_transaction(slug, allow_demo=allow_demo) as transaction:
            await _expire_checkout_sessions(transaction)
            booking_enabled = await transaction.session.scalar(
                text(
                    "SELECT booking_enabled FROM storefront_profiles WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": transaction.tenant_id},
            )
            if not booking_enabled:
                raise HTTPException(status_code=409, detail="Booking is not enabled")
            session_id = uuid4()
            token, token_hash = issue_capability()
            created = (
                await transaction.session.execute(
                    text(
                        "INSERT INTO checkout_sessions "
                        "(id, tenant_id, token_hash, expires_at) VALUES "
                        "(:id, :tenant_id, :token_hash, "
                        "now() + make_interval(secs => :ttl_seconds)) "
                        "RETURNING expires_at"
                    ),
                    {
                        "id": session_id,
                        "tenant_id": transaction.tenant_id,
                        "token_hash": token_hash,
                        "ttl_seconds": ttl_seconds,
                    },
                )
            ).one()
            return CheckoutSession(
                session_id=session_id,
                checkout_token=token,
                expires_at=created.expires_at,
            )
    except PublicStorefrontNotFound:
        raise HTTPException(status_code=404, detail="Storefront not found") from None
