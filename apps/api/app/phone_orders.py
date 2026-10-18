"""Atomic staff-created phone orders with encrypted private details."""

import hashlib
import hmac
import json
import secrets
from datetime import date
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from app.admin_orders import (
    OrderPermissionDenied,
    OrderTransitionConflict,
    OrderTransitionResult,
    load_staff_order,
    require_order_operator,
)
from app.availability import Fulfillment, QuoteRequest, load_quote_context, quote_from_context
from app.capabilities import capability_digest
from app.contact_crypto import ContactCipher
from app.customer_auth import phone_lookup_digest
from app.database import TenantTransaction
from app.holds import expire_holds
from app.orders import OrderSnapshot

DEMO_PHONE = "+70000000000"
DEMO_RECIPIENT = "Тестовый клиент"
DEMO_ADDRESS = "Тестовый адрес"


class PhoneOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_e164: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    recipient_name: str = Field(min_length=1, max_length=120)
    delivery_address: str | None = Field(default=None, max_length=500)
    model_id: UUID
    branch_id: UUID
    start_date: date
    end_date: date
    fulfillment: Fulfillment

    @model_validator(mode="after")
    def validate_delivery_address(self) -> "PhoneOrderRequest":
        self.recipient_name = self.recipient_name.strip()
        if not self.recipient_name:
            raise ValueError("recipient_name must not be blank")
        if self.delivery_address is not None:
            self.delivery_address = self.delivery_address.strip() or None
        if self.fulfillment == "delivery" and self.delivery_address is None:
            raise ValueError("delivery_address is required for delivery")
        if self.fulfillment == "pickup" and self.delivery_address is not None:
            raise ValueError("delivery_address must be empty for pickup")
        return self

    def quote_request(self) -> QuoteRequest:
        return QuoteRequest(
            model_id=self.model_id,
            branch_id=self.branch_id,
            start_date=self.start_date,
            end_date=self.end_date,
            fulfillment=self.fulfillment,
        )


