import os
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.bootstrap_runtime_role import RUNTIME_ROLE
from app.config import Settings
from app.main import create_app

TENANT_UFA = UUID("d0000000-0000-0000-0000-000000000001")
TENANT_KAZAN = UUID("d0000000-0000-0000-0000-000000000002")
BRANCH_UFA = UUID("d1000000-0000-0000-0000-000000000001")
MODEL_UFA = UUID("d2000000-0000-0000-0000-000000000001")
DEMO_USER = UUID("d5000000-0000-0000-0000-000000000001")


def database_urls() -> tuple[str, str]:
    defaults = Settings()
    configured = make_url(os.getenv("DATABASE_URL", defaults.database_url.get_secret_value()))
    runtime = configured.set(
        username=RUNTIME_ROLE,
        password="local-runtime-only",  # noqa: S106 - synthetic test credential
    ).render_as_string(hide_password=False)
    owner = os.getenv("DATABASE_OWNER_URL", defaults.migration_database_url())
    return runtime, owner


async def clear_order_data(owner: AsyncEngine) -> None:
    async with owner.begin() as connection:
        await connection.execute(
            text("UPDATE orders SET customer_id = NULL WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )
        await connection.execute(
            text("UPDATE checkout_sessions SET customer_id = NULL WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )
        for table in ("customer_auth_challenges", "customer_sessions"):
            await connection.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :tenant_id"),  # noqa: S608
                {"tenant_id": TENANT_UFA},
            )
        await connection.execute(
            text("UPDATE rental_allocations SET order_id = NULL WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )
        for table in ("order_events", "orders", "rental_allocations", "checkout_sessions"):
            await connection.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :tenant_id"),  # noqa: S608
                {"tenant_id": TENANT_UFA},
            )
        await connection.execute(
            text("DELETE FROM customers WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )


async def clear_rate_limits(redis_url: str) -> None:
    redis = Redis.from_url(redis_url)
    try:
        keys = [key async for key in redis.scan_iter(match="photolamp:rate:v1:*")]
        if keys:
            await redis.delete(*keys)
    finally:
        await redis.aclose()


async def set_booking(owner: AsyncEngine, enabled: bool) -> None:
    async with owner.begin() as connection:
        await connection.execute(
            text(
                "UPDATE storefront_profiles SET booking_enabled = :enabled "
                "WHERE tenant_id = :tenant_id"
            ),
            {"enabled": enabled, "tenant_id": TENANT_UFA},
        )


async def create_draft(client: AsyncClient) -> dict[str, object]:
    checkout = await client.post("/api/v1/r/demo-ufa/checkout/sessions")
    assert checkout.status_code == 201
    checkout_token = checkout.json()["checkout_token"]
    start = date.today() + timedelta(days=60)
    hold = await client.post(
        "/api/v1/r/demo-ufa/checkout/holds",
        json={
            "model_id": str(MODEL_UFA),
            "branch_id": str(BRANCH_UFA),
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=2)).isoformat(),
            "fulfillment": "pickup",
        },
        headers={
            "Idempotency-Key": "synthetic-admin-order-hold-0001",
            "X-Checkout-Token": checkout_token,
        },
    )
    assert hold.status_code == 201
    order = await client.post(
        "/api/v1/r/demo-ufa/checkout/orders",
        json={"hold_id": hold.json()["hold_id"]},
        headers={
            "Idempotency-Key": "synthetic-admin-order-draft-0001",
            "X-Checkout-Token": checkout_token,
            "X-Hold-Token": hold.json()["management_token"],
        },
    )
    assert order.status_code == 201
    return {**order.json(), "checkout_token": checkout_token}


@pytest.mark.integration
async def test_staff_order_queue_snapshot_and_idempotent_transitions() -> None:
    runtime_url, owner_url = database_urls()
    owner = create_async_engine(owner_url)
    runtime = create_async_engine(runtime_url)
    settings = Settings(
        app_env="test",
        database_url=runtime_url,
        enable_demo_previews=True,
        checkout_mutation_rate_limit=50,
    )
    app = create_app(settings)
    original_model_name = ""
    original_branch_name = ""
    try:
        await clear_order_data(owner)
        await clear_rate_limits(settings.redis_url.get_secret_value())
        await set_booking(owner, True)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                login = await client.post("/api/v1/auth/demo-session")
                assert login.status_code == 200
                headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
                order = await create_draft(client)
                order_id = order["order_id"]
                assert order["snapshot"]["model_name"] == "Домашняя LED-фотолампа"  # type: ignore[index]
                assert order["snapshot"]["branch_name"] == "Демо-точка в Уфе"  # type: ignore[index]

                no_session = await client.get(f"/api/v1/t/{TENANT_UFA}/orders")
                assert no_session.status_code == 401
                assert no_session.headers["cache-control"] == "no-store"
                queue = await client.get(
                    f"/api/v1/t/{TENANT_UFA}/orders?state=held", headers=headers
                )
                assert queue.status_code == 200
                assert queue.headers["cache-control"] == "no-store"
                assert queue.json()["total"] == 1
                item = queue.json()["items"][0]
                assert item["identity_verified"] is False
                assert item["payment_status"] == "not_started"
                for forbidden in (
                    "phone",
                    "email",
                    "address",
                    "asset_code",
                    "serial_no",
                    "staff_user_id",
                ):
                    assert forbidden not in queue.text

                with pytest.raises(DBAPIError, match="order_snapshot is immutable"):
                    async with owner.begin() as connection:
                        await connection.execute(
                            text(
                                "UPDATE orders SET order_snapshot = '{}'::jsonb "
                                "WHERE id = :order_id"
                            ),
                            {"order_id": UUID(str(order_id))},
                        )

                unverified = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/confirm",
                    headers={**headers, "Idempotency-Key": "confirm-admin-order-0001"},
                )
                assert unverified.status_code == 409
                assert unverified.json()["detail"] == "Verified customer identity is required"

                customer_id = uuid4()
                async with owner.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO customers "
                            "(id, tenant_id, phone_lookup_hash, phone_last4, phone_verified_at) "
                            "VALUES (:id, :tenant_id, :phone_hash, '0001', now())"
                        ),
                        {
                            "id": customer_id,
                            "tenant_id": TENANT_UFA,
                            "phone_hash": b"o" * 32,
                        },
                    )
                    await connection.execute(
                        text(
                            "UPDATE orders SET customer_id = :customer_id "
                            "WHERE tenant_id = :tenant_id AND id = :order_id"
                        ),
                        {
                            "customer_id": customer_id,
                            "tenant_id": TENANT_UFA,
                            "order_id": UUID(str(order_id)),
                        },
                    )
                    names = (
                        await connection.execute(
                            text(
                                "SELECT em.name, b.name FROM equipment_models em, branches b "
                                "WHERE em.id = :model_id AND b.id = :branch_id"
                            ),
                            {"model_id": MODEL_UFA, "branch_id": BRANCH_UFA},
                        )
                    ).one()
                    original_model_name = str(names[0])
                    original_branch_name = str(names[1])

                confirm_headers = {
                    **headers,
                    "Idempotency-Key": "confirm-admin-order-0001",
                }
                confirmed = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/confirm",
                    headers=confirm_headers,
                )
                assert confirmed.status_code == 200
                assert confirmed.json()["order"]["state"] == "confirmed"
                assert "confirmed_by_staff_user_id" not in confirmed.text
                assert confirmed.json()["replayed"] is False
                replay = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/confirm",
                    headers=confirm_headers,
                )
                assert replay.status_code == 200
                assert replay.json()["replayed"] is True

                async with owner.begin() as connection:
                    await connection.execute(
                        text("UPDATE equipment_models SET name = 'Renamed model' WHERE id = :id"),
                        {"id": MODEL_UFA},
                    )
                    await connection.execute(
                        text("UPDATE branches SET name = 'Renamed branch' WHERE id = :id"),
                        {"id": BRANCH_UFA},
                    )
                detail = await client.get(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}", headers=headers
                )
                assert detail.status_code == 200
                assert detail.json()["snapshot"]["model_name"] == original_model_name
                assert detail.json()["snapshot"]["branch_name"] == original_branch_name
                cross_tenant = await client.get(
                    f"/api/v1/t/{TENANT_KAZAN}/orders/{order_id}", headers=headers
                )
                assert cross_tenant.status_code == 404

                reused_key = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/cancel",
                    headers=confirm_headers,
                )
                assert reused_key.status_code == 409
                cancel_headers = {
                    **headers,
                    "Idempotency-Key": "cancel-admin-order-0001",
                }
                cancelled = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/cancel",
                    headers=cancel_headers,
                )
                assert cancelled.status_code == 200
                assert cancelled.json()["order"]["state"] == "cancelled"
                assert cancelled.json()["replayed"] is False
                cancelled_replay = await client.post(
                    f"/api/v1/t/{TENANT_UFA}/orders/{order_id}/cancel",
                    headers=cancel_headers,
                )
                assert cancelled_replay.status_code == 200
                assert cancelled_replay.json()["replayed"] is True

                async with owner.begin() as connection:
                    states = (
                        await connection.execute(
                            text(
                                "SELECT o.state, ra.state, ra.expires_at FROM orders o "
                                "JOIN rental_allocations ra ON ra.tenant_id = o.tenant_id "
                                "AND ra.id = o.allocation_id WHERE o.id = :order_id"
                            ),
                            {"order_id": UUID(str(order_id))},
                        )
                    ).one()
                    assert states == ("cancelled", "cancelled", None)
                    events = (
                        await connection.execute(
                            text(
                                "SELECT event_type, actor_staff_user_id FROM order_events "
                                "WHERE order_id = :order_id ORDER BY happened_at"
                            ),
                            {"order_id": UUID(str(order_id))},
                        )
                    ).all()
                    assert [event.event_type for event in events] == [
                        "draft_created",
                        "order_confirmed",
                        "order_cancelled",
                    ]
                    assert events[1].actor_staff_user_id == DEMO_USER
                    assert events[2].actor_staff_user_id == DEMO_USER
                    await connection.execute(
                        text(
                            "UPDATE tenant_memberships SET role = 'courier' "
                            "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                        ),
                        {"tenant_id": TENANT_UFA, "user_id": DEMO_USER},
                    )
                courier = await client.get(f"/api/v1/t/{TENANT_UFA}/orders", headers=headers)
                assert courier.status_code == 403

        async with runtime.begin() as connection:
            assert (await connection.execute(text("SELECT id FROM orders"))).all() == []
            assert (await connection.execute(text("SELECT id FROM order_events"))).all() == []
    finally:
        async with owner.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE tenant_memberships SET role = 'owner' "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                {"tenant_id": TENANT_UFA, "user_id": DEMO_USER},
            )
            if original_model_name:
                await connection.execute(
                    text("UPDATE equipment_models SET name = :name WHERE id = :id"),
                    {"name": original_model_name, "id": MODEL_UFA},
                )
            if original_branch_name:
                await connection.execute(
                    text("UPDATE branches SET name = :name WHERE id = :id"),
                    {"name": original_branch_name, "id": BRANCH_UFA},
                )
        await clear_order_data(owner)
        await set_booking(owner, False)
        await runtime.dispose()
        await owner.dispose()


