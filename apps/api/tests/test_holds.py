import asyncio
import os
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.bootstrap_runtime_role import RUNTIME_ROLE
from app.config import Settings
from app.main import create_app

TENANT_UFA = UUID("d0000000-0000-0000-0000-000000000001")
BRANCH_UFA = UUID("d1000000-0000-0000-0000-000000000001")
MODEL_UFA = UUID("d2000000-0000-0000-0000-000000000001")
ASSET_UFA = UUID("d3000000-0000-0000-0000-000000000001")
ASSET_KAZAN = UUID("d3000000-0000-0000-0000-000000000004")


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


async def clear_holds(owner: AsyncEngine) -> None:
    async with owner.begin() as connection:
        await connection.execute(
            text("UPDATE orders SET customer_id = NULL WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )
        await connection.execute(
            text("UPDATE checkout_sessions SET customer_id = NULL WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )
        await connection.execute(
            text("DELETE FROM customer_auth_challenges WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )
        await connection.execute(
            text("DELETE FROM customer_sessions WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )
        await connection.execute(
            text("UPDATE rental_allocations SET order_id = NULL WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )
        await connection.execute(
            text("DELETE FROM order_events WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )
        await connection.execute(
            text("DELETE FROM orders WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )
        await connection.execute(
            text("DELETE FROM rental_allocations WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )
        await connection.execute(
            text("DELETE FROM checkout_sessions WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )
        await connection.execute(
            text("DELETE FROM customers WHERE tenant_id = :tenant_id"),
            {"tenant_id": TENANT_UFA},
        )


def hold_request() -> dict[str, str]:
    start = date.today() + timedelta(days=20)
    return {
        "model_id": str(MODEL_UFA),
        "branch_id": str(BRANCH_UFA),
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=2)).isoformat(),
        "fulfillment": "pickup",
    }


@pytest.mark.integration
async def test_hold_is_gated_atomic_idempotent_and_releasable() -> None:
    runtime_url, owner_url = database_urls()
    owner = create_async_engine(owner_url)
    app = create_app(
        Settings(
            app_env="test",
            database_url=runtime_url,
            enable_demo_previews=True,
            hold_ttl_seconds=600,
        )
    )
    command = hold_request()
    url = "/api/v1/r/demo-ufa/checkout/holds"
    keys = ("synthetic-hold-key-0001", "synthetic-hold-key-0002")
    unavailable_capability = "synthetic-checkout-capability-that-is-not-valid"
    try:
        await clear_holds(owner)
        await set_booking(owner, False)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                disabled = await client.post(
                    url,
                    json=command,
                    headers={
                        "Idempotency-Key": keys[0],
                        "X-Checkout-Token": unavailable_capability,
                    },
                )
                assert disabled.status_code == 409
                assert disabled.json() == {"detail": "Booking is not enabled"}

                await set_booking(owner, True)
                session = await client.post("/api/v1/r/demo-ufa/checkout/sessions")
                assert session.status_code == 201
                checkout_token = session.json()["checkout_token"]
                responses = await asyncio.gather(
                    *(
                        client.post(
                            url,
                            json=command,
                            headers={
                                "Idempotency-Key": key,
                                "X-Checkout-Token": checkout_token,
                            },
                        )
                        for key in keys
                    )
                )
                assert sorted(response.status_code for response in responses) == [201, 409]
                created = next(response for response in responses if response.status_code == 201)
                winner_key = keys[responses.index(created)]
                body = created.json()
                assert created.headers["cache-control"] == "no-store"
                assert body["state"] == "held"
                assert body["replayed"] is False
                assert body["quote"]["total_minor"] == 147_000
                assert body["quote"]["reservation_created"] is False
                assert "asset_id" not in created.text
                assert "asset_code" not in created.text

                unavailable = await client.post("/api/v1/r/demo-ufa/checkout/quote", json=command)
                assert unavailable.status_code == 409

                replay = await client.post(
                    url,
                    json=command,
                    headers={
                        "Idempotency-Key": winner_key,
                        "X-Checkout-Token": checkout_token,
                    },
                )
                assert replay.status_code == 201
                replayed = replay.json()
                assert replayed["hold_id"] == body["hold_id"]
                assert replayed["replayed"] is True
                assert replayed["management_token"] != body["management_token"]

                changed = await client.post(
                    url,
                    json={
                        **command,
                        "end_date": (
                            date.fromisoformat(command["end_date"]) + timedelta(days=1)
                        ).isoformat(),
                    },
                    headers={
                        "Idempotency-Key": winner_key,
                        "X-Checkout-Token": checkout_token,
                    },
                )
                assert changed.status_code == 409
                assert "another request" in changed.json()["detail"]

                release_url = f"{url}/{body['hold_id']}"
                old_token = await client.delete(
                    release_url,
                    headers={
                        "X-Hold-Token": body["management_token"],
                        "X-Checkout-Token": checkout_token,
                    },
                )
                assert old_token.status_code == 404
                released = await client.delete(
                    release_url,
                    headers={
                        "X-Hold-Token": replayed["management_token"],
                        "X-Checkout-Token": checkout_token,
                    },
                )
                assert released.status_code == 200
                assert released.json() == {"hold_id": body["hold_id"], "released": True}
                repeated_release = await client.delete(
                    release_url,
                    headers={
                        "X-Hold-Token": replayed["management_token"],
                        "X-Checkout-Token": checkout_token,
                    },
                )
                assert repeated_release.status_code == 200

                available = await client.post("/api/v1/r/demo-ufa/checkout/quote", json=command)
                assert available.status_code == 200
                assert available.json()["available_units"] == 1
    finally:
        await clear_holds(owner)
        await set_booking(owner, False)
        await owner.dispose()


@pytest.mark.integration
async def test_expired_holds_are_reclaimed_and_rls_is_enforced() -> None:
    runtime_url, owner_url = database_urls()
    owner = create_async_engine(owner_url)
    runtime = create_async_engine(runtime_url)
    app = create_app(Settings(app_env="test", database_url=runtime_url, enable_demo_previews=True))
    command = hold_request()
    url = "/api/v1/r/demo-ufa/checkout/holds"
    try:
        await clear_holds(owner)
        await set_booking(owner, True)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                session = await client.post("/api/v1/r/demo-ufa/checkout/sessions")
                assert session.status_code == 201
                checkout_token = session.json()["checkout_token"]
                first = await client.post(
                    url,
                    json=command,
                    headers={
                        "Idempotency-Key": "synthetic-expiry-key-001",
                        "X-Checkout-Token": checkout_token,
                    },
                )
                assert first.status_code == 201
                async with owner.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE rental_allocations SET expires_at = "
                            "now() - interval '1 second' "
                            "WHERE id = :hold_id"
                        ),
                        {"hold_id": UUID(first.json()["hold_id"])},
                    )
                replacements = await asyncio.gather(
                    *(
                        client.post(
                            url,
                            json=command,
                            headers={
                                "Idempotency-Key": "synthetic-expiry-key-002",
                                "X-Checkout-Token": checkout_token,
                            },
                        )
                        for _ in range(2)
                    )
                )
                assert [response.status_code for response in replacements] == [201, 201]
                replacement_ids = {response.json()["hold_id"] for response in replacements}
                assert len(replacement_ids) == 1
                assert replacement_ids != {first.json()["hold_id"]}
                assert sorted(response.json()["replayed"] for response in replacements) == [
                    False,
                    True,
                ]

        with pytest.raises(DBAPIError):
            async with owner.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO rental_allocations "
                        "(id, tenant_id, asset_id, equipment_model_id, branch_id, start_date, "
                        "end_date, fulfillment, state, expires_at, idempotency_key_hash, "
                        "management_token_hash, quote_snapshot) VALUES "
                        "(:id, :tenant_id, :asset_id, :model_id, :branch_id, "
                        ":start_date, :end_date, 'pickup', 'held', "
                        "now() + interval '10 minutes', decode(repeat('03', 32), 'hex'), "
                        "decode(repeat('04', 32), 'hex'), '{}')"
                    ),
                    {
                        "id": uuid4(),
                        "tenant_id": TENANT_UFA,
                        "asset_id": ASSET_UFA,
                        "model_id": MODEL_UFA,
                        "branch_id": BRANCH_UFA,
                        "start_date": date.fromisoformat(command["start_date"]),
                        "end_date": date.fromisoformat(command["end_date"]),
                    },
                )

        async with runtime.begin() as connection:
            assert (await connection.execute(text("SELECT id FROM rental_allocations"))).all() == []
        with pytest.raises(DBAPIError):
            async with runtime.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(TENANT_UFA)},
                )
                await connection.execute(
                    text(
                        "INSERT INTO rental_allocations "
                        "(id, tenant_id, asset_id, equipment_model_id, branch_id, start_date, "
                        "end_date, fulfillment, state, expires_at, idempotency_key_hash, "
                        "management_token_hash, quote_snapshot) VALUES "
                        "(gen_random_uuid(), :tenant_id, :asset_id, :model_id, :branch_id, "
                        ":start_date, :end_date, 'pickup', 'held', now() + interval '10 minutes', "
                        "decode(repeat('01', 32), 'hex'), decode(repeat('02', 32), 'hex'), '{}')"
                    ),
                    {
                        "tenant_id": TENANT_UFA,
                        "asset_id": ASSET_KAZAN,
                        "model_id": MODEL_UFA,
                        "branch_id": BRANCH_UFA,
                        "start_date": date.fromisoformat(command["start_date"]),
                        "end_date": date.fromisoformat(command["end_date"]),
                    },
                )
    finally:
        await clear_holds(owner)
        await set_booking(owner, False)
        await runtime.dispose()
        await owner.dispose()
