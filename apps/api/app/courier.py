"""Least-privilege courier dispatch and assigned delivery task commands."""

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.capabilities import capability_digest
from app.contact_crypto import ContactCipher, EncryptedContact
from app.database import TenantTransaction
from app.order_requests import plan_return_notifications
from app.orders import OrderSnapshot

CourierTaskState = Literal["assigned", "accepted", "picked_up", "delivered"]


class CourierOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: UUID
    display_name: str = Field(min_length=1, max_length=120)


class CourierAssignment(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: UUID
    order_id: UUID
    courier_user_id: UUID
    courier_display_name: str = Field(min_length=1, max_length=120)
    state: CourierTaskState
    assigned_at: datetime
    accepted_at: datetime | None
    picked_up_at: datetime | None
    delivered_at: datetime | None
    updated_at: datetime


class DispatchSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    couriers: list[CourierOption]
    assignments: list[CourierAssignment]


class CourierAssignmentCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    courier_user_id: UUID


class CourierAssignmentResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    assignment: CourierAssignment | None
    replayed: bool


class CourierTask(BaseModel):
    """Assigned work without inventory identifiers or contact fields."""

    model_config = ConfigDict(frozen=True)

    task_id: UUID
    order_id: UUID
    state: CourierTaskState
    snapshot: OrderSnapshot
    planned_end_date: date
    assigned_at: datetime
    accepted_at: datetime | None
    picked_up_at: datetime | None
    delivered_at: datetime | None
    updated_at: datetime


class CourierTaskPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[CourierTask]


class CourierTaskResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    task: CourierTask
    replayed: bool


class CourierContact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    phone_e164: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    recipient_name: str = Field(min_length=1, max_length=120)
    delivery_address: str = Field(min_length=1, max_length=500)


class CourierContactResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    contact: CourierContact
    replayed: bool


class CourierPermissionDenied(Exception):
    """The current role cannot use this courier operation."""


class CourierTaskNotFound(Exception):
    """The task is outside the authorized tenant/user scope or missing."""


class CourierConflict(Exception):
    """The requested assignment or task transition is invalid."""


class CourierContactUnavailable(Exception):
    """The assigned delivery has no readable delivery contact."""


def require_dispatcher(transaction: TenantTransaction) -> None:
    if transaction.role not in {"owner", "admin", "operator"}:
        raise CourierPermissionDenied


def require_courier(transaction: TenantTransaction) -> None:
    if transaction.role != "courier":
        raise CourierPermissionDenied


def _assignment(row: object) -> CourierAssignment:
    mapping = row._mapping  # type: ignore[attr-defined]
    return CourierAssignment(
        task_id=mapping["id"],
        order_id=mapping["order_id"],
        courier_user_id=mapping["courier_user_id"],
        courier_display_name=mapping["courier_display_name"],
        state=mapping["state"],
        assigned_at=mapping["assigned_at"],
        accepted_at=mapping["accepted_at"],
        picked_up_at=mapping["picked_up_at"],
        delivered_at=mapping["delivered_at"],
        updated_at=mapping["updated_at"],
    )


ASSIGNMENT_PROJECTION = """
SELECT ct.*, tm.display_name AS courier_display_name
FROM courier_tasks ct
JOIN tenant_memberships tm
  ON tm.tenant_id = ct.tenant_id AND tm.user_id = ct.courier_user_id
"""


async def load_dispatch_snapshot(transaction: TenantTransaction) -> DispatchSnapshot:
    require_dispatcher(transaction)
    couriers = (
        await transaction.session.execute(
            text(
                "SELECT user_id, display_name FROM tenant_memberships "
                "WHERE tenant_id = :tenant_id AND role = 'courier' AND state = 'active' "
                "ORDER BY display_name, user_id"
            ),
            {"tenant_id": transaction.tenant_id},
        )
    ).all()
    assignments: Sequence[object] = (
        await transaction.session.execute(
            text(
                ASSIGNMENT_PROJECTION
                + " WHERE ct.tenant_id = :tenant_id AND ct.state <> 'cancelled' "
                "ORDER BY ct.assigned_at DESC, ct.id DESC LIMIT 200"
            ),
            {"tenant_id": transaction.tenant_id},
        )
    ).all()
    return DispatchSnapshot(
        couriers=[
            CourierOption(user_id=row.user_id, display_name=row.display_name) for row in couriers
        ],
        assignments=[_assignment(row) for row in assignments],
    )


async def _lock_idempotency(transaction: TenantTransaction, key_hash: bytes) -> None:
    lock_key = int.from_bytes(
        hashlib.sha256(transaction.tenant_id.bytes + key_hash).digest()[:8],
        "big",
        signed=True,
    )
    await transaction.session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
    )


