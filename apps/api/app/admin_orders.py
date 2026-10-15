"""Tenant-scoped staff order queue and idempotent lifecycle commands."""

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.capabilities import capability_digest
from app.contact_crypto import ContactCipher, EncryptedContact
from app.database import TenantTransaction
from app.holds import expire_holds
from app.order_requests import (
    ExtensionRequestView,
    ReturnRequestView,
    cancel_planned_return_notifications,
    extension_request_from_mapping,
    plan_return_notifications,
    queue_decision_notification,
    return_request_from_mapping,
)
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
    planned_end_date: date
    is_overdue: bool
    overdue_days: int = Field(ge=0)
    extension_request: ExtensionRequestView | None
    return_request: ReturnRequestView | None
    identity_verified: bool
    payment_status: Literal["not_started"] = "not_started"
    expires_at: datetime | None
    confirmed_at: datetime | None
    handed_over_at: datetime | None
    returned_at: datetime | None
    completed_at: datetime | None
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


class RequestDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal["availability_conflict", "manual_review"] = "manual_review"


class ReturnRequestDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal["date_unavailable", "manual_review"] = "manual_review"


class ExtensionDecisionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    order: StaffOrder
    request: ExtensionRequestView
    replayed: bool


class ReturnDecisionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    order: StaffOrder
    request: ReturnRequestView
    replayed: bool


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
        planned_end_date=mapping["end_date"],
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
        identity_verified=mapping["phone_verified_at"] is not None,
        expires_at=mapping["expires_at"],
        confirmed_at=mapping["confirmed_at"],
        handed_over_at=mapping["handed_over_at"],
        returned_at=mapping["returned_at"],
        completed_at=mapping["completed_at"],
        inspection_outcome=mapping["inspection_outcome"],
        created_at=mapping["created_at"],
        updated_at=mapping["updated_at"],
    )


STAFF_ORDER_PROJECTION = """
SELECT o.*, c.phone_verified_at,
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
LEFT JOIN customers c ON c.tenant_id = o.tenant_id AND c.id = o.customer_id
JOIN branches b ON b.tenant_id = o.tenant_id AND b.id = o.branch_id
"""


