import os
from datetime import date, timedelta
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.bootstrap_runtime_role import RUNTIME_ROLE
from app.config import Settings
from app.contact_crypto import EncryptedContact
from app.main import create_app
from app.sms import MemorySmsProvider

TENANT_UFA = UUID("d0000000-0000-0000-0000-000000000001")
TENANT_KAZAN = UUID("d0000000-0000-0000-0000-000000000002")
BRANCH_UFA = UUID("d1000000-0000-0000-0000-000000000001")
MODEL_UFA = UUID("d2000000-0000-0000-0000-000000000001")
SYNTHETIC_PHONE = "+70000000000"


def database_urls() -> tuple[str, str]:
    defaults = Settings()
    configured = make_url(os.getenv("DATABASE_URL", defaults.database_url.get_secret_value()))
    runtime = configured.set(
        username=RUNTIME_ROLE,
        password="local-runtime-only",  # noqa: S106 - synthetic test credential
    ).render_as_string(hide_password=False)
    return runtime, os.getenv("DATABASE_OWNER_URL", defaults.migration_database_url())


async def clear_data(owner_url: str) -> None:
    owner = create_async_engine(owner_url)
    try:
        async with owner.begin() as connection:
            await connection.execute(
                text("UPDATE checkout_sessions SET customer_id = NULL WHERE tenant_id = :tenant"),
                {"tenant": TENANT_UFA},
            )
            await connection.execute(
                text("UPDATE rental_allocations SET order_id = NULL WHERE tenant_id = :tenant"),
                {"tenant": TENANT_UFA},
            )
            for table in (
                "customer_auth_challenges",
                "customer_sessions",
                "order_events",
                "orders",
                "rental_allocations",
                "checkout_sessions",
                "customers",
            ):
                await connection.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id = :tenant"),  # noqa: S608
                    {"tenant": TENANT_UFA},
                )
            await connection.execute(
                text(
                    "UPDATE assets SET state = CASE asset_code "
                    "WHEN 'DEMO-UFA-02' THEN 'inspection' ELSE 'available' END, "
                    "updated_at = now() WHERE tenant_id = :tenant "
                    "AND asset_code IN ('DEMO-UFA-01', 'DEMO-UFA-02', 'DEMO-UFA-03')"
                ),
                {"tenant": TENANT_UFA},
            )
    finally:
        await owner.dispose()


async def clear_rate_limits(redis_url: str) -> None:
    redis = Redis.from_url(redis_url)
    try:
        keys = [key async for key in redis.scan_iter(match="photolamp:rate:v1:*")]
        if keys:
            await redis.delete(*keys)
    finally:
        await redis.aclose()


async def set_public_booking(owner_url: str, enabled: bool) -> None:
    owner = create_async_engine(owner_url)
    try:
        async with owner.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE storefront_profiles SET booking_enabled = :enabled "
                    "WHERE tenant_id = :tenant"
                ),
                {"enabled": enabled, "tenant": TENANT_UFA},
            )
    finally:
        await owner.dispose()