def _command_fingerprint(command: PhoneOrderRequest, pepper: str) -> bytes:
    canonical = json.dumps(
        command.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hmac.new(pepper.encode(), canonical, hashlib.sha256).digest()


def _validate_demo_contact(command: PhoneOrderRequest) -> None:
    expected_address = DEMO_ADDRESS if command.fulfillment == "delivery" else None
    if (
        command.phone_e164 != DEMO_PHONE
        or command.recipient_name != DEMO_RECIPIENT
        or command.delivery_address != expected_address
    ):
        raise HTTPException(
            status_code=422,
            detail="Demo phone orders accept only the displayed synthetic contact",
        )


async def create_phone_order(
    transaction: TenantTransaction,
    command: PhoneOrderRequest,
    idempotency_key: str,
    *,
    pepper: str,
    cipher: ContactCipher,
) -> OrderTransitionResult:
    try:
        require_order_operator(transaction)
    except OrderPermissionDenied:
        raise

    key_hash = capability_digest(idempotency_key)
    fingerprint = _command_fingerprint(command, pepper)
    lock_key = int.from_bytes(
        hashlib.sha256(transaction.tenant_id.bytes + key_hash).digest()[:8],
        "big",
        signed=True,
    )
    await transaction.session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
    )
    await expire_holds(transaction)

    existing = (
        await transaction.session.execute(
            text(
                "SELECT id, source, command_fingerprint FROM orders "
                "WHERE tenant_id = :tenant_id AND idempotency_key_hash = :key_hash FOR UPDATE"
            ),
            {"tenant_id": transaction.tenant_id, "key_hash": key_hash},
        )
    ).one_or_none()
    if existing is not None:
        if existing.source != "phone" or not secrets.compare_digest(
            existing.command_fingerprint or b"", fingerprint
        ):
            raise OrderTransitionConflict("Idempotency key was already used")
        return OrderTransitionResult(
            order=await load_staff_order(transaction, existing.id), replayed=True
        )

    is_demo = bool(
        await transaction.session.scalar(
            text("SELECT is_demo FROM storefront_profiles WHERE tenant_id = :tenant_id"),
            {"tenant_id": transaction.tenant_id},
        )
    )
    if is_demo:
        _validate_demo_contact(command)

    quote_request = command.quote_request()
    context, days = await load_quote_context(transaction, quote_request)
    quote = quote_from_context(quote_request, context, days)
    asset = (
        await transaction.session.execute(
            text(
                "SELECT a.id, em.name AS model_name, b.name AS branch_name FROM assets a "
                "JOIN equipment_models em ON em.tenant_id = a.tenant_id "
                "AND em.id = a.equipment_model_id "
                "JOIN branches b ON b.tenant_id = a.tenant_id AND b.id = a.branch_id "
                "WHERE a.tenant_id = :tenant_id AND a.equipment_model_id = :model_id "
                "AND a.branch_id = :branch_id AND a.state = 'available' "
                "AND NOT EXISTS (SELECT 1 FROM asset_blocks ab "
                "  WHERE ab.tenant_id = a.tenant_id AND ab.asset_id = a.id "
                "  AND ab.start_date <= :end_date AND ab.end_date >= :start_date) "
                "AND NOT EXISTS (SELECT 1 FROM rental_allocations ra "
                "  WHERE ra.tenant_id = a.tenant_id AND ra.asset_id = a.id "
                "  AND ra.start_date <= :end_date AND ra.end_date >= :start_date "
                "  AND (ra.state = 'confirmed' OR "
                "    (ra.state = 'held' AND ra.expires_at > now()))) "
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
    ).one_or_none()
    if asset is None:
        raise HTTPException(
            status_code=409, detail={"code": "NO_UNITS_AVAILABLE", "field": "dates"}
        )

    customer_id = await transaction.session.scalar(
        text(
            "INSERT INTO customers (id, tenant_id, phone_lookup_hash, phone_last4) "
            "VALUES (:id, :tenant_id, :phone_hash, :phone_last4) "
            "ON CONFLICT (tenant_id, phone_lookup_hash) DO UPDATE "
            "SET phone_last4 = EXCLUDED.phone_last4, updated_at = now() RETURNING id"
        ),
        {
            "id": uuid4(),
            "tenant_id": transaction.tenant_id,
            "phone_hash": phone_lookup_digest(transaction.tenant_id, command.phone_e164, pepper),
            "phone_last4": command.phone_e164[-4:],
        },
    )
    customer_id = UUID(str(customer_id))
    encrypted_customer = cipher.encrypt(
        transaction.tenant_id,
        "customer",
        customer_id,
        {"phone_e164": command.phone_e164, "recipient_name": command.recipient_name},
    )
    await transaction.session.execute(
        text(
            "INSERT INTO customer_private_profiles "
            "(tenant_id, customer_id, ciphertext, nonce, key_version) VALUES "
            "(:tenant_id, :customer_id, :ciphertext, :nonce, :key_version) "
            "ON CONFLICT (tenant_id, customer_id) DO UPDATE SET "
            "ciphertext = EXCLUDED.ciphertext, nonce = EXCLUDED.nonce, "
            "key_version = EXCLUDED.key_version, updated_at = now()"
        ),
        {
            "tenant_id": transaction.tenant_id,
            "customer_id": customer_id,
            "ciphertext": encrypted_customer.ciphertext,
            "nonce": encrypted_customer.nonce,
            "key_version": encrypted_customer.key_version,
        },
    )

    allocation_id = uuid4()
    order_id = uuid4()
    order_snapshot = OrderSnapshot(
        model_id=command.model_id,
        model_name=asset.model_name,
        branch_id=command.branch_id,
        branch_name=asset.branch_name,
        start_date=command.start_date,
        end_date=command.end_date,
        fulfillment=command.fulfillment,
        quote=quote,
    )
    await transaction.session.execute(
        text(
            "INSERT INTO rental_allocations "
            "(id, tenant_id, asset_id, equipment_model_id, branch_id, start_date, end_date, "
            "fulfillment, state, expires_at, idempotency_key_hash, management_token_hash, "
            "quote_snapshot, order_id) VALUES "
            "(:id, :tenant_id, :asset_id, :model_id, :branch_id, :start_date, :end_date, "
            ":fulfillment, 'confirmed', NULL, :key_hash, :management_hash, "
            "CAST(:quote_snapshot AS jsonb), NULL)"
        ),
        {
            "id": allocation_id,
            "tenant_id": transaction.tenant_id,
            "asset_id": asset.id,
            "model_id": command.model_id,
            "branch_id": command.branch_id,
            "start_date": command.start_date,
            "end_date": command.end_date,
            "fulfillment": command.fulfillment,
            "key_hash": key_hash,
            "management_hash": secrets.token_bytes(32),
            "quote_snapshot": json.dumps(quote.model_dump(mode="json")),
        },
    )
    await transaction.session.execute(
        text(
            "INSERT INTO orders "
            "(id, tenant_id, branch_id, checkout_session_id, allocation_id, source, state, "
            "start_date, end_date, fulfillment, expires_at, quote_snapshot, "
            "idempotency_key_hash, customer_id, order_snapshot, confirmed_at, "
            "confirmed_by_staff_user_id, created_by_staff_user_id, command_fingerprint) VALUES "
            "(:id, :tenant_id, :branch_id, NULL, :allocation_id, 'phone', 'confirmed', "
            ":start_date, :end_date, :fulfillment, NULL, CAST(:quote_snapshot AS jsonb), "
            ":key_hash, :customer_id, CAST(:order_snapshot AS jsonb), now(), :user_id, "
            ":user_id, :fingerprint)"
        ),
        {
            "id": order_id,
            "tenant_id": transaction.tenant_id,
            "branch_id": command.branch_id,
            "allocation_id": allocation_id,
            "start_date": command.start_date,
            "end_date": command.end_date,
            "fulfillment": command.fulfillment,
            "quote_snapshot": json.dumps(quote.model_dump(mode="json")),
            "key_hash": key_hash,
            "customer_id": customer_id,
            "order_snapshot": json.dumps(order_snapshot.model_dump(mode="json")),
            "user_id": transaction.user_id,
            "fingerprint": fingerprint,
        },
    )
    await transaction.session.execute(
        text(
            "UPDATE rental_allocations SET order_id = :order_id, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :allocation_id"
        ),
        {
            "tenant_id": transaction.tenant_id,
            "allocation_id": allocation_id,
            "order_id": order_id,
        },
    )
    encrypted_order = cipher.encrypt(
        transaction.tenant_id,
        "order",
        order_id,
        {
            "phone_e164": command.phone_e164,
            "recipient_name": command.recipient_name,
            "delivery_address": command.delivery_address,
        },
    )
    await transaction.session.execute(
        text(
            "INSERT INTO order_private_details "
            "(tenant_id, order_id, ciphertext, nonce, key_version) VALUES "
            "(:tenant_id, :order_id, :ciphertext, :nonce, :key_version)"
        ),
        {
            "tenant_id": transaction.tenant_id,
            "order_id": order_id,
            "ciphertext": encrypted_order.ciphertext,
            "nonce": encrypted_order.nonce,
            "key_version": encrypted_order.key_version,
        },
    )
    await transaction.session.execute(
        text(
            "INSERT INTO order_events "
            "(id, tenant_id, order_id, event_type, payload, idempotency_key_hash, "
            "actor_staff_user_id) VALUES "
            "(:id, :tenant_id, :order_id, 'phone_order_created', "
            '\'{"state":"confirmed"}\', :key_hash, :user_id)'
        ),
        {
            "id": uuid4(),
            "tenant_id": transaction.tenant_id,
            "order_id": order_id,
            "key_hash": key_hash,
            "user_id": transaction.user_id,
        },
    )
    return OrderTransitionResult(
        order=await load_staff_order(transaction, order_id), replayed=False
    )
