import os
from collections.abc import Sequence
from datetime import date, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.bootstrap_runtime_role import RUNTIME_ROLE
from app.capabilities import capability_digest
from app.config import Settings
from app.main import create_app
from app.sms import MemorySmsProvider

TENANT_UFA = UUID("d0000000-0000-0000-0000-000000000001")
BRANCH_UFA = UUID("d1000000-0000-0000-0000-000000000001")
MODEL_UFA = UUID("d2000000-0000-0000-0000-000000000001")
SYNTHETIC_PHONE = "+70000000001"


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


async def clear_identity(owner: AsyncEngine) -> None:
    async with owner.begin() as connection:
        await connection.execute(
            text("UPDATE orders SET customer_id = NULL WHERE tenant_id = :id"),
            {"id": TENANT_UFA},
        )
        await connection.execute(
            text("UPDATE checkout_sessions SET customer_id = NULL WHERE tenant_id = :id"),
            {"id": TENANT_UFA},
        )
        for table in ("customer_auth_challenges", "customer_sessions", "customers"):
            await connection.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :id"),  # noqa: S608
                {"id": TENANT_UFA},
            )


async def clear_checkout(owner: AsyncEngine) -> None:
    await clear_identity(owner)
    async with owner.begin() as connection:
        await connection.execute(
            text("UPDATE rental_allocations SET order_id = NULL WHERE tenant_id = :id"),
            {"id": TENANT_UFA},
        )
        for table in ("order_events", "orders", "rental_allocations", "checkout_sessions"):
            await connection.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :id"),  # noqa: S608
                {"id": TENANT_UFA},
            )


async def clear_rate_limits(redis_url: str) -> None:
    redis = Redis.from_url(redis_url)
    try:
        keys = [key async for key in redis.scan_iter(match="photolamp:rate:v1:*")]
        if keys:
            await redis.delete(*keys)
    finally:
        await redis.aclose()


async def create_checkout(client: AsyncClient) -> str:
    response = await client.post("/api/v1/r/demo-ufa/checkout/sessions")
    assert response.status_code == 201
    return str(response.json()["checkout_token"])


def hold_request() -> dict[str, str]:
    start = date.today() + timedelta(days=60)
    return {
        "model_id": str(MODEL_UFA),
        "branch_id": str(BRANCH_UFA),
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=2)).isoformat(),
        "fulfillment": "pickup",
    }


@pytest.mark.integration
async def test_disabled_sms_provider_fails_without_persisting_phone_or_challenge() -> None:
    runtime_url, owner_url = database_urls()
    owner = create_async_engine(owner_url)
    settings = Settings(app_env="test", database_url=runtime_url, enable_demo_previews=True)
    app = create_app(settings)
    try:
        await clear_checkout(owner)
        await clear_rate_limits(settings.redis_url.get_secret_value())
        await set_booking(owner, True)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                checkout_token = await create_checkout(client)
                response = await client.post(
                    "/api/v1/r/demo-ufa/checkout/identity/otp/request",
                    json={"phone_e164": SYNTHETIC_PHONE},
                    headers={"X-Checkout-Token": checkout_token},
                )
        assert response.status_code == 503
        assert response.json() == {"detail": "SMS verification is not configured"}
        assert SYNTHETIC_PHONE not in response.text
        async with owner.begin() as connection:
            count = await connection.scalar(text("SELECT count(*) FROM customer_auth_challenges"))
            assert count == 0
    finally:
        await clear_checkout(owner)
        await set_booking(owner, False)
        await owner.dispose()