async def list_staff_orders(
    transaction: TenantTransaction,
    *,
    state: OrderState | None,
    overdue: bool,
    requests: bool,
    limit: int,
    offset: int,
) -> StaffOrderPage:
    require_order_operator(transaction)
    await expire_holds(transaction)
    parameters = {
        "tenant_id": transaction.tenant_id,
        "state": state,
        "overdue": overdue,
        "requests": requests,
        "limit": limit,
        "offset": offset,
    }
    total = await transaction.session.scalar(
        text(
            "SELECT count(*) FROM orders o JOIN branches b "
            "ON b.tenant_id = o.tenant_id AND b.id = o.branch_id "
            "WHERE o.tenant_id = :tenant_id "
            "AND (CAST(:state AS text) IS NULL OR o.state = CAST(:state AS text)) "
            "AND (NOT :overdue OR (o.state = 'in_progress' "
            "AND o.end_date < (now() AT TIME ZONE b.timezone)::date)) "
            "AND (NOT :requests OR EXISTS (SELECT 1 FROM order_extension_requests er "
            "WHERE er.tenant_id = o.tenant_id AND er.order_id = o.id AND er.state = 'pending') "
            "OR EXISTS (SELECT 1 FROM order_return_requests rr WHERE rr.tenant_id = o.tenant_id "
            "AND rr.order_id = o.id AND rr.state = 'pending'))"
        ),
        parameters,
    )
    rows: Sequence[object] = (
        await transaction.session.execute(
            text(
                STAFF_ORDER_PROJECTION  # noqa: S608 - projection and clauses are constants
                + " WHERE o.tenant_id = :tenant_id "
                "AND (CAST(:state AS text) IS NULL OR o.state = CAST(:state AS text)) "
                "AND (NOT :overdue OR (o.state = 'in_progress' "
                "AND o.end_date < (now() AT TIME ZONE b.timezone)::date)) "
                "AND (NOT :requests OR EXISTS (SELECT 1 FROM order_extension_requests er "
                "WHERE er.tenant_id = o.tenant_id AND er.order_id = o.id AND er.state = 'pending') "
                "OR EXISTS (SELECT 1 FROM order_return_requests rr WHERE "
                "rr.tenant_id = o.tenant_id "
                "AND rr.order_id = o.id AND rr.state = 'pending')) "
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
            text(STAFF_ORDER_PROJECTION + " WHERE o.tenant_id = :tenant_id AND o.id = :order_id"),
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
                "UPDATE courier_tasks SET state = 'cancelled', updated_at = now() "
                "WHERE tenant_id = :tenant_id AND order_id = :order_id "
                "AND state IN ('assigned', 'accepted')"
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
    request_id: UUID | None = None,
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
        or (request_id is not None and payload.get("request_id") != str(request_id))
    ):
        raise OrderTransitionConflict("Idempotency key was already used")
    return True


async def _record_event(
    transaction: TenantTransaction,
    order_id: UUID,
    event_type: str,
    payload: dict[str, str | int],
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
    if row.ciphertext is None or row.nonce is None:
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
                "SELECT o.id, o.state, o.fulfillment, o.allocation_id, ra.asset_id, "
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
    assigned_delivery = await transaction.session.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM courier_tasks WHERE tenant_id = :tenant_id "
            "AND order_id = :order_id AND state IN ('assigned', 'accepted'))"
        ),
        {"tenant_id": transaction.tenant_id, "order_id": order_id},
    )
    if order.fulfillment == "delivery" and assigned_delivery:  # type: ignore[attr-defined]
        raise OrderTransitionConflict("Assigned delivery must be picked up by its courier")
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
    await plan_return_notifications(transaction, order_id)
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
    await cancel_planned_return_notifications(transaction, order_id)
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


async def decide_extension_request(
    transaction: TenantTransaction,
    order_id: UUID,
    request_id: UUID,
    idempotency_key: str,
    *,
    approve: bool,
    rejection_reason: Literal["availability_conflict", "manual_review"] = "manual_review",
) -> ExtensionDecisionResult:
    require_order_operator(transaction)
    key_hash = capability_digest(idempotency_key)
    await _lock_command(transaction, key_hash)
    event_type = "order_extension_approved" if approve else "order_extension_rejected"
    replayed = await _command_was_replayed(
        transaction, order_id, key_hash, event_type, request_id=request_id
    )
    if replayed:
        request = (
            await transaction.session.execute(
                text(
                    "SELECT * FROM order_extension_requests WHERE tenant_id = :tenant_id "
                    "AND id = :request_id AND order_id = :order_id"
                ),
                {
                    "tenant_id": transaction.tenant_id,
                    "request_id": request_id,
                    "order_id": order_id,
                },
            )
        ).one_or_none()
        if request is None:
            raise OrderNotFound
        return ExtensionDecisionResult(
            order=await load_staff_order(transaction, order_id),
            request=extension_request_from_mapping(request),
            replayed=True,
        )
    row = (
        await transaction.session.execute(
            text(
                "SELECT er.*, o.state AS order_state, o.allocation_id, ra.asset_id "
                "FROM order_extension_requests er JOIN orders o "
                "ON o.tenant_id = er.tenant_id AND o.id = er.order_id "
                "JOIN rental_allocations ra ON ra.tenant_id = o.tenant_id "
                "AND ra.id = o.allocation_id WHERE er.tenant_id = :tenant_id "
                "AND er.id = :request_id AND er.order_id = :order_id "
                "FOR UPDATE OF er, o, ra"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "request_id": request_id,
                "order_id": order_id,
            },
        )
    ).one_or_none()
    if row is None:
        raise OrderNotFound
    if row.state != "pending":
        raise OrderTransitionConflict("Only a pending extension request can be decided")
    if row.order_state != "in_progress":
        raise OrderTransitionConflict("Only an active rental can be extended")
    if approve:
        conflict = await transaction.session.scalar(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM asset_blocks ab WHERE ab.tenant_id = :tenant_id "
                "  AND ab.asset_id = :asset_id AND ab.start_date <= :requested_end_date "
                "  AND ab.end_date > :original_end_date"
                ") OR EXISTS ("
                "  SELECT 1 FROM rental_allocations other WHERE other.tenant_id = :tenant_id "
                "  AND other.asset_id = :asset_id AND other.id <> :allocation_id "
                "  AND other.start_date <= :requested_end_date "
                "  AND other.end_date > :original_end_date "
                "  AND (other.state = 'confirmed' OR "
                "    (other.state = 'held' AND other.expires_at > now()))"
                ")"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "asset_id": row.asset_id,
                "allocation_id": row.allocation_id,
                "original_end_date": row.original_end_date,
                "requested_end_date": row.requested_end_date,
            },
        )
        if conflict:
            raise OrderTransitionConflict(
                "Requested extension conflicts with a future booking or asset block"
            )
        await transaction.session.execute(
            text(
                "UPDATE rental_allocations SET end_date = :end_date, updated_at = now() "
                "WHERE tenant_id = :tenant_id AND id = :allocation_id AND state = 'confirmed'"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "allocation_id": row.allocation_id,
                "end_date": row.requested_end_date,
            },
        )
        await transaction.session.execute(
            text(
                "UPDATE orders SET end_date = :end_date, updated_at = now() "
                "WHERE tenant_id = :tenant_id AND id = :order_id"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "order_id": order_id,
                "end_date": row.requested_end_date,
            },
        )
        await transaction.session.execute(
            text(
                "UPDATE order_extension_requests SET state = 'approved', decided_at = now(), "
                "decided_by_staff_user_id = :user_id, updated_at = now() "
                "WHERE tenant_id = :tenant_id AND id = :request_id"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "request_id": request_id,
                "user_id": transaction.user_id,
            },
        )
        await cancel_planned_return_notifications(transaction, order_id)
        await plan_return_notifications(transaction, order_id)
        await queue_decision_notification(
            transaction,
            order_id=order_id,
            customer_id=row.customer_id,
            template="extension_approved",
            request_id=request_id,
        )
    else:
        await transaction.session.execute(
            text(
                "UPDATE order_extension_requests SET state = 'rejected', decided_at = now(), "
                "decided_by_staff_user_id = :user_id, rejection_reason = :reason, "
                "updated_at = now() WHERE tenant_id = :tenant_id AND id = :request_id"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "request_id": request_id,
                "user_id": transaction.user_id,
                "reason": rejection_reason,
            },
        )
        await queue_decision_notification(
            transaction,
            order_id=order_id,
            customer_id=row.customer_id,
            template="extension_rejected",
            request_id=request_id,
        )
    await _record_event(
        transaction,
        order_id,
        event_type,
        {
            "request_id": str(request_id),
            "original_end_date": row.original_end_date.isoformat(),
            "requested_end_date": row.requested_end_date.isoformat(),
            "amount_minor": row.amount_minor,
            **({} if approve else {"reason": rejection_reason}),
        },
        key_hash,
    )
    request = (
        await transaction.session.execute(
            text(
                "SELECT * FROM order_extension_requests WHERE tenant_id = :tenant_id "
                "AND id = :request_id"
            ),
            {"tenant_id": transaction.tenant_id, "request_id": request_id},
        )
    ).one()
    return ExtensionDecisionResult(
        order=await load_staff_order(transaction, order_id),
        request=extension_request_from_mapping(request),
        replayed=False,
    )


