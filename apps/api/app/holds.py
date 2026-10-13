"""Short-lived, atomic inventory holds for an enabled public storefront."""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.availability import Quote, QuoteRequest, load_quote_context, quote_from_context
from app.capabilities import capability_digest, issue_capability
from app.checkout import require_checkout_session
from app.database import (
    Database,
    PublicStorefrontNotFound,
    PublicTenantTransaction,
    TenantTransaction,
)


class RentalHold(BaseModel):
    model_config = ConfigDict(frozen=True)

    hold_id: UUID
    state: Literal["held"] = "held"
    expires_at: datetime
    management_token: str
    replayed: bool
    quote: Quote


class ReleasedHold(BaseModel):
    model_config = ConfigDict(frozen=True)

    hold_id: UUID
    released: Literal[True] = True


async def expire_holds(transaction: PublicTenantTransaction | TenantTransaction) -> None:
    await transaction.session.execute(
        text(
            "WITH expired AS ("
            "  UPDATE rental_allocations SET state = 'expired', updated_at = now() "
            "  WHERE tenant_id = :tenant_id AND state = 'held' AND expires_at <= now() "
            "  RETURNING order_id"
            ") UPDATE orders SET state = 'expired', updated_at = now() "
            "WHERE tenant_id = :tenant_id AND state = 'held' "
            "AND id IN (SELECT order_id FROM expired WHERE order_id IS NOT NULL)"
        ),
        {"tenant_id": transaction.tenant_id},
    )


def _same_request(row: object, command: QuoteRequest) -> bool:
    mapping = row._mapping  # type: ignore[attr-defined]
    return (
        UUID(str(mapping["equipment_model_id"])) == command.model_id
        and UUID(str(mapping["branch_id"])) == command.branch_id
        and mapping["start_date"] == command.start_date
        and mapping["end_date"] == command.end_date
        and mapping["fulfillment"] == command.fulfillment
    )


async def _create_hold_in_transaction(
    transaction: PublicTenantTransaction,
    command: QuoteRequest,
    key_hash: bytes,
    checkout_token: str,
    *,
    ttl_seconds: int,
) -> RentalHold:
    lock_key = int.from_bytes(
        hashlib.sha256(transaction.tenant_id.bytes + key_hash).digest()[:8],
        "big",
        signed=True,
    )
    await transaction.session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
    )
    await expire_holds(transaction)
    context, days = await load_quote_context(transaction, command)
    if not bool(context["booking_enabled"]):
        raise HTTPException(status_code=409, detail="Booking is not enabled")
    checkout_session_id = await require_checkout_session(transaction, checkout_token)
    existing = (
        await transaction.session.execute(
            text(
                "SELECT id, equipment_model_id, branch_id, start_date, end_date, "
                "fulfillment, state, expires_at, quote_snapshot, checkout_session_id "
                "FROM rental_allocations WHERE tenant_id = :tenant_id "
                "AND idempotency_key_hash = :key_hash FOR UPDATE"
            ),
            {"tenant_id": transaction.tenant_id, "key_hash": key_hash},
        )
    ).one_or_none()
    if existing is not None:
        if (
            not _same_request(existing, command)
            or UUID(str(existing.checkout_session_id)) != checkout_session_id
        ):
            raise HTTPException(
                status_code=409,
                detail="Idempotency key was already used for another request",
            )
        if existing.state != "held":
            raise HTTPException(status_code=409, detail="Hold is no longer active")
        token, token_hash = issue_capability()
        await transaction.session.execute(
            text(
                "UPDATE rental_allocations SET management_token_hash = :token_hash, "
                "updated_at = now() WHERE tenant_id = :tenant_id AND id = :hold_id"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "hold_id": existing.id,
                "token_hash": token_hash,
            },
        )
        return RentalHold(
            hold_id=existing.id,
            expires_at=existing.expires_at,
            management_token=token,
            replayed=True,
            quote=Quote.model_validate(existing.quote_snapshot),
        )

    quote = quote_from_context(command, context, days)
    asset_id = await transaction.session.scalar(
        text(
            "SELECT a.id FROM assets a "
            "WHERE a.tenant_id = :tenant_id AND a.equipment_model_id = :model_id "
            "AND a.branch_id = :branch_id AND a.state = 'available' "
            "AND NOT EXISTS (SELECT 1 FROM asset_blocks ab "
            "  WHERE ab.tenant_id = a.tenant_id AND ab.asset_id = a.id "
            "  AND ab.start_date <= :end_date AND ab.end_date >= :start_date) "
            "AND NOT EXISTS (SELECT 1 FROM rental_allocations ra "
            "  WHERE ra.tenant_id = a.tenant_id AND ra.asset_id = a.id "
            "  AND ra.start_date <= :end_date AND ra.end_date >= :start_date "
            "  AND ra.state IN ('held', 'confirmed')) "
            "ORDER BY a.id FOR UPDATE OF a SKIP LOCKED LIMIT 1"
        ),
        {
            "tenant_id": transaction.tenant_id,
            "model_id": command.model_id,
            "branch_id": command.branch_id,
            "start_date": command.start_date,
            "end_date": command.end_date,
        },
    )
    if asset_id is None:
        raise HTTPException(status_code=409, detail="No units available for selected dates")
    hold_id = uuid4()
    token, token_hash = issue_capability()
    inserted = (
        await transaction.session.execute(
            text(
                "INSERT INTO rental_allocations "
                "(id, tenant_id, asset_id, equipment_model_id, branch_id, start_date, "
                "end_date, fulfillment, state, expires_at, idempotency_key_hash, "
                "management_token_hash, quote_snapshot, checkout_session_id) VALUES "
                "(:id, :tenant_id, :asset_id, :model_id, :branch_id, :start_date, "
                ":end_date, :fulfillment, 'held', "
                "now() + make_interval(secs => :ttl_seconds), :key_hash, :token_hash, "
                "CAST(:quote_snapshot AS jsonb), :checkout_session_id) RETURNING expires_at"
            ),
            {
                "id": hold_id,
                "tenant_id": transaction.tenant_id,
                "asset_id": asset_id,
                "model_id": command.model_id,
                "branch_id": command.branch_id,
                "start_date": command.start_date,
                "end_date": command.end_date,
                "fulfillment": command.fulfillment,
                "ttl_seconds": ttl_seconds,
                "key_hash": key_hash,
                "token_hash": token_hash,
                "quote_snapshot": json.dumps(quote.model_dump(mode="json")),
                "checkout_session_id": checkout_session_id,
            },
        )
    ).one()
    return RentalHold(
        hold_id=hold_id,
        expires_at=inserted.expires_at,
        management_token=token,
        replayed=False,
        quote=quote,
    )


