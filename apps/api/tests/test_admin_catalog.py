import os
from collections.abc import AsyncIterator
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.bootstrap_runtime_role import RUNTIME_ROLE
from app.config import Settings
from app.main import create_app

DEMO_USER = UUID("d5000000-0000-0000-0000-000000000001")


@pytest.fixture
async def admin_client() -> AsyncIterator[tuple[AsyncClient, str, str]]:
    defaults = Settings()
    owner_url = os.getenv("DATABASE_OWNER_URL", defaults.migration_database_url())
    configured = make_url(os.getenv("DATABASE_URL", defaults.database_url.get_secret_value()))
    runtime_url = configured.set(
        username=RUNTIME_ROLE,
        password="local-runtime-only",  # noqa: S106 - synthetic test credential
    ).render_as_string(hide_password=False)
    settings = Settings(app_env="test", database_url=runtime_url, enable_demo_previews=True)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/api/v1/auth/demo-session")
            assert login.status_code == 200
            token = login.json()["access_token"]
            yield client, token, owner_url


@pytest.mark.integration
async def test_demo_owner_manages_catalog_and_public_read_model(
    admin_client: tuple[AsyncClient, str, str],
) -> None:
    client, token, owner_url = admin_client
    headers = {"Authorization": f"Bearer {token}"}
    memberships = await client.get("/api/v1/me/tenants", headers=headers)
    assert memberships.status_code == 200
    ufa = next(item for item in memberships.json() if item["slug"] == "demo-ufa")
    tenant_id = ufa["tenant_id"]
    snapshot_url = f"/api/v1/t/{tenant_id}/catalog-admin"
    snapshot = await client.get(snapshot_url, headers=headers)
    assert snapshot.status_code == 200
    initial = snapshot.json()
    assert initial["role"] == "owner"
    assert initial["branches"] and initial["models"]

    marker = uuid4().hex[:8].upper()
    branch_response = await client.post(
        f"/api/v1/t/{tenant_id}/branches",
        headers=headers,
        json={
            "name": f"Synthetic branch {marker}",
            "city": "Synthetic city",
            "timezone": "Europe/Moscow",
            "pickup_enabled": True,
            "delivery_enabled": False,
        },
    )
    assert branch_response.status_code == 201
    branch_id = branch_response.json()["id"]
    assert (
        await client.patch(
            f"/api/v1/t/{tenant_id}/branches/{branch_id}",
            headers=headers,
            json={"state": "inactive"},
        )
    ).status_code == 200

    direct_publish = await client.post(
        f"/api/v1/t/{tenant_id}/models",
        headers=headers,
        json={
            "name": f"Blocked published model {marker}",
            "description": "A model cannot be published before its tariff exists.",
            "included_items": ["lamp"],
            "publication_state": "published",
        },
    )
    assert direct_publish.status_code == 422
    assert direct_publish.json()["detail"] == (
        "Чтобы опубликовать модель, сначала сохраните её как черновик и активируйте тариф"
    )

    model_response = await client.post(
        f"/api/v1/t/{tenant_id}/models",
        headers=headers,
        json={
            "name": f"Synthetic admin model {marker}",
            "manufacturer": "Synthetic manufacturer",
            "description": "Synthetic integration-test model without customer data.",
            "included_items": ["lamp", "case", "manufacturer guide"],
            "illustration": "compact",
            "publication_state": "draft",
        },
    )
    assert model_response.status_code == 201
    model_id = model_response.json()["id"]
    rejected_publish = await client.patch(
        f"/api/v1/t/{tenant_id}/models/{model_id}",
        headers=headers,
        json={"publication_state": "published"},
    )
    assert rejected_publish.status_code == 422
    assert rejected_publish.json()["detail"] == (
        "Чтобы опубликовать модель, сначала активируйте для неё тариф"
    )
    assert (
        await client.patch(
            f"/api/v1/t/{tenant_id}/models/{model_id}",
            headers=headers,
            json={"description": "Updated synthetic model description."},
        )
    ).status_code == 200

    asset_response = await client.post(
        f"/api/v1/t/{tenant_id}/assets",
        headers=headers,
        json={
            "branch_id": branch_id,
            "equipment_model_id": model_id,
            "asset_code": f"TEST-{marker}",
            "state": "available",
        },
    )
    assert asset_response.status_code == 201
    asset_id = asset_response.json()["id"]

    block_response = await client.post(
        f"/api/v1/t/{tenant_id}/assets/{asset_id}/blocks",
        headers=headers,
        json={
            "start_date": (date.today() + timedelta(days=20)).isoformat(),
            "end_date": (date.today() + timedelta(days=21)).isoformat(),
            "reason": "inspection",
            "note": "Synthetic admin test block",
        },
    )
    assert block_response.status_code == 201
    block_id = block_response.json()["id"]

    pricing_response = await client.post(
        f"/api/v1/t/{tenant_id}/pricing",
        headers=headers,
        json={
            "equipment_model_id": model_id,
            "amount_minor_per_day": 32100,
            "deposit_minor": 0,
            "minimum_days": 2,
            "maximum_days": 21,
            "state": "active",
        },
    )
    assert pricing_response.status_code == 201
    pricing_id = pricing_response.json()["id"]
    published = await client.patch(
        f"/api/v1/t/{tenant_id}/models/{model_id}",
        headers=headers,
        json={"publication_state": "published"},
    )
    assert published.status_code == 200
    assert (
        await client.patch(
            f"/api/v1/t/{tenant_id}/pricing/{pricing_id}",
            headers=headers,
            json={"amount_minor_per_day": 34500},
        )
    ).status_code == 200

    refreshed = (await client.get(snapshot_url, headers=headers)).json()
    assert any(item["id"] == model_id for item in refreshed["models"])
    assert any(item["asset_code"] == f"TEST-{marker}" for item in refreshed["assets"])
    assert any(item["id"] == block_id for item in refreshed["asset_blocks"])
    public = await client.get("/api/v1/r/demo-ufa/catalog")
    assert public.status_code == 200
    published_item = next(item for item in public.json()["items"] if item["id"] == model_id)
    assert published_item == {
        "id": model_id,
        "name": f"Synthetic admin model {marker}",
        "category": "phototherapy_lamp",
        "manufacturer": "Synthetic manufacturer",
        "description": "Updated synthetic model description.",
        "included_items": ["lamp", "case", "manufacturer guide"],
        "illustration": "compact",
        "amount_minor_per_day": 34500,
        "deposit_minor": 0,
        "currency": "RUB",
        "minimum_days": 2,
        "maximum_days": 21,
        "price_is_demo": True,
        "listed_units": 1,
        "serviceable_units": 1,
    }
    rejected_tariff_retirement = await client.patch(
        f"/api/v1/t/{tenant_id}/pricing/{pricing_id}",
        headers=headers,
        json={"state": "retired"},
    )
    assert rejected_tariff_retirement.status_code == 422
    assert rejected_tariff_retirement.json()["detail"] == (
        "Сначала снимите модель с витрины, затем отключите её активный тариф"
    )

    patched = await client.patch(
        f"/api/v1/t/{tenant_id}/assets/{asset_id}",
        headers=headers,
        json={"state": "inspection"},
    )
    assert patched.status_code == 200
    missing = await client.patch(
        f"/api/v1/t/{tenant_id}/assets/{uuid4()}",
        headers=headers,
        json={"state": "inspection"},
    )
    assert missing.status_code == 404
    removed_block = await client.delete(
        f"/api/v1/t/{tenant_id}/asset-blocks/{block_id}", headers=headers
    )
    assert removed_block.status_code == 200
    assert removed_block.json()["deleted"] is True
    rejected_tenant_body = await client.post(
        f"/api/v1/t/{tenant_id}/branches",
        headers=headers,
        json={
            "tenant_id": str(uuid4()),
            "name": "Blocked tenant override",
            "city": "Test",
            "timezone": "UTC",
        },
    )
    assert rejected_tenant_body.status_code == 422

    owner = create_async_engine(owner_url)
    try:
        async with owner.begin() as connection:
            audit_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM audit_events WHERE tenant_id = :tenant_id "
                    "AND entity_id IN (:branch_id, :model_id, :asset_id, :pricing_id, :block_id)"
                ),
                {
                    "tenant_id": tenant_id,
                    "branch_id": branch_id,
                    "model_id": model_id,
                    "asset_id": asset_id,
                    "pricing_id": pricing_id,
                    "block_id": block_id,
                },
            )
            assert audit_count == 11
            await connection.execute(
                text(
                    "DELETE FROM audit_events "
                    "WHERE entity_id IN (:branch, :model, :asset, :pricing, :block)"
                ),
                {
                    "branch": branch_id,
                    "model": model_id,
                    "asset": asset_id,
                    "pricing": pricing_id,
                    "block": block_id,
                },
            )
            await connection.execute(
                text("DELETE FROM pricing_rules WHERE id = :id"), {"id": pricing_id}
            )
            await connection.execute(text("DELETE FROM assets WHERE id = :id"), {"id": asset_id})
            await connection.execute(
                text("DELETE FROM equipment_models WHERE id = :id"), {"id": model_id}
            )
            await connection.execute(text("DELETE FROM branches WHERE id = :id"), {"id": branch_id})
    finally:
        await owner.dispose()

    logout = await client.delete("/api/v1/auth/session", headers=headers)
    assert logout.status_code == 200
    assert logout.json() == {"revoked": True}
    assert (await client.get("/api/v1/me/tenants", headers=headers)).status_code == 401


