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
from app.contact_crypto import ContactCipher, EncryptedContact
from app.database import TenantTransaction
from app.holds import expire_holds
from app.orders import OrderSnapshot

OrderState = Literal[
    "held",
    "confirmed",
    "in_progress",
    "returned",
    "completed",
    "expired",
    "cancelled",
]
OrderSource = Literal["website", "phone"]
InspectionOutcome = Literal["passed", "maintenance_required"]


class StaffOrder(BaseModel):
    """Operational order data without customer contact details or asset secrets."""

    model_config = ConfigDict(frozen=True)

    order_id: UUID
    state: OrderState
    source: OrderSource
    snapshot: OrderSnapshot
    identity_verified: bool
    payment_status: Literal["not_started"] = "not_started"
    expires_at: datetime | None
    confirmed_at: datetime | None
    confirmed_by_staff_user_id: UUID | None
    handed_over_at: datetime | None
    handed_over_by_staff_user_id: UUID | None
    returned_at: datetime | None
    returned_by_staff_user_id: UUID | None
    completed_at: datetime | None
    completed_by_staff_user_id: UUID | None
    inspection_outcome: InspectionOutcome | None
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


class InspectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: InspectionOutcome


class StaffOrderContact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    phone_e164: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    recipient_name: str = Field(min_length=1, max_length=120)
    delivery_address: str | None = Field(default=None, max_length=500)


class ContactAccessResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    contact: StaffOrderContact
    replayed: bool


class OrderPermissionDenied(Exception):
    """The staff role cannot manage orders."""


class OrderNotFound(Exception):
    """No order is visible in the authorized tenant scope."""


class OrderTransitionConflict(Exception):
    """The requested transition is invalid or its idempotency key conflicts."""


class OrderContactUnavailable(Exception):
    """The order has no readable encrypted contact snapshot."""


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
        identity_verified=mapping["phone_verified_at"] is not None,
        expires_at=mapping["expires_at"],
        confirmed_at=mapping["confirmed_at"],
        confirmed_by_staff_user_id=mapping["confirmed_by_staff_user_id"],
        handed_over_at=mapping["handed_over_at"],
        handed_over_by_staff_user_id=mapping["handed_over_by_staff_user_id"],
        returned_at=mapping["returned_at"],
        returned_by_staff_user_id=mapping["returned_by_staff_user_id"],
        completed_at=mapping["completed_at"],
        completed_by_staff_user_id=mapping["completed_by_staff_user_id"],
        inspection_outcome=mapping["inspection_outcome"],
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
                "SELECT o.*, c.phone_verified_at FROM orders o LEFT JOIN customers c "
                "ON c.tenant_id = o.tenant_id AND c.id = o.customer_id "
                "WHERE o.tenant_id = :tenant_id "
                "AND (CAST(:state AS text) IS NULL OR o.state = CAST(:state AS text)) "
                "ORDER BY o.created_at DESC, o.id DESC LIMIT :limit OFFSET :offset"
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
            text(
                "SELECT o.*, c.phone_verified_at FROM orders o LEFT JOIN customers c "
                "ON c.tenant_id = o.tenant_id AND c.id = o.customer_id "
                "WHERE o.tenant_id = :tenant_id AND o.id = :order_id"
            ),
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
            text(
                "SELECT o.*, c.phone_verified_at FROM orders o LEFT JOIN customers c "
                "ON c.tenant_id = o.tenant_id AND c.id = o.customer_id "
                "WHERE o.tenant_id = :tenant_id AND o.id = :order_id FOR UPDATE OF o"
            ),
            {"tenant_id": transaction.tenant_id, "order_id": order_id},
        )
    ).one_or_none()
    if order is None:
        raise OrderNotFound

    if target == "confirmed":
        if order.state != "held":
            raise OrderTransitionConflict("Only an active held order can be confirmed")
        if order.phone_verified_at is None:
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


async def _lock_command(transaction: TenantTransaction, key_hash: bytes) -> None:
    lock_key = int.from_bytes(
        hashlib.sha256(transaction.tenant_id.bytes + key_hash).digest()[:8],
        "big",
        signed=True,
    )
    await transaction.session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
    )


async def _command_was_replayed(
    transaction: TenantTransaction,
    order_id: UUID,
    key_hash: bytes,
    event_type: str,
    *,
    inspection_outcome: InspectionOutcome | None = None,
) -> bool:
    previous = (
        await transaction.session.execute(
            text(
                "SELECT order_id, event_type, payload FROM order_events "
                "WHERE tenant_id = :tenant_id AND idempotency_key_hash = :key_hash"
            ),
            {"tenant_id": transaction.tenant_id, "key_hash": key_hash},
        )
    ).one_or_none()
    if previous is None:
        return False
    payload = previous.payload if isinstance(previous.payload, dict) else {}
    if (
        previous.order_id != order_id
        or previous.event_type != event_type
        or (inspection_outcome is not None and payload.get("outcome") != inspection_outcome)
    ):
        raise OrderTransitionConflict("Idempotency key was already used")
    return True