async def decide_return_request(
    transaction: TenantTransaction,
    order_id: UUID,
    request_id: UUID,
    idempotency_key: str,
    *,
    acknowledge: bool,
    rejection_reason: Literal["date_unavailable", "manual_review"] = "manual_review",
) -> ReturnDecisionResult:
    require_order_operator(transaction)
    key_hash = capability_digest(idempotency_key)
    await _lock_command(transaction, key_hash)
    event_type = "order_return_acknowledged" if acknowledge else "order_return_rejected"
    replayed = await _command_was_replayed(
        transaction, order_id, key_hash, event_type, request_id=request_id
    )
    if not replayed:
        request = (
            await transaction.session.execute(
                text(
                    "SELECT rr.*, o.state AS order_state FROM order_return_requests rr "
                    "JOIN orders o ON o.tenant_id = rr.tenant_id AND o.id = rr.order_id "
                    "WHERE rr.tenant_id = :tenant_id AND rr.id = :request_id "
                    "AND rr.order_id = :order_id FOR UPDATE OF rr, o"
                ),
                {
                    "tenant_id": transaction.tenant_id,
                    "request_id": request_id,
                    "order_id": order_id,
                },
            )
        ).one_or_none()
        if request is None:
            raise OrderNotFound
        if request.state != "pending":
            raise OrderTransitionConflict("Only a pending return request can be decided")
        if request.order_state != "in_progress":
            raise OrderTransitionConflict("Only an active rental can schedule return")
        state = "acknowledged" if acknowledge else "rejected"
        await transaction.session.execute(
            text(
                "UPDATE order_return_requests SET state = :state, decided_at = now(), "
                "decided_by_staff_user_id = :user_id, rejection_reason = :reason, "
                "updated_at = now() WHERE tenant_id = :tenant_id AND id = :request_id"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "request_id": request_id,
                "user_id": transaction.user_id,
                "state": state,
                "reason": None if acknowledge else rejection_reason,
            },
        )
        await queue_decision_notification(
            transaction,
            order_id=order_id,
            customer_id=request.customer_id,
            template="return_acknowledged" if acknowledge else "return_rejected",
            request_id=request_id,
        )
        await _record_event(
            transaction,
            order_id,
            event_type,
            {
                "request_id": str(request_id),
                "requested_date": request.requested_date.isoformat(),
                **({} if acknowledge else {"reason": rejection_reason}),
            },
            key_hash,
        )
    stored = (
        await transaction.session.execute(
            text(
                "SELECT * FROM order_return_requests WHERE tenant_id = :tenant_id "
                "AND id = :request_id AND order_id = :order_id"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "request_id": request_id,
                "order_id": order_id,
            },
        )
    ).one_or_none()
    if stored is None:
        raise OrderNotFound
    return ReturnDecisionResult(
        order=await load_staff_order(transaction, order_id),
        request=return_request_from_mapping(stored),
        replayed=replayed,
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