@pytest.mark.integration
async def test_phone_order_fulfillment_is_encrypted_audited_and_releases_inventory() -> None:
    runtime_url, owner_url = database_urls()
    owner = create_async_engine(owner_url)
    runtime = create_async_engine(runtime_url)
    settings = Settings(
        app_env="test",
        database_url=runtime_url,
        enable_demo_previews=True,
        sms_provider_mode="memory",
        otp_request_rate_limit=10,
        otp_verify_rate_limit=20,
    )
    app = create_app(settings)
    start = date.today() + timedelta(days=90)
    command = {
        "phone_e164": SYNTHETIC_PHONE,
        "recipient_name": "Тестовый клиент",
        "delivery_address": None,
        "model_id": str(MODEL_UFA),
        "branch_id": str(BRANCH_UFA),
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=2)).isoformat(),
        "fulfillment": "pickup",
    }
    try:
        await clear_data(owner_url)
        await clear_rate_limits(settings.redis_url.get_secret_value())
        await set_public_booking(owner_url, True)
        async with app.router.lifespan_context(app):
            provider = app.state.sms_provider
            assert isinstance(provider, MemorySmsProvider)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                login = await client.post("/api/v1/auth/demo-session")
                headers = {
                    "Authorization": f"Bearer {login.json()['access_token']}",
                    "Idempotency-Key": "synthetic-phone-order-0001",
                }
                invalid = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/phone",
                    headers={**headers, "Idempotency-Key": "synthetic-phone-invalid-0001"},
                    json={**command, "phone_e164": "+79991234567"},
                )
                assert invalid.status_code == 422

                created = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/phone", headers=headers, json=command
                )
                assert created.status_code == 201
                result = created.json()
                order = result["order"]
                order_id = UUID(order["order_id"])
                assert result["replayed"] is False
                assert order["source"] == "phone"
                assert order["state"] == "confirmed"
                assert order["identity_verified"] is False
                assert order["expires_at"] is None
                assert SYNTHETIC_PHONE not in created.text
                assert "Тестовый клиент" not in created.text

                replay = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/phone", headers=headers, json=command
                )
                assert replay.status_code == 201
                assert replay.json()["replayed"] is True
                assert replay.json()["order"]["order_id"] == str(order_id)
                conflict = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/phone",
                    headers=headers,
                    json={**command, "end_date": (start + timedelta(days=3)).isoformat()},
                )
                assert conflict.status_code == 409

                overlap = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/phone",
                    headers={**headers, "Idempotency-Key": "synthetic-phone-order-0002"},
                    json=command,
                )
                assert overlap.status_code == 409
                cross_tenant = await client.get(
                    f"/api/v1/t/{TENANT_KAZAN}/orders/{order_id}", headers=headers
                )
                assert cross_tenant.status_code == 404

                async with owner.begin() as connection:
                    stored = (
                        await connection.execute(
                            text(
                                "SELECT o.customer_id, o.checkout_session_id, o.source, "
                                "ra.state AS allocation_state, ra.asset_id, c.phone_verified_at, "
                                "cp.ciphertext AS customer_ciphertext, cp.nonce AS customer_nonce, "
                                "op.ciphertext AS order_ciphertext, op.nonce AS order_nonce "
                                "FROM orders o JOIN rental_allocations ra "
                                "ON ra.id = o.allocation_id "
                                "JOIN customers c ON c.id = o.customer_id "
                                "JOIN customer_private_profiles cp ON cp.customer_id = c.id "
                                "AND cp.tenant_id = c.tenant_id "
                                "JOIN order_private_details op ON op.order_id = o.id "
                                "AND op.tenant_id = o.tenant_id WHERE o.id = :order_id"
                            ),
                            {"order_id": order_id},
                        )
                    ).one()
                    event = (
                        await connection.execute(
                            text(
                                "SELECT event_type, actor_staff_user_id FROM order_events "
                                "WHERE order_id = :order_id"
                            ),
                            {"order_id": order_id},
                        )
                    ).one()
                assert stored.checkout_session_id is None
                assert stored.source == "phone"
                assert stored.allocation_state == "confirmed"
                assert stored.phone_verified_at is None
                assert SYNTHETIC_PHONE.encode() not in bytes(stored.customer_ciphertext)
                assert "Тестовый клиент".encode() not in bytes(stored.order_ciphertext)
                customer_payload = app.state.contact_cipher.decrypt(
                    TENANT_UFA,
                    "customer",
                    stored.customer_id,
                    EncryptedContact(stored.customer_nonce, stored.customer_ciphertext),
                )
                order_payload = app.state.contact_cipher.decrypt(
                    TENANT_UFA,
                    "order",
                    order_id,
                    EncryptedContact(stored.order_nonce, stored.order_ciphertext),
                )
                assert customer_payload == {
                    "phone_e164": SYNTHETIC_PHONE,
                    "recipient_name": "Тестовый клиент",
                }
                assert order_payload["delivery_address"] is None
                assert event.event_type == "phone_order_created"
                assert event.actor_staff_user_id is not None

                contact_headers = {
                    **headers,
                    "Idempotency-Key": "synthetic-contact-access-0001",
                }
                cross_tenant_contact = await client.post(
                    f"/api/v1/t/{TENANT_KAZAN}/orders/{order_id}/contact-access",
                    headers=contact_headers,
                )
                assert cross_tenant_contact.status_code == 404
                contact = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/contact-access",
                    headers=contact_headers,
                )
                assert contact.status_code == 200
                assert contact.headers["cache-control"] == "no-store"
                assert contact.json() == {
                    "contact": {
                        "phone_e164": SYNTHETIC_PHONE,
                        "recipient_name": "Тестовый клиент",
                        "delivery_address": None,
                    },
                    "replayed": False,
                }
                contact_replay = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/contact-access",
                    headers=contact_headers,
                )
                assert contact_replay.status_code == 200
                assert contact_replay.json()["replayed"] is True

                reused_contact_key = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/handover",
                    headers=contact_headers,
                )
                assert reused_contact_key.status_code == 409
                handover_headers = {
                    **headers,
                    "Idempotency-Key": "synthetic-order-handover-0001",
                }
                handed_over = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/handover",
                    headers=handover_headers,
                )
                assert handed_over.status_code == 200
                assert handed_over.json()["order"]["state"] == "in_progress"
                assert handed_over.json()["order"]["handed_over_at"] is not None
                bypass = await client.patch(
                    f"/api/v1/t/{TENANT_UFA}/assets/{stored.asset_id}",
                    headers=headers,
                    json={"state": "available"},
                )
                assert bypass.status_code == 422
                assert bypass.json()["detail"] == (
                    "Asset state and assignment are controlled by its active order"
                )
                handover_replay = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/handover",
                    headers=handover_headers,
                )
                assert handover_replay.status_code == 200
                assert handover_replay.json()["replayed"] is True

                returned = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/return",
                    headers={
                        **headers,
                        "Idempotency-Key": "synthetic-order-return-0001",
                    },
                )
                assert returned.status_code == 200
                assert returned.json()["order"]["state"] == "returned"
                assert returned.json()["order"]["returned_at"] is not None
                inspected = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/inspection",
                    headers={
                        **headers,
                        "Idempotency-Key": "synthetic-order-inspection-0001",
                    },
                    json={"outcome": "passed"},
                )
                assert inspected.status_code == 200
                assert inspected.json()["order"]["state"] == "completed"
                assert inspected.json()["order"]["inspection_outcome"] == "passed"
                contact_after_completion = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/contact-access",
                    headers={
                        **headers,
                        "Idempotency-Key": "synthetic-contact-after-completion-0001",
                    },
                )
                assert contact_after_completion.status_code == 409

                async with owner.begin() as connection:
                    lifecycle = (
                        await connection.execute(
                            text(
                                "SELECT o.state, o.inspection_outcome, "
                                "ra.state AS allocation_state, "
                                "a.state AS asset_state FROM orders o "
                                "JOIN rental_allocations ra ON ra.id = o.allocation_id "
                                "JOIN assets a ON a.id = ra.asset_id WHERE o.id = :order_id"
                            ),
                            {"order_id": order_id},
                        )
                    ).one()
                    event_types: list[str] = list(
                        (
                            await connection.execute(
                                text(
                                    "SELECT event_type FROM order_events "
                                    "WHERE order_id = :order_id "
                                    "ORDER BY happened_at, event_type"
                                ),
                                {"order_id": order_id},
                            )
                        )
                        .scalars()
                        .all()
                    )
                assert lifecycle.state == "completed"
                assert lifecycle.inspection_outcome == "passed"
                assert lifecycle.allocation_state == "released"
                assert lifecycle.asset_state == "available"
                assert event_types.count("contact_accessed") == 1
                assert {
                    "phone_order_created",
                    "order_handed_over",
                    "order_returned",
                    "order_inspection_completed",
                }.issubset(event_types)

                maintenance_created = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/phone",
                    headers={
                        **headers,
                        "Idempotency-Key": "synthetic-phone-order-maintenance-0001",
                    },
                    json=command,
                )
                assert maintenance_created.status_code == 201
                maintenance_order_id = maintenance_created.json()["order"]["order_id"]
                for action, key in (
                    ("handover", "synthetic-maintenance-handover-0001"),
                    ("return", "synthetic-maintenance-return-0001"),
                ):
                    response = await client.post(
                        f"/api/v1/t/{TENANT_UFA}/orders/{maintenance_order_id}/{action}",
                        headers={**headers, "Idempotency-Key": key},
                    )
                    assert response.status_code == 200
                maintenance = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{maintenance_order_id}/inspection",
                    headers={
                        **headers,
                        "Idempotency-Key": "synthetic-maintenance-inspection-0001",
                    },
                    json={"outcome": "maintenance_required"},
                )
                assert maintenance.status_code == 200
                assert maintenance.json()["order"]["inspection_outcome"] == ("maintenance_required")
                async with owner.begin() as connection:
                    maintenance_states = (
                        await connection.execute(
                            text(
                                "SELECT ra.state AS allocation_state, a.state AS asset_state "
                                "FROM orders o JOIN rental_allocations ra "
                                "ON ra.id = o.allocation_id "
                                "JOIN assets a ON a.id = ra.asset_id WHERE o.id = :order_id"
                            ),
                            {"order_id": UUID(maintenance_order_id)},
                        )
                    ).one()
                assert maintenance_states.allocation_state == "released"
                assert maintenance_states.asset_state == "maintenance"

                checkout = await client.post("/api/v1/r/demo-ufa/checkout/sessions")
                requested = await client.post(
                    "/api/v1/r/demo-ufa/checkout/identity/otp/request",
                    json={"phone_e164": SYNTHETIC_PHONE},
                    headers={"X-Checkout-Token": checkout.json()["checkout_token"]},
                )
                challenge_id = UUID(requested.json()["challenge_id"])
                code = provider.code_for(challenge_id)
                assert code is not None
                verified = await client.post(
                    "/api/v1/r/demo-ufa/checkout/identity/otp/verify",
                    json={"challenge_id": str(challenge_id), "code": code},
                    headers={"X-Checkout-Token": checkout.json()["checkout_token"]},
                )
                assert verified.status_code == 200
                customer_orders = await client.get(
                    "/api/v1/r/demo-ufa/me/orders",
                    headers={"Authorization": f"Bearer {verified.json()['access_token']}"},
                )
                assert customer_orders.status_code == 200
                assert customer_orders.json()["total"] == 2
                assert customer_orders.json()["items"][0]["source"] == "phone"
                assert customer_orders.json()["items"][0]["state"] == "completed"

        async with runtime.begin() as connection:
            assert (
                await connection.execute(text("SELECT customer_id FROM customer_private_profiles"))
            ).all() == []
            assert (
                await connection.execute(text("SELECT order_id FROM order_private_details"))
            ).all() == []
    finally:
        await clear_data(owner_url)
        await set_public_booking(owner_url, False)
        await runtime.dispose()
        await owner.dispose()