@pytest.mark.integration
async def test_memory_otp_verifies_identity_links_order_and_rotates_session() -> None:
    runtime_url, owner_url = database_urls()
    owner = create_async_engine(owner_url)
    runtime = create_async_engine(runtime_url)
    settings = Settings(
        app_env="test",
        database_url=runtime_url,
        enable_demo_previews=True,
        sms_provider_mode="memory",
        checkout_mutation_rate_limit=50,
        otp_request_rate_limit=10,
        otp_verify_rate_limit=20,
    )
    app = create_app(settings)
    try:
        await clear_checkout(owner)
        await clear_rate_limits(settings.redis_url.get_secret_value())
        await set_booking(owner, True)
        async with app.router.lifespan_context(app):
            provider = app.state.sms_provider
            assert isinstance(provider, MemorySmsProvider)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                checkout_token = await create_checkout(client)
                hold = await client.post(
                    "/api/v1/r/demo-ufa/checkout/holds",
                    json=hold_request(),
                    headers={
                        "Idempotency-Key": "synthetic-identity-hold-0001",
                        "X-Checkout-Token": checkout_token,
                    },
                )
                assert hold.status_code == 201
                order = await client.post(
                    "/api/v1/r/demo-ufa/checkout/orders",
                    json={"hold_id": hold.json()["hold_id"]},
                    headers={
                        "Idempotency-Key": "synthetic-identity-order-0001",
                        "X-Checkout-Token": checkout_token,
                        "X-Hold-Token": hold.json()["management_token"],
                    },
                )
                assert order.status_code == 201
                assert order.json()["identity_verified"] is False
                unauthenticated_orders = await client.get("/api/v1/r/demo-ufa/me/orders")
                assert unauthenticated_orders.status_code == 401

                requested = await client.post(
                    "/api/v1/r/demo-ufa/checkout/identity/otp/request",
                    json={"phone_e164": SYNTHETIC_PHONE},
                    headers={"X-Checkout-Token": checkout_token},
                )
                assert requested.status_code == 201
                assert requested.headers["cache-control"] == "no-store"
                assert SYNTHETIC_PHONE not in requested.text
                assert requested.json()["phone_masked"].endswith("0001")
                challenge_id = UUID(requested.json()["challenge_id"])
                code = provider.code_for(challenge_id)
                assert code is not None
                wrong_code = "999999" if code != "999999" else "888888"
                wrong = await client.post(
                    "/api/v1/r/demo-ufa/checkout/identity/otp/verify",
                    json={"challenge_id": str(challenge_id), "code": wrong_code},
                    headers={"X-Checkout-Token": checkout_token},
                )
                assert wrong.status_code == 422

                verified = await client.post(
                    "/api/v1/r/demo-ufa/checkout/identity/otp/verify",
                    json={"challenge_id": str(challenge_id), "code": code},
                    headers={"X-Checkout-Token": checkout_token},
                )
                assert verified.status_code == 200
                identity = verified.json()
                assert identity["identity_verified"] is True
                assert identity["replayed"] is False
                assert identity["access_token"].startswith("pc_")

                me = await client.get(
                    "/api/v1/r/demo-ufa/me",
                    headers={"Authorization": f"Bearer {identity['access_token']}"},
                )
                assert me.status_code == 200
                assert me.json()["customer_id"] == identity["customer_id"]

                orders = await client.get(
                    "/api/v1/r/demo-ufa/me/orders?limit=10&offset=0",
                    headers={"Authorization": f"Bearer {identity['access_token']}"},
                )
                assert orders.status_code == 200
                assert orders.headers["cache-control"] == "no-store"
                assert orders.json()["total"] == 1
                assert orders.json()["limit"] == 10
                assert orders.json()["offset"] == 0
                customer_order = orders.json()["items"][0]
                assert customer_order["order_id"] == order.json()["order_id"]
                assert customer_order["state"] == "held"
                assert customer_order["identity_verified"] is True
                assert customer_order["confirmation_available"] is False
                assert customer_order["payment_status"] == "not_started"
                for forbidden in (
                    "phone",
                    "address",
                    "asset_id",
                    "asset_code",
                    "checkout_session_id",
                ):
                    assert forbidden not in orders.text

                customer_detail = await client.get(
                    f"/api/v1/r/demo-ufa/me/orders/{order.json()['order_id']}",
                    headers={"Authorization": f"Bearer {identity['access_token']}"},
                )
                assert customer_detail.status_code == 200
                assert customer_detail.json() == customer_order
                invalid_page = await client.get(
                    "/api/v1/r/demo-ufa/me/orders?limit=0",
                    headers={"Authorization": f"Bearer {identity['access_token']}"},
                )
                assert invalid_page.status_code == 422

                other_token = "pc_" + "B" * 43
                async with owner.begin() as connection:
                    other_customer_id = uuid4()
                    await connection.execute(
                        text(
                            "INSERT INTO customers "
                            "(id, tenant_id, phone_lookup_hash, phone_last4) VALUES "
                            "(:id, :tenant_id, :phone_hash, '0002')"
                        ),
                        {
                            "id": other_customer_id,
                            "tenant_id": TENANT_UFA,
                            "phone_hash": b"b" * 32,
                        },
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO customer_sessions "
                            "(id, tenant_id, customer_id, token_hash, expires_at) VALUES "
                            "(:id, :tenant_id, :customer_id, :token_hash, "
                            "now() + interval '1 hour')"
                        ),
                        {
                            "id": uuid4(),
                            "tenant_id": TENANT_UFA,
                            "customer_id": other_customer_id,
                            "token_hash": capability_digest(other_token),
                        },
                    )
                other_orders = await client.get(
                    "/api/v1/r/demo-ufa/me/orders",
                    headers={"Authorization": f"Bearer {other_token}"},
                )
                assert other_orders.status_code == 200
                assert other_orders.json()["total"] == 0
                private_order = await client.get(
                    f"/api/v1/r/demo-ufa/me/orders/{order.json()['order_id']}",
                    headers={"Authorization": f"Bearer {other_token}"},
                )
                assert private_order.status_code == 404
                wrong_tenant = await client.get(
                    "/api/v1/r/demo-kazan/me/orders",
                    headers={"Authorization": f"Bearer {identity['access_token']}"},
                )
                assert wrong_tenant.status_code == 401

                linked = await client.get(
                    f"/api/v1/r/demo-ufa/checkout/orders/{order.json()['order_id']}",
                    headers={"X-Checkout-Token": checkout_token},
                )
                assert linked.status_code == 200
                assert linked.json()["identity_verified"] is True
                assert linked.json()["confirmation_available"] is False

                replay = await client.post(
                    "/api/v1/r/demo-ufa/checkout/identity/otp/verify",
                    json={"challenge_id": str(challenge_id), "code": code},
                    headers={"X-Checkout-Token": checkout_token},
                )
                assert replay.status_code == 200
                assert replay.json()["replayed"] is True
                assert replay.json()["access_token"] != identity["access_token"]
                old_session = await client.get(
                    "/api/v1/r/demo-ufa/me",
                    headers={"Authorization": f"Bearer {identity['access_token']}"},
                )
                assert old_session.status_code == 401
                old_session_orders = await client.get(
                    "/api/v1/r/demo-ufa/me/orders",
                    headers={"Authorization": f"Bearer {identity['access_token']}"},
                )
                assert old_session_orders.status_code == 401
                new_session = await client.get(
                    "/api/v1/r/demo-ufa/me",
                    headers={"Authorization": f"Bearer {replay.json()['access_token']}"},
                )
                assert new_session.status_code == 200
                async with owner.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE rental_allocations SET expires_at = "
                            "now() - interval '1 second' WHERE order_id = :order_id"
                        ),
                        {"order_id": UUID(order.json()["order_id"])},
                    )
                expired_order = await client.get(
                    f"/api/v1/r/demo-ufa/me/orders/{order.json()['order_id']}",
                    headers={"Authorization": f"Bearer {replay.json()['access_token']}"},
                )
                assert expired_order.status_code == 200
                assert expired_order.json()["state"] == "expired"
                async with owner.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE customer_auth_challenges SET expires_at = "
                            "now() - interval '1 second' WHERE id = :challenge_id"
                        ),
                        {"challenge_id": challenge_id},
                    )
                late_replay = await client.post(
                    "/api/v1/r/demo-ufa/checkout/identity/otp/verify",
                    json={"challenge_id": str(challenge_id), "code": code},
                    headers={"X-Checkout-Token": checkout_token},
                )
                assert late_replay.status_code == 409
                async with owner.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE customer_sessions SET expires_at = "
                            "now() - interval '1 second' WHERE tenant_id = :tenant_id"
                        ),
                        {"tenant_id": TENANT_UFA},
                    )
                expired_session = await client.get(
                    "/api/v1/r/demo-ufa/me",
                    headers={"Authorization": f"Bearer {replay.json()['access_token']}"},
                )
                assert expired_session.status_code == 401
                expired_session_orders = await client.get(
                    "/api/v1/r/demo-ufa/me/orders",
                    headers={"Authorization": f"Bearer {replay.json()['access_token']}"},
                )
                assert expired_session_orders.status_code == 401
                async with owner.begin() as connection:
                    expired_state = await connection.scalar(
                        text(
                            "SELECT state FROM customer_sessions "
                            "WHERE tenant_id = :tenant_id AND token_hash = :token_hash"
                        ),
                        {
                            "tenant_id": TENANT_UFA,
                            "token_hash": capability_digest(replay.json()["access_token"]),
                        },
                    )
                    assert expired_state == "expired"

        async with owner.begin() as connection:
            columns = {
                row.column_name
                for row in (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name IN ('customers', 'customer_auth_challenges')"
                        )
                    )
                )
            }
            assert "phone_e164" not in columns
            stored = (
                await connection.execute(
                    text(
                        "SELECT octet_length(phone_lookup_hash), phone_last4 "
                        "FROM customers WHERE tenant_id = :tenant_id "
                        "AND phone_last4 = '0001'"
                    ),
                    {"tenant_id": TENANT_UFA},
                )
            ).one()
            assert stored == (32, "0001")
        async with runtime.begin() as connection:
            for table in ("customers", "customer_sessions", "customer_auth_challenges"):
                assert (await connection.execute(text(f"SELECT id FROM {table}"))).all() == []  # noqa: S608
    finally:
        await clear_checkout(owner)
        await set_booking(owner, False)
        await runtime.dispose()
        await owner.dispose()


