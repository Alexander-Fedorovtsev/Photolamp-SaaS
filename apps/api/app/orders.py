"""Expiring draft orders created atomically from an active inventory hold."""

import hashlib
import json
from datetime import date, datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.availability import Fulfillment, Quote
from app.capabilities import capability_digest
from app.checkout import require_checkout_session
from app.database import Database, PublicStorefrontNotFound, PublicTenantTransaction
from app.holds import expire_holds


class DraftOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hold_id: UUID


class DraftOrder(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: UUID
    state: Literal["held"] = "held"
    source: Literal["website"] = "website"
    start_date: date
    end_date: date
    fulfillment: Fulfillment
    expires_at: datetime
    quote: Quote
    identity_verified: Literal[False] = False
    payment_status: Literal["not_started"] = "not_started"
    confirmation_available: Literal[False] = False
    replayed: bool


def _draft_from_row(row: object, *, replayed: bool) -> DraftOrder:
    mapping = row._mapping  # type: ignore[attr-defined]
    return DraftOrder(
        order_id=mapping["id"],
        start_date=mapping["start_date"],
        end_date=mapping["end_date"],
        fulfillment=mapping["fulfillment"],
        expires_at=mapping["expires_at"],
        quote=Quote.model_validate(mapping["quote_snapshot"]),
        replayed=replayed,
    )


async def _create_draft_order_in_transaction(
    transaction: PublicTenantTransaction,
    command: DraftOrderRequest,
    idempotency_hash: bytes,
    checkout_token: str,
    hold_token: str,
) -> DraftOrder:
    lock_key = int.from_bytes(
        hashlib.sha256(transaction.tenant_id.bytes + idempotency_hash).digest()[:8],
        "big",
        signed=True,
    )
    await transaction.session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
    )
    await expire_holds(transaction)
    checkout_session_id = await require_checkout_session(transaction, checkout_token)
    hold_token_hash = capability_digest(hold_token)
    existing = (
        await transaction.session.execute(
            text(
                "SELECT o.*, ra.management_token_hash = :hold_token_hash AS token_matches "
                "FROM orders o JOIN rental_allocations ra "
                "ON ra.tenant_id = o.tenant_id AND ra.id = o.allocation_id "
                "WHERE o.tenant_id = :tenant_id "
                "AND o.idempotency_key_hash = :idempotency_hash FOR UPDATE OF o"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "idempotency_hash": idempotency_hash,
                "hold_token_hash": hold_token_hash,
            },
        )
    ).one_or_none()
    if existing is not None:
        if (
            UUID(str(existing.allocation_id)) != command.hold_id
            or UUID(str(existing.checkout_session_id)) != checkout_session_id
        ):
            raise HTTPException(
                status_code=409,
                detail="Idempotency key was already used for another request",
            )
        if not existing.token_matches:
            raise HTTPException(status_code=404, detail="Active hold not found")
        if existing.state != "held":
            raise HTTPException(status_code=409, detail="Draft order is no longer active")
        return _draft_from_row(existing, replayed=True)

    allocation = (
        await transaction.session.execute(
            text(
                "SELECT id, branch_id, start_date, end_date, fulfillment, expires_at, "
                "quote_snapshot, order_id FROM rental_allocations "
                "WHERE tenant_id = :tenant_id AND id = :hold_id "
                "AND checkout_session_id = :checkout_session_id "
                "AND management_token_hash = :hold_token_hash "
                "AND state = 'held' AND expires_at > now() FOR UPDATE"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "hold_id": command.hold_id,
                "checkout_session_id": checkout_session_id,
                "hold_token_hash": hold_token_hash,
            },
        )
    ).one_or_none()
    if allocation is None:
        raise HTTPException(status_code=404, detail="Active hold not found")
    if allocation.order_id is not None:
        raise HTTPException(status_code=409, detail="Hold already belongs to a draft order")

    order_id = uuid4()
    created = (
        await transaction.session.execute(
            text(
                "INSERT INTO orders "
                "(id, tenant_id, branch_id, checkout_session_id, allocation_id, source, "
                "state, start_date, end_date, fulfillment, expires_at, quote_snapshot, "
                "idempotency_key_hash) VALUES "
                "(:id, :tenant_id, :branch_id, :checkout_session_id, :allocation_id, "
                "'website', 'held', :start_date, :end_date, :fulfillment, :expires_at, "
                "CAST(:quote_snapshot AS jsonb), :idempotency_hash) RETURNING *"
            ),
            {
                "id": order_id,
                "tenant_id": transaction.tenant_id,
                "branch_id": allocation.branch_id,
                "checkout_session_id": checkout_session_id,
                "allocation_id": allocation.id,
                "start_date": allocation.start_date,
                "end_date": allocation.end_date,
                "fulfillment": allocation.fulfillment,
                "expires_at": allocation.expires_at,
                "quote_snapshot": json.dumps(allocation.quote_snapshot),
                "idempotency_hash": idempotency_hash,
            },
        )
    ).one()
    await transaction.session.execute(
        text(
            "UPDATE rental_allocations SET order_id = :order_id, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :allocation_id"
        ),
        {
            "tenant_id": transaction.tenant_id,
            "allocation_id": allocation.id,
            "order_id": order_id,
        },
    )
    await transaction.session.execute(
        text(
            "INSERT INTO order_events "
            "(id, tenant_id, order_id, event_type, payload, idempotency_key_hash) "
            "VALUES (:id, :tenant_id, :order_id, 'draft_created', '{}', :idempotency_hash)"
        ),
        {
            "id": uuid4(),
            "tenant_id": transaction.tenant_id,
            "order_id": order_id,
            "idempotency_hash": idempotency_hash,
        },
    )
    return _draft_from_row(created, replayed=False)


async def create_draft_order(
    database: Database,
    slug: str,
    command: DraftOrderRequest,
    idempotency_key: str,
    checkout_token: str,
    hold_token: str,
    *,
    allow_demo: bool,
) -> DraftOrder:
    try:
        async with database.public_tenant_transaction(slug, allow_demo=allow_demo) as transaction:
            return await _create_draft_order_in_transaction(
                transaction,
                command,
                capability_digest(idempotency_key),
                checkout_token,
                hold_token,
            )
    except PublicStorefrontNotFound:
        raise HTTPException(status_code=404, detail="Storefront not found") from None
    except IntegrityError:
        raise HTTPException(
            status_code=409, detail="Draft order conflicts with existing data"
        ) from None


async def load_draft_order(
    database: Database,
    slug: str,
    order_id: UUID,
    checkout_token: str,
    *,
    allow_demo: bool,
) -> DraftOrder:
    try:
        async with database.public_tenant_transaction(slug, allow_demo=allow_demo) as transaction:
            await expire_holds(transaction)
            checkout_session_id = await require_checkout_session(transaction, checkout_token)
            order = (
                await transaction.session.execute(
                    text(
                        "SELECT * FROM orders WHERE tenant_id = :tenant_id AND id = :order_id "
                        "AND checkout_session_id = :checkout_session_id AND state = 'held'"
                    ),
                    {
                        "tenant_id": transaction.tenant_id,
                        "order_id": order_id,
                        "checkout_session_id": checkout_session_id,
                    },
                )
            ).one_or_none()
    except PublicStorefrontNotFound:
        raise HTTPException(status_code=404, detail="Storefront not found") from None
    if order is None:
        raise HTTPException(status_code=404, detail="Active draft order not found")
    return _draft_from_row(order, replayed=False)