async def _record_event(
    transaction: TenantTransaction,
    order_id: UUID,
    event_type: str,
    payload: dict[str, str],
    key_hash: bytes,
) -> None:
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
            "payload": json.dumps(payload),
            "key_hash": key_hash,
            "user_id": transaction.user_id,
        },
    )


async def access_order_contact(
    transaction: TenantTransaction,
    order_id: UUID,
    idempotency_key: str,
    cipher: ContactCipher,
) -> ContactAccessResult:
    """Decrypt one order snapshot only after an authorized, audited staff command."""

    require_order_operator(transaction)
    key_hash = capability_digest(idempotency_key)
    await _lock_command(transaction, key_hash)
    replayed = await _command_was_replayed(transaction, order_id, key_hash, "contact_accessed")
    row = (
        await transaction.session.execute(
            text(
                "SELECT o.id, o.state, op.ciphertext, op.nonce, op.key_version "
                "FROM orders o LEFT JOIN order_private_details op "
                "ON op.tenant_id = o.tenant_id AND op.order_id = o.id "
                "WHERE o.tenant_id = :tenant_id AND o.id = :order_id"
            ),
            {"tenant_id": transaction.tenant_id, "order_id": order_id},
        )
    ).one_or_none()
    if row is None:
        raise OrderNotFound
    if row.state not in {"confirmed", "in_progress", "returned"}:
        raise OrderContactUnavailable
    if row.ciphertext is None or row.nonce is None or row.key_version != 1:
        raise OrderContactUnavailable
    try:
        payload = cipher.decrypt(
            transaction.tenant_id,
            "order",
            order_id,
            EncryptedContact(
                nonce=bytes(row.nonce),
                ciphertext=bytes(row.ciphertext),
                key_version=row.key_version,
            ),
        )
        contact = StaffOrderContact.model_validate(payload)
    except Exception as error:
        raise OrderContactUnavailable from error
    if not replayed:
        await _record_event(
            transaction,
            order_id,
            "contact_accessed",
            {"reason": "fulfillment"},
            key_hash,
        )
    return ContactAccessResult(contact=contact, replayed=replayed)


async def _load_fulfillment_order(transaction: TenantTransaction, order_id: UUID) -> object:
    row = (
        await transaction.session.execute(
            text(
                "SELECT o.id, o.state, o.allocation_id, ra.asset_id, "
                "ra.state AS allocation_state, a.state AS asset_state "
                "FROM orders o JOIN rental_allocations ra "
                "ON ra.tenant_id = o.tenant_id AND ra.id = o.allocation_id "
                "JOIN assets a ON a.tenant_id = ra.tenant_id AND a.id = ra.asset_id "
                "WHERE o.tenant_id = :tenant_id AND o.id = :order_id "
                "FOR UPDATE OF o, ra, a"
            ),
            {"tenant_id": transaction.tenant_id, "order_id": order_id},
        )
    ).one_or_none()
    if row is None:
        raise OrderNotFound
    return row


async def hand_over_order(
    transaction: TenantTransaction, order_id: UUID, idempotency_key: str
) -> OrderTransitionResult:
    require_order_operator(transaction)
    key_hash = capability_digest(idempotency_key)
    await _lock_command(transaction, key_hash)
    if await _command_was_replayed(transaction, order_id, key_hash, "order_handed_over"):
        return OrderTransitionResult(
            order=await load_staff_order(transaction, order_id), replayed=True
        )
    order = await _load_fulfillment_order(transaction, order_id)
    if order.state != "confirmed":  # type: ignore[attr-defined]
        raise OrderTransitionConflict("Only a confirmed order can be handed over")
    if order.allocation_state != "confirmed":  # type: ignore[attr-defined]
        raise OrderTransitionConflict("The confirmed inventory allocation is unavailable")
    changed = await transaction.session.scalar(
        text(
            "UPDATE assets SET state = 'rented', updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :asset_id AND state = 'available' "
            "RETURNING id"
        ),
        {"tenant_id": transaction.tenant_id, "asset_id": order.asset_id},  # type: ignore[attr-defined]
    )
    if changed is None:
        raise OrderTransitionConflict("The assigned asset is not available for handover")
    await transaction.session.execute(
        text(
            "UPDATE orders SET state = 'in_progress', handed_over_at = now(), "
            "handed_over_by_staff_user_id = :user_id, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :order_id"
        ),
        {
            "tenant_id": transaction.tenant_id,
            "order_id": order_id,
            "user_id": transaction.user_id,
        },
    )
    await _record_event(
        transaction,
        order_id,
        "order_handed_over",
        {"from": "confirmed", "to": "in_progress"},
        key_hash,
    )
    return OrderTransitionResult(
        order=await load_staff_order(transaction, order_id), replayed=False
    )