async def create_hold(
    database: Database,
    slug: str,
    command: QuoteRequest,
    idempotency_key: str,
    checkout_token: str,
    *,
    allow_demo: bool,
    ttl_seconds: int,
) -> RentalHold:
    try:
        async with database.public_tenant_transaction(slug, allow_demo=allow_demo) as transaction:
            return await _create_hold_in_transaction(
                transaction,
                command,
                capability_digest(idempotency_key),
                checkout_token,
                ttl_seconds=ttl_seconds,
            )
    except PublicStorefrontNotFound:
        raise HTTPException(status_code=404, detail="Storefront not found") from None
    except IntegrityError:
        raise HTTPException(
            status_code=409, detail="No units available for selected dates"
        ) from None


async def _release_hold_in_transaction(
    transaction: PublicTenantTransaction,
    hold_id: UUID,
    management_token: str,
    checkout_token: str,
) -> ReleasedHold:
    await expire_holds(transaction)
    checkout_session_id = await require_checkout_session(transaction, checkout_token)
    released = (
        await transaction.session.execute(
            text(
                "UPDATE rental_allocations SET state = 'released', updated_at = now() "
                "WHERE tenant_id = :tenant_id AND id = :hold_id AND state = 'held' "
                "AND checkout_session_id = :checkout_session_id "
                "AND management_token_hash = :token_hash RETURNING id, order_id"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "hold_id": hold_id,
                "token_hash": capability_digest(management_token),
                "checkout_session_id": checkout_session_id,
            },
        )
    ).one_or_none()
    if released is not None and released.order_id is not None:
        await transaction.session.execute(
            text(
                "UPDATE orders SET state = 'cancelled', updated_at = now() "
                "WHERE tenant_id = :tenant_id AND id = :order_id AND state = 'held'"
            ),
            {"tenant_id": transaction.tenant_id, "order_id": released.order_id},
        )
    if released is None:
        already_released = await transaction.session.scalar(
            text(
                "SELECT id FROM rental_allocations WHERE tenant_id = :tenant_id "
                "AND id = :hold_id AND state = 'released' "
                "AND checkout_session_id = :checkout_session_id "
                "AND management_token_hash = :token_hash"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "hold_id": hold_id,
                "token_hash": capability_digest(management_token),
                "checkout_session_id": checkout_session_id,
            },
        )
        if already_released is None:
            raise HTTPException(status_code=404, detail="Active hold not found")
    return ReleasedHold(hold_id=hold_id)


async def release_hold(
    database: Database,
    slug: str,
    hold_id: UUID,
    management_token: str,
    checkout_token: str,
    *,
    allow_demo: bool,
) -> ReleasedHold:
    try:
        async with database.public_tenant_transaction(slug, allow_demo=allow_demo) as transaction:
            return await _release_hold_in_transaction(
                transaction, hold_id, management_token, checkout_token
            )
    except PublicStorefrontNotFound:
        raise HTTPException(status_code=404, detail="Storefront not found") from None
