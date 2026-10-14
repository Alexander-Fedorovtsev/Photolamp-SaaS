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
from app.main import create_app
from app.sms import MemorySmsProvider

TENANT_UFA = UUID("d0000000-0000-0000-0000-000000000001")
TENANT_KAZAN = UUID("d0000000-0000-0000-0000-000000000002")
BRANCH_UFA = UUID("d1000000-0000-0000-0000-000000000001")
MODEL_UFA = UUID("d2000000-0000-0000-0000-000000000001")
CONFLICT_BLOCK_ID = UUID("f4000000-0000-0000-0000-000000000001")
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
                text("DELETE FROM asset_blocks WHERE id = :id"), {"id": CONFLICT_BLOCK_ID}
            )
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


async def customer_token(client: AsyncClient, provider: MemorySmsProvider) -> str:
    checkout = await client.post("/api/v1/r/demo-ufa/checkout/sessions")
    requested = await client.post(
        "/api/v1/r/demo-ufa/checkout/identity/otp/request",
        json={"phone_e164": SYNTHETIC_PHONE},
        headers={"X-Checkout-Token": checkout.json()["checkout_token"]},
    )
    code = provider.code_for(UUID(requested.json()["challenge_id"]))
    assert code is not None
    verified = await client.post(
        "/api/v1/r/demo-ufa/checkout/identity/otp/verify",
        json={"challenge_id": requested.json()["challenge_id"], "code": code},
        headers={"X-Checkout-Token": checkout.json()["checkout_token"]},
    )
    assert verified.status_code == 200
    return str(verified.json()["access_token"])


