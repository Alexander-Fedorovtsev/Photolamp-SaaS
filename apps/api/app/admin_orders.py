"""Tenant-scoped staff order queue and idempotent lifecycle commands."""

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.capabilities import capability_digest
from app.database import TenantTransaction
from app.holds import expire_holds
from app.orders import OrderSnapshot

OrderState = Literal["held", "confirmed", "expired", "cancelled"]
OrderSource = Literal["website"]


class StaffOrder(BaseModel):
    """Operational order data without customer contact details or asset secrets."""

    model_config = ConfigDict(frozen=True)

    order_id: UUID
    state: OrderState
    source: OrderSource
    snapshot: OrderSnapshot
    identity_verified: bool
    payment_status: Literal["not_started"] = "not_started"
    expires_at: datetime
    confirmed_at: datetime | None
    confirmed_by_staff_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class StaffOrderPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[StaffOrder]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class OrderTransitionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    order: StaffOrder
    replayed: bool


class OrderPermissionDenied(Exception):
    """The staff role cannot manage orders."""


class OrderNotFound(Exception):
    """No order is visible in the authorized tenant scope."""


class OrderTransitionConflict(Exception):
    """The requested transition is invalid or its idempotency key conflicts."""


def require_order_operator(transaction: TenantTransaction) -> None:
    if transaction.role not in {"owner", "admin", "operator"}:
        raise OrderPermissionDenied


def _staff_order_from_row(row: object) -> StaffOrder:
    mapping = row._mapping  # type: ignore[attr-defined]
    return StaffOrder(
        order_id=mapping["id"],
        state=mapping["state"],
        source=mapping["source"],
        snapshot=OrderSnapshot.model_validate(mapping["order_snapshot"]),
        identity_verified=mapping["customer_id"] is not None,
        expires_at=mapping["expires_at"],
        confirmed_at=mapping["confirmed_at"],
        confirmed_by_staff_user_id=mapping["confirmed_by_staff_user_id"],
        created_at=mapping["created_at"],
        updated_at=mapping["updated_at"],
    )