@pytest.mark.integration
async def test_operations_dashboard_is_private_tenant_scoped_and_role_guarded() -> None:
    runtime_url, owner_url = database_urls()
    owner = create_async_engine(owner_url)
    settings = Settings(
        app_env="test",
        database_url=runtime_url,
        enable_demo_previews=True,
        checkout_mutation_rate_limit=50,
    )
    app = create_app(settings)
    try:
        await clear_order_data(owner)
        await clear_rate_limits(settings.redis_url.get_secret_value())
        await set_booking(owner, True)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                login = await client.post("/api/v1/auth/demo-session")
                headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
                order = await create_draft(client)

                anonymous = await client.get(f"/api/v1/t/{TENANT_UFA}/dashboard")
                assert anonymous.status_code == 401

                response = await client.get(f"/api/v1/t/{TENANT_UFA}/dashboard", headers=headers)
                assert response.status_code == 200
                assert response.headers["cache-control"] == "no-store"
                dashboard = response.json()
                assert dashboard["tenant_id"] == str(TENANT_UFA)
                assert dashboard["timezone"] == "Asia/Yekaterinburg"
                assert dashboard["counts"]["awaiting_confirmation"] == 1
                assert sum(dashboard["inventory"].values()) >= 1
                assert str(order["order_id"]) not in response.text
                for forbidden in (
                    "phone_e164",
                    "recipient_name",
                    "delivery_address",
                    "asset_code",
                ):
                    assert forbidden not in response.text

                other_tenant = await client.get(
                    f"/api/v1/t/{TENANT_KAZAN}/dashboard", headers=headers
                )
                assert other_tenant.status_code == 200
                assert other_tenant.json()["tenant_id"] == str(TENANT_KAZAN)
                assert other_tenant.json()["counts"]["awaiting_confirmation"] == 0

                async with owner.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE tenant_memberships SET role = 'courier' "
                            "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                        ),
                        {"tenant_id": TENANT_UFA, "user_id": DEMO_USER},
                    )
                forbidden_response = await client.get(
                    f"/api/v1/t/{TENANT_UFA}/dashboard", headers=headers
                )
                assert forbidden_response.status_code == 403
    finally:
        async with owner.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE tenant_memberships SET role = 'owner' "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                {"tenant_id": TENANT_UFA, "user_id": DEMO_USER},
            )
        await clear_order_data(owner)
        await set_booking(owner, False)
        await owner.dispose()
