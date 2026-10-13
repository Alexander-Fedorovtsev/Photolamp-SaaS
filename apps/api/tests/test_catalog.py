import os
from uuid import UUID

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
TENANT_KAZAN = UUID("d0000000-0000-0000-0000-000000000002")
BRANCH_KAZAN = UUID("d1000000-0000-0000-0000-000000000002")
MODEL_UFA = UUID("d2000000-0000-0000-0000-000000000001")


def runtime_url() -> str:
    defaults = Settings()
    configured = make_url(os.getenv("DATABASE_URL", defaults.database_url.get_secret_value()))
    return configured.set(
        username=RUNTIME_ROLE,
        password="local-runtime-only",  # noqa: S106 - synthetic test credential
    ).render_as_string(hide_password=False)


@pytest.mark.integration
async def test_public_catalog_is_database_backed_and_privacy_safe() -> None:
    app = create_app(
        Settings(
            app_env="test",
            database_url=runtime_url(),
            enable_demo_previews=True,
        )
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ufa = await client.get("/api/v1/r/demo-ufa/catalog")
            kazan = await client.get("/api/v1/r/demo-kazan/catalog")
            assert ufa.status_code == kazan.status_code == 200
            assert ufa.headers["cache-control"] == "no-store"
            ufa_body = ufa.json()
            kazan_body = kazan.json()
            assert ufa_body["city"] == "Уфа"
            assert kazan_body["city"] == "Казань"
            assert len(ufa_body["items"]) == 2
            assert len(kazan_body["items"]) == 1
            assert all(item["price_is_demo"] for item in ufa_body["items"])
            assert all(item["deposit_minor"] == 0 for item in ufa_body["items"])
            assert "DEMO-UFA" not in ufa.text
            assert "serial_no" not in ufa.text
            assert (await client.get("/api/v1/r/unknown/catalog")).status_code == 404

    disabled = create_app(
        Settings(
            app_env="test",
            database_url=runtime_url(),
            enable_demo_previews=False,
        )
    )
    async with disabled.router.lifespan_context(disabled):
        async with AsyncClient(
            transport=ASGITransport(app=disabled), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1/r/demo-ufa/catalog")).status_code == 404


@pytest.mark.integration
async def test_catalog_rls_and_composite_foreign_keys_block_cross_tenant_access() -> None:
    engine = create_async_engine(runtime_url(), pool_size=1, max_overflow=0)
    try:
        async with engine.begin() as connection:
            for statement in [
                "SELECT tenant_id FROM storefront_profiles",
                "SELECT tenant_id FROM branches",
                "SELECT tenant_id FROM equipment_models",
                "SELECT tenant_id FROM assets",
                "SELECT tenant_id FROM pricing_rules",
            ]:
                assert (await connection.execute(text(statement))).all() == []

        async with engine.begin() as connection:
            storefront = (
                await connection.execute(
                    text("SELECT tenant_id, is_demo FROM resolve_public_storefront('demo-ufa')")
                )
            ).one()
            assert storefront == (TENANT_UFA, True)
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(TENANT_UFA)},
            )
            assert (
                await connection.execute(text("SELECT DISTINCT tenant_id FROM assets"))
            ).scalars().all() == [TENANT_UFA]
            assert (
                await connection.execute(
                    text(
                        "UPDATE equipment_models SET name = 'blocked' WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": TENANT_KAZAN},
                )
            ).rowcount == 0

        async with engine.begin() as connection:
            assert (await connection.execute(text("SELECT id FROM assets"))).all() == []

        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(TENANT_UFA)},
                )
                await connection.execute(
                    text(
                        "INSERT INTO assets "
                        "(id, tenant_id, branch_id, equipment_model_id, asset_code) "
                        "VALUES (:id, :tenant_id, :branch_id, :model_id, 'CROSS-TENANT')"
                    ),
                    {
                        "id": UUID("d3000000-0000-0000-0000-000000000099"),
                        "tenant_id": TENANT_UFA,
                        "branch_id": BRANCH_KAZAN,
                        "model_id": MODEL_UFA,
                    },
                )
    finally:
        await engine.dispose()