@pytest.mark.integration
async def test_extension_return_requests_overdue_and_blocked_notifications() -> None:
    runtime_url, owner_url = database_urls()
    owner = create_async_engine(owner_url)
    settings = Settings(
        app_env="test",
        database_url=runtime_url,
        enable_demo_previews=True,
        sms_provider_mode="memory",
        otp_request_rate_limit=10,
        otp_verify_rate_limit=20,
        checkout_mutation_rate_limit=20,
    )
    app = create_app(settings)
    start = date.today() + timedelta(days=90)
    original_end = start + timedelta(days=2)
    extended_end = original_end + timedelta(days=2)
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
                staff = {"Authorization": f"Bearer {login.json()['access_token']}"}
                created = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/phone",
                    headers={
                        **staff,
                        "Idempotency-Key": "request-test-phone-order-0001",
                    },
                    json={
                        "phone_e164": SYNTHETIC_PHONE,
                        "recipient_name": "Тестовый клиент",
                        "delivery_address": None,
                        "model_id": str(MODEL_UFA),
                        "branch_id": str(BRANCH_UFA),
                        "start_date": start.isoformat(),
                        "end_date": original_end.isoformat(),
                        "fulfillment": "pickup",
                    },
                )
                assert created.status_code == 201
                order_id = UUID(created.json()["order"]["order_id"])
                handed_over = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/handover",
                    headers={**staff, "Idempotency-Key": "request-test-handover-0001"},
                )
                assert handed_over.status_code == 200
                assert handed_over.json()["order"]["is_overdue"] is False

                token = await customer_token(client, provider)
                customer = {"Authorization": f"Bearer {token}"}
                extension_headers = {
                    **customer,
                    "Idempotency-Key": "customer-extension-request-0001",
                }
                extension = await client.post(
                    f"/api/v1/r/demo-ufa/me/orders/{order_id}/extension-requests",
                    headers=extension_headers,
                    json={"requested_end_date": extended_end.isoformat()},
                )
                assert extension.status_code == 201
                extension_body = extension.json()
                extension_id = UUID(extension_body["request"]["request_id"])
                assert extension_body["request"]["state"] == "pending"
                assert extension_body["request"]["additional_days"] == 2
                assert extension_body["request"]["amount_minor"] == 98_000
                replay = await client.post(
                    f"/api/v1/r/demo-ufa/me/orders/{order_id}/extension-requests",
                    headers=extension_headers,
                    json={"requested_end_date": extended_end.isoformat()},
                )
                assert replay.status_code == 201
                assert replay.json()["replayed"] is True
                duplicate = await client.post(
                    f"/api/v1/r/demo-ufa/me/orders/{order_id}/extension-requests",
                    headers={
                        **customer,
                        "Idempotency-Key": "customer-extension-request-0002",
                    },
                    json={"requested_end_date": (extended_end + timedelta(days=1)).isoformat()},
                )
                assert duplicate.status_code == 409

                return_headers = {
                    **customer,
                    "Idempotency-Key": "customer-return-request-0001",
                }
                return_request = await client.post(
                    f"/api/v1/r/demo-ufa/me/orders/{order_id}/pickup-requests",
                    headers=return_headers,
                    json={"requested_date": original_end.isoformat()},
                )
                assert return_request.status_code == 201
                return_id = UUID(return_request.json()["request"]["request_id"])
                return_replay = await client.post(
                    f"/api/v1/r/demo-ufa/me/orders/{order_id}/pickup-requests",
                    headers=return_headers,
                    json={"requested_date": original_end.isoformat()},
                )
                assert return_replay.status_code == 201
                assert return_replay.json()["replayed"] is True

                request_queue = await client.get(
                    f"/api/v1/t/{TENANT_UFA}/orders?requests=true", headers=staff
                )
                assert request_queue.status_code == 200
                assert request_queue.json()["total"] == 1
                assert request_queue.json()["items"][0]["order_id"] == str(order_id)
                dashboard = await client.get(f"/api/v1/t/{TENANT_UFA}/dashboard", headers=staff)
                assert dashboard.status_code == 200
                assert dashboard.json()["counts"]["pending_requests"] == 1
                attention = dashboard.json()["pending_requests"][0]
                assert attention["pending_request_types"] == ["extension", "return"]
                assert "phone_e164" not in dashboard.text

                approved_headers = {
                    **staff,
                    "Idempotency-Key": "staff-extension-approve-0001",
                }
                approved = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/"
                    f"extension-requests/{extension_id}/approve",
                    headers=approved_headers,
                )
                assert approved.status_code == 200
                approved_body = approved.json()
                assert approved_body["order"]["planned_end_date"] == extended_end.isoformat()
                assert approved_body["order"]["snapshot"]["end_date"] == original_end.isoformat()
                assert approved_body["request"]["state"] == "approved"
                approved_replay = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/"
                    f"extension-requests/{extension_id}/approve",
                    headers=approved_headers,
                )
                assert approved_replay.status_code == 200
                assert approved_replay.json()["replayed"] is True

                second_end = extended_end + timedelta(days=2)
                second_extension = await client.post(
                    f"/api/v1/r/demo-ufa/me/orders/{order_id}/extension-requests",
                    headers={
                        **customer,
                        "Idempotency-Key": "customer-extension-request-0003",
                    },
                    json={"requested_end_date": second_end.isoformat()},
                )
                assert second_extension.status_code == 201
                second_id = UUID(second_extension.json()["request"]["request_id"])

                async with owner.begin() as connection:
                    asset_id = await connection.scalar(
                        text(
                            "SELECT ra.asset_id FROM orders o JOIN rental_allocations ra "
                            "ON ra.id = o.allocation_id WHERE o.id = :order_id"
                        ),
                        {"order_id": order_id},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO asset_blocks "
                            "(id, tenant_id, asset_id, start_date, end_date, reason, note) "
                            "VALUES (:id, :tenant_id, :asset_id, :start_date, :end_date, "
                            "'maintenance', 'Synthetic extension conflict')"
                        ),
                        {
                            "id": CONFLICT_BLOCK_ID,
                            "tenant_id": TENANT_UFA,
                            "asset_id": asset_id,
                            "start_date": extended_end + timedelta(days=1),
                            "end_date": second_end,
                        },
                    )
                conflict = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/"
                    f"extension-requests/{second_id}/approve",
                    headers={**staff, "Idempotency-Key": "staff-extension-conflict-0001"},
                )
                assert conflict.status_code == 409
                rejected = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/"
                    f"extension-requests/{second_id}/reject",
                    headers={**staff, "Idempotency-Key": "staff-extension-reject-0001"},
                    json={"reason": "availability_conflict"},
                )
                assert rejected.status_code == 200
                assert rejected.json()["request"]["state"] == "rejected"

                acknowledged_headers = {
                    **staff,
                    "Idempotency-Key": "staff-return-acknowledge-0001",
                }
                acknowledged = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/"
                    f"pickup-requests/{return_id}/acknowledge",
                    headers=acknowledged_headers,
                )
                assert acknowledged.status_code == 200
                assert acknowledged.json()["request"]["state"] == "acknowledged"
                acknowledged_replay = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/"
                    f"pickup-requests/{return_id}/acknowledge",
                    headers=acknowledged_headers,
                )
                assert acknowledged_replay.status_code == 200
                assert acknowledged_replay.json()["replayed"] is True

                customer_order = await client.get(
                    f"/api/v1/r/demo-ufa/me/orders/{order_id}", headers=customer
                )
                assert customer_order.status_code == 200
                assert customer_order.json()["end_date"] == extended_end.isoformat()
                assert customer_order.json()["original_end_date"] == original_end.isoformat()
                assert customer_order.json()["extension_request"]["state"] == "rejected"
                assert customer_order.json()["return_request"]["state"] == "acknowledged"
                cross_tenant = await client.get(
                    f"/api/v1/t/{TENANT_KAZAN}/orders/{order_id}", headers=staff
                )
                assert cross_tenant.status_code == 404

                async with owner.begin() as connection:
                    jobs = (
                        await connection.execute(
                            text(
                                "SELECT template, state, blocked_reason FROM notification_jobs "
                                "WHERE order_id = :order_id ORDER BY template, created_at"
                            ),
                            {"order_id": order_id},
                        )
                    ).all()
                    events: set[str] = set(
                        (
                            await connection.execute(
                                text(
                                    "SELECT event_type FROM order_events WHERE order_id = :order_id"
                                ),
                                {"order_id": order_id},
                            )
                        )
                        .scalars()
                        .all()
                    )
                assert all(
                    job.blocked_reason == "provider_disabled"
                    for job in jobs
                    if job.state == "blocked"
                )
                assert {"extension_approved", "extension_rejected", "return_acknowledged"} <= {
                    job.template for job in jobs
                }
                assert {
                    "order_extension_requested",
                    "order_extension_approved",
                    "order_extension_rejected",
                    "order_return_requested",
                    "order_return_acknowledged",
                } <= events

                async with app.state.engine.begin() as connection:
                    await connection.execute(
                        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                        {"tenant_id": str(TENANT_KAZAN)},
                    )
                    assert (
                        await connection.scalar(
                            text("SELECT count(*) FROM order_extension_requests")
                        )
                    ) == 0
                    assert (
                        await connection.scalar(text("SELECT count(*) FROM order_return_requests"))
                    ) == 0
                    assert (
                        await connection.scalar(text("SELECT count(*) FROM notification_jobs"))
                    ) == 0

                async with owner.begin() as connection:
                    await connection.execute(
                        text("DELETE FROM asset_blocks WHERE id = :id"),
                        {"id": CONFLICT_BLOCK_ID},
                    )
                    await connection.execute(
                        text(
                            "UPDATE rental_allocations SET start_date = :start_date, "
                            "end_date = :end_date WHERE order_id = :order_id"
                        ),
                        {
                            "order_id": order_id,
                            "start_date": date.today() - timedelta(days=5),
                            "end_date": date.today() - timedelta(days=2),
                        },
                    )
                    await connection.execute(
                        text(
                            "UPDATE orders SET start_date = :start_date, end_date = :end_date "
                            "WHERE id = :order_id"
                        ),
                        {
                            "order_id": order_id,
                            "start_date": date.today() - timedelta(days=5),
                            "end_date": date.today() - timedelta(days=2),
                        },
                    )
                overdue = await client.get(
                    f"/api/v1/t/{TENANT_UFA}/orders?overdue=true", headers=staff
                )
                assert overdue.status_code == 200
                assert overdue.json()["total"] == 1
                assert overdue.json()["items"][0]["is_overdue"] is True
                assert overdue.json()["items"][0]["overdue_days"] >= 2
    finally:
        await clear_data(owner_url)
        await set_public_booking(owner_url, False)
        await owner.dispose()
