"""Customer service requests and fail-closed notification planning."""

import hashlib
import json
from datetime import date, datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.capabilities import capability_digest
from app.customer_auth import invalid_customer_session, resolve_customer_identity
from app.database import (
    Database,
    PublicStorefrontNotFound,
    PublicTenantTransaction,
    TenantTransaction,
)


class ExtensionRequestCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_end_date: date


class ReturnRequestCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_date: date


class ExtensionRequestView(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    state: Literal["pending", "approved", "rejected"]
    original_end_date: date
    requested_end_date: date
    additional_days: int
    amount_minor: int
    currency: Literal["RUB"] = "RUB"
    requested_at: datetime
    decided_at: datetime | None
    rejection_reason: Literal["availability_conflict", "manual_review"] | None


class ReturnRequestView(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: UUID
    state: Literal["pending", "acknowledged", "rejected"]
    requested_date: date
    requested_at: datetime
    decided_at: datetime | None
    rejection_reason: Literal["date_unavailable", "manual_review"] | None


class ExtensionRequestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    request: ExtensionRequestView
    replayed: bool


class ReturnRequestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    request: ReturnRequestView
    replayed: bool


def extension_request_from_mapping(mapping: object) -> ExtensionRequestView:
    data = mapping if isinstance(mapping, dict) else mapping._mapping  # type: ignore[attr-defined]
    return ExtensionRequestView(
        request_id=data["id"],
        state=data["state"],
        original_end_date=data["original_end_date"],
        requested_end_date=data["requested_end_date"],
        additional_days=data["additional_days"],
        amount_minor=data["amount_minor"],
        currency=data["currency"],
        requested_at=data["requested_at"],
        decided_at=data["decided_at"],
        rejection_reason=data["rejection_reason"],
    )


def return_request_from_mapping(mapping: object) -> ReturnRequestView:
    data = mapping if isinstance(mapping, dict) else mapping._mapping  # type: ignore[attr-defined]
    return ReturnRequestView(
        request_id=data["id"],
        state=data["state"],
        requested_date=data["requested_date"],
        requested_at=data["requested_at"],
        decided_at=data["decided_at"],
        rejection_reason=data["rejection_reason"],
    )


def _fingerprint(kind: str, order_id: UUID, requested_date: date) -> bytes:
    payload = json.dumps(
        {"kind": kind, "order_id": str(order_id), "requested_date": requested_date.isoformat()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).digest()


async def _lock_idempotency(
    transaction: PublicTenantTransaction | TenantTransaction, key_hash: bytes
) -> None:
    lock_key = int.from_bytes(
        hashlib.sha256(transaction.tenant_id.bytes + key_hash).digest()[:8],
        "big",
        signed=True,
    )
    await transaction.session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
    )


async def _record_customer_event(
    transaction: PublicTenantTransaction,
    order_id: UUID,
    customer_id: UUID,
    event_type: str,
    payload: dict[str, str | int],
    key_hash: bytes,
) -> None:
    await transaction.session.execute(
        text(
            "INSERT INTO order_events "
            "(id, tenant_id, order_id, event_type, payload, idempotency_key_hash, "
            "actor_customer_id) VALUES "
            "(:id, :tenant_id, :order_id, :event_type, CAST(:payload AS jsonb), :key_hash, "
            ":customer_id)"
        ),
        {
            "id": uuid4(),
            "tenant_id": transaction.tenant_id,
            "order_id": order_id,
            "event_type": event_type,
            "payload": json.dumps(payload),
            "key_hash": key_hash,
            "customer_id": customer_id,
        },
    )


async def _queue_blocked_notification(
    transaction: PublicTenantTransaction | TenantTransaction,
    *,
    order_id: UUID,
    customer_id: UUID,
    template: str,
    dedupe_key: str,
) -> None:
    await transaction.session.execute(
        text(
            "INSERT INTO notification_jobs "
            "(id, tenant_id, order_id, customer_id, channel, template, send_after, state, "
            "blocked_reason, dedupe_key) SELECT :id, :tenant_id, :order_id, :customer_id, "
            "'sms', :template, now(), 'blocked', 'provider_disabled', :dedupe_key "
            "ON CONFLICT (tenant_id, dedupe_key) DO NOTHING"
        ),
        {
            "id": uuid4(),
            "tenant_id": transaction.tenant_id,
            "order_id": order_id,
            "customer_id": customer_id,
            "template": template,
            "dedupe_key": dedupe_key,
        },
    )


async def plan_return_notifications(
    transaction: PublicTenantTransaction | TenantTransaction, order_id: UUID
) -> None:
    """Plan one due and one overdue reminder without pretending SMS can be delivered."""

    order = (
        await transaction.session.execute(
            text(
                "SELECT o.customer_id, o.end_date, b.timezone FROM orders o "
                "JOIN branches b ON b.tenant_id = o.tenant_id AND b.id = o.branch_id "
                "WHERE o.tenant_id = :tenant_id AND o.id = :order_id"
            ),
            {"tenant_id": transaction.tenant_id, "order_id": order_id},
        )
    ).one()
    if order.customer_id is None:
        return
    await transaction.session.execute(
        text(
            "INSERT INTO notification_jobs "
            "(id, tenant_id, order_id, customer_id, channel, template, send_after, state, "
            "blocked_reason, dedupe_key) VALUES (:id, :tenant_id, :order_id, :customer_id, "
            "'sms', 'return_due_24h', "
            "((CAST(:end_date AS date) - 1) + time '12:00') AT TIME ZONE :timezone, "
            "'blocked', 'provider_disabled', :dedupe_key) "
            "ON CONFLICT (tenant_id, dedupe_key) DO NOTHING"
        ),
        {
            "id": uuid4(),
            "tenant_id": transaction.tenant_id,
            "order_id": order_id,
            "customer_id": order.customer_id,
            "end_date": order.end_date,
            "timezone": order.timezone,
            "dedupe_key": f"order:{order_id}:return_due_24h:{order.end_date.isoformat()}",
        },
    )
    await transaction.session.execute(
        text(
            "INSERT INTO notification_jobs "
            "(id, tenant_id, order_id, customer_id, channel, template, send_after, state, "
            "blocked_reason, dedupe_key) VALUES (:id, :tenant_id, :order_id, :customer_id, "
            "'sms', 'return_overdue', "
            "((CAST(:end_date AS date) + 1) + time '09:00') AT TIME ZONE :timezone, "
            "'blocked', 'provider_disabled', :dedupe_key) "
            "ON CONFLICT (tenant_id, dedupe_key) DO NOTHING"
        ),
        {
            "id": uuid4(),
            "tenant_id": transaction.tenant_id,
            "order_id": order_id,
            "customer_id": order.customer_id,
            "end_date": order.end_date,
            "timezone": order.timezone,
            "dedupe_key": f"order:{order_id}:return_overdue:{order.end_date.isoformat()}",
        },
    )


async def cancel_planned_return_notifications(
    transaction: TenantTransaction, order_id: UUID
) -> None:
    await transaction.session.execute(
        text(
            "UPDATE notification_jobs SET state = 'cancelled', blocked_reason = NULL, "
            "updated_at = now() WHERE tenant_id = :tenant_id AND order_id = :order_id "
            "AND template IN ('return_due_24h', 'return_overdue') AND state = 'blocked'"
        ),
        {"tenant_id": transaction.tenant_id, "order_id": order_id},
    )


async def queue_decision_notification(
    transaction: TenantTransaction,
    *,
    order_id: UUID,
    customer_id: UUID,
    template: str,
    request_id: UUID,
) -> None:
    await _queue_blocked_notification(
        transaction,
        order_id=order_id,
        customer_id=customer_id,
        template=template,
        dedupe_key=f"request:{request_id}:{template}",
    )


async def _create_extension_request(
    transaction: PublicTenantTransaction,
    *,
    customer_id: UUID,
    order_id: UUID,
    command: ExtensionRequestCommand,
    key_hash: bytes,
) -> ExtensionRequestResult:
    fingerprint = _fingerprint("extension", order_id, command.requested_end_date)
    await _lock_idempotency(transaction, key_hash)
    existing = (
        await transaction.session.execute(
            text(
                "SELECT * FROM order_extension_requests WHERE tenant_id = :tenant_id "
                "AND idempotency_key_hash = :key_hash"
            ),
            {"tenant_id": transaction.tenant_id, "key_hash": key_hash},
        )
    ).one_or_none()
    if existing is not None:
        if existing.order_id != order_id or existing.command_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="Idempotency key was already used")
        return ExtensionRequestResult(
            request=extension_request_from_mapping(existing), replayed=True
        )
    order = (
        await transaction.session.execute(
            text(
                "SELECT o.id, o.state, o.start_date, o.end_date, o.quote_snapshot "
                "FROM orders o WHERE o.tenant_id = :tenant_id AND o.id = :order_id "
                "AND o.customer_id = :customer_id FOR UPDATE"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "order_id": order_id,
                "customer_id": customer_id,
            },
        )
    ).one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Customer order not found")
    if order.state != "in_progress":
        raise HTTPException(status_code=409, detail="Only an active rental can be extended")
    if command.requested_end_date <= order.end_date:
        raise HTTPException(status_code=422, detail="requested_end_date must be after current end")
    total_days = (command.requested_end_date - order.start_date).days + 1
    if total_days > 365:
        raise HTTPException(status_code=422, detail="Rental period cannot exceed 365 days")
    additional_days = (command.requested_end_date - order.end_date).days
    rate = int(order.quote_snapshot["amount_minor_per_day"])
    request_id = uuid4()
    created = (
        await transaction.session.execute(
            text(
                "INSERT INTO order_extension_requests "
                "(id, tenant_id, order_id, customer_id, original_end_date, requested_end_date, "
                "additional_days, amount_minor, currency, idempotency_key_hash, "
                "command_fingerprint) VALUES (:id, :tenant_id, :order_id, :customer_id, "
                ":original_end_date, :requested_end_date, :additional_days, :amount_minor, "
                "'RUB', :key_hash, :fingerprint) RETURNING *"
            ),
            {
                "id": request_id,
                "tenant_id": transaction.tenant_id,
                "order_id": order_id,
                "customer_id": customer_id,
                "original_end_date": order.end_date,
                "requested_end_date": command.requested_end_date,
                "additional_days": additional_days,
                "amount_minor": rate * additional_days,
                "key_hash": key_hash,
                "fingerprint": fingerprint,
            },
        )
    ).one()
    await _record_customer_event(
        transaction,
        order_id,
        customer_id,
        "order_extension_requested",
        {
            "request_id": str(request_id),
            "original_end_date": order.end_date.isoformat(),
            "requested_end_date": command.requested_end_date.isoformat(),
            "amount_minor": rate * additional_days,
        },
        key_hash,
    )
    return ExtensionRequestResult(request=extension_request_from_mapping(created), replayed=False)


async def create_extension_request(
    database: Database,
    slug: str,
    order_id: UUID,
    command: ExtensionRequestCommand,
    idempotency_key: str,
    token_hash: bytes,
    *,
    allow_demo: bool,
) -> ExtensionRequestResult:
    try:
        async with database.public_tenant_transaction(slug, allow_demo=allow_demo) as transaction:
            identity = await resolve_customer_identity(transaction, token_hash)
            if identity is None:
                raise invalid_customer_session()
            return await _create_extension_request(
                transaction,
                customer_id=identity.customer_id,
                order_id=order_id,
                command=command,
                key_hash=capability_digest(idempotency_key),
            )
    except PublicStorefrontNotFound:
        raise HTTPException(status_code=404, detail="Storefront not found") from None
    except IntegrityError as error:
        if "ux_order_extension_requests_pending_order" in str(error.orig):
            raise HTTPException(
                status_code=409, detail="This order already has a pending extension request"
            ) from None
        raise


async def _create_return_request(
    transaction: PublicTenantTransaction,
    *,
    customer_id: UUID,
    order_id: UUID,
    command: ReturnRequestCommand,
    key_hash: bytes,
) -> ReturnRequestResult:
    fingerprint = _fingerprint("return", order_id, command.requested_date)
    await _lock_idempotency(transaction, key_hash)
    existing = (
        await transaction.session.execute(
            text(
                "SELECT * FROM order_return_requests WHERE tenant_id = :tenant_id "
                "AND idempotency_key_hash = :key_hash"
            ),
            {"tenant_id": transaction.tenant_id, "key_hash": key_hash},
        )
    ).one_or_none()
    if existing is not None:
        if existing.order_id != order_id or existing.command_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="Idempotency key was already used")
        return ReturnRequestResult(request=return_request_from_mapping(existing), replayed=True)
    order = (
        await transaction.session.execute(
            text(
                "SELECT o.id, o.state, o.customer_id, "
                "(now() AT TIME ZONE b.timezone)::date AS local_date "
                "FROM orders o JOIN branches b ON b.tenant_id = o.tenant_id "
                "AND b.id = o.branch_id WHERE o.tenant_id = :tenant_id "
                "AND o.id = :order_id AND o.customer_id = :customer_id FOR UPDATE OF o"
            ),
            {
                "tenant_id": transaction.tenant_id,
                "order_id": order_id,
                "customer_id": customer_id,
            },
        )
    ).one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Customer order not found")
    if order.state != "in_progress":
        raise HTTPException(status_code=409, detail="Only an active rental can request return")
    if command.requested_date < order.local_date:
        raise HTTPException(status_code=422, detail="requested_date must not be in the past")
    request_id = uuid4()
    created = (
        await transaction.session.execute(
            text(
                "INSERT INTO order_return_requests "
                "(id, tenant_id, order_id, customer_id, requested_date, idempotency_key_hash, "
                "command_fingerprint) VALUES (:id, :tenant_id, :order_id, :customer_id, "
                ":requested_date, :key_hash, :fingerprint) RETURNING *"
            ),
            {
                "id": request_id,
                "tenant_id": transaction.tenant_id,
                "order_id": order_id,
                "customer_id": customer_id,
                "requested_date": command.requested_date,
                "key_hash": key_hash,
                "fingerprint": fingerprint,
            },
        )
    ).one()
    await _record_customer_event(
        transaction,
        order_id,
        customer_id,
        "order_return_requested",
        {"request_id": str(request_id), "requested_date": command.requested_date.isoformat()},
        key_hash,
    )
    return ReturnRequestResult(request=return_request_from_mapping(created), replayed=False)


async def create_return_request(
    database: Database,
    slug: str,
    order_id: UUID,
    command: ReturnRequestCommand,
    idempotency_key: str,
    token_hash: bytes,
    *,
    allow_demo: bool,
) -> ReturnRequestResult:
    try:
        async with database.public_tenant_transaction(slug, allow_demo=allow_demo) as transaction:
            identity = await resolve_customer_identity(transaction, token_hash)
            if identity is None:
                raise invalid_customer_session()
            return await _create_return_request(
                transaction,
                customer_id=identity.customer_id,
                order_id=order_id,
                command=command,
                key_hash=capability_digest(idempotency_key),
            )
    except PublicStorefrontNotFound:
        raise HTTPException(status_code=404, detail="Storefront not found") from None
    except IntegrityError as error:
        if "ux_order_return_requests_pending_order" in str(error.orig):
            raise HTTPException(
                status_code=409, detail="This order already has a pending return request"
            ) from None
        raise