async def list_staff_orders(
    transaction: TenantTransaction,
    *,
    state: OrderState | None,
    limit: int,
    offset: int,
) -> StaffOrderPage:
    require_order_operator(transaction)
    await expire_holds(transaction)
    parameters = {
        "tenant_id": transaction.tenant_id,
        "state": state,
        "limit": limit,
        "offset": offset,
    }
    total = await transaction.session.scalar(
        text(
            "SELECT count(*) FROM orders WHERE tenant_id = :tenant_id "
            "AND (CAST(:state AS text) IS NULL OR state = CAST(:state AS text))"
        ),
        parameters,
    )
    rows: Sequence[object] = (
        await transaction.session.execute(
            text(
                "SELECT * FROM orders WHERE tenant_id = :tenant_id "
                "AND (CAST(:state AS text) IS NULL OR state = CAST(:state AS text)) "
                "ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset"
            ),
            parameters,
        )
    ).all()
    return StaffOrderPage(
        items=[_staff_order_from_row(row) for row in rows],
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


async def load_staff_order(transaction: TenantTransaction, order_id: UUID) -> StaffOrder:
    require_order_operator(transaction)
    await expire_holds(transaction)
    row = (
        await transaction.session.execute(
            text("SELECT * FROM orders WHERE tenant_id = :tenant_id AND id = :order_id"),
            {"tenant_id": transaction.tenant_id, "order_id": order_id},
        )
    ).one_or_none()
    if row is None:
        raise OrderNotFound
    return _staff_order_from_row(row)


async def _transition_order(
    transaction: TenantTransaction,
    order_id: UUID,
    idempotency_key: str,
    *,
    target: Literal["confirmed", "cancelled"],
) -> OrderTransitionResult:
    require_order_operator(transaction)
    key_hash = capability_digest(idempotency_key)
    lock_key = int.from_bytes(
        hashlib.sha256(transaction.tenant_id.bytes + key_hash).digest()[:8],
        "big",
        signed=True,
    )
    await transaction.session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
    )
    await expire_holds(transaction)
    event_type = f"order_{target}"
    previous = (
        await transaction.session.execute(
            text(
                "SELECT order_id, event_type FROM order_events "
                "WHERE tenant_id = :tenant_id AND idempotency_key_hash = :key_hash"
            ),
            {"tenant_id": transaction.tenant_id, "key_hash": key_hash},
        )
    ).one_or_none()
    if previous is not None:
        if previous.order_id != order_id or previous.event_type != event_type:
            raise OrderTransitionConflict("Idempotency key was already used")
        return OrderTransitionResult(
            order=await load_staff_order(transaction, order_id), replayed=True
        )

    order = (
        await transaction.session.execute(
            text("SELECT * FROM orders WHERE tenant_id = :tenant_id AND id = :order_id FOR UPDATE"),
            {"tenant_id": transaction.tenant_id, "order_id": order_id},
        )
    ).one_or_none()
    if order is None:
        raise OrderNotFound

    if target == "confirmed":
        if order.state != "held":
            raise OrderTransitionConflict("Only an active held order can be confirmed")
        if order.customer_id is None:
            raise OrderTransitionConflict("Verified customer identity is required")
        allocation_id = await transaction.session.scalar(
            text(
                "UPDATE rental_allocations SET state = 'confirmed', expires_at = NULL, "
                "updated_at = now() WHERE tenant_id = :tenant_id AND id = :allocation_id "
                "AND state = 'held' AND expires_at > now() RETURNING id"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "allocation_id": order.allocation_id,
            },
        )
        if allocation_id is None:
            raise OrderTransitionConflict("The inventory hold is no longer active")
        await transaction.session.execute(
            text(
                "UPDATE orders SET state = 'confirmed', confirmed_at = now(), "
                "confirmed_by_staff_user_id = :user_id, updated_at = now() "
                "WHERE tenant_id = :tenant_id AND id = :order_id"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "order_id": order_id,
                "user_id": transaction.user_id,
            },
        )
    else:
        if order.state not in {"held", "confirmed"}:
            raise OrderTransitionConflict("Only a held or confirmed order can be cancelled")
        await transaction.session.execute(
            text(
                "UPDATE rental_allocations SET state = 'cancelled', expires_at = NULL, "
                "updated_at = now() WHERE tenant_id = :tenant_id AND id = :allocation_id "
                "AND state IN ('held', 'confirmed')"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "allocation_id": order.allocation_id,
            },
        )
        await transaction.session.execute(
            text(
                "UPDATE orders SET state = 'cancelled', updated_at = now() "
                "WHERE tenant_id = :tenant_id AND id = :order_id"
            ),
            {"tenant_id": transaction.tenant_id, "order_id": order_id},
        )

    await transaction.session.execute(
        text(
            "INSERT INTO order_events "
            "(id, tenant_id, order_id, event_type, payload, idempotency_key_hash, "
            "actor_staff_user_id) VALUES "
            "(:id, :tenant_id, :order_id, :event_type, CAST(:payload AS jsonb), :key_hash, "
            ":user_id)"
        ),
        {
            "id": uuid4(),
            "tenant_id": transaction.tenant_id,
            "order_id": order_id,
            "event_type": event_type,
            "payload": json.dumps({"from": order.state, "to": target}),
            "key_hash": key_hash,
            "user_id": transaction.user_id,
        },
    )
    return OrderTransitionResult(
        order=await load_staff_order(transaction, order_id), replayed=False
    )


async def confirm_order(
    transaction: TenantTransaction, order_id: UUID, idempotency_key: str
) -> OrderTransitionResult:
    return await _transition_order(transaction, order_id, idempotency_key, target="confirmed")


async def cancel_order(
    transaction: TenantTransaction, order_id: UUID, idempotency_key: str
) -> OrderTransitionResult:
    return await _transition_order(transaction, order_id, idempotency_key, target="cancelled")
