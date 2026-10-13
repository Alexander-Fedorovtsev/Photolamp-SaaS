import asyncio
import os
from datetime import date, timedelta
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.bootstrap_runtime_role import RUNTIME_ROLE
from app.config import Settings
from app.main import create_app

TENANT_UFA = UUID("d0000000-0000-0000-0000-000000000001")
BRANCH_UFA = UUID("d1000000-0000-0000-0000-000000000001")
MODEL_UFA = UUID("d2000000-0000-0000-0000-000000000001")


def database_urls() -> tuple[str, str]:
    defaults = Settings()
    configured = make_url(os.getenv("DATABASE_URL", defaults.database_url.get_secret_value()))
    runtime = configured.set(
        username=RUNTIME_ROLE,
        password="local-runtime-only",  # noqa: S106 - synthetic test credential
    ).render_as_string(hide_password=False)
    owner = os.getenv("DATABASE_OWNER_URL", defaults.migration_database_url())
    return runtime, owner


async def set_booking(owner: AsyncEngine, enabled: bool) -> None:
    async with owner.begin() as connection:
        await connection.execute(
            text(
                "UPDATE storefront_profiles SET booking_enabled = :enabled "
                "WHERE tenant_id = :tenant_id"
            ),
            {"enabled": enabled, "tenant_id": TENANT_UFA},
        )


async def clear_checkout(owner: AsyncEngine) -> None:
    async with owner.begin() as connection:
        await connection.execute(
            text("UPDATE rental_allocations SET order_id = NULL WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )
        for table in ("order_events", "orders", "rental_allocations", "checkout_sessions"):
            await connection.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :tenant_id"),  # noqa: S608
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


def hold_request() -> dict[str, str]:
    start = date.today() + timedelta(days=40)
    return {
        "model_id": str(MODEL_UFA),
        "branch_id": str(BRANCH_UFA),
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=2)).isoformat(),
        "fulfillment": "pickup",
    }


async def create_session_and_hold(client: AsyncClient) -> tuple[str, dict[str, object]]:
    session = await client.post("/api/v1/r/demo-ufa/checkout/sessions")
    assert session.status_code == 201
    assert session.json()["identity_verified"] is False
    checkout_token = session.json()["checkout_token"]
    hold = await client.post(
        "/api/v1/r/demo-ufa/checkout/holds",
        json=hold_request(),
        headers={
            "Idempotency-Key": "synthetic-checkout-hold-0001",
            "X-Checkout-Token": checkout_token,
        },
    )
    assert hold.status_code == 201
    return checkout_token, hold.json()


@pytest.mark.integration
async def test_draft_order_is_atomic_private_and_cancelled_with_hold() -> None:
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
    try:
        await clear_checkout(owner)
        await clear_rate_limits(settings.redis_url.get_secret_value())
        await set_booking(owner, True)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                checkout_token, hold = await create_session_and_hold(client)
                order_url = "/api/v1/r/demo-ufa/checkout/orders"
                headers = {
                    "Idempotency-Key": "synthetic-draft-order-0001",
                    "X-Checkout-Token": checkout_token,
                    "X-Hold-Token": str(hold["management_token"]),
                }
                responses = await asyncio.gather(
                    *(
                        client.post(order_url, json={"hold_id": hold["hold_id"]}, headers=headers)
                        for _ in range(2)
                    )
                )
                assert [response.status_code for response in responses] == [201, 201]
                bodies = [response.json() for response in responses]
                assert len({body["order_id"] for body in bodies}) == 1
                assert sorted(body["replayed"] for body in bodies) == [False, True]
                order = bodies[0]
                assert order["state"] == "held"
                assert order["source"] == "website"
                assert order["identity_verified"] is False
                assert order["payment_status"] == "not_started"
                assert order["confirmation_available"] is False
                assert order["quote"]["total_minor"] == 147_000
                for forbidden in ("asset_id", "asset_code", "phone", "email", "address"):
                    assert forbidden not in responses[0].text

                detail = await client.get(
                    f"{order_url}/{order['order_id']}",
                    headers={"X-Checkout-Token": checkout_token},
                )
                assert detail.status_code == 200
                assert detail.headers["cache-control"] == "no-store"

                another_session = await client.post("/api/v1/r/demo-ufa/checkout/sessions")
                assert another_session.status_code == 201
                private = await client.get(
                    f"{order_url}/{order['order_id']}",
                    headers={"X-Checkout-Token": another_session.json()["checkout_token"]},
                )
                assert private.status_code == 404

                different_key = await client.post(
                    order_url,
                    json={"hold_id": hold["hold_id"]},
                    headers={**headers, "Idempotency-Key": "synthetic-draft-order-0002"},
                )
                assert different_key.status_code == 409

                released = await client.delete(
                    f"/api/v1/r/demo-ufa/checkout/holds/{hold['hold_id']}",
                    headers={
                        "X-Checkout-Token": checkout_token,
                        "X-Hold-Token": str(hold["management_token"]),
                    },
                )
                assert released.status_code == 200
                gone = await client.get(
                    f"{order_url}/{order['order_id']}",
                    headers={"X-Checkout-Token": checkout_token},
                )
                assert gone.status_code == 404

        async with owner.begin() as connection:
            state = await connection.execute(
                text(
                    "SELECT o.state, count(oe.id) AS events "
                    "FROM orders o LEFT JOIN order_events oe "
                    "ON oe.tenant_id = o.tenant_id AND oe.order_id = o.id "
                    "WHERE o.tenant_id = :tenant_id GROUP BY o.state"
                ),
                {"tenant_id": TENANT_UFA},
            )
            assert state.one() == ("cancelled", 1)

        async with runtime.begin() as connection:
            assert (await connection.execute(text("SELECT id FROM checkout_sessions"))).all() == []
            assert (await connection.execute(text("SELECT id FROM orders"))).all() == []
            assert (await connection.execute(text("SELECT id FROM order_events"))).all() == []
    finally:
        await clear_checkout(owner)
        await set_booking(owner, False)
        await runtime.dispose()
        await owner.dispose()


