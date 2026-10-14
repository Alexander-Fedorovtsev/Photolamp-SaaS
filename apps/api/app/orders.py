"""Expiring draft orders created atomically from an active inventory hold."""

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.availability import Fulfillment, Quote
from app.capabilities import capability_digest
from app.checkout import require_checkout_session
from app.customer_auth import invalid_customer_session, resolve_customer_identity
from app.database import Database, PublicStorefrontNotFound, PublicTenantTransaction
from app.holds import expire_holds
from app.order_requests import (
    ExtensionRequestView,
    ReturnRequestView,
    extension_request_from_mapping,
    return_request_from_mapping,
)


class DraftOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hold_id: UUID


class OrderSnapshot(BaseModel):
    """Customer-safe facts frozen when the order is created."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    model_id: UUID
    model_name: str
    branch_id: UUID
    branch_name: str
    start_date: date
    end_date: date
    fulfillment: Fulfillment
    quote: Quote


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
    snapshot: OrderSnapshot
    identity_verified: bool
    payment_status: Literal["not_started"] = "not_started"
    confirmation_available: Literal[False] = False
    replayed: bool


class CustomerOrder(BaseModel):
    """Privacy-safe order history item for an authenticated customer."""

    model_config = ConfigDict(frozen=True)

    order_id: UUID
    state: Literal[
        "held",
        "confirmed",
        "in_progress",
        "returned",
        "completed",
        "expired",
        "cancelled",
    ]
    source: Literal["website", "phone"]
    start_date: date
    end_date: date
    original_end_date: date
    is_overdue: bool
    overdue_days: int = Field(ge=0)
    extension_request: ExtensionRequestView | None
    return_request: ReturnRequestView | None
    fulfillment: Fulfillment
    expires_at: datetime | None
    quote: Quote
    snapshot: OrderSnapshot
    identity_verified: Literal[True] = True
    payment_status: Literal["not_started"] = "not_started"
    confirmation_available: Literal[False] = False
    created_at: datetime
    updated_at: datetime


class CustomerOrderPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[CustomerOrder]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=50)
    offset: int = Field(ge=0)


def _draft_from_row(row: object, *, replayed: bool) -> DraftOrder:
    mapping = row._mapping  # type: ignore[attr-defined]
    return DraftOrder(
        order_id=mapping["id"],
        start_date=mapping["start_date"],
        end_date=mapping["end_date"],
        fulfillment=mapping["fulfillment"],
        expires_at=mapping["expires_at"],
        quote=Quote.model_validate(mapping["quote_snapshot"]),
        snapshot=OrderSnapshot.model_validate(mapping["order_snapshot"]),
        identity_verified=mapping["customer_id"] is not None,
        replayed=replayed,
    )


def _customer_order_from_row(row: object) -> CustomerOrder:
    mapping = row._mapping  # type: ignore[attr-defined]
    return CustomerOrder(
        order_id=mapping["id"],
        state=mapping["state"],
        source=mapping["source"],
        start_date=mapping["start_date"],
        end_date=mapping["end_date"],
        original_end_date=OrderSnapshot.model_validate(mapping["order_snapshot"]).end_date,
        is_overdue=mapping["overdue_days"] > 0,
        overdue_days=mapping["overdue_days"],
        extension_request=(
            extension_request_from_mapping(mapping["extension_request"])
            if mapping["extension_request"] is not None
            else None
        ),
        return_request=(
            return_request_from_mapping(mapping["return_request"])
            if mapping["return_request"] is not None
            else None
        ),
        fulfillment=mapping["fulfillment"],
        expires_at=mapping["expires_at"],
        quote=Quote.model_validate(mapping["quote_snapshot"]),
        snapshot=OrderSnapshot.model_validate(mapping["order_snapshot"]),
        created_at=mapping["created_at"],
        updated_at=mapping["updated_at"],
    )


CUSTOMER_ORDER_PROJECTION = """
SELECT o.*,
  CASE WHEN o.state = 'in_progress'
    THEN GREATEST(((now() AT TIME ZONE b.timezone)::date - o.end_date), 0)
    ELSE 0 END::integer AS overdue_days,
  (SELECT jsonb_build_object(
      'id', er.id, 'state', er.state, 'original_end_date', er.original_end_date,
      'requested_end_date', er.requested_end_date, 'additional_days', er.additional_days,
      'amount_minor', er.amount_minor, 'currency', er.currency,
      'requested_at', er.requested_at, 'decided_at', er.decided_at,
      'rejection_reason', er.rejection_reason)
    FROM order_extension_requests er
    WHERE er.tenant_id = o.tenant_id AND er.order_id = o.id
    ORDER BY er.requested_at DESC, er.id DESC LIMIT 1) AS extension_request,
  (SELECT jsonb_build_object(
      'id', rr.id, 'state', rr.state, 'requested_date', rr.requested_date,
      'requested_at', rr.requested_at, 'decided_at', rr.decided_at,
      'rejection_reason', rr.rejection_reason)
    FROM order_return_requests rr
    WHERE rr.tenant_id = o.tenant_id AND rr.order_id = o.id
    ORDER BY rr.requested_at DESC, rr.id DESC LIMIT 1) AS return_request