async def return_order(
    transaction: TenantTransaction, order_id: UUID, idempotency_key: str
) -> OrderTransitionResult:
    require_order_operator(transaction)
    key_hash = capability_digest(idempotency_key)
    await _lock_command(transaction, key_hash)
    if await _command_was_replayed(transaction, order_id, key_hash, "order_returned"):
        return OrderTransitionResult(
            order=await load_staff_order(transaction, order_id), replayed=True
        )
    order = await _load_fulfillment_order(transaction, order_id)
    if order.state != "in_progress":  # type: ignore[attr-defined]
        raise OrderTransitionConflict("Only an in-progress order can be returned")
    changed = await transaction.session.scalar(
        text(
            "UPDATE assets SET state = 'inspection', updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :asset_id AND state = 'rented' "
            "RETURNING id"
        ),
        {"tenant_id": transaction.tenant_id, "asset_id": order.asset_id},  # type: ignore[attr-defined]
    )
    if changed is None:
        raise OrderTransitionConflict("The assigned asset is not marked as rented")
    await transaction.session.execute(
        text(
            "UPDATE orders SET state = 'returned', returned_at = now(), "
            "returned_by_staff_user_id = :user_id, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :order_id"
        ),
        {
            "tenant_id": transaction.tenant_id,
            "order_id": order_id,
            "user_id": transaction.user_id,
        },
    )
    await _record_event(
        transaction,
        order_id,
        "order_returned",
        {"from": "in_progress", "to": "returned"},
        key_hash,
    )
    return OrderTransitionResult(
        order=await load_staff_order(transaction, order_id), replayed=False
    )


async def complete_order_inspection(
    transaction: TenantTransaction,
    order_id: UUID,
    idempotency_key: str,
    outcome: InspectionOutcome,
) -> OrderTransitionResult:
    require_order_operator(transaction)
    key_hash = capability_digest(idempotency_key)
    await _lock_command(transaction, key_hash)
    if await _command_was_replayed(
        transaction,
        order_id,
        key_hash,
        "order_inspection_completed",
        inspection_outcome=outcome,
    ):
        return OrderTransitionResult(
            order=await load_staff_order(transaction, order_id), replayed=True
        )
    order = await _load_fulfillment_order(transaction, order_id)
    if order.state != "returned":  # type: ignore[attr-defined]
        raise OrderTransitionConflict("Only a returned order can complete inspection")
    if order.allocation_state != "confirmed":  # type: ignore[attr-defined]
        raise OrderTransitionConflict("The inventory allocation is not active")
    released = await transaction.session.scalar(
        text(
            "UPDATE rental_allocations SET state = 'released', updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :allocation_id AND state = 'confirmed' "
            "RETURNING id"
        ),
        {
            "tenant_id": transaction.tenant_id,
            "allocation_id": order.allocation_id,  # type: ignore[attr-defined]
        },
    )
    if released is None:
        raise OrderTransitionConflict("The inventory allocation could not be released")
    asset_state = "available" if outcome == "passed" else "maintenance"
    changed = await transaction.session.scalar(
        text(
            "UPDATE assets SET state = :asset_state, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :asset_id AND state = 'inspection' "
            "RETURNING id"
        ),
        {
            "tenant_id": transaction.tenant_id,
            "asset_id": order.asset_id,  # type: ignore[attr-defined]
            "asset_state": asset_state,
        },
    )
    if changed is None:
        raise OrderTransitionConflict("The returned asset is not awaiting inspection")
    await transaction.session.execute(
        text(
            "UPDATE orders SET state = 'completed', completed_at = now(), "
            "completed_by_staff_user_id = :user_id, inspection_outcome = :outcome, "
            "updated_at = now() WHERE tenant_id = :tenant_id AND id = :order_id"
        ),
        {
            "tenant_id": transaction.tenant_id,
            "order_id": order_id,
            "user_id": transaction.user_id,
            "outcome": outcome,
        },
    )
    await _record_event(
        transaction,
        order_id,
        "order_inspection_completed",
        {"from": "returned", "to": "completed", "outcome": outcome},
        key_hash,
    )
    return OrderTransitionResult(
        order=await load_staff_order(transaction, order_id), replayed=False
    )