@pytest.mark.integration
async def test_catalog_read_model_is_minimized_by_staff_role(
    admin_client: tuple[AsyncClient, str, str],
) -> None:
    client, token, owner_url = admin_client
    headers = {"Authorization": f"Bearer {token}"}
    memberships = await client.get("/api/v1/me/tenants", headers=headers)
    tenant_id = next(item["tenant_id"] for item in memberships.json() if item["slug"] == "demo-ufa")
    catalog_url = f"/api/v1/t/{tenant_id}/catalog-admin"
    owner_catalog = (await client.get(catalog_url, headers=headers)).json()
    asset_id = owner_catalog["assets"][0]["id"]
    owner = create_async_engine(owner_url)
    try:
        async with owner.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE tenant_memberships SET role = 'operator' "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                {"tenant_id": tenant_id, "user_id": DEMO_USER},
            )

        operator_response = await client.get(catalog_url, headers=headers)
        assert operator_response.status_code == 200
        operator_catalog = operator_response.json()
        assert operator_catalog["role"] == "operator"
        assert operator_catalog["branches"]
        assert operator_catalog["models"]
        assert operator_catalog["pricing"]
        assert operator_catalog["assets"] == []
        assert operator_catalog["asset_blocks"] == []
        assert "asset_code" not in operator_response.text
        assert "serial_no" not in operator_response.text
        forbidden_mutation = await client.patch(
            f"/api/v1/t/{tenant_id}/assets/{asset_id}",
            headers=headers,
            json={"state": "available"},
        )
        assert forbidden_mutation.status_code == 403

        async with owner.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE tenant_memberships SET role = 'courier' "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                {"tenant_id": tenant_id, "user_id": DEMO_USER},
            )
        courier_response = await client.get(catalog_url, headers=headers)
        assert courier_response.status_code == 403
        assert courier_response.json()["detail"] == "Owner, admin or operator role required"
    finally:
        async with owner.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE tenant_memberships SET role = 'owner' "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                {"tenant_id": tenant_id, "user_id": DEMO_USER},
            )
        await owner.dispose()


@pytest.mark.integration
async def test_demo_session_is_environment_gated() -> None:
    settings = Settings(app_env="test", enable_demo_previews=False)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/api/v1/auth/demo-session")).status_code == 404
            tenant_id = "d0000000-0000-0000-0000-000000000001"
            assert (await client.get(f"/api/v1/t/{tenant_id}/catalog-admin")).status_code == 401