FROM orders o
JOIN branches b ON b.tenant_id = o.tenant_id AND b.id = o.branch_id
"""


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
    customer_id = await transaction.session.scalar(
        text(
            "SELECT customer_id FROM checkout_sessions WHERE tenant_id = :tenant_id "
            "AND id = :checkout_session_id"
        ),
        {
            "tenant_id": transaction.tenant_id,
            "checkout_session_id": checkout_session_id,
        },
    )
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
                "SELECT ra.id, ra.branch_id, ra.start_date, ra.end_date, ra.fulfillment, "
                "ra.expires_at, "
                "ra.quote_snapshot, ra.order_id, ra.equipment_model_id, "
                "em.name AS model_name, b.name AS branch_name "
                "FROM rental_allocations ra "
                "JOIN equipment_models em ON em.tenant_id = ra.tenant_id "
                "AND em.id = ra.equipment_model_id "
                "JOIN branches b ON b.tenant_id = ra.tenant_id AND b.id = ra.branch_id "
                "WHERE ra.tenant_id = :tenant_id AND ra.id = :hold_id "
                "AND ra.checkout_session_id = :checkout_session_id "
                "AND ra.management_token_hash = :hold_token_hash "
                "AND ra.state = 'held' AND ra.expires_at > now() FOR UPDATE OF ra"
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
    order_snapshot = OrderSnapshot(
        model_id=allocation.equipment_model_id,
        model_name=allocation.model_name,
        branch_id=allocation.branch_id,
        branch_name=allocation.branch_name,
        start_date=allocation.start_date,
        end_date=allocation.end_date,
        fulfillment=allocation.fulfillment,
        quote=Quote.model_validate(allocation.quote_snapshot),
    )
    created = (
        await transaction.session.execute(
            text(
                "INSERT INTO orders "
                "(id, tenant_id, branch_id, checkout_session_id, allocation_id, source, "
                "state, start_date, end_date, fulfillment, expires_at, quote_snapshot, "
                "idempotency_key_hash, customer_id, order_snapshot) VALUES "
                "(:id, :tenant_id, :branch_id, :checkout_session_id, :allocation_id, "
                "'website', 'held', :start_date, :end_date, :fulfillment, :expires_at, "
                "CAST(:quote_snapshot AS jsonb), :idempotency_hash, :customer_id, "
                "CAST(:order_snapshot AS jsonb)) RETURNING *"
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
                "customer_id": customer_id,
                "order_snapshot": json.dumps(order_snapshot.model_dump(mode="json")),
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


async def list_customer_orders(
    database: Database,
    slug: str,
    token_hash: bytes,
    *,
    allow_demo: bool,
    limit: int,
    offset: int,
) -> CustomerOrderPage:
    try:
        async with database.public_tenant_transaction(slug, allow_demo=allow_demo) as transaction:
            await expire_holds(transaction)
            identity = await resolve_customer_identity(transaction, token_hash)
            rows: Sequence[object]
            if identity is None:
                total = 0
                rows = []
            else:
                parameters = {
                    "tenant_id": transaction.tenant_id,
                    "customer_id": identity.customer_id,
                    "limit": limit,
                    "offset": offset,
                }
                total = await transaction.session.scalar(
                    text(
                        "SELECT count(*) FROM orders WHERE tenant_id = :tenant_id "
                        "AND customer_id = :customer_id"
                    ),
                    parameters,
                )
                rows = (
                    await transaction.session.execute(
                        text(
                            CUSTOMER_ORDER_PROJECTION + " WHERE o.tenant_id = :tenant_id "
                            "AND o.customer_id = :customer_id "
                            "ORDER BY o.created_at DESC, o.id DESC LIMIT :limit OFFSET :offset"
                        ),
                        parameters,
                    )
                ).all()
    except PublicStorefrontNotFound:
        raise HTTPException(status_code=404, detail="Storefront not found") from None
    if identity is None:
        raise invalid_customer_session()
    return CustomerOrderPage(
        items=[_customer_order_from_row(row) for row in rows],
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


async def load_customer_order(
    database: Database,
    slug: str,
    order_id: UUID,
    token_hash: bytes,
    *,
    allow_demo: bool,
) -> CustomerOrder:
    try:
        async with database.public_tenant_transaction(slug, allow_demo=allow_demo) as transaction:
            await expire_holds(transaction)
            identity = await resolve_customer_identity(transaction, token_hash)
            if identity is None:
                order = None
            else:
                order = (
                    await transaction.session.execute(
                        text(
                            CUSTOMER_ORDER_PROJECTION + " WHERE o.tenant_id = :tenant_id "
                            "AND o.id = :order_id AND o.customer_id = :customer_id"
                        ),
                        {
                            "tenant_id": transaction.tenant_id,
                            "order_id": order_id,
                            "customer_id": identity.customer_id,
                        },
                    )
                ).one_or_none()
    except PublicStorefrontNotFound:
        raise HTTPException(status_code=404, detail="Storefront not found") from None
    if identity is None:
        raise invalid_customer_session()
    if order is None:
        raise HTTPException(status_code=404, detail="Customer order not found")
    return _customer_order_from_row(order)
