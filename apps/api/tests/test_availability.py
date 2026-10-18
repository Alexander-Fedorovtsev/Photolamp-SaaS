import os
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

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


@pytest.mark.integration
async def test_quote_is_server_calculated_and_blocks_reduce_availability() -> None:
    runtime_url, owner_url = database_urls()
    settings = Settings(app_env="test", database_url=runtime_url, enable_demo_previews=True)
    app = create_app(settings)
    start = date.today() + timedelta(days=10)
    end = start + timedelta(days=2)
    request = {
        "model_id": str(MODEL_UFA),
        "branch_id": str(BRANCH_UFA),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "fulfillment": "delivery",
    }
    block_id = uuid4()
    owner = create_async_engine(owner_url)
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                quote = await client.post("/api/v1/r/demo-ufa/checkout/quote", json=request)
                assert quote.status_code == 200
                body = quote.json()
                assert quote.headers["cache-control"] == "no-store"
                assert body["days"] == 3
                assert body["amount_minor_per_day"] == 49_000
                assert body["rental_amount_minor"] == 147_000
                assert body["total_minor"] == 147_000
                assert body["available_units"] == 1
                assert body["availability_confirmed"] is True
                assert body["reservation_created"] is False
                assert body["booking_enabled"] is False
                assert "asset_id" not in quote.text
                assert "asset_code" not in quote.text

                query = await client.get(
                    "/api/v1/r/demo-ufa/availability",
                    params={key: value for key, value in request.items() if key != "fulfillment"},
                )
                assert query.status_code == 200
                assert query.json()["available_units"] == 1

                async with owner.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO asset_blocks "
                            "(id, tenant_id, asset_id, start_date, end_date, reason, note) "
                            "VALUES (:id, :tenant_id, :asset_id, :start_date, :end_date, "
                            "'maintenance', 'Synthetic integration test block')"
                        ),
                        {
                            "id": block_id,
                            "tenant_id": TENANT_UFA,
                            "asset_id": ASSET_UFA,
                            "start_date": start,
                            "end_date": end,
                        },
                    )

                unavailable = await client.post("/api/v1/r/demo-ufa/checkout/quote", json=request)
                assert unavailable.status_code == 409
                assert unavailable.json() == {
                    "detail": {"code": "NO_UNITS_AVAILABLE", "field": "dates"}
                }
                availability = await client.get(
                    "/api/v1/r/demo-ufa/availability",
                    params={key: value for key, value in request.items() if key != "fulfillment"},
                )
                assert availability.status_code == 200
                assert availability.json()["available"] is False
                assert availability.json()["available_units"] == 0

                tampered = await client.post(
                    "/api/v1/r/demo-ufa/checkout/quote",
                    json={**request, "rental_amount_minor": 1},
                )
                assert tampered.status_code == 422
    finally:
        async with owner.begin() as connection:
            await connection.execute(
                text("DELETE FROM asset_blocks WHERE id = :id"), {"id": block_id}
            )
        await owner.dispose()


@pytest.mark.integration
async def test_quote_validates_dates_tariff_and_tenant_scope() -> None:
    runtime_url, _ = database_urls()
    settings = Settings(app_env="test", database_url=runtime_url, enable_demo_previews=True)
    app = create_app(settings)
    start = date.today() + timedelta(days=10)
    base = {
        "model_id": str(MODEL_UFA),
        "branch_id": str(BRANCH_UFA),
        "start_date": start.isoformat(),
        "end_date": start.isoformat(),
        "fulfillment": "pickup",
    }
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (
                await client.post("/api/v1/r/demo-ufa/checkout/quote", json=base)
            ).status_code == 422
            for request, code in (
                (base, "MINIMUM_DAYS"),
                ({**base, "end_date": (start + timedelta(days=30)).isoformat()}, "MAXIMUM_DAYS"),
                ({**base, "end_date": (start - timedelta(days=1)).isoformat()}, "END_BEFORE_START"),
                (
                    {**base, "start_date": (date.today() - timedelta(days=2)).isoformat()},
                    "DATE_IN_PAST",
                ),
            ):
                rejected = await client.post("/api/v1/r/demo-ufa/checkout/quote", json=request)
                assert rejected.status_code == 422
                assert rejected.json()["detail"]["code"] == code
            reversed_dates = {
                **base,
                "end_date": (start - timedelta(days=1)).isoformat(),
            }
            assert (
                await client.post("/api/v1/r/demo-ufa/checkout/quote", json=reversed_dates)
            ).status_code == 422
            assert (
                await client.post(
                    "/api/v1/r/demo-kazan/checkout/quote",
                    json={**base, "end_date": (start + timedelta(days=2)).isoformat()},
                )
            ).status_code == 404

    engine = create_async_engine(runtime_url)
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(text("SELECT id FROM asset_blocks"))).all() == []
        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(TENANT_UFA)},
                )
                await connection.execute(
                    text(
                        "INSERT INTO asset_blocks "
                        "(id, tenant_id, asset_id, start_date, end_date, reason) "
                        "VALUES (:id, :tenant_id, :asset_id, :start_date, :end_date, 'manual')"
                    ),
                    {
                        "id": uuid4(),
                        "tenant_id": TENANT_UFA,
                        "asset_id": ASSET_KAZAN,
                        "start_date": start,
                        "end_date": start,
                    },
                )
    finally:
        await engine.dispose()