@pytest.mark.integration
async def test_expired_hold_expires_order_and_session_expiry_is_enforced() -> None:
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
        await clear_checkout(owner)
        await clear_rate_limits(settings.redis_url.get_secret_value())
        await set_booking(owner, True)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                checkout_token, hold = await create_session_and_hold(client)
                order = await client.post(
                    "/api/v1/r/demo-ufa/checkout/orders",
                    json={"hold_id": hold["hold_id"]},
                    headers={
                        "Idempotency-Key": "synthetic-expiring-order-0001",
                        "X-Checkout-Token": checkout_token,
                        "X-Hold-Token": str(hold["management_token"]),
                    },
                )
                assert order.status_code == 201
                async with owner.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE rental_allocations SET expires_at = "
                            "now() - interval '1 second' "
                            "WHERE tenant_id = :tenant_id AND id = :hold_id"
                        ),
                        {"tenant_id": TENANT_UFA, "hold_id": UUID(str(hold["hold_id"]))},
                    )
                expired = await client.get(
                    f"/api/v1/r/demo-ufa/checkout/orders/{order.json()['order_id']}",
                    headers={"X-Checkout-Token": checkout_token},
                )
                assert expired.status_code == 404

                async with owner.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE checkout_sessions SET expires_at = now() - interval '1 second' "
                            "WHERE tenant_id = :tenant_id"
                        ),
                        {"tenant_id": TENANT_UFA},
                    )
                rejected = await client.post(
                    "/api/v1/r/demo-ufa/checkout/holds",
                    json=hold_request(),
                    headers={
                        "Idempotency-Key": "synthetic-expired-session-0001",
                        "X-Checkout-Token": checkout_token,
                    },
                )
                assert rejected.status_code == 401

        async with owner.begin() as connection:
            states = (
                await connection.execute(
                    text(
                        "SELECT o.state, ra.state FROM orders o JOIN rental_allocations ra "
                        "ON ra.tenant_id = o.tenant_id AND ra.id = o.allocation_id "
                        "WHERE o.tenant_id = :tenant_id"
                    ),
                    {"tenant_id": TENANT_UFA},
                )
            ).one()
            assert states == ("expired", "expired")
    finally:
        await clear_checkout(owner)
        await set_booking(owner, False)
        await owner.dispose()


@pytest.mark.integration
async def test_checkout_session_issuance_is_rate_limited() -> None:
    runtime_url, owner_url = database_urls()
    owner = create_async_engine(owner_url)
    settings = Settings(
        app_env="test",
        database_url=runtime_url,
        enable_demo_previews=True,
        checkout_session_rate_limit=2,
    )
    app = create_app(settings)
    try:
        await clear_checkout(owner)
        await clear_rate_limits(settings.redis_url.get_secret_value())
        await set_booking(owner, True)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                responses = [
                    await client.post("/api/v1/r/demo-ufa/checkout/sessions") for _ in range(3)
                ]
        assert [response.status_code for response in responses] == [201, 201, 429]
        assert int(responses[-1].headers["retry-after"]) > 0
        assert responses[-1].json() == {"detail": "Too many checkout requests"}
    finally:
        await clear_rate_limits(settings.redis_url.get_secret_value())
        await clear_checkout(owner)
        await set_booking(owner, False)
        await owner.dispose()