async def _previous_event(transaction: TenantTransaction, key_hash: bytes) -> Any | None:
    return (
        await transaction.session.execute(
            text(
                "SELECT order_id, event_type, payload FROM order_events "
                "WHERE tenant_id = :tenant_id AND idempotency_key_hash = :key_hash"
            ),
            {"tenant_id": transaction.tenant_id, "key_hash": key_hash},
        )
    ).one_or_none()


async def _record_event(
    transaction: TenantTransaction,
    *,
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


async def _load_assignment(
    transaction: TenantTransaction, order_id: UUID
) -> CourierAssignment | None:
    row = (
        await transaction.session.execute(
            text(
                ASSIGNMENT_PROJECTION
                + " WHERE ct.tenant_id = :tenant_id AND ct.order_id = :order_id"
            ),
            {"tenant_id": transaction.tenant_id, "order_id": order_id},
        )
    ).one_or_none()
    return None if row is None or row.state == "cancelled" else _assignment(row)


async def assign_courier(
    transaction: TenantTransaction,
    order_id: UUID,
    courier_user_id: UUID,
    idempotency_key: str,
) -> CourierAssignmentResult:
    require_dispatcher(transaction)
    key_hash = capability_digest(idempotency_key)
    await _lock_idempotency(transaction, key_hash)
    previous = await _previous_event(transaction, key_hash)
    if previous is not None:
        payload = previous.payload if isinstance(previous.payload, dict) else {}
        if (
            previous.order_id != order_id
            or previous.event_type not in {"courier_assigned", "courier_reassigned"}
            or payload.get("courier_user_id") != str(courier_user_id)
        ):
            raise CourierConflict("Idempotency key was already used")
        assignment = await _load_assignment(transaction, order_id)
        if assignment is None:
            raise CourierConflict("The assignment changed after this command")
        return CourierAssignmentResult(assignment=assignment, replayed=True)

    order = (
        await transaction.session.execute(
            text(
                "SELECT id, state, fulfillment FROM orders "
                "WHERE tenant_id = :tenant_id AND id = :order_id FOR UPDATE"
            ),
            {"tenant_id": transaction.tenant_id, "order_id": order_id},
        )
    ).one_or_none()
    if order is None:
        raise CourierTaskNotFound
    if order.fulfillment != "delivery" or order.state != "confirmed":
        raise CourierConflict("Only confirmed delivery orders can be assigned")
    courier = (
        await transaction.session.execute(
            text(
                "SELECT display_name FROM tenant_memberships "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id "
                "AND role = 'courier' AND state = 'active' FOR UPDATE"
            ),
            {"tenant_id": transaction.tenant_id, "user_id": courier_user_id},
        )
    ).one_or_none()
    if courier is None:
        raise CourierConflict("The selected courier is unavailable")
    existing = (
        await transaction.session.execute(
            text(
                "SELECT id, courier_user_id, state FROM courier_tasks "
                "WHERE tenant_id = :tenant_id AND order_id = :order_id FOR UPDATE"
            ),
            {"tenant_id": transaction.tenant_id, "order_id": order_id},
        )
    ).one_or_none()
    if existing is not None and existing.state not in {"assigned", "cancelled"}:
        raise CourierConflict("An accepted delivery cannot be reassigned")
    event_type = "courier_assigned"
    payload = {"courier_user_id": str(courier_user_id)}
    if existing is None:
        await transaction.session.execute(
            text(
                "INSERT INTO courier_tasks "
                "(id, tenant_id, order_id, courier_user_id, assigned_by_staff_user_id, state) "
                "VALUES (:id, :tenant_id, :order_id, :courier_user_id, :actor_id, 'assigned')"
            ),
            {
                "id": uuid4(),
                "tenant_id": transaction.tenant_id,
                "order_id": order_id,
                "courier_user_id": courier_user_id,
                "actor_id": transaction.user_id,
            },
        )
    else:
        event_type = "courier_reassigned"
        payload["previous_courier_user_id"] = str(existing.courier_user_id)
        await transaction.session.execute(
            text(
                "UPDATE courier_tasks SET courier_user_id = :courier_user_id, "
                "assigned_by_staff_user_id = :actor_id, state = 'assigned', "
                "assigned_at = now(), accepted_at = NULL, picked_up_at = NULL, "
                "delivered_at = NULL, updated_at = now() "
                "WHERE tenant_id = :tenant_id AND order_id = :order_id"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "order_id": order_id,
                "courier_user_id": courier_user_id,
                "actor_id": transaction.user_id,
            },
        )
    await _record_event(
        transaction,
        order_id=order_id,
        event_type=event_type,
        payload=payload,
        key_hash=key_hash,
    )
    return CourierAssignmentResult(
        assignment=await _load_assignment(transaction, order_id), replayed=False
    )


async def unassign_courier(
    transaction: TenantTransaction, order_id: UUID, idempotency_key: str
) -> CourierAssignmentResult:
    require_dispatcher(transaction)
    key_hash = capability_digest(idempotency_key)
    await _lock_idempotency(transaction, key_hash)
    previous = await _previous_event(transaction, key_hash)
    if previous is not None:
        if previous.order_id != order_id or previous.event_type != "courier_unassigned":
            raise CourierConflict("Idempotency key was already used")
        return CourierAssignmentResult(assignment=None, replayed=True)
    task = (
        await transaction.session.execute(
            text(
                "SELECT id, courier_user_id, state FROM courier_tasks "
                "WHERE tenant_id = :tenant_id AND order_id = :order_id FOR UPDATE"
            ),
            {"tenant_id": transaction.tenant_id, "order_id": order_id},
        )
    ).one_or_none()
    if task is None:
        raise CourierTaskNotFound
    if task.state != "assigned":
        raise CourierConflict("Only an unaccepted delivery can be unassigned")
    await transaction.session.execute(
        text(
            "UPDATE courier_tasks SET state = 'cancelled', updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :task_id"
        ),
        {"tenant_id": transaction.tenant_id, "task_id": task.id},
    )
    await _record_event(
        transaction,
        order_id=order_id,
        event_type="courier_unassigned",
        payload={"courier_user_id": str(task.courier_user_id)},
        key_hash=key_hash,
    )
    return CourierAssignmentResult(assignment=None, replayed=False)


def _task(row: object) -> CourierTask:
    mapping = row._mapping  # type: ignore[attr-defined]
    return CourierTask(
        task_id=mapping["id"],
        order_id=mapping["order_id"],
        state=mapping["state"],
        snapshot=OrderSnapshot.model_validate(mapping["order_snapshot"]),
        planned_end_date=mapping["end_date"],
        assigned_at=mapping["assigned_at"],
        accepted_at=mapping["accepted_at"],
        picked_up_at=mapping["picked_up_at"],
        delivered_at=mapping["delivered_at"],
        updated_at=mapping["updated_at"],
    )


TASK_PROJECTION = """
SELECT ct.*, o.order_snapshot, o.end_date
FROM courier_tasks ct
JOIN orders o ON o.tenant_id = ct.tenant_id AND o.id = ct.order_id
"""


async def list_courier_tasks(transaction: TenantTransaction) -> CourierTaskPage:
    require_courier(transaction)
    rows = (
        await transaction.session.execute(
            text(
                TASK_PROJECTION
                + " WHERE ct.tenant_id = :tenant_id AND ct.courier_user_id = :user_id "
                "AND ct.state <> 'cancelled' "
                "ORDER BY CASE ct.state WHEN 'assigned' THEN 0 WHEN 'accepted' THEN 1 "
                "WHEN 'picked_up' THEN 2 ELSE 3 END, ct.assigned_at DESC LIMIT 100"
            ),
            {"tenant_id": transaction.tenant_id, "user_id": transaction.user_id},
        )
    ).all()
    return CourierTaskPage(items=[_task(row) for row in rows])


async def _load_own_task(
    transaction: TenantTransaction, task_id: UUID, *, lock: bool = False
) -> Any:
    if lock:
        order_id = await transaction.session.scalar(
            text(
                "SELECT order_id FROM courier_tasks WHERE tenant_id = :tenant_id "
                "AND id = :task_id AND courier_user_id = :user_id AND state <> 'cancelled'"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "task_id": task_id,
                "user_id": transaction.user_id,
            },
        )
        if order_id is None:
            raise CourierTaskNotFound
        await transaction.session.execute(
            text(
                "SELECT id FROM orders WHERE tenant_id = :tenant_id AND id = :order_id FOR UPDATE"
            ),
            {"tenant_id": transaction.tenant_id, "order_id": order_id},
        )
    suffix = " FOR UPDATE OF ct" if lock else ""
    row = (
        await transaction.session.execute(
            text(
                TASK_PROJECTION + " WHERE ct.tenant_id = :tenant_id AND ct.id = :task_id "
                "AND ct.courier_user_id = :user_id AND ct.state <> 'cancelled'" + suffix
            ),
            {
                "tenant_id": transaction.tenant_id,
                "task_id": task_id,
                "user_id": transaction.user_id,
            },
        )
    ).one_or_none()
    if row is None:
        raise CourierTaskNotFound
    return row


async def transition_courier_task(
    transaction: TenantTransaction,
    task_id: UUID,
    action: Literal["accept", "pickup", "deliver"],
    idempotency_key: str,
) -> CourierTaskResult:
    require_courier(transaction)
    key_hash = capability_digest(idempotency_key)
    await _lock_idempotency(transaction, key_hash)
    event_type = {
        "accept": "courier_accepted",
        "pickup": "courier_picked_up",
        "deliver": "courier_delivered",
    }[action]
    previous = await _previous_event(transaction, key_hash)
    if previous is not None:
        payload = previous.payload if isinstance(previous.payload, dict) else {}
        if previous.event_type != event_type or payload.get("task_id") != str(task_id):
            raise CourierConflict("Idempotency key was already used")
        return CourierTaskResult(
            task=_task(await _load_own_task(transaction, task_id)), replayed=True
        )

    row = await _load_own_task(transaction, task_id, lock=True)
    expected_state = {"accept": "assigned", "pickup": "accepted", "deliver": "picked_up"}[action]
    if row.state != expected_state:
        raise CourierConflict(f"Task must be {expected_state} before {action}")
    if action == "pickup":
        if row.order_snapshot["fulfillment"] != "delivery":
            raise CourierConflict("Only delivery orders can be picked up")
        fulfillment = (
            await transaction.session.execute(
                text(
                    "SELECT o.state, o.allocation_id, ra.asset_id, "
                    "ra.state AS allocation_state, a.state AS asset_state "
                    "FROM orders o JOIN rental_allocations ra "
                    "ON ra.tenant_id = o.tenant_id AND ra.id = o.allocation_id "
                    "JOIN assets a ON a.tenant_id = ra.tenant_id AND a.id = ra.asset_id "
                    "WHERE o.tenant_id = :tenant_id AND o.id = :order_id "
                    "FOR UPDATE OF o, ra, a"
                ),
                {"tenant_id": transaction.tenant_id, "order_id": row.order_id},
            )
        ).one()
        if fulfillment.state != "confirmed" or fulfillment.allocation_state != "confirmed":
            raise CourierConflict("The confirmed delivery is no longer available for pickup")
        changed = await transaction.session.scalar(
            text(
                "UPDATE assets SET state = 'rented', updated_at = now() "
                "WHERE tenant_id = :tenant_id AND id = :asset_id AND state = 'available' "
                "RETURNING id"
            ),
            {"tenant_id": transaction.tenant_id, "asset_id": fulfillment.asset_id},
        )
        if changed is None:
            raise CourierConflict("The assigned equipment is unavailable for pickup")
        await transaction.session.execute(
            text(
                "UPDATE orders SET state = 'in_progress', handed_over_at = now(), "
                "handed_over_by_staff_user_id = :user_id, updated_at = now() "
                "WHERE tenant_id = :tenant_id AND id = :order_id"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "order_id": row.order_id,
                "user_id": transaction.user_id,
            },
        )
        await plan_return_notifications(transaction, row.order_id)

    target_state = {"accept": "accepted", "pickup": "picked_up", "deliver": "delivered"}[action]
    timestamp_column = {
        "accept": "accepted_at",
        "pickup": "picked_up_at",
        "deliver": "delivered_at",
    }[action]
    await transaction.session.execute(
        text(
            f"UPDATE courier_tasks SET state = :state, {timestamp_column} = now(), "  # noqa: S608
            "updated_at = now() WHERE tenant_id = :tenant_id AND id = :task_id"
        ),
        {"state": target_state, "tenant_id": transaction.tenant_id, "task_id": task_id},
    )
    await _record_event(
        transaction,
        order_id=row.order_id,
        event_type=event_type,
        payload={"task_id": str(task_id), "from": expected_state, "to": target_state},
        key_hash=key_hash,
    )
    return CourierTaskResult(task=_task(await _load_own_task(transaction, task_id)), replayed=False)


async def access_courier_contact(
    transaction: TenantTransaction,
    task_id: UUID,
    idempotency_key: str,
    cipher: ContactCipher,
) -> CourierContactResult:
    require_courier(transaction)
    key_hash = capability_digest(idempotency_key)
    await _lock_idempotency(transaction, key_hash)
    previous = await _previous_event(transaction, key_hash)
    replayed = previous is not None
    if previous is not None:
        payload = previous.payload if isinstance(previous.payload, dict) else {}
        if previous.event_type != "courier_contact_accessed" or payload.get("task_id") != str(
            task_id
        ):
            raise CourierConflict("Idempotency key was already used")
    task = await _load_own_task(transaction, task_id)
    if task.state not in {"accepted", "picked_up"}:
        raise CourierContactUnavailable
    row = (
        await transaction.session.execute(
            text(
                "SELECT op.ciphertext, op.nonce, op.key_version "
                "FROM orders o LEFT JOIN order_private_details op "
                "ON op.tenant_id = o.tenant_id AND op.order_id = o.id "
                "WHERE o.tenant_id = :tenant_id AND o.id = :order_id "
                "AND o.fulfillment = 'delivery' AND o.state IN ('confirmed', 'in_progress')"
            ),
            {"tenant_id": transaction.tenant_id, "order_id": task.order_id},
        )
    ).one_or_none()
    if row is None or row.ciphertext is None or row.nonce is None:
        raise CourierContactUnavailable
    try:
        payload = cipher.decrypt(
            transaction.tenant_id,
            "order",
            task.order_id,
            EncryptedContact(
                nonce=bytes(row.nonce),
                ciphertext=bytes(row.ciphertext),
                key_version=row.key_version,
            ),
        )
        contact = CourierContact.model_validate(payload)
    except Exception as error:
        raise CourierContactUnavailable from error
    if not replayed:
        await _record_event(
            transaction,
            order_id=task.order_id,
            event_type="courier_contact_accessed",
            payload={"task_id": str(task_id), "reason": "assigned_delivery"},
            key_hash=key_hash,
        )
    return CourierContactResult(contact=contact, replayed=replayed)