@pytest.mark.integration
async def test_otp_attempt_limit_expiry_and_supersession_are_enforced() -> None:
    runtime_url, owner_url = database_urls()
    owner = create_async_engine(owner_url)
    settings = Settings(
        app_env="test",
        database_url=runtime_url,
        enable_demo_previews=True,
        sms_provider_mode="memory",
        otp_max_attempts=3,
        otp_request_rate_limit=10,
        otp_verify_rate_limit=20,
    )
    app = create_app(settings)
    try:
        await clear_checkout(owner)
        await clear_rate_limits(settings.redis_url.get_secret_value())
        await set_booking(owner, True)
        async with app.router.lifespan_context(app):
            provider = app.state.sms_provider
            assert isinstance(provider, MemorySmsProvider)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                checkout_token = await create_checkout(client)
                first = await client.post(
                    "/api/v1/r/demo-ufa/checkout/identity/otp/request",
                    json={"phone_e164": SYNTHETIC_PHONE},
                    headers={"X-Checkout-Token": checkout_token},
                )
                second = await client.post(
                    "/api/v1/r/demo-ufa/checkout/identity/otp/request",
                    json={"phone_e164": SYNTHETIC_PHONE},
                    headers={"X-Checkout-Token": checkout_token},
                )
                second_id = UUID(second.json()["challenge_id"])
                actual_code = provider.code_for(second_id)
                assert actual_code is not None
                wrong_code = "999999" if actual_code != "999999" else "888888"
                superseded = await client.post(
                    "/api/v1/r/demo-ufa/checkout/identity/otp/verify",
                    json={"challenge_id": first.json()["challenge_id"], "code": "000000"},
                    headers={"X-Checkout-Token": checkout_token},
                )
                assert superseded.status_code == 409
                for expected in (422, 422, 422, 429):
                    response = await client.post(
                        "/api/v1/r/demo-ufa/checkout/identity/otp/verify",
                        json={
                            "challenge_id": str(second_id),
                            "code": wrong_code,
                        },
                        headers={"X-Checkout-Token": checkout_token},
                    )
                    assert response.status_code == expected

                third = await client.post(
                    "/api/v1/r/demo-ufa/checkout/identity/otp/request",
                    json={"phone_e164": SYNTHETIC_PHONE},
                    headers={"X-Checkout-Token": checkout_token},
                )
                async with owner.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE customer_auth_challenges SET expires_at = "
                            "now() - interval '1 second' WHERE id = :challenge_id"
                        ),
                        {"challenge_id": UUID(third.json()["challenge_id"])},
                    )
                expired = await client.post(
                    "/api/v1/r/demo-ufa/checkout/identity/otp/verify",
                    json={"challenge_id": third.json()["challenge_id"], "code": "999999"},
                    headers={"X-Checkout-Token": checkout_token},
                )
                assert expired.status_code == 409
        async with owner.begin() as connection:
            states = list(
                cast(
                    Sequence[str],
                    (
                        await connection.execute(
                            text(
                                "SELECT state FROM customer_auth_challenges "
                                "WHERE tenant_id = :tenant_id ORDER BY created_at"
                            ),
                            {"tenant_id": TENANT_UFA},
                        )
                    )
                    .scalars()
                    .all(),
                )
            )
            assert states == ["superseded", "locked", "expired"]
    finally:
        await clear_checkout(owner)
        await set_booking(owner, False)
        await owner.dispose()
